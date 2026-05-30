"""
UPVS-Engine — Planner (2. Réteg)
Feladata: A dokumentum logikai vázának (DAG) megtervezése a TaskContext alapján.
"""
import json
import yaml
from pathlib import Path

from pydantic import ValidationError

from models import TaskContext, ArgumentGraph
from state_manager import StateManager

PLANNER_SYSTEM_PROMPT = """
# SZEREPKÖR
Te vagy a UPVS-Engine (Ultra-Precision Verification and Synthesis Engine) 2. rétege: a DAG Planner (Váztervező).
A feladatod, hogy a felhasználói kérés és a megadott kontextus alapján felépítsd a dokumentum logikai vázát egy Irányított Ametrikus Gráf (DAG) formájában.

# FELADATOD
1. Oszd fel a témát logikai egységekre (csomópontok/node-ok).
2. Határozd meg a csomópontok közötti logikai kapcsolatokat (élek/edges). Minden élnek egy létező `source` és `target` node azonosítóra kell mutatnia.
3. A kontextusban megadott komplexitási szintnek megfelelően tervezz.
4. Minden csomóponthoz generálj specifikus kutatási keresőszavakat (`research_queries`), amiket a Forráskutató API-k (pl. OpenAlex, PubMed) használni fognak. Olyan kifejezéseket adj meg, amik jó eséllyel adnak releváns szakmai/akadémiai találatot.

# BEMENETI KONTEXTUS
- Felhasználói kérés: "{user_prompt}"
- Feladat típusa: {task_type}
- Elvárt komplexitás: {complexity} (Elvárt csomópontok száma: {min_nodes} - {max_nodes})
- Célközönség / Regiszter: {audience}

# KIMENETI FORMÁTUM (KIZÁRÓLAG ÉRVÉNYES JSON)
{{
  "title": "A dokumentum/gráf átfogó címe",
  "nodes": [
    {{
      "id": "node_1",
      "section_title": "A szekció rövid címe",
      "claim": "A szekció fő állítása (1 mondat)",
      "premises": ["premissza 1", "premissza 2"],
      "node_type": "axiom",
      "research_queries": ["keresőszó 1", "keresőszó 2"]
    }}
  ],
  "edges": [
    {{
      "source": "node_1",
      "target": "node_2",
      "relation": "supports"
    }}
  ]
}}

Érvényes `node_type` értékek: "axiom", "derivation", "synthesis", "conclusion".
Érvényes `relation` értékek: "supports", "contrasts", "extends", "synthesizes".
"""

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def build_planner_prompt(context: TaskContext, feedback: str = "") -> str:
    """A rendszerpromptba behelyettesíti a kontextust és az esetleges kritikát."""
    config = load_config()
    complexity_range = config["engine_parameters"]["dag_complexity_ranges"].get(context.dag_complexity, [3, 5])
    min_nodes, max_nodes = complexity_range
    
    prompt = PLANNER_SYSTEM_PROMPT.format(
        user_prompt=context.user_prompt,
        task_type=context.task_type,
        complexity=context.dag_complexity,
        min_nodes=min_nodes,
        max_nodes=max_nodes,
        audience=context.target_audience
    )
    
    if feedback:
        prompt += f"\n\n# ELŐZŐ PRÓBÁLKOZÁS VISSZAUTASÍTVA\nA DAG Reviewer az alábbi kritikát adta az előző tervedre. Javítsd a hibákat és tervezz egy új DAG-ot!\n\nKritika a Reviewertől:\n{feedback}"
        
    return prompt

def parse_planner_response(raw_json: str) -> ArgumentGraph:
    """A JSON-t Pydantic ArgumentGraph objektummá alakítja."""
    return ArgumentGraph.model_validate_json(raw_json)

def plan_dag(context: TaskContext, state_manager: StateManager, session_id: str, feedback: str = "") -> ArgumentGraph:
    """
    A Planner fő metódusa. Felépíti a DAG-ot, és szükség esetén beépíti a Reviewer kritikáját.
    """
    prompt = build_planner_prompt(context, feedback)
    prompt_hash = state_manager.generate_hash(prompt, "planner")

    # Cache ellenőrzés
    cached = state_manager.get_llm_cache(prompt_hash)
    if cached:
        try:
            graph = parse_planner_response(cached)
            state_manager.log_action(session_id, "planner", "cache_hit", {
                "nodes_count": len(graph.nodes),
                "edges_count": len(graph.edges)
            })
            return graph
        except Exception:
            pass # Ha rossz volt a cache, hagyjuk, hogy újragenerálja
            
    # Pydantic JSON Retry Loop
    max_retries = 3
    current_prompt = prompt
    
    for attempt in range(max_retries):
        try:
            # LLM hívás placeholder az engine.py-nak.
            # llm_response = call_gemini(current_prompt)
            raise NotImplementedError("Az LLM hívást az engine.py végzi.")
            
            # graph = parse_planner_response(llm_response)
            # return graph
            
        except ValidationError as e:
            current_prompt += f"\n\nJavítsd ezt a JSON hibát (Próbálkozás {attempt+1}/{max_retries}):\n{e.json()}"
        except json.JSONDecodeError as e:
            current_prompt += f"\n\nJavítsd ezt a JSON hibát (Invalid JSON):\n{str(e)}"
            
    # Ha kifut a retries-ből:
    raise ValueError("A Planner 3 próbálkozás után sem tudott érvényes DAG JSON-t generálni.")
