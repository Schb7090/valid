"""
UPVS-Engine — Engine Orchestrator (A fő állapotgép)
Feladata: Összeköti és szinkronizálja a 6 réteget, biztosítja az autonóm,
megszakítás nélküli folyamatot (HITL mentesen), és kezeli az LLM hívások absztrakcióját.
"""
import uuid
import yaml
from pathlib import Path
from typing import Optional

from models import UPVSEngineState, TokenUsage
from state_manager import StateManager
from intent_router import route
from planner import plan_dag
from dag_reviewer import review_dag
from researcher import execute_research
from generator import generate_drafts_for_node, get_facts_for_node
from council import council_session
from logic_auditor import execute_logic_audit
from assembler import assemble_document

# ==========================================
# LLM MOCK / ABSTRACTION LAYER
# ==========================================
def call_llm(prompt: str, temperature: float = 0.5) -> str:
    """
    Ezt a metódust kell később lecserélni a tényleges (pl. Gemini API) hívásra.
    Mivel a jelen fázis csak az architektúra építése, ez egy Mock.
    """
    raise NotImplementedError("API hívás nem megvalósított.")

class TokenLimitExceededError(Exception):
    pass

# ==========================================
# FŐ MOTOR
# ==========================================

class UPVSEngine:
    def __init__(self, db_path: str = "upvs_cache.db"):
        self.state_manager = StateManager(db_path=db_path)
        self.config_path = Path(__file__).parent / "config.yaml"

    def _reload_config(self, state: UPVSEngineState):
        """On-The-Fly Update: Beolvassa a config.yaml-t és frissíti a state paramétereit."""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                
            if state.task_context:
                # Frissítjük a vétó küszöböket röptében
                if "domain_expert_veto" in config["prompts"]["council_thresholds"]:
                    state.task_context.council_thresholds.domain_expert_veto = config["prompts"]["council_thresholds"]["domain_expert_veto"]
                if "debiaser_veto" in config["prompts"]["council_thresholds"]:
                    state.task_context.council_thresholds.debiaser_veto = config["prompts"]["council_thresholds"]["debiaser_veto"]
                    
        except Exception as e:
            # Ha valaki épp menti a YAML-t és hibás szintaxisú, hagyjuk figyelmen kívül
            pass

    def check_token_limit(self, state: UPVSEngineState):
        """Költségsapka (Kill-Switch): Ha a token limitet átléptük, megszakítjuk a folyamatot."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            
        max_tokens = config.get("engine_parameters", {}).get("limits", {}).get("max_session_tokens", 50000)
        
        if state.token_usage.total_tokens > max_tokens:
            raise TokenLimitExceededError(f"A Token Limit ({max_tokens}) kimerült! Jelenlegi: {state.token_usage.total_tokens}")

    def run(self, user_prompt: str, session_id: Optional[str] = None) -> str:
        """A rendszer belépési pontja. Teljesen autonóm módon (HITL nélkül) fut le."""
        session_id = session_id or uuid.uuid4().hex
        
        # 0. Állapot betöltése vagy inicializálása
        state = self.state_manager.load_checkpoint(session_id)
        if not state:
            state = UPVSEngineState(session_id=session_id, status="init")
            self.state_manager.save_checkpoint(state)

        try:
            # 1. FÁZIS: INTENT ROUTER
            if state.status == "init":
                state.status = "routing"
                state.task_context = route(user_prompt, self.state_manager, session_id)
                self.state_manager.save_checkpoint(state)
                
                # Early Reject (Költségvédelem)
                if state.task_context.is_rejectable:
                    state.status = "rejected"
                    self.state_manager.log_action(session_id, "engine", "early_reject", {"reason": state.task_context.reject_reason})
                    self.state_manager.save_checkpoint(state)
                    return f"A kérés visszautasítva: {state.task_context.reject_reason}"

            # 2. FÁZIS: PLANNER ÉS DAG REVIEWER
            if state.status == "routing":
                state.status = "planning"
                self._reload_config(state) # Újraolvassuk a config-ot a tervezés előtt
                
                dag_approved = False
                retries = 0
                max_retries = 2
                feedback = ""
                
                while not dag_approved and retries <= max_retries:
                    # 2.1 Tervezés
                    state.argument_graph = plan_dag(state.task_context, self.state_manager, session_id, feedback)
                    
                    # 2.2 Pre-Audit
                    is_approved, critique = review_dag(state.argument_graph, state.task_context, self.state_manager, session_id)
                    if is_approved:
                        dag_approved = True
                    else:
                        feedback = critique
                        retries += 1
                        
                if not dag_approved:
                    # Fallback ha nem sikerül jó gráfot csinálni
                    self.state_manager.log_action(session_id, "engine", "dag_failed", {"reason": feedback})
                    # A kód itt robusztusabb fallbackot használhatna, de a V4 szerint megpróbáljuk abból, ami van.

                self.state_manager.save_checkpoint(state)

            # 3. FÁZIS: RESEARCHER
            if state.status == "planning":
                state.status = "researching"
                execute_research(state.argument_graph, state.task_context, self.state_manager, session_id)
                self.state_manager.save_checkpoint(state)

            # 4. FÁZIS: GENERATOR ÉS COUNCIL
            if state.status == "researching":
                state.status = "generating"
                
                for node in state.argument_graph.nodes:
                    if node.id in state.completed_nodes:
                        continue
                        
                    # Minden csomópont előtt frissítjük a konfigurációt!
                    self._reload_config(state)
                    self.check_token_limit(state)
                    
                    # 4.0 Generálási Stratégia (V12: 3-Tier Routing)
                    facts = get_facts_for_node(self.state_manager.db_path, node.id)
                    
                    if facts:
                        node_confidence = min(f.confidence for f in facts)
                    else:
                        node_confidence = 0.0
                    
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        config_dict = yaml.safe_load(f)
                        
                    system_warning = config_dict.get("fallback_action", {}).get("system_warning_tag", "[SYSTEM WARNING: Válasz nem konzisztens.]")
                    
                    # 3-Tier Routing Logika
                    if node_confidence >= 0.85:
                        path_name = "super"
                        branches = ["prioritized"]
                        max_retries = 0
                        active_personas = [] # Bypass
                    elif node_confidence >= 0.70:
                        path_name = "moderate"
                        branches = ["conservative"]
                        max_retries = 0
                        active_personas = ["Domain Expert"] # Csak 1 persona
                    else:
                        path_name = "weak"
                        branches = ["conservative", "critical", "synthetic"]
                        max_retries = config_dict.get("fallback_action", {}).get("council_max_retries", 3)
                        active_personas = None # Mind a 4 persona
                        
                    self.state_manager.log_action(session_id, "engine", "routing_decision", {"node_id": node.id, "confidence": node_confidence, "path": path_name})
                    
                    retries = 0
                    node_approved = False
                    feedback = ""
                    best_overall_vetoed = None
                    
                    while not node_approved and retries <= max_retries:
                        # 4.1 Generálás (Dinamikus ágakkal)
                        drafts = generate_drafts_for_node(node, state.task_context, self.state_manager, session_id, feedback, branches=branches)
                        
                        # 4.2 Tanács szavazás (Dinamikus personákkal)
                        best_valid, best_vetoed, combined_feedback = council_session(node, drafts, state.task_context, self.state_manager, session_id, active_personas=active_personas)
                        
                        if best_valid:
                            state.final_sections[node.id] = best_valid.draft_text
                            state.completed_nodes.append(node.id)
                            node_approved = True
                        else:
                            retries += 1
                            feedback = combined_feedback
                            
                            # Nyomon követjük az összes iteráció legjobb, de vétózott draftját
                            if not best_overall_vetoed or (best_vetoed and best_vetoed.average_score > best_overall_vetoed.average_score):
                                best_overall_vetoed = best_vetoed

                    if not node_approved:
                        # Fallback Protokoll (Ha kimerült a max_retries)
                        if best_overall_vetoed:
                            fallback_text = f"{system_warning}\n\n{best_overall_vetoed.draft_text}"
                        else:
                            fallback_text = config_dict.get("fallback_action", {}).get("error_tag_placeholder", "[HIBA]")
                            
                        state.final_sections[node.id] = fallback_text
                        state.completed_nodes.append(node.id)
                        self.state_manager.log_action(session_id, "engine", "fallback_protocol_triggered", {
                            "node_id": node.id, 
                            "retries_exhausted": retries - 1
                        })

                self.state_manager.save_checkpoint(state)

            # 5. FÁZIS: LOGICAL AUDITOR
            if state.status == "generating":
                state.status = "auditing_logic"
                audit_results = execute_logic_audit(state.argument_graph, state.final_sections, self.state_manager, session_id)
                # Audit trail mentése
                state.audit_trail.extend([r.model_dump() for r in audit_results])
                self.state_manager.save_checkpoint(state)

            # 6. FÁZIS: OUTPUT ASSEMBLER
            if state.status == "auditing_logic":
                state.status = "assembling"
                # Betöltjük a Pydantic dict-eket vissza objektumokká a híváshoz
                audit_objects = [EdgeAuditResult(**r) for r in state.audit_trail]
                
                final_document = assemble_document(
                    state.argument_graph, 
                    state.final_sections, 
                    audit_objects, 
                    state.task_context, 
                    self.state_manager, 
                    session_id
                )
                
                state.status = "completed"
                self.state_manager.save_checkpoint(state)
                return final_document

        except Exception as e:
            state.status = "error"
            self.state_manager.log_action(session_id, "engine", "critical_error", {"error": str(e)})
            self.state_manager.save_checkpoint(state)
            return f"KRITIKUS RENDSZERHIBA: {e}"

        return "Futás megszakadt váratlan helyen."

# --- Próba futtatás (Ha direktbe indítjuk a scriptet) ---
if __name__ == "__main__":
    engine = UPVSEngine()
    # Mock miatt NotImplementedError-okat fog dobni az LLM hívásoknál
    print("UPVS Engine Inicializálva.")
