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
A feladatod a generált szekció-változatok (draftok) szigorú értékelése.

# AZ ÁGENS FELADATA: {agent_description}

# BEMENET
Feladat/Téma: {task_title}
Csomópont állítása (Claim): {claim}

{drafts_text}

# FACT STORE TÉNYEK (Grounding Verifier számára kritikus):
{facts_list}

# KIMENETI FORMÁTUM (KIZÁRÓLAG ÉRVÉNYES JSON)
{{
  "evaluations": [
    {{
      "draft_index": 0,
      "thought_process": "Lépésről-lépésre elemzés: Először is megvizsgálom a premisszákat...",
      "score": 85,
      "veto_raised": false,
      "conclusion": "Összefoglalva: A szöveg megfelelő, kisebb stilisztikai hibákkal."
    }},
    {{
      "draft_index": 1,
      "thought_process": "...",
      "score": 40,
      "veto_raised": true,
      "conclusion": "Vétó, mert hiányzik a logikai ív."
    }}
  ]
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

def evaluate_drafts_batched(drafts: List[Dict[str, str]], node: ArgumentNode, context: TaskContext, state_manager: StateManager, session_id: str) -> List[DelphiDraftResult]:
    """A 4 ágens kiértékeli az összes draftot egyetlen (vagy 4) hívással draftonkénti 4 hívás helyett."""
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    temperatures = config["engine_parameters"]["agent_council"].get("temperatures", {})
    profiles = get_agent_profiles()
    
    facts = get_facts_for_node(state_manager.db_path, node.id)
    facts_str = "\n".join([f"[{f.fact_id}]: {f.claim_text}" for f in facts])
    
    drafts_text = ""
    for i, d in enumerate(drafts):
        drafts_text += f"\n--- DRAFT {i} ({d['branch']}) ---\n{d['text']}\n"
        
    draft_evals = [[] for _ in drafts]
    
    for agent_name, agent_desc in profiles.items():
        if agent_name == "Grounding Verifier":
            required_ratio = 0.9 if context.grounding_level == "strict" else 0.6
            for i, draft in enumerate(drafts):
                score, veto, reason = verify_grounding_deterministic(draft["text"], facts, context.grounding_level, required_ratio)
                draft_evals[i].append(SectionEvaluation(
                    agent_name=agent_name, 
                    thought_process="Automatikus determinisztikus futás.",
                    scores={"grounding": score}, 
                    veto_raised=veto,
                    conclusion=reason
                ))
            continue
            
        prompt = COUNCIL_SYSTEM_PROMPT.format(
            agent_role=agent_name,
            agent_description=agent_desc,
            task_title=node.section_title,
            claim=node.claim,
            drafts_text=drafts_text,
            facts_list=facts_str
        )
        
        prompt_hash = state_manager.generate_hash(prompt, f"council_batched_{agent_name}")
        cached = state_manager.get_llm_cache(prompt_hash)
        
        try:
            if cached:
                data = json.loads(cached)
            else:
                # LLM MOCK
                data = {"evaluations": [{"draft_index": i, "thought_process": "Mocked CoT", "score": 85, "veto_raised": False, "conclusion": "Mocked LLM batched evaluation pass."} for i in range(len(drafts))]}
                
            for eval_data in data.get("evaluations", []):
                i = eval_data.get("draft_index", 0)
                if i >= len(drafts): continue
                
                score = eval_data.get("score", 0)
                veto = eval_data.get("veto_raised", False)
                
                thresholds = context.council_thresholds
                if agent_name == "Domain Expert" and score < thresholds.domain_expert_veto:
                    veto = True
                elif agent_name == "De-biaser" and score < thresholds.debiaser_veto:
                    veto = True
                    
                draft_evals[i].append(SectionEvaluation(
                    agent_name=agent_name,
                    thought_process=eval_data.get("thought_process", ""),
                    scores={"overall": score},
                    veto_raised=veto,
                    conclusion=eval_data.get("conclusion", "")
                ))
                
                agent_temp = temperatures.get(agent_name, 0.0)
                state_manager.log_agent_memory(
                    session_id=session_id,
                    agent_name=agent_name.lower().replace(" ", "_"),
                    data={
                        "node_id": node.id,
                        "draft_index": i,
                        "score": score,
                        "veto": veto,
                        "thought_process": eval_data.get("thought_process", ""),
                        "conclusion": eval_data.get("conclusion", ""),
                        "temperature": agent_temp
                    }
                )
        except Exception as e:
            for i in range(len(drafts)):
                draft_evals[i].append(SectionEvaluation(
                    agent_name=agent_name, 
                    thought_process="Hiba történt.", 
                    scores={"overall": 0}, 
                    veto_raised=True, 
                    conclusion=f"Parse Error: {e}"
                ))
                
    results = []
    for i, draft in enumerate(drafts):
        total_score = sum(ev.scores.get("overall", ev.scores.get("grounding", 0)) for ev in draft_evals[i])
        avg_score = total_score / len(profiles) if profiles else 0
        is_vetoed = any(ev.veto_raised for ev in draft_evals[i])
        
        results.append(DelphiDraftResult(
            draft_id=state_manager.generate_hash(draft["text"]),
            draft_text=draft["text"],
            branch_type=draft["branch"],
            evaluations=draft_evals[i],
            average_score=avg_score,
            is_vetoed=is_vetoed
        ))
        
    return results

def council_session(node: ArgumentNode, drafts: List[Dict[str, str]], context: TaskContext, state_manager: StateManager, session_id: str) -> Tuple[Optional[DelphiDraftResult], Optional[DelphiDraftResult], str]:
    """
    Lefuttatja a tanácsot a node-hoz tartozó draftokon kötegelve. 
    Visszaadja: (best_valid_draft, best_vetoed_draft, combined_feedback)
    """
    results = evaluate_drafts_batched(drafts, node, context, state_manager, session_id)
        
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
                feedbacks.append(f"- {ev.agent_name}: {ev.thought_process} -> {ev.conclusion}")
        combined_feedback = "\n".join(feedbacks)
        
    return best_valid, best_vetoed, combined_feedback
