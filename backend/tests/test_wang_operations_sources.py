from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from backend.api.wang_operations import _cell, _sermon_rows, _source_body_sha256


def _catalog(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({
        "records": [{"transcript_id": "lecture", "title": "Lecture"}],
    }), encoding="utf-8")
    return path


def test_operations_status_uses_published_then_reviewed_fallback(tmp_path: Path) -> None:
    published = tmp_path / "script_published"
    reviewed = tmp_path / "script_review"
    published.mkdir()
    reviewed.mkdir()
    reviewed_path = reviewed / "lecture.json"
    published_path = published / "lecture.json"
    reviewed_path.write_text(json.dumps({"script": [{"index": 1, "text": "正文"}]}), encoding="utf-8")
    paths = SimpleNamespace(sermon_catalog=_catalog(tmp_path))

    assert _sermon_rows(paths, tmp_path)[0]["source_path"] == reviewed_path

    published_path.write_text(json.dumps({"script": [{"index": 1, "text": "正文"}]}), encoding="utf-8")
    assert _sermon_rows(paths, tmp_path)[0]["source_path"] == published_path


def test_editorial_only_publishing_does_not_make_extraction_stale(
    tmp_path: Path,
) -> None:
    published = tmp_path / "script_published"
    reviewed = tmp_path / "script_review"
    published.mkdir()
    reviewed.mkdir()
    reviewed_path = reviewed / "lecture.json"
    reviewed_path.write_text(json.dumps({"script": [{"index": 1, "text": "正文"}]}), encoding="utf-8")
    paths = SimpleNamespace(sermon_catalog=_catalog(tmp_path))
    reviewed_sha = _source_body_sha256(reviewed_path, "sermon")
    run = {
        "effective_status": "succeeded", "status": "succeeded",
        "finished_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc),
        "quality": {}, "run_id": "RUN-1", "trigger": "cli",
        "triggered_by": None, "model_id": None, "cost_usd": 0,
        "error_message": None,
        "input_sha256": {"source_sha256": reviewed_sha},
    }
    assert _cell(
        [run], stage="extraction", current_source_sha=reviewed_sha,
        upstream_finished=None,
    )["state"] == "current"

    published_path = published / "lecture.json"
    published_path.write_text(json.dumps({"script": [
        {"index": "subtitle-1", "type": "subtitle", "text": "## 编辑标题"},
        {"index": 1, "text": "正文"},
    ]}), encoding="utf-8")
    current_path = _sermon_rows(paths, tmp_path)[0]["source_path"]
    cell = _cell(
        [run], stage="extraction", current_source_sha=_source_body_sha256(current_path, "sermon"),
        upstream_finished=None,
    )
    assert cell["state"] == "current"

    published_path.write_text(json.dumps({"script": [{"index": 1, "text": "正文已改"}]}), encoding="utf-8")
    changed = _cell(
        [run], stage="extraction",
        current_source_sha=_source_body_sha256(published_path, "sermon"),
        upstream_finished=None,
    )
    assert changed["state"] == "stale"
    assert changed["reason"] == "source_changed"


def test_operations_status_has_no_source_only_when_both_are_missing(tmp_path: Path) -> None:
    (tmp_path / "script_published").mkdir()
    (tmp_path / "script_review").mkdir()
    paths = SimpleNamespace(sermon_catalog=_catalog(tmp_path))
    assert _sermon_rows(paths, tmp_path)[0]["source_path"] is None
