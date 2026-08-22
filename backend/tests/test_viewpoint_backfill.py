from __future__ import annotations

import json

import pytest

from backend.api.canonical_repository.viewpoint_backfill import (
    SELECTION_VERSION,
    audit_backfill_readiness,
    authorize_backfill_apply,
    freeze_source_manifest,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    QUALITY_DIMENSIONS,
    ViewpointFoundationValidationError,
    build_resolution_ledger,
    sha256_json,
)
from backend.pipeline import viewpoint_backfill_runner


def _selection(*members: dict) -> dict:
    payload = {
        "schema_version": SELECTION_VERSION,
        "selection_id": "VBS-LATEST-20",
        "selected_by": "operator",
        "members": sorted(members, key=lambda row: row["source_id"]),
    }
    payload["selection_sha256"] = sha256_json(payload)
    return payload


def _source(source_id: str) -> dict:
    return {
        "source_id": source_id,
        "source_type": "manuscript",
        "source_sha256": f"sha-{source_id}",
        "revision": 2,
    }


def test_preflight_keeps_failed_latest_member_but_excludes_its_historical_claims() -> None:
    sources = [_source("SRC-A"), _source("SRC-B")]
    selection = _selection(
        {"source_id": "SRC-A", "latest_extraction_status": "applied", "lineage_ref": "KCS-A"},
        {"source_id": "SRC-B", "latest_extraction_status": "failed", "lineage_ref": "RUN-B"},
    )
    manifest = freeze_source_manifest(selection, sources)
    fragments = [
        {"fragment_id": "FR-A1", "source_id": "SRC-A", "source_sha256": "sha-SRC-A", "verbatim_excerpt": "a"},
        {"fragment_id": "FR-A2", "source_id": "SRC-A", "source_sha256": "sha-SRC-A", "verbatim_excerpt": "b"},
        {"fragment_id": "FR-B", "source_id": "SRC-B", "source_sha256": "sha-SRC-B", "verbatim_excerpt": "old"},
    ]
    evidence = [
        {"evidence_step_id": "EV-A", "source_fragment_ids": ["FR-A1", "FR-A2"]},
        {"evidence_step_id": "EV-B", "source_fragment_id": "FR-B"},
    ]
    claims = [
        {"claim_id": "CL-A", "statement": "current", "claim_type": "explicit", "evidence_step_ids": ["EV-A"]},
        {"claim_id": "CL-B", "statement": "historical", "claim_type": "explicit", "evidence_step_ids": ["EV-B"]},
    ]

    result = audit_backfill_readiness(
        manifest=manifest,
        sources=sources,
        fragments=fragments,
        evidence_steps=evidence,
        claims=claims,
        created_at="2026-08-22T12:00:00+00:00",
    )

    assert result["readiness"]["summary"] == {
        "selected_source_count": 2,
        "resolution_ready_source_count": 1,
        "blocked_source_count": 1,
        "resolution_ready_claim_count": 1,
    }
    assert [row["claim_id"] for row in result["claim_manifest"]["claims"]] == ["CL-A"]
    assert result["preflight_packet"]["apply_allowed"] is False
    assert result["preflight_packet"]["resolution_allowed"] is False
    blocked = next(row for row in result["readiness"]["sources"] if row["source_id"] == "SRC-B")
    assert blocked["blocker_codes"] == ["latest_extraction_not_applied"]


def test_freezer_rejects_directory_order_or_tampered_selection() -> None:
    selection = _selection(
        {"source_id": "SRC-B", "latest_extraction_status": "applied"},
        {"source_id": "SRC-A", "latest_extraction_status": "applied"},
    )
    selection["members"].reverse()
    selection["selection_sha256"] = sha256_json(
        {key: value for key, value in selection.items() if key != "selection_sha256"}
    )
    with pytest.raises(ViewpointFoundationValidationError, match="sorted"):
        freeze_source_manifest(selection, [_source("SRC-A"), _source("SRC-B")])

    valid = _selection({"source_id": "SRC-A", "latest_extraction_status": "applied"})
    valid["members"][0]["source_id"] = "SRC-B"
    with pytest.raises(ViewpointFoundationValidationError, match="SHA mismatch"):
        freeze_source_manifest(valid, [_source("SRC-A"), _source("SRC-B")])


