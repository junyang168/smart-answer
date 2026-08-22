from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.knowledge_models import (
    CanonicalViewpointRecord,
    ViewpointClaimLinkRecord,
    ViewpointIdentityDecisionRecord,
    ViewpointRevisionRecord,
)
from backend.api.canonical_repository.postgres_store import build_change_set_plan
from backend.api.canonical_repository.viewpoint_foundation import (
    build_coverage_snapshot,
    build_foundation_quality_report,
    build_identity_candidate_seeds,
    build_input_claim_manifest,
    build_resolution_ledger,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    CallableReviewerAdapter,
    SemanticAssessment,
    StructuredJsonReviewerAdapter,
    ViewpointResolutionError,
    ViewpointResolutionRunArtifact,
    build_exception_queue,
    build_identity_review_packet,
    run_viewpoint_resolution,
)


def _source(source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_type": "sermon_transcript",
        "title": f"太十六章 {source_id}",
        "source_sha256": f"source-sha-{source_id}",
        "review_status": "approved",
        "revision": 1,
    }


def _fragment(index: int, source_id: str) -> dict[str, Any]:
    return {
        "fragment_id": f"FR-{index}",
        "source_id": source_id,
        "verbatim_excerpt": "教会不是建立在彼得个人身上。",
        "paragraph_key": f"S{index:04d}",
        "media_time": float(index * 10),
        "citation_id": f"CIT-{index}",
        "source_sha256": f"source-sha-{source_id}",
        "anchor_state": "source_version_bound",
    }


def _evidence(index: int) -> dict[str, Any]:
    return {
        "evidence_step_id": f"E-{index}",
        "source_fragment_id": f"FR-{index}",
        "statement": "逐字证据支持该释经判断",
        "support_eligibility": "eligible",
        "citation_ids": [f"CIT-{index}"],
        "review_status": "approved",
    }


def _citation(index: int, source_id: str) -> dict[str, Any]:
    return {
        "citation_id": f"CIT-{index}",
        "source_id": source_id,
        "source_sha256": f"source-sha-{source_id}",
        "locator": {
            "kind": "transcript",
            "paragraph_keys": [f"S{index:04d}"],
            "highlight_text": "教会不是建立在彼得个人身上",
            "highlight_text_sha256": f"highlight-sha-{index}",
        },
        "evidence_ids": [f"E-{index}"],
        "status": "approved",
        "revision": 1,
    }


def _claim(
    index: int,
    statement: str = "教会不是建立在彼得个人身上",
) -> dict[str, Any]:
    return {
        "claim_id": f"CL-PETER-{index}",
        "statement": statement,
        "claim_type": "explicit_claim",
        "attribution": "professor",
        "scripture_refs": ["Matt.16.18"],
        "evidence_step_ids": [f"E-{index}"],
        "review_status": "approved",
        "revision": 1,
    }


def _source_manifest(sources: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "schema_version": "wang_source_universe_manifest_v1",
        "source_universe_manifest_id": "SUM-RESOLUTION-TEST",
        "sources": [
            {
                "source_id": item["source_id"],
                "source_revision_id": f"{item['source_id']}@1",
                "source_sha256": item["source_sha256"],
            }
            for item in sources
        ],
    }
    payload["manifest_sha256"] = sha256_json(payload)
    return payload


