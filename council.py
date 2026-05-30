"""
UPVS-Engine — Agent Council (4. Réteg, 2. rész)
Feladata: A Generátor által készített 3 draft kiértékelése 4 szakértő ágens bevonásával 
(Delphi módszer), a legjobb draft kiválasztása, vagy újragenerálás kikényszerítése vétó esetén.
"""
import json
import yaml
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from models import TaskContext, ArgumentNode, SectionEvaluation, DelphiDraftResult, FactRecord
from state_manager import StateManager
from generator import get_facts_for_node

COUNCIL_SYSTEM_PROMPT = """
# SZEREPKÖR
Te a UPVS-Engine "Bölcsek Tanácsának" egy specifikus tagja vagy: {agent_role}.
A feladatod egy nyers szekció-draft szigorú értékelése.

# AZ ÁGENS FELADATA: {agent_description}

# BEMENET
Feladat/Téma: {task_title}
Csomópont állítása (Claim): {claim}

Értékelendő Draft szövege:
\"\"\"{draft_text}\"\"\"

# FACT STORE TÉNYEK (Grounding Verifier számára kritikus):
{facts_list}

# KIMENETI FORMÁTUM (KIZÁRÓLAG JSON)
{{
  "score": 85,  // Értékelés 1-100 között a te szakterületed alapján
  "reasoning": "Részletes indoklás, miért adtad ezt a pontszámot. Milyen hibákat találtál?",
  "veto_raised": false  // true, ha a hiba annyira súlyos, hogy teljesen elutasítod a szöveget
}}
"""

def get_agent_profiles() -> Dict[str, str]:
    return {
        "Domain Expert": "Értékeld a szakmai mélységet, az érvelés robusztusságát és a stílus professzionalizmusát. Ha felszínes vagy banális a szöveg, pontozd le.",
        "Logical Arc Auditor": "Kizárólag a belső logikát figyeld! A bekezdések logikusan következnek egymásból? A premisszák valóban alátámasztják a Claim-et?",
        "De-biaser": "Keresd a torzításokat! Van benne 'sycophancy' (bólogató AI klisék), szalmabáb érvelés, vagy egyoldalú bemutatás? Ha részrehajló, pontozd le.",
        "Grounding Verifier": "A LEGADATVEZÉRELTEBB ÁGENS. Számold meg a tényállításokat a szövegben. Ellenőrizd, hogy a szövegben lévő [fact_id]-k valóban szerepelnek-e a Fact Store-ban, és a forrás tényleg azt mondja-e, amit az AI írt."
    }

def verify_grounding_deterministic(draft_text: str, facts: List[FactRecord], grounding_level: str, required_ratio: float) -> Tuple[int, bool, str]:
    """
    A Grounding Verifier munkáját segítő determinisztikus kód.
    Kikeresi a szövegből a [fact_id] markereket, és megnézi, arányaiban megvannak-e.
    """
    if grounding_level == "none":
        return 100, False, "Grounding kikapcsolva, automatikus 100 pont."
        
    found_markers = re.findall(r'\[fact_[a-z0-9]+\]', draft_text)
    unique_markers_in_text = set(found_markers)
    available_fact_ids = {f"[{f.fact_id}]" for f in facts}
    
    # 1. Hivatkozik-e nem létező (hallucinált) fact_id-re?
    hallucinated = unique_markers_in_text - available_fact_ids
    if hallucinated:
        return 10, True, f"VÉTÓ! A szöveg nem létező fact_id-kra hivatkozik: {', '.join(hallucinated)}"
        
    # 2. Megvan a kellő lefedettség? 
    # (Egyszerűsített metrika: a rendelkezésre álló tények hány százalékát használta fel)
    if not available_fact_ids:
        if grounding_level == "strict" and len(draft_text) > 50:
            return 20, True, "VÉTÓ! Szigorú grounding van érvényben, de nincsenek hivatkozások."
        return 100, False, "Nincs elérhető tény, de a fallback engedi."
        
    ratio = len(unique_markers_in_text) / len(available_fact_ids)
    if ratio < required_ratio:
        return int(ratio * 100), True, f"VÉTÓ! A rendelkezésre álló tények csupán {ratio*100:.0f}%-át használta (Elvárt: {required_ratio*100:.0f}%)."
        
    return 100, False, "A [fact_id] hivatkozások formai és mennyiségi szintje megfelelő."

