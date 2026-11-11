from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import runpy
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from pptx import Presentation
import pytest

from backend.sunday_presenter import OneDrivePpts, upcoming_folder, service_day
from backend.tests.test_sunday_ppt_ready import ready
from backend.tests.test_sunday_assistant import agent


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    root = tmp_path / "OneDrive" / "主日"
    root.mkdir(parents=True)
    config = tmp_path / "presenter.json"
    config.write_text(json.dumps({"syncRoot": str(root), "webRootUrl":
        "https://onedrive.live.com/my?id=%2Fpersonal%2Ffixture%2FDocuments%2FDocuments%2F%E4%B8%BB%E6%97%A5"}))
    monkeypatch.setenv("SUNDAY_PRESENTER_CONFIG", str(config))
    monkeypatch.setenv("DATA_BASE_DIR", str(tmp_path))
    return OneDrivePpts.configured()


@pytest.mark.parametrize("stamp, expected", [
    ("2026-11-02T05:59:59+00:00", "2026-11-01"),
    ("2026-11-02T06:00:00+00:00", "2026-11-08"),
    ("2026-03-08T08:00:00+00:00", "2026-03-08"),
    ("2026-12-28T12:00:00+00:00", "2027-01-03"),
])
def test_upcoming_exact_calendar_never_falls_back_to_previous_week(cloud, stamp, expected):
    result = upcoming_folder(datetime.fromisoformat(stamp))
    assert result["date"] == expected
    assert parse_qs(urlsplit(result["folderUrl"]).query)["id"][0].endswith("/" + result["folderName"])
    assert result["cloudStatus"] == "not_verified"
    assert result["folderExists"] is False
    assert not list(cloud.root.iterdir())  # GET is read-only.


def test_unavailable_root_and_symlink_escape_never_manufacture_local_fallback(cloud, tmp_path):
    cloud.root.rmdir()
    with pytest.raises(OSError): cloud.generated(date(2026, 11, 8))
    assert not cloud.root.exists()
    cloud.root.mkdir()
    (cloud.root / "11-8-2026").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError): cloud.generated(date(2026, 11, 8))
    with pytest.raises(ValueError): cloud.file(date(2026, 11, 1), "../outside.pptx")


def test_copy_failure_preserves_complete_file_and_speaker_deck(cloud, tmp_path, monkeypatch):
    import backend.sunday_presenter as module
    original = tmp_path / "artifact.pptx"; original.write_bytes(b"original")
    target = cloud.publish_generated(date(2026, 11, 8), original)
    speaker = target.parent / "speaker.pptx"; speaker.write_bytes(b"speaker")
    def broken(source, output):
        output.write(b"partial")
        raise OSError("disk full")
    monkeypatch.setattr(module.shutil, "copyfileobj", broken)
    original.write_bytes(b"new version")
    with pytest.raises(OSError): cloud.publish_generated(date(2026, 11, 8), original)
    assert target.read_bytes() == b"original"
    assert speaker.read_bytes() == b"speaker"
    assert not list(target.parent.glob(".*tmp"))


def test_api_returns_no_store_and_503_if_configuration_missing(cloud, monkeypatch, tmp_path):
    from backend.api.router import sunday_service_router
    app = FastAPI(); app.include_router(sunday_service_router)
    client = TestClient(app)
    response = client.get("/admin/sunday-services/presenter/upcoming")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "syncRoot" not in response.json()
    monkeypatch.setenv("SUNDAY_PRESENTER_CONFIG", str(tmp_path / "missing.json"))
    assert client.get("/admin/sunday-services/presenter/upcoming").status_code == 503


def test_automatic_ppt_is_published_only_after_input_revalidation(ready, cloud, monkeypatch):
    agent, ppt, day, now, calls = ready
    publish = OneDrivePpts.publish_generated
    def check_not_ready(storage, day_value, source):
        with agent.store.db() as db:
            assert db.execute("SELECT status FROM ppt_jobs WHERE day=?", (day,)).fetchone()[0] == "generating"
        return publish(storage, day_value, source)
    monkeypatch.setattr(OneDrivePpts, "publish_generated", check_not_ready)
    ppt.tick(now)
    version = agent.store.get("ppt_readiness")["version"]
    target = cloud.generated(service_day(day))
    assert target.read_bytes() == ppt.downloadable(day, version).read_bytes()
    assert calls == [day]
    ppt.tick(now)
    assert calls == [day]  # No rerender or second publication for unchanged input.


def test_superseded_auto_render_does_not_publish_to_onedrive(ready, cloud):
    agent, ppt, day, now, _ = ready
    generator = ppt.generator
    def changed(day, *, output_path):
        generator(day, output_path=output_path)
        agent.repository.services[day].sermon_title = "new input"
    ppt.generator = changed
    ppt.tick(now)
    assert not cloud.generated(service_day(day)).exists()


