import hashlib
import json

import pytest

from backend.api.canonical_repository.reviewed_candidate_contract import (
    reviewed_candidate_artifact_sha256,
)
from backend.pipeline.candidate_projection import (
    SCOPE,
    build_incremental_package,
    projection_input,
    scripture_targets,
    validate_candidates,
)
from backend.pipeline.candidate_projection_runner import _validate_plan_review, run
from backend.tests.test_cross_sermon_relation import _authenticated_knowledge


class _NeverStore:
    def compile_package(self, **_kwargs):
        pytest.fail("invalid projection input must fail before DB access")

    def ingest_package(self, *_args, **_kwargs):
        pytest.fail("invalid projection input must never reach ingest")


class _NeverClient:
    model = "never-called"

    def generate_json(self, *_args, **_kwargs):
        pytest.fail("invalid projection input must fail before model access")


def _projection_paths(tmp_path, knowledge):
    knowledge_path = tmp_path / "knowledge.json"
    relations_path = tmp_path / "relations.json"
    knowledge_path.write_text(json.dumps(knowledge, ensure_ascii=False), encoding="utf-8")
    relations_path.write_text(json.dumps({
        "generation": {
            "source_knowledge_sha256": hashlib.sha256(
                knowledge_path.read_bytes()
            ).hexdigest()
        },
        "result": {},
    }), encoding="utf-8")
    return knowledge_path, relations_path


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


def test_runner_rejects_unsealed_aggregate_before_db_or_model(tmp_path):
    knowledge = _authenticated_knowledge()
    knowledge["consensus_application"].pop("artifact_sha256")
    knowledge_path, relations_path = _projection_paths(tmp_path, knowledge)

    with pytest.raises(ValueError, match="does not authenticate"):
        run(
            knowledge_path=knowledge_path,
            relations_path=relations_path,
            output_dir=tmp_path / "out",
            store=_NeverStore(),
            openai_client=_NeverClient(),
            claude_client=_NeverClient(),
            apply=False,
            force=False,
        )


def test_runner_rejects_unresolved_live_claim_before_db_or_model(tmp_path):
    knowledge = _authenticated_knowledge()
    knowledge["claims"][0]["review_status"] = "human_review_required"
    resolution = knowledge["consensus_application"]["review_resolutions"][0]
    resolution["adjudication_status"] = "human_spot_check"
    resolution["target_review_status"] = "human_review_required"
    knowledge["consensus_application"]["final_review_status_counts"] = {
        "ai_consensus_reviewed": 2,
        "human_review_required": 1,
    }
    knowledge["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(knowledge)
    )
    knowledge_path, relations_path = _projection_paths(tmp_path, knowledge)

    with pytest.raises(ValueError, match="every live claim"):
        run(
            knowledge_path=knowledge_path,
            relations_path=relations_path,
            output_dir=tmp_path / "out",
            store=_NeverStore(),
            openai_client=_NeverClient(),
            claude_client=_NeverClient(),
            apply=False,
            force=False,
        )


def test_runner_rejects_relations_from_another_aggregate_before_db_or_model(
    tmp_path,
):
    knowledge_path, relations_path = _projection_paths(
        tmp_path, _authenticated_knowledge()
    )
    relations = json.loads(relations_path.read_text(encoding="utf-8"))
    relations["generation"]["source_knowledge_sha256"] = "0" * 64
    relations_path.write_text(json.dumps(relations), encoding="utf-8")

    with pytest.raises(ValueError, match="exact reviewed aggregate"):
        run(
            knowledge_path=knowledge_path,
            relations_path=relations_path,
            output_dir=tmp_path / "out",
            store=_NeverStore(),
            openai_client=_NeverClient(),
            claude_client=_NeverClient(),
            apply=False,
            force=False,
        )


def test_runner_rejects_invalid_reviewed_relation_before_db_or_model(tmp_path):
    knowledge_path, relations_path = _projection_paths(
        tmp_path, _authenticated_knowledge()
    )
    relations = json.loads(relations_path.read_text(encoding="utf-8"))
    relations["result"] = {
        "reviewed_relations": [{
            "candidate_id": "XSR-MISSING",
            "source_claim_id": "CL-404",
            "target_claim_id": "CL-A",
            "relation_type": "supports",
            "review_status": "ai_consensus",
        }]
    }
    relations_path.write_text(json.dumps(relations), encoding="utf-8")

    with pytest.raises(ValueError, match="relation_endpoint_missing"):
        run(
            knowledge_path=knowledge_path,
            relations_path=relations_path,
            output_dir=tmp_path / "out",
            store=_NeverStore(),
            openai_client=_NeverClient(),
            claude_client=_NeverClient(),
            apply=False,
            force=False,
        )
