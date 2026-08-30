import hashlib
from copy import deepcopy

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.theological_editorial_composition_runner import _review_packet
from backend.pipeline.theological_editorial_recomposition_runner import _as_brief_finding
from backend.pipeline.theological_editorial_synthesis import (
    TheologicalEditorialContractError,
    build_scoped_source_originals,
    compile_approved_editorial_brief,
    compile_theological_evidence_packet,
    make_editorial_scope,
    validate_brief_review,
    validate_brief_revision,
    validate_editorial_brief_candidate,
    validate_editorial_scope,
)


def _original_content(source_id):
    return f"Complete original source text for {source_id}."


def _source_reader(source):
    return {
        "original_file_sha256": source["source_sha256"],
        "content_format": (
            "markdown"
            if source["source_type"] == "notes_manuscript"
            else "timestamped_transcript"
        ),
        "content": _original_content(source["source_id"]),
    }


def _records():
    roles = {
        "CVR-CENTRAL": "central_claim",
        "CVR-POSITIVE": "positive_identification",
        "CVR-NEGATIVE": "negative_boundary",
    }
    viewpoint_ids = {
        "CVR-CENTRAL": "CV-CENTRAL",
        "CVR-POSITIVE": "CV-POSITIVE",
        "CVR-NEGATIVE": "CV-NEGATIVE",
    }
    records = {
        "viewpoint_structures": [
            {
                "structure_id": "VS-FOUNDATION",
                "current_revision_id": "VSR-FOUNDATION-1",
                "effective_state": "active",
                "review_status": "system_approved",
                "revision": 1,
            }
        ],
        "viewpoint_structure_revisions": [
            {
                "structure_revision_id": "VSR-FOUNDATION-1",
                "structure_id": "VS-FOUNDATION",
                "central_synthesis": "The positive centre remains qualified.",
                "focal_viewpoints": [
                    {
                        "viewpoint_revision_id": revision_id,
                        "structure_role": role,
                        "basis_claim_ids": [],
                    }
                    for revision_id, role in roles.items()
                ],
                "unresolved_items": ["Positive identifications are not fully unified."],
                "review_status": "system_approved",
                "revision": 1,
            }
        ],
        "canonical_viewpoints": [],
        "viewpoint_revisions": [],
        "viewpoint_claim_links": [],
        "argument_routes": [],
        "argument_route_revisions": [],
        "argument_route_attestations": [],
        "viewpoint_relations": [],
        "claims": [],
        "evidence_steps": [],
        "source_fragments": [],
        "source_documents": [],
    }
    for index, revision_id in enumerate(roles, start=1):
        viewpoint_id = viewpoint_ids[revision_id]
        claim_id = f"CL-{index}"
        evidence_id = f"EV-{index}"
        fragment_id = f"FR-{index}"
        source_id = f"SRC-{index}"
        route_id = f"AR-{index}"
        route_revision_id = f"ARR-{index}"
        records["canonical_viewpoints"].append(
            {
                "viewpoint_id": viewpoint_id,
                "current_revision_id": revision_id,
                "identity_status": "active",
                "review_status": "system_approved",
                "revision": 1,
            }
        )
        records["viewpoint_revisions"].append(
            {
                "viewpoint_revision_id": revision_id,
                "viewpoint_id": viewpoint_id,
                "core_proposition": f"Proposition {index}",
                "review_status": "system_approved",
                "revision": 1,
            }
        )
        records["viewpoint_claim_links"].append(
            {
                "viewpoint_claim_link_id": f"VCL-{index}",
                "viewpoint_id": viewpoint_id,
                "claim_id": claim_id,
                "validated_against_viewpoint_revision_id": revision_id,
                "effective_state": "active",
                "review_status": "system_approved",
                "revision": 1,
            }
        )
        records["argument_routes"].append(
            {
                "argument_route_id": route_id,
                "current_revision_id": route_revision_id,
                "conclusion_viewpoint_id": viewpoint_id,
                "route_status": "active",
                "review_status": "system_approved",
                "revision": 1,
            }
        )
        records["argument_route_revisions"].append(
            {
                "argument_route_revision_id": route_revision_id,
                "argument_route_id": route_id,
                "validated_against_conclusion_viewpoint_revision_id": revision_id,
                "route_label": f"Route {index}",
                "ordered_inference_nodes": [
                    {"route_step_key": "P1", "role": "premise"},
                    {
                        "route_step_key": "C1",
                        "role": "conclusion",
                        "conclusion_viewpoint_revision_id": revision_id,
                    },
                ],
                "review_status": "system_approved",
                "revision": 1,
            }
        )
        records["argument_route_attestations"].append(
            {
                "argument_route_attestation_id": f"ARA-{index}",
                "argument_route_id": route_id,
                "validated_against_route_revision_id": route_revision_id,
                "source_id": source_id,
                "claim_ids": [claim_id],
                "step_bindings": [
                    {
                        "route_step_key": "P1",
                        "evidence_step_ids": [evidence_id],
                        "source_fragment_ids": [fragment_id],
                    }
                ],
                "completeness": "full",
                "effective_state": "active",
                "review_status": "system_approved",
                "revision": 1,
            }
        )
        records["claims"].append(
            {
                "claim_id": claim_id,
                "statement": f"Claim {index}",
                "evidence_step_ids": [evidence_id],
                "revision": 1,
            }
        )
        records["evidence_steps"].append(
            {
                "evidence_step_id": evidence_id,
                "statement": f"Evidence {index}",
                "source_fragment_ids": [fragment_id],
                "revision": 1,
            }
        )
        records["source_fragments"].append(
            {
                "fragment_id": fragment_id,
                "source_id": source_id,
                "verbatim_excerpt": f"Excerpt {index}",
                "revision": 1,
            }
        )
        records["source_documents"].append(
            {
                "source_id": source_id,
                "title": f"Source {index}",
                "source_type": (
                    "notes_manuscript" if index == 3 else "sermon_transcript"
                ),
                "source_sha256": hashlib.sha256(
                    _original_content(source_id).encode("utf-8")
                ).hexdigest(),
                "revision": 1,
            }
        )
    return records