def _fixture(
    *,
    source_count: int = 2,
    material_relation: str | None = None,
    statements: list[str] | None = None,
    citation_status: str = "approved",
    omit_relations_from_packet: bool = False,
    negative_duplicate_constraint: bool = False,
    match_existing: bool = False,
) -> tuple[Any, dict[tuple[str, str], dict[str, Any]]]:
    sources = [_source(f"SRC-{index}") for index in range(1, source_count + 1)]
    statements = statements or [
        "教会不是建立在彼得个人身上" for _ in range(source_count)
    ]
    claims = [
        _claim(index, statements[index - 1]) for index in range(1, source_count + 1)
    ]
    evidence = [_evidence(index) for index in range(1, source_count + 1)]
    fragments = [
        _fragment(index, sources[index - 1]["source_id"])
        for index in range(1, source_count + 1)
    ]
    citations = [
        _citation(index, sources[index - 1]["source_id"])
        for index in range(1, source_count + 1)
    ]
    citations[0]["status"] = citation_status
    coverage = build_coverage_snapshot(
        sources,
        roles_by_source={
            item["source_id"]: [
                "source_universe",
                "detailed_extraction",
                "viewpoint_reviewed",
            ]
            for item in sources
        },
        source_universe_manifest=_source_manifest(sources),
        created_at="2026-08-22T14:30:00+00:00",
        coverage_status="complete",
    )
    manifest = build_input_claim_manifest(claims, evidence, fragments, coverage)
    relations: list[dict[str, Any]] = []
    if source_count > 1:
        relations.append(
            {
                "claim_relation_id": "CR-DUPLICATE",
                "from_id": claims[0]["claim_id"],
                "to_id": claims[1]["claim_id"],
                "relation_type": "duplicate",
                "review_status": "ai_consensus",
            }
        )
        if material_relation:
            relations.append(
                {
                    "claim_relation_id": "CR-MATERIAL",
                    "from_id": claims[0]["claim_id"],
                    "to_id": claims[1]["claim_id"],
                    "relation_type": material_relation,
                    "review_status": "human_approved",
                }
            )
    constraints = []
    if negative_duplicate_constraint:
        constraints.append(
            {
                "constraint_id": "CRC-NO-DUPLICATE",
                "source_id": claims[0]["claim_id"],
                "target_id": claims[1]["claim_id"],
                "forbidden_relation_types": ["duplicate"],
                "bidirectional": True,
                "review_status": "human_approved",
            }
        )
    origin_candidates: list[Any] = []
    origin_decisions: list[Any] = []
    existing_viewpoints: list[Any] = []
    existing_revisions: list[Any] = []
    existing_links: list[Any] = []
    candidate_viewpoint_context: list[Any] = []
    if match_existing:
        origin = next(
            item
            for item in build_identity_candidate_seeds(manifest, [], [])
            if item.candidate_claim_ids == [claims[0]["claim_id"]]
        )
        origin_decision = ViewpointIdentityDecisionRecord(
            identity_decision_id="VID-EXISTING",
            identity_candidate_id=origin.identity_candidate_id,
            decision="create_new",
            resolved_viewpoint_id="CV-EXISTING",
            claim_link_decisions=[
                {"claim_id": claims[0]["claim_id"], "link_type": "equivalent_full"}
            ],
            reviewer_kind="human_editor",
            reviewer_id="fixture-editor",
            approval_basis="human_exception_review",
            reason="Existing fixture identity.",
            input_sha256=origin.generation_fingerprint,
            created_at="2026-08-22T14:30:10+00:00",
            review_status="human_approved",
        )
        revision = ViewpointRevisionRecord(
            viewpoint_revision_id="CVR-EXISTING",
            viewpoint_id="CV-EXISTING",
            revision_number=1,
            core_proposition="教会不是建立在彼得个人身上",
            proposition_signature={
                "subject": "教会",
                "predicate": "不是建立在",
                "object": "彼得个人",
                "polarity": "denied",
                "modality": "asserted",
                "population_scope": ["教会"],
            },
            scope={"scripture_scope": ["Matt.16.18"]},
            provenance={
                "basis_identity_decision_ids": [origin_decision.identity_decision_id],
                "review_artifact_sha256": "existing-review-sha",
            },
            approved_by="fixture-editor",
            approved_at="2026-08-22T14:30:11+00:00",
            review_status="human_approved",
        )
        viewpoint = CanonicalViewpointRecord(
            viewpoint_id="CV-EXISTING",
            current_revision_id=revision.viewpoint_revision_id,
            created_from_candidate_id=origin.identity_candidate_id,
            review_status="human_approved",
        )
        existing_link = ViewpointClaimLinkRecord(
            viewpoint_claim_link_id="VCL-EXISTING",
            viewpoint_id=viewpoint.viewpoint_id,
            validated_against_viewpoint_revision_id=revision.viewpoint_revision_id,
            claim_id=claims[0]["claim_id"],
            pinned_claim_revision=1,
            link_type="equivalent_full",
            decision_id=origin_decision.identity_decision_id,
            review_status="human_approved",
        )
        origin_candidates = [origin]
        origin_decisions = [origin_decision]
        existing_viewpoints = [viewpoint]
        existing_revisions = [revision]
        existing_links = [existing_link]
        candidate_viewpoint_context = [(viewpoint, revision)]
    candidate = build_identity_candidate_seeds(
        manifest, relations, constraints, existing_links
    )[0]
    candidate_decision = ViewpointIdentityDecisionRecord(
        identity_decision_id="VID-CANDIDATE-DISPOSITION",
        identity_candidate_id=candidate.identity_candidate_id,
        decision="defer",
        reviewer_kind="system",
        reviewer_id="candidate-projector",
        approval_basis="deterministic",
        reason="Candidate disposition recorded before semantic review.",
        input_sha256=candidate.generation_fingerprint,
        created_at="2026-08-22T14:31:00+00:00",
        review_status="candidate",
    )
    manifest_by_id = {item["claim_id"]: item for item in manifest["claims"]}
    ledger = build_resolution_ledger(
        manifest,
        [
            {
                "claim_id": claim_id,
                "pinned_claim_revision": manifest_by_id[claim_id][
                    "pinned_claim_revision"
                ],
                "claim_revision_sha256": manifest_by_id[claim_id][
                    "claim_revision_sha256"
                ],
                "processing_status": "resolved",
                "resolution_kind": "new_viewpoint_candidate",
                "new_viewpoint_candidate_id": candidate.identity_candidate_id,
                "decision_id": candidate_decision.identity_decision_id,
            }
            for claim_id in candidate.candidate_claim_ids
        ],
        coverage_snapshot_id=coverage.coverage_snapshot_id,
    )
    quality = build_foundation_quality_report(
        scope_ids=[candidate.identity_candidate_id],
        coverage_snapshot=coverage,
        ledger=ledger,
        claims=claims,
        evidence_steps=evidence,
        source_fragments=fragments,
        candidate_regression_artifact_sha256="peter-rock-regression-sha",
        candidate_regression_passed=True,
    )
    packet = build_identity_review_packet(
        candidate=candidate,
        coverage_snapshot=coverage,
        ledger=ledger,
        quality_report=quality,
        claims=claims,
        evidence_steps=evidence,
        source_fragments=fragments,
        citations=citations,
        claim_relations=[] if omit_relations_from_packet else relations,
        constraints=constraints,
        existing_links=existing_links,
        candidate_viewpoints=candidate_viewpoint_context,
    )
    existing_package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "VIEWPOINT-RESOLUTION-INPUT-TEST",
        "source_documents": sources,
        "source_fragments": fragments,
        "evidence_steps": evidence,
        "claims": claims,
        "claim_relations": relations,
        "claim_relation_constraints": constraints,
        "viewpoint_coverage_snapshots": [coverage.model_dump(mode="json")],
        "viewpoint_identity_candidates": [
            *[item.model_dump(mode="json") for item in origin_candidates],
            candidate.model_dump(mode="json"),
        ],
        "viewpoint_identity_decisions": [
            *[item.model_dump(mode="json") for item in origin_decisions],
            candidate_decision.model_dump(mode="json"),
        ],
        "canonical_viewpoints": [
            item.model_dump(mode="json") for item in existing_viewpoints
        ],
        "viewpoint_revisions": [
            item.model_dump(mode="json") for item in existing_revisions
        ],
        "viewpoint_claim_links": [
            item.model_dump(mode="json") for item in existing_links
        ],
        "viewpoint_resolution_ledgers": [ledger.model_dump(mode="json")],
        "viewpoint_quality_reports": [quality.model_dump(mode="json")],
    }
    plan = build_change_set_plan(existing_package, {})
    existing = {
        (operation.collection, operation.object_id): {
            "revision": operation.after_revision,
            "content_sha256": operation.after_sha256,
            "payload": operation.payload,
        }
        for operation in plan.operations
    }
    return packet, existing


