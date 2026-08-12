import pytest

from backend.pipeline.candidate_projection import (
    SCOPE,
    build_incremental_package,
    projection_input,
    scripture_targets,
    validate_candidates,
)
from backend.pipeline.candidate_projection_runner import _validate_plan_review


def _knowledge():
    return {
        "batch": {"batch_id": "RB-TEST"},
        "claims": [
            {
                "claim_id": "C1",
                "title": "解释罗马书三章",
                "claim_type": "interpretive_judgment",
                "scripture_refs": ["罗马书3:21-31"],
                "topic_terms": ["义"],
                "occurrences": [{"transcript_id": "S1"}],
            },
            {
                "claim_id": "C2",
                "title": "约先由宗主国施恩",
                "claim_type": "theological_claim",
                "scripture_refs": ["出埃及记20:1-3"],
                "topic_terms": ["约"],
                "occurrences": [{"transcript_id": "S2"}],
            },
        ],
    }


def test_scripture_targets_group_by_book_and_chapter():
    rows = scripture_targets(_knowledge()["claims"])
    assert [(row["target_id"], row["claim_ids"]) for row in rows] == [
        ("SCRIPTURE-Exod-20", ["C2"]),
        ("SCRIPTURE-Rom-3", ["C1"]),
    ]


def test_projection_requires_every_claim_assigned_or_explicitly_unassigned():
    source = projection_input(_knowledge(), {"result": {}}, [])
    payload = {
        "scope_confirmation": "product_candidate_structure_no_theological_critique",
        "candidate_plans": [
            {
                "axis": "scripture",
                "title": "罗马书第三章释经",
                "description": "",
                "canonical_topic_id": None,
                "scripture_target_id": "SCRIPTURE-Rom-3",
                "sections": [
                    {
                        "section_title": "义的显明",
                        "arrangement": "main_section",
                        "reason": "直接解释该段",
                        "claim_ids": ["C1"],
                    }
                ],
            }
        ],
        "unassigned_claim_ids": ["C2"],
        "summary": "一个释经候选",
    }
    validate_candidates(payload, source)
    payload["unassigned_claim_ids"] = []
    try:
        validate_candidates(payload, source)
    except ValueError as exc:
        assert "omitted claims" in str(exc)
    else:
        raise AssertionError("missing claim coverage should fail")


def test_reviewed_candidates_become_routes_and_plans_without_approval():
    reviewed = {
        "candidate_plans": [
            {
                "axis": "topic",
                "title": "约与顺服",
                "description": "候选专题",
                "canonical_topic_id": "covenant-law-history",
                "scripture_target_id": None,
                "sections": [
                    {
                        "section_title": "恩典先行",
                        "arrangement": "main_section",
                        "reason": "论证次序",
                        "claim_ids": ["C2"],
                    }
                ],
            }
        ],
        "unassigned_claim_ids": ["C1"],
    }
    package = build_incremental_package(
        batch_id="RB-TEST",
        reviewed_payload=reviewed,
        canonical_topics=[{"topic_id": "covenant-law-history", "label": "圣约"}],
    )
    assert len(package["product_plans"]) == 1
    assert len(package["knowledge_routes"]) == 1
    assert package["knowledge_routes"][0]["canonical_topic_ids"] == [
        "covenant-law-history"
    ]
    assert package["knowledge_routes"][0]["review_status"] == "candidate"
    assert package["product_plans"][0]["review_status"] == "candidate"


def test_plan_review_replacement_must_preserve_exact_claim_set():
    source = projection_input(_knowledge(), {"result": {}}, [])
    original = {
        "axis": "scripture",
        "title": "罗马书第三章释经",
        "description": "",
        "canonical_topic_id": None,
        "scripture_target_id": "SCRIPTURE-Rom-3",
        "sections": [{
            "section_title": "义的显明",
            "arrangement": "main_section",
            "reason": "直接解释该段",
            "claim_ids": ["C1"],
        }],
    }
    response = {
        "scope_confirmation": SCOPE,
        "decision": "replace",
        "reason": "错误地漏掉原主张",
        "replacement_plans": [{
            **original,
            "sections": [{**original["sections"][0], "claim_ids": []}],
        }],
    }
    with pytest.raises(ValueError, match="omitted claims"):
        _validate_plan_review(response, source, original)
