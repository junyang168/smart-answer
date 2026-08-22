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
    stages = ["extract", "cross_section", "review", "adjudicate", "apply", "ingest"]
    assert len(plan) == 2 * len(stages)
    assert [row["stage"] for row in plan[: len(stages)]] == stages
    assert plan[0]["transcript_id"] == "讲道甲"
    assert plan[len(stages)]["transcript_id"] == "讲道乙"
    assert "讲道甲" in plan[0]["command"]
    assert "讲道乙" not in plan[0]["command"]
    assert artifact_paths(tmp_path / "output", "讲道甲")["reviewed"].name.endswith(
        ".reviewed-candidate.json"
    )


def test_command_plan_propagates_subscription_and_governed_subtitle_writeback(
    tmp_path: Path,
) -> None:
    review = tmp_path / "script_review"
    published = tmp_path / "script_published"
    review.mkdir()
    published.mkdir()
    (review / "讲道甲.json").write_text("[]", encoding="utf-8")
    (published / "讲道乙.json").write_text("[]", encoding="utf-8")

    plan = build_command_plan(
        _batch(), transcript_dir=[review, published], output_root=tmp_path / "output",
        force=False, extraction_backend="codex-subscription",
        write_back_generated_subtitles=True,
        subtitle_user_id="editor@example.org",
    )
    extracts = {
        row["transcript_id"]: row["command"] for row in plan if row["stage"] == "extract"
    }
    assert extracts["讲道甲"][extracts["讲道甲"].index("--backend") + 1] == (
        "codex-subscription"
    )
    cross_sections = {
        row["transcript_id"]: row["command"]
        for row in plan if row["stage"] == "cross_section"
    }
    adjudications = {
        row["transcript_id"]: row["command"]
        for row in plan if row["stage"] == "adjudicate"
    }
    assert cross_sections["讲道甲"][cross_sections["讲道甲"].index("--backend") + 1] == (
        "codex-subscription"
    )
    assert adjudications["讲道甲"][
        adjudications["讲道甲"].index("--openai-backend") + 1
    ] == "codex-subscription"
    assert "--write-back-generated-subtitles" in extracts["讲道甲"]
    assert extracts["讲道甲"][extracts["讲道甲"].index("--subtitle-user-id") + 1] == (
        "editor@example.org"
    )
    assert "--write-back-generated-subtitles" not in extracts["讲道乙"]


def test_command_plan_reuses_explicit_reviewed_package(tmp_path: Path) -> None:
    batch = _batch()
    batch["reviewed_package_reuse"] = {"讲道甲": "output/prior/甲.json"}
    plan = build_command_plan(
        batch, transcript_dir=tmp_path / "transcripts", output_root=tmp_path / "output",
        force=False,
    )
    assert len(plan) == 6
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


def _notes_batch(manuscript: Path) -> dict:
    batch = _batch()
    batch["transcript_ids"] = ["讲道甲"]
    batch["sources"] = [
        {
            "source_id": "notes_manuscript:16章釋經",
            "source_path": str(manuscript),
            "source_type": "notes_manuscript",
            "title": "16章 - 彼得的認信",
        }
    ]
    return batch


def test_notes_manuscript_is_a_batch_member(tmp_path: Path) -> None:
    """A 母本 has no transcript directory, so it is addressed by path.

    Before this it could not be batched at all: the plan was built from
    `transcript_ids` and always passed `--ids`, so all three chapter-16 母本
    had to be driven stage by stage from a terminal.
    """

    manuscript = tmp_path / "final.md"
    manuscript.write_text("# 一\n\n正文\n", encoding="utf-8")
    batch = _notes_batch(manuscript)
    validate_research_batch(batch)
    plan = build_command_plan(
        batch, transcript_dir=tmp_path / "transcripts", output_root=tmp_path / "output",
        force=False,
    )
    notes = [row for row in plan if row["transcript_id"] == "notes_manuscript:16章釋經"]
    extract = next(row for row in notes if row["stage"] == "extract")["command"]
    assert "--source-manifest" in extract
    assert "--ids" not in extract

    sermon = next(row for row in plan if row["transcript_id"] == "讲道甲" and row["stage"] == "extract")
    assert "--ids" in sermon["command"]
    assert "--source-manifest" not in sermon["command"]


def test_every_member_gets_a_cross_section_stage(tmp_path: Path) -> None:
    """Sectioned extraction splits a source; something has to put it back.

    `cross_section` was not a stage, and the consequence is on disk: the 母本
    extracted on 08-19 has its cross-section relations and the sermon extracted
    two hours later does not.
    """

    manuscript = tmp_path / "final.md"
    manuscript.write_text("# 一\n\n正文\n", encoding="utf-8")
    plan = build_command_plan(
        _notes_batch(manuscript), transcript_dir=tmp_path / "transcripts",
        output_root=tmp_path / "output", force=False,
    )
    by_member: dict[str, list[str]] = {}
    for row in plan:
        by_member.setdefault(row["transcript_id"], []).append(row["stage"])
    assert by_member and all("cross_section" in stages for stages in by_member.values())

    # Downstream reads the cross-section package, never the raw extraction, so
    # a skipped cross-section cannot silently become the published material.
    for member, stages in by_member.items():
        paths = artifact_paths(tmp_path / "output", member)
        review = next(
            row for row in plan
            if row["transcript_id"] == member and row["stage"] == "review"
        )
        assert str(paths["cross_section"]) in review["command"]
        assert str(paths["package"]) not in review["command"]


def test_ingest_supersedes_and_only_applies_when_asked(tmp_path: Path) -> None:
    """Ingest is a stage, and writing to the store stays opt-in.

    One source reached PostgreSQL from its raw extraction package, having
    skipped adjudication and consensus, because ingest lived outside the
    orchestrator and was typed by hand.
    """

    plan = build_command_plan(
        _batch(), transcript_dir=tmp_path / "transcripts", output_root=tmp_path / "output",
        force=False,
    )
    ingest = next(row for row in plan if row["stage"] == "ingest")["command"]
    assert "backend.pipeline.extraction_supersede_runner" in ingest
    assert str(artifact_paths(tmp_path / "output", "讲道甲")["reviewed"]) in ingest
    assert "--apply" not in ingest

    applied = build_command_plan(
        _batch(), transcript_dir=tmp_path / "transcripts", output_root=tmp_path / "output",
        force=False, apply_ingest=True,
    )
    assert "--apply" in next(row for row in applied if row["stage"] == "ingest")["command"]


def test_batch_rejects_a_source_id_that_shadows_a_transcript(tmp_path: Path) -> None:
    manuscript = tmp_path / "final.md"
    manuscript.write_text("正文\n", encoding="utf-8")
    batch = _notes_batch(manuscript)
    batch["sources"][0]["source_id"] = "讲道甲"
    with pytest.raises(ResearchBatchValidationError, match="cannot repeat"):
        validate_research_batch(batch)


def test_batch_still_needs_at_least_one_member() -> None:
    batch = _batch()
    batch["transcript_ids"] = []
    with pytest.raises(ResearchBatchValidationError, match="at least one"):
        validate_research_batch(batch)
