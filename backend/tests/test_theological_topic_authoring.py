import hashlib
import json
from pathlib import Path

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.matthew_exposition_authoring import sha256_text
from backend.pipeline.theological_editorial_synthesis import (
    TheologicalEditorialContractError,
    build_scoped_source_originals,
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
    HARD_FAILURE_DIMENSIONS,
    build_topic_presentation_package,
    program_audit,
)


ROOT = Path(__file__).parents[2]


def _source_content(source_id):
    return f"Complete source original for {source_id}."


def _source_reader(source):
    return {
        "original_file_sha256": source["source_sha256"],
        "content_format": (
            "markdown"
            if source["source_type"] == "notes_manuscript"
            else "timestamped_transcript"
        ),
        "content": _source_content(source["source_id"]),
    }


def _refresh_source_originals(evidence):
    source_shas = {}
    for source in evidence["source_documents"]:
        source["source_sha256"] = hashlib.sha256(
            _source_content(source["source_id"]).encode("utf-8")
        ).hexdigest()
        source_shas[source["source_id"]] = source["source_sha256"]
    for fragment in evidence["source_fragments"]:
        fragment["source_sha256"] = source_shas[fragment["source_id"]]
    evidence["source_originals"] = build_scoped_source_originals(
        evidence["source_documents"], reader=_source_reader
    )


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
        "source_documents": [
            {
                "source_id": "SRC-1",
                "title": "Source 1",
                "source_type": "sermon_transcript",
            }
        ],
        "relations": [],
        "compiler_findings": [],
        "compiler_readiness": "ready_for_composition",
        "dependency_manifest": dependencies,
        "dependency_manifest_sha256": sha256_json(dependencies),
    }
    _refresh_source_originals(evidence)
    evidence["evidence_packet_sha256"] = sha256_json(
        {
            key: value
            for key, value in evidence.items()
            if key != "evidence_packet_sha256"
        }
    )
    decisions = {
        "status": "ready",
        "article_title": "Positive title",
        "opening_contract": {
            "opening_position": "State the interpretation under examination.",
            "why_it_requires_examination": "The conclusion depends on the text.",
            "governing_question": "What is the claim?",
            "first_section_id": "SEC-1",
            "first_evidence_path": "Enter the first section's textual evidence.",
            "answer_preview_policy": "orientation_only_no_answer_inventory",
        },
        "conclusion_contract": {
            "settled_conclusion": "The positive claim is established.",
            "settled_conclusion_claim_ids": ["CL-1"],
            "positive_answer_sequence": [
                {
                    "role": "direct_answer",
                    "summary": "State the positive proposition.",
                    "claim_ids": ["CL-1"],
                    "modality": "asserted",
                }
            ],
            "unresolved_relation_policy": {
                "disclosure_required": False,
                "summary": "",
                "disclose_in_section_id": "",
                "max_reader_visible_disclosures": 0,
                "repeat_in_conclusion": False,
            },
            "application_boundary": "",
            "application_boundary_placement": "not_applicable",
            "closing_function": "Answer the reader question directly.",
            "closing_source_claim_ids": ["CL-1"],
            "prohibited_closing_moves": [
                "Do not replace the answer with editorial process language."
            ],
        },
        "viewpoint_coverage": [],
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

<!-- provenance: {\"attribution\":\"editorial_synthesis\",\"claim_ids\":[\"CL-1\"],\"evidence_step_ids\":[\"EV-1\"],\"argument_route_revision_ids\":[]} -->
An interpretation makes a claim. What is the claim?

## Positive heading

<!-- provenance: {\"attribution\":\"professor\",\"claim_ids\":[\"CL-1\"],\"evidence_step_ids\":[\"EV-1\"],\"argument_route_revision_ids\":[]} -->
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
    assert packet["knowledge"]["source_originals"] == evidence["source_originals"]


def test_final_section_requires_closing_claims_but_not_every_settled_claim():
    evidence, brief, publication, quality = _inputs()
    evidence["claims"].append(
        {"claim_id": "CL-SETTLED", "statement": "Established earlier", "evidence_step_ids": []}
    )
    evidence["evidence_packet_sha256"] = sha256_json(
        {
            key: value
            for key, value in evidence.items()
            if key != "evidence_packet_sha256"
        }
    )
    brief["evidence_packet_sha256"] = evidence["evidence_packet_sha256"]
    brief["editorial_decisions"]["conclusion_contract"][
        "settled_conclusion_claim_ids"
    ] = ["CL-SETTLED"]
    brief["brief_sha256"] = sha256_json(
        {key: value for key, value in brief.items() if key != "brief_sha256"}
    )
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    validate_topic_author_result(_valid_result(), authoring_packet=packet)

    packet["editorial_decisions"]["conclusion_contract"][
        "closing_source_claim_ids"
    ] = ["CL-SETTLED"]
    with pytest.raises(
        TheologicalEditorialContractError,
        match="omits closing Claims",
    ):
        validate_topic_author_result(_valid_result(), authoring_packet=packet)


def test_topic_author_rejects_an_opening_with_competing_questions():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    result["manuscript_markdown"] = result["manuscript_markdown"].replace(
        "An interpretation makes a claim. What is the claim?",
        (
            "An interpretation makes a claim. However, why does the text use two terms? "
            "Is the answer a person, a confession, or received truth?"
        ),
    )
    with pytest.raises(TheologicalEditorialContractError, match="one governing question"):
        validate_topic_author_result(result, authoring_packet=packet)


def test_topic_author_rejects_a_different_single_opening_question():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    result["manuscript_markdown"] = result["manuscript_markdown"].replace(
        "What is the claim?",
        "Why does this matter?",
    )
    with pytest.raises(
        TheologicalEditorialContractError,
        match="approved governing question exactly",
    ):
        validate_topic_author_result(result, authoring_packet=packet)


def test_topic_author_rejects_an_extra_clause_inside_the_approved_question():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    result["manuscript_markdown"] = result["manuscript_markdown"].replace(
        "What is the claim?",
        "What is the claim, and what follows from it?",
    )
    with pytest.raises(
        TheologicalEditorialContractError,
        match="approved governing question exactly as a complete sentence",
    ):
        validate_topic_author_result(result, authoring_packet=packet)


def test_paragraph_route_provenance_must_stay_inside_its_brief_section():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    result["manuscript_markdown"] = result["manuscript_markdown"].replace(
        '"argument_route_revision_ids":[]',
        '"argument_route_revision_ids":["ARR-OTHER"]',
    )

    with pytest.raises(
        TheologicalEditorialContractError,
        match="unknown ArgumentRoutes",
    ):
        validate_topic_author_result(result, authoring_packet=packet)


def test_substantive_paragraph_requires_explicit_evidence_step_provenance():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    result["manuscript_markdown"] = result["manuscript_markdown"].replace(
        ',"evidence_step_ids":["EV-1"]', ""
    )

    with pytest.raises(
        TheologicalEditorialContractError,
        match="requires evidence_step_ids",
    ):
        validate_topic_author_result(result, authoring_packet=packet)


def test_paragraph_claim_provenance_must_be_in_its_own_section_ledger():
    evidence, brief, publication, quality = _inputs()
    evidence["claims"].append(
        {
            "claim_id": "CL-OTHER",
            "statement": "A claim established in another section.",
            "evidence_step_ids": ["EV-1"],
        }
    )
    evidence["evidence_packet_sha256"] = sha256_json(
        {
            key: value
            for key, value in evidence.items()
            if key != "evidence_packet_sha256"
        }
    )
    brief["evidence_packet_sha256"] = evidence["evidence_packet_sha256"]
    brief["editorial_decisions"]["sections"].append(
        {
            "section_id": "SEC-2",
            "heading": "Second heading",
            "viewpoint_revision_ids": ["CVR-1"],
            "argument_route_revision_ids": ["ARR-1"],
        }
    )
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
    result["manuscript_markdown"] = result["manuscript_markdown"].replace(
        "Positive proposition in prose.",
        """Positive proposition in prose.

## Second heading

<!-- provenance: {\"attribution\":\"professor\",\"claim_ids\":[\"CL-1\",\"CL-OTHER\"],\"evidence_step_ids\":[\"EV-1\"],\"argument_route_revision_ids\":[\"ARR-1\"]} -->
Second proposition in prose.""",
    )
    result["sections"][0]["claim_ids_used"].append("CL-OTHER")
    result["sections"].append(
        {
            "section_id": "SEC-2",
            "claim_ids_used": ["CL-1"],
            "viewpoint_revision_ids_used": ["CVR-1"],
            "argument_route_revision_ids_used": ["ARR-1"],
            "output_anchor": "Second proposition in prose.",
        }
    )
    with pytest.raises(
        TheologicalEditorialContractError,
        match="Claims outside its section ledger",
    ):
        validate_topic_author_result(result, authoring_packet=packet)


def test_editorial_reviewer_receives_the_same_complete_originals():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    assert review_packet["source_originals"] == evidence["source_originals"]
    assert review_packet["source_originals"]["originals"][0]["content"] == (
        _source_content("SRC-1")
    )
    assert review_packet["opening_reader_prose"] == (
        "An interpretation makes a claim. What is the claim?"
    )
    assert review_packet["opening_contract"] == brief["editorial_decisions"][
        "opening_contract"
    ]
    assert review_packet["conclusion_reader_prose"] == (
        "Positive proposition in prose."
    )
    assert review_packet["conclusion_contract"] == brief["editorial_decisions"][
        "conclusion_contract"
    ]


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


@pytest.mark.parametrize(
    "reader_prose",
    [
        "教授接着提出另一种说法。",
        "近距语境提供另一项印证。",
        "正面答案须按原稿的‘或者’保留。",
        "正面答案可以并列表述为两种说法。",
        "正面的答案需要沿着经文继续追问。",
    ],
)
def test_topic_author_rejects_professor_analysis_voice_in_reader_prose(reader_prose):
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    result = _valid_result()
    result["manuscript_markdown"] = result["manuscript_markdown"].replace(
        "Positive proposition in prose.",
        reader_prose,
    )
    result["sections"][0]["output_anchor"] = reader_prose
    with pytest.raises(TheologicalEditorialContractError, match="forbidden reader-prose"):
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


def _review_for(packet, manuscript_sha, *, hard_failure_id=None):
    quality = packet["quality_profile"]
    hard = []
    for failure_id in quality["hard_failures"]:
        failed = failure_id == hard_failure_id
        hard.append({"failure_id": failure_id, "failed": failed, "evidence": "Checked against title, opening, headings, and conclusion."})
    findings = []
    if hard_failure_id:
        opening_failure = hard_failure_id == "opening_reader_path_broken"
        conclusion_failure = hard_failure_id == "conclusion_reader_answer_broken"
        findings.append({
            "finding_id": "F-HARD", "dimension_id": (
                "general_reader_readability"
                if opening_failure
                else "reader_memory_center"
                if conclusion_failure
                else "positive_thesis_and_structural_fidelity"
            ),
            "section_id": "SEC-1", "severity": "major", "blocking": True,
            "manuscript_anchor": (
                "An interpretation makes a claim."
                if opening_failure
                else "Positive proposition in prose."
                if conclusion_failure
                else "Positive proposition in prose."
            ),
            "problem": f"Hard failure: {hard_failure_id}.",
            "required_change": "Restore the approved reader-facing argument.",
        })
    opening_anchor = "An interpretation makes a claim."
    conclusion_anchor = "Positive proposition in prose."
    return {
        "schema_version": "wang_theological_topic_editorial_review_v1",
        "reviewed_manuscript_sha256": manuscript_sha,
        "scope_confirmation": "theological_topic_essay_quality",
        "summary": "Independent review.",
        "dimension_scores": [
            {
                "dimension_id": item["id"],
                "score": item["weight"],
                "evidence": (
                    f"The opening begins: {opening_anchor}"
                    if item["id"] == "general_reader_readability"
                    else f"The conclusion states: {conclusion_anchor}"
                    if item["id"] == "reader_memory_center"
                    else "Evidence."
                ),
            }
            for item in quality["dimensions"]
        ],
        "hard_failure_assessments": hard,
        "conclusion_assessment": {
            "evidence_anchor": conclusion_anchor,
            "reader_answer_in_one_sentence": "The positive proposition is the answer.",
            "answers_reader_question": not (
                hard_failure_id == "conclusion_reader_answer_broken"
            ),
            "editorial_process_displaces_answer": (
                hard_failure_id == "conclusion_reader_answer_broken"
            ),
            "positive_claims_follow_contract": not (
                hard_failure_id == "conclusion_reader_answer_broken"
            ),
            "unresolved_disclosure_repeated": False,
        },
        "findings": findings,
    }


def test_passing_review_must_cite_the_opening_in_readability_evidence():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    review = _review_for(packet, review_packet["manuscript_sha256"])
    readability = next(
        item
        for item in review["dimension_scores"]
        if item["dimension_id"] == "general_reader_readability"
    )
    readability["evidence"] = "The middle sections are clear."
    with pytest.raises(TheologicalEditorialContractError, match="cite the opening"):
        validate_topic_editorial_review(review, review_packet=review_packet)


def test_negative_material_hard_failure_rejects_even_with_perfect_scores():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence, approved_brief=brief,
        publication_profile=publication, quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    review = _review_for(
        packet,
        review_packet["manuscript_sha256"],
        hard_failure_id="negative_material_displaces_positive_thesis",
    )
    outcome = validate_topic_editorial_review(review, review_packet=review_packet)
    assert outcome["passed"] is False
    assert outcome["total_score"] == 100
    assert outcome["total_score_decides_nothing"] is True
    assert outcome["hard_failures"] == ["negative_material_displaces_positive_thesis"]


def test_opening_reader_path_hard_failure_requires_an_opening_anchored_finding():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    failure_id = "opening_reader_path_broken"
    assert HARD_FAILURE_DIMENSIONS[failure_id] == {
        "positive_thesis_and_structural_fidelity",
        "general_reader_readability",
    }
    review = _review_for(
        packet,
        review_packet["manuscript_sha256"],
        hard_failure_id=failure_id,
    )
    outcome = validate_topic_editorial_review(review, review_packet=review_packet)
    assert outcome["passed"] is False
    assert outcome["hard_failures"] == [failure_id]


def test_conclusion_reader_answer_hard_failure_requires_a_conclusion_finding():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    failure_id = "conclusion_reader_answer_broken"
    assert HARD_FAILURE_DIMENSIONS[failure_id] == {
        "positive_thesis_and_structural_fidelity",
        "general_reader_readability",
        "editorial_voice_restraint",
        "reader_memory_center",
    }
    review = _review_for(
        packet,
        review_packet["manuscript_sha256"],
        hard_failure_id=failure_id,
    )
    outcome = validate_topic_editorial_review(review, review_packet=review_packet)
    assert outcome["passed"] is False
    assert outcome["hard_failures"] == [failure_id]


def test_passing_review_must_cite_the_conclusion_in_memory_evidence():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    review = _review_for(packet, review_packet["manuscript_sha256"])
    memory = next(
        item
        for item in review["dimension_scores"]
        if item["dimension_id"] == "reader_memory_center"
    )
    memory["evidence"] = "The middle sections cover all claims."
    with pytest.raises(TheologicalEditorialContractError, match="cite the conclusion"):
        validate_topic_editorial_review(review, review_packet=review_packet)


def test_meta_analysis_hard_failure_rejects_even_with_perfect_scores():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence, approved_brief=brief,
        publication_profile=publication, quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    failure_id = "meta_analysis_displaces_first_order_argument"
    assert failure_id in quality["hard_failures"]
    review = _review_for(
        packet,
        review_packet["manuscript_sha256"],
        hard_failure_id=failure_id,
    )
    outcome = validate_topic_editorial_review(review, review_packet=review_packet)
    assert outcome["passed"] is False
    assert outcome["total_score"] == 100
    assert outcome["hard_failures"] == [failure_id]


def test_flattened_article_hierarchy_is_an_independent_hard_failure():
    evidence, brief, publication, quality = _inputs()
    packet = build_topic_authoring_packet(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
    )
    review_packet = build_topic_editorial_review_packet(
        authoring_packet=packet, author_result=_valid_result()
    )
    failure_id = "article_argument_hierarchy_flattened"
    assert failure_id in quality["hard_failures"]
    assert HARD_FAILURE_DIMENSIONS[failure_id] == {
        "positive_thesis_and_structural_fidelity",
        "argument_route_integrity",
        "reader_memory_center",
    }
    review = _review_for(
        packet,
        review_packet["manuscript_sha256"],
        hard_failure_id=failure_id,
    )
    outcome = validate_topic_editorial_review(review, review_packet=review_packet)
    assert outcome["passed"] is False
    assert outcome["hard_failures"] == [failure_id]


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
        "conclusion_assessment": {
            "evidence_anchor": "Positive proposition in prose.",
            "reader_answer_in_one_sentence": "The positive proposition is the answer.",
            "answers_reader_question": True,
            "editorial_process_displaces_answer": False,
            "positive_claims_follow_contract": True,
            "unresolved_disclosure_repeated": False,
        },
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
    )
    _refresh_source_originals(evidence)
    source_sha = evidence["source_documents"][0]["source_sha256"]
    evidence["source_fragments"][0].update(
        source_sha256=source_sha, media_time=12.0, media_end_time=34.0
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


def test_program_audit_discloses_notes_only_section_without_inventing_audio():
    evidence, brief, publication, quality = _inputs()
    evidence["source_documents"][0].update(source_type="notes_manuscript")
    _refresh_source_originals(evidence)
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
    author_result = _valid_result()
    manuscript_sha = sha256_text(author_result["manuscript_markdown"])
    review = _review_for(packet, manuscript_sha)
    outcome = validate_topic_editorial_review(
        review,
        review_packet=build_topic_editorial_review_packet(
            authoring_packet=packet, author_result=author_result
        ),
    )
    audit = program_audit(
        packet=packet,
        author_result=author_result,
        grounding={"passed": True, "manuscript_sha256": manuscript_sha},
        editorial_review=review,
        editorial_outcome=outcome,
        presentation_package=build_topic_presentation_package(
            packet=packet, author_result=author_result
        ),
    )
    assert audit["status"] == "pass"
    assert audit["summary"] == {"error_total": 0, "warning_total": 1}
    assert audit["warnings"] == [{
        "code": "section_has_text_source_only_no_audio",
        "message": "SEC-1",
        "source_types": ["notes_manuscript"],
    }]
