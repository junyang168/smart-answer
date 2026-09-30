"""Fail-closed, no-model checks for Claim-level exegesis role review."""

import hashlib

import pytest

from backend.pipeline.claim_passage_role_runner import (
    RESPONSE_VERSION, _claim_matches_pin, reconcile, response_schema,
    select_pilot, validate_cached_decisions, validate_response,
)


ROWS = [
    {
        "claim_id": "CL-1", "statement": "這裡的磐石是彼得的認信。",
        "scripture_refs": ["Matthew 16:18"],
        "evidence_steps": [{"scripture_refs": ["Ephesians 2:20"],
                            "fragments": [{"verbatim_excerpt": "這裡的磐石是彼得的認信。"}]}],
    },
    {
        "claim_id": "CL-2", "statement": "要彼此相愛。",
        "scripture_refs": [],
        "evidence_steps": [{"scripture_refs": ["John 13:34"],
                            "fragments": [{"verbatim_excerpt": "主說要彼此相愛。"}]}],
    },
]


def response(decisions):
    return {"schema_version": RESPONSE_VERSION, "decisions": {
        row["claim_id"]: {key: value for key, value in row.items() if key != "claim_id"}
        for row in decisions
    }}


def decision(claim_id, role, indices, evidence_refs, quote):
    del quote  # Text is SHA-bound in the packet; the model does not recopy it.
    return {
        "claim_id": claim_id, "role": role,
        "interpreted_ref_indices": indices,
        "interpreted_evidence_refs": evidence_refs,
        "reason": "直接判斷這段話的意思。",
    }


def test_direct_exegesis_and_other_are_distinct():
    rows = validate_response(response([
        decision("CL-2", "other", [], [], "要彼此相愛"),
        decision("CL-1", "passage_exegesis", [0], [], "磐石是彼得的認信"),
    ]), ROWS)
    assert [row["claim_id"] for row in rows] == ["CL-1", "CL-2"]
    assert [row["role"] for row in rows] == ["passage_exegesis", "other"]
    assert rows[0]["claim_statement_sha256"] == hashlib.sha256(
        ROWS[0]["statement"].encode("utf-8")
    ).hexdigest()


def test_evidence_reference_can_bind_missing_claim_reference():
    rows = validate_response(response([
        decision("CL-1", "other", [], [], "磐石是彼得的認信"),
        decision("CL-2", "passage_exegesis", [], ["John 13:34"], "主說要彼此相愛"),
    ]), ROWS)
    assert rows[1]["interpreted_evidence_refs"] == ["John 13:34"]


@pytest.mark.parametrize("bad", [
    decision("CL-1", "passage_exegesis", [], [], "磐石是彼得的認信"),
    decision("CL-1", "other", [0], [], "磐石是彼得的認信"),
    decision("CL-1", "passage_exegesis", [1], [], "磐石是彼得的認信"),
    decision("CL-1", "passage_exegesis", [], ["John 1:1"], "磐石是彼得的認信"),
])
def test_invalid_decision_fails_closed(bad):
    with pytest.raises(ValueError):
        validate_response(response([
            bad, decision("CL-2", "other", [], [], "要彼此相愛"),
        ]), ROWS)


def test_missing_or_foreign_claim_fails_closed():
    with pytest.raises(ValueError):
        validate_response(response([decision("CL-1", "other", [], [], "磐石")]), ROWS)
    with pytest.raises(ValueError):
        validate_response(response([
            decision("CL-1", "other", [], [], "磐石"),
            decision("CL-3", "other", [], [], "磐石"),
        ]), ROWS)


def test_schema_requires_exact_claim_keys_and_cache_rejects_duplicates():
    schema = response_schema(["CL-1", "CL-2"])["schema"]["properties"]["decisions"]
    assert schema["required"] == ["CL-1", "CL-2"]
    assert schema["additionalProperties"] is False
    assert "evidence_quote" not in schema["properties"]["CL-1"]["properties"]
    with pytest.raises(ValueError):
        response_schema(["CL-1", "CL-1"])
    with pytest.raises(ValueError):
        validate_cached_decisions([
            decision("CL-1", "other", [], [], "磐石"),
            decision("CL-1", "other", [], [], "磐石"),
        ], ROWS)


def test_review_disagreement_stays_unresolved():
    a = [decision("CL-1", "passage_exegesis", [0], [], "磐石是彼得的認信")]
    b = [decision("CL-1", "other", [], [], "磐石是彼得的認信")]
    assert reconcile(a, b, ROWS[:1])[0]["role"] == "unresolved"


def test_passage_identity_disagreement_does_not_erase_role_consensus():
    row = dict(ROWS[0])
    row["scripture_refs"] = ["Matthew 16:18", "Matthew 16:19"]
    a = [decision("CL-1", "passage_exegesis", [0], [], "磐石是彼得的認信")]
    b = [decision("CL-1", "passage_exegesis", [1], [], "磐石是彼得的認信")]
    result = reconcile(a, b, [row])[0]
    assert result["role"] == "passage_exegesis"
    assert result["passage_identity_status"] == "disputed"
    assert result["interpreted_passage_keys"] == []


def test_claim_and_evidence_duplicate_passage_are_one_consensus():
    row = dict(ROWS[0])
    row["evidence_steps"] = [{"scripture_refs": ["太16:18"], "fragments": []}]
    a = [decision("CL-1", "passage_exegesis", [0], ["太16:18"], "磐石是彼得的認信")]
    b = [decision("CL-1", "passage_exegesis", [0], [], "磐石是彼得的認信")]
    result = reconcile(a, b, [row])[0]
    assert result["role"] == "passage_exegesis"
    assert result["interpreted_passage_keys"] == ["Matt.16.18"]


def test_pilot_selection_is_deterministic_and_covers_both_reference_cases():
    rows = [{"claim_id": f"CL-{n}", "scripture_refs": ["Matt 1:1"] if n % 2 else []}
            for n in range(20)]
    selected = select_pilot(rows, 8)
    assert selected == select_pilot(list(reversed(rows)), 8)
    assert len(selected) == len({row["claim_id"] for row in selected}) == 8
    assert sum(bool(row["scripture_refs"]) for row in selected) == 4
    later = select_pilot(rows, 8, offset=8)
    assert {row["claim_id"] for row in later}.isdisjoint(
        {row["claim_id"] for row in selected}
    )


def test_claim_pin_allows_only_reference_order_changes():
    claim = {"revision": 2, "content_sha256": "abc", "payload": {
        "statement": "statement", "scripture_refs": ["Gal 5:7", "Gal 5:10"],
        "review_status": "ai_consensus_reviewed",
    }}
    pin = {"pinned_claim_revision": 2, "claim_revision_sha256": "abc",
           "statement": "statement", "scripture_refs": ["Gal 5:10", "Gal 5:7"]}
    assert _claim_matches_pin(claim, pin)
    assert not _claim_matches_pin(claim, pin | {"scripture_refs": ["Gal 5:7", "Gal 5:7"]})
    assert not _claim_matches_pin(claim, pin | {"claim_revision_sha256": "stale"})
