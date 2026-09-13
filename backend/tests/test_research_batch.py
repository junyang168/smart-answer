from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.knowledge_consensus_applier import (
    ConsensusApplicationError,
    reviewed_candidate_artifact_sha256,
    validate_reviewed_candidate_artifact,
)

from backend.pipeline.research_batch import (
    ResearchBatchValidationError,
    merge_reviewed_packages,
    validate_research_batch,
)
from backend.pipeline.research_batch_runner import (
    artifact_paths,
    build_command_plan,
    failed_member_runs,
    resolve_transcript_dir,
    authoritative_members_with_untitled_leading_sections,
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
    claim_id = f"CL-{suffix}"
    payload = {
        "schema_version": "wang_shared_knowledge_v1.2",
        "complete": True,
        "source_documents": [
            {"source_id": f"SRC-{suffix}", "transcript_id": transcript_id}
        ],
        "source_fragments": [
            {"fragment_id": f"FR-{suffix}", "source_id": f"SRC-{suffix}"}
        ],
        "questions": [],
        "position_nodes": [],
        "observations": [
            {"observation_id": f"O-{suffix}", "source_fragment_ids": [f"FR-{suffix}"]}
        ],
        "evidence_steps": [
            {
                "evidence_step_id": f"E-{suffix}", "statement": "证据",
                "source_fragment_ids": [f"FR-{suffix}"],
                "produced_claim_ids": [claim_id],
            }
        ],
        "claims": [
            {
                "claim_id": claim_id, "title": "主张",
                "evidence_step_ids": [f"E-{suffix}"],
                "review_status": "ai_consensus_reviewed",
                "reviewed_by": "claude-sonnet-5",
                "reviewed_at": "2026-09-12T00:00:00+00:00",
                "review_note": "独立 AI 复审：pass；仲裁：not_required",
            }
        ],
        "knowledge_relations": [
            {
                "relation_id": f"ER-{suffix}", "from_id": f"O-{suffix}",
                "to_id": f"E-{suffix}", "relation_type": "supports",
            }
        ],
        "claim_relations": [],
        "extraction": {"fingerprint_sha256": f"extract-{suffix}"},
        "consensus_application": {
            "schema_version": "wang_ai_consensus_application_v2",
            "scope_kind": "source_scoped",
            "approval_status": "not_human_approved",
            "adjudication_fingerprint": f"adjudicate-{suffix}",
            "review_completion": "complete",
            "review_artifact_sha256": "a" * 64,
            "review_fingerprint": f"review-{suffix}",
            "adjudication_artifact_sha256": "b" * 64,
            "overrides_artifact_sha256": "c" * 64,
            "applied_claim_ids": [],
            "merged_claim_ids": {},
            "final_review_status_counts": {"ai_consensus_reviewed": 1},
            "review_resolutions": [{
                "schema_version": "wang_claim_ai_review_provenance_v1",
                "claim_id": claim_id,
                "independent_review_decision": "pass",
                "adjudication_status": "not_required",
                "target_review_status": "ai_consensus_reviewed",
                "reviewer_id": "claude-sonnet-5",
                "reason": "独立 AI 复审：pass；仲裁：not_required",
                "approval_status": "not_human_approved",
            }],
        },
    }
    payload["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(payload)
    )
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _reseal(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(payload)
    )
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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


def test_command_plan_applies_section_limit_to_only_the_named_member(tmp_path: Path) -> None:
    batch = _batch()
    batch["extraction_max_section_sentences"] = {"讲道甲": 180}
    plan = build_command_plan(
        batch, transcript_dir=tmp_path / "transcripts", output_root=tmp_path / "output",
        force=False,
    )
    extracts = {
        row["transcript_id"]: row["command"] for row in plan if row["stage"] == "extract"
    }
    assert extracts["讲道甲"][extracts["讲道甲"].index("--max-section-sentences") + 1] == "180"
    assert "--max-section-sentences" not in extracts["讲道乙"]


def test_batch_rejects_section_limit_for_member_outside_batch() -> None:
    batch = _batch()
    batch["extraction_max_section_sentences"] = {"讲道丙": 180}
    with pytest.raises(ResearchBatchValidationError, match="outside the batch"):
        validate_research_batch(batch)


def test_visual_source_attestation_is_validated_and_forwarded(tmp_path: Path) -> None:
    batch = _batch()
    batch["visual_source_attestations"] = {
        "讲道甲": {"S0003/V01": "a" * 64}
    }

    validate_research_batch(batch)
    plan = build_command_plan(
        batch,
        transcript_dir=tmp_path / "transcripts",
        output_root=tmp_path / "output",
        force=False,
        extraction_backend="codex-subscription",
    )
    command = next(
        row["command"]
        for row in plan
        if row["stage"] == "extract" and row["transcript_id"] == "讲道甲"
    )
    index = command.index("--visual-source-attestation")
    assert command[index + 1] == f"S0003/V01={'a' * 64}"

    batch["visual_source_attestations"] = {
        "讲道丙": {"S0003/V01": "a" * 64}
    }
    with pytest.raises(ResearchBatchValidationError, match="outside the batch"):
        validate_research_batch(batch)


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
        anthropic_backend="claude-subscription",
        write_back_generated_subtitles=True,
        subtitle_user_id="editor@example.org",
    )
    extracts = {
        row["transcript_id"]: row["command"] for row in plan if row["stage"] == "extract"
    }
    assert extracts["讲道甲"][extracts["讲道甲"].index("--backend") + 1] == (
        "codex-subscription"
    )
    assert extracts["讲道甲"][
        extracts["讲道甲"].index("--fallback-max-section-sentences") + 1
    ] == "125"
    assert extracts["讲道乙"][
        extracts["讲道乙"].index("--fallback-max-section-sentences") + 1
    ] == "125"
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
    reviews = {
        row["transcript_id"]: row["command"] for row in plan if row["stage"] == "review"
    }
    assert "backend.pipeline.claim_layer_review_batch_runner" in reviews["讲道甲"]
    assert reviews["讲道甲"][reviews["讲道甲"].index("--backend") + 1] == (
        "claude-subscription"
    )
    assert reviews["讲道甲"][reviews["讲道甲"].index("--batch-size") + 1] == "20"
    assert reviews["讲道甲"][
        reviews["讲道甲"].index("--spot-check-percent") + 1
    ] == "0"
    assert adjudications["讲道甲"][
        adjudications["讲道甲"].index("--claude-backend") + 1
    ] == "claude-subscription"
    assert "--write-back-generated-subtitles" in extracts["讲道甲"]
    assert extracts["讲道甲"][extracts["讲道甲"].index("--subtitle-user-id") + 1] == (
        "editor@example.org"
    )
    assert "--write-back-generated-subtitles" in extracts["讲道乙"]
    assert extracts["讲道乙"][extracts["讲道乙"].index("--subtitle-user-id") + 1] == (
        "editor@example.org"
    )


