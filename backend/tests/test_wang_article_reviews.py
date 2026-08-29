from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api import wang_article_reviews


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    staging = tmp_path / "staging"
    authoring = staging / "topic-essays" / "church-foundation" / "authoring-v1"
    manuscript = authoring / "draft.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(
        '# 标题\n\n<!-- provenance: {"attribution":"professor"} -->\n正文。',
        encoding="utf-8",
    )
    workflow = authoring / "workflow-status.json"
    _write_json(workflow, {"status": "drafted_grounding_not_run"})
    manifest_root = staging / "topic-essay-reviews"
    _write_json(
        manifest_root / "church-foundation-v1.json",
        {
            "schema_version": wang_article_reviews.MANIFEST_SCHEMA,
            "review_id": "church-foundation-v1",
            "title": "标题",
            "passage": "太16:16-23；弗2:20",
            "registered_at": "2026-08-28T00:00:00+00:00",
            "manuscript_relative_path": str(manuscript.relative_to(staging)),
            "manuscript_sha256": hashlib.sha256(manuscript.read_bytes()).hexdigest(),
            "workflow_status_relative_path": str(workflow.relative_to(staging)),
            "workflow_status_sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
            "authoring_packet_sha256": "packet-sha",
            "brief_sha256": "brief-sha",
        },
    )
    monkeypatch.setattr(wang_article_reviews, "WANG_STAGING_DIR", staging)
    monkeypatch.setattr(wang_article_reviews, "REVIEW_MANIFEST_ROOT", manifest_root)
    return manuscript, manifest_root


def test_internal_review_is_sha_bound_and_not_a_publication(tmp_path: Path, monkeypatch) -> None:
    _fixture(tmp_path, monkeypatch)

    result = wang_article_reviews.article_review("church-foundation-v1")

    assert result["status"] == "internal_review"
    assert result["integrity_status"] == "verified"
    assert "provenance" not in result["markdown"]
    assert "正文。" in result["markdown"]
    assert [item["state"] for item in result["stage_checks"]] == [
        "complete", "not_run", "not_run", "not_run", "not_run"
    ]
    assert "publication_decision" not in result


def test_changed_manuscript_invalidates_review_preview(tmp_path: Path, monkeypatch) -> None:
    manuscript, _ = _fixture(tmp_path, monkeypatch)
    manuscript.write_text(manuscript.read_text(encoding="utf-8") + "\n改变。", encoding="utf-8")

    listing = wang_article_reviews.list_article_reviews()
    assert listing["reviews"][0]["integrity_status"] == "changed"
    with pytest.raises(HTTPException) as exc:
        wang_article_reviews.article_review("church-foundation-v1")
    assert exc.value.status_code == 409


def test_review_manifest_cannot_escape_staging(tmp_path: Path, monkeypatch) -> None:
    _, manifest_root = _fixture(tmp_path, monkeypatch)
    manifest_path = manifest_root / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manuscript_relative_path"] = "../../secret.md"
    _write_json(manifest_path, manifest)

    listing = wang_article_reviews.list_article_reviews()
    assert listing["reviews"] == []
    assert "leaves Wang staging" in listing["warnings"][0]["message"]
