from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI


class DeepSeekClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.flash_model = os.getenv("DEEPSEEK_V4_FLASH_MODEL", "deepseek-v4-flash")
        self.pro_model = os.getenv("DEEPSEEK_V4_PRO_MODEL", "deepseek-v4-pro")
        self._client: Optional[OpenAI] = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def client(self) -> OpenAI:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def model_for_mode(self, mode: str) -> str:
        return self.pro_model if mode == "deep" else self.flash_model

    def generate_json(self, messages: List[Dict[str, str]], mode: str = "normal") -> Dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model_for_mode(mode),
            messages=messages,
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        return self._parse_json(content)

    def _parse_json(self, text: str) -> Dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            match = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
            if match:
                stripped = match.group(1).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                return json.loads(stripped[start : end + 1])
            raise

