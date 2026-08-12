from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.research_batch import (
    ResearchBatchValidationError,
    merge_reviewed_packages,
    validate_research_batch,
)
from backend.pipeline.research_batch_runner import artifact_paths, build_command_plan


def _batch() -> dict:
    return {
        "schema_version": "wang_research_batch_v1",
        "batch_id": "RB-TEST-01",
        "purpose": "test",
        "semantic_assumption": "none",
        "transcript_ids": ["讲道甲", "讲道乙"],
        "candidate_generation_policy": {
            "derive_after_independent_extraction": True,
            "allow_unassigned_material": True,
        },
        "models": {
            "extraction": "gpt-5.6-sol",
            "independent_review": "claude-sonnet-5",
            "adjudicator": "gpt-5.6-sol",
        },
    }


def _package(path: Path, transcript_id: str, suffix: str) -> Path:
    payload = {
        "schema_version": "wang_shared_knowledge_v1.2",
        "source_documents": [
            {"source_id": f"SRC-{suffix}", "transcript_id": transcript_id}
        ],
        "source_fragments": [
            {"fragment_id": f"FR-{suffix}", "source_id": f"SRC-{suffix}"}
        ],
        "questions": [],
        "position_nodes": [],
        "observations": [],
        "evidence_steps": [
            {"evidence_step_id": f"E-{suffix}", "statement": "证据"}
        ],
        "claims": [
            {"claim_id": f"CL-{suffix}", "title": "主张", "evidence_step_ids": [f"E-{suffix}"]}
        ],
        "knowledge_relations": [
            {
                "relation_id": f"ER-{suffix}", "from_id": f"E-{suffix}",
                "to_id": f"CL-{suffix}", "relation_type": "supports",
            }
        ],
        "claim_relations": [],
        "extraction": {"fingerprint_sha256": f"extract-{suffix}"},
        "consensus_application": {
            "approval_status": "not_human_approved",
            "adjudication_fingerprint": f"adjudicate-{suffix}",
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_batch_rejects_preassigned_topic() -> None:
    batch = _batch()
    batch["target_topic_id"] = "TOPIC-COVENANT"
    with pytest.raises(ResearchBatchValidationError, match="cannot pre-assign topics"):
        validate_research_batch(batch)


def test_command_plan_keeps_each_transcript_independent(tmp_path: Path) -> None:
    batch = _batch()
    plan = build_command_plan(
        batch, transcript_dir=tmp_path / "transcripts", output_root=tmp_path / "output",
        force=False,
    )
    assert len(plan) == 8
    assert [row["stage"] for row in plan[:4]] == ["extract", "review", "adjudicate", "apply"]
    assert plan[0]["transcript_id"] == "讲道甲"
    assert plan[4]["transcript_id"] == "讲道乙"
    assert "讲道甲" in plan[0]["command"]
    assert "讲道乙" not in plan[0]["command"]
    assert artifact_paths(tmp_path / "output", "讲道甲")["reviewed"].name.endswith(
        ".reviewed-candidate.json"
    )


def test_merge_preserves_unassigned_material_without_topics(tmp_path: Path) -> None:
    batch = _batch()
    first = _package(tmp_path / "a.json", "讲道甲", "A")
    second = _package(tmp_path / "b.json", "讲道乙", "B")
    merged = merge_reviewed_packages(batch, [first, second])
    assert merged["batch"]["selection_is_not_classification"] is True
    assert merged["topic_candidates"] == []
    assert merged["knowledge_routes"] == []
    assert merged["candidate_generation"]["status"] == "pending_cross_sermon_comparison"
    assert [row["transcript_id"] for row in merged["lineage"]] == ["讲道甲", "讲道乙"]
    assert merged["summary"]["claims"] == 2