def test_batch_may_explicitly_request_a_review_spot_check_rate(tmp_path: Path) -> None:
    batch = _batch()
    batch["review_spot_check_percent"] = 7

    plan = build_command_plan(
        batch,
        transcript_dir=tmp_path,
        output_root=tmp_path / "output",
        force=False,
    )
    review = next(row["command"] for row in plan if row["stage"] == "review")

    assert review[review.index("--spot-check-percent") + 1] == "7"


@pytest.mark.parametrize("value", [-1, 101, 2.5, True])
def test_review_spot_check_rate_is_bounded(value) -> None:
    batch = _batch()
    batch["review_spot_check_percent"] = value

    with pytest.raises(ResearchBatchValidationError, match="review_spot_check_percent"):
        validate_research_batch(batch)


def test_batch_source_resolution_prefers_published_regardless_of_argument_order(
    tmp_path: Path,
) -> None:
    review = tmp_path / "script_review"
    published = tmp_path / "script_published"
    review.mkdir()
    published.mkdir()
    member = {"key": "讲道甲", "source_type": "sermon_transcript"}
    (review / "讲道甲.json").write_text(
        json.dumps(
            [
                {"index": "subtitle-review", "type": "subtitle", "text": "## Review 标题"},
                {"index": 1, "text": "review 正文不得参与。"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (published / "讲道甲.json").write_text(
        json.dumps(
            {
                "metadata": {"status": "published"},
                "script": [{"index": 1, "text": "published 权威正文。"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert resolve_transcript_dir(member, [review, published]) == published
    assert resolve_transcript_dir(member, [published, review]) == published
    assert authoritative_members_with_untitled_leading_sections(
        [member], [review, published]
    ) == ["讲道甲"]


def test_a_failed_current_member_blocks_merge_even_when_old_artifacts_exist() -> None:
    assert failed_member_runs(
        {
            "讲道甲": {"status": "completed"},
            "讲道乙": {"status": "failed", "failed_stage": "review"},
            "讲道丙": {"status": "interrupted", "failed_stage": "extract"},
        }
    ) == ["讲道丙", "讲道乙"]


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
    assert len(merged["consensus_application"]["review_resolutions"]) == 2
    validate_reviewed_candidate_artifact(merged)


def test_merged_candidate_rejects_resealed_member_resolution_mismatch(
    tmp_path: Path,
) -> None:
    merged = merge_reviewed_packages(
        _batch(),
        [
            _package(tmp_path / "a.json", "讲道甲", "A"),
            _package(tmp_path / "b.json", "讲道乙", "B"),
        ],
    )
    merged["consensus_application"]["review_resolutions"][0][
        "source_review_artifact_sha256"
    ] = "f" * 64
    merged["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(merged)
    )

    with pytest.raises(ConsensusApplicationError, match="member artifacts"):
        validate_reviewed_candidate_artifact(merged)


def test_merged_candidate_rejects_swapped_claim_source_bindings(
    tmp_path: Path,
) -> None:
    merged = merge_reviewed_packages(
        _batch(),
        [
            _package(tmp_path / "a.json", "讲道甲", "A"),
            _package(tmp_path / "b.json", "讲道乙", "B"),
        ],
    )
    first, second = merged["consensus_application"]["review_resolutions"]
    source_fields = [
        "source_transcript_id",
        "source_reviewed_candidate_artifact_sha256",
        "source_review_artifact_sha256",
        "source_review_fingerprint",
        "source_adjudication_artifact_sha256",
        "source_adjudication_fingerprint",
        "source_overrides_artifact_sha256",
    ]
    first_values = {field: first[field] for field in source_fields}
    second_values = {field: second[field] for field in source_fields}
    first.update(second_values)
    second.update(first_values)
    merged["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(merged)
    )

    with pytest.raises(
        ConsensusApplicationError, match="does not match claim evidence"
    ):
        validate_reviewed_candidate_artifact(merged)


def test_aggregate_scope_cannot_be_hidden_by_removing_batch_lineage(
    tmp_path: Path,
) -> None:
    merged = merge_reviewed_packages(
        _batch(),
        [
            _package(tmp_path / "a.json", "讲道甲", "A"),
            _package(tmp_path / "b.json", "讲道乙", "B"),
        ],
    )
    merged.pop("batch")
    merged.pop("lineage")
    merged["consensus_application"].pop("member_artifact_lineage")
    merged["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(merged)
    )

    with pytest.raises(ConsensusApplicationError, match="batch identity"):
        validate_reviewed_candidate_artifact(merged)


def test_aggregate_rejects_empty_batch_identity_after_reseal(tmp_path: Path) -> None:
    merged = merge_reviewed_packages(
        _batch(),
        [
            _package(tmp_path / "a.json", "讲道甲", "A"),
            _package(tmp_path / "b.json", "讲道乙", "B"),
        ],
    )
    merged["batch"] = {}
    merged["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(merged)
    )

    with pytest.raises(ConsensusApplicationError, match="batch identity"):
        validate_reviewed_candidate_artifact(merged)


def test_aggregate_rejects_duplicate_top_lineage_after_reseal(
    tmp_path: Path,
) -> None:
    merged = merge_reviewed_packages(
        _batch(),
        [
            _package(tmp_path / "a.json", "讲道甲", "A"),
            _package(tmp_path / "b.json", "讲道乙", "B"),
        ],
    )
    merged["lineage"].insert(0, dict(merged["lineage"][0]))
    merged["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(merged)
    )

    with pytest.raises(ConsensusApplicationError, match="merge-stage lineage"):
        validate_reviewed_candidate_artifact(merged)


def test_aggregate_rejects_duplicate_source_transcript_identity_after_reseal(
    tmp_path: Path,
) -> None:
    merged = merge_reviewed_packages(
        _batch(),
        [
            _package(tmp_path / "a.json", "讲道甲", "A"),
            _package(tmp_path / "b.json", "讲道乙", "B"),
        ],
    )
    merged["source_documents"][1]["transcript_id"] = "讲道甲"
    merged["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(merged)
    )

    with pytest.raises(ConsensusApplicationError, match="source transcript identities"):
        validate_reviewed_candidate_artifact(merged)


def test_merge_rejects_tampered_reviewed_package_before_id_migration(
    tmp_path: Path,
) -> None:
    batch = _batch()
    first = _package(tmp_path / "a.json", "讲道甲", "A")
    second = _package(tmp_path / "b.json", "讲道乙", "B")
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["claims"][0]["title"] = "graph-valid tampering"
    payload["knowledge_relations"][0]["relation_id"] = "XER001"
    first.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ResearchBatchValidationError, match="modified"):
        merge_reviewed_packages(batch, [first, second])


def test_merge_rejects_reviewed_package_without_final_self_seal(
    tmp_path: Path,
) -> None:
    batch = _batch()
    first = _package(tmp_path / "a.json", "讲道甲", "A")
    second = _package(tmp_path / "b.json", "讲道乙", "B")
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["consensus_application"].pop("artifact_sha256")
    first.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ResearchBatchValidationError, match="modified"):
        merge_reviewed_packages(batch, [first, second])


def test_merge_globalizes_legacy_relation_ids_before_duplicate_check(tmp_path: Path) -> None:
    batch = _batch()
    first = _package(tmp_path / "a.json", "讲道甲", "A")
    second = _package(tmp_path / "b.json", "讲道乙", "B")
    for path in (first, second):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["knowledge_relations"][0]["relation_id"] = "XER001"
        payload["claim_relations"] = [{
            "claim_relation_id": "XCR001",
            "from_id": payload["claims"][0]["claim_id"],
            "to_id": f"CL-{path.stem}-OTHER",
            "relation_type": "qualifies",
        }]
        payload["claims"].append({
            "claim_id": f"CL-{path.stem}-OTHER",
            "title": "另一主张",
            "evidence_step_ids": [payload["evidence_steps"][0]["evidence_step_id"]],
            "review_status": "ai_consensus_reviewed",
            "reviewed_by": "claude-sonnet-5",
            "reviewed_at": "2026-09-12T00:00:00+00:00",
            "review_note": "独立 AI 复审：pass；仲裁：not_required",
        })
        payload["evidence_steps"][0]["produced_claim_ids"].append(
            f"CL-{path.stem}-OTHER"
        )
        payload["consensus_application"]["review_resolutions"].append({
            "schema_version": "wang_claim_ai_review_provenance_v1",
            "claim_id": f"CL-{path.stem}-OTHER",
            "independent_review_decision": "pass",
            "adjudication_status": "not_required",
            "target_review_status": "ai_consensus_reviewed",
            "reviewer_id": "claude-sonnet-5",
            "reason": "独立 AI 复审：pass；仲裁：not_required",
            "approval_status": "not_human_approved",
        })
        payload["consensus_application"]["final_review_status_counts"] = {
            "ai_consensus_reviewed": 2
        }
        payload["consensus_application"]["artifact_sha256"] = (
            reviewed_candidate_artifact_sha256(payload)
        )
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    merged = merge_reviewed_packages(batch, [first, second])

    evidence_relation_ids = [row["relation_id"] for row in merged["knowledge_relations"]]
    claim_relation_ids = [row["claim_relation_id"] for row in merged["claim_relations"]]
    assert len(evidence_relation_ids) == len(set(evidence_relation_ids)) == 2
    assert len(claim_relation_ids) == len(set(claim_relation_ids)) == 2
    assert all(value.endswith("-XER001") for value in evidence_relation_ids)
    assert all(value.endswith("-XCR001") for value in claim_relation_ids)
    assert all(
        row["relation_id_namespace_migration"]["status"] == "applied"
        for row in merged["lineage"]
    )
    assert all(
        row["package_canonical_sha256"] != row["effective_package_sha256"]
        for row in merged["lineage"]
    )
    assert all(
        row["upstream_reviewed_candidate_artifact_sha256"]
        != row["reviewed_candidate_artifact_sha256"]
        for row in merged["lineage"]
    )


def test_merge_rejects_post_review_source_fidelity_correction(tmp_path: Path) -> None:
    batch = _batch()
    first = _package(tmp_path / "a.json", "讲道甲", "A")
    second = _package(tmp_path / "b.json", "讲道乙", "B")
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["source_fragments"][0]["verbatim_excerpt"] = "教授明确说：因信成为义。"
    payload["evidence_steps"][0]["source_fragment_ids"] = ["FR-A"]
    first.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _reseal(first)
    batch["source_fidelity_corrections"] = [
        {
            "claim_id": "CL-A",
            "replacement_title": "因信成为义",
            "reason": "自动摘要误写为称义",
            "verbatim_basis": "因信成为义",
        }
    ]

    with pytest.raises(ResearchBatchValidationError, match="after independent review are retired"):
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
