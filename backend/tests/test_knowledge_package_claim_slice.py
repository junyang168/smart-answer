from backend.pipeline.knowledge_package_claim_slice_runner import slice_package


def test_slice_keeps_only_selected_claim_provenance() -> None:
    package = {
        "package_id": "PKG",
        "source_documents": [
            {"source_id": "S1", "transcript_id": "T1"},
            {"source_id": "S2", "transcript_id": "T2"},
        ],
        "source_fragments": [
            {"fragment_id": "F1", "source_id": "S1"},
            {"fragment_id": "F2", "source_id": "S2"},
            {"fragment_id": "FP1", "source_id": "S1"},
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "E1",
                "source_fragment_ids": ["F1"],
                "produced_claim_ids": ["C1", "C2"],
            },
            {"evidence_step_id": "E2", "source_fragment_ids": ["F2"]},
        ],
        "claims": [
            {
                "claim_id": "C1",
                "evidence_step_ids": ["E1"],
                "opposed_position_ids": ["P1"],
            },
            {"claim_id": "C2", "evidence_step_ids": ["E2"]},
        ],
        "position_nodes": [
            {"position_id": "P1", "source_fragment_ids": ["FP1"]},
            {"position_id": "P2", "source_fragment_ids": ["F2"]},
        ],
        "claim_relations": [
            {"source_claim_id": "C1", "target_claim_id": "C2"},
        ],
    }

    result = slice_package(package, {"C1"})

    assert [row["claim_id"] for row in result["claims"]] == ["C1"]
    assert [row["evidence_step_id"] for row in result["evidence_steps"]] == ["E1"]
    assert result["evidence_steps"][0]["produced_claim_ids"] == ["C1"]
    assert [row["fragment_id"] for row in result["source_fragments"]] == ["F1", "FP1"]
    assert [row["transcript_id"] for row in result["source_documents"]] == ["T1"]
    assert [row["position_id"] for row in result["position_nodes"]] == ["P1"]
    assert result["claim_relations"] == []


def test_slice_rejects_unknown_claim() -> None:
    try:
        slice_package({"claims": []}, {"missing"})
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("unknown claim id should fail")
