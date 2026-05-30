"""
UPVS-Engine — Output Assembler (6. Réteg, 1. rész)
Feladata: A finalizált szekciók összefűzése (topologikus sorrendben), az Anti-Frankenstein
simítás elvégzése, a [fact_id] markerek cseréje lábjegyzetekre, és az APA Irodalomjegyzék generálása.
"""
import re
import json
import sqlite3
import yaml
from pathlib import Path
from typing import List, Dict, Tuple

from models import TaskContext, ArgumentGraph, EdgeAuditResult, SourceRecord
from state_manager import StateManager

ASSEMBLER_SYSTEM_PROMPT = """
# SZEREPKÖR
Te vagy a UPVS-Engine 6. rétege: az Anti-Frankenstein Szerkesztő (Output Assembler).
Kaptál egy tényekkel teli, logikailag auditált dokumentum-vázat, amely azonban stílusában darabos ("Frankenstein-hatás") lehet, mivel különálló ágensek generálták a szekcióit.

# FELADATOD
1. Fűzd össze a megadott szekciókat egy folyékony, professzionális dokumentummá.
2. Használj megfelelő diskurzus-jelölőket (kötőszavakat, átvezetéseket) a szekciók között, figyelembe véve az Auditor figyelmeztetéseit (Edge Warnings).
3. SZABADSÁGFOK ({assembly_freedom}): {freedom_rules}

# KÖTELEZŐ SZABÁLY (TÉNYEK VÉDELME)
A szekciókban található [fact_id] markereket és a hozzájuk tartozó állításokat SZIGORÚAN TILOS megváltoztatni, törölni vagy áthelyezni! Ha átrendezed a mondatot, a [fact_id]-t mindig az állítás végén kell hagynod.

# BEMENET
Célközönség/Regiszter: {audience}

Auditor figyelmeztetések a logikai ugrásokhoz:
{edge_warnings}

Szekciók (topologikus sorrendben):
{sections_text}

# KIMENET
A végső, egybefüggő szöveg. Csak a szöveget add vissza, semmi mást!
"""

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def build_freedom_rules(level: str) -> str:
    if level == "strict":
        return "SZIGORÚ: KIZÁRÓLAG kötőszavakat és átvezetéseket adhatsz a bekezdések közé. A mondatok belső szerkezetéhez nem nyúlhatsz!"
    elif level == "moderate":
        return "MÉRSÉKELT: Átrendezheted a mondatszerkezetet az olvashatóság érdekében, de a tartalmat nem bővítheted ki új gondolatokkal."
    else:
        return "LAZA (Flexible): Szabadon átírhatod a stílust, a szerkezetet és a megfogalmazást a maximális élvezhetőségért."

def get_topological_order(graph: ArgumentGraph) -> List[str]:
    """A DAG élei alapján topologikusan sorba rendezi a node-okat (szekciókat)."""
    # Egyszerűsített topologikus rendezés (valós esetben Kahn algoritmusa)
    in_degree = {n.id: 0 for n in graph.nodes}
    adj = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        if e.target in in_degree and e.source in in_degree:
            in_degree[e.target] += 1
            adj[e.source].append(e.target)
            
    queue = [n for n in in_degree if in_degree[n] == 0]
    ordered = []
    
    while queue:
        n = queue.pop(0)
        ordered.append(n)
        for neighbor in adj[n]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
                
    # Fallback: ha kör van a gráfban (nem kéne lennie DAG-nál), csak visszaadjuk id szerint
    if len(ordered) != len(graph.nodes):
        return [n.id for n in graph.nodes]
    return ordered

def get_used_sources(db_path: str, fact_ids: List[str]) -> List[SourceRecord]:
    """Lekéri az SQLite-ból a ténylegesen felhasznált [fact_id]-k forrásait."""
    if not fact_ids:
        return []
    
    sources = {}
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in fact_ids)
        # Kiszedjük a 'fact_' prefixet, hogy illeszkedjen az adatbázishoz
        clean_ids = [fid.replace('[', '').replace(']', '') for fid in fact_ids]
        
        cursor.execute(f'''
            SELECT s.source_id, s.title, s.authors, s.institution, s.year, s.doi, s.url, s.source_type, s.quality
            FROM sources s
            JOIN fact_sources fs ON s.source_id = fs.source_id
            WHERE fs.fact_id IN ({placeholders})
        ''', clean_ids)
        
        for r in cursor.fetchall():
            if r[0] not in sources:
                sources[r[0]] = SourceRecord(
                    source_id=r[0], title=r[1], authors=r[2], institution=r[3], year=r[4], 
                    doi=r[5], url=r[6], source_type=r[7], quality=r[8]
                )
    return list(sources.values())