def evaluate_draft(draft: Dict[str, str], node: ArgumentNode, context: TaskContext, state_manager: StateManager, session_id: str) -> DelphiDraftResult:
    """A 4 ágens kiértékel egyetlen draftot."""
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    temperatures = config["engine_parameters"]["agent_council"].get("temperatures", {})
    profiles = get_agent_profiles()
    evaluations = []
    total_score = 0
    is_vetoed = False
    
    facts = get_facts_for_node(state_manager.db_path, node.id)
    facts_str = "\n".join([f"[{f.fact_id}]: {f.claim_text}" for f in facts])
    
    for agent_name, agent_desc in profiles.items():
        # 1. Determinisztikus Grounding Verifier override
        if agent_name == "Grounding Verifier":
            required_ratio = 0.9 if context.grounding_level == "strict" else 0.6
            score, veto, reason = verify_grounding_deterministic(draft["text"], facts, context.grounding_level, required_ratio)
            # Ha a kód vétóz, be sem hívjuk az LLM-et
            if veto:
                evaluations.append(SectionEvaluation(agent_name=agent_name, scores={"grounding": score}, reasoning=reason, veto_raised=True))
                is_vetoed = True
                continue

        # 2. LLM Értékelés
        prompt = COUNCIL_SYSTEM_PROMPT.format(
            agent_role=agent_name,
            agent_description=agent_desc,
            task_title=node.section_title,
            claim=node.claim,
            draft_text=draft["text"],
            facts_list=facts_str
        )
        
        prompt_hash = state_manager.generate_hash(prompt, f"council_{agent_name}")
        cached = state_manager.get_llm_cache(prompt_hash)
        
        try:
            if cached:
                data = json.loads(cached)
            else:
                # LLM MOCK
                data = {"score": 85, "reasoning": "Mocked LLM evaluation pass.", "veto_raised": False}
                
            score = data.get("score", 0)
            veto = data.get("veto_raised", False)
            
            # Dinamikus vétó küszöbök ellenőrzése a Router (TaskContext) alapján
            thresholds = context.council_thresholds
            if agent_name == "Domain Expert" and score < thresholds.domain_expert_veto:
                veto = True
            elif agent_name == "De-biaser" and score < thresholds.debiaser_veto:
                veto = True
                
            evaluations.append(SectionEvaluation(
                agent_name=agent_name,
                scores={"overall": score},
                reasoning=data.get("reasoning", ""),
                veto_raised=veto
            ))
            
            # --- JSON MEMORY POOL LOGGING ---
            agent_temp = temperatures.get(agent_name, 0.0)
            state_manager.log_agent_memory(
                session_id=session_id,
                agent_name=agent_name.lower().replace(" ", "_"),
                data={
                    "node_id": node.id,
                    "claim": node.claim,
                    "draft_branch": draft["branch"],
                    "score": score,
                    "veto": veto,
                    "reasoning": data.get("reasoning", ""),
                    "temperature": agent_temp
                }
            )
            # -------------------------------
            
            total_score += score
            if veto:
                is_vetoed = True
                
        except Exception as e:
            evaluations.append(SectionEvaluation(agent_name=agent_name, scores={"overall": 0}, reasoning=f"Parse Error: {e}", veto_raised=True))
            is_vetoed = True

    avg_score = total_score / len(profiles) if profiles else 0
    return DelphiDraftResult(
        draft_id=state_manager.generate_hash(draft["text"]),
        draft_text=draft["text"],
        branch_type=draft["branch"],
        evaluations=evaluations,
        average_score=avg_score,
        is_vetoed=is_vetoed
    )

def council_session(node: ArgumentNode, drafts: List[Dict[str, str]], context: TaskContext, state_manager: StateManager, session_id: str) -> Tuple[Optional[DelphiDraftResult], Optional[DelphiDraftResult], str]:
    """
    Lefuttatja a tanácsot a node-hoz tartozó draftokon. 
    Visszaadja: (best_valid_draft, best_vetoed_draft, combined_feedback)
    """
    results = []
    for draft in drafts:
        res = evaluate_draft(draft, node, context, state_manager, session_id)
        results.append(res)
        
    state_manager.log_action(session_id, "council", "evaluations_completed", {"node_id": node.id, "draft_results": [{"branch": r.branch_type, "score": r.average_score, "veto": r.is_vetoed} for r in results]})

    valid_drafts = [r for r in results if not r.is_vetoed]
    vetoed_drafts = [r for r in results if r.is_vetoed]
    
    best_valid = max(valid_drafts, key=lambda x: x.average_score) if valid_drafts else None
    best_vetoed = max(vetoed_drafts, key=lambda x: x.average_score) if vetoed_drafts else None
    
    # Összesítjük a kritikákat az újrageneráláshoz, ha nincs érvényes draft
    combined_feedback = ""
    if not best_valid and best_vetoed:
        feedbacks = []
        for ev in best_vetoed.evaluations:
            if ev.veto_raised:
                feedbacks.append(f"- {ev.agent_name}: {ev.reasoning}")
        combined_feedback = "\n".join(feedbacks)
        
    return best_valid, best_vetoed, combined_feedback
