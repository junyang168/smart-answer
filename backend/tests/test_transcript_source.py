from __future__ import annotations

from pathlib import Path

from backend.pipeline.transcript_source import resolve_transcript_path


def _directory(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def test_published_wins_regardless_of_cli_directory_order(tmp_path: Path) -> None:
    published = _directory(tmp_path, "script_published")
    reviewed = _directory(tmp_path, "script_review")
    (published / "lecture.json").write_text("published", encoding="utf-8")
    (reviewed / "lecture.json").write_text("reviewed", encoding="utf-8")

    assert resolve_transcript_path("lecture", [reviewed, published]) == (
        published / "lecture.json"
    )
    assert resolve_transcript_path("lecture", [published, reviewed]) == (
        published / "lecture.json"
    )


def test_reviewed_is_used_when_published_does_not_exist(tmp_path: Path) -> None:
    published = _directory(tmp_path, "script_published")
    reviewed = _directory(tmp_path, "script_review")
    (reviewed / "lecture.json").write_text("reviewed", encoding="utf-8")

    assert resolve_transcript_path("lecture", [published, reviewed]) == (
        reviewed / "lecture.json"
    )


def test_missing_transcript_has_no_source(tmp_path: Path) -> None:
    published = _directory(tmp_path, "script_published")
    reviewed = _directory(tmp_path, "script_review")
    assert resolve_transcript_path("absent", [reviewed, published]) is None
