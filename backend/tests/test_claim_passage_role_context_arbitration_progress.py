"""Progress accounting preserves earlier reviewed holds and exact coverage."""

from __future__ import annotations

import hashlib
import json

import pytest

from backend.pipeline import claim_passage_role_context_arbitration_progress as progress
from backend.pipeline import claim_passage_role_context_arbitration_round as round1
from backend.pipeline import claim_passage_role_runner as base


def test_prior_repair_hold_is_masked_not_promoted(tmp_path) -> None:
    row = {"claim_id": "CL-1", "source_id": "SRC-1",
           "claim_content_sha256": "c" * 64, "source_content_sha256": "s" * 64,
           "reason_code": "REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED",
           "source_match": "exact_file", "statement": "test",
           "claim_scripture_refs": [], "evidence_steps": [], "anchor_indices": [0],
           "context": [{"paragraph_key": "S0001", "text": "the source says test"}]}
    prior = {"claim_id": "CL-1", "primary_role": "unresolved",
             "primary_reason": "repair first", "independent_role": "unresolved",
             "independent_reason": "repair first",
             "reason_code": "REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED"}
    queue = base._artifact({"rows": [prior]})
    audit = base._artifact({"schema_version": "wang_claim_role_source_context_audit_v1",
                            "queue_sha256": queue["artifact_sha256"],
                            "claim_count": 1, "rows": [row]})
    payload = json.dumps({"audit_sha256": audit["artifact_sha256"],
                          "queue_sha256": queue["artifact_sha256"],
                          "claims": [round1.input_row(row, prior)]},
                         ensure_ascii=False, separators=(",", ":"))
    answer = {"decisions": {"CL-1": {"role": "passage_exegesis",
                                       "disposition": "resolved",
                                       "candidate_reference": "太 1:1",
                                       "source_key": "S0001",
                                       "source_quote": "the source says test",
                                       "reason": "model proposed promotion"}}}
    artifact = base._artifact({
        "schema_version": "wang_claim_role_context_arbitration_round_v1",
        "audit_sha256": audit["artifact_sha256"],
        "queue_sha256": queue["artifact_sha256"],
        "model": round1.MODEL, "claim_ids": ["CL-1"],
        "prompt_sha256": "a" * 64,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "schema_sha256": base.sha256_json(round1.schema(["CL-1"])["schema"]),
        "response": answer,
    })
    base._write_immutable(tmp_path / "batch-test.json", artifact)
    report = progress.progress(audit, queue, tmp_path)
    assert report["completed_claims"] == 1
    assert report["masked_prior_hold_claim_ids"] == ["CL-1"]
    assert report["decisions"][0]["role"] == "unresolved"
    assert report["decisions"][0]["disposition"] == "repair_required"
    assert report["decisions"][0]["prior_hold_masked"] is True

    retry = base._artifact({key: value for key, value in artifact.items()
                            if key != "artifact_sha256"} | {
                                "attempt_number": 2,
                                "retry_of_artifact_sha256": artifact["artifact_sha256"],
                            })
    base._write_immutable(tmp_path / "batch-test.retry-2.json", retry)
    with_retry = progress.progress(audit, queue, tmp_path)
    assert with_retry["completed_claims"] == 1
    assert with_retry["decisions"][0]["arbitration_artifact_sha256"] == retry["artifact_sha256"]

    compliant_root = tmp_path / "compliant"
    compliant_root.mkdir()
    compliant_answer = {"decisions": {"CL-1": {
        "role": "unresolved", "disposition": "repair_required",
        "candidate_reference": "太 1:1", "source_key": "S0001",
        "source_quote": "the source says test",
        "reason": "source repair is still required; reference is only a lead",
    }}}
    compliant = base._artifact({key: value for key, value in artifact.items()
                                if key != "artifact_sha256"} | {"response": compliant_answer})
    base._write_immutable(compliant_root / "batch-test.json", compliant)
    preserved = progress.progress(audit, queue, compliant_root)
    assert preserved["masked_prior_hold_claim_ids"] == []
    assert preserved["unresolved_candidate_reference_claim_ids"] == ["CL-1"]
    assert preserved["decisions"][0]["reason"] == compliant_answer["decisions"]["CL-1"]["reason"]


def test_model_transition_joins_disjoint_roots_and_rejects_overlap(tmp_path) -> None:
    rows = [{"claim_id": cid, "source_id": "SRC-1", "statement": "test",
             "claim_content_sha256": "c" * 64, "source_content_sha256": "s" * 64,
             "source_match": "exact_file", "reason_code": "BOTH_REVIEWERS_UNRESOLVED",
             "claim_scripture_refs": [], "evidence_steps": [], "anchor_indices": [0],
             "context": [{"paragraph_key": "S0001", "text": "the source says test"}]}
            for cid in ("CL-1", "CL-2")]
    queue = base._artifact({"rows": [{"claim_id": row["claim_id"],
        "primary_role": "unresolved", "independent_role": "unresolved",
        "primary_reason": "context missing", "independent_reason": "context missing",
        "reason_code": row["reason_code"]} for row in rows]})
    audit = base._artifact({"queue_sha256": queue["artifact_sha256"],
                            "claim_count": 2, "rows": rows})

    class Fake:
        def generate_json(self, _prompt, payload, _schema):
            return {"decisions": {row["claim_id"]: {
                "role": "other", "disposition": "resolved", "candidate_reference": "",
                "source_key": "S0001", "source_quote": "the source says test",
                "reason": "general statement"} for row in json.loads(payload)["claims"]}}

    old_root, new_root = tmp_path / "old", tmp_path / "new"
    for row, root, model in zip(rows, (old_root, new_root), round1.SUPPORTED_MODELS):
        round1.run_batch(audit=audit, queue=queue, rows=[row], root=root,
                         client=Fake(), retry_invalid_once=False, model=model)
    joined = progress.progress(audit, queue, old_root, (new_root,))
    assert joined["completed_claims"] == 2
    assert joined["model_claim_counts"] == {"gpt-6-sol": 1, "gpt-6.1-sol": 1}
    with pytest.raises(ValueError, match="roots repeat"):
        progress.progress(audit, queue, old_root, (old_root,))
    round1.run_batch(audit=audit, queue=queue, rows=[rows[0]], root=new_root,
                     client=Fake(), retry_invalid_once=False)
    with pytest.raises(ValueError, match="conflicting valid batches"):
        progress.progress(audit, queue, old_root, (new_root,))