def _assessment(
    packet: Any,
    *,
    role: str = "equivalent_full",
    mismatch_field: str | None = None,
    object_value: str = "彼得个人",
    proposed_action: str = "create_new",
    target_viewpoint_id: str | None = None,
) -> dict[str, Any]:
    verdicts = {
        "subject": "compatible",
        "predicate_object": "compatible",
        "polarity": "compatible",
        "population_scope": "compatible",
        "scripture_scope": "compatible",
        "temporal_scope": "compatible",
        "conditions": "compatible",
        "modality": "compatible",
        "attribution": "compatible",
        "material_qualification": "compatible",
    }
    if mismatch_field:
        verdicts[mismatch_field] = "mismatch"
    return {
        "candidate_id": packet.candidate.identity_candidate_id,
        "packet_sha256": packet.packet_sha256,
        "proposed_action": proposed_action,
        "target_viewpoint_id": target_viewpoint_id,
        "core_proposition": "教会不是建立在彼得个人身上",
        "proposition_signature": {
            "subject": "教会",
            "predicate": "不是建立在",
            "object": object_value,
            "polarity": "denied",
            "modality": "asserted",
            "population_scope": ["教会"],
        },
        "scope": {"scripture_scope": ["Matt.16.18"]},
        "members": [
            {
                "claim_id": claim_id,
                "member_role": role,
                "truth_conditions": verdicts,
                **(
                    {
                        "component_statement": "教会不是建立在彼得个人身上",
                        "component_json_pointer": "/statement",
                    }
                    if role == "equivalent_component"
                    else {}
                ),
            }
            for claim_id in packet.candidate.candidate_claim_ids
        ],
        "canonical_wording_conservative": True,
        "added_truth_conditions": [],
        "semantic_blockers": [],
        "rationale": "逐项真值条件一致。",
    }