def _scope():
    return make_editorial_scope(
        scope_id="TES-M16-FOUNDATION",
        working_title="教会的根基",
        reader_question="教授认为教会建立在什么根基上，他怎样形成这个判断？",
        passage_refs=["太16:18"],
        structure_revision_id="VSR-FOUNDATION-1",
        publication_profile_id="PP-theological-topic-essay-v1",
    )


def _compile(records=None):
    return compile_theological_evidence_packet(
        scope=_scope(),
        records=records or _records(),
        source_original_reader=_source_reader,
    )


def _candidate(packet):
    return {
        "schema_version": "wang_theological_editorial_brief_candidate_v2",
        "evidence_packet_sha256": packet["evidence_packet_sha256"],
        "status": "ready",
        "summary": "A positive-centred structure with a later boundary.",
        "article_title": "教会建立在什么根基上",
        "opening_contract": {
            "opening_position": "Introduce the interpretation being examined.",
            "why_it_requires_examination": (
                "Its downstream conclusion depends on what the rock denotes."
            ),
            "governing_question": "What is the church founded upon?",
            "first_section_id": "SEC-POSITIVE",
            "first_evidence_path": (
                "Move directly from the question to the first section's textual evidence."
            ),
            "answer_preview_policy": "orientation_only_no_answer_inventory",
        },
        "reader_takeaway": "文章先呈现正面根基，再说明彼得本人不是根基。",
        "reader_takeaway_attribution": "editorial_synthesis",
        "reader_takeaway_viewpoint_revision_ids": ["CVR-POSITIVE"],
        "sections": [
            {
                "section_id": "SEC-POSITIVE",
                "heading": "正面答案",
                "article_function": "positive_exposition",
                "reader_function": "Explain the centre and positive identification.",
                "governing_question": "What is the church founded upon?",
                "section_conclusion": "The section gives the positive foundation.",
                "depends_on_section_ids": [],
                "viewpoint_revision_ids": ["CVR-CENTRAL", "CVR-POSITIVE"],
                "argument_route_revision_ids": ["ARR-1", "ARR-2"],
                "argument_route_uses": [
                    {
                        "argument_route_revision_id": "ARR-1",
                        "role": "primary_support",
                    },
                    {
                        "argument_route_revision_id": "ARR-2",
                        "role": "corroboration",
                    },
                ],
                "embedded_materials": [],
                "required_qualifications": [],
                "prohibited_functions": ["lead_with_opponent"],
            },
            {
                "section_id": "SEC-BOUNDARY",
                "heading": "不是彼得本人",
                "article_function": "negative_boundary",
                "reader_function": "Prevent a specific misunderstanding after the answer.",
                "governing_question": "Why is Peter himself not the rock?",
                "section_conclusion": "Peter himself is excluded as the rock.",
                "depends_on_section_ids": ["SEC-POSITIVE"],
                "viewpoint_revision_ids": ["CVR-NEGATIVE"],
                "argument_route_revision_ids": ["ARR-3"],
                "argument_route_uses": [
                    {
                        "argument_route_revision_id": "ARR-3",
                        "role": "primary_support",
                    }
                ],
                "embedded_materials": [],
                "required_qualifications": [],
                "prohibited_functions": ["replace_positive_center"],
            },
        ],
        "viewpoint_coverage": [
            {
                "viewpoint_revision_id": "CVR-CENTRAL",
                "disposition": "include",
                "section_id": "SEC-POSITIVE",
                "reason": "",
            },
            {
                "viewpoint_revision_id": "CVR-POSITIVE",
                "disposition": "include",
                "section_id": "SEC-POSITIVE",
                "reason": "",
            },
            {
                "viewpoint_revision_id": "CVR-NEGATIVE",
                "disposition": "include",
                "section_id": "SEC-BOUNDARY",
                "reason": "",
            },
        ],
        "editorial_constraint_coverage": [
            {
                "constraint_id": item["constraint_id"],
                "status": "satisfied",
                "implementation_paths": ["/sections/1"],
                "explanation": "The binding editorial constraint is implemented.",
            }
            for item in packet["scope"].get("editorial_constraints") or []
        ],
        "unresolved_items": ["Positive identifications are not fully unified."],
        "stop_reasons": [],
    }


