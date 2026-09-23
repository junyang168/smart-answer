from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.pipeline.run_ledger_reconciliation import (
    RunLedgerReconciliationError,
    _deterministic_run_id,
    load_reconciliation,
)


SOURCE = "2019-3-31 宗主国与附庸国的约"


def _write(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    staging = tmp_path / "staging"
    root = staging / "repair"
    source_document = {"transcript_id": SOURCE, "source_id": "SRC-test"}
    coverage = {
        "available": True,
        "sentences": 10,
        "represented": 8,
        "excluded": 1,
        "unprocessed": 1,
        "exclusions_recorded": 1,
        "exclusions_terminal": 1,
        "by_category": {
            "prose": {
                "total": 8,
                "represented": 7,
                "unprocessed": 1,
                "represented_pct": 87.5,
            }
        },
    }
    extraction_path = root / "detailed-extractions/test.detailed-knowledge.json"
    extraction_sha = _write(
        extraction_path,
        {
            "source_documents": [source_document],
            "coverage": coverage,
            "usage": [],
            "extraction": {
                "source_body_sha256": "body-sha",
                "prompt_sha256": "extraction-prompt",
                "fingerprint_sha256": "extraction-fingerprint",
                "generated_at": "2026-09-12T16:48:04+00:00",
            },
        },
    )
    cross_path = root / "cross-section/test.cross-section.json"
    cross_sha = _write(
        cross_path,
        {
            "source_documents": [source_document],
            "usage": [],
            "cross_section_relations": {
                "package_sha256": extraction_sha,
                "prompt_sha256": "cross-prompt",
                "fingerprint_sha256": "cross-fingerprint",
                "evidence_relations_added": 3,
                "claim_relations_added": 2,
            },
        },
    )
    os.utime(
        cross_path,
        (datetime(2026, 9, 12, 16, 54, tzinfo=timezone.utc).timestamp(),) * 2,
    )
    review_path = root / "reviews/test.independent-review.json"
    review_sha = _write(
        review_path,
        {
            "source": {"transcript_id": SOURCE, "package_sha256": cross_sha},
            "reviewer": {"fingerprint_sha256": "review-fingerprint"},
            "routing_summary": {
                "ai_reviewed": 4,
                "awaiting_openai_adjudication": 1,
                "human_spot_check": 0,
            },
            "usage": [],
        },
    )
    os.utime(
        review_path,
        (datetime(2026, 9, 12, 17, 6, tzinfo=timezone.utc).timestamp(),) * 2,
    )
    adjudication_path = root / "adjudications/test.adjudication.json"
    adjudication_sha = _write(
        adjudication_path,
        {
            "source": {
                "transcript_id": SOURCE,
                "package_path": str(cross_path),
                "review_path": str(review_path),
            },
            "adjudicator": {
                "source_package_sha256": cross_sha,
                "review_artifact_sha256": review_sha,
                "fingerprint_sha256": "adjudication-fingerprint",
                "generated_at": "2026-09-12T17:08:53+00:00",
            },
            "summary": {"auto_applied": 1, "withdrawn": 0},
        },
    )
    override_path = root / "overrides/test.overrides.json"
    override_sha = _write(override_path, {"artifact": "override"})
    merged_path = root / "reviewed/test.reviewed-candidate.json"
    merged_sha = _write(merged_path, {"artifact": "merged"})

    manifest = {
        "schema_version": "wang_pipeline_run_reconciliation_v1",
        "reconciliation_id": "RECON-TEST",
        "source_id": SOURCE,
        "triggered_by": "tester",
        "artifact_root": "repair",
        "artifacts": [
            {"stage": "extraction", "path": str(extraction_path.relative_to(root)), "sha256": extraction_sha, "model_id": "gpt-test"},
            {"stage": "cross_section", "path": str(cross_path.relative_to(root)), "sha256": cross_sha, "model_id": "gpt-test"},
            {"stage": "review", "path": str(review_path.relative_to(root)), "sha256": review_sha, "model_id": "claude-test"},
            {"stage": "adjudication", "path": str(adjudication_path.relative_to(root)), "sha256": adjudication_sha, "model_id": "gpt-test"},
        ],
        "adjudication_companion": {
            "path": str(override_path.relative_to(root)),
            "sha256": override_sha,
        },
        "consumer": {
            "merge_run_id": "RUN-MERGE",
            "ingest_run_id": "RUN-INGEST",
            "merged_output": str(merged_path.relative_to(root)),
            "merged_output_sha256": merged_sha,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path, staging


def test_manifest_reconstructs_the_exact_ordered_chain(tmp_path: Path) -> None:
    manifest, staging = _fixture(tmp_path)
    plan = load_reconciliation(manifest, staging)

    assert [row["stage"] for row in plan["rows"]] == [
        "extraction", "cross_section", "review", "adjudication"
    ]
    assert plan["rows"][0]["inputs"]["source_sha256"] == "body-sha"
    assert plan["rows"][1]["quality"] == {
        "evidence_relations_added": 3,
        "claim_relations_added": 2,
    }
    assert plan["rows"][2]["quality"]["ai_reviewed"] == 4
    assert len(plan["rows"][3]["output_paths"]) == 2
    assert plan["rows"][0]["triggered_by"] == "tester"


def test_manifest_rejects_a_changed_artifact_before_database_access(tmp_path: Path) -> None:
    manifest, staging = _fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["artifacts"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RunLedgerReconciliationError, match="artifact SHA mismatch"):
        load_reconciliation(manifest, staging)


def test_manifest_rejects_a_cross_stage_lineage_mismatch(tmp_path: Path) -> None:
    manifest, staging = _fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    review = staging / "repair" / payload["artifacts"][2]["path"]
    review_payload = json.loads(review.read_text(encoding="utf-8"))
    review_payload["source"]["package_sha256"] = "wrong-upstream"
    new_sha = _write(review, review_payload)
    payload["artifacts"][2]["sha256"] = new_sha
    adjudication = staging / "repair" / payload["artifacts"][3]["path"]
    adjudication_payload = json.loads(adjudication.read_text(encoding="utf-8"))
    adjudication_payload["adjudicator"]["review_artifact_sha256"] = new_sha
    payload["artifacts"][3]["sha256"] = _write(adjudication, adjudication_payload)
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RunLedgerReconciliationError, match="review.package_sha256"):
        load_reconciliation(manifest, staging)


def test_run_ids_are_deterministic_and_stage_specific() -> None:
    first = _deterministic_run_id("RECON", "review", "a" * 64)
    assert first == _deterministic_run_id("RECON", "review", "a" * 64)
    assert first != _deterministic_run_id("RECON", "adjudication", "a" * 64)
    assert first.startswith("RUN-") and len(first) == 30