class _CountingAdapter:
    def __init__(self, model_id: str, response: Any) -> None:
        self.model_id = model_id
        self.prompt_sha256 = sha256_json({"prompt": model_id})
        self.calls = 0
        self.response = response

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls += 1
        if callable(self.response):
            return self.response(payload)
        return json.loads(json.dumps(self.response))


def _delta_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "resolutions": [
            {
                "field_path": item["field_path"],
                "selected_source": "unresolved",
                "reason": "两份独立判断仍不一致，应交给编辑。",
            }
            for item in payload["semantic_deltas"]
        ],
        "remaining_findings": ["正面所指仍有分歧"],
    }


def test_review_packet_is_source_bound_and_byte_stable() -> None:
    first, _ = _fixture()
    second, _ = _fixture()

    assert first == second
    assert first.packet_sha256 == second.packet_sha256
    assert {item.source_id for item in first.claims} == {"SRC-1", "SRC-2"}
    assert all(
        evidence.valid_for_identity_review
        for claim in first.claims
        for evidence in claim.evidence
    )


def test_two_agreeing_reviewers_compile_system_approval_and_resume(tmp_path) -> None:
    packet, existing = _fixture()
    answer = _assessment(packet)
    proposal = _CountingAdapter("proposal-model", answer)
    blind = _CountingAdapter("blind-model", {**answer, "rationale": "独立复核一致。"})
    delta = _CountingAdapter("delta-model", _delta_response)

    first = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=proposal,
        blind_reviewer=blind,
        delta_adjudicator=delta,
        output_dir=tmp_path,
        decided_at="2026-08-22T14:40:00+00:00",
    )
    second = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=proposal,
        blind_reviewer=blind,
        delta_adjudicator=delta,
        output_dir=tmp_path,
        decided_at="2026-08-22T14:40:00+00:00",
    )

    assert first == second
    assert first.disposition == "system_approved"
    assert [item.stage for item in first.call_ledger] == ["proposal", "blind_review"]
    assert (proposal.calls, blind.calls, delta.calls) == (1, 1, 0)
    package = first.proposed_change_package
    assert package is not None
    approved_decision = package["viewpoint_identity_decisions"][0]
    assert approved_decision["review_status"] == "system_approved"
    assert approved_decision["policy_version"] == (
        "viewpoint_identity_automation_policy_v1"
    )
    assert len(approved_decision["reviewer_model_ids"]) == 2
    assert len(approved_decision["semantic_call_artifact_sha256s"]) == 2
    assert package["viewpoint_revisions"][0]["provenance"][
        "review_artifact_sha256"
    ] == approved_decision["review_artifact_sha256"]
    assert len(package["viewpoint_claim_links"]) == 2
    assert package["viewpoint_claim_links"][0]["occurrence_refs"][0].startswith(
        "OCC-"
    )
    assert package["viewpoint_claim_links"][0]["supporting_relation_ids"] == [
        "CR-DUPLICATE"
    ]
    plan = build_change_set_plan(package, existing)
    assert len(plan.operations) == 5


