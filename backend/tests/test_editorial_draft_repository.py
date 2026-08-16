from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from backend.pipeline.editorial_draft_repository import (
    create_automated_publication_decision,
    publish_editorial_draft,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_publication_gates(source: Path, draft_id: str = "DRAFT-1") -> None:
    manuscript = source / "draft.md"
    audit = source / "audit.json"
    review = source / "review.json"
    manuscript_sha = _sha256(manuscript)
    _write(
        audit,
        json.dumps(
            {
                "draft_id": draft_id,
                "status": "pass_with_warnings",
                "summary": {"error_total": 0},
                "fingerprint": {"draft_sha256": manuscript_sha},
            }
        ),
    )
    _write(
        review,
        json.dumps({"reviewed_draft_sha256": manuscript_sha, "passed": True}),
    )
    _write(
        source / "publication-decision.json",
        json.dumps(
            {
                "schema_version": "human-publication-decision.v1",
                "draft_id": draft_id,
                "decision": "approved",
                "manuscript_sha256": manuscript_sha,
                "technical_audit_sha256": _sha256(audit),
                "editorial_review_path": "review.json",
                "editorial_review_sha256": _sha256(review),
            }
        ),
    )


def test_publish_editorial_draft_copies_only_manifest_bound_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write(source / "draft.md", "# 正文\n")
    _write(source / "presentation.json", "{}")
    _write_publication_gates(source)
    _write(source / "private-generation.json", "{}")
    manifest = {
        "schema_version": "editorial-draft-manifest.v1",
        "drafts": [
            {
                "draft_id": "DRAFT-1",
                "relative_path": "draft.md",
                "presentation_package_path": "presentation.json",
                "audit_config": {
                    "knowledge_snapshot_path": "presentation.json",
                    "audit_output_path": "audit.json",
                    "editorial_review_path": "review.json",
                    "publication_decision_path": "publication-decision.json",
                },
            }
        ],
    }
    manifest_path = source / "editorial-draft-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = publish_editorial_draft(
        manifest_path,
        "DRAFT-1",
        destination_root=tmp_path / "wang_repository",
    )

    destination = Path(result["destination"])
    assert (destination / "draft.md").read_text(encoding="utf-8") == "# 正文\n"
    assert (destination / "presentation.json").is_file()
    assert (destination / "audit.json").is_file()
    assert (destination / "review.json").is_file()
    assert (destination / "publication-decision.json").is_file()
    assert not (destination / "private-generation.json").exists()
    published = json.loads(
        (destination / "editorial-draft-manifest.json").read_text(encoding="utf-8")
    )
    assert [item["draft_id"] for item in published["drafts"]] == ["DRAFT-1"]


def test_automated_pass_creates_bound_decision_and_publishes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write(source / "draft.md", "# 正文\n")
    _write(source / "presentation.json", "{}")
    manuscript_sha = _sha256(source / "draft.md")
    _write(
        source / "audit.json",
        json.dumps(
            {
                "draft_id": "DRAFT-1",
                "status": "pass",
                "summary": {"error_total": 0},
                "fingerprint": {"draft_sha256": manuscript_sha},
            }
        ),
    )
    _write(
        source / "review.json",
        json.dumps(
            {
                "checks": {
                    "rubric_outcome": {
                        "manuscript_sha256": manuscript_sha,
                        "total_score": 90,
                        "passed": True,
                        "hard_gate_failures": [],
                        "declared_hard_failures": [],
                    }
                }
            }
        ),
    )
    manifest = {
        "schema_version": "editorial-draft-manifest.v1",
        "drafts": [
            {
                "draft_id": "DRAFT-1",
                "relative_path": "draft.md",
                "presentation_package_path": "presentation.json",
                "audit_config": {
                    "knowledge_snapshot_path": "presentation.json",
                    "audit_output_path": "audit.json",
                    "editorial_review_path": "review.json",
                },
            }
        ],
    }
    manifest_path = source / "editorial-draft-manifest.json"
    _write(manifest_path, json.dumps(manifest))

    decision_path = create_automated_publication_decision(
        manifest_path, "DRAFT-1"
    )
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["schema_version"] == "automated-publication-decision.v1"
    assert decision["approval_authority"] == "automated_quality_gates"
    assert decision["editorial_total_score"] == 90

    result = publish_editorial_draft(
        manifest_path,
        "DRAFT-1",
        destination_root=tmp_path / "repository",
    )
    destination = Path(result["destination"])
    assert (destination / "automated-publication-decision.json").is_file()


def test_automated_publication_rejects_score_below_ninety(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write(source / "draft.md", "# 正文\n")
    manuscript_sha = _sha256(source / "draft.md")
    _write(
        source / "audit.json",
        json.dumps(
            {
                "draft_id": "DRAFT-1",
                "status": "pass",
                "summary": {"error_total": 0},
                "fingerprint": {"draft_sha256": manuscript_sha},
            }
        ),
    )
    _write(
        source / "review.json",
        json.dumps(
            {
                "manuscript_sha256": manuscript_sha,
                "passed": False,
                "total_score": 89,
                "hard_gate_failures": [],
                "declared_hard_failures": [],
            }
        ),
    )
    manifest_path = source / "editorial-draft-manifest.json"
    _write(
        manifest_path,
        json.dumps(
            {
                "drafts": [
                    {
                        "draft_id": "DRAFT-1",
                        "relative_path": "draft.md",
                        "audit_config": {
                            "audit_output_path": "audit.json",
                            "editorial_review_path": "review.json",
                        },
                    }
                ]
            }
        ),
    )

    with pytest.raises(ValueError, match=r"bound 90\+ editorial pass"):
        create_automated_publication_decision(manifest_path, "DRAFT-1")


def test_publish_editorial_draft_rejects_path_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest_path = source / "editorial-draft-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "drafts": [
                    {
                        "draft_id": "DRAFT-1",
                        "relative_path": "../outside.md",
                        "audit_config": {
                            "audit_output_path": "audit.json",
                            "editorial_review_path": "review.json",
                            "publication_decision_path": "publication-decision.json",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes manifest directory"):
        publish_editorial_draft(
            manifest_path,
            "DRAFT-1",
            destination_root=tmp_path / "repository",
        )


def test_publish_editorial_draft_rejects_stale_audit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write(source / "draft.md", "# 正文\n")
    _write_publication_gates(source)
    audit = json.loads((source / "audit.json").read_text(encoding="utf-8"))
    audit["fingerprint"]["draft_sha256"] = "0" * 64
    _write(source / "audit.json", json.dumps(audit))
    manifest = {
        "drafts": [
            {
                "draft_id": "DRAFT-1",
                "relative_path": "draft.md",
                "audit_config": {
                    "audit_output_path": "audit.json",
                    "editorial_review_path": "review.json",
                    "publication_decision_path": "publication-decision.json",
                },
            }
        ]
    }
    manifest_path = source / "editorial-draft-manifest.json"
    _write(manifest_path, json.dumps(manifest))

    with pytest.raises(ValueError, match="fingerprint does not match"):
        publish_editorial_draft(
            manifest_path,
            "DRAFT-1",
            destination_root=tmp_path / "repository",
        )
