import json
from typing import List, Dict, Any
from backend.api.openai_client import generate_structured_json

MODEL_STORYBOARD = "gpt-5.4"

# --- SYSTEM PROMPT: EXEGESIS (V6) ---
PROMPT_EXEGESIS = """
You are generating a storyboard JSON for a Chinese biblical exegesis video (3–5 minutes).
This is a teaching-first video for general audience and seekers.
Goal: The viewer must understand the core biblical logic clearly within 5 minutes.

### Core Instruction (CRITICAL)
Use the provided `core_flow` as the logical backbone.
- Follow order strictly. Do NOT reorder. Do NOT skip steps.
- You may split or group based on meaning. Priority: clarity > compression.

### Scene Granularity & Density (CRITICAL)
- Scenes follow MEANING, not sentences. Create a new scene when a new idea begins, a logical shift occurs, or emphasis changes.
- Typical range: 8–16 scenes.
- **Scene Density Rule**: Each scene must contain ONLY ONE primary idea. 

### Theological Segmentation Rule (CRITICAL)
Do NOT merge different theological functions:
- explanation of rest, religious burden, illustration (home), future rest (Hebrews), yoke (teaser).
These MUST be separated.

### Ending Structure Rule (CRITICAL)
The ending MUST follow this sequence: 1. present rest, 2. future rest (brief), 3. yoke (teaser).

### Yoke Rule (VERY IMPORTANT)
- Yoke MUST appear ONLY in the final scene.
- MUST NOT be explained; MUST be a teaser question.

### Voiceover Rules (CRITICAL)
- `voiceover_text` MUST match the provided `script`. 
- DO NOT rewrite or paraphrase. Only split or minimally trim.
- If the script is missing, the output is INVALID.
"""

# --- SYSTEM PROMPT: SHORT SERMON (FALLBACK) ---
PROMPT_SHORT_SERMON = """
You are generating a storyboard JSON for a Chinese Short Sermon video.
Maintain a balance between biblical truth and practical application.
Follow the core_flow and script provided.
Output Format: scenes array with scene_id and voiceover_text.
"""

STORYBOARD_RESPONSE_SCHEMA = {
    "name": "storyboard_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "metadata": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string"},
                    "title": {"type": "string"}
                },
                "required": ["mode", "title"],
                "additionalProperties": False
            },
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scene_id": {"type": "number"},
                        "voiceover_text": {"type": "string"}
                    },
                    "required": ["scene_id", "voiceover_text"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["metadata", "scenes"],
        "additionalProperties": False
    }
}

def generate_storyboard(input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Calls OpenAI to generate the structured JSON storyboard based on the input JSON.
    Selects the system prompt based on 'video_type' or 'mode'.
    """
    mode = input_data.get("mode") or input_data.get("video_type", "Exegesis")
    if "exegesis" in str(mode).lower():
        system_prompt = PROMPT_EXEGESIS
    else:
        system_prompt = PROMPT_SHORT_SERMON

    # Handle core_flow which can be a list of strings or a list of objects
    raw_flow = input_data.get("core_flow", [])
    if raw_flow and isinstance(raw_flow[0], dict):
        core_flow_text = "\\n".join([f"- {i.get('label', 'step')}: {i.get('content', '')}" for i in raw_flow])
    else:
        core_flow_text = "\\n".join(raw_flow)

    script = input_data.get("script", "")
    
    # Handle core_guidance vs older fields
    core_guidance = input_data.get("core_guidance", {})
    if not core_guidance:
        core_guidance = {
            "director_notes": input_data.get("director_notes", {}),
            "visual_philosophy": input_data.get("visual_philosophy", [])
        }
    
    scripture = input_data.get("scripture", {})
    audience = input_data.get("audience", {})
    
    user_prompt = f"""
Core Flow:
{core_flow_text}

Scripture:
{json.dumps(scripture, ensure_ascii=False, indent=2)}

Audience:
{json.dumps(audience, ensure_ascii=False, indent=2)}

Core Guidance:
{json.dumps(core_guidance, ensure_ascii=False, indent=2)}

Script:
{script}
"""
    
    try:
        result = generate_structured_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_schema=STORYBOARD_RESPONSE_SCHEMA,
            model=MODEL_STORYBOARD,
            temperature=0.0
        )
        # Ensure metadata is correctly populated with the actual mode
        if "metadata" not in result:
            result["metadata"] = {}
        result["metadata"]["mode"] = str(mode)
        # Preserve title if LLM generated a better one, else use input
        if "title" not in result["metadata"]:
            result["metadata"]["title"] = input_data.get("title", "Untitled")
        return result
        
    except Exception as e:
        print(f"Error generating storyboard with OpenAI: {e}")
        raise
