"""
UPVS-Engine — Intent Router (1. Réteg)
Feladata: A felhasználói prompt elemzése és a rendszer konfigurálása.
"""
import json
import yaml
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, ValidationError

from models import TaskContext, CouncilThresholds, TaskConstraints, TASK_TYPES, RESEARCH_DEPTH, GROUNDING_LEVEL, ASSEMBLY_FREEDOM, DAG_COMPLEXITY, TARGET_AUDIENCE
from state_manager import StateManager

# --- A v2 rendszerprompt beágyazva ---

ROUTER_SYSTEM_PROMPT = """
# SZEREPKÖR

Te vagy a UPVS-Engine (Ultra-Precision Verification and Synthesis Engine) Intent Routere: az egyetlen belépési pont, amely a felhasználó nyers kérését strukturált konfigurációvá alakítja a mögötted álló 6 rétegű rendszer (Planner, Researcher, Generator, Council, Auditor, Assembler) számára.

Teljesen autonóm vagy. Soha nem kérdezel vissza. Ha bizonytalan vagy, mindig a SZIGORÚBB konfiguráció felé dönts.

---

# ALAPELVEK

1. ALAPÉRTELMEZÉS = MAXIMUM SZIGOR. A rendszer kiindulópontja mindig a legszigorúbb, hallucinációmentes, tudományos szint. Lazítani CSAK a felhasználói kérés explicit jellege alapján szabad (pl. vers, ötletbörze, kód).
2. AUTONÓMIA. Futás közben tilos megállni vagy visszakérdezni. Ha bizonytalan vagy egy paraméterben, válaszd a szigorúbb opciót.
3. KÉNYSZERÍTETT LEHORGONYZÁS (GROUNDING). Ha a grounding_level értéke "strict" vagy "moderate", a Generator kizárólag a Fact Store-ból származó, [fact_id]-vel jelölt tényekből dolgozhat.

---

# ZÁRT TAXONÓMIA — FELADATTÍPUSOK

Az alábbi listából KELL választanod. Más érték nem megengedett.

| task_type             | Leírás                                                        |
|-----------------------|---------------------------------------------------------------|
| academic_essay        | Tudományos/akadémiai esszé, tanulmány, szakdolgozat           |
| analytical_report     | Tényalapú elemzés, összehasonlítás, döntéstámogatás           |
| business_document     | Üzleti levél, memo, prezentáció, projektterv                  |
| journalistic_article  | Újságcikk, riport, véleménycikk, blogposzt                    |
| creative_writing      | Vers, novella, kreatív szöveg, szlogen                         |
| code_generation       | Kód, script, technikai dokumentáció                            |
| translation_rewrite   | Fordítás, átfogalmazás, stílusváltás                           |
| brainstorm_ideation   | Ötletbörze, lista, kreatív gondolkodás                         |
| summarization         | Összefoglalás, kivonat, tömörítés                              |

---

# FELADATOD

A felhasználói kérés alapján töltsd ki az alábbi JSON-t.

## 1. task_type — A fenti táblázatból. Ha ambivalens, válaszd a SZIGORÚBBAT.
## 2. research_depth — deep (5/node) | standard (2/node) | shallow (1/node) | none (0)
## 3. council_thresholds — Ágens-szintű vétóküszöbök:
   - domain_expert_veto (alapértelmezett: 60, kreatív: 30)
   - debiaser_veto (alapértelmezett: 40, kreatív: 20)
   - grounding_verifier_veto (alapértelmezett: 90, kreatív: 0)
   - max_variance (alapértelmezett: 15, kreatív: 25)
## 4. grounding_level — strict (90%) | moderate (60%) | none (0%)
## 5. assembly_freedom — strict | moderate | flexible
## 6. dag_complexity — micro (3-5) | small (6-12) | medium (13-25) | large (26-50)
## 7. target_audience — academic | professional | general | casual
## 8. constraints — Strukturált kinyerés:
   - language, length_pages, length_words, format, deadline_priority, citation_style

---

# DÖNTÉSI SZABÁLY (BIZTONSÁGI HÁLÓ)

Ha a kérés AMBIVALENS:
1. task_type → "analytical_report"
2. research_depth → "deep"
3. grounding_level → "strict"
4. council_thresholds → alapértelmezett (szigorú)
5. dag_complexity → "medium"
6. target_audience → "professional"

---

# BEMENET

Felhasználói kérés: \"\"\"{user_prompt}\"\"\"

# KIMENETI FORMÁTUM (KIZÁRÓLAG ÉRVÉNYES JSON, SEMMI MÁS)

{{
  "task_type": "...",
  "research_depth": "deep | standard | shallow | none",
  "council_thresholds": {{
    "domain_expert_veto": int,
    "debiaser_veto": int,
    "grounding_verifier_veto": int,
    "max_variance": int
  }},
  "grounding_level": "strict | moderate | none",
  "assembly_freedom": "strict | moderate | flexible",
  "dag_complexity": "micro | small | medium | large",
  "target_audience": "academic | professional | general | casual",
  "constraints": {{
    "language": "...",
    "length_pages": null,
    "length_words": null,
    "format": null,
    "deadline_priority": "quality_first",
    "citation_style": "APA"
  }},
  "reasoning": "1-2 mondatos indoklás."
}}
"""


