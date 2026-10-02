import hashlib

import pytest

from backend.pipeline import claim_passage_role_exception_queue as queue
from backend.pipeline import claim_passage_role_runner as base


def fixture():
    statement = "教授解释经文。"
    claim = {
        "claim_id": "CL-1", "claim_revision": 1,
        "claim_content_sha256": "c" * 64,
        "source_id": "SRC-1", "source_content_sha256": "s" * 64,
        "source_file_sha256": "f" * 64,
        "statement": statement, "scripture_refs": ["太 1:1"],
    }
    packet = base._artifact({"claims": [claim]})
    reviewer = {
        "claim_statement_sha256": hashlib.sha256(statement.encode()).hexdigest(),
        "reason": "Cannot identify whether the statement explains the text.",
        "role": "unresolved",
        "interpreted_ref_indices": [], "interpreted_evidence_refs": [],
    }
    ledger = base._artifact({
        "schema_version": "wang_claim_passage_role_ledger_v7",
        "status": "all_eligible_reviewed",
        "packet_sha256": packet["artifact_sha256"],
        "batch_size": 16,
        "review_artifact_shas": {"primary-00001": "a" * 64, "independent-00001": "b" * 64},
        "counts": {"unresolved": 1},
        "decisions": [{
            "claim_id": "CL-1", "role": "unresolved",
            "interpreted_passage_keys": [],
            "decision_basis": "review_disagreement_or_uncertainty",
            "primary": reviewer, "independent": reviewer,
        }],
    })
    return ledger, packet


def test_exact_once_unresolved_queue_keeps_reviewer_reasons():
    ledger, packet = fixture()
    result = queue.build_queue(ledger, packet)
    assert result["exception_count"] == 1
    assert result["reason_counts"] == {"BOTH_REVIEWERS_UNRESOLVED": 1}
    assert result["rows"][0]["primary_reason"] == ledger["decisions"][0]["primary"]["reason"]
    assert result["role_ledger_sha256"] == ledger["artifact_sha256"]
    assert result["rows"][0]["primary_artifact_sha256"] == "a" * 64


def test_missing_decision_fails_closed():
    ledger, packet = fixture()
    ledger = base._artifact({**{k: v for k, v in ledger.items() if k != "artifact_sha256"}, "decisions": []})
    with pytest.raises(ValueError, match="exactly once"):
        queue.build_queue(ledger, packet)


def test_repair_disposition_is_not_auto_classified():
    ledger, packet = fixture()
    body = {key: value for key, value in ledger.items() if key != "artifact_sha256"}
    body["decisions"][0]["decision_basis"] = "analyst_repair_required_under_user_instruction"
    ledger = base._artifact(body)
    result = queue.build_queue(ledger, packet)
    assert result["rows"][0]["reason_code"] == "REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED"
    assert result["rows"][0]["next_step"] == "repair_then_re_review"
