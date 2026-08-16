from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.research_batch import (
    ResearchBatchValidationError,
    merge_reviewed_packages,
    validate_research_batch,
)
from backend.pipeline.research_batch_runner import (
    artifact_paths,
    build_command_plan,
    reviewed_package_paths,
)


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


def test_command_plan_reuses_explicit_reviewed_package(tmp_path: Path) -> None:
    batch = _batch()
    batch["reviewed_package_reuse"] = {"讲道甲": "output/prior/甲.json"}
    plan = build_command_plan(
        batch, transcript_dir=tmp_path / "transcripts", output_root=tmp_path / "output",
        force=False,
    )
    assert len(plan) == 4
    assert {row["transcript_id"] for row in plan} == {"讲道乙"}
    paths = reviewed_package_paths(batch, output_root=tmp_path / "output")
    assert paths[0].as_posix().endswith("/output/prior/甲.json")
    assert paths[1] == artifact_paths(tmp_path / "output", "讲道乙")["reviewed"]


def test_batch_rejects_reuse_for_transcript_outside_batch() -> None:
    batch = _batch()
    batch["reviewed_package_reuse"] = {"讲道丙": "output/prior/丙.json"}
    with pytest.raises(ResearchBatchValidationError, match="outside the batch"):
        validate_research_batch(batch)


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


def test_merge_applies_only_source_bound_fidelity_correction(tmp_path: Path) -> None:
    batch = _batch()
    first = _package(tmp_path / "a.json", "讲道甲", "A")
    second = _package(tmp_path / "b.json", "讲道乙", "B")
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["source_fragments"][0]["verbatim_excerpt"] = "教授明确说：因信成为义。"
    payload["evidence_steps"][0]["source_fragment_ids"] = ["FR-A"]
    first.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    batch["source_fidelity_corrections"] = [
        {
            "claim_id": "CL-A",
            "replacement_title": "因信成为义",
            "reason": "自动摘要误写为称义",
            "verbatim_basis": "因信成为义",
        }
    ]

    merged = merge_reviewed_packages(batch, [first, second])

    claim = next(row for row in merged["claims"] if row["claim_id"] == "CL-A")
    assert claim["title"] == "因信成为义"
    assert claim["source_fidelity_correction"]["original_title"] == "主张"
    assert merged["source_fidelity_corrections"][0]["approval_status"] == "not_human_approved"


def test_merge_rejects_fidelity_correction_without_verbatim_support(tmp_path: Path) -> None:
    batch = _batch()
    batch["source_fidelity_corrections"] = [
        {
            "claim_id": "CL-A",
            "replacement_title": "因信成为义",
            "reason": "自动摘要误写",
            "verbatim_basis": "逐字稿里不存在",
        }
    ]
    first = _package(tmp_path / "a.json", "讲道甲", "A")
    second = _package(tmp_path / "b.json", "讲道乙", "B")

    with pytest.raises(ResearchBatchValidationError, match="not supported by a claim fragment"):
        merge_reviewed_packages(batch, [first, second])
