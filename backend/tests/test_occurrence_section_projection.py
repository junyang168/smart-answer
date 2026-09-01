from __future__ import annotations

import pytest

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.matthew16_viewpoint_pilot import PASSAGE_UNITS
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
from backend.pipeline.occurrence_section_projection import (
    build_occurrence_section_projection,
    projection_admissions_by_claim,
    verify_projection_artifact,
)


def _fixture():
    claims = [
        {
            "claim_id": "C-SEED",
            "statement": "经文直接结论",
            "claim_type": "interpretive_judgment",
            "scripture_refs": ["太16:18"],
            "evidence_step_ids": ["E-SEED"],
        },
        {
            "claim_id": "C-SAME",
            "statement": "同段论证",
            "claim_type": "reasoning",
            "scripture_refs": [],
            "evidence_step_ids": ["E-SAME"],
        },
        {
            "claim_id": "C-OTHER",
            "statement": "另一段材料",
            "claim_type": "reasoning",
            "scripture_refs": [],
            "evidence_step_ids": ["E-OTHER"],
        },
    ]
    scope_claims = [
        {
            "claim_id": row["claim_id"],
            "pinned_claim_revision": 1,
            "claim_revision_sha256": semantic_record_sha(
                ClaimRecord.model_validate(row)
            ),
            "source_id": "SRC-1",
            "scripture_refs": row["scripture_refs"],
        }
        for row in claims
    ]
    steps = [
        {
            "evidence_step_id": "E-SEED",
            "source_fragment_id": "F-SEED",
        },
        {
            "evidence_step_id": "E-SAME",
            "source_fragment_id": "F-SAME",
        },
        {
            "evidence_step_id": "E-OTHER",
            "source_fragment_id": "F-OTHER",
        },
    ]
    fragments = [
        {
            "fragment_id": "F-SEED",
            "source_id": "SRC-1",
            "source_sha256": "a" * 64,
            "paragraph_key": "S0001",
            "verbatim_excerpt": "seed",
            "anchor_state": "source_version_bound",
        },
        {
            "fragment_id": "F-SAME",
            "source_id": "SRC-1",
            "source_sha256": "a" * 64,
            "paragraph_key": "S0002",
            "verbatim_excerpt": "same",
            "anchor_state": "source_version_bound",
        },
        {
            "fragment_id": "F-OTHER",
            "source_id": "SRC-1",
            "source_sha256": "a" * 64,
            "paragraph_key": "S0003",
            "verbatim_excerpt": "other",
            "anchor_state": "source_version_bound",
        },
    ]
    documents = [
        {
            "source_id": "SRC-1",
            "source_type": "sermon_transcript",
            "source_sha256": "a" * 64,
        }
    ]
    plans = [
        (
            "one/section-plans/source.json",
            {
                "source_sha256": "a" * 64,
                "origin": "generated_subtitles",
                "sections": [
                    {"index": 1, "start": 0, "end": 2, "title": "one"},
                    {"index": 2, "start": 2, "end": 3, "title": "two"},
                ],
            },
        )
    ]
    return scope_claims, claims, steps, fragments, documents, plans


def _build(*, mutate_fragment=None, plans=None):
    scope, claims, steps, fragments, documents, default_plans = _fixture()
    if mutate_fragment:
        fragments[1].update(mutate_fragment)
    return build_occurrence_section_projection(
        scope_claims=scope,
        scope_artifact_sha256="b" * 64,
        parent_claim_manifest_sha256="c" * 64,
        passage_units=PASSAGE_UNITS,
        claims=claims,
        evidence_steps=steps,
        source_fragments=fragments,
        source_documents=documents,
        section_plans=plans or default_plans,
    )


def test_occurrence_projection_inherits_only_inside_exact_source_section():
    result = _build()
    by_claim = {row["claim_id"]: row for row in result["claims"]}

    assert by_claim["C-SAME"]["projection_status"] == "proved_by_occurrence_section"
    admission = by_claim["C-SAME"]["admissions"][0]
    assert admission["passage_unit_ids"] == ["16:13-18"]
    assert admission["section_seed_claim_ids"] == ["C-SEED"]
    assert admission["evidence_step_id"] == "E-SAME"
    assert admission["source_fragment_id"] == "F-SAME"
    assert by_claim["C-OTHER"]["projection_status"] == "not_proved_by_occurrence_section"
    assert by_claim["C-OTHER"]["admissions"] == []
    assert result["policy"]["title_semantics_used"] is False
    assert result["model_calls_executed"] == 0
    assert result["master_data_mutations"] == 0
    verify_projection_artifact(result)


def test_stale_fragment_fails_closed_instead_of_inheriting_section():
    result = _build(mutate_fragment={"source_sha256": "c" * 64})
    row = next(item for item in result["claims"] if item["claim_id"] == "C-SAME")
    assert row["projection_status"] == "pending_missing_projection_input"
    assert row["reason_codes"] == ["fragment_source_sha_mismatch"]
    assert "C-SAME" not in projection_admissions_by_claim(result)


def test_section_labels_use_current_claim_revision_not_stale_parent_refs():
    scope, claims, steps, fragments, documents, plans = _fixture()
    claims[0]["revision"] = 2
    claims[0]["scripture_refs"] = []
    result = build_occurrence_section_projection(
        scope_claims=scope,
        scope_artifact_sha256="b" * 64,
        parent_claim_manifest_sha256="c" * 64,
        passage_units=PASSAGE_UNITS,
        claims=claims,
        evidence_steps=steps,
        source_fragments=fragments,
        source_documents=documents,
        section_plans=plans,
    )

    same = next(row for row in result["claims"] if row["claim_id"] == "C-SAME")
    seed = next(row for row in result["claims"] if row["claim_id"] == "C-SEED")
    assert seed["parent_scope_pin_status"] == "stale"
    assert same["projection_status"] == "not_proved_by_occurrence_section"


def test_duplicate_identical_plan_is_accepted_but_ambiguity_is_rejected():
    *_, plans = _fixture()
    duplicate = ("two/section-plans/source.json", dict(plans[0][1]))
    result = _build(plans=[plans[0], duplicate])
    assert result["section_plan_inventory"][0]["duplicate_locator_count"] == 2

    changed = dict(plans[0][1])
    changed["sections"] = [
        {"index": 1, "start": 0, "end": 1, "title": "different"},
        {"index": 2, "start": 1, "end": 3, "title": "different two"},
    ]
    with pytest.raises(ValueError, match="ambiguous section plans"):
        _build(plans=[plans[0], ("three/section-plans/source.json", changed)])


def test_unrelated_source_plan_cannot_change_or_block_scoped_projection():
    *_, plans = _fixture()
    unrelated = {
        "source_sha256": "d" * 64,
        "origin": "generated_subtitles",
        "sections": [{"index": 1, "start": 0, "end": 1, "title": "unrelated"}],
    }
    baseline = _build(plans=plans)
    with_unrelated = _build(
        plans=plans
        + [
            ("unrelated/section-plans/one.json", unrelated),
            (
                "unrelated/section-plans/two.json",
                unrelated
                | {
                    "sections": [
                        {"index": 1, "start": 0, "end": 2, "title": "different"}
                    ]
                },
            ),
        ]
    )
    assert with_unrelated == baseline


def test_projection_sha_detects_any_path_mutation():
    result = _build()
    result["claims"][0]["validated_occurrence_count"] += 1
    with pytest.raises(ValueError, match="artifact SHA mismatch"):
        verify_projection_artifact(result)
