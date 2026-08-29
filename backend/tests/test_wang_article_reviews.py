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


def _fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    staging = tmp_path / "staging"
    authoring = staging / "topic-essays" / "church-foundation" / "authoring-v1"
    manuscript = authoring / "draft.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(
        '# 标题\n\n<!-- provenance: {"attribution":"professor","claim_ids":["CL-1"]} -->\n正文。',
        encoding="utf-8",
    )
    workflow = authoring / "workflow-status.json"
    _write_json(workflow, {"status": "drafted_grounding_not_run"})
    packet = authoring / "topic-authoring-packet.json"
    _write_json(
        packet,
        {
            "result": {
                "packet_sha256": "packet-sha",
                "knowledge": {
                    "claims": [{"claim_id": "CL-1", "evidence_step_ids": ["ES-1"]}],
                    "evidence_steps": [
                        {
                            "evidence_step_id": "ES-1",
                            "source_fragment_ids": ["FR-1", "FR-1", "FR-NOTES"],
                        }
                    ],
                    "source_fragments": [
                        {
                            "fragment_id": "FR-1",
                            "source_id": "SRC-1",
                            "media_time": 65,
                            "media_end_time": 82,
                            "verbatim_excerpt": "教授逐字稿原句。",
                        },
                        {
                            "fragment_id": "FR-NOTES",
                            "source_id": "NOTES-1",
                            "verbatim_excerpt": "母本中的对应段落。",
                        },
                    ],
                    "source_documents": [
                        {
                            "source_id": "SRC-1",
                            "source_type": "sermon_transcript",
                            "title": "讲道一",
                            "transcript_id": "讲道一",
                        },
                        {
                            "source_id": "NOTES-1",
                            "source_type": "notes_manuscript",
                            "title": "十六章母本",
                            "source_url": "/resources/notes_to_manuscript_series/series/十六章",
                        },
                    ],
                },
            }
        },
    )
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
            "authoring_packet_relative_path": str(packet.relative_to(staging)),
            "authoring_packet_file_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
            "authoring_packet_sha256": "packet-sha",
            "brief_sha256": "brief-sha",
        },
    )
    monkeypatch.setattr(wang_article_reviews, "WANG_STAGING_DIR", staging)
    monkeypatch.setattr(wang_article_reviews, "REVIEW_MANIFEST_ROOT", manifest_root)
    return manuscript, manifest_root, packet


def test_internal_review_is_sha_bound_and_not_a_publication(tmp_path: Path, monkeypatch) -> None:
    _fixture(tmp_path, monkeypatch)

    result = wang_article_reviews.article_review("church-foundation-v1")

    assert result["status"] == "internal_review"
    assert result["integrity_status"] == "verified"
    assert "provenance" not in result["markdown"]
    assert "正文。" in result["markdown"]
    assert "#review-source-evidence-p1" in result["markdown"]
    assert len(result["source_annotations"]) == 1
    assert [item["fragment_ids"] for item in result["source_annotations"][0]["sources"]] == [
        ["FR-1"],
        ["FR-NOTES"],
    ]
    transcript, notes = result["source_annotations"][0]["sources"]
    assert transcript["full_source_url"] == "/resources/sermons/%E8%AE%B2%E9%81%93%E4%B8%80"
    assert transcript["media"]["start_seconds"] == 65
    assert notes["full_source_url"] == "/resources/notes_to_manuscript_series/series/十六章"
    assert [item["state"] for item in result["stage_checks"]] == [
        "complete", "not_run", "not_run", "not_run", "not_run"
    ]
    assert "publication_decision" not in result


def test_changed_manuscript_invalidates_review_preview(tmp_path: Path, monkeypatch) -> None:
    manuscript, _, _ = _fixture(tmp_path, monkeypatch)
    manuscript.write_text(manuscript.read_text(encoding="utf-8") + "\n改变。", encoding="utf-8")

    listing = wang_article_reviews.list_article_reviews()
    assert listing["reviews"][0]["integrity_status"] == "changed"
    with pytest.raises(HTTPException) as exc:
        wang_article_reviews.article_review("church-foundation-v1")
    assert exc.value.status_code == 409


def test_review_manifest_cannot_escape_staging(tmp_path: Path, monkeypatch) -> None:
    _, manifest_root, _ = _fixture(tmp_path, monkeypatch)
    manifest_path = manifest_root / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manuscript_relative_path"] = "../../secret.md"
    _write_json(manifest_path, manifest)

    listing = wang_article_reviews.list_article_reviews()
    assert listing["reviews"] == []
    assert "leaves Wang staging" in listing["warnings"][0]["message"]


def test_changed_authoring_packet_invalidates_review_preview(tmp_path: Path, monkeypatch) -> None:
    _, _, packet = _fixture(tmp_path, monkeypatch)
    packet.write_text(packet.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    listing = wang_article_reviews.list_article_reviews()
    assert listing["reviews"][0]["integrity_status"] == "changed"
    with pytest.raises(HTTPException) as exc:
        wang_article_reviews.article_review("church-foundation-v1")
    assert exc.value.status_code == 409


def test_paragraph_without_verifiable_fragments_has_no_empty_source_control(
    tmp_path: Path, monkeypatch
) -> None:
    manuscript, _, packet = _fixture(tmp_path, monkeypatch)
    payload = json.loads(packet.read_text(encoding="utf-8"))
    payload["result"]["knowledge"]["source_fragments"] = []
    _write_json(packet, payload)
    manifest_path = packet.parents[3] / "topic-essay-reviews" / "church-foundation-v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["authoring_packet_file_sha256"] = hashlib.sha256(packet.read_bytes()).hexdigest()
    _write_json(manifest_path, manifest)

    result = wang_article_reviews.article_review("church-foundation-v1")

    assert result["source_annotations"] == []
    assert "review-source-evidence" not in result["markdown"]
    assert manuscript.read_text(encoding="utf-8").endswith("正文。")
