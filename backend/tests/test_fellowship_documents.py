from __future__ import annotations

import errno
import importlib
import os
import sys
import types


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
    (config_dir / "config.json").write_text(
        '{"users":[],"user_roles":[],"role_permissions":[]}',
        encoding="utf-8",
    )
    (config_dir / "sermon.json").write_text("[]", encoding="utf-8")
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


def test_public_document_text_route_reads_markdown_with_unicode_name(monkeypatch, tmp_path):
    _load_service_with_data_dir(monkeypatch, tmp_path)
    docs_dir = tmp_path / "data" / "fellowship" / "docs" / "2026-05-22"
    document_name = "教會的根基與權柄 太 16_18-19.md"
    (docs_dir / document_name).write_text("# 教會的根基與權柄\n\ncontent", encoding="utf-8")

    sys.modules.pop("backend.api.sc_api.router", None)
    router_module = importlib.import_module("backend.api.sc_api.router")

    response = router_module.read_public_fellowship_document_text("2026-05-22", document_name)

    assert response.status_code == 200
    assert response.media_type == "text/markdown"
    assert response.body.decode("utf-8").startswith("# 教會的根基與權柄")


def test_public_document_text_route_does_not_expose_hidden_or_binary_files(monkeypatch, tmp_path):
    _load_service_with_data_dir(monkeypatch, tmp_path)
    docs_dir = tmp_path / "data" / "fellowship" / "docs" / "2026-05-22"
    recording_name = "達拉斯聖道教會團契查經 - 2026_05_22 19_10 CDT - Recording.mp4"
    (docs_dir / "recording.transcript.generated.md").write_text("generated", encoding="utf-8")
    (docs_dir / recording_name).write_bytes(b"mp4")

    sys.modules.pop("backend.api.sc_api.router", None)
    router_module = importlib.import_module("backend.api.sc_api.router")

    try:
        router_module.read_public_fellowship_document_text("2026-05-22", "recording.transcript.generated.md")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("Expected generated transcript to remain hidden")

    try:
        router_module.read_public_fellowship_document_text("2026-05-22", recording_name)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("Expected binary recording to be rejected by text endpoint")


