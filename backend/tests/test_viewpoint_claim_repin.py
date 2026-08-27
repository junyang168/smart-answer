"""A Claim review must not invalidate the viewpoint layer it never touched."""

from __future__ import annotations

from typing import Any

from backend.api.canonical_repository.viewpoint_claim_repin import (
    plan_claim_link_repin,
    substantive_difference,
)
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha


def _claim(revision: int, **overrides: Any) -> dict[str, Any]:
    payload = {
        "claim_id": "DK-1-CL001",
        "schema_version": "wang_claim_v1",
        "statement": "馬太福音十六章十八節的磐石不是彼得這個人本身。",
        "source_id": "SRC-1",
        "revision": revision,
        "review_status": "candidate",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
        "evidence": [{"evidence_step_id": "E1", "source_fragment_id": "FR-1"}],
    }
    payload.update(overrides)
    return payload


def _reviewed(revision: int) -> dict[str, Any]:
    return _claim(
        revision,
        review_status="ai_consensus_reviewed",
        reviewed_by="independent_ai_review",
        reviewed_at="2026-08-26T14:23:16.226918+00:00",
        review_note="独立 AI 复审：pass",
    )


def _link(pinned: int, claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "viewpoint_claim_link_id": "VCL-1",
        "claim_id": "DK-1-CL001",
        "pinned_claim_revision": pinned,
        "effective_state": "active",
        "revision": 1,
        "component_locator": {
            "claim_sha256": semantic_record_sha(claim),
            "statement_component": "磐石不是彼得這個人本身",
            "canonical_spans": [],
        },
    }


def test_a_review_stamp_is_not_a_change_to_what_the_claim_says():
    # 2026-08-26: an independent AI review stamped review_status, reviewed_by,
    # reviewed_at and review_note on 113 Claims without altering a word. All 88
    # active claim links failed both pin checks, and every write to viewpoints
    # and routes was blocked until the pins moved.
    pinned = _claim(2)
    current = _reviewed(3)
    assert substantive_difference(pinned, current) == []

    report = plan_claim_link_repin(
        links=[_link(2, pinned)],
        claims={"DK-1-CL001": current},
        pinned_payloads={("DK-1-CL001", 2): pinned},
    )
    assert report["needs_review"] == []
    assert len(report["repinned"]) == 1
    moved = report["repinned"][0]
    assert moved["pinned_claim_revision"] == 3
    assert moved["component_locator"]["claim_sha256"] == semantic_record_sha(current)
    # The locator's own text is untouched: the component did not move.
    assert moved["component_locator"]["statement_component"] == "磐石不是彼得這個人本身"


def test_a_claim_whose_wording_moved_is_left_for_a_person():
    pinned = _claim(2)
    current = _reviewed(3)
    current["statement"] = "馬太福音十六章十八節的磐石就是彼得本人。"

    report = plan_claim_link_repin(
        links=[_link(2, pinned)],
        claims={"DK-1-CL001": current},
        pinned_payloads={("DK-1-CL001", 2): pinned},
    )
    assert report["repinned"] == []
    assert report["needs_review"][0]["changed_fields"] == ["statement"]


def test_evidence_moving_is_substantive_too():
    pinned = _claim(2)
    current = _reviewed(3)
    current["evidence"] = [{"evidence_step_id": "E9", "source_fragment_id": "FR-9"}]

    report = plan_claim_link_repin(
        links=[_link(2, pinned)],
        claims={"DK-1-CL001": current},
        pinned_payloads={("DK-1-CL001", 2): pinned},
    )
    assert report["repinned"] == []
    assert report["needs_review"][0]["changed_fields"] == ["evidence"]


def test_a_pin_with_no_history_is_reported_not_guessed():
    report = plan_claim_link_repin(
        links=[_link(2, _claim(2))],
        claims={"DK-1-CL001": _reviewed(3)},
        pinned_payloads={},
    )
    assert report["repinned"] == []
    assert report["missing"][0]["reason"] == "pinned revision is not in the version history"


def test_an_unmoved_pin_is_left_exactly_as_it_is():
    claim = _claim(2)
    report = plan_claim_link_repin(
        links=[_link(2, claim)],
        claims={"DK-1-CL001": claim},
        pinned_payloads={("DK-1-CL001", 2): claim},
    )
    assert report["repinned"] == []
    assert report["unchanged_link_ids"] == ["VCL-1"]


def test_review_fields_are_the_only_thing_a_pin_may_advance_over():
    """The rule the scope packet leans on, stated once.

    Both the claim link pin and the Claim manifest pin were invalidated by the
    same review stamp, and both now ask this one question. Whatever a future
    review learns to write must be added here deliberately -- a field that
    quietly joins the exemption list is how "the Claim did not change" stops
    being true.
    """

    from backend.api.canonical_repository.viewpoint_claim_repin import (
        CLAIM_REVIEW_FIELDS,
    )

    assert CLAIM_REVIEW_FIELDS == {
        "review_status",
        "reviewed_by",
        "reviewed_at",
        "review_note",
        "revision",
    }
    pinned = _claim(2)
    current = _reviewed(3)
    current["visibility"] = "public"
    assert substantive_difference(pinned, current) == ["visibility"]
