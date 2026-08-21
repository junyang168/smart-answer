from __future__ import annotations

from google import genai
from google.genai import types

from .config import GENERATION_MODEL, GEMINI_API_KEY


class GeminiClient:
    def __init__(self) -> None:
        # Standard Client (Gemini 1.5/2.0 Pro/Flash etc via AI Studio)
        if GEMINI_API_KEY:
            self._client = genai.Client(api_key=GEMINI_API_KEY, vertexai=False)
        else:
            self._client = genai.Client(vertexai=False)

    def _get_client_for_model(self, model: str):
        """
        Selects the appropriate client based on the model.
        Every supported model is reachable on AI Studio, so there is one client.
        """
        return self._client

    def generate_raw(self, contents, config=None, model=GENERATION_MODEL):
        """
        Direct wrapper around client.models.generate_content for advanced usage.
        """
        client = self._get_client_for_model(model)
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )

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
        # Use generate_raw to leverage routing logic
        response = self.generate_raw(
            model=model,
            contents=contents,
            config=generate_content_config
        )
        return response.text


gemini_client = GeminiClient()
