"""
UPVS-Engine — Validator (4. Réteg, 2. rész)
Egyetlen erős LLM (V2/V3) és determinisztikus + LLM-alapú Grounding (V1) ellenőrzése.
"""
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel, Field

from models import TaskContext, ArgumentNode, SectionEvaluation, DelphiDraftResult, FactRecord
from state_manager import StateManager
from llm_utils import call_text, enforce_pydantic_schema
from generator import get_facts_for_node

class AtomicClaims(BaseModel):
    claims: List[str] = Field(description="A szöveg elemi (atomi) állításokra bontva. SZÓ SZERINTI részletek az eredeti szövegből.")

class EntailmentCheck(BaseModel):
    is_entailed: bool = Field(description="Igaz, ha a források TÉNYLEG tartalmazzák/alátámasztják ezt az állítást.")
    reasoning: str

class ValidatorOutput(BaseModel):
    v2_logic_passed: bool = Field(description="Igaz, ha a premisszák valóban alátámasztják a konklúziót, és nincs érvelési hiba.")
    v2_logic_feedback: str
    v3_quality_score: int = Field(description="A szöveg minősége és elfogulatlansága 0-100 között.", ge=0, le=100)
    v3_quality_feedback: str

def calculate_overlap(claim: str, facts: List[FactRecord]) -> float:
    """Kiszámolja a token-szintű átfedést a claim és a forrástények között."""
    claim_tokens = set(re.findall(r'\w+', claim.lower()))
    if not claim_tokens: return 0.0
    
    fact_tokens = set()
    for f in facts:
        fact_tokens.update(re.findall(r'\w+', f.claim_text.lower()))
        
    overlap = len(claim_tokens.intersection(fact_tokens))
    return overlap / len(claim_tokens)

def verify_grounding_v1(draft_text: str, facts: List[FactRecord], config: dict, state_manager: StateManager) -> Tuple[bool, str, str]:
    """
    V1: Atomic Claim Splitting -> Token Overlap Prefilter -> LLM Entailment.
    Visszatérés: (is_valid, cleaned_text, feedback)
    """
    split_model = config.get("engine_parameters", {}).get("split_model", "gemini-1.5-flash")
    overlap_hi = config.get("engine_parameters", {}).get("overlap_hi", 0.60)
    overlap_lo = config.get("engine_parameters", {}).get("overlap_lo", 0.15)
    
    # 1. Atomic Split
    try:
        atomic_result = enforce_pydantic_schema(
            model_name=split_model,
            schema_model=AtomicClaims,
            system_prompt="Bontsd fel a kapott szöveget független, elemi állításokra. Csak a lényegi információt tartalmazó mondatokat/tagmondatokat add vissza.",
            user_prompt=draft_text,
            max_retries=1
        )
    except Exception as e:
        return False, draft_text, f"V1 Grounding Hiba (Split): {e}"
        
    unsupported_claims = []
    
    for claim in atomic_result.claims:
        # 2. Token Overlap Prefilter
        overlap = calculate_overlap(claim, facts)
        
        if overlap >= overlap_hi:
            continue # Lexikálisan lefedett, biztonságos
        elif overlap < overlap_lo:
            unsupported_claims.append(claim) # Esélytelen
            continue
            
        # 3. LLM Entailment bizonytalan (LO < overlap < HI) esetén
        val_model = config.get("engine_parameters", {}).get("validator_model", "gemini-1.5-pro")
        facts_str = "\n".join([f"- {f.claim_text}" for f in facts])
        
        entail_prompt = f"Forrás tények:\n{facts_str}\n\nÁllítás:\n{claim}"
        try:
            entail_result = enforce_pydantic_schema(
                model_name=val_model,
                schema_model=EntailmentCheck,
                system_prompt="Döntsd el szigorúan, hogy a Forrás tények közvetlenül alátámasztják-e az Állítást. Ne feltételezz semmit, ami nincs leírva.",
                user_prompt=entail_prompt,
                max_retries=1
            )
            if not entail_result.is_entailed:
                unsupported_claims.append(claim)
        except Exception:
            unsupported_claims.append(claim) # Biztonság kedvéért bukik
            
    if not unsupported_claims:
        return True, draft_text, "V1 Grounding: Minden állítás alátámasztva."
        
    # Extrinsic hallucination eltávolítása (Fizikai törlés)
    cleaned_text = draft_text
    for uc in unsupported_claims:
        # Egyszerű substring törlés, vagy LLM-es átírás is lehetne.
        # Itt most egy figyelemfelkeltő markerre cseréljük, hogy a következő körben az LLM lássa.
        cleaned_text = cleaned_text.replace(uc, "[UNSUPPORTED]")
        
    # Ha túl sok az unsupported, elutasítjuk az egészet
    if len(unsupported_claims) > len(atomic_result.claims) * 0.5:
        return False, cleaned_text, f"V1 Vétó: Túl sok hallucinált állítás ({len(unsupported_claims)} db)."
        
    return False, cleaned_text, f"V1 Vétó: Az alábbi állítások nem alátámasztottak a források által: {unsupported_claims}"

