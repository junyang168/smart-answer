from copy import deepcopy

import pytest

from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claim_passage_role_context_overlay import assemble


def fixture():
    binding = {"source_id": "S1", "claim_content_sha256": "c" * 64,
               "source_content_sha256": "s" * 64}
    ledger = base._artifact({"packet_sha256": "p" * 64, "counts": {},
                            "decisions": [{"claim_id": cid, "role": "unresolved",
                                           "interpreted_passage_keys": [],
                                           "passage_identity_status": "unresolved"}
                                          for cid in ("C1", "C2")]})
    queue = base._artifact({"role_ledger_sha256": ledger["artifact_sha256"],
                           "role_packet_sha256": ledger["packet_sha256"],
                           "rows": [{"claim_id": cid, **binding} for cid in ("C1", "C2")]})
    report = base._artifact({"queue_sha256": queue["artifact_sha256"],
                            "audit_sha256": "a" * 64, "remaining_claims": 0,
                            "completed_claims": 2, "decisions": [
        {"claim_id": "C1", **binding, "role": "passage_exegesis",
         "disposition": "resolved", "candidate_reference": "太16:19", "reason": "source"},
        {"claim_id": "C2", **binding, "role": "unresolved",
         "disposition": "repair_required", "candidate_reference": "太1:1", "reason": "repair"}]})
    return ledger, queue, report


def rehash(value):
    return base._artifact({k: v for k, v in value.items() if k != "artifact_sha256"})


def test_preserves_base_holds_and_candidate_is_not_passage_identity():
    ledger, queue, report = fixture()
    before = deepcopy(ledger)
    updated, held = assemble(ledger, queue, report)
    assert ledger == before
    assert updated["counts"] == {"passage_exegesis": 1, "unresolved": 1}
    assert updated["decisions"][0]["interpreted_passage_keys"] == []
    assert updated["decisions"][0]["passage_identity_status"] == "pending_context_reference_verification"
    assert held["rows"][0]["arbitration"]["reason"] == "repair"
    assert held["rows"][0]["candidate_reference_is_confirmed"] is False
    base._check_artifact(updated)
    base._check_artifact(held)


@pytest.mark.parametrize("error", ["gap", "duplicate", "foreign", "source", "resolved_base", "sha"])
def test_rejects_changed_scope(error):
    ledger, queue, report = fixture()
    if error == "gap":
        report["remaining_claims"] = 1
    elif error == "duplicate":
        report["decisions"].append(report["decisions"][0])
    elif error == "foreign":
        report["decisions"][0]["claim_id"] = "foreign"
    elif error == "source":
        report["decisions"][0]["source_id"] = "different"
    elif error == "resolved_base":
        ledger["decisions"][0]["role"] = "other"
        ledger = rehash(ledger)
        queue["role_ledger_sha256"] = ledger["artifact_sha256"]
        queue = rehash(queue)
        report["queue_sha256"] = queue["artifact_sha256"]
    elif error == "sha":
        report["artifact_sha256"] = "bad"
    if error != "sha":
        report = rehash(report)
    with pytest.raises(ValueError):
        assemble(ledger, queue, report)


def test_does_not_promote_prior_human_hold():
    ledger, queue, report = fixture()
    queue["rows"][0]["reason_code"] = "REVIEWED_HUMAN_DECISION_REQUIRED"
    queue = rehash(queue)
    report["queue_sha256"] = queue["artifact_sha256"]
    with pytest.raises(ValueError, match="reviewed hold"):
        assemble(ledger, queue, rehash(report))


def test_other_existing_rows_are_byte_for_byte_unchanged():
    ledger, queue, report = fixture()
    untouched = {"claim_id": "C3", "role": "passage_exegesis",
                 "interpreted_passage_keys": ["Matt.16.19"],
                 "passage_identity_status": "agreed", "primary": {"reason": "original"}}
    ledger["decisions"].append(untouched)
    ledger = rehash(ledger)
    queue["role_ledger_sha256"] = ledger["artifact_sha256"]
    queue = rehash(queue)
    report["queue_sha256"] = queue["artifact_sha256"]
    updated, _ = assemble(ledger, queue, rehash(report))
    assert updated["decisions"][2] == untouched
    assert assemble(ledger, queue, rehash(report))[0] == updated
