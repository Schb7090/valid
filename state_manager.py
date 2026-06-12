import sqlite3
import json
import hashlib
import os
import time
import queue
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

# FONTOS: A models importálás a helyi models.py fájlból történik
from models import UPVSEngineState

import threading

class SQLiteAsyncWriter:
    """
    Háttérszálon futó, SQLite tábla alapú író mechanizmus.
    Perzisztensen tárolja a feldolgozásra váró lemezműveleteket, így
    crash-safe, de a főszálat nem blokkolja.
    V11: Zero-CPU event-driven működés queue.Queue használatával.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.running = True
        self.work_queue = queue.Queue()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def wake_up(self):
        """Eseményvezérelt jelzés a háttérszálnak, hogy van új feladat."""
        self.work_queue.put(1)

    def _worker(self):
        # Kezdeti felébresztés, hátha maradtak feldolgozatlan adatok egy korábbi crash után
        self.wake_up()
        
        while self.running:
            try:
                processed_any = False
                with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, task_type, filepath, data FROM pending_writes ORDER BY id ASC LIMIT 1")
                    row = cursor.fetchone()
                    if row:
                        processed_any = True
                        row_id, task, filepath, data = row
                        try:
                            if task == "write_json":
                                with open(filepath, "w", encoding="utf-8") as f:
                                    f.write(data)
                            elif task == "append_json":
                                memory = []
                                if os.path.exists(filepath):
                                    with open(filepath, "r", encoding="utf-8") as f:
                                        try: memory = json.load(f)
                                        except json.JSONDecodeError: pass
                                
                                new_data = json.loads(data)
                                # V13 Idempotencia: Ha a crash miatt a fájlba már beleíródott, de a sorból nem törlődött, ne írjuk bele újra.
                                if new_data not in memory:
                                    memory.append(new_data)
                                    with open(filepath, "w", encoding="utf-8") as f:
                                        json.dump(memory, f, indent=2, ensure_ascii=False)
                            # ACK: Sikeres írás után töröljük a rekordot
                            cursor.execute("DELETE FROM pending_writes WHERE id = ?", (row_id,))
                            conn.commit()
                        except Exception as e:
                            print(f"SQLiteAsyncWriter IO hiba: {e}")
                            # Hibás rekord eldobása, hogy ne akassza meg végleg a sort
                            cursor.execute("DELETE FROM pending_writes WHERE id = ?", (row_id,))
                            conn.commit()
                            
                # Ha nem találtunk semmit az adatbázisban, blokkolva várjuk a következő ébresztést (Zero CPU Idle)
                if not processed_any:
                    self.work_queue.get(block=True, timeout=None)
                    
            except Exception as e:
                time.sleep(0.5) # Adatbázis zárás vagy hiba esetén várakozás


class StateManager:
    """
    Központi memóriakezelő a UPVS-Engine-hez.
    Felel a SQLite adatbázis sémáinak felépítéséért (cache, fact_store, audit_log),
    a JSON Memory Pool-ért, és a Pydantic állapotgép diszkre mentéséért.
    """
    
    def __init__(self, db_path: str = "upvs_cache.db", checkpoint_dir: str = "checkpoints", memory_pool_dir: str = "memory_pool"):
        self.db_path = db_path
        self.checkpoint_dir = checkpoint_dir
        self.memory_pool_dir = memory_pool_dir
        
        # Mappák létrehozása
        for directory in [checkpoint_dir, memory_pool_dir]:
            if not os.path.exists(directory):
                os.makedirs(directory)
                
        # Inicializáljuk az adatbázisokat (csak ha még nincsenek)
        self._init_db()
        
        # Elindítjuk az aszinkron worker szálat, ami a pending_writes-ot olvassa
        self.async_writer = SQLiteAsyncWriter(self.db_path)

    def get_db_connection(self):
        """SQLite kapcsolat WAL móddal (konkurens olvasás/írás blokkolás nélkül)."""
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.execute('PRAGMA journal_mode=WAL;')
        return conn

    def _init_db(self):
        """Inicializálja az SQLite sémákat a cache-hez, audit naplóhoz és fact store-hoz."""
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            
            # --- 1. LLM és API Cache ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS api_cache (
                    query_hash TEXT PRIMARY KEY,
                    endpoint TEXT,
                    response TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS llm_cache (
                    prompt_hash TEXT PRIMARY KEY,
                    model TEXT,
                    response TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # --- 2. Fact Store (Researcher rétegnek) ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    authors TEXT,
                    institution TEXT,
                    year INTEGER,
                    doi TEXT,
                    url TEXT,
                    source_type TEXT,
                    quality INTEGER DEFAULT 50
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS facts (
                    fact_id TEXT PRIMARY KEY,
                    claim_text TEXT NOT NULL,
                    context TEXT,
                    confidence REAL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fact_sources (
                    fact_id TEXT REFERENCES facts(fact_id),
                    source_id TEXT REFERENCES sources(source_id),
                    PRIMARY KEY (fact_id, source_id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS node_facts (
                    node_id TEXT,
                    fact_id TEXT REFERENCES facts(fact_id),
                    PRIMARY KEY (node_id, fact_id)
                )
            ''')
            
            # --- 3. Audit Napló (Minden döntés logolása) ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    layer TEXT,
                    action TEXT,
                    details TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # --- 4. Queue Tábla (Async I/O-hoz) ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_writes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT,
                    filepath TEXT,
                    data TEXT
                )
            ''')
            conn.commit()

    def submit_write_json(self, filepath: str, data_str: str):
        with self.get_db_connection() as conn:
            conn.execute("INSERT INTO pending_writes (task_type, filepath, data) VALUES (?, ?, ?)", 
                         ("write_json", filepath, data_str))
            conn.commit()
        self.async_writer.wake_up()

    def submit_append_json(self, filepath: str, data_dict: dict):
        with self.get_db_connection() as conn:
            conn.execute("INSERT INTO pending_writes (task_type, filepath, data) VALUES (?, ?, ?)", 
                         ("append_json", filepath, json.dumps(data_dict, ensure_ascii=False)))
            conn.commit()
        self.async_writer.wake_up()

    # --- JSON Memory Pool Kezelés (Ágens specifikus) ---

    def log_agent_memory(self, session_id: str, agent_name: str, data: dict):
        """
        Elszeparált JSON memóriába menti egy specifikus LLM (pl. 'debiaser') belső gondolatait
        és döntéseit a transzparencia és hibakeresés érdekében. Konkurenciakezelést (FileLock) használ.
        """
        base_dir = Path(self.memory_pool_dir) / session_id
        base_dir.mkdir(parents=True, exist_ok=True)
        
        mem_file = base_dir / f"{agent_name}_memory.json"
        # Memória írás beillesztése a perzisztens queue-ba
        data["timestamp"] = datetime.now().isoformat()
        self.submit_append_json(str(mem_file), data)

    # --- Cache Kezelő Metódusok ---

    def get_api_cache(self, query_hash: str) -> Optional[str]:
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT response FROM api_cache WHERE query_hash = ?", (query_hash,))
            row = cursor.fetchone()
            return row[0] if row else None

    def set_api_cache(self, query_hash: str, endpoint: str, response: str):
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO api_cache (query_hash, endpoint, response) VALUES (?, ?, ?)",
                (query_hash, endpoint, response)
            )
            conn.commit()

    def get_llm_cache(self, prompt_hash: str) -> Optional[str]:
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT response FROM llm_cache WHERE prompt_hash = ?", (prompt_hash,))
            row = cursor.fetchone()
            return row[0] if row else None

    def set_llm_cache(self, prompt_hash: str, model: str, response: str):
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO llm_cache (prompt_hash, model, response) VALUES (?, ?, ?)",
                (prompt_hash, model, response)
            )
            conn.commit()

    # --- Audit Naplózás ---

    def log_action(self, session_id: str, layer: str, action: str, details: Dict[str, Any]):
        """Bármely réteg hívhatja globális döntések naplózására (SQLite)."""
        with self.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO audit_log (session_id, layer, action, details) VALUES (?, ?, ?, ?)",
                (session_id, layer, action, json.dumps(details))
            )
            conn.commit()

    # --- State Checkpointing (Pydantic <-> JSON) ---

    def save_checkpoint(self, state: UPVSEngineState):
        """Kimenti a teljes állapotgépet JSON fájlba a session_id alapján. SQLite-backed Queue-t használ."""
        file_path = os.path.join(self.checkpoint_dir, f"{state.session_id}.json")
        self.submit_write_json(file_path, state.model_dump_json(indent=2))
            
    def load_checkpoint(self, session_id: str) -> Optional[UPVSEngineState]:
        """Visszatölti az állapotgépet megszakadás esetén."""
        file_path = os.path.join(self.checkpoint_dir, f"{session_id}.json")
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return UPVSEngineState.model_validate_json(f.read())
        return None

    # --- Segédmetódusok ---

    @staticmethod
    def generate_hash(*args) -> str:
        """Determinisztikus MD5 hash generálása bemenetekből (LLM/API hívások kulcsaihoz)."""
        combined = "|".join(str(a) for a in args)
        return hashlib.md5(combined.encode('utf-8')).hexdigest()
