from __future__ import annotations

import copy

import pytest

from backend.pipeline.topic_structure_discovery import (
    SCOPE,
    build_incremental_package,
    discovery_input,
    graph_profile,
    validate_discovery,
)


def _knowledge() -> dict:
    return {
        "batch": {"batch_id": "RB-TEST"},
        "claims": [
            {"claim_id": "C1", "title": "主张一", "topic_terms": ["约"], "occurrences": [{"transcript_id": "S1"}]},
            {"claim_id": "C2", "title": "主张二", "topic_terms": ["约"], "occurrences": [{"transcript_id": "S2"}]},
            {"claim_id": "C3", "title": "孤立问答", "topic_terms": ["问答"], "occurrences": [{"transcript_id": "S3"}]},
        ],
        "claim_relations": [
            {"claim_relation_id": "R1", "from_id": "C1", "to_id": "C2", "relation_type": "supports"},
            {"claim_relation_id": "R2", "source_id": "C2", "target_id": "C1", "relation_type": "explains"},
        ],
    }


def _discovery() -> dict:
    return {
        "scope_confirmation": SCOPE,
        "topic_families": [{
            "title": "约与关系",
            "organizing_question": "约如何组织关系？",
            "editorial_rationale": "两条互相支持的主张形成一条论证线。",
            "subtopics": [{
                "title": "约的结构",
                "central_question": "约如何运行？",
                "editorial_rationale": "先主旨后证据。",
                "sections": [
                    {"title": "核心判断", "role": "core_thesis", "purpose": "提出主旨", "claim_ids": ["C1"]},
                    {"title": "论证展开", "role": "reasoning", "purpose": "解释主旨", "claim_ids": ["C2"]},
                ],
            }],
        }],
        "unassigned_claim_ids": ["C3"],
        "summary": "候选结构",
    }


def test_graph_profile_accepts_legacy_and_canonical_relation_keys() -> None:
    profile = graph_profile(_knowledge())
    assert profile["relation_count"] == 2
    assert profile["relation_type_counts"] == {"explains": 1, "supports": 1}
    assert profile["high_connection_claims"][0]["degree"] == 2


def test_discovery_input_is_graph_first_and_preserves_sources() -> None:
    source = discovery_input(_knowledge())
    assert source["policy"]["processing_batch_is_not_a_topic"] is True
    assert source["claims"][0]["source_transcript_ids"] == ["S1"]
    assert source["claim_relations"][0]["source_claim_id"] == "C1"


def test_validate_discovery_requires_exactly_one_home_or_unassigned() -> None:
    source = discovery_input(_knowledge())
    validate_discovery(_discovery(), source)

    omitted = copy.deepcopy(_discovery())
    omitted["unassigned_claim_ids"] = []
    with pytest.raises(ValueError, match="omitted claims"):
        validate_discovery(omitted, source)

    duplicate = copy.deepcopy(_discovery())
    duplicate["topic_families"][0]["subtopics"][0]["sections"][1]["claim_ids"].append("C1")
    with pytest.raises(ValueError, match="repeated"):
        validate_discovery(duplicate, source)


def test_incremental_package_persists_hierarchy_plans_sections_and_routes() -> None:
    package = build_incremental_package(batch_id="RB-TEST", reviewed_payload=_discovery())
    topics = package["topic_nodes"]
    family = next(row for row in topics if row["topic_level"] == "family")
    subtopic = next(row for row in topics if row["topic_level"] == "subtopic")
    assert subtopic["parent_topic_id"] == family["topic_id"]

    plan = package["product_plans"][0]
    assert plan["product_type"] == "topic_research"
    assert [row["section_role"] for row in plan["decisions"]] == ["core_thesis", "reasoning"]
    assert {row["claim_id"] for row in package["knowledge_routes"]} == {"C1", "C2"}
    assert all(row["review_status"] == "candidate" for row in topics)
    assert package["candidate_generation"]["unassigned_claim_ids"] == ["C3"]
