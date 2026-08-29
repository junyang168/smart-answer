import hashlib
from copy import deepcopy

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.theological_editorial_composition_runner import _review_packet
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
        "schema_version": "wang_theological_editorial_brief_candidate_v1",
        "evidence_packet_sha256": packet["evidence_packet_sha256"],
        "status": "ready",
        "summary": "A positive-centred structure with a later boundary.",
        "article_title": "教会建立在什么根基上",
        "reader_takeaway": "文章先呈现正面根基，再说明彼得本人不是根基。",
        "reader_takeaway_attribution": "editorial_synthesis",
        "reader_takeaway_viewpoint_revision_ids": ["CVR-POSITIVE"],
        "sections": [
            {
                "section_id": "SEC-POSITIVE",
                "heading": "正面答案",
                "article_function": "positive_exposition",
                "reader_function": "Explain the centre and positive identification.",
                "viewpoint_revision_ids": ["CVR-CENTRAL", "CVR-POSITIVE"],
                "argument_route_revision_ids": ["ARR-1", "ARR-2"],
                "required_qualifications": [],
                "prohibited_functions": ["lead_with_opponent"],
            },
            {
                "section_id": "SEC-BOUNDARY",
                "heading": "不是彼得本人",
                "article_function": "negative_boundary",
                "reader_function": "Prevent a specific misunderstanding after the answer.",
                "viewpoint_revision_ids": ["CVR-NEGATIVE"],
                "argument_route_revision_ids": ["ARR-3"],
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
        "unresolved_items": ["Positive identifications are not fully unified."],
        "stop_reasons": [],
    }


def test_editorial_scope_is_sha_bound():
    scope = _scope()
    validate_editorial_scope(scope)
    scope["reader_question"] = "changed"
    with pytest.raises(TheologicalEditorialContractError, match="SHA mismatch"):
        validate_editorial_scope(scope)


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


def test_route_must_stay_with_its_conclusion():
    packet = _compile()
    candidate = _candidate(packet)
    candidate["sections"][0]["argument_route_revision_ids"] = ["ARR-1", "ARR-3"]
    candidate["sections"][1]["argument_route_revision_ids"] = ["ARR-2"]
    with pytest.raises(TheologicalEditorialContractError, match="conclusion"):
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
        "schema_version": "wang_theological_editorial_brief_review_v1",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "pass",
        "summary": "The brief is supported and keeps the positive centre.",
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
        "schema_version": "wang_theological_editorial_brief_review_v1",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "pass",
        "summary": "Contradictory pass.",
        "findings": [
            {
                "finding_id": "BRF-1",
                "code": "positive_center_missing",
                "severity": "high",
                "blocking": True,
                "record_ids": ["CVR-POSITIVE"],
                "explanation": "The positive centre is absent.",
                "recommended_action": "Restore it.",
            }
        ],
    }
    with pytest.raises(TheologicalEditorialContractError, match="cannot contain findings"):
        validate_brief_review(review, candidate=candidate)


def test_brief_revision_must_dispose_every_finding_and_bind_both_shas():
    packet = _compile()
    candidate = _candidate(packet)
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v1",
        "scope_confirmation": (
            "editorial_structure_and_material_no_theological_judgment"
        ),
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "changes_required",
        "summary": "Keep the boundary out of the title.",
        "findings": [
            {
                "finding_id": "BRF-1",
                "code": "negative_material_displaces_center",
                "severity": "high",
                "blocking": True,
                "record_ids": ["CVR-NEGATIVE"],
                "explanation": "The title leads with the rejection.",
                "recommended_action": "Lead with the positive answer.",
            }
        ],
    }
    revised_candidate = deepcopy(candidate)
    revised_candidate["article_title"] = "教会的根基：正面答案与必要边界"
    revision = {
        "schema_version": "wang_theological_editorial_brief_revision_v1",
        "baseline_candidate_sha256": sha256_json(candidate),
        "baseline_review_sha256": sha256_json(review),
        "finding_dispositions": [
            {
                "finding_id": "BRF-1",
                "resolution": "resolved",
                "changed_fields": ["article_title"],
                "explanation": "The title now leads with the positive centre.",
            }
        ],
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
