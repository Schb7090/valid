import json
from pydantic import BaseModel, ValidationError
from typing import Type, TypeVar, Callable, Any

T = TypeVar('T', bound=BaseModel)

def enforce_pydantic_schema(
    llm_call_func: Callable[[str], str], 
    schema_model: Type[T], 
    initial_prompt: str, 
    max_retries: int = 2
) -> T:
    """
    LLM hívást burkoló retry mechanizmus, ami elkapja a Pydantic hálózatot,
    és token-hatékony JSON hibaüzenetekkel bombázza vissza az LLM-et, ha hibázik.
    Hard cap (max_retries) véd a végtelen hurkok ellen.
    """
    current_prompt = initial_prompt
    
    for attempt in range(max_retries + 1):
        raw_response = llm_call_func(current_prompt)
        
        try:
            # Próbáljuk meg parszolni a választ
            # A raw_response-t érdemes megtisztítani markdown kódblokkoktól (```json)
            clean_response = raw_response.strip()
            if clean_response.startswith("```json"):
                clean_response = clean_response[7:]
            elif clean_response.startswith("```"):
                clean_response = clean_response[3:]
            if clean_response.endswith("```"):
                clean_response = clean_response[:-3]
            clean_response = clean_response.strip()
            
            parsed = schema_model.model_validate_json(clean_response)
            return parsed
        except ValidationError as e:
            if attempt == max_retries:
                raise Exception(f"Sikertelen generálás {max_retries} próbálkozás után. Utolsó strukturális hiba: {e.errors()}")
            
            # Strukturált, token-takarékos hibakódok kinyerése a nyers traceback helyett
            structured_errors = []
            for err in e.errors():
                structured_errors.append({
                    "field": ".".join(str(loc) for loc in err["loc"]),
                    "error": err["type"],
                    "msg": err["msg"]
                })
            
            error_json_str = json.dumps(structured_errors, ensure_ascii=False)
            
            # Hozzáfűzzük a korrigált inputot a promphoz
            current_prompt += f"\n\n[SYSTEM FEEDBACK]: A legutóbbi JSON válaszod megbukott a séma-validáción. Kérlek javítsd ki a következő strukturális hibákat:\n{error_json_str}\nNe generálj magyarázatot, csak a javított, tiszta JSON-t küldd vissza!"
        except Exception as e:
            if attempt == max_retries:
                raise Exception(f"Kritikus hiba parszolás közben: {e}")
            current_prompt += f"\n\n[SYSTEM FEEDBACK]: A kimeneted nem volt érvényes JSON formátumú. Kérlek ellenőrizd a szintaktikát!"
             
    raise Exception("Váratlan futási hiba a retry ciklusban.")