def test_failed_cloud_publish_is_not_announced_as_ready(ready, cloud):
    agent, ppt, day, now, _ = ready
    cloud.root.rmdir()
    ppt.tick(now)
    assert agent.store.get("ppt_readiness")["status"] == "error"
    assert not [x for x in agent.store.outbox() if x["key"].startswith("ppt-ready:")]


def test_final_upload_download_and_email_use_same_onedrive_file(ready, cloud, monkeypatch, tmp_path):
    from backend.api import service, sunday_service_email
    agent, _, day, _, _ = ready
    monkeypatch.setattr(service, "repository", agent.repository)
    monkeypatch.setattr(service, "get_sunday_service", lambda _: agent.repository.services[day])
    monkeypatch.setattr(service, "SUNDAY_WORSHIP_DIR", tmp_path / "old-local")
    monkeypatch.setattr(sunday_service_email, "SUNDAY_WORSHIP_DIR", tmp_path / "old-local")
    monkeypatch.setattr(service, "build_sunday_service_email_bodies", lambda _: ("", "<p>test</p>"))
    agent.repository.set_sunday_service_final_ppt = lambda _, filename: agent.repository.services[day].model_copy(update={"final_ppt_filename": filename})
    def save(_, body):
        agent.repository.services[day].final_ppt_filename = service._build_final_ppt_filename(day)
        return agent.repository.services[day]
    agent.repository.set_sunday_service_email_body = save
    deck = Presentation(); deck.slides.add_slide(deck.slide_layouts[6])
    content = BytesIO(); deck.save(content); data = content.getvalue()
    draft = tmp_path / "generated.pptx"; draft.write_bytes(b"initial auto generated")
    first = cloud.publish_generated(service_day(day), draft)
    service.upload_final_sunday_service_ppt(day, UploadFile(filename="final.pptx", file=BytesIO(data)))
    target = service.get_final_sunday_service_ppt(day)
    assert target.parent == cloud.folder(service_day(day))
    assert target == first
    assert list(target.parent.iterdir()) == [target]
    assert target.read_bytes() == data
    assert sunday_service_email._resolve_ppt_path(agent.repository.services[day]) == target
    assert not (tmp_path / "old-local").exists()


def test_website_generation_returns_onedrive_path_without_leaving_old_local_output(ready, cloud, monkeypatch, tmp_path):
    from backend.api import service
    agent, ppt, day, _, _ = ready
    monkeypatch.setattr(service, "repository", agent.repository)
    monkeypatch.setattr(service, "get_sunday_service", lambda _: agent.repository.services[day])
    monkeypatch.setattr(service, "PPT_TEMPLATE_FILE", ppt.template)
    old = tmp_path / "old-local"
    monkeypatch.setattr(service, "SUNDAY_WORSHIP_DIR", old)
    monkeypatch.setattr(service, "list_fellowships", lambda: [])
    monkeypatch.setattr(service, "_fetch_scripture_sections", lambda _: [{"display": "馬太福音20:28", "book": "馬太福音",
        "verses": [{"chapter": 20, "verse": 28, "text": "測試"}]}])
    def render(template, replacements, target, **kwargs):
        assert old not in target.parents
        assert cloud.folder(service_day(day)).is_dir()
        deck = Presentation(); deck.slides.add_slide(deck.slide_layouts[6]); deck.save(target)
    monkeypatch.setattr(service, "generate_presentation_from_template", render)
    result = service.generate_sunday_service_ppt(day)
    assert result == cloud.generated(service_day(day))
    assert len(Presentation(result).slides) == 1
    assert not list(old.glob("*.pptx"))
    final = cloud.file(service_day(day), service._build_final_ppt_filename(day))
    final.write_bytes(b"human final")
    service.generate_sunday_service_ppt(day)
    assert final.read_bytes() == b"human final"


def test_historical_migration_is_repeatable_and_refuses_collision(cloud, tmp_path):
    migration = runpy.run_path(str(Path(__file__).parents[2] / "scripts/migrate-sunday-ppts.py"))
    source = tmp_path / "legacy"; source.mkdir()
    original = source / "聖道教會2026-11-01主日崇拜.pptx"; original.write_bytes(b"same bytes")
    (source / "template.pptx").write_bytes(b"template")
    rows = migration["plan"](source, cloud)
    assert len(rows) == 1
    migration["apply"](rows)
    assert Path(rows[0]["target"]).read_bytes() == original.read_bytes()
    assert migration["plan"](source, cloud)[0]["status"] == "identical"
    original.write_bytes(b"conflict")
    with pytest.raises(ValueError, match="refusing to overwrite"): migration["plan"](source, cloud)


