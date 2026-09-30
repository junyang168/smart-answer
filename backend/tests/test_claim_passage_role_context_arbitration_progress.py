"""Progress accounting preserves earlier reviewed holds and exact coverage."""

from __future__ import annotations

import hashlib
import json

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
