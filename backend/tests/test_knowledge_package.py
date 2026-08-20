"""A merged package must read as merged, at every stage that reads it."""

from __future__ import annotations

import pytest

from backend.pipeline.corpus_ai_adjudication import compile_outcome
from backend.pipeline.cross_section_relation import (
    CrossSectionValidationError,
    record_positions,
    validate_proposals,
)
from backend.pipeline.knowledge_package import live_claim_ids, live_claims
from backend.pipeline.observation_coverage_adjudication import build_packet
from backend.pipeline.passage_knowledge_slice import Passage, build_passage_slice
from backend.pipeline.topic_structure_discovery import discovery_input, graph_profile


def _merged_package() -> dict:
    """Two claims, one of them retired into the other by an accepted merge."""
    return {
        "claims": [
            {
                "claim_id": "CL-1",
                "title": "留下的那條",
                "statement": "留下的那條",
                "claim_type": "reasoning_conclusion",
                "evidence_step_ids": ["E1", "E2"],
                "occurrences": [{"transcript_id": "L1", "anchors": [
                    {"evidence_id": "E1", "paragraph_key": "S0001"},
                    {"evidence_id": "E2", "paragraph_key": "S0002"},
                ]}],
            },
            {
                "claim_id": "CL-2",
                "title": "被併掉的那條",
                "statement": "被併掉的那條",
                "claim_type": "reasoning_conclusion",
                "evidence_step_ids": ["E2"],
                "review_status": "superseded",
                "superseded_by": "CL-1",
                "occurrences": [{"transcript_id": "L1", "anchors": [
                    {"evidence_id": "E2", "paragraph_key": "S0002"},
                ]}],
            },
        ],
        "evidence_steps": [
            {"evidence_step_id": "E1", "statement": "第一段證據", "section_index": 1},
            {"evidence_step_id": "E2", "statement": "第二段證據", "section_index": 2},
        ],
        "observations": [],
        "claim_relations": [],
    }


def test_live_claims_skips_what_a_merge_retired() -> None:
    assert [row["claim_id"] for row in live_claims(_merged_package())] == ["CL-1"]
    assert live_claim_ids(_merged_package()) == {"CL-1"}


def test_coverage_packet_does_not_show_the_retired_claim() -> None:
    packet = build_packet(_merged_package(), [], scope="太16")

    assert [row["claim_id"] for row in packet["claims"]] == ["CL-1"]


def test_topic_discovery_does_not_group_the_retired_claim() -> None:
    source = discovery_input(_merged_package())

    assert [row["claim_id"] for row in source["claims"]] == ["CL-1"]


def test_a_proposed_relation_cannot_point_at_a_retired_claim() -> None:
    package = _merged_package()
    positions = record_positions(package)
    response = {
        "claim_relations": [{
            "claim_relation_id": "CR-9", "from_id": "CL-1", "to_id": "CL-2",
            "relation_type": "supports", "reason": "指向已退役的重複",
        }],
    }

    with pytest.raises(CrossSectionValidationError, match="CL-2"):
        validate_proposals(response, package, positions=positions, boundaries=[0, 1])

    # And the retired claim is not offered to the model in the first place.
    assert "CL-2" not in positions


def test_adjudication_summary_reports_how_much_it_accepted() -> None:
    response = {
        "adjudications": [
            {"claim_id": "CL-1", "decision": "accept", "patch": {}},
            {"claim_id": "CL-2", "decision": "reject", "patch": {}},
        ]
    }
    reconsideration = {"reconsiderations": [{"claim_id": "CL-2", "decision": "withdraw"}]}

    summary = compile_outcome(response, reconsideration, reviews=[])["summary"]

    assert summary["adjudicated"] == 2
    assert summary["accepted"] == 1
    assert summary["acceptance_rate"] == 0.5


def test_topic_profile_does_not_offer_the_retired_claim_to_the_model() -> None:
    """The profile and the claim list go into the same payload.

    Naming a claim the model may not use would have it assigned to a subtopic
    and then rejected by this stage's own validation.
    """
    package = _merged_package()
    package["claims"][1]["topic_terms"] = ["彌賽亞"]
    package["claims"][0]["topic_terms"] = ["彌賽亞"]

    profile = graph_profile(package)

    assert profile["claim_count"] == 1
    named = {
        claim_id
        for row in profile.get("recurring_topic_terms") or []
        for claim_id in row.get("claim_ids") or []
    }
    assert "CL-2" not in named


def test_passage_slice_does_not_hand_the_retired_claim_to_the_author() -> None:
    package = _merged_package()
    for claim in package["claims"]:
        claim["scripture_refs"] = ["太 16:21"]

    slice_ = build_passage_slice(package, Passage(book="太", chapter=16, start_verse=21, end_verse=21))

    seen = {row["claim_id"] for row in slice_["claims"] + slice_.get("contextual_claims", [])}
    assert "CL-2" not in seen


def test_the_pilot_refuses_a_merge_it_cannot_execute() -> None:
    """A silent no-op here would leave the duplicate live with no trace."""
    # Imported here: the pilot resolves platform paths at import time.
    from backend.pipeline.shared_knowledge_pilot import _apply_claim_overrides

    claims = {"claims": [{"claim_id": "CL-2", "title": "被併掉的那條"}]}
    overrides = {"claims": {"CL-2": {"status": "ai_consensus_applied", "superseded_by": "CL-1"}}}

    with pytest.raises(ValueError, match="knowledge_consensus_applier"):
        _apply_claim_overrides(claims, overrides)
