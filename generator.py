"""
UPVS-Engine — Generator (4. Réteg, 1. rész)
Feladata: A DAG egy adott csomópontjához 3 különböző (Konzervatív, Kritikai, Szintetikus) 
szövegváltozatot (draftot) generálni, a Fact Store-ból kinyert tények szigorú felhasználásával.
"""
import sqlite3
import yaml
from pathlib import Path
from typing import List, Dict
from pydantic import BaseModel, Field

from models import TaskContext, ArgumentNode, FactRecord
from state_manager import StateManager
from llm_utils import enforce_pydantic_schema

class QuoteFirstDraft(BaseModel):
    extracted_quotes: List[str] = Field(description="A felhasznált, SZÓ SZERINTI idézetek listája a Fact Store-ból.")
    draft_text: str = Field(description="A végleges szekció szövege, mely KIZÁRÓLAG a kigyűjtött idézetekre épülhet.")

GENERATOR_SYSTEM_PROMPT = """
# SZEREPKÖR
Te vagy a UPVS-Engine 4. rétege: a Multi-Branch Szöveggenerátor.
A feladatod a dokumentum egy adott szekciójának (Node) megírása.

# KÖTELEZŐ SZABÁLYOK (GROUNDING LEVEL: {grounding_level})
Kaptál egy listát validált tényekből (Fact Store). Minden ténynek van egy [fact_id] azonosítója.
{grounding_rules}
FONTOS: Ha egy tény mellett a [LOW_CONFIDENCE] jelölést látod, KÖTELEZŐ explicit jelezned a generált szövegben (pl. "egyes források szerint", "bár nem megerősített"), hogy az állítás bizonytalan, és KÖTELEZŐ a [LOW_CONFIDENCE] taget fizikailag is benne hagyni a szövegtestben a [fact_id] mellett!

# BEMENET
Szekció címe: {section_title}
Fő állítás (Claim): {claim}
Premisszák, amikre építened kell: {premises}
Célközönség/Regiszter: {audience}
Generálási ág (Branch) stílusa: {branch_style}

# RENDELKEZÉSRE ÁLLÓ TÉNYEK (FACT STORE)
{facts_list}

# FELADAT (QUOTE-FIRST)
1. Először KÖTELEZŐ kigyűjtened a Fact Store-ból a szó szerinti, legfontosabb idézeteket (extracted_quotes), amik alátámasztják a Claim-et.
2. Csak és kizárólag ezen idézetek alapján írhatod meg a szöveget (draft_text), szigorúan elhelyezve a [fact_id] markereket.

A kimenet KIZÁRÓLAG a kért JSON struktúra lehet!
"""

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def get_facts_for_node(db_path: str, node_id: str) -> List[FactRecord]:
    """Lekéri az SQLite Fact Store-ból az adott csomóponthoz tartozó tényeket és a multi-source azonosítókat."""
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.fact_id, f.claim_text, f.context, f.confidence
            FROM facts f
            JOIN node_facts nf ON f.fact_id = nf.fact_id
            WHERE nf.node_id = ?
        ''', (node_id,))
        fact_rows = cursor.fetchall()
        
        results = []
        for row in fact_rows:
            fact_id, claim_text, context, confidence = row
            cursor.execute('SELECT source_id FROM fact_sources WHERE fact_id = ?', (fact_id,))
            source_ids = [s[0] for s in cursor.fetchall()]
            
            results.append(FactRecord(
                fact_id=fact_id,
                source_ids=source_ids,
                claim_text=claim_text,
                context=context,
                confidence=confidence
            ))
        return results

def build_grounding_rules(level: str) -> str:
    if level == "strict":
        return "SZIGORÚ: KIZÁRÓLAG a lenti tényekből dolgozhatsz. Minden egyes tényállításod mögé KÖTELEZŐ odaírni a pontos [fact_id]-t. Külső tudást nem használhatsz!"
    elif level == "moderate":
        return "MÉRSÉKELT: A lenti tényeket használd alapként és hivatkozd őket [fact_id]-vel, de kiegészítheted őket általánosan elfogadott, közismert összefüggésekkel."
    else:
        return "LAZA (None): A lenti tények csak inspirációk. Szabadon generálhatsz saját tudásodból, hivatkozás nem kötelező."

def get_branch_style_instructions(branch: str) -> str:
    if branch == "conservative":
        return "Konzervatív: Objektív, száraz, tény-orientált leírás. Semmi spekuláció, kizárólag az adatok egyértelmű közlése."
    elif branch == "critical":
        return "Kritikai: Elemző megközelítés. Keresd az ellentmondásokat, mutass rá a premisszák korlátaira és a lehetséges ellenérvekre."
    elif branch == "synthetic":
        return "Szintetikus: Összekötő, narratív stílus. Próbáld a tényeket egy nagyobb, összefüggő történetté formálni, rávilágítva az ok-okozati láncokra."
    return ""

def generate_drafts_for_node(node: ArgumentNode, context: TaskContext, state_manager: StateManager, session_id: str, feedback: str = "", branches: List[str] = None) -> List[Dict[str, str]]:
    """
    Legenerálja a kért ágakat (alapértelmezetten mind a 3-at: conservative, critical, synthetic) a csomóponthoz.
    Visszatérés: [{"branch": "...", "text": "...", "temp": 0.1}, ...]
    """
    if branches is None:
        branches = ["conservative", "critical", "synthetic"]
        
    config = load_config()
    facts = get_facts_for_node(state_manager.db_path, node.id)
    
    facts_list_str = ""
    for f in facts:
        conf_prefix = "[LOW_CONFIDENCE] " if f.confidence < 0.7 else ""
        facts_list_str += f"- [{f.fact_id}]: {conf_prefix}{f.claim_text}\n"
    if not facts_list_str:
        facts_list_str = "(Nincsenek elérhető tények a Fact Store-ban ehhez a csomóponthoz.)"

    grounding_rules = build_grounding_rules(context.grounding_level)
    results = []

    for branch in branches:
        branch_style = get_branch_style_instructions(branch)
        temp = config["engine_parameters"]["generator_temperatures"].get(branch, 0.4)
        
        prompt = GENERATOR_SYSTEM_PROMPT.format(
            grounding_level=context.grounding_level,
            grounding_rules=grounding_rules,
            section_title=node.section_title,
            claim=node.claim,
            premises=", ".join(node.premises) if node.premises else "Nincs megadva",
            audience=context.target_audience,
            branch_style=branch_style,
            facts_list=facts_list_str
        )
        
        if feedback:
            prompt += f"\n\n# TANÁCSI VISSZAUTASÍTÁS ÉS KRITIKA (ÚJRAGENERÁLÁS)\nAz előző verziódat az Agent Council megvétózta az alábbi indokokkal:\n{feedback}\nKérlek, javítsd ki ezeket a hibákat a mostani generálás során!"
            
        prompt_hash = state_manager.generate_hash(prompt, f"generator_{branch}")
        cached_text = state_manager.get_llm_cache(prompt_hash)
        
        if cached_text:
            results.append({"branch": branch, "text": cached_text, "temp": temp})
            continue

        # V15: Gemini API hívás natív JSON (Pydantic) kicsomagolással
        model_name = config.get("engine_parameters", {}).get("generator_model", "gemini-1.5-flash")
        
        try:
            parsed_draft = enforce_pydantic_schema(
                model_name=model_name,
                schema_model=QuoteFirstDraft,
                system_prompt="Te egy JSON generáló rendszer vagy.",
                user_prompt=prompt,
                max_retries=2,
                base_temperature=temp
            )
            
            # Mentés cache-be
            state_manager.set_llm_cache(prompt_hash, model_name, parsed_draft.draft_text)
            
            results.append({"branch": branch, "text": parsed_draft.draft_text, "temp": temp})
            
            # Telemetria: elmentjük az idézeteket is
            state_manager.log_action(session_id, "generator", "quote_first_extraction", {
                "branch": branch,
                "quotes_extracted": len(parsed_draft.extracted_quotes)
            })
            
        except Exception as e:
            state_manager.log_action(session_id, "generator", "generation_failed", {"error": str(e), "branch": branch})
            dummy_text = f"[SYSTEM WARNING] Generálási hiba a {branch} ágon: {e}"
            results.append({"branch": branch, "text": dummy_text, "temp": temp})

    state_manager.log_action(session_id, "generator", "drafts_created", {
        "node_id": node.id,
        "branches_generated": len(results)
    })

    return results
