from __future__ import annotations

from typing import Optional
import os

from google import genai
from google.genai import types
import json

from .config import GENERATION_MODEL, GEMINI_API_KEY


class GeminiClient:
    def __init__(self) -> None:
        self._client = genai.Client()

    def generate(self, prompt: str, model=GENERATION_MODEL, use_search_tool=False, use_url_context=False) -> str:
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        ]
        tools = []
        if use_search_tool:
            tools.append(types.Tool(googleSearch=types.GoogleSearch(
            ))),
        
        if use_url_context:
            tools.append(types.Tool(urlContext=types.UrlContext()))

       
        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=-1),
            tools=tools,
        )
        response = self._client.models.generate_content(
            model=model,
            contents=contents,
            config=generate_content_config
        )
        return response.text

    def generate_subtitles(self, paragraphs: list[dict]) -> list[dict]:
        """
        Generates subtitles for the given paragraphs.
        Returns a list of insertions, where each insertion has:
        - after_index: The index of the paragraph after which to insert the subtitle.
        - text: The subtitle text (markdown).
        - level: 1 or 2.
        """
        prompt = """
You are a helpful assistant for a sermon editor.
Your task is to analyze the following sermon script and suggest subtitles to structure the content.
The script is provided as a list of paragraphs with their indices.

Please output a JSON list of objects, where each object represents a subtitle insertion:
{
  "after_index": "string", // The index of the paragraph AFTER which this subtitle should be inserted.
  "text": "string", // The subtitle text. Use markdown headers: "## Title" for level 1, "### Subtitle" for level 2.
  "level": number // 1 or 2
}

Rules:
1. Insert subtitles where there is a clear topic change or logical break.
2. Use Level 1 (##) for main sections and Level 2 (###) for subsections.
3. Do not change the original text. Only provide insertions.
4. Ensure the "after_index" corresponds to a valid index from the input.
5. If a subtitle should be at the very beginning, use "START" as the after_index.

Input Script:
"""
        # Simplify input for the model to save tokens and reduce noise
        simplified_script = []
        for p in paragraphs:
            simplified_script.append({
                "index": p.get("index"),
                "text": p.get("text", "")
            })
        
        prompt += json.dumps(simplified_script, ensure_ascii=False, indent=2)

        try:
            response = self._client.models.generate_content(
                model=GENERATION_MODEL,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
            )
            # Extract JSON from response
            text = response.text
            # Simple cleanup if the model wraps code in markdown blocks
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            
            insertions = json.loads(text.strip())
            return insertions
        except Exception as e:
            print(f"Error generating subtitles: {e}")
            return []


gemini_client = GeminiClient()
