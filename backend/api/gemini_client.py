from __future__ import annotations

from typing import Optional

from google import genai
from google.genai import types

from .config import GENERATION_MODEL, GEMINI_API_KEY


class GeminiClient:
    def __init__(self) -> None:
        self._client = genai.Client()

    def generate(self, prompt: str, model=GENERATION_MODEL, use_search_tool=False) -> str:
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        ]
        tools = [
            types.Tool(googleSearch=types.GoogleSearch(
            )),
        ] if use_search_tool else []
       
        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=-1)
        )
        response = self._client.models.generate_content(
            model=model,
            contents=contents,
            config=generate_content_config
        )
        return response.text


gemini_client = GeminiClient()
