import json
from pathlib import Path

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.matthew_exposition_authoring import sha256_text
from backend.pipeline.theological_editorial_synthesis import (
    TheologicalEditorialContractError,
)
from backend.pipeline.theological_topic_authoring import (
    build_topic_editorial_review_packet,
    build_topic_authoring_packet,
    editorial_instructions_by_claim,
    validate_topic_editorial_review,
    validate_topic_final_delta_review,
    validate_topic_grounding_revision,
    validate_topic_author_result,
)
from backend.pipeline.theological_topic_quality_runner import (
    build_topic_presentation_package,
)


ROOT = Path(__file__).parents[2]


def _inputs():
    scope = {
        "schema_version": "wang_theological_editorial_scope_v1",
        "scope_id": "TES-1",
        "product_kind": "theological_topic_essay",
        "working_title": "Topic",
        "reader_question": "What is the claim?",
        "passage_refs": ["太16:18"],
        "structure_revision_id": "VSR-1",
        "publication_profile_id": "PP-theological-topic-essay-v1",
        "explicit_exclusions": [],
        "editorial_attribution": "church_editor",
        "not_professor_words": True,
    }
    scope["scope_sha256"] = sha256_json(scope)
    dependencies = []
    evidence = {
        "schema_version": "wang_theological_evidence_packet_v1",
        "scope": scope,
        "structure": {"revision": {"unresolved_items": []}},
        "focal_viewpoints": [
            {
                "structure_role": "positive_identification",
                "revision": {
                    "viewpoint_revision_id": "CVR-1",
                    "viewpoint_id": "CV-1",
                    "core_proposition": "Positive proposition",
                },
                "member_claim_ids": ["CL-1"],
            }
        ],
        "argument_routes": [
            {
                "revision": {
                    "argument_route_revision_id": "ARR-1",
                    "validated_against_conclusion_viewpoint_revision_id": "CVR-1",
                    "route_label": "Positive route",
                    "ordered_inference_nodes": [],
                }
            }
        ],
        "claims": [{"claim_id": "CL-1", "statement": "Claim", "evidence_step_ids": ["EV-1"]}],
        "evidence_steps": [{"evidence_step_id": "EV-1", "source_fragment_ids": ["FR-1"]}],
        "source_fragments": [{"fragment_id": "FR-1", "source_id": "SRC-1", "verbatim_excerpt": "Excerpt"}],
        "source_documents": [{"source_id": "SRC-1"}],
        "relations": [],
        "compiler_findings": [],
        "compiler_readiness": "ready_for_composition",
        "dependency_manifest": dependencies,
        "dependency_manifest_sha256": sha256_json(dependencies),
    }
    evidence["evidence_packet_sha256"] = sha256_json(evidence)
    decisions = {
        "status": "ready",
        "article_title": "Positive title",
        "sections": [
            {
                "section_id": "SEC-1",
                "heading": "Positive heading",
                "viewpoint_revision_ids": ["CVR-1"],
                "argument_route_revision_ids": ["ARR-1"],
            }
        ],
    }
    brief = {
        "schema_version": "wang_theological_editorial_brief_v1",
        "scope_sha256": scope["scope_sha256"],
        "evidence_packet_sha256": evidence["evidence_packet_sha256"],
        "brief_candidate_sha256": "a",
        "brief_review_sha256": "b",
        "editorial_decisions": decisions,
    }
    brief["brief_sha256"] = sha256_json(brief)
    publication = json.loads(
        (ROOT / "backend/config/publication_profiles/PP-theological-topic-essay-v1.json").read_text()
    )
    quality = json.loads(
        (ROOT / "backend/config/editorial_quality_profiles/WQ-theological-topic-essay-v1.json").read_text()
    )
    return evidence, brief, publication, quality


def _valid_result():
    return {
        "status": "drafted",
        "manuscript_markdown": """# Positive title

## Positive heading

<!-- provenance: {\"attribution\":\"professor\",\"claim_ids\":[\"CL-1\"]} -->
Positive proposition in prose.
""",
        "sections": [
            {
                "section_id": "SEC-1",
                "claim_ids_used": ["CL-1"],
                "viewpoint_revision_ids_used": ["CVR-1"],
                "argument_route_revision_ids_used": ["ARR-1"],
                "output_anchor": "Positive proposition in prose.",
            }
        ],
        "composition_change_requests": [],
    }