def evaluate_drafts_1model(drafts: List[Dict[str, str]], node: ArgumentNode, context: TaskContext, state_manager: StateManager, session_id: str) -> List[DelphiDraftResult]:
    """1 erős LLM model (V2, V3) + Determinisztikus V1 Grounding futtatása."""
    import yaml
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    val_model = config.get("engine_parameters", {}).get("validator_model", "gemini-1.5-pro")
    facts = get_facts_for_node(state_manager.db_path, node.id)
    
    results = []
    for draft in drafts:
        # V1: Grounding Verifier
        is_grounded, cleaned_text, v1_feedback = verify_grounding_v1(draft["text"], facts, config, state_manager)
        
        evaluations = [
            SectionEvaluation(
                agent_name="V1 Grounding",
                thought_process="Atomic Split -> Overlap -> Entailment",
                scores={"overall": 100 if is_grounded else 0},
                veto_raised=not is_grounded,
                conclusion=v1_feedback
            )
        ]
        
        if not is_grounded:
            # Ha megbukik V1-en, nem is futtatjuk a drága V2/V3 modellt
            results.append(DelphiDraftResult(
                draft_id=state_manager.generate_hash(draft["text"]),
                draft_text=cleaned_text,
                branch_type=draft["branch"],
                evaluations=evaluations,
                average_score=0.0,
                is_vetoed=True
            ))
            continue
            
        # V2, V3: 1 Erős Modell hívása
        system_prompt = """Te a UPVS-Engine Validátora vagy.
V2 (Logika): Vizsgáld meg, hogy az érvelés logikus-e, nincsenek-e ellentmondások. Ez BLOKKOLÓ.
V3 (Minőség): Pontozd a stílust, objektivitást és olvashatóságot (0-100). Ez TANÁCSADÓ.
"""
        user_prompt = f"Claim: {node.claim}\nSzöveg:\n{draft['text']}"
        
        try:
            val_result = enforce_pydantic_schema(
                model_name=val_model,
                schema_model=ValidatorOutput,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_retries=2
            )
            
            evaluations.append(SectionEvaluation(
                agent_name="V2 Logic",
                thought_process=val_result.v2_logic_feedback,
                scores={"overall": 100 if val_result.v2_logic_passed else 0},
                veto_raised=not val_result.v2_logic_passed,
                conclusion="Logic Pass" if val_result.v2_logic_passed else "Logic Fail"
            ))
            
            evaluations.append(SectionEvaluation(
                agent_name="V3 Quality",
                thought_process=val_result.v3_quality_feedback,
                scores={"overall": val_result.v3_quality_score},
                veto_raised=False,
                conclusion="Quality Graded"
            ))
            
            avg_score = val_result.v3_quality_score
            is_vetoed = not val_result.v2_logic_passed
            
        except Exception as e:
            evaluations.append(SectionEvaluation(
                agent_name="Validator Error",
                thought_process="Hiba",
                scores={"overall": 0},
                veto_raised=True,
                conclusion=str(e)
            ))
            avg_score = 0.0
            is_vetoed = True
            
        results.append(DelphiDraftResult(
            draft_id=state_manager.generate_hash(draft["text"]),
            draft_text=cleaned_text,
            branch_type=draft["branch"],
            evaluations=evaluations,
            average_score=avg_score,
            is_vetoed=is_vetoed
        ))
        
    return results

def validation_session(node: ArgumentNode, drafts: List[Dict[str, str]], context: TaskContext, state_manager: StateManager, session_id: str) -> Tuple[Optional[DelphiDraftResult], Optional[DelphiDraftResult], str]:
    """A régi council_session helyett."""
    results = evaluate_drafts_1model(drafts, node, context, state_manager, session_id)
    
    valid_drafts = [r for r in results if not r.is_vetoed]
    vetoed_drafts = [r for r in results if r.is_vetoed]
    
    best_valid = max(valid_drafts, key=lambda x: x.average_score) if valid_drafts else None
    best_vetoed = max(vetoed_drafts, key=lambda x: x.average_score) if vetoed_drafts else None
    
    combined_feedback = ""
    if not best_valid and best_vetoed:
        feedbacks = []
        for ev in best_vetoed.evaluations:
            if ev.veto_raised:
                feedbacks.append(f"- {ev.agent_name} (BLOKKOLÓ): {ev.conclusion} | {ev.thought_process}")
        combined_feedback = "\n".join(feedbacks)
        
    return best_valid, best_vetoed, combined_feedback
