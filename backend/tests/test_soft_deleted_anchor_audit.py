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
    assert ("claim_relations", "REL-1") in result.closure()


def test_the_closure_is_fragments_then_owners_then_claims_then_edges() -> None:
    assert _audit().closure() == [
        ("source_fragments", "FR-dead"),
        ("evidence_steps", "E-gone"),
        ("claims", "CL-gone"),
        ("claim_relations", "REL-1"),
    ]


def test_a_fragment_whose_source_cannot_be_read_is_counted_not_judged() -> None:
    """"We could not check these" is a different statement from "these are clean"."""

    result = _audit(segments_by_source={})
    assert result.unresolved_fragments == 2
    assert result.withdrawn_fragments == {}


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


# ---------------------------------------------------------------------------
# what a re-extraction replaces
# ---------------------------------------------------------------------------

from backend.pipeline.extraction_supersede import package_source_ids, superseded  # noqa: E402


def _package():
    return {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "PKG-NEW",
        "source_documents": [
            {"source_id": "SRC-1", "source_type": "sermon_transcript", "title": "讲道"}
        ],
        "source_fragments": [
            {"fragment_id": "FR-new", "source_id": "SRC-1", "verbatim_excerpt": "新的原话"}
        ],
    }


LIVE = {
    "FR-old": {"source_id": "SRC-1"},
    "FR-kept": {"source_id": "SRC-1"},
    "FR-elsewhere": {"source_id": "SRC-2"},
}


def test_a_re_extraction_supersedes_only_its_own_sources() -> None:
    """A package that carries no document for a source is not claiming to
    replace that source's records."""

    result = superseded(_package(), live_fragments=LIVE, owners={}, claims={})
    assert set(result.withdrawn_fragments) == {"FR-old", "FR-kept"}
    assert "FR-elsewhere" not in result.withdrawn_fragments


def test_a_fragment_the_new_extraction_reproduces_is_an_update_not_a_casualty() -> None:
    package = _package()
    package["source_fragments"].append({"fragment_id": "FR-kept", "source_id": "SRC-1"})
    result = superseded(package, live_fragments=LIVE, owners={}, claims={})
    assert set(result.withdrawn_fragments) == {"FR-old"}


def test_the_sources_come_from_the_documents_not_the_fragments() -> None:
    assert package_source_ids(_package()) == {"SRC-1"}
    assert package_source_ids({"source_fragments": [{"source_id": "SRC-9"}]}) == set()


def test_arrival_and_withdrawal_plan_as_one_change_set() -> None:
    """Two change sets would leave a window in which the store holds both
    extractions, or neither, and nothing to say which state it is in."""

    from backend.api.canonical_repository.postgres_store import (
        build_change_set_plan,
        build_retirement_plan,
        combined_plan,
    )

    arrival = build_change_set_plan(_package(), {})
    withdrawal = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="superseded by PKG-NEW", package_id="PKG-NEW",
    )
    merged = combined_plan(arrival, withdrawal)
    assert merged.as_dict()["summary"]["retired"] == 1
    assert merged.as_dict()["summary"]["created"] == arrival.as_dict()["summary"]["created"]
    assert [item.operation for item in merged.operations][-1] == "retire"
    assert merged.change_set_id not in {arrival.change_set_id, withdrawal.change_set_id}


# ---------------------------------------------------------------------------
# which written articles a withdrawal invalidates
# ---------------------------------------------------------------------------

from backend.pipeline.extraction_supersede_runner import articles_to_regenerate  # noqa: E402

PLANS = {
    "CP-written": {"manuscript_sha256": "abc", "description": "太16:13–20"},
    "CP-candidate": {"description": "還沒寫成稿"},
}


def test_only_a_plan_that_produced_a_manuscript_is_reported() -> None:
    """A candidate plan is rebuilt from whatever the claim layer holds when
    somebody writes from it, so there is nothing to tell anyone about."""

    articles = articles_to_regenerate(
        {"CL-old"},
        routes={"R1": {"claim_id": "CL-old", "target_id": "CP-candidate"}},
        decisions={},
        plans=PLANS,
    )
    assert articles == []


def test_a_written_article_is_reported_through_either_citation_path() -> None:
    by_route = articles_to_regenerate(
        {"CL-old"},
        routes={"R1": {"claim_id": "CL-old", "target_id": "CP-written"}},
        decisions={},
        plans=PLANS,
    )
    by_decision = articles_to_regenerate(
        {"CL-old"},
        routes={},
        decisions={"CD-1": {"plan_id": "CP-written", "claim_ids": ["CL-old", "CL-kept"]}},
        plans=PLANS,
    )
    assert [row["plan_id"] for row in by_route] == ["CP-written"]
    assert [row["plan_id"] for row in by_decision] == ["CP-written"]
    assert by_decision[0]["claims_withdrawn"] == 1


def test_an_id_appearing_only_in_prose_is_not_a_dependency() -> None:
    """Traced through the citation fields, not by searching the payload text."""

    articles = articles_to_regenerate(
        {"CL-old"},
        routes={},
        decisions={"CD-1": {"plan_id": "CP-written", "decision": "參見 CL-old 的討論", "claim_ids": []}},
        plans=PLANS,
    )
    assert articles == []