def test_match_existing_adds_only_the_unmastered_claim(tmp_path) -> None:
    packet, existing = _fixture(match_existing=True)
    answer = _assessment(
        packet,
        proposed_action="match_existing",
        target_viewpoint_id="CV-EXISTING",
    )
    result = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=_CountingAdapter("proposal-match", answer),
        blind_reviewer=_CountingAdapter("blind-match", answer),
        delta_adjudicator=_CountingAdapter("delta-match", _delta_response),
        output_dir=tmp_path,
        decided_at="2026-08-22T14:40:30+00:00",
    )

    assert result.disposition == "system_approved"
    package = result.proposed_change_package
    assert package is not None
    assert package["canonical_viewpoints"] == []
    assert package["viewpoint_revisions"] == []
    assert [item["claim_id"] for item in package["viewpoint_claim_links"]] == [
        "CL-PETER-2"
    ]
    assert package["viewpoint_claim_links"][0]["viewpoint_id"] == "CV-EXISTING"
    plan = build_change_set_plan(package, existing)
    assert {(item.collection, item.object_id) for item in plan.operations} == {
        (
            "viewpoint_identity_decisions",
            package["viewpoint_identity_decisions"][0]["identity_decision_id"],
        ),
        (
            "viewpoint_claim_links",
            package["viewpoint_claim_links"][0]["viewpoint_claim_link_id"],
        ),
    }


def test_disagreement_gets_exactly_one_delta_call_and_stays_human(tmp_path) -> None:
    packet, _ = _fixture()
    proposal_answer = _assessment(packet)
    blind_answer = _assessment(packet, object_value="彼得的认信与真理")
    proposal = _CountingAdapter("proposal-model", proposal_answer)
    blind = _CountingAdapter("blind-model", blind_answer)
    delta = _CountingAdapter("delta-model", _delta_response)

    result = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=proposal,
        blind_reviewer=blind,
        delta_adjudicator=delta,
        output_dir=tmp_path,
        decided_at="2026-08-22T14:41:00+00:00",
        consumer_impact="publication",
    )

    assert result.disposition == "human_exception"
    assert [item.semantic_call_ordinal for item in result.call_ledger] == [1, 2, 3]
    assert (proposal.calls, blind.calls, delta.calls) == (1, 1, 1)
    assert result.exception_bundle is not None
    assert result.exception_bundle.requested_editor_decision == "identity_bundle"
    assert result.exception_bundle.consumer_impact == "publication"
    assert "semantic_reviewer_disagreement" in result.exception_bundle.blocker_codes
    assert result.exception_bundle.remaining_findings == ["正面所指仍有分歧"]
    assert len(result.exception_bundle.claims) == 2
    assert result.exception_bundle.claims[0].evidence[0].verbatim_excerpt
    assert result.exception_bundle.semantic_deltas
    assert result.exception_bundle.delta_adjudication_artifact_sha256
    assert result.exception_bundle.proposal.core_proposition
    assert result.exception_bundle.blind_review.core_proposition


