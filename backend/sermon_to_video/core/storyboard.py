import json
import json
from pathlib import Path
from typing import List, Dict, Any

from google.genai import types

from backend.api.gemini_client import gemini_client

GEMINI_MODEL_STORYBOARD = "gemini-3.0-pro-preview" # Or any currently available "gemini-3-pro-preview" depending on exact naming on Vertex AI
# Actually wait, sermon_converter_service used "gemini-3-pro-preview"
GEMINI_MODEL_STORYBOARD = "gemini-3-pro-preview"

STORYBOARD_SYSTEM_PROMPT = """
You are an expert video producer and storyteller for a Sermon-to-Video tool.
Your task is to convert the provided sermon/bible-study transcript into a highly engaging, structured storyboard.

Please output a rigorous JSON array of scene objects. Each scene represents a specific visual/audio moment in the video.

Data Contract for each object:
{
  "scene_id": number,
  "voiceover_text": "string (the exact spoken narration in Traditional Chinese)",
  "visual_prompt": "string (English visual prompt for an AI video/image generator, describe lighting, subject, mood, cinematic style)",
  "overlay_text": "string (Key scripture or quote in Traditional Chinese to overlay on screen, keep it short. Use empty string '' if none)",
  "overlay_start_ratio": float (0.0 to 1.0, representing when the text appears relative to the audio duration. 0.0 means immediately, 0.5 means halfway through)
}

Rules:
1. Ensure the `voiceover_text` covers the core message of the transcript. It should flow naturally like a professional narration.
2. Ensure strict JSON format, no markdown tags around the response.
3. The visual prompts should be powerful, cinematic, and metaphorical where appropriate (B-Roll).
"""

def generate_storyboard(transcript_text: str) -> List[Dict[str, Any]]:
    """
    Calls Gemini 3 via gemini_client to generate the structured JSON storyboard.
    """
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=transcript_text)],
        )
    ]
    
    config = types.GenerateContentConfig(
        system_instruction=types.Content(
            role="system",
            parts=[types.Part.from_text(text=STORYBOARD_SYSTEM_PROMPT)]
        ),
        response_mime_type="application/json"
    )

    try:
        response = gemini_client.generate_raw(
            model=GEMINI_MODEL_STORYBOARD,
            contents=contents,
            config=config
        )
        
        raw_text = response.text
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        return json.loads(raw_text.strip())
        
    except Exception as e:
        print(f"Error generating storyboard: {e}")
        raise
