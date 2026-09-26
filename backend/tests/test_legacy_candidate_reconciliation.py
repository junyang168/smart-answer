from __future__ import annotations

from backend.api.canonical_repository.postgres_store import (
    _substantive_payload,
    record_content_sha,
    validate_change_set_plan_integrity,
)
from backend.pipeline.legacy_candidate_reconciliation import (
    _decode_plan,
    classify_candidate,
    plan_review_migration,
)
from dataclasses import asdict

import pytest


def _snapshot():
    payload = {
        "claim_id": "CL-1", "statement": "教授原有论点", "review_status": "candidate",
        "evidence_step_ids": ["E-1"], "revision": 3,
    }
    return {
        "object_id": "CL-1", "revision": 3,
        "content_sha256": record_content_sha(payload), "payload": payload,
    }


def _ready_row():
    current = _snapshot()
    return {
        "claim_id": "CL-1", "revision": 3,
        "content_sha256": current["content_sha256"],
        "reason": "pass_ready_for_legacy_migration",
        "review_decision": "pass",
        "adjudication_status": "not_required",
        "target_review_status": "ai_consensus_reviewed",
        "source_id": "SRC-1", "source_revision": 2,
        "source_content_sha256": "a" * 64,
        "reviewer_id": "legacy-review-model",
        "adjudicated_at": "2026-09-01T00:00:00+00:00",
        "package_sha256": "b" * 64,
        "review_sha256": "c" * 64,
        "review_fingerprint": "d" * 64,
        "adjudication_sha256": "e" * 64,
        "reviewed_candidate_sha256": "f" * 64,
    }


def test_pass_migration_is_status_only_with_atomic_review_event():
    current = _snapshot()
    first = plan_review_migration([_ready_row()], {"CL-1": current}, freeze_sha256="1" * 64)
    second = plan_review_migration([_ready_row()], {"CL-1": current}, freeze_sha256="1" * 64)
    assert first == second
    validate_change_set_plan_integrity(first)
    assert _decode_plan(asdict(first)) == first
    operation = first.operations[0]
    event = first.review_events[0]
    assert _substantive_payload(operation.payload) == _substantive_payload(current["payload"])
    assert operation.before_revision == 3
    assert operation.after_revision == 4
    assert operation.payload["review_status"] == "ai_consensus_reviewed"
    assert event.object_revision == 4
    assert event.decision == "ai_consensus_reviewed"
    assert event.artifact["legacy_unsealed"] is True


def test_human_required_enters_only_as_human_review_queue():
    row = {
        **_ready_row(), "reason": "human_spot_check_required",
        "adjudication_status": "human_spot_check",
        "target_review_status": "human_review_required",
    }
    plan = plan_review_migration([row], {"CL-1": _snapshot()}, freeze_sha256="1" * 64)
    assert plan.operations[0].payload["review_status"] == "human_review_required"
    assert plan.review_events[0].decision == "human_review_required"


def test_unverified_outcome_cannot_enter_plan():
    row = {**_ready_row(), "reason": "legacy_adjudicated_claim_needs_patch_replay"}
    with pytest.raises(ValueError, match="not a verified status"):
        plan_review_migration([row], {"CL-1": _snapshot()}, freeze_sha256="1" * 64)


def test_stale_claim_cannot_enter_pass_plan():
    current = _snapshot()
    current["revision"] += 1
    with pytest.raises(ValueError, match="changed since preflight"):
        plan_review_migration([_ready_row()], {"CL-1": current}, freeze_sha256="1" * 64)


def test_classification_rejects_substantive_drift_before_review_status():
    current = _snapshot()
    source = {
        "SRC-1": {
            "revision": 2, "content_sha256": "a" * 64,
            "payload": {"source_body_sha256": "b" * 64,
                        "source_file_sha256": "c" * 64},
        }
    }
    bundle = {"reason": None, "claims": {
        "CL-1": {**current["payload"], "statement": "不同论点"}
    }}
    result = classify_candidate(current, [bundle], source)
    assert result["reason"] == "claim_payload_changed"