def generate_apa_bibliography(sources: List[SourceRecord]) -> str:
    """A forrásokat APA formátumra hozza."""
    if not sources:
        return ""
    
    lines = ["\n\n## Irodalomjegyzék\n"]
    # Rendezés szerző, majd év szerint
    sources_sorted = sorted(sources, key=lambda x: (str(x.authors), str(x.year)))
    
    for idx, src in enumerate(sources_sorted, 1):
        author = src.authors if src.authors else "Ismeretlen Szerző"
        year = f"({src.year})" if src.year else "(n.d.)"
        title = f"*{src.title}*."
        link = f" https://doi.org/{src.doi}" if src.doi else (f" {src.url}" if src.url else "")
        
        lines.append(f"[{idx}] {author} {year}. {title}{link}")
        
    return "\n".join(lines)

def replace_fact_markers_with_numbers(text: str, sources: List[SourceRecord], db_path: str) -> str:
    """Kicseréli a [fact_xxx] stringeket [1], [2] számokra az irodalomjegyzék alapján."""
    # Mapping source_id -> Index (APA szerint rendezve vannak)
    sources_sorted = sorted(sources, key=lambda x: (str(x.authors), str(x.year)))
    source_to_idx = {src.source_id: i+1 for i, src in enumerate(sources_sorted)}
    
    # Meg kell nézni, melyik fact_id melyik source_id-khez tartozik
    fact_to_sources = {}
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT fact_id, source_id FROM fact_sources")
        for fid, sid in cursor.fetchall():
            if fid not in fact_to_sources:
                fact_to_sources[fid] = []
            fact_to_sources[fid].append(sid)

    # Csere
    def replacer(match):
        fact_marker = match.group(0) # e.g. [fact_123]
        clean_id = fact_marker.replace('[', '').replace(']', '')
        if clean_id in fact_to_sources:
            source_ids = fact_to_sources[clean_id]
            indexes = []
            for source_id in source_ids:
                if source_id in source_to_idx:
                    indexes.append(str(source_to_idx[source_id]))
            if indexes:
                # Pl: [1, 3] formátum ha két forrása van
                return f"[{', '.join(sorted(indexes))}]"
        return fact_marker

    return re.sub(r'\[fact_[a-z0-9]+\]', replacer, text)

def assemble_document(graph: ArgumentGraph, final_sections: Dict[str, str], audit_results: List[EdgeAuditResult], context: TaskContext, state_manager: StateManager, session_id: str) -> str:
    """
    Összefűzi a szöveget, elsimítja LLM-mel (Anti-Frankenstein), majd legenerálja az Irodalomjegyzéket.
    """
    # 1. Topologikus sorrend
    ordered_nodes = get_topological_order(graph)
    raw_text_parts = []
    
    for nid in ordered_nodes:
        if nid in final_sections:
            title = next((n.section_title for n in graph.nodes if n.id == nid), "")
            raw_text_parts.append(f"### {title}\n{final_sections[nid]}\n")

    sections_text = "\n".join(raw_text_parts)
    
    # 2. Auditor Warningok összeszedése
    warnings = []
    for aud in audit_results:
        if aud.verdict in ["weak", "fail"]:
            warnings.append(f"Figyelem az átmenetnél ({aud.edge_source} -> {aud.edge_target}): {aud.reasoning}")
    edge_warnings = "\n".join(warnings) if warnings else "Nincs figyelmeztetés, a logikai ívek stabilak."

    # 3. LLM Anti-Frankenstein
    freedom = build_freedom_rules(context.assembly_freedom)
    prompt = ASSEMBLER_SYSTEM_PROMPT.format(
        freedom_rules=freedom,
        assembly_freedom=context.assembly_freedom,
        audience=context.target_audience,
        edge_warnings=edge_warnings,
        sections_text=sections_text
    )
    
    prompt_hash = state_manager.generate_hash(prompt, "assembler")
    cached = state_manager.get_llm_cache(prompt_hash)
    
    if cached:
        smoothed_text = cached
    else:
        # FONTOS: LLM hívás placeholder.
        smoothed_text = sections_text # Ideiglenesen a nyers szöveget használjuk
        
    # 4. Hivatkozások és Irodalomjegyzék generálása
    found_facts = list(set(re.findall(r'\[fact_[a-z0-9]+\]', smoothed_text)))
    used_sources = get_used_sources(state_manager.db_path, found_facts)
    
    final_text = replace_fact_markers_with_numbers(smoothed_text, used_sources, state_manager.db_path)
    bibliography = generate_apa_bibliography(used_sources)
    
    final_document = final_text + bibliography
    
    state_manager.log_action(session_id, "assembler", "completed", {"sources_used": len(used_sources)})
    return final_document
