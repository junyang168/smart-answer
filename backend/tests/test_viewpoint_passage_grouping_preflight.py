"""The passage preflight never turns a reading bucket into a CVP judgment."""

from __future__ import annotations

import hashlib

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_passage_grouping_preflight import (
    build_preview,
    passages_overlap,
    passage_sort_key,
)


def _inputs(
    keys_by_claim: list[list[str]], *, disputed: set[int] | None = None
) -> tuple[dict, dict]:
    disputed = disputed or set()
    claims = []
    decisions = []
    for index, keys in enumerate(keys_by_claim):
        claim_id = f"C{index:03}"
        statement = f"来源 Claim {index}"
        statement_sha = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        claims.append({"claim_id": claim_id, "statement": statement})
        reviewer = {"claim_statement_sha256": statement_sha}
        decisions.append(
            {
                "claim_id": claim_id,
                "role": "passage_exegesis",
                "passage_identity_status": "disputed" if index in disputed else "agreed",
                "interpreted_passage_keys": keys,
                "primary": reviewer,
                "independent": reviewer,
            }
        )
    packet = {"claims": claims}
    packet["artifact_sha256"] = sha256_json(packet)
    ledger = {
        "status": "all_eligible_reviewed",
        "packet_sha256": packet["artifact_sha256"],
        "decisions": decisions,
    }
    ledger["artifact_sha256"] = sha256_json(ledger)
    return ledger, packet


def test_at_most_twenty_is_one_deterministic_passage_group():
    ledger, packet = _inputs([["Matt.16.19"]] * 20)
    result = build_preview(ledger=ledger, packet=packet, batch_size=20)
    assert result["deterministic_passage_count"] == 1
    assert result["model_split_passage_count"] == 0
    assert result["passages"][0]["claim_count"] == 20
    assert result["passages"][0]["grouping_action"] == "one_deterministic_group"
    assert result["model_calls_executed"] == 0


def test_over_twenty_requires_model_split_without_mechanical_chunking():
    ledger, packet = _inputs([["Matt.16.19"]] * 21)
    result = build_preview(ledger=ledger, packet=packet, batch_size=20)
    assert result["model_split_passage_count"] == 1
    assert result["passages"][0]["claim_count"] == 21
    assert result["passages"][0]["grouping_action"] == "model_split_required"
    assert len(result["passages"][0]["claims"]) == 21


def test_scripture_order_and_multikey_claim_requires_passage_unit():
    ledger, packet = _inputs(
        [["Matt.16.19", "Matt.18.18"], ["Gen.1.1"], ["John.20.23", "Matt.16.19"]]
    )
    result = build_preview(ledger=ledger, packet=packet, batch_size=20)
    assert [row["passage_key"] for row in result["passages"]] == ["Gen.1.1", "Matt.16.19"]
    assert result["passages"][1]["claim_count"] == 2
    assert result["passages"][1]["claims"][0]["other_interpreted_passage_keys"] == ["Matt.18.18"]
    assert result["owned_claim_count"] == 3
    assert result["passages"][1]["grouping_action"] == "passage_unit_required"


def test_overlapping_range_and_verse_cannot_be_finalized_as_separate_groups():
    ledger, packet = _inputs([["Matt.16.18-Matt.16.23"], ["Matt.16.19"]])
    result = build_preview(ledger=ledger, packet=packet, batch_size=20)
    assert result["passage_unit_required_count"] == 2
    assert result["deterministic_passage_count"] == 0
    assert result["passages"][0]["overlapping_bucket_keys"] == ["Matt.16.19"]
    assert passages_overlap("Matt.16.18-Matt.16.23", "Matt.16.19")
    assert not passages_overlap("Matt.16.19", "Matt.16.24")


def test_disputed_and_raw_locators_are_held_not_guessed():
    ledger, packet = _inputs([["Matt.16.19"], ["raw:啟示錄"], []], disputed={2})
    result = build_preview(ledger=ledger, packet=packet, batch_size=20)
    assert result["owned_claim_count"] == 1
    assert result["held_claims"] == [
        {"claim_id": "C001", "reason": "passage_locator_not_normalized"},
        {"claim_id": "C002", "reason": "passage_identity_disputed"},
    ]


def test_sha_binding_fails_closed():
    ledger, packet = _inputs([["Matt.16.19"]])
    packet["claims"][0]["statement"] = "changed"
    with pytest.raises(ValueError, match="artifact SHA mismatch"):
        build_preview(ledger=ledger, packet=packet, batch_size=20)


def test_bible_order_is_not_lexicographic():
    assert passage_sort_key("Gen.1.1") < passage_sort_key("Matt.16.19")
    assert passage_sort_key("Matt.16.9") < passage_sort_key("Matt.16.19")
