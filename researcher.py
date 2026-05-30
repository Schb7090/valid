"""
UPVS-Engine — Researcher (3. Réteg)
Feladata: A DAG csomópontjaihoz tartozó kutatási kérdések alapján API hívások szimulálása, 
a források LLM általi minősítése, és az elfogadott tények lementése az SQLite Fact Store-ba.
"""
import sqlite3
import json
import uuid
import yaml
import hashlib
import re
from pathlib import Path
from typing import List, Tuple

from models import TaskContext, ArgumentGraph, ArgumentNode, SourceRecord, FactRecord
from state_manager import StateManager

RESEARCHER_SYSTEM_PROMPT = """
# SZEREPKÖR
Te vagy a UPVS-Engine 3. rétegének forrás-értékelője (Relevance & Quality Filter).
Kaptál egy nyers forrást (cím, szerző, típus, absztrakt/szöveg) és egy logikai állítást (Claim), amit a dokumentum készítése során bizonyítanunk kell.

# FELADATOD
1. Relevancia: Döntsd el, hogy a forrás tartalma ténylegesen alátámasztja-e a Claim-et.
2. Minőség: Pontozd a forrás szakmai/tudományos minőségét 1-100-as skálán (pl. PubMed/arXiv tanulmány magas, blogposzt alacsony).
3. Kivonatolás: Ha elfogadod, emelj ki egy pontos tény-részletet (1-2 mondat), amit a Generátor később fel tud használni!

# BEMENET
Bizonyítandó állítás (Claim): "{claim}"
Minimum elvárt minőségi pontszám: {quality_threshold}

Forrás címe: {source_title}
Forrás típusa: {source_type}
Forrás tartalma: {source_context}

# KIMENETI FORMÁTUM (KIZÁRÓLAG ÉRVÉNYES JSON)
{{
  "is_relevant": true,
  "quality_score": 85,
  "extracted_fact": "A dán flexicurity modell 2023-ban 75%-os foglalkoztatottságot eredményezett, ami...",
  "reasoning": "Rövid indoklás, miért jó vagy rossz a forrás."
}}
"""

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# --- Normalizációs és Segédmetódusok ---

def normalize_institution(inst: str) -> str:
    """Intézménynevek tisztítása és gyakori rövidítéseinek feloldása."""
    if not inst: return ""
    inst = inst.lower()
    inst = re.sub(r'[^a-z0-9\s]', '', inst) # Írásjelek eltávolítása (pl. M.I.T -> mit)
    
    # Gyakori intézmény mapping (bővíthető)
    mappings = {
        "mit": "massachusetts institute of technology",
        "ucla": "university of california los angeles",
        "stanford": "stanford university"
    }
    
    inst = " ".join(inst.split())
    return mappings.get(inst, inst)

def normalize_author(author: str) -> str:
    """Szerzőnevek tisztítása és titulusok eltávolítása."""
    if not author: return ""
    author = author.lower()
    for title in ["dr", "prof", "phd", "md", "msc", "bsc"]:
        author = re.sub(rf'\b{title}\b', '', author)
    author = re.sub(r'[^a-z0-9\s]', '', author)
    return " ".join(author.split())

# --- SQLite Segédmetódusok ---

