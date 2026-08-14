from backend.pipeline.passage_knowledge_slice import (
    Passage,
    build_passage_slice,
    reference_overlaps,
)


def test_reference_overlap_handles_chinese_and_english_ranges() -> None:
    passage = Passage("Matt", 16, 21, 23)
    assert reference_overlaps("太16:16-23", passage)
    assert reference_overlaps("Matt.16:22", passage)
    assert not reference_overlaps("太16:24-27", passage)
    assert not reference_overlaps("太17:21-23", passage)


def test_passage_slice_keeps_only_transitive_provenance() -> None:
    package = {
        "package_id": "PKG",
        "source_documents": [
            {"source_id": "SRC-1"},
            {"source_id": "SRC-2"},
        ],
        "source_fragments": [
            {"fragment_id": "FR-1", "source_id": "SRC-1"},
            {"fragment_id": "FR-2", "source_id": "SRC-2"},
            {"fragment_id": "FR-O", "source_id": "SRC-1"},
        ],
        "observations": [
            {"observation_id": "OBS-1", "scripture_refs": ["太16:22"], "source_fragment_ids": ["FR-O"]}
        ],
        "claims": [
            {"claim_id": "CL-1", "scripture_refs": ["太16:16-23"], "evidence_step_ids": ["E-1"]},
            {"claim_id": "CL-2", "scripture_refs": ["太16:24"], "evidence_step_ids": ["E-2"]},
            {"claim_id": "CL-WIDE", "title": "宽范围背景", "scripture_refs": ["太16:1-23"], "evidence_step_ids": []},
        ],
        "evidence_steps": [
            {"evidence_step_id": "E-1", "produced_claim_ids": ["CL-1", "CL-2"], "source_fragment_ids": ["FR-1"], "support_eligibility": "eligible"},
            {"evidence_step_id": "E-2", "produced_claim_ids": ["CL-2"], "source_fragment_ids": ["FR-2"], "support_eligibility": "eligible"},
        ],
    }

    result = build_passage_slice(package, Passage("Matt", 16, 21, 23))

    assert [row["claim_id"] for row in result["claims"]] == ["CL-1"]
    assert [row["claim_id"] for row in result["contextual_claim_leads"]] == ["CL-WIDE"]
    assert [row["evidence_step_id"] for row in result["evidence_steps"]] == ["E-1"]
    assert result["evidence_steps"][0]["produced_claim_ids"] == ["CL-1"]
    assert {row["fragment_id"] for row in result["source_fragments"]} == {"FR-1", "FR-O"}
    assert [row["source_id"] for row in result["source_documents"]] == ["SRC-1"]
    assert result["passage_slice"] == {
        "passage": "Matt16:21–23",
        "selection_policy": "structured_scripture_reference_overlap",
        "covered_verses": [21, 22, 23],
        "missing_verses": [],
        "requires_model_extraction": False,
    }