@pytest.mark.parametrize(
    "case_name,statements,role,mismatch_field",
    [
        (
            "personal_negation_vs_positive",
            ["彼得本人是磐石", "教会不是建立在彼得个人身上"],
            "equivalent_full",
            "polarity",
        ),
        (
            "rock_is_christ_or_truth",
            ["彼得本人是磐石", "磐石是彼得所承认的基督与真理"],
            "equivalent_full",
            "predicate_object",
        ),
        (
            "peter_represents_apostles",
            ["彼得本人是磐石", "彼得因认信代表使徒群体"],
            "related_only",
            "population_scope",
        ),
        (
            "compound_claim_component",
            ["彼得本人是磐石", "彼得是磐石并且代表使徒群体"],
            "equivalent_component",
            None,
        ),
    ],
)
def test_peter_rock_adversarial_matrix_never_auto_merges(
    tmp_path,
    case_name: str,
    statements: list[str],
    role: str,
    mismatch_field: str | None,
) -> None:
    packet, _ = _fixture(statements=statements)
    answer = _assessment(packet, role=role, mismatch_field=mismatch_field)
    proposal = _CountingAdapter(f"proposal-{case_name}", answer)
    blind = _CountingAdapter(f"blind-{case_name}", answer)
    delta = _CountingAdapter(f"delta-{case_name}", _delta_response)

    result = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=proposal,
        blind_reviewer=blind,
        delta_adjudicator=delta,
        output_dir=tmp_path / case_name,
        decided_at="2026-08-22T14:42:00+00:00",
    )

    assert result.disposition == "human_exception"
    assert delta.calls == 0
    failed = {item.gate for item in result.risk_assessment.checks if not item.passed}
    expected = (
        "full_proposition_members_only" if role != "equivalent_full"
        else "truth_conditions_compatible"
    )
    assert expected in failed


def test_one_source_and_material_relation_fail_closed_without_extra_calls(tmp_path) -> None:
    one_source_packet, _ = _fixture(source_count=1)
    answer = _assessment(one_source_packet)
    proposal = _CountingAdapter("proposal-one-source", answer)
    blind = _CountingAdapter("blind-one-source", answer)
    delta = _CountingAdapter("delta-one-source", _delta_response)
    one_source = run_viewpoint_resolution(
        packet=one_source_packet,
        proposal_reviewer=proposal,
        blind_reviewer=blind,
        delta_adjudicator=delta,
        output_dir=tmp_path / "one-source",
        decided_at="2026-08-22T14:43:00+00:00",
    )
    assert one_source.disposition == "human_exception"
    assert delta.calls == 0
    assert "two_independent_sources" in {
        item.gate for item in one_source.risk_assessment.checks if not item.passed
    }

    relation_packet, _ = _fixture(material_relation="qualifies")
    relation_answer = _assessment(relation_packet)
    relation_result = run_viewpoint_resolution(
        packet=relation_packet,
        proposal_reviewer=_CountingAdapter("proposal-relation", relation_answer),
        blind_reviewer=_CountingAdapter("blind-relation", relation_answer),
        delta_adjudicator=_CountingAdapter("delta-relation", _delta_response),
        output_dir=tmp_path / "relation",
        decided_at="2026-08-22T14:44:00+00:00",
    )
    assert relation_result.disposition == "human_exception"
    assert "material_relation" in relation_result.risk_assessment.blocker_codes

    constrained_packet, _ = _fixture(negative_duplicate_constraint=True)
    constrained_answer = _assessment(constrained_packet)
    constrained_result = run_viewpoint_resolution(
        packet=constrained_packet,
        proposal_reviewer=_CountingAdapter(
            "proposal-negative-constraint", constrained_answer
        ),
        blind_reviewer=_CountingAdapter(
            "blind-negative-constraint", constrained_answer
        ),
        delta_adjudicator=_CountingAdapter(
            "delta-negative-constraint", _delta_response
        ),
        output_dir=tmp_path / "negative-constraint",
        decided_at="2026-08-22T14:44:15+00:00",
    )
    assert constrained_result.disposition == "human_exception"
    assert "approved_negative_constraint" in (
        constrained_result.risk_assessment.blocker_codes
    )


def test_citation_authority_failure_blocks_auto_approval(tmp_path) -> None:
    packet, _ = _fixture(citation_status="stale")
    answer = _assessment(packet)
    result = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=_CountingAdapter("proposal-stale-citation", answer),
        blind_reviewer=_CountingAdapter("blind-stale-citation", answer),
        delta_adjudicator=_CountingAdapter("delta-stale-citation", _delta_response),
        output_dir=tmp_path,
        decided_at="2026-08-22T14:44:30+00:00",
    )

    assert result.disposition == "human_exception"
    assert "evidence_invalid" in result.risk_assessment.blocker_codes
    assert result.exception_bundle is not None
    assert result.exception_bundle.claims[0].evidence[0].citation_status == "stale"


