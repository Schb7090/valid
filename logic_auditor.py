"""
UPVS-Engine — Logical Arc Auditor (5. Réteg)
Feladata: A már legenerált és elfogadott szekciók (csomópontok) közötti 
logikai átmenetek (élek) globális ellenőrzése, ellentmondások és érvelési hibák kiszűrése.
"""
import json
import yaml
from pathlib import Path
from typing import List, Dict

from models import TaskContext, ArgumentGraph, ArgumentEdge, EdgeAuditResult
from state_manager import StateManager

AUDITOR_SYSTEM_PROMPT = """
# SZEREPKÖR
Te vagy a UPVS-Engine 5. rétege: a Globális Logikai Ív Ellenőrző (Logical Arc Auditor).
A feladatod, hogy megvizsgáld két egymást követő szekció (Node A -> Node B) közötti logikai kapcsolatot.

# ELLENŐRZENDŐ SZEMPONTOK
1. Entailment (Következtetés): A 'Source' szekció állításai és tartalma logikailag megalapozzák-e a 'Target' szekciót?
2. Ellentmondás (Contradiction): Van-e bármilyen ténybeli vagy logikai ellentmondás a két szöveg között?
3. Érvelési hibák (Fallacies): Keresd az alábbi logikai hibákat a kapcsolódásban: 
   {fallacy_checklist}
4. Reláció helyessége: A tervezett kapcsolat ({relation_type}) ténylegesen megvalósul-e a szövegek között?

# BEMENET
Tervezett logikai kapcsolat (Relation): {relation_type}

--- SOURCE SZEKCIÓ [{source_id}] ---
Cím: {source_title}
Szöveg:
{source_text}

--- TARGET SZEKCIÓ [{target_id}] ---
Cím: {target_title}
Szöveg:
{target_text}

# KIMENETI FORMÁTUM (KIZÁRÓLAG ÉRVÉNYES JSON)
{{
  "entailment_score": 85,
  "detected_fallacies": ["non_sequitur"], // Vagy üres lista, ha tiszta
  "relation_correct": true,
  "reasoning": "Rövid, fókuszált indoklás az átmenet minőségéről és az esetleges ellentmondásokról.",
  "verdict": "pass" // Lehetőségek: "pass", "weak", "fail"
}}
"""

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def audit_single_edge(edge: ArgumentEdge, graph: ArgumentGraph, final_sections: Dict[str, str], config: dict, state_manager: StateManager, session_id: str) -> EdgeAuditResult:
    """Megvizsgál egyetlen élt (A -> B kapcsolat)."""
    
    source_text = final_sections.get(edge.source)
    target_text = final_sections.get(edge.target)
    
    # Ha valamelyik szekció hiányzik (pl. Fallback miatt elbukott), az él is elbukik.
    if not source_text or not target_text:
        return EdgeAuditResult(
            edge_source=edge.source,
            edge_target=edge.target,
            entailment_score=0,
            detected_fallacies=["missing_section"],
            relation_correct=False,
            reasoning="Az egyik szekció hiányzik, a logikai kapcsolat nem vizsgálható.",
            verdict="fail"
        )

    # Node metaadatok kinyerése
    source_node = next((n for n in graph.nodes if n.id == edge.source), None)
    target_node = next((n for n in graph.nodes if n.id == edge.target), None)
    
    fallacy_list = ", ".join(config["prompts"].get("fallacy_checklist", []))
    
    prompt = AUDITOR_SYSTEM_PROMPT.format(
        fallacy_checklist=fallacy_list,
        relation_type=edge.relation,
        source_id=edge.source,
        source_title=source_node.section_title if source_node else "Ismeretlen",
        source_text=source_text,
        target_id=edge.target,
        target_title=target_node.section_title if target_node else "Ismeretlen",
        target_text=target_text
    )
    
    prompt_hash = state_manager.generate_hash(prompt, f"auditor_{edge.source}_{edge.target}")
    cached = state_manager.get_llm_cache(prompt_hash)
    
    try:
        if cached:
            data = json.loads(cached)
        else:
            # LLM MOCK placeholder
            data = {
                "entailment_score": 90,
                "detected_fallacies": [],
                "relation_correct": True,
                "reasoning": "A logikai ív stabil, az A szekció tényei alátámasztják B konklúzióját.",
                "verdict": "pass"
            }
            
        return EdgeAuditResult(
            edge_source=edge.source,
            edge_target=edge.target,
            entailment_score=data.get("entailment_score", 0),
            detected_fallacies=data.get("detected_fallacies", []),
            relation_correct=data.get("relation_correct", False),
            reasoning=data.get("reasoning", ""),
            verdict=data.get("verdict", "fail")
        )
    except Exception as e:
        return EdgeAuditResult(
            edge_source=edge.source,
            edge_target=edge.target,
            entailment_score=0,
            detected_fallacies=["parse_error"],
            relation_correct=False,
            reasoning=f"LLM Parse Error: {e}",
            verdict="fail"
        )

def execute_logic_audit(graph: ArgumentGraph, final_sections: Dict[str, str], state_manager: StateManager, session_id: str) -> List[EdgeAuditResult]:
    """
    Végigmegy a DAG összes élén (átmenetén), és megvizsgálja a logikai konzisztenciát.
    Az eredményeket az Output Assembler fogja felhasználni az átvezetések elsimításánál.
    """
    config = load_config()
    results = []
    
    for edge in graph.edges:
        audit_res = audit_single_edge(edge, graph, final_sections, config, state_manager, session_id)
        results.append(audit_res)
        
    # Naplózzuk a gyanús éleket
    weak_or_fail = [r for r in results if r.verdict in ["weak", "fail"]]
    state_manager.log_action(session_id, "logic_auditor", "audit_completed", {
        "total_edges": len(results),
        "issues_found": len(weak_or_fail),
        "details": [{"source": r.edge_source, "target": r.edge_target, "verdict": r.verdict} for r in weak_or_fail]
    })
    
    return results