def test_topic_author_packet_and_ledger_bind_the_approved_brief():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    validate_topic_author_result(_valid_result(), authoring_packet=packet)
    assert packet["input_bindings"]["brief_sha256"] == brief["brief_sha256"]


def test_topic_author_requires_exact_heading_order():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    result["manuscript_markdown"] = result["manuscript_markdown"].replace(
        "## Positive heading", "## Changed heading"
    )
    with pytest.raises(TheologicalEditorialContractError, match="missing approved heading"):
        validate_topic_author_result(result, authoring_packet=packet)


def test_topic_author_requires_a_member_claim_for_each_viewpoint():
    evidence, brief, publication, quality = _inputs()
    evidence["claims"].append({"claim_id": "CL-OTHER", "statement": "Other", "evidence_step_ids": []})
    evidence["evidence_packet_sha256"] = sha256_json(
        {key: value for key, value in evidence.items() if key != "evidence_packet_sha256"}
    )
    brief["evidence_packet_sha256"] = evidence["evidence_packet_sha256"]
    brief["brief_sha256"] = sha256_json(
        {key: value for key, value in brief.items() if key != "brief_sha256"}
    )
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    result["sections"][0]["claim_ids_used"] = ["CL-OTHER"]
    with pytest.raises(TheologicalEditorialContractError, match="has no member Claim"):
        validate_topic_author_result(result, authoring_packet=packet)


def test_composition_change_stops_without_a_draft():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    validate_topic_author_result(
        {
            "status": "composition_change_required",
            "manuscript_markdown": "",
            "sections": [],
            "composition_change_requests": [
                {
                    "request_id": "CCR-1",
                    "reason": "Missing material",
                    "proposed_change": "Return to Composition",
                    "affected_record_ids": ["CVR-1"],
                }
            ],
        },
        authoring_packet=packet,
    )


def test_grounding_instructions_keep_editorial_judgment_separate():
    evidence, brief, publication, quality = _inputs()
    brief["editorial_decisions"]["sections"][0].update(
        reader_function="Put the positive answer first.",
        required_qualifications=["Do not upgrade the modality."],
        prohibited_functions=["Do not lead with the opponent."],
    )
    brief["editorial_decisions"]["unresolved_items"] = [
        "The relation remains unresolved."
    ]
    brief["brief_sha256"] = sha256_json(
        {key: value for key, value in brief.items() if key != "brief_sha256"}
    )
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    instructions = editorial_instructions_by_claim(
        authoring_packet=packet, author_result=_valid_result()
    )
    assert "Required qualification: Do not upgrade the modality." in instructions["CL-1"]
    assert "Unresolved structure item: The relation remains unresolved." in instructions["CL-1"]


def test_grounding_revision_is_bound_and_disposes_every_finding():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    finding = {"finding_id": "TGF-1", "code": "unsupported_assertion"}
    revision = {
        "schema_version": "wang_theological_topic_grounding_revision_v1",
        "baseline_manuscript_sha256": sha256_text(result["manuscript_markdown"]),
        "finding_dispositions": [
            {
                "finding_id": "TGF-1",
                "resolution": "resolved",
                "resolution_anchor": "Positive proposition in prose.",
                "explanation": "Removed the unsupported qualifier.",
            }
        ],
        "revised_author_result": result,
    }
    validate_topic_grounding_revision(
        revision,
        baseline_manuscript_sha256=sha256_text(result["manuscript_markdown"]),
        findings=[finding],
        authoring_packet=packet,
    )
    revision["finding_dispositions"] = []
    with pytest.raises(TheologicalEditorialContractError, match="every finding"):
        validate_topic_grounding_revision(
            revision,
            baseline_manuscript_sha256=sha256_text(result["manuscript_markdown"]),
            findings=[finding],
            authoring_packet=packet,
        )


