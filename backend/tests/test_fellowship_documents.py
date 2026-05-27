from __future__ import annotations

import importlib
import os
import sys


def _load_service_with_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    full_article_dir = data_dir / "full_article"
    config_dir = data_dir / "config"
    docs_dir = data_dir / "fellowship" / "docs" / "2026-05-22"
    full_article_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (full_article_dir / "full_articles.json").write_text("[]", encoding="utf-8")
    (full_article_dir / "full_article_prompt.md").write_text("prompt", encoding="utf-8")
    (config_dir / "fellowship.json").write_text('[{"date":"05/22/2026"}]', encoding="utf-8")
    (docs_dir / "lesson notes.txt").write_text("hello", encoding="utf-8")

    monkeypatch.setenv("DATA_BASE_DIR", str(data_dir))
    monkeypatch.setenv("FULL_ARTICLE_ROOT", str(full_article_dir))
    for module_name in list(sys.modules):
        if module_name.startswith("backend.api.config") or module_name.startswith("backend.api.service"):
            sys.modules.pop(module_name, None)
    return importlib.import_module("backend.api.service")


def test_list_fellowship_documents_uses_iso_date_folder(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    documents = service.list_fellowship_documents("05/22/2026")

    assert len(documents) == 1
    assert documents[0].name == "lesson notes.txt"
    assert documents[0].url == "/admin/fellowships/05%2F22%2F2026/documents/lesson%20notes.txt"
    assert documents[0].size == 5


def test_get_fellowship_document_path_rejects_traversal(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    try:
        service.get_fellowship_document_path("05/22/2026", "../secret.txt")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("Expected path traversal to be rejected")
