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
    (docs_dir / "恩典的國度，僕人的生命.pptx").write_bytes(b"pptx")

    monkeypatch.setenv("DATA_BASE_DIR", str(data_dir))
    monkeypatch.setenv("FULL_ARTICLE_ROOT", str(full_article_dir))
    for module_name in list(sys.modules):
        if module_name.startswith("backend.api.config") or module_name.startswith("backend.api.service"):
            sys.modules.pop(module_name, None)
    return importlib.import_module("backend.api.service")


def test_list_fellowship_documents_uses_iso_date_folder(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    documents = service.list_fellowship_documents("05/22/2026")
    document = next(doc for doc in documents if doc.name == "lesson notes.txt")

    assert document.url == "/admin/fellowships/2026-05-22/documents/lesson%20notes.txt"
    assert document.size == 5


def test_list_fellowship_documents_accepts_iso_date(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    documents = service.list_fellowship_documents("2026-05-22")
    document = next(doc for doc in documents if doc.name == "lesson notes.txt")

    assert document.url == "/admin/fellowships/2026-05-22/documents/lesson%20notes.txt"


def test_list_fellowship_documents_encodes_non_ascii_pptx(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    documents = service.list_fellowship_documents("05/22/2026")
    document = next(doc for doc in documents if doc.name == "恩典的國度，僕人的生命.pptx")

    assert document.url == (
        "/admin/fellowships/2026-05-22/documents/"
        "%E6%81%A9%E5%85%B8%E7%9A%84%E5%9C%8B%E5%BA%A6%EF%BC%8C"
        "%E5%83%95%E4%BA%BA%E7%9A%84%E7%94%9F%E5%91%BD.pptx"
    )


def test_get_fellowship_document_path_accepts_iso_date(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    path, media_type = service.get_fellowship_document_path("2026-05-22", "lesson notes.txt")

    assert path.name == "lesson notes.txt"
    assert media_type == "text/plain"


def test_get_fellowship_document_path_rejects_traversal(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    try:
        service.get_fellowship_document_path("05/22/2026", "../secret.txt")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("Expected path traversal to be rejected")