def load_config() -> dict:
    """Betölti a config.yaml-t."""
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_router_prompt(user_prompt: str) -> str:
    """A rendszerpromptba behelyettesíti a felhasználói kérést."""
    return ROUTER_SYSTEM_PROMPT.replace("{user_prompt}", user_prompt)


class IntentRouterOutput(BaseModel):
    """Pydantic séma az LLM kimenetének validálására."""
    task_type: TASK_TYPES
    research_depth: RESEARCH_DEPTH
    council_thresholds: CouncilThresholds
    grounding_level: GROUNDING_LEVEL
    assembly_freedom: ASSEMBLY_FREEDOM
    dag_complexity: DAG_COMPLEXITY
    target_audience: TARGET_AUDIENCE
    constraints: TaskConstraints
    reasoning: str = ""

def parse_router_response(raw_json: str, user_prompt: str) -> TaskContext:
    """
    Az LLM nyers JSON válaszát Pydantic modellel validálja.
    Ha hibás, a ValidationError kivételt dobja feljebb a Retry Loop-nak.
    """
    parsed = IntentRouterOutput.model_validate_json(raw_json)
    return TaskContext(user_prompt=user_prompt, **parsed.model_dump())


def route(user_prompt: str, state_manager: StateManager, session_id: str) -> TaskContext:
    """
    A fő belépési pont: felhasználói prompt → TaskContext.
    
    Lépései:
    1. Ellenőrzi az LLM cache-t (ha már volt ilyen kérés, azonnal visszaadja).
    2. Ha nincs cache → felépíti a promptot, meghívja az LLM-et.
    3. Parsolja a JSON választ → TaskContext.
    4. Naplózza az audit logba.
    
    FONTOS: Az LLM hívás itt egy placeholder — a tényleges API integráció
    az engine.py-ban történik majd (Gemini API). Ez a modul csak a prompt
    felépítését és a válasz feldolgozását végzi.
    """
    prompt = build_router_prompt(user_prompt)
    prompt_hash = state_manager.generate_hash(prompt, "router")

    # Cache ellenőrzés
    cached = state_manager.get_llm_cache(prompt_hash)
    if cached:
        try:
            task_context = parse_router_response(cached, user_prompt)
            state_manager.log_action(session_id, "intent_router", "cache_hit", {"task_type": task_context.task_type})
            return task_context
        except Exception:
            pass # Invalid cache, fall through

    # Pydantic JSON Retry Loop
    max_retries = 3
    current_prompt = prompt
    
    for attempt in range(max_retries):
        try:
            # LLM hívás placeholder
            # llm_response = call_gemini(current_prompt)
            raise NotImplementedError("Az LLM hívást az engine.py végzi.")
            
            # task_context = parse_router_response(llm_response, user_prompt)
            # return task_context
            
        except ValidationError as e:
            current_prompt += f"\n\nJavítsd ezt a JSON hibát (Próbálkozás {attempt+1}/{max_retries}):\n{e.json()}"
        except json.JSONDecodeError as e:
            current_prompt += f"\n\nJavítsd ezt a JSON hibát (Invalid JSON):\n{str(e)}"
            
    # Biztonsági háló: Ha 3 próbálkozás után is hibás
    config = load_config()
    defaults = config["engine_parameters"]["ambiguous_default"]
    state_manager.log_action(session_id, "intent_router", "fallback_used", {"reason": "Pydantic validation failed 3 times."})
    
    return TaskContext(
        user_prompt=user_prompt,
        task_type=defaults["task_type"],
        research_depth=defaults["research_depth"],
        grounding_level=defaults["grounding_level"],
        assembly_freedom=defaults["assembly_freedom"],
        dag_complexity=defaults["dag_complexity"],
        target_audience=defaults["target_audience"],
    )
