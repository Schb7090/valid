import os
import json
import google.generativeai as genai
from pydantic import BaseModel, ValidationError
from typing import Type, TypeVar, Optional, Any

# A Gemini API kulcsot a környezeti változókból olvassuk be.
api_key = os.environ.get("GEMINI_API_KEY", "MOCK_API_KEY")
genai.configure(api_key=api_key)

T = TypeVar('T', bound=BaseModel)

def call_text(model_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    """Nyers szöveggenerálás Gemini API-val."""
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(
            temperature=temperature
        )
    )
    response = model.generate_content(user_prompt)
    if response.parts:
        return response.text.strip()
    return ""

def enforce_pydantic_schema(
    model_name: str,
    schema_model: Type[T],
    system_prompt: str,
    user_prompt: str,
    max_retries: int = 3,
    base_temperature: float = 0.5
) -> T:
    """
    LLM hívást burkoló retry mechanizmus a Gemini native JSON képességeivel.
    Ha a Pydantic validáció elbukik, visszacsatoljuk a hibát az LLM-nek.
    """
    current_prompt = user_prompt
    
    for attempt in range(max_retries + 1):
        current_temp = min(base_temperature + (attempt * 0.3), 1.0)
        
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=current_temp
            )
        )
        
        try:
            response = model.generate_content(current_prompt)
            if not response.parts:
                raise ValueError("Üres válasz érkezett az API-tól.")
                
            raw_response = response.text.strip()
            
            # Tisztítás biztonság kedvéért
            if raw_response.startswith("```json"):
                raw_response = raw_response[7:]
            elif raw_response.startswith("```"):
                raw_response = raw_response[3:]
            if raw_response.endswith("```"):
                raw_response = raw_response[:-3]
            raw_response = raw_response.strip()
            
            parsed = schema_model.model_validate_json(raw_response)
            return parsed
            
        except ValidationError as e:
            if attempt == max_retries:
                raise Exception(f"Sikertelen generálás {max_retries} próbálkozás után. Utolsó hiba: {e.errors()}")
            
            structured_errors = []
            for err in e.errors():
                structured_errors.append({
                    "field": ".".join(str(loc) for loc in err["loc"]),
                    "error": err["type"],
                    "expected": err.get("msg", "")
                })
            
            error_json_str = json.dumps(structured_errors, ensure_ascii=False)
            current_prompt += f"\n\n[SYSTEM FEEDBACK]: A JSON válaszod megbukott a séma-validáción. Hibák:\n{error_json_str}\nKérlek javítsd ki!"
            
        except Exception as e:
            if attempt == max_retries:
                raise Exception(f"Kritikus API hiba: {e}")
            current_prompt += f"\n\n[SYSTEM FEEDBACK]: Váratlan hiba történt a generálás során: {str(e)}"
            
    raise Exception("Váratlan futási hiba a retry ciklusban.")
