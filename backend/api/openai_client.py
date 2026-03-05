import os
import json
from typing import Dict, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables once when the module is imported
load_dotenv()

_client = None

def get_openai_client() -> OpenAI:
    """Returns a singleton OpenAI client."""
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        _client = OpenAI(api_key=api_key)
    return _client

def generate_structured_json(
    system_prompt: str,
    user_prompt: str,
    json_schema: Dict[str, Any],
    model: str = "gpt-5.2",
    temperature: float = 0.0,
    max_tokens: Optional[int] = None
) -> Dict[str, Any]:
    """
    Calls the OpenAI API aiming for a structured output matching the provided json_schema.
    Returns the parsed JSON dictionary.
    """
    client = get_openai_client()
    
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": json_schema,
        },
        "temperature": temperature,
    }
    
    if max_tokens is not None:
        kwargs["max_completion_tokens"] = max_tokens  # OpenAI updated mapping for o1/newer models

    response = client.chat.completions.create(**kwargs)
    
    result_text = response.choices[0].message.content or ""
    return json.loads(result_text)
