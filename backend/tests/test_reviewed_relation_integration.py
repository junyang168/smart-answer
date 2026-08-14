from __future__ import annotations

from backend.pipeline.reviewed_relation_integration import (
    build_reviewed_relation_integration,
    merge_relation_increment,
)


def _base() -> dict:
    return {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "BASE",
        "claims": [
            {"claim_id": "CL-1", "statement": "一", "claim_type": "explicit_claim"},
            {"claim_id": "CL-2", "statement": "二", "claim_type": "explicit_claim"},
        ],
        "evidence_steps": [
            {"evidence_step_id": "E-1", "statement": "证据"},
            {"evidence_step_id": "E-2", "statement": "证据"},
        ],
        "claim_relations": [],
        "claim_relation_constraints": [],
    }


def _artifact(target: str = "CL-2") -> dict:
    return {
        "result": {
            "reviewed_relations": [
                {
                    "candidate_id": "XSR-1",
                    "source_claim_id": "CL-1",
                    "target_claim_id": target,
                    "source_evidence_step_ids": ["E-1"],
                    "target_evidence_step_ids": ["E-2"],
                    "relation_type": "extends",
                    "review_status": "ai_consensus",
                }
            ],
            "outcomes": [
                {"candidate_id": "XSR-HUMAN", "status": "human_review_required"}
            ],
        }
    }


def test_builds_candidate_and_keeps_human_disagreement_out_of_increment() -> None:
    result = build_reviewed_relation_integration(_artifact(), _base())
    assert result["status"] == "ready_with_human_queue"
    assert result["summary"] == {
        "accepted_relation_records": 1,
        "human_review_items": 1,
        "errors": 0,
        "warnings": 0,
    }
    assert len(result["incremental_package"]["claim_relations"]) == 1
    assert len(result["candidate_snapshot"]["claim_relations"]) == 1


def test_missing_relation_endpoint_blocks_integration() -> None:
    result = build_reviewed_relation_integration(_artifact("CL-MISSING"), _base())
    assert result["status"] == "blocked"
    assert "relation_endpoint_missing" in {row["code"] for row in result["findings"]}


def test_candidate_merge_is_idempotent() -> None:
    result = build_reviewed_relation_integration(_artifact(), _base())
    first = result["candidate_snapshot"]
    second, findings = merge_relation_increment(first, result["incremental_package"])
    assert findings == []
    assert len(second["claim_relations"]) == 1
