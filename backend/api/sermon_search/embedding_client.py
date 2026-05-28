from __future__ import annotations

import os
from typing import List, Sequence


class EmbeddingClient:
    def __init__(self) -> None:
        self.provider = os.getenv("SERMON_SEARCH_EMBEDDING_PROVIDER", "").strip().lower()
        self.model = os.getenv("SERMON_SEARCH_EMBEDDING_MODEL", "gemini-embedding-001")
        self.dimensions = int(os.getenv("SERMON_SEARCH_EMBEDDING_DIMENSIONS", "768"))
        self._client = None

    @property
    def available(self) -> bool:
        return self.provider == "google"

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> List[float]:
        vectors = self._embed([text], task_type="RETRIEVAL_QUERY")
        return vectors[0] if vectors else []

    def _embed(self, texts: Sequence[str], task_type: str) -> List[List[float]]:
        if not self.available or not texts:
            return []
        from google import genai
        from google.genai import types

        if self._client is None:
            api_key = os.getenv("GEMINI_API_KEY") or None
            self._client = genai.Client(api_key=api_key) if api_key else genai.Client()
        response = self._client.models.embed_content(
            model=self.model,
            contents=list(texts),
            config=types.EmbedContentConfig(
                taskType=task_type,
                outputDimensionality=self.dimensions,
            ),
        )
        return [list(embedding.values) for embedding in response.embeddings or []]