def test_manual_upload_overwrites_single_playback_and_regeneration_stays_outside_day(cloud, tmp_path):
    day = date(2026, 11, 8)
    draft = tmp_path / "generated.pptx"; draft.write_bytes(b"first generated")
    playback = cloud.publish_generated(day, draft)
    sermon = playback.parent / "講道.pptx"; sermon.write_bytes(b"speaker")
    final = tmp_path / "revised.pptx"; final.write_bytes(b"human revision")
    assert cloud.publish_final(day, final) == playback
    assert playback.read_bytes() == b"human revision"
    draft.write_bytes(b"new generated draft")
    regenerated = cloud.publish_generated(day, draft)
    assert regenerated.read_bytes() == b"new generated draft"
    assert regenerated.parent == cloud.archive_folder(day, "崇拜PPT版本")
    assert playback.read_bytes() == b"human revision"
    assert set(playback.parent.iterdir()) == {playback, sermon}
    assert any(p.read_bytes() == b"first generated" for p in regenerated.parent.iterdir())


def test_identical_upload_still_establishes_manual_final_ownership(cloud, tmp_path):
    day = date(2026, 11, 8)
    source = tmp_path / "original.pptx"; source.write_bytes(b"generated")
    playback = cloud.publish_generated(day, source)
    cloud.publish_final(day, source)
    source.write_bytes(b"later regeneration")
    cloud.publish_generated(day, source)
    assert playback.read_bytes() == b"generated"


def test_direct_manual_edit_and_missing_ownership_never_get_overwritten(cloud, tmp_path):
    day = date(2026, 11, 8)
    source = tmp_path / "original.pptx"; source.write_bytes(b"generated")
    playback = cloud.publish_generated(day, source)
    playback.write_bytes(b"edited directly in PowerPoint")
    source.write_bytes(b"new automated version")
    cloud.publish_generated(day, source)
    assert playback.read_bytes() == b"edited directly in PowerPoint"
    cloud._state_path(day).unlink()
    cloud.publish_generated(day, source)
    assert playback.read_bytes() == b"edited directly in PowerPoint"


def test_legacy_duplicate_is_archived_outside_date_folder(cloud, tmp_path):
    day = date(2026, 11, 8)
    folder = cloud.folder(day); folder.mkdir()
    legacy = folder / "主日崇拜-2026-11-08-自動生成.pptx"
    legacy.write_bytes(b"old generated")
    final = cloud.generated(day); final.write_bytes(b"already uploaded final")
    source = tmp_path / "new.pptx"; source.write_bytes(b"new draft")
    cloud.publish_generated(day, source)
    assert list(folder.iterdir()) == [final]
    assert final.read_bytes() == b"already uploaded final"
    assert {p.read_bytes() for p in cloud.archive_folder(day, "崇拜PPT版本").iterdir()} == {b"old generated", b"new draft"}


def test_background_worker_never_replaces_uploaded_revision(ready, cloud, tmp_path):
    agent, ppt, day, now, calls = ready
    ppt.tick(now)
    target = cloud.generated(service_day(day))
    final = tmp_path / "manual.pptx"; final.write_bytes(b"manual final")
    cloud.publish_final(service_day(day), final)
    agent.repository.services[day].sermon_title = "updated title"
    ppt.tick(now)
    ppt.tick(now)
    assert target.read_bytes() == b"manual final"
    assert len(calls) == 2
    assert list(target.parent.iterdir()) == [target]


def test_archive_symlink_escape_rejected_without_touching_playback(cloud, tmp_path):
    (cloud.root / "歷史版本").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        cloud.archive_folder(date(2026, 11, 8), "講員PPT版本")


def test_failed_final_publish_keeps_previous_deck_and_protects_it_from_worker(cloud, tmp_path, monkeypatch):
    import backend.sunday_presenter as module
    day = date(2026, 11, 8)
    source = tmp_path / "original.pptx"; source.write_bytes(b"generated")
    target = cloud.publish_generated(day, source)
    final = tmp_path / "final.pptx"; final.write_bytes(b"final")
    original_copy = cloud._copy
    def fail_final(storage, source_path, target_path):
        if source_path == final and target_path == target:
            raise OSError("disk full")
        return original_copy(source_path, target_path)
    with monkeypatch.context() as patch:
        patch.setattr(module.OneDrivePpts, "_copy", fail_final)
        with pytest.raises(OSError):
            cloud.publish_final(day, final)
    assert target.read_bytes() == b"generated"
    source.write_bytes(b"new draft")
    cloud.publish_generated(day, source)
    assert target.read_bytes() == b"generated"
