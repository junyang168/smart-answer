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
        if (
            module_name.startswith("backend.api.config")
            or module_name.startswith("backend.api.service")
            or module_name.startswith("backend.api.storage")
        ):
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


def test_list_public_fellowship_documents_shows_inputs_and_hides_generated_outputs(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    docs_dir = tmp_path / "data" / "fellowship" / "docs" / "2026-05-22"
    (docs_dir / "查經講稿.md").write_text("prepared manuscript", encoding="utf-8")
    (docs_dir / "主題與查經重點.md").write_text("report", encoding="utf-8")
    (docs_dir / "recording.transcript.generated.md").write_text("generated", encoding="utf-8")
    (docs_dir / "達拉斯聖道教會團契查經 - 2026_05_22 19_10 CDT - Recording.mp4").write_bytes(b"mp4")
    audio_dir = docs_dir / "audio"
    audio_dir.mkdir()
    (audio_dir / "達拉斯聖道教會團契查經 - 2026_05_22 19_10 CDT - Recording.mp3").write_bytes(b"mp3")

    documents = service.list_public_fellowship_documents("05/22/2026")
    names = {document.name for document in documents}

    assert "查經講稿.md" in names
    assert "恩典的國度，僕人的生命.pptx" in names
    assert "達拉斯聖道教會團契查經 - 2026_05_22 19_10 CDT - Recording.mp4" in names
    assert "主題與查經重點.md" not in names
    assert "recording.transcript.generated.md" not in names
    assert "audio/達拉斯聖道教會團契查經 - 2026_05_22 19_10 CDT - Recording.mp3" not in names


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


def test_get_public_fellowship_document_path_allows_input_mp4(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    docs_dir = tmp_path / "data" / "fellowship" / "docs" / "2026-05-22"
    recording_name = "達拉斯聖道教會團契查經 - 2026_05_22 19_10 CDT - Recording.mp4"
    (docs_dir / recording_name).write_bytes(b"mp4")

    path, media_type = service.get_public_fellowship_document_path("2026-05-22", recording_name)

    assert path.name == recording_name
    assert media_type == "video/mp4"


def test_get_public_fellowship_document_path_hides_generated_outputs(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    docs_dir = tmp_path / "data" / "fellowship" / "docs" / "2026-05-22"
    (docs_dir / "主題與查經重點.md").write_text("report", encoding="utf-8")
    (docs_dir / "recording.transcript.generated.md").write_text("generated", encoding="utf-8")

    for document_name in ("主題與查經重點.md", "recording.transcript.generated.md"):
        try:
            service.get_public_fellowship_document_path("2026-05-22", document_name)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 404
        else:
            raise AssertionError(f"Expected generated document to be hidden: {document_name}")


def test_parse_google_drive_folder_id(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    folder_id = service.parse_google_drive_folder_id(
        "https://drive.google.com/drive/folders/19VF_eDRUkpBy0vc7YljpTFFPzgHiuTUX"
    )

    assert folder_id == "19VF_eDRUkpBy0vc7YljpTFFPzgHiuTUX"


def test_analysis_assets_selects_drive_recording_and_ignores_empty_chat(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    config_file = tmp_path / "data" / "config" / "fellowship.json"
    config_file.write_text(
        """
        [
          {
            "date": "06/19/2026",
            "title": "苦難與榮耀之路",
            "sourceLinks": [
              {
                "label": "Meet Recordings",
                "url": "https://drive.google.com/drive/folders/19VF_eDRUkpBy0vc7YljpTFFPzgHiuTUX"
              }
            ]
          }
        ]
        """,
        encoding="utf-8",
    )
    docs_dir = tmp_path / "data" / "fellowship" / "docs" / "2026-06-19"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "苦難與榮耀之路 太 16_20–17_13.md").write_text("prepared manuscript" * 100, encoding="utf-8")
    (docs_dir / "苦難與榮耀之路_太16_20-17_13_查經.pptx").write_bytes(b"pptx")

    def fake_drive_assets(folder_id, date):
        assert folder_id == "19VF_eDRUkpBy0vc7YljpTFFPzgHiuTUX"
        return [
            service.FellowshipAnalysisAsset(
                name="達拉斯聖道教會團契查經 - 2026/06/19 19:28 CDT - Chat",
                source="drive",
                kind="chat",
                size=44,
                usable=False,
                reason="emptyChat",
                driveFileId="chat-id",
            ),
            service.FellowshipAnalysisAsset(
                name="達拉斯聖道教會團契查經 - 2026/06/19 19:28 CDT - Recording",
                source="drive",
                kind="recording",
                size=213_100_000,
                usable=True,
                driveFileId="recording-id",
            ),
        ]

    monkeypatch.setattr(service, "_drive_folder_ids_for_entry", lambda entry: ["19VF_eDRUkpBy0vc7YljpTFFPzgHiuTUX"])
    monkeypatch.setattr(service, "_list_drive_folder_assets", fake_drive_assets)

    assets = service.resolve_fellowship_analysis_assets("2026-06-19")

    assert assets.recording is not None
    assert assets.recording.drive_file_id == "recording-id"
    assert assets.empty_chat is not None
    assert assets.empty_chat.reason == "emptyChat"
    assert assets.transcript is not None
    assert assets.pptx is not None


def test_prepared_chinese_manuscript_is_not_meeting_transcript(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    manuscript = "原因就在一句話：門徒名字認對了，畫面卻錯了。\n【讀經：馬太福音 16:20】\n" * 20
    transcript = "00:00:01 Jun Yang: 大家平安\n00:00:04 Mary: 我有一個問題\n00:00:08 Jun Yang: 好\n00:00:12 Mary: 分享一下\n"

    assert service._looks_like_meeting_transcript(manuscript) is False
    assert service._looks_like_meeting_transcript(transcript) is True
