"""
UPVS-Engine — DAG Reviewer (2.5. Réteg)
Feladata: A Planner által generált DAG szigorú logikai elő-auditja, mielőtt a rendszer kutatni kezdene.
"""
import json
import yaml
from pathlib import Path
from typing import Tuple

from models import TaskContext, ArgumentGraph
from state_manager import StateManager

REVIEWER_SYSTEM_PROMPT = """
# SZEREPKÖR
Te vagy a UPVS-Engine 2.5. rétege: a DAG Reviewer (Váz-ellenőrző és Logikai Pre-Audit).
A feladatod a Planner által készített logikai gráf SZIGORÚ ellenőrzése, MIELŐTT a rendszer elkezdené a drága API hívásokat (Forráskutatás).

# ELLENŐRZENDŐ SZEMPONTOK
1. Érvelési logikák: A premisszákból logikusan következik-e a csomópont (Node) konklúziója? Vannak-e logikai ugrások, ahol hiányzik egy köztes csomópont?
2. Keresőkérdések minősége: A `research_queries` mezőben lévő kulcsszavak megfelelően fókuszáltak-e ahhoz, hogy tudományos (pl. PubMed) vagy üzleti adatbázisokban értékelhető találatot adjanak? Nem túl tágak (pl. "economy") vagy túl szűkek?
3. Struktúra: Az élek (Edges) helyesen kötik-e össze a csomópontokat (nincs-e körkörös hivatkozás, izolált csomópont)?

# BEMENET
Feladat típusa: {task_type}
Kutatás mélysége: {research_depth}
Gráf címe: {dag_title}

Gráf csomópontjai és élei:
{dag_content}

# KIMENETI FORMÁTUM (KIZÁRÓLAG ÉRVÉNYES JSON)
{{
  "is_approved": true/false,
  "fallacies_detected": ["szalmabáb", "ugrás a logikában" ... vagy üres lista, ha minden jó],
  "critique": "Részletes, konstruktív kritika a Planner számára. Melyik Node-nál mi a gond, hogyan javítsa. Ha elfogadod, hagyd üresen."
}}
"""

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def check_axiom_ratio(graph: ArgumentGraph, config: dict) -> Tuple[bool, str]:
    """Determinisztikus ellenőrzés a túl sok axiómára."""
    max_ratio = config["engine_parameters"].get("max_axiom_ratio", 0.3)
    if not graph.nodes:
        return False, "A gráf üres (0 csomópont)."
    
    axiom_count = sum(1 for node in graph.nodes if node.node_type == "axiom")
    ratio = axiom_count / len(graph.nodes)
    
    if ratio > max_ratio:
        return False, f"Axióma hiba: A csomópontok {ratio*100:.1f}%-a axióma (max engedélyezett: {max_ratio*100:.1f}%). A gráf túl sok bizonyítatlan alapfeltevésre épít. Használj több 'derivation' típusú levezetést!"
    return True, ""

def build_reviewer_prompt(graph: ArgumentGraph, context: TaskContext) -> str:
    """A rendszerpromptba behelyettesíti a DAG-ot és a kontextust."""
    # DAG egyszerűsített szöveges reprezentációja a promptba
    dag_content = []
    for node in graph.nodes:
        dag_content.append(f"- NODE [{node.id}] ({node.node_type}): {node.section_title} | Claim: {node.claim} | Queries: {node.research_queries}")
    for edge in graph.edges:
        dag_content.append(f"- EDGE: {edge.source} -> {edge.target} [{edge.relation}]")

    return REVIEWER_SYSTEM_PROMPT.format(
        task_type=context.task_type,
        research_depth=context.research_depth,
        dag_title=graph.title,
        dag_content="\n".join(dag_content)
    )

def parse_reviewer_response(raw_json: str) -> Tuple[bool, str]:
    """
    Kinyeri az LLM válaszából az engedélyezést és a kritikát.
    Visszatérés: (is_approved, critique)
    """
    try:
        data = json.loads(raw_json)
        is_approved = data.get("is_approved", False)
        critique = data.get("critique", "")
        return is_approved, critique
    except Exception as e:
        # Ha hibás a JSON, inkább dobja vissza kritikával
        return False, f"Reviewer JSON Parse Error: {e}"

def review_dag(graph: ArgumentGraph, context: TaskContext, state_manager: StateManager, session_id: str) -> Tuple[bool, str]:
    """
    A Reviewer fő metódusa. Először lefuttatja a determinisztikus szabályokat,
    majd felépíti a promptot az LLM alapú logikai audithoz.
    """
    config = load_config()
    
    # 1. Determinisztikus ellenőrzés (kód)
    axiom_ok, axiom_msg = check_axiom_ratio(graph, config)
    if not axiom_ok:
        state_manager.log_action(session_id, "dag_reviewer", "rejected_by_code", {"reason": axiom_msg})
        return False, axiom_msg

    # 2. LLM hívás felkészítése
    prompt = build_reviewer_prompt(graph, context)
    prompt_hash = state_manager.generate_hash(prompt, "dag_reviewer")

    cached = state_manager.get_llm_cache(prompt_hash)
    if cached:
        is_approved, critique = parse_reviewer_response(cached)
        state_manager.log_action(session_id, "dag_reviewer", "cache_hit", {"is_approved": is_approved})
        return is_approved, critique

    # FONTOS: LLM hívás placeholder az engine.py-nak.
    raise NotImplementedError(
        "Az LLM hívást az engine.py végzi. "
        "Használd a build_reviewer_prompt() és parse_reviewer_response() metódusokat."
    )