def test_exception_queue_is_identity_level_and_impact_ranked(tmp_path) -> None:
    packet, _ = _fixture(source_count=1)
    answer = _assessment(packet)

    def run(name: str, impact: str, decided_at: str):
        return run_viewpoint_resolution(
            packet=packet,
            proposal_reviewer=_CountingAdapter(f"proposal-{name}", answer),
            blind_reviewer=_CountingAdapter(f"blind-{name}", answer),
            delta_adjudicator=_CountingAdapter(f"delta-{name}", _delta_response),
            output_dir=tmp_path / name,
            decided_at=decided_at,
            consumer_impact=impact,
        )

    ordinary = run("ordinary", "none", "2026-08-22T14:44:31+00:00")
    withdrawal = run(
        "withdrawal", "withdrawal", "2026-08-22T14:44:32+00:00"
    )
    queue = build_exception_queue(
        [ordinary.exception_bundle, withdrawal.exception_bundle]
    )

    assert queue.bundles[0].consumer_impact == "withdrawal"
    assert queue.bundles[0].priority > queue.bundles[1].priority
    assert all(item.requested_editor_decision == "identity_bundle" for item in queue.bundles)
    assert queue.exception_queue_id.startswith("VEQ-")


def test_packet_cannot_omit_candidate_seed_relations() -> None:
    with pytest.raises(ViewpointResolutionError, match="seed relations"):
        _fixture(omit_relations_from_packet=True)


@pytest.mark.parametrize(
    "truth_field",
    [
        "subject",
        "predicate_object",
        "polarity",
        "population_scope",
        "scripture_scope",
        "temporal_scope",
        "conditions",
        "modality",
        "attribution",
        "material_qualification",
    ],
)
def test_each_truth_condition_mutation_blocks_membership(tmp_path, truth_field: str) -> None:
    packet, _ = _fixture()
    answer = _assessment(packet, mismatch_field=truth_field)
    result = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=_CountingAdapter(f"proposal-{truth_field}", answer),
        blind_reviewer=_CountingAdapter(f"blind-{truth_field}", answer),
        delta_adjudicator=_CountingAdapter(f"delta-{truth_field}", _delta_response),
        output_dir=tmp_path / truth_field,
        decided_at="2026-08-22T14:44:45+00:00",
    )

    assert result.disposition == "human_exception"
    assert "truth_conditions_compatible" in {
        item.gate for item in result.risk_assessment.checks if not item.passed
    }


def test_model_cannot_assign_master_ids_or_get_repair_calls(tmp_path) -> None:
    packet, _ = _fixture()
    forged = _assessment(packet)
    forged["canonical_viewpoint_id"] = "CV-MODEL-CHOICE"
    proposal = _CountingAdapter("proposal-forged", forged)
    blind = _CountingAdapter("blind-unused", _assessment(packet))
    delta = _CountingAdapter("delta-unused", _delta_response)

    with pytest.raises(ValidationError, match="canonical_viewpoint_id"):
        run_viewpoint_resolution(
            packet=packet,
            proposal_reviewer=proposal,
            blind_reviewer=blind,
            delta_adjudicator=delta,
            output_dir=tmp_path,
            decided_at="2026-08-22T14:45:00+00:00",
        )
    assert (proposal.calls, blind.calls, delta.calls) == (1, 0, 0)

    proposal.response = _assessment(packet)
    with pytest.raises(ViewpointResolutionError, match="cannot be retried"):
        run_viewpoint_resolution(
            packet=packet,
            proposal_reviewer=proposal,
            blind_reviewer=blind,
            delta_adjudicator=delta,
            output_dir=tmp_path,
            decided_at="2026-08-22T14:45:00+00:00",
        )
    assert (proposal.calls, blind.calls, delta.calls) == (1, 0, 0)


