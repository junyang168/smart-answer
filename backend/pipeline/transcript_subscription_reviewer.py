"""Structured GPT proofreading through ChatGPT subscription authentication."""

import json

from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


class TranscriptSubscriptionReviewer:
    model = "gpt-6.1-sol"

    def __init__(self, prompt, schema):
        self.prompt = prompt
        self.schema = schema
        self.config = {"backend": "codex_subscription", "reasoning_effort": "low",
                       "max_output_tokens": 8192, "schema": schema}

    def generate(self, contents):
        client = CodexSubscriptionClient(
            model=self.model, reasoning_effort=self.config["reasoning_effort"],
            max_output_tokens=self.config["max_output_tokens"],
        )
        answer = client.generate_json(self.prompt, contents[0]["content"], self.schema)
        return {"model": self.model, "backend": self.config["backend"],
                "answer": answer, "usage": client.last_usage}

    def text(self, raw):
        if raw.get("model") != self.model or raw.get("backend") != "codex_subscription":
            raise ValueError("Expected a saved Codex subscription response")
        return json.dumps(raw["answer"], ensure_ascii=False)

    def usage(self, raw):
        return {"estimated_usd_upper": 0.0}
