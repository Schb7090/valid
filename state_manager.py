import sqlite3
import json
import hashlib
import os
import time
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

# FONTOS: A models importálás a helyi models.py fájlból történik
from models import UPVSEngineState

import asyncio
import threading

class AsyncWriter:
    """
    Háttérszálon futó, Asyncio Queue alapú író mechanizmus.
    Eltünteti a FileLock okozta szűk keresztmetszetet (overhead), így a JSON
    memóriamedencék és checkpointok írása non-blocking módon, azonnal lefut.
    """
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.queue = asyncio.Queue()
        self.thread = threading.Thread(target=self._start_loop, daemon=True)
        self.thread.start()

    def _start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._worker())

    async def _worker(self):
        while True:
            task, args = await self.queue.get()
            try:
                if task == "write_json":
                    filepath, data = args
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(data)
                elif task == "append_json":
                    filepath, data = args
                    memory = []
                    if os.path.exists(filepath):
                        with open(filepath, "r", encoding="utf-8") as f:
                            try: memory = json.load(f)
                            except json.JSONDecodeError: pass
                    memory.append(data)
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(memory, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"AsyncWriter hiba: {e}")
            finally:
                self.queue.task_done()

    def submit_write_json(self, filepath: str, data_str: str):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, ("write_json", (filepath, data_str)))

    def submit_append_json(self, filepath: str, data_dict: dict):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, ("append_json", (filepath, data_dict)))

# Globális singleton példány az aszinkron íróhoz
async_writer = AsyncWriter()


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
            conn.commit()

    # --- JSON Memory Pool Kezelés (Ágens specifikus) ---

    def log_agent_memory(self, session_id: str, agent_name: str, data: dict):
        """
        Elszeparált JSON memóriába menti egy specifikus LLM (pl. 'debiaser') belső gondolatait
        és döntéseit a transzparencia és hibakeresés érdekében. Konkurenciakezelést (FileLock) használ.
        """
        base_dir = Path(self.memory_pool_dir) / session_id
        base_dir.mkdir(parents=True, exist_ok=True)
        
        mem_file = base_dir / f"{agent_name}_memory.json"
        # FileLock helyett az AsyncWriter-nek küldjük be a feladatot (Non-blocking)
        data["timestamp"] = datetime.utcnow().isoformat()
        async_writer.submit_append_json(str(mem_file), data)

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
        """Kimenti a teljes állapotgépet JSON fájlba a session_id alapján. AsyncQueue-t használ FileLock helyett."""
        file_path = os.path.join(self.checkpoint_dir, f"{state.session_id}.json")
        async_writer.submit_write_json(file_path, state.model_dump_json(indent=2))
            
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