def save_fact_to_db(db_path: str, node_id: str, sources: List[SourceRecord], fact: FactRecord):
    """Lementi a forrásokat, a tényt, és összeköti a DAG csomóponttal a Fact Store-ban."""
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        cursor = conn.cursor()
        for source in sources:
            cursor.execute('''
                INSERT OR IGNORE INTO sources (source_id, title, authors, institution, year, doi, url, source_type, quality)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (source.source_id, source.title, source.authors, source.institution, source.year, source.doi, source.url, source.source_type, source.quality))
            
        # Tény mentése
        cursor.execute('''
            INSERT OR IGNORE INTO facts (fact_id, claim_text, context, confidence)
            VALUES (?, ?, ?, ?)
        ''', (fact.fact_id, fact.claim_text, fact.context, fact.confidence))
        
        # Kapcsolat mentése (fact -> sources)
        for source in sources:
            cursor.execute('''
                INSERT OR IGNORE INTO fact_sources (fact_id, source_id)
                VALUES (?, ?)
            ''', (fact.fact_id, source.source_id))
            
        # Kapcsolat mentése (node -> fact)
        cursor.execute('''
            INSERT OR IGNORE INTO node_facts (node_id, fact_id)
            VALUES (?, ?)
        ''', (node_id, fact.fact_id))
        conn.commit()

# --- API Mock és Kiértékelés ---

def mock_fetch_from_apis(query: str, depth: str, config: dict) -> List[dict]:
    """
    Placeholder külső API hívásokhoz (OpenAlex, PubMed, arXiv, Web).
    A 'depth' paraméter (deep, standard, shallow) határozza meg, mennyi hívást indítunk.
    Valós implementációnál itt futnának az aszinkron aiohttp kérések.
    """
    api_calls_allowed = config["engine_parameters"]["research"]["api_calls_per_depth"].get(depth, 0)
    if api_calls_allowed == 0:
        return []

    # Szimulált válasz
    return [
        {
            "source_id": f"src_{uuid.uuid4().hex[:8]}",
            "title": f"Tanulmány: {query} 1",
            "authors": "Dr. Smith",
            "author_id": "0000-0002-1825-0097", # ORCID Mock
            "institution": "Harvard",
            "source_type": "journal",
            "context": f"Ez egy szintetizált mock tartalom, ami bizonyítja ezt a keresést: {query}"
        },
        {
            "source_id": f"src_{uuid.uuid4().hex[:8]}",
            "title": f"Tanulmány: {query} 2",
            "authors": "Prof. Miller",
            "author_id": "0000-0001-2345-6789", # ORCID Mock
            "institution": "MIT",
            "source_type": "journal",
            "context": f"Második független forrás a kereséshez: {query}"
        }
    ]

def truncate_context(context_text: str, query: str, max_words: int = 500) -> str:
    """
    Költségcsökkentő csonkolás (Chunking): 
    Ha a nyers forrás hatalmas, megkeresi a query-hez legjobban illeszkedő 
    ablakot, és csak azt az 500 szót adja vissza az LLM-nek.
    """
    words = context_text.split()
    if len(words) <= max_words:
        return context_text
        
    query_terms = [t.lower() for t in query.split() if len(t) > 3]
    best_idx = 0
    max_matches = 0
    
    # Egyszerű csúszóablak alapú keresés a legsűrűbb részre
    window_size = max_words
    for i in range(len(words) - window_size + 1):
        window_text = " ".join(words[i:i+window_size]).lower()
        matches = sum(1 for term in query_terms if term in window_text)
        if matches > max_matches:
            max_matches = matches
            best_idx = i
            
    return " ".join(words[best_idx : best_idx + window_size])

def evaluate_source_with_llm(claim: str, raw_source: dict, config: dict, state_manager: StateManager) -> Tuple[bool, int, str]:
    """Felépíti a promptot és értékeli a forrást az LLM segítségével."""
    threshold = config["engine_parameters"]["research"]["quality_threshold"]
    
    # Védjük a Context Window-t a csonkolóval
    truncated_context = truncate_context(raw_source["context"], claim)
    
    prompt = RESEARCHER_SYSTEM_PROMPT.format(
        claim=claim,
        quality_threshold=threshold,
        source_title=raw_source["title"],
        source_type=raw_source["source_type"],
        source_context=truncated_context
    )
    
    prompt_hash = state_manager.generate_hash(prompt, "researcher_eval")
    cached = state_manager.get_llm_cache(prompt_hash)
    
    if cached:
        try:
            data = json.loads(cached)
            # Ellenőrizzük a küszöböt is, hiába releváns, ha gyenge a minősége
            is_valid = data.get("is_relevant", False) and data.get("quality_score", 0) >= threshold
            return is_valid, data.get("quality_score", 0), data.get("extracted_fact", "")
        except Exception:
            return False, 0, ""

    # FONTOS: LLM hívás placeholder. 
    raise NotImplementedError("Az LLM hívást a Forrás-értékelőhöz az engine.py végzi.")

# --- Fő Metódus ---

def execute_research(graph: ArgumentGraph, context: TaskContext, state_manager: StateManager, session_id: str):
    """
    A Researcher fő orchestrátor metódusa.
    Végigmegy a DAG csomópontjain, letölti a forrásokat, kiértékeli, majd lementi a Fact Store-ba.
    """
    config = load_config()
    depth = context.research_depth
    
    if depth == "none":
        state_manager.log_action(session_id, "researcher", "skipped", {"reason": "research_depth is none"})
        return

    total_facts_found = 0

    for node in graph.nodes:
        # Ha a csomópont egy Axióma, ritkán igényel kutatást, de ha van query, megnézzük
        if not node.research_queries:
            continue
            
        facts_for_node = 0
            
        for query in node.research_queries:
            raw_sources = mock_fetch_from_apis(query, depth, config)
            
            valid_sources_for_claim = []
            extracted_claim_text = ""
            
            for raw_src in raw_sources:
                try:
                    is_valid, quality, extracted_fact = evaluate_source_with_llm(node.claim, raw_src, config, state_manager)
                    
                    if is_valid:
                        source = SourceRecord(
                            source_id=raw_src["source_id"],
                            title=raw_src["title"],
                            authors=raw_src.get("authors", "Ismeretlen Szerző"),
                            author_id=raw_src.get("author_id"),
                            institution=raw_src.get("institution", "Ismeretlen Intézmény"),
                            source_type=raw_src["source_type"],
                            quality=quality
                        )
                        valid_sources_for_claim.append(source)
                        extracted_claim_text = extracted_fact # Használjuk az egyik kivonatolást
                        
                except NotImplementedError:
                    # Szimuláljuk a sikert (Mock)
                    dummy_source_id = f"src_{uuid.uuid4().hex[:8]}"
                    valid_sources_for_claim.append(
                        SourceRecord(
                            source_id=dummy_source_id, 
                            title=f"Mock Source {len(valid_sources_for_claim)+1}", 
                            authors=raw_src.get("authors", f"Szerző_{len(valid_sources_for_claim)}"),
                            institution=raw_src.get("institution", f"Intézmény_{len(valid_sources_for_claim)}"),
                            source_type="journal", 
                            quality=85
                        )
                    )
                    extracted_claim_text = "Mocked Fact text for claim"
            
            # Multi-Source Grounding (Dual-Grounding) ellenőrzése + Source Independence Check (ORCID/Hash alapú)
            if valid_sources_for_claim:
                independent_sources = []
                seen_author_ids = set()
                
                for src in valid_sources_for_claim:
                    # Determinisztikus deduplikáció: ORCID, vagy SHA-256 hash a normalizált szerzőből és intézményből
                    if src.author_id:
                        auth_id = src.author_id
                    else:
                        norm_auth = normalize_author(src.authors if src.authors else f"unknown_{src.source_id}")
                        norm_inst = normalize_institution(src.institution if src.institution else "")
                        combined_str = f"{norm_auth}|{norm_inst}"
                        auth_id = hashlib.sha256(combined_str.encode('utf-8')).hexdigest()
                    
                    if auth_id not in seen_author_ids:
                        independent_sources.append(src)
                        seen_author_ids.add(auth_id)
                
                # A confidence score-alapú (0.0 - 1.0)
                num_indep = len(independent_sources)
                if num_indep >= 3:
                    confidence = 1.0
                elif num_indep == 2:
                    confidence = 0.7
                elif num_indep == 1:
                    confidence = 0.3
                else:
                    confidence = 0.0
                
                fact = FactRecord(
                    fact_id=f"fact_{uuid.uuid4().hex[:8]}",
                    source_ids=[s.source_id for s in valid_sources_for_claim],
                    claim_text=extracted_claim_text,
                    context="Aggregated context from sources",
                    confidence=confidence
                )
                
                save_fact_to_db(state_manager.db_path, node.id, valid_sources_for_claim, fact)
                facts_for_node += 1
                total_facts_found += 1
        
        # Frissítjük a DAG node állapotát
        if facts_for_node > 0:
            node.grounding_status = "grounded"
        else:
            node.grounding_status = "ungrounded"
            
    state_manager.log_action(session_id, "researcher", "completed", {"total_facts_found": total_facts_found})
    
    # Checkpoint mentése a kutatás után
    state_manager.save_checkpoint(state_manager.load_checkpoint(session_id) or UPVSEngineState(session_id=session_id, status="researching"))
