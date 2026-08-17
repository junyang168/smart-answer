from backend.pipeline.research_batch_validation import compute_validation_metrics


def test_metrics_calculate_counts_coverage_and_overlap_without_model_summary():
    knowledge = {
        "batch": {"batch_id": "RB-TEST"},
        "source_documents": [{"id": "S1"}],
        "claims": [{"claim_id": "C1"}, {"claim_id": "C2"}, {"claim_id": "C3"}],
        "claim_relations": [
            {
                "relation_type": "supports",
                "review_status": "ai_consensus",
                "review_artifact_id": "XSR-1",
            },
            {"relation_type": "answers"},
        ],
    }
    candidates = {
        "final": {
            "candidate_plans": [
                {
                    "axis": "scripture",
                    "title": "short",
                    "scripture_target_id": "SCRIPTURE-Matt-5",
                    "sections": [{"claim_ids": ["C1"]}],
                },
                {
                    "axis": "scripture",
                    "title": "long",
                    "scripture_target_id": "SCRIPTURE-Matt-5",
                    "sections": [{"claim_ids": ["C1", "C2"]}],
                },
            ],
            "summary": "incorrect prose count",
        }
    }
    relations = {
        "result": {
            "reviewed_relations": [{"relation_type": "supports"}],
            "unassigned_claim_ids": ["C3"],
            "summary": {"human_review_required": 1},
        }
    }

    result = compute_validation_metrics(knowledge, candidates, relations)

    projection = result["candidate_projection"]
    assert projection["axis_counts"] == {"scripture": 2}
    assert projection["unique_assigned_claim_count"] == 2
    assert projection["claim_placement_count"] == 3
    assert projection["multi_plan_claim_count"] == 1
    assert projection["unassigned_claim_ids"] == ["C3"]
    assert projection["overlap_findings"][0]["smaller_plan_coverage"] == 1.0
    assert result["cross_sermon_relations"]["relation_type_counts"] == {"supports": 1}
    assert result["cross_sermon_relations"]["human_review_item_count"] == 1
    assert result["integrated_claim_graph"] == {
        "relation_count": 2,
        "relation_type_counts": {"answers": 1, "supports": 1},
        "review_status_counts": {"ai_consensus": 1, "legacy_reviewed": 1},
        "accepted_cross_sermon_relation_count": 1,
    }
