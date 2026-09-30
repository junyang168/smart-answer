"""Triage creates reversible passage candidates, never ledger overrides."""

from __future__ import annotations

import hashlib

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_passage_identity_triage import (
    build_triage,
    strict_normalized_ref,
    strict_normalized_keys,
)


def _inputs() -> tuple[dict, dict]:
    specifications = [
        ("C1", ["Romans 2:13"], "disputed", [], [0], [0], [], ["Romans 6:16"]),
        ("C2", ["1 Thessalonians 5:2,8-9"], "disputed", [], [0], [0], [], ["1 Thessalonians 5:2"]),
        ("C3", ["Matt 16:19", "Matt 18:18"], "disputed", [], [0], [1], [], []),
        ("C4", ["約壹2:2"], "agreed", ["raw:約壹2:2"], [0], [0], [], []),
        ("C5", ["啟示錄"], "agreed", ["raw:啟示錄"], [0], [0], [], []),
        ("C6", ["Matt 16:19", "Matt 18:18"], "disputed", [], [0, 1], [0], [], []),
    ]
    claims, decisions = [], []
    for claim_id, refs, status, keys, primary_indices, independent_indices, primary_evidence, independent_evidence in specifications:
        statement = f"Claim {claim_id}"
        statement_sha = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        claims.append({
            "claim_id": claim_id, "claim_content_sha256": f"sha-{claim_id}",
            "source_id": "S1", "statement": statement, "scripture_refs": refs,
        })
        decisions.append({
            "claim_id": claim_id,
            "role": "passage_exegesis",
            "passage_identity_status": status,
            "interpreted_passage_keys": keys,
            "primary": {
                "claim_statement_sha256": statement_sha,
                "interpreted_ref_indices": primary_indices,
                "interpreted_evidence_refs": primary_evidence,
            },
            "independent": {
                "claim_statement_sha256": statement_sha,
                "interpreted_ref_indices": independent_indices,
                "interpreted_evidence_refs": independent_evidence,
            },
        })
    packet = {"claims": claims}
    packet["artifact_sha256"] = sha256_json(packet)
    ledger = {
        "status": "all_eligible_reviewed", "packet_sha256": packet["artifact_sha256"],
        "decisions": decisions,
    }
    ledger["artifact_sha256"] = sha256_json(ledger)
    return ledger, packet


def test_strict_normalization_rejects_prefix_match():
    assert strict_normalized_ref("Romans 2:13") == "Rom.2.13"
    assert strict_normalized_ref("1 Thessalonians 5:2,8-9") is None
    assert strict_normalized_keys("帖撒羅尼迦前書 5:2,8-9") == [
        "1Thess.5.2", "1Thess.5.8-1Thess.5.9"
    ]
    assert strict_normalized_keys("哥林多前書 1-15") == ["1Cor.1-1Cor.15"]
    assert strict_normalized_keys("但以理書7章") == ["Dan.7"]


def test_triage_separates_primary_agreement_from_evidence_dispute():
    ledger, packet = _inputs()
    result = build_triage(ledger=ledger, packet=packet)
    by_id = {row["claim_id"]: row for row in result["rows"]}
    assert result["held_denominator"] == 6
    assert by_id["C1"]["status"] == "provisional_primary_passage_candidate"
    assert by_id["C1"]["candidate_passage_keys"] == ["Rom.2.13"]
    assert by_id["C1"]["evidence_reference_dispute_retained"] is True
    assert by_id["C2"]["reason_code"] == "AGREED_REFERENCE_NOT_FULLY_NORMALIZED"
    assert by_id["C3"]["status"] == "needs_independent_adjudication"
    assert by_id["C4"]["candidate_passage_keys"] == ["1John.2.2"]
    assert by_id["C5"]["reason_code"] == "RAW_LOCATOR_HAS_NO_EXPLICIT_COORDINATES"
    assert by_id["C6"]["status"] == "provisional_shared_passage_candidate"
    assert by_id["C6"]["candidate_passage_keys"] == ["Matt.16.19"]
    assert by_id["C6"]["additional_reference_dispute_retained"] is True
    assert result["model_calls_executed"] == result["master_data_mutations"] == 0
