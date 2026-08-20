from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.shared_knowledge_pilot import (
    _merge_claim_relation_consensus,
    _validate_product_plan_evidence_scopes,
    build_shared_knowledge_package,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = (
    ROOT / "backend" / "tests" / "fixtures" / "wang_knowledge_platform" / "shared_pilot_inputs"
)


def test_real_pilot_preserves_graph_and_marks_scope() -> None:
    source = FIXTURE_ROOT
    package = build_shared_knowledge_package(
        json.loads((source / "claims.json").read_text(encoding="utf-8")),
        json.loads((source / "argument_graph.json").read_text(encoding="utf-8")),
        json.loads((source / "composition_plan_matthew_17.json").read_text(encoding="utf-8")),
        json.loads((source / "evidence_attribution_overrides_v1.json").read_text(encoding="utf-8")),
    )
    counts = package["summary"]["counts"]
    assert counts["sources"] == 2
    assert counts["fragments"] == 140
    assert counts["claims"] == 30
    assert counts["relations"] == 125
    assert counts["claim_relations"] == 7
    assert counts["knowledge_routes"] == 30
    assert counts["topic_nodes"] == 55
    assert counts["validation_experiments"] == 5
    assert package["corpus_scope"]["completeness"] == "not_corpus_complete"
    assert all(item["validation_only"] for item in package["cross_source_syntheses"])
    assert package["product_plans"][0]["editorial_attribution"] == "editor"
    claim = next(item for item in package["claims"] if item["claim_id"] == "CL-0001")
    assert "L3-E040" in claim["context_evidence_step_ids"]
    assert "L3-E037" in claim["eligible_evidence_step_ids"]
    assert package["framework_candidate"]["evidence_step_ids"]
    assert "claims" not in package["framework_candidate"]
    topic_ids = {item["topic_id"] for item in package["topic_nodes"]}
    assert "righteousness-faith-relationship" in topic_ids
    topic_routes = [
        item for item in package["knowledge_routes"]
        if item["route_type"] == "topic_research"
    ]
    assert topic_routes
    assert all(item["canonical_topic_ids"] for item in topic_routes)


def test_question_answer_state_is_derived_not_assumed() -> None:
    """A blanket "linked" label hid unanswered questions from the open list."""
    source = FIXTURE_ROOT
    graph = json.loads((source / "argument_graph.json").read_text(encoding="utf-8"))
    package = build_shared_knowledge_package(
        json.loads((source / "claims.json").read_text(encoding="utf-8")),
        graph,
        json.loads((source / "composition_plan_matthew_17.json").read_text(encoding="utf-8")),
        json.loads((source / "evidence_attribution_overrides_v1.json").read_text(encoding="utf-8")),
    )
    answered_sources = {
        item["source_evidence_id"]
        for item in graph["relations"]
        if item["relation_type"] == "answers"
    }
    extracted = [
        item
        for item in package["questions"]
        if item.get("answer_state_origin") == "derived_from_argument_graph"
    ]
    assert extracted, "graph questions must carry a derived state"
    for question in extracted:
        node_id = question["question_id"].removeprefix("Q-")
        expected = "answered" if node_id in answered_sources else "unanswered"
        assert question["answer_state"] == expected, question["question_id"]
        expected_link = "linked_in_argument_graph" if node_id in answered_sources else "unlinked"
        assert question["argument_link_state"] == expected_link
        # Being linked in the graph is never a human confirmation of completeness.
        assert question["answer_verified_by_human"] is False
    assert any(item["answer_state"] == "unanswered" for item in extracted)


def test_compound_question_can_be_only_partially_answered() -> None:
    source = FIXTURE_ROOT
    package = build_shared_knowledge_package(
        json.loads((source / "claims.json").read_text(encoding="utf-8")),
        json.loads((source / "argument_graph.json").read_text(encoding="utf-8")),
        json.loads((source / "composition_plan_matthew_17.json").read_text(encoding="utf-8")),
        json.loads((source / "evidence_attribution_overrides_v1.json").read_text(encoding="utf-8")),
        question_answer_state_overrides=json.loads(
            (source / "question_answer_state_overrides_v1.json").read_text(encoding="utf-8")
        ),
    )
    question = next(item for item in package["questions"] if item["question_id"] == "Q-L3-E034")
    assert question["argument_link_state"] == "linked_in_argument_graph"
    assert question["answer_state"] == "partially_answered"
    assert question["answered_subquestions"]
    assert question["unanswered_subquestions"]


def test_every_reference_points_to_an_existing_object() -> None:
    source = FIXTURE_ROOT
    package = build_shared_knowledge_package(
        json.loads((source / "claims.json").read_text(encoding="utf-8")),
        json.loads((source / "argument_graph.json").read_text(encoding="utf-8")),
        json.loads((source / "composition_plan_matthew_17.json").read_text(encoding="utf-8")),
        json.loads((source / "evidence_attribution_overrides_v1.json").read_text(encoding="utf-8")),
    )
    fragments = {item["fragment_id"] for item in package["source_fragments"]}
    evidence = {item["evidence_step_id"] for item in package["evidence_steps"]}
    claims = {item["claim_id"] for item in package["claims"]}
    assert all(item["source_fragment_id"] in fragments for item in package["evidence_steps"])
    assert all(item["source_id"] in evidence and item["target_id"] in evidence for item in package["knowledge_relations"])
    assert all(set(item["claim_ids"]) <= claims for item in package["cross_source_syntheses"])
    assert {item["claim_id"] for item in package["knowledge_routes"]} == claims
    topics = {item["topic_id"] for item in package["topic_nodes"]}
    assert all(
        set(item.get("canonical_topic_ids", [])) <= topics
        for item in package["knowledge_routes"]
    )
    claim_or_position_ids = claims | {item["position_id"] for item in package["position_nodes"]}
    assert all(item["source_id"] in claim_or_position_ids and item["target_id"] in claim_or_position_ids for item in package["claim_relations"])
    assert all(claim["evidence_step_ids"] for claim in package["claims"])
    for claim in package["claims"]:
        for occurrence in claim.get("occurrences", []):
            assert "source_evidence_ids" not in occurrence
            assert "local_source_evidence_ids" in occurrence
            assert "canonical_evidence_step_ids" in occurrence


def test_same_claim_can_route_to_scripture_and_topic_plans() -> None:
    source = FIXTURE_ROOT
    topic_plan = json.loads(
        (source / "composition_plan_son_of_man.json").read_text(encoding="utf-8")
    )
    package = build_shared_knowledge_package(
        json.loads((source / "claims.json").read_text(encoding="utf-8")),
        json.loads((source / "argument_graph.json").read_text(encoding="utf-8")),
        json.loads((source / "composition_plan_matthew_17.json").read_text(encoding="utf-8")),
        json.loads((source / "evidence_attribution_overrides_v1.json").read_text(encoding="utf-8")),
        additional_composition_payloads=[topic_plan],
    )

    assert {plan["plan_id"] for plan in package["product_plans"]} == {
        "CP-matthew-17",
        "CP-topic-son-of-man",
    }
    cl_0007_routes = [
        route for route in package["knowledge_routes"]
        if route["claim_id"] == "CL-0007"
    ]
    assert {route["route_type"] for route in cl_0007_routes} == {"scripture_exposition"}
    cl_0028_routes = [
        route for route in package["knowledge_routes"]
        if route["claim_id"] == "CL-0028"
    ]
    assert {route["route_type"] for route in cl_0028_routes} == {
        "scripture_exposition",
        "topic_research",
    }
    topic_synthesis = next(
        item for item in package["cross_source_syntheses"]
        if item["synthesis_id"] == "SYN-TOPIC-SON-OF-MAN"
    )
    assert len(topic_synthesis["source_leads"]) == 8
    assert topic_synthesis["source_leads"][0]["transcript_id"] == "011WSR01"
    assert topic_synthesis["source_leads"][0]["evidence_maturity"] == (
        "ai_consensus_detailed_claims"
    )
    assert any(
        lead["transcript_id"] == "2016 NYSC 專題：馬太福音釋經（五）4"
        for lead in topic_synthesis["source_leads"]
    )
    topic_plan_record = next(
        plan for plan in package["product_plans"]
        if plan["plan_id"] == "CP-topic-son-of-man"
    )
    decision_by_id = {
        decision["decision_id"]: decision
        for decision in topic_plan_record["decisions"]
    }
    d1_scopes = {
        scope["claim_id"]: scope["evidence_step_ids"]
        for scope in decision_by_id["CD-SON-001"]["claim_hierarchy"][
            "evidence_step_scopes"
        ]
    }
    assert d1_scopes["DK-f0eac41a4244-CL001"] == [
        "DK-f0eac41a4244-E002",
        "DK-f0eac41a4244-E003",
    ]
    assert d1_scopes["CL-0028"] == ["L3-E006", "L3-E035"]
    d2_scopes = {
        scope["claim_id"]: scope["evidence_step_ids"]
        for scope in decision_by_id["CD-SON-002"]["claim_hierarchy"][
            "evidence_step_scopes"
        ]
    }
    assert d2_scopes["DK-f0eac41a4244-CL001"] == [
        "DK-f0eac41a4244-E004",
        "DK-f0eac41a4244-E005",
    ]
    assert d2_scopes["CL-0028"] == [
        "L3-E029",
        "L3-E030",
        "L3-E031",
        "L3-E032",
    ]
    assert decision_by_id["CD-SON-003"]["editorial_transition"][
        "editorial_attribution"
    ] == "editor"
    assert decision_by_id["CD-SON-003"]["passage"] == "太16:28"
    assert decision_by_id["CD-SON-003"]["section_title"] == (
        "句首 Amen 所显示的宣告权柄"
    )
    assert decision_by_id["CD-SON-003"]["pending_passages"] == [
        "太9:1–8",
        "太12:1–8",
    ]
    assert decision_by_id["CD-SON-006"]["source_lead_ids"] == [
        "SL-SON-001",
        "SL-SON-004",
        "SL-SON-008",
    ]
    assert decision_by_id["CD-SON-004"]["claim_ids"] == []
    assert decision_by_id["CD-SON-006"]["claim_ids"] == [
        "DK-f0eac41a4244-CL001",
        "CL-0028",
    ]


def test_reviewed_detailed_sermon_joins_shared_topic_plan() -> None:
    source = FIXTURE_ROOT
    topic_plan = json.loads(
        (source / "composition_plan_son_of_man.json").read_text(encoding="utf-8")
    )
    exposition_plan = json.loads(
        (source / "composition_plan_matthew_26_1_30_011.json").read_text(encoding="utf-8")
    )
    detailed = json.loads(
        (
            source
            / "detailed-extractions"
            / "011WSR01-f0eac41a4244.reviewed-candidate.json"
        ).read_text(encoding="utf-8")
    )
    relation_consensus = json.loads(
        (source / "claim_relation_consensus_v1.json").read_text(encoding="utf-8")
    )
    relation_review = json.loads(
        (source / "claim_relation_review_v1.json").read_text(encoding="utf-8")
    )
    package = build_shared_knowledge_package(
        json.loads((source / "claims.json").read_text(encoding="utf-8")),
        json.loads((source / "argument_graph.json").read_text(encoding="utf-8")),
        json.loads((source / "composition_plan_matthew_17.json").read_text(encoding="utf-8")),
        json.loads((source / "evidence_attribution_overrides_v1.json").read_text(encoding="utf-8")),
        additional_composition_payloads=[exposition_plan, topic_plan],
        detailed_packages=[detailed],
        claim_relation_consensus=relation_consensus,
        claim_relation_review=relation_review,
    )

    detailed_claim_id = "DK-f0eac41a4244-CL001"
    assert package["summary"]["counts"]["sources"] == 3
    assert package["summary"]["counts"]["claims"] == 47
    assert any(item["claim_id"] == detailed_claim_id for item in package["claims"])
    routes = [
        item for item in package["knowledge_routes"]
        if item["claim_id"] == detailed_claim_id
    ]
    assert len(routes) == 2
    route_by_target = {item["target_id"]: item for item in routes}
    assert set(route_by_target) == {
        "CP-matthew-26-1-30-011",
        "CP-topic-son-of-man",
    }
    assert route_by_target["CP-matthew-26-1-30-011"]["decision_ids"] == [
        "CD-M26-011-001"
    ]
    assert set(route_by_target["CP-topic-son-of-man"]["decision_ids"]) == {
        "CD-SON-001",
        "CD-SON-002",
        "CD-SON-006",
    }
    synthesis = next(
        item for item in package["cross_source_syntheses"]
        if item["synthesis_id"] == "SYN-TOPIC-SON-OF-MAN"
    )
    assert detailed_claim_id in synthesis["claim_ids"]
    source_lead = synthesis["source_leads"][0]
    assert source_lead["evidence_maturity"] == "ai_consensus_detailed_claims"
    routed_outside_main = next(
        item
        for item in package["knowledge_routes"]
        if item["claim_id"] == "DK-f0eac41a4244-CL003"
    )
    assert routed_outside_main["route_type"] == "scripture_exposition"
    assert routed_outside_main["decision_ids"] == ["CD-M26-011-009"]

    covenant_claim = next(
        item
        for item in package["knowledge_routes"]
        if item["claim_id"] == "DK-f0eac41a4244-CL017"
    )
    assert covenant_claim["decision_ids"] == ["CD-M26-011-008"]
    assert {
        plan["plan_id"] for plan in package["product_plans"]
    } == {"CP-matthew-17", "CP-matthew-26-1-30-011", "CP-topic-son-of-man"}
    consensus_relations = {
        item["claim_relation_id"]: item for item in package["claim_relations"]
    }
    assert consensus_relations["CR-CONSENSUS-SON-001"]["relation_type"] == "corroborates"
    assert consensus_relations["CR-CONSENSUS-COVENANT-001"]["relation_type"] == "contextualizes"
    assert consensus_relations["DK-f0eac41a4244-CR011"]["relation_type"] == "contextualizes"
    assert consensus_relations["DK-f0eac41a4244-CR011"]["review_status"] == "ai_consensus_reviewed"
    assert package["claim_relation_constraints"] == [
        {
            **relation_consensus["constraints"][0],
            "review_status": "ai_consensus_candidate",
        }
    ]


def test_relation_constraint_rejects_forbidden_support_edge() -> None:
    relations = [
        {
            "claim_relation_id": "CR-BAD",
            "source_id": "CL-A",
            "target_id": "CL-B",
            "relation_type": "supports",
        }
    ]
    consensus = {
        "constraints": [
            {
                "constraint_id": "CRC-001",
                "source_id": "CL-A",
                "target_id": "CL-B",
                "forbidden_relation_types": ["supports"],
                "bidirectional": True,
            }
        ]
    }

    with pytest.raises(ValueError, match="violates constraint CRC-001"):
        _merge_claim_relation_consensus(relations, consensus, {"CL-A", "CL-B"})


def test_composition_evidence_scope_rejects_a_foreign_step() -> None:
    plans = [
        {
            "plan_id": "CP-1",
            "decisions": [
                {
                    "decision_id": "CD-1",
                    "claim_ids": ["CL-A"],
                    "claim_hierarchy": {
                        "evidence_step_scopes": [
                            {
                                "claim_id": "CL-A",
                                "evidence_step_ids": ["E-B"],
                            }
                        ]
                    },
                }
            ],
        }
    ]
    claims = {"CL-A": {"claim_id": "CL-A", "evidence_step_ids": ["E-A"]}}

    with pytest.raises(ValueError, match="cites foreign steps"):
        _validate_product_plan_evidence_scopes(plans, claims)


def test_a_claim_a_merge_retired_is_not_published() -> None:
    """The merge's whole point is that one of the two stops being asserted.

    The retired row stays in the detailed package as the record that the merge
    happened; projecting it into the shared store would put the duplicate back
    under a second id, with its anchors now counted twice.
    """
    source = FIXTURE_ROOT
    detailed = json.loads(
        (
            source
            / "detailed-extractions"
            / "011WSR01-f0eac41a4244.reviewed-candidate.json"
        ).read_text(encoding="utf-8")
    )
    retired = next(
        item for item in detailed["claims"] if item["claim_id"] == "DK-f0eac41a4244-CL003"
    )
    retired["superseded_by"] = "DK-f0eac41a4244-CL001"
    retired["review_status"] = "superseded"

    package = build_shared_knowledge_package(
        json.loads((source / "claims.json").read_text(encoding="utf-8")),
        json.loads((source / "argument_graph.json").read_text(encoding="utf-8")),
        json.loads((source / "composition_plan_matthew_17.json").read_text(encoding="utf-8")),
        json.loads((source / "evidence_attribution_overrides_v1.json").read_text(encoding="utf-8")),
        detailed_packages=[detailed],
    )

    published = {item["claim_id"] for item in package["claims"]}
    assert "DK-f0eac41a4244-CL003" not in published
    assert "DK-f0eac41a4244-CL001" in published