def test_parse_google_drive_folder_id(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    folder_id = service.parse_google_drive_folder_id(
        "https://drive.google.com/drive/folders/19VF_eDRUkpBy0vc7YljpTFFPzgHiuTUX"
    )

    assert folder_id == "19VF_eDRUkpBy0vc7YljpTFFPzgHiuTUX"


def test_list_drive_folder_assets_matches_underscore_dates_and_all_drives(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    class FakeRequest:
        def execute(self):
            return {
                "files": [
                    {
                        "id": "recording-id",
                        "name": "達拉斯聖道教會團契查經 - 2026_07_10 19_22 CDT - Recording.mp4",
                        "mimeType": "video/mp4",
                        "size": "187107205",
                        "modifiedTime": "2026-07-11T01:57:52.323Z",
                    },
                    {
                        "id": "other-id",
                        "name": "達拉斯聖道教會團契查經 - 2026_07_09 19_22 CDT - Recording.mp4",
                        "mimeType": "video/mp4",
                        "size": "1",
                        "modifiedTime": "2026-07-10T01:57:52.323Z",
                    },
                ]
            }

    class FakeFiles:
        def list(self, **kwargs):
            assert kwargs["includeItemsFromAllDrives"] is True
            assert kwargs["supportsAllDrives"] is True
            return FakeRequest()

    class FakeService:
        def files(self):
            return FakeFiles()

    monkeypatch.setattr(service, "_get_drive_service", lambda scopes: FakeService())

    assets = service._list_drive_folder_assets("folder-id", "07/10/2026")

    assert len(assets) == 1
    assert assets[0].drive_file_id == "recording-id"
    assert assets[0].kind == "recording"


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
                "label": "王守仁牧師講義",
                "url": "https://example.com/dr-wang"
              },
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
    (docs_dir / "recording.transcript.generated.md").write_text("generated transcript" * 100, encoding="utf-8")
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

    def fake_download_drive_recording(date, asset):
        return service.FellowshipAnalysisAsset(
            name="達拉斯聖道教會團契查經 - 2026_06_19 19_28 CDT - Recording.mp4",
            source="local",
            kind="recording",
            size=asset.size,
            usable=True,
        )

    monkeypatch.setattr(service, "_list_drive_folder_assets", fake_drive_assets)
    monkeypatch.setattr(service, "_search_drive_meet_assets", lambda date: [])
    monkeypatch.setattr(service, "_download_drive_recording_to_docs", fake_download_drive_recording)

    assets = service.resolve_fellowship_analysis_assets("2026-06-19")

    assert assets.recording is not None
    assert assets.recording.source == "local"
    assert assets.recording.name.endswith(".mp4")
    assert assets.empty_chat is not None
    assert assets.empty_chat.reason == "emptyChat"
    assert assets.transcript is not None
    assert assets.transcript.name == "苦難與榮耀之路 太 16_20–17_13.md"
    assert assets.pptx is not None
    generated_transcript = next(
        candidate for candidate in assets.candidates if candidate.name == "recording.transcript.generated.md"
    )
    assert generated_transcript.kind == "transcript"
    public_entry = service.get_public_fellowship("2026-06-19")
    assert [source.label for source in public_entry.source_links] == ["王守仁牧師講義"]


def test_prepared_chinese_manuscript_is_not_meeting_transcript(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    manuscript = "原因就在一句話：門徒名字認對了，畫面卻錯了。\n【讀經：馬太福音 16:20】\n" * 20
    transcript = "00:00:01 Jun Yang: 大家平安\n00:00:04 Mary: 我有一個問題\n00:00:08 Jun Yang: 好\n00:00:12 Mary: 分享一下\n"

    assert service._looks_like_meeting_transcript(manuscript) is False
    assert service._looks_like_meeting_transcript(transcript) is True


def test_ffmpeg_executable_prefers_configured_path(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    monkeypatch.setenv("FFMPEG_PATH", "/opt/bin/ffmpeg")
    monkeypatch.setattr(service.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    assert service._ffmpeg_executable() == "/opt/bin/ffmpeg"


def test_ffmpeg_executable_uses_imageio_fallback(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr(service.shutil, "which", lambda _name: None)
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        types.SimpleNamespace(get_ffmpeg_exe=lambda: "/tmp/imageio-ffmpeg"),
    )

    assert service._ffmpeg_executable() == "/tmp/imageio-ffmpeg"


def test_run_ffmpeg_command_retries_transient_deadlock(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    calls = {"count": 0}

    def fake_run(_cmd, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        return None

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)

    service._run_ffmpeg_command(["ffmpeg", "-version"], stage="test stage")

    assert calls["count"] == 2


def test_run_ffmpeg_command_reports_non_transient_oserror(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)

    def fake_run(_cmd, **_kwargs):
        raise OSError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    try:
        service._run_ffmpeg_command(["ffmpeg"], stage="test stage")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 500
        assert "test stage" in getattr(exc, "detail", "")
    else:
        raise AssertionError("Expected ffmpeg OSError to be reported")


class _FakeDrive:
    """files().list / files().get as the Drive API client chains them."""

    def __init__(self, items=(), visible=()):
        self.items, self.visible, self.queries = list(items), set(visible), []

    def files(self):
        return self

    def list(self, **kwargs):
        self.queries.append(kwargs["q"])
        items = self.items
        return type("R", (), {"execute": lambda _self: {"files": items}})()

    def get(self, fileId, **kwargs):
        visible = self.visible

        def execute(_self):
            if fileId not in visible:
                raise RuntimeError("404 File not found")
            return {"id": fileId}

        return type("R", (), {"execute": execute})()


def test_meet_files_are_found_by_name_wherever_meet_saved_them(monkeypatch, tmp_path):
    # OPS-28: on 2026-09-25 Meet saved the recording in
    # `Google Meet/達拉斯聖道教會團契查經 (recurring)`, not `Meet Recordings`.
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    owner = _FakeDrive(
        items=[
            {"id": "rec", "name": "達拉斯聖道教會團契查經 - 2026/09/25 19:24 CDT - Recording", "mimeType": "video/mp4", "size": "100"},
            {"id": "notes", "name": "達拉斯聖道教會團契查經 - 2026/09/25 19:24 CDT - Gemini 提供的会议记录", "mimeType": "application/vnd.google-apps.document"},
            {"id": "deck", "name": "查經 2026/09/25 講義.pptx", "mimeType": "application/vnd.openxmlformats"},  # not a Meet file
            {"id": "other-day", "name": "達拉斯聖道教會團契查經 - 2026/09/26 10:00 CDT - Recording", "mimeType": "video/mp4"},
        ]
    )
    monkeypatch.setattr(service, "_get_owner_drive_service", lambda: owner)

    assets = service._search_drive_meet_assets("2026-09-25")

    assert {(a.drive_file_id, a.kind) for a in assets} == {("rec", "recording"), ("notes", "transcript")}
    assert "name contains '2026/09/25'" in owner.queries[0]
    assert "trashed=false" in owner.queries[0]


def test_search_and_folder_results_are_merged_once(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    (tmp_path / "data" / "config" / "fellowship.json").write_text(
        '[{"date": "09/25/2026", "title": "你們不知道所求的是甚麼", "sourceLinks": []}]', encoding="utf-8"
    )
    recording = service.FellowshipAnalysisAsset(
        name="達拉斯聖道教會團契查經 - 2026/09/25 19:24 CDT - Recording",
        source="drive", kind="recording", size=100, usable=True, driveFileId="rec",
    )
    monkeypatch.setattr(service, "_list_drive_folder_assets", lambda folder_id, date: [recording])
    monkeypatch.setattr(service, "_search_drive_meet_assets", lambda date: [recording])
    downloaded = []

    def download(date, asset):
        downloaded.append(asset.drive_file_id)
        return service.FellowshipAnalysisAsset(name="達拉斯聖道教會團契查經 - 2026_09_25 19_24 CDT - Recording.mp4", source="local", kind="recording", size=100, usable=True)

    monkeypatch.setattr(service, "_download_drive_recording_to_docs", download)

    assets = service.resolve_fellowship_analysis_assets("2026-09-25")

    assert [a.drive_file_id for a in assets.candidates if a.source == "drive"] == ["rec"]
    assert downloaded == ["rec"]
    assert assets.recording is not None and assets.recording.source == "local"


def test_a_failed_search_is_reported_not_fatal(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    (tmp_path / "data" / "config" / "fellowship.json").write_text(
        '[{"date": "09/25/2026", "title": "t", "sourceLinks": []}]', encoding="utf-8"
    )
    monkeypatch.setattr(service, "_list_drive_folder_assets", lambda folder_id, date: [])

    def dead_token(date):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(service, "_search_drive_meet_assets", dead_token)

    assets = service.resolve_fellowship_analysis_assets("2026-09-25")

    assert any("Unable to search Drive for Meet files: invalid_grant" in m for m in assets.messages)


def test_downloads_use_the_owner_when_the_service_account_cannot_see_the_file(monkeypatch, tmp_path):
    service = _load_service_with_data_dir(monkeypatch, tmp_path)
    service_account = _FakeDrive(visible={"shared"})
    owner = _FakeDrive()
    monkeypatch.setattr(service, "_get_drive_service", lambda scopes: service_account)
    monkeypatch.setattr(service, "_get_owner_drive_service", lambda: owner)

    assert service._drive_service_for_file("shared", ["scope"]) is service_account
    assert service._drive_service_for_file("in-new-folder", ["scope"]) is owner