def test_transport_failure_can_resume_without_becoming_a_semantic_retry(tmp_path) -> None:
    packet, _ = _fixture()
    answer = _assessment(packet)

    class TransportOnceAdapter(_CountingAdapter):
        def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("transport timed out before a response")
            return json.loads(json.dumps(self.response))

    proposal = TransportOnceAdapter("proposal-transport", answer)
    blind = _CountingAdapter("blind-transport", answer)
    delta = _CountingAdapter("delta-transport", _delta_response)

    with pytest.raises(TimeoutError, match="transport timed out"):
        run_viewpoint_resolution(
            packet=packet,
            proposal_reviewer=proposal,
            blind_reviewer=blind,
            delta_adjudicator=delta,
            output_dir=tmp_path,
            decided_at="2026-08-22T14:45:30+00:00",
        )
    result = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=proposal,
        blind_reviewer=blind,
        delta_adjudicator=delta,
        output_dir=tmp_path,
        decided_at="2026-08-22T14:45:30+00:00",
    )

    assert result.disposition == "system_approved"
    assert (proposal.calls, blind.calls, delta.calls) == (2, 1, 0)


def test_call_invariant_cannot_be_forged(tmp_path) -> None:
    packet, _ = _fixture(source_count=1)
    answer = _assessment(packet)
    valid = run_viewpoint_resolution(
        packet=packet,
        proposal_reviewer=_CountingAdapter("proposal-invariant", answer),
        blind_reviewer=_CountingAdapter("blind-invariant", answer),
        delta_adjudicator=_CountingAdapter("delta-invariant", _delta_response),
        output_dir=tmp_path,
        decided_at="2026-08-22T14:46:00+00:00",
    )
    forged = valid.model_dump(mode="json")
    forged["call_ledger"] = []
    forged.pop("artifact_sha256")
    forged["artifact_sha256"] = sha256_json(forged)

    with pytest.raises(ValidationError, match="reviewer-call invariant"):
        ViewpointResolutionRunArtifact.model_validate(forged)


def test_callable_adapter_exposes_only_callable_result() -> None:
    adapter = CallableReviewerAdapter(
        model_id="fixture",
        prompt="strict prompt",
        generate=lambda payload: {"seen": sorted(payload)},
    )
    assert adapter.generate({"b": 1, "a": 2}) == {"seen": ["a", "b"]}
    assert adapter.prompt_sha256 == sha256_json({"prompt": "strict prompt"})


def test_structured_adapter_makes_one_schema_bound_call() -> None:
    packet, _ = _fixture()
    answer = _assessment(packet)

    class FakeClient:
        model = "schema-model"

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def generate_json(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return answer

    client = FakeClient()
    adapter = StructuredJsonReviewerAdapter(
        client=client,
        prompt="review independently",
        response_model=SemanticAssessment,
        schema_name="viewpoint_assessment_test",
    )
    result = adapter.generate(packet.model_dump(mode="json"))

    assert result["candidate_id"] == packet.candidate.identity_candidate_id
    assert len(client.calls) == 1
    schema = client.calls[0]["json_schema"]
    assert schema["strict"] is True
    assert set(schema["schema"]["required"]) == set(
        schema["schema"]["properties"]
    )
    member_schema = schema["schema"]["$defs"]["SemanticMemberAssessment"]
    assert set(member_schema["required"]) == set(member_schema["properties"])
    assert client.calls[0]["temperature"] == 0.0


def test_reviewers_must_have_independent_model_and_prompt_identities(tmp_path) -> None:
    packet, _ = _fixture()
    answer = _assessment(packet)
    first = _CountingAdapter("same-model", answer)
    second = _CountingAdapter("same-model", answer)
    delta = _CountingAdapter("delta-model", _delta_response)

    with pytest.raises(ViewpointResolutionError, match="independent model identities"):
        run_viewpoint_resolution(
            packet=packet,
            proposal_reviewer=first,
            blind_reviewer=second,
            delta_adjudicator=delta,
            output_dir=tmp_path,
            decided_at="2026-08-22T14:47:00+00:00",
        )
    assert (first.calls, second.calls, delta.calls) == (0, 0, 0)
