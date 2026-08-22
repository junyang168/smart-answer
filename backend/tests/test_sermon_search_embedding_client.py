from __future__ import annotations

from backend.api.sermon_search.embedding_client import EmbeddingClient


class FakeSharedProvider:
    def __init__(self):
        self.document_calls = []
        self.query_calls = []

    def embed_document_texts(self, texts, *, use_case, titles=None):
        self.document_calls.append((list(texts), use_case, titles))
        return [[1.0, 0.0] for _ in texts]

    def embed_queries(self, texts, use_case):
        self.query_calls.append((list(texts), use_case))
        return [[0.0, 1.0] for _ in texts]


def test_sermon_search_adapter_reuses_shared_document_and_query_contract(monkeypatch):
    monkeypatch.setenv("SERMON_SEARCH_EMBEDDING_PROVIDER", "google")
    client = EmbeddingClient()
    provider = FakeSharedProvider()
    client._provider = provider

    assert client.embed_documents(["one", "two"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert client.embed_query("question") == [0.0, 1.0]
    assert provider.document_calls == [(["one", "two"], "semantic_search", None)]
    assert provider.query_calls == [(["question"], "semantic_search")]


def test_sermon_search_adapter_remains_disabled_without_provider(monkeypatch):
    monkeypatch.delenv("SERMON_SEARCH_EMBEDDING_PROVIDER", raising=False)
    client = EmbeddingClient()

    assert client.available is False
    assert client.embed_documents(["one"]) == []
    assert client.embed_query("question") == []
