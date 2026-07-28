import json

import pytest

from backend.api import sermon_converter_service as service


@pytest.fixture
def transcript_workspace(tmp_path, monkeypatch):
    notes_dir = tmp_path / "notes_to_surmon"
    manuscript_dir = tmp_path / "transcripts_to_manuscript"
    published_dir = tmp_path / "script_published"
    reviewed_dir = tmp_path / "script_review"
    raw_dir = tmp_path / "script_patched"
    for folder in (notes_dir, manuscript_dir, published_dir, reviewed_dir, raw_dir):
        folder.mkdir()
    monkeypatch.setattr(service, "NOTES_TO_SERMON_DIR", notes_dir)
    monkeypatch.setattr(service, "TRANSCRIPTS_TO_MANUSCRIPT_DIR", manuscript_dir)
    monkeypatch.setattr(
        service,
        "SERMON_TRANSCRIPT_DIRS",
        (("published", published_dir), ("reviewed", reviewed_dir), ("raw", raw_dir)),
    )
    return {
        "notes": notes_dir,
        "manuscripts": manuscript_dir,
        "published": published_dir,
        "reviewed": reviewed_dir,
        "raw": raw_dir,
    }


def _write_transcript(path, paragraphs, published=False):
    payload = {"metadata": {}, "script": paragraphs} if published else paragraphs
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_resolve_prefers_published_and_ignores_comments(transcript_workspace):
    transcript_id = "lecture-4"
    _write_transcript(
        transcript_workspace["reviewed"] / f"{transcript_id}.json",
        [{"text": "reviewed text"}],
    )
    _write_transcript(
        transcript_workspace["published"] / f"{transcript_id}.json",
        [
            {"text": "## Published heading", "type": "subtitle"},
            {"text": "editor note", "type": "comment"},
            {"text": "Published body", "type": "content"},
        ],
        published=True,
    )

    info = service.get_sermon_transcript_info(f"{transcript_id}.json")
    resolved = service.resolve_sermon_transcript(transcript_id)
    content = service._load_sermon_transcript_content(resolved["path"])

    assert info["source_stage"] == "published"
    assert content == "## Published heading\n\nPublished body\n"


def test_import_links_transcript_and_protects_existing_input(transcript_workspace):
    transcript_id = "lecture-4"
    _write_transcript(
        transcript_workspace["reviewed"] / f"{transcript_id}.json",
        [{"text": "Reviewed transcript"}],
    )
    project = service.create_sermon_project(
        "Matthew 17",
        [],
        project_type="transcript",
        sermon_transcript_id=transcript_id,
    )

    result = service.import_sermon_transcript(project.id, transcript_id)
    assert result["source_stage"] == "reviewed"
    assert service.get_sermon_source(project.id) == "Reviewed transcript\n"

    service.save_sermon_source(project.id, "Manual changes")
    with pytest.raises(service.SermonTranscriptConflictError):
        service.import_sermon_transcript(project.id, transcript_id)

    service.import_sermon_transcript(project.id, transcript_id, overwrite=True)
    metadata = service.get_sermon_project_metadata(project.id)
    assert metadata.sermon_transcript_id == transcript_id
    assert metadata.sermon_transcript_source_stage == "reviewed"
    assert metadata.audit_passed is False
