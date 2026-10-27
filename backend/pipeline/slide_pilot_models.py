"""Bounded vision adapters for the local slide pilot; no automatic fallback."""
from __future__ import annotations

import base64
import os
from pathlib import Path


class VisionReviewer:
    def __init__(self, provider: str, prompt: str, schema: dict):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        self.provider, self.prompt = provider, prompt
        if provider == "openai":
            from openai import OpenAI
            self.client = OpenAI(timeout=90, max_retries=0)
            self.model = "gpt-6.1-sol"
            self.config = {"reasoning": {"effort": "low"}, "max_output_tokens": 8192,
                           "store": False, "service_tier": "default",
                           "text": {"format": {"type": "json_schema", "name": "slide_pair_review",
                                                "strict": True, "schema": schema}}}
            # Conservatively include published cache-write premium, claim no
            # cached input discount. output_tokens already includes reasoning.
            self.input_rate, self.output_rate = 2.5, 10.0
            self.price_source = "https://developers.openai.com/api/docs/models/gpt-6.1-sol"
        elif provider == "gemini":
            from google import genai
            from google.genai import types
            key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY1"))
            if not key:
                raise ValueError("Gemini key required (same key names as backend/api/config.py)")
            self.client = genai.Client(vertexai=False, api_key=key,
                http_options=types.HttpOptions(timeout=90000, retry_options=types.HttpRetryOptions(attempts=1)))
            self.model = "gemini-3.8-flash"
            self.config = {"temperature": 0, "max_output_tokens": 8192,
                           "response_mime_type": "application/json", "response_json_schema": schema}
            self.input_rate, self.output_rate = .75, 3.75
            self.price_source = "https://ai.google.dev/gemini-api/docs/pricing"
        else:
            raise ValueError("Provider must be openai or gemini")

    def contents(self, ids: list[str], paths: list[Path]):
        import json
        if self.provider == "openai":
            parts = [{"type": "input_text", "text": "Candidate IDs: " + json.dumps(ids)}]
            parts += [{"type": "input_image", "detail": "high", "image_url":
                "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()} for p in paths]
            return [{"role": "user", "content": parts}]
        from google.genai import types
        return [types.Part.from_text(text=self.prompt + '\nCandidate IDs: ' + json.dumps(ids))] + [
            types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg") for p in paths]

    def count(self, contents) -> int:
        if self.provider == "openai":
            return self.client.responses.input_tokens.count(model=self.model, instructions=self.prompt,
                input=contents, reasoning=self.config["reasoning"], text=self.config["text"]).input_tokens
        result = self.client.models.count_tokens(model=self.model, contents=contents).total_tokens
        if result is None:
            raise ValueError("Token counter did not return input size")
        return result

    def generate(self, contents) -> dict:
        if self.provider == "openai":
            response = self.client.responses.create(model=self.model, instructions=self.prompt,
                                                    input=contents, **self.config)
        else:
            from google.genai import types
            response = self.client.models.generate_content(model=self.model, contents=contents,
                config=types.GenerateContentConfig(**self.config))
        return response.model_dump(mode="json", exclude_none=True)

    def text(self, raw: dict) -> str:
        if self.provider == "openai":
            if raw.get("status") != "completed":
                raise ValueError("Incomplete response saved; no automatic retry")
            return ''.join(p.get("text", "") for item in raw.get("output", [])
                           if item.get("type") == "message" for p in item.get("content", [])
                           if p.get("type") == "output_text")
        candidates = raw.get("candidates", [])
        if len(candidates) != 1 or candidates[0].get("finish_reason") != "STOP":
            raise ValueError("Incomplete Gemini response saved; no automatic retry")
        return ''.join(p.get("text", "") for p in candidates[0]["content"]["parts"] if not p.get("thought"))

    def usage(self, raw: dict) -> dict:
        if self.provider == "openai":
            usage = raw["usage"]
            inputs, outputs = usage["input_tokens"], usage["output_tokens"]
        else:
            usage = raw["usage_metadata"]
            inputs = usage["prompt_token_count"]
            outputs = (usage.get("candidates_token_count") or 0) + (usage.get("thoughts_token_count") or 0)
        return {"input_tokens": inputs, "output_including_reasoning_tokens": outputs,
                "estimated_usd_upper": (inputs * self.input_rate + outputs * self.output_rate) / 1_000_000}