def test_editorial_scope_is_sha_bound():
    scope = _scope()
    validate_editorial_scope(scope)
    scope["reader_question"] = "changed"
    with pytest.raises(TheologicalEditorialContractError, match="SHA mismatch"):
        validate_editorial_scope(scope)


def test_binding_footnote_constraint_is_machine_verified_in_the_brief():
    scope = make_editorial_scope(
        scope_id="TES-M16-FOOTNOTE",
        working_title="教会的根基",
        reader_question="教会建立在什么根基上？",
        passage_refs=["太16:18"],
        structure_revision_id="VSR-FOUNDATION-1",
        publication_profile_id="PP-theological-topic-essay-v1",
        editorial_constraints=[
            {
                "constraint_id": "EC-FOOTNOTE",
                "constraint_type": "material_placement",
                "target_record_ids": ["ARR-2"],
                "required_value": "footnote",
                "instruction": "Keep the secondary objection in a footnote.",
                "rationale": "It must not interrupt the main argument.",
                "feedback_artifact_sha256": "a" * 64,
            }
        ],
    )
    packet = compile_theological_evidence_packet(
        scope=scope,
        records=_records(),
        source_original_reader=_source_reader,
    )
    candidate = _candidate(packet)
    candidate["sections"][0]["embedded_materials"] = [
        {
            "embedded_material_id": "EM-FOOTNOTE",
            "presentation_mode": "footnote",
            "reader_function": "Answer the objection without interrupting the section.",
            "viewpoint_revision_ids": ["CVR-POSITIVE"],
            "argument_route_revision_ids": ["ARR-2"],
            "required_qualifications": [],
        }
    ]
    validate_editorial_brief_candidate(candidate, evidence_packet=packet)
    candidate["sections"][0]["embedded_materials"] = []
    with pytest.raises(TheologicalEditorialContractError, match="footnote"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_evidence_compiler_follows_the_complete_structure():
    packet = _compile()
    assert packet["compiler_readiness"] == "ready_for_composition"
    assert {
        item["revision"]["viewpoint_revision_id"]
        for item in packet["focal_viewpoints"]
    } == {"CVR-CENTRAL", "CVR-POSITIVE", "CVR-NEGATIVE"}
    assert len(packet["argument_routes"]) == 3
    assert packet["dependency_manifest_sha256"] == sha256_json(
        packet["dependency_manifest"]
    )


def test_evidence_packet_contains_every_complete_scoped_original():
    packet = _compile()
    originals = packet["source_originals"]
    assert originals["source_ids"] == ["SRC-1", "SRC-2", "SRC-3"]
    assert originals["coverage"] == {
        "source_count": 3,
        "sermon_transcript_count": 2,
        "notes_manuscript_count": 1,
        "total_character_count": sum(
            len(_original_content(f"SRC-{index}")) for index in range(1, 4)
        ),
        "direct_context_limit_characters": 120_000,
        "delivery_mode": "complete_originals_in_context",
        "overflow_policy": "stop_before_generation_pending_batched_reading",
        "truncation_allowed": False,
    }
    assert [item["content"] for item in originals["originals"]] == [
        _original_content("SRC-1"),
        _original_content("SRC-2"),
        _original_content("SRC-3"),
    ]


def test_complete_originals_fail_closed_instead_of_truncating():
    records = _records()
    with pytest.raises(
        TheologicalEditorialContractError,
        match="batched source reading is required",
    ):
        build_scoped_source_originals(
            records["source_documents"],
            reader=_source_reader,
            max_total_characters=10,
        )


def test_composition_reviewer_receives_the_same_complete_originals():
    evidence = _compile()
    review_packet = _review_packet(
        evidence_packet=evidence,
        candidate=_candidate(evidence),
    )
    assert review_packet["source_fragments"] == evidence["source_fragments"]
    assert review_packet["source_originals"] == evidence["source_originals"]


def test_final_composition_review_receives_deterministic_diff_and_change_scope():
    evidence = _compile()
    candidate = _candidate(evidence)
    revised = deepcopy(candidate)
    revised["sections"][1]["reader_function"] = "Keep the objection in a note."
    review = {
        "findings": [
            {
                "finding_id": "BRF-1",
                "authorized_change_paths": ["/sections/1/reader_function"],
            }
        ]
    }
    packet = _review_packet(
        evidence_packet=evidence,
        candidate=revised,
        revision_context={
            "baseline_candidate": candidate,
            "baseline_review": review,
            "finding_dispositions": [],
            "collateral_changes": [],
            "revision_sha256": "a" * 64,
        },
    )
    assert packet["revision_context"]["deterministic_changed_fields"] == [
        "/sections/1/reader_function"
    ]
    assert packet["revision_context"]["authorized_change_paths_by_finding"] == {
        "BRF-1": ["/sections/1/reader_function"]
    }


def test_evidence_compiler_rejects_a_stale_structure_revision():
    records = _records()
    records["viewpoint_structures"][0]["current_revision_id"] = "VSR-NEW"
    with pytest.raises(TheologicalEditorialContractError, match="active/current"):
        _compile(records)


def test_ready_brief_cannot_silently_omit_a_focal_viewpoint():
    packet = _compile()
    candidate = _candidate(packet)
    candidate["viewpoint_coverage"] = candidate["viewpoint_coverage"][:-1]
    with pytest.raises(TheologicalEditorialContractError, match="every focal"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_negative_boundary_cannot_carry_the_reader_takeaway():
    packet = _compile()
    candidate = _candidate(packet)
    candidate["reader_takeaway_viewpoint_revision_ids"] = ["CVR-NEGATIVE"]
    with pytest.raises(TheologicalEditorialContractError, match="negative boundary"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_ready_brief_preserves_structure_unresolved_items():
    packet = _compile()
    candidate = _candidate(packet)
    candidate["unresolved_items"] = []
    with pytest.raises(TheologicalEditorialContractError, match="unresolved"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_ready_brief_requires_an_opening_contract_bound_to_the_first_section():
    packet = _compile()
    candidate = _candidate(packet)
    candidate.pop("opening_contract")
    with pytest.raises(TheologicalEditorialContractError, match="opening contract"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)

    candidate = _candidate(packet)
    candidate["opening_contract"]["first_section_id"] = "SEC-BOUNDARY"
    with pytest.raises(TheologicalEditorialContractError, match="first section"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)

    candidate = _candidate(packet)
    candidate["opening_contract"]["governing_question"] = "A different question?"
    with pytest.raises(TheologicalEditorialContractError, match="governing question"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)

    candidate = _candidate(packet)
    candidate["opening_contract"]["governing_question"] = (
        "What is the foundation? Which evidence establishes it?"
    )
    candidate["sections"][0]["governing_question"] = candidate[
        "opening_contract"
    ]["governing_question"]
    with pytest.raises(TheologicalEditorialContractError, match="one governing question"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_brief_review_must_authorize_the_paired_opening_question_fields():
    packet = _compile()
    candidate = _candidate(packet)
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v3",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "changes_required",
        "summary": "The first governing question needs revision.",
        "article_progression_coherent": False,
        "article_progression_explanation": "The opening and first section diverge.",
        "section_assessments": [
            {
                "section_id": section["section_id"],
                "heading_frames_governing_question": section["section_id"]
                != "SEC-POSITIVE",
                "heading_is_consistent_with_section_conclusion": True,
                "route_roles_form_hierarchy": True,
                "explanation": "Checked.",
            }
            for section in candidate["sections"]
        ],
        "editorial_constraint_assessments": [],
        "findings": [
            {
                "finding_id": "BRF-OPENING",
                "code": "heading_governing_question_mismatch",
                "severity": "high",
                "blocking": True,
                "record_ids": ["SEC-POSITIVE"],
                "explanation": "The question needs revision.",
                "recommended_action": "Revise the paired question.",
                "authorized_change_paths": ["/sections/0/governing_question"],
            }
        ],
    }
    with pytest.raises(TheologicalEditorialContractError, match="authorized together"):
        validate_brief_review(review, candidate=candidate)


def test_route_must_stay_with_its_conclusion():
    packet = _compile()
    candidate = _candidate(packet)
    candidate["sections"][0]["argument_route_revision_ids"] = ["ARR-1", "ARR-3"]
    candidate["sections"][1]["argument_route_revision_ids"] = ["ARR-2"]
    candidate["sections"][0]["argument_route_uses"] = [
        {
            "argument_route_revision_id": "ARR-1",
            "role": "primary_support",
        },
        {
            "argument_route_revision_id": "ARR-3",
            "role": "corroboration",
        },
    ]
    candidate["sections"][1]["argument_route_uses"] = [
        {
            "argument_route_revision_id": "ARR-2",
            "role": "primary_support",
        }
    ]
    with pytest.raises(TheologicalEditorialContractError, match="conclusion"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_section_dependencies_must_form_the_declared_article_progression():
    packet = _compile()
    candidate = _candidate(packet)
    candidate["sections"][0]["depends_on_section_ids"] = ["SEC-BOUNDARY"]
    with pytest.raises(TheologicalEditorialContractError, match="earlier section"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_route_uses_must_preserve_primary_and_supporting_roles():
    packet = _compile()
    candidate = _candidate(packet)
    candidate["sections"][1]["argument_route_uses"][0]["role"] = "corroboration"
    with pytest.raises(TheologicalEditorialContractError, match="primary_support"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_non_ready_brief_requires_machine_readable_stop_reason():
    packet = _compile()
    candidate = _candidate(packet)
    candidate.update(
        status="insufficient_material",
        sections=[],
        article_title="",
        reader_takeaway="",
        stop_reasons=[],
    )
    with pytest.raises(TheologicalEditorialContractError, match="stop reasons"):
        validate_editorial_brief_candidate(candidate, evidence_packet=packet)


def test_only_sha_bound_passing_review_compiles_approved_brief():
    packet = _compile()
    candidate = _candidate(packet)
    validate_editorial_brief_candidate(candidate, evidence_packet=packet)
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v3",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "pass",
        "summary": "The brief is supported and keeps the positive centre.",
        "article_progression_coherent": True,
        "article_progression_explanation": "The boundary depends on the positive answer.",
        "section_assessments": [
            {
                "section_id": section["section_id"],
                "heading_frames_governing_question": True,
                "heading_is_consistent_with_section_conclusion": True,
                "route_roles_form_hierarchy": True,
                "explanation": "The heading and route roles preserve the section logic.",
            }
            for section in candidate["sections"]
        ],
        "editorial_constraint_assessments": [],
        "findings": [],
    }
    validate_brief_review(review, candidate=candidate)
    brief = compile_approved_editorial_brief(
        candidate=candidate, evidence_packet=packet, review=review
    )
    assert brief["brief_candidate_sha256"] == sha256_json(candidate)
    assert brief["brief_review_sha256"] == sha256_json(review)
    assert brief["brief_sha256"] == sha256_json(
        {key: value for key, value in brief.items() if key != "brief_sha256"}
    )


def test_passing_review_cannot_hide_findings():
    packet = _compile()
    candidate = _candidate(packet)
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v3",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "pass",
        "summary": "Contradictory pass.",
        "article_progression_coherent": True,
        "article_progression_explanation": "Claimed coherent.",
        "section_assessments": [
            {
                "section_id": section["section_id"],
                "heading_frames_governing_question": True,
                "heading_is_consistent_with_section_conclusion": True,
                "route_roles_form_hierarchy": True,
                "explanation": "Claimed aligned.",
            }
            for section in candidate["sections"]
        ],
        "editorial_constraint_assessments": [],
        "findings": [
            {
                "finding_id": "BRF-1",
                "code": "positive_center_missing",
                "severity": "high",
                "blocking": True,
                "record_ids": ["CVR-POSITIVE"],
                "explanation": "The positive centre is absent.",
                "recommended_action": "Restore it.",
                "authorized_change_paths": ["/article_title"],
            }
        ],
    }
    with pytest.raises(TheologicalEditorialContractError, match="cannot contain findings"):
        validate_brief_review(review, candidate=candidate)


def test_passing_review_must_explicitly_reject_a_flattened_section_heading():
    packet = _compile()
    candidate = _candidate(packet)
    candidate["sections"][1]["heading"] = (
        "Petrus与Petra指向谁？彼得为何随即受责备？"
    )
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v3",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "pass",
        "summary": "The route inventory is complete.",
        "article_progression_coherent": True,
        "article_progression_explanation": "Claimed coherent.",
        "section_assessments": [
            {
                "section_id": "SEC-POSITIVE",
                "heading_frames_governing_question": True,
                "heading_is_consistent_with_section_conclusion": True,
                "route_roles_form_hierarchy": True,
                "explanation": "Aligned.",
            },
            {
                "section_id": "SEC-BOUNDARY",
                "heading_frames_governing_question": False,
                "heading_is_consistent_with_section_conclusion": False,
                "route_roles_form_hierarchy": False,
                "explanation": "The heading lists two evidence items instead of the governing question.",
            },
        ],
        "editorial_constraint_assessments": [],
        "findings": [],
    }
    with pytest.raises(TheologicalEditorialContractError, match="structural assessment"):
        validate_brief_review(review, candidate=candidate)


def test_approved_brief_compiler_cannot_bypass_structural_review_validation():
    packet = _compile()
    candidate = _candidate(packet)
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v3",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "pass",
        "summary": "The candidate is structurally coherent.",
        "article_progression_coherent": True,
        "article_progression_explanation": "Each later section uses an earlier conclusion.",
        "section_assessments": [
            {
                "section_id": section["section_id"],
                "heading_frames_governing_question": True,
                "heading_is_consistent_with_section_conclusion": True,
                "route_roles_form_hierarchy": True,
                "explanation": "The section has one governing question and a route hierarchy.",
            }
            for section in candidate["sections"]
        ],
        "editorial_constraint_assessments": [],
        "findings": [],
    }
    review["section_assessments"][0]["route_roles_form_hierarchy"] = False

    with pytest.raises(TheologicalEditorialContractError, match="structural assessment"):
        compile_approved_editorial_brief(
            candidate=candidate,
            evidence_packet=packet,
            review=review,
        )


def test_downstream_composition_finding_rejects_an_unknown_section():
    packet = _compile()
    candidate = _candidate(packet)
    finding = {
        "finding_id": "F-UNKNOWN-SECTION",
        "dimension_id": "argument_route_integrity",
        "section_id": "SEC-DOES-NOT-EXIST",
        "severity": "major",
        "problem": "The brief locks the prose into a flat route list.",
        "required_change": "Restore a governing question and route hierarchy.",
    }

    with pytest.raises(ValueError, match="unknown section"):
        _as_brief_finding(finding, candidate=candidate)


def test_brief_revision_must_dispose_every_finding_and_bind_both_shas():
    packet = _compile()
    candidate = _candidate(packet)
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v3",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "changes_required",
        "summary": "Keep the boundary out of the title.",
        "article_progression_coherent": True,
        "article_progression_explanation": "Only the title needs repair.",
        "section_assessments": [
            {
                "section_id": section["section_id"],
                "heading_frames_governing_question": True,
                "heading_is_consistent_with_section_conclusion": True,
                "route_roles_form_hierarchy": True,
                "explanation": "The section structure remains sound.",
            }
            for section in candidate["sections"]
        ],
        "editorial_constraint_assessments": [],
        "findings": [
            {
                "finding_id": "BRF-1",
                "code": "negative_material_displaces_center",
                "severity": "high",
                "blocking": True,
                "record_ids": ["CVR-NEGATIVE"],
                "explanation": "The title leads with the rejection.",
                "recommended_action": "Lead with the positive answer.",
                "authorized_change_paths": ["/article_title"],
            }
        ],
    }
    revised_candidate = deepcopy(candidate)
    revised_candidate["article_title"] = "教会的根基：正面答案与必要边界"
    revision = {
        "schema_version": "wang_theological_editorial_brief_revision_v3",
        "baseline_candidate_sha256": sha256_json(candidate),
        "baseline_review_sha256": sha256_json(review),
        "finding_dispositions": [
            {
                "finding_id": "BRF-1",
                "resolution": "resolved",
                "changed_fields": ["/article_title"],
                "explanation": "The title now leads with the positive centre.",
            }
        ],
        "collateral_changes": [],
        "revised_candidate": revised_candidate,
    }
    validate_brief_revision(
        revision,
        candidate=candidate,
        review=review,
        evidence_packet=packet,
    )
    revision["finding_dispositions"] = []
    with pytest.raises(TheologicalEditorialContractError, match="every review finding"):
        validate_brief_revision(
            revision,
            candidate=candidate,
            review=review,
            evidence_packet=packet,
        )


def test_brief_revision_rejects_an_unreported_heading_regression():
    packet = _compile()
    candidate = _candidate(packet)
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v3",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "changes_required",
        "summary": "Move a secondary objection into a note.",
        "article_progression_coherent": True,
        "article_progression_explanation": "The original progression is sound.",
        "section_assessments": [
            {
                "section_id": section["section_id"],
                "heading_frames_governing_question": True,
                "heading_is_consistent_with_section_conclusion": True,
                "route_roles_form_hierarchy": True,
                "explanation": "The original section is coherent.",
            }
            for section in candidate["sections"]
        ],
        "editorial_constraint_assessments": [],
        "findings": [
            {
                "finding_id": "BRF-ARAMAIC",
                "code": "argument_hierarchy_flattened",
                "severity": "high",
                "blocking": True,
                "record_ids": ["SEC-BOUNDARY"],
                "explanation": "A secondary objection interrupts the primary argument.",
                "recommended_action": "Move it into a note without changing the heading.",
                "authorized_change_paths": [
                    "/sections/1/reader_function",
                    "/sections/1/required_qualifications",
                ],
            }
        ],
    }
    revised_candidate = deepcopy(candidate)
    revised_candidate["sections"][1]["reader_function"] = (
        "Keep the secondary objection in a note."
    )
    revised_candidate["sections"][1]["heading"] = (
        "Petrus与Petra指向谁？彼得为何随即受责备？"
    )
    revision = {
        "schema_version": "wang_theological_editorial_brief_revision_v3",
        "baseline_candidate_sha256": sha256_json(candidate),
        "baseline_review_sha256": sha256_json(review),
        "finding_dispositions": [
            {
                "finding_id": "BRF-ARAMAIC",
                "resolution": "resolved",
                "changed_fields": ["/sections/1/reader_function"],
                "explanation": "The objection is now assigned to a note.",
            }
        ],
        "collateral_changes": [],
        "revised_candidate": revised_candidate,
    }
    with pytest.raises(TheologicalEditorialContractError, match="unreported changed fields"):
        validate_brief_revision(
            revision,
            candidate=candidate,
            review=review,
            evidence_packet=packet,
        )


def test_explicit_section_object_authorization_covers_its_changed_children():
    packet = _compile()
    candidate = _candidate(packet)
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v3",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "changes_required",
        "summary": "The whole boundary section must be reframed.",
        "article_progression_coherent": False,
        "article_progression_explanation": "The boundary section needs a linked repair.",
        "section_assessments": [
            {
                "section_id": section["section_id"],
                "heading_frames_governing_question": section["section_id"]
                != "SEC-BOUNDARY",
                "heading_is_consistent_with_section_conclusion": True,
                "route_roles_form_hierarchy": True,
                "explanation": "The boundary heading needs repair."
                if section["section_id"] == "SEC-BOUNDARY"
                else "Aligned.",
            }
            for section in candidate["sections"]
        ],
        "editorial_constraint_assessments": [],
        "findings": [
            {
                "finding_id": "BRF-SECTION",
                "code": "heading_governing_question_mismatch",
                "severity": "high",
                "blocking": True,
                "record_ids": ["SEC-BOUNDARY"],
                "explanation": "The section needs linked field changes.",
                "recommended_action": "Reframe the section without changing scope.",
                "authorized_change_paths": ["/sections/1"],
            }
        ],
    }
    revised_candidate = deepcopy(candidate)
    revised_candidate["sections"][1]["heading"] = "为什么磐石不是彼得本人？"
    revised_candidate["sections"][1]["reader_function"] = (
        "Answer one governing question with ordered evidence."
    )
    revision = {
        "schema_version": "wang_theological_editorial_brief_revision_v3",
        "baseline_candidate_sha256": sha256_json(candidate),
        "baseline_review_sha256": sha256_json(review),
        "finding_dispositions": [
            {
                "finding_id": "BRF-SECTION",
                "resolution": "resolved",
                "changed_fields": ["/sections/1"],
                "explanation": "The linked section fields were repaired together.",
            }
        ],
        "collateral_changes": [],
        "revised_candidate": revised_candidate,
    }
    validate_brief_revision(
        revision,
        candidate=candidate,
        review=review,
        evidence_packet=packet,
    )