def test_apply_boundary_requires_complete_resolution_and_passing_quality() -> None:
    source = _source("SRC-A")
    manifest = freeze_source_manifest(
        _selection(
            {"source_id": "SRC-A", "latest_extraction_status": "applied", "lineage_ref": "KCS-A"}
        ),
        [source],
    )
    result = audit_backfill_readiness(
        manifest=manifest,
        sources=[source],
        fragments=[
            {"fragment_id": "FR-A", "source_id": "SRC-A", "source_sha256": "sha-SRC-A", "verbatim_excerpt": "a"}
        ],
        evidence_steps=[{"evidence_step_id": "EV-A", "source_fragment_ids": ["FR-A"]}],
        claims=[
            {"claim_id": "CL-A", "statement": "a", "claim_type": "explicit", "evidence_step_ids": ["EV-A"]}
        ],
        created_at="2026-08-22T12:00:00+00:00",
    )
    packet = result["preflight_packet"]
    claim_row = result["claim_manifest"]["claims"][0]
    ledger = build_resolution_ledger(
        result["claim_manifest"],
        [
            {
                **{
                    key: claim_row[key]
                    for key in (
                        "claim_id",
                        "pinned_claim_revision",
                        "claim_revision_sha256",
                    )
                },
                "processing_status": "source_ineligible",
                "source_eligibility_reason_code": "external_position",
            }
        ],
        coverage_snapshot_id=packet["coverage_snapshot_id"],
    ).model_dump(mode="json")
    quality_build = {
        "validator_version": "validator-v1",
        "scope_kind": "identity_decision",
        "scope_ids": ["DEC-A"],
        "coverage_snapshot_id": packet["coverage_snapshot_id"],
        "resolution_ledger_id": ledger["resolution_ledger_id"],
        "input_artifact_sha256s": [ledger["artifact_sha256"]],
        "dimensions": [
            {
                "dimension": dimension,
                "applicable": True,
                "minimum_policy": "test",
                "observed": {},
                "status": "pass",
                "evidence_artifact_sha256s": [],
                "reason_not_applicable": None,
            }
            for dimension in QUALITY_DIMENSIONS
        ],
        "hard_failures": [],
    }
    quality_fingerprint = sha256_json(quality_build)
    quality = {
        "quality_report_id": f"VQR-{quality_fingerprint[:20]}",
        **quality_build,
        "eligibility_decision": "pass",
        "build_fingerprint_sha256": quality_fingerprint,
    }
    quality["artifact_sha256"] = sha256_json(quality)

    authorization = authorize_backfill_apply(
        preflight_packet=packet, resolution_ledger=ledger, quality_report=quality
    )

    assert authorization["apply_allowed"] is True
    with pytest.raises(ViewpointFoundationValidationError, match="incomplete"):
        authorize_backfill_apply(
            preflight_packet=packet,
            resolution_ledger={**ledger, "coverage_status": "partial"},
            quality_report=quality,
        )


def test_runner_uses_change_set_claim_denominator_and_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    source = _source("SRC-A")
    selection = _selection(
        {
            "source_id": "SRC-A",
            "latest_extraction_status": "applied",
            "lineage_ref": "KCS-A",
        }
    )
    selection["selected_at"] = "2026-08-22T12:00:00+00:00"
    selection["selection_sha256"] = sha256_json(
        {key: value for key, value in selection.items() if key != "selection_sha256"}
    )
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selection), encoding="utf-8")

    class FakeStore:
        def __init__(self, database_url=None):
            pass

        def list_records(self, collection):
            return {
                "source_documents": [source],
                "source_fragments": [
                    {
                        "fragment_id": "FR-A",
                        "source_id": "SRC-A",
                        "source_sha256": "sha-SRC-A",
                        "verbatim_excerpt": "a",
                    }
                ],
                "evidence_steps": [
                    {"evidence_step_id": "EV-A", "source_fragment_ids": ["FR-A"]}
                ],
                "claims": [
                    {
                        "claim_id": "CL-CURRENT",
                        "statement": "current",
                        "claim_type": "explicit",
                        "evidence_step_ids": ["EV-A"],
                    },
                    {
                        "claim_id": "CL-HISTORICAL",
                        "statement": "historical",
                        "claim_type": "explicit",
                        "evidence_step_ids": ["EV-A"],
                    },
                ],
                "claim_relations": [],
                "claim_relation_constraints": [],
                "viewpoint_claim_links": [],
            }[collection]

        def list_change_set_object_ids(self, change_set_ids, collection):
            assert change_set_ids == ["KCS-A"]
            assert collection == "claims"
            return ["CL-CURRENT"]

    monkeypatch.setattr(viewpoint_backfill_runner, "PostgresKnowledgeStore", FakeStore)
    output_dir = tmp_path / "output"

    first = viewpoint_backfill_runner.run_preflight(
        selection_path=selection_path, output_dir=output_dir
    )
    first_queue = json.loads((output_dir / "resolution-queue.json").read_text())
    first_schedule = json.loads(
        (output_dir / "semantic-bundle-schedule.json").read_text()
    )
    second = viewpoint_backfill_runner.run_preflight(
        selection_path=selection_path, output_dir=output_dir
    )

    assert first == second
    assert first["resolution_ready_claim_count"] == 1
    assert first_queue["claim_count"] == 1
    assert first_queue["identity_candidate_count"] == 1
    assert first["semantic_bundle_count"] == 1
    reuse_key = first_schedule["work_items"][0]["reuse_key_sha256"]
    completed_path = tmp_path / "completed-results.json"
    completed = {
        "schema_version": "wang_viewpoint_semantic_completed_results_v1",
        "results": [
            {
                "reuse_key_sha256": reuse_key,
                "result_artifact_sha256": "result-sha",
                "status": "complete",
            }
        ],
    }
    completed["artifact_sha256"] = sha256_json(completed)
    completed_path.write_text(json.dumps(completed), encoding="utf-8")
    reused = viewpoint_backfill_runner.run_preflight(
        selection_path=selection_path,
        output_dir=output_dir,
        completed_results_path=completed_path,
    )
    assert reused["semantic_bundle_count"] == 0
    assert reused["semantic_reused_count"] == 1
    assert not list(output_dir.glob("*.tmp"))
