"""What falls when an anchor turns out to quote deleted text."""

from __future__ import annotations

from backend.api.canonical_repository.postgres_store import build_retirement_plan
from backend.pipeline.soft_deleted_anchor_audit import audit, excerpt_is_deleted

SEGMENTS = ["教會建立在信仰上。~~我昨天講過這個。~~所以我們繼續。"]


def test_an_excerpt_inside_a_struck_span_is_deleted() -> None:
    assert excerpt_is_deleted("我昨天講過這個", SEGMENTS, "S0001")


def test_an_excerpt_outside_the_struck_span_is_not() -> None:
    assert not excerpt_is_deleted("教會建立在信仰上", SEGMENTS, "S0001")
    assert not excerpt_is_deleted("所以我們繼續", SEGMENTS, "S0001")


def test_an_anchor_whose_key_no_longer_resolves_is_still_checked() -> None:
    """The question is whether the quoted text was deleted.

    Only 20% of claimed indices in the staged packages still resolve, so a key
    that points nowhere must not read as "not deleted".
    """

    assert excerpt_is_deleted("我昨天講過這個", SEGMENTS, "S0099")


def _audit(**overrides):
    base = dict(
        fragments={
            "FR-live": {"source_id": "SRC-1", "paragraph_key": "S0001",
                        "verbatim_excerpt": "教會建立在信仰上"},
            "FR-dead": {"source_id": "SRC-1", "paragraph_key": "S0001",
                        "verbatim_excerpt": "我昨天講過這個"},
        },
        owners={
            "evidence_steps": {
                "E-gone": {"source_fragment_ids": ["FR-dead"]},
                "E-kept": {"source_fragment_ids": ["FR-dead", "FR-live"]},
            },
        },
        claims={
            "CL-gone": {"evidence_step_ids": ["E-gone"]},
            "CL-kept": {"evidence_step_ids": ["E-gone", "E-kept"]},
        },
        segments_by_source={"SRC-1": SEGMENTS},
        relations={"claim_relations": {"REL-1": {"from_id": "CL-gone", "to_id": "CL-kept"}}},
    )
    base.update(overrides)
    return audit(**base)


def test_a_record_keeping_one_live_anchor_is_weakened_not_retired() -> None:
    """It loses a citation, not its footing."""

    result = _audit()
    assert result.weakened_owners == [("evidence_steps", "E-kept")]
    assert result.orphaned_owners == [("evidence_steps", "E-gone")]


def test_a_claim_keeps_standing_while_one_of_its_steps_does() -> None:
    result = _audit()
    assert result.orphaned_claims == ["CL-gone"]


def test_an_edge_to_a_retired_endpoint_joins_the_closure() -> None:
    """An edge whose endpoint is gone is not a weaker edge, it is an edge to nothing."""

    result = _audit()
    assert ("claim_relations", "REL-1") in result.retirement_closure()


def test_the_closure_is_fragments_then_owners_then_claims_then_edges() -> None:
    assert _audit().retirement_closure() == [
        ("source_fragments", "FR-dead"),
        ("evidence_steps", "E-gone"),
        ("claims", "CL-gone"),
        ("claim_relations", "REL-1"),
    ]


def test_a_fragment_whose_source_cannot_be_read_is_counted_not_judged() -> None:
    """"We could not check these" is a different statement from "these are clean"."""

    result = _audit(segments_by_source={})
    assert result.unresolved_fragments == 2
    assert result.deleted_fragments == {}


# ---------------------------------------------------------------------------
# planning the withdrawal
# ---------------------------------------------------------------------------

EXISTING = {
    ("claims", "CL-1"): {"revision": 3, "content_sha256": "sha-1", "payload": {"claim_id": "CL-1"}},
}


def test_a_retirement_leaves_the_record_saying_what_it_said() -> None:
    """Rewriting the payload to say "retired" would edit the evidence to record
    a decision about it. The withdrawal belongs to the store, not to the text."""

    plan = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="測試", package_id="RETIRE-TEST",
    )
    operation = plan.operations[0]
    assert operation.operation == "retire"
    assert operation.payload == {"claim_id": "CL-1"}
    assert operation.before_sha256 == operation.after_sha256 == "sha-1"
    assert (operation.before_revision, operation.after_revision) == (3, 4)


def test_retiring_what_is_already_gone_is_not_an_error() -> None:
    """Running it twice must not fail, or nobody will dare run it once."""

    plan = build_retirement_plan(
        [("claims", "CL-1"), ("claims", "CL-missing")], EXISTING,
        reason="測試", package_id="RETIRE-TEST",
    )
    assert [item.object_id for item in plan.operations] == ["CL-1"]
    assert plan.unchanged == 1


def test_the_same_retirement_plans_to_the_same_change_set() -> None:
    """The fingerprint is what makes a re-run idempotent rather than additive."""

    first = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="測試", package_id="RETIRE-TEST")
    second = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="測試", package_id="RETIRE-TEST")
    assert first.change_set_id == second.change_set_id
    other = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="別的理由", package_id="RETIRE-TEST")
    assert other.change_set_id != first.change_set_id


# ---------------------------------------------------------------------------
# what the store says when a package would revive something withdrawn
# ---------------------------------------------------------------------------


def test_a_retired_object_is_not_reported_as_a_concurrent_write() -> None:
    """It reaches the conflict check looking exactly like one.

    The planner reads only live rows, so a retired object is planned as a
    `create` with no `before_sha256` while its row is still there with a hash.
    "Concurrent change" would send the reader hunting for another writer.
    """

    from datetime import datetime, timezone

    from backend.api.canonical_repository.postgres_store import conflict_for

    retired = conflict_for(
        "claims", "CL-1", expected=None, found="sha-1",
        retired_at=datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc),
    )
    assert "was retired at 2026-08-20 09:30:00+0000" in str(retired)
    assert "concurrent" not in str(retired).lower()

    concurrent = conflict_for("claims", "CL-1", expected="sha-0", found="sha-1", retired_at=None)
    assert "Concurrent change" in str(concurrent)
