from __future__ import annotations

import os
from typing import List, Sequence

from ..semantic_index.embeddings import GoogleGeminiEmbeddingProvider


class EmbeddingClient:
    def __init__(self) -> None:
        self.provider = os.getenv("SERMON_SEARCH_EMBEDDING_PROVIDER", "").strip().lower()
        self.model = os.getenv("SERMON_SEARCH_EMBEDDING_MODEL", "gemini-embedding-001")
        self.dimensions = int(os.getenv("SERMON_SEARCH_EMBEDDING_DIMENSIONS", "768"))
        self.batch_size = int(os.getenv("SERMON_SEARCH_EMBEDDING_BATCH_SIZE", "64"))
        self._provider = None

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
        if self._provider is None:
            self._provider = GoogleGeminiEmbeddingProvider(
                model=self.model,
                dimensions=self.dimensions,
                batch_size=max(1, self.batch_size),
            )
        if task_type == "RETRIEVAL_DOCUMENT":
            return self._provider.embed_document_texts(
                texts, use_case="semantic_search"
            )
        return self._provider.embed_queries(texts, use_case="semantic_search")