def _review_for(packet, manuscript_sha, *, negative_center_failure=False):
    quality = packet["quality_profile"]
    hard = []
    for failure_id in quality["hard_failures"]:
        failed = negative_center_failure and failure_id == "negative_material_displaces_positive_thesis"
        hard.append({"failure_id": failure_id, "failed": failed, "evidence": "Checked against title, opening, headings, and conclusion."})
    findings = []
    if negative_center_failure:
        findings.append({
            "finding_id": "F-NEG", "dimension_id": "positive_thesis_and_structural_fidelity",
            "section_id": "SEC-1", "severity": "major", "blocking": True,
            "manuscript_anchor": "Positive proposition in prose.",
            "problem": "The negative rebuttal displaces the approved positive thesis.",
            "required_change": "Restore the positive thesis as the memory center.",
        })
    return {
        "schema_version": "wang_theological_topic_editorial_review_v1",
        "reviewed_manuscript_sha256": manuscript_sha,
        "scope_confirmation": "theological_topic_essay_quality",
        "summary": "Independent review.",
        "dimension_scores": [
            {"dimension_id": item["id"], "score": item["weight"], "evidence": "Evidence."}
            for item in quality["dimensions"]
        ],
        "hard_failure_assessments": hard,
        "findings": findings,
    }


def test_negative_material_hard_failure_rejects_even_with_perfect_scores():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence, approved_brief=brief,
        publication_profile=publication, quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    review = _review_for(packet, review_packet["manuscript_sha256"], negative_center_failure=True)
    outcome = validate_topic_editorial_review(review, review_packet=review_packet)
    assert outcome["passed"] is False
    assert outcome["total_score"] == 100
    assert outcome["total_score_decides_nothing"] is True
    assert outcome["hard_failures"] == ["negative_material_displaces_positive_thesis"]


def test_delta_can_return_next_round_finding_in_new_dimension():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence, approved_brief=brief,
        publication_profile=publication, quality_profile=quality,
    )
    manuscript = _valid_result()["manuscript_markdown"]
    finding = {"finding_id": "F-1", "blocking": True}
    delta = {
        "schema_version": "wang_theological_topic_final_delta_review_v1",
        "baseline_manuscript_sha256": "a" * 64,
        "reviewed_manuscript_sha256": sha256_text(manuscript),
        "summary": "The direct finding is resolved; a changed paragraph exposed another issue.",
        "dimension_scores": [{"dimension_id": "approved_written_style", "score": 9, "evidence": "Improved."}],
        "hard_failure_assessments": [],
        "finding_dispositions": [{"finding_id": "F-1", "resolution": "resolved", "resolution_anchor": "Positive proposition in prose.", "explanation": "Resolved."}],
        "findings": [{
            "finding_id": "F-2", "dimension_id": "general_reader_readability",
            "section_id": "SEC-1", "severity": "minor", "blocking": True,
            "manuscript_anchor": "Positive proposition in prose.",
            "problem": "New wording is hard to read.", "required_change": "Clarify it.",
        }],
    }
    validate_topic_final_delta_review(
        delta, baseline_manuscript_sha256="a" * 64,
        revised_manuscript=manuscript,
        affected_dimension_ids=["approved_written_style"],
        affected_hard_failure_ids=[], findings=[finding], quality_profile=quality,
    )


def test_presentation_package_uses_the_same_claim_evidence_audio_chain():
    evidence, brief, publication, quality = _inputs()
    evidence["source_documents"][0].update(
        source_type="sermon_transcript",
        transcript_id="SERMON-1",
        source_sha256="source-sha",
    )
    evidence["source_fragments"][0].update(
        source_sha256="source-sha", media_time=12.0, media_end_time=34.0
    )
    evidence["claims"][0]["scripture_refs"] = ["Matt.16.18"]
    evidence["evidence_packet_sha256"] = sha256_json(
        {key: value for key, value in evidence.items() if key != "evidence_packet_sha256"}
    )
    brief["evidence_packet_sha256"] = evidence["evidence_packet_sha256"]
    brief["brief_sha256"] = sha256_json(
        {key: value for key, value in brief.items() if key != "brief_sha256"}
    )
    packet = build_topic_authoring_packet(
        evidence_packet=evidence, approved_brief=brief,
        publication_profile=publication, quality_profile=quality,
    )
    package = build_topic_presentation_package(
        packet=packet, author_result=_valid_result()
    )
    decision = package["product_plans"][0]["decisions"][0]
    assert decision["passage"] == "Matt.16.18"
    assert decision["source_presentations"] == [{
        "source_id": "SRC-1", "start_seconds": 12.0, "end_seconds": 34.0,
        "claim_ids": ["CL-1"], "fragment_ids": ["FR-1"],
    }]
