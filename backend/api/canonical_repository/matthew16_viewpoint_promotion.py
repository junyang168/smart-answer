"""Fail-closed master-data promotion proposal for the first Matthew 16 viewpoint."""

from __future__ import annotations

from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_models import (
    CanonicalViewpointRecord,
    ViewpointIdentityDecisionRecord,
    ViewpointPropositionUnitLinkRecord,
    ViewpointPropositionUnitRecord,
    ViewpointRevisionRecord,
)
from .matthew16_viewpoint_candidate import (
    Matthew16ViewpointPilotArtifact,
    ViewpointKnowledgeClassification,
    classify_pilot_viewpoint,
)
from .viewpoint_foundation import sha256_json
from .viewpoint_proposition_units import ClaimAtomicDecompositionArtifact
from .viewpoint_resolution import ViewpointIdentityReviewPacket


class StrictPromotionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromotionQualityCheck(StrictPromotionModel):
    code: Literal[
        "atomic_universe_closed",
        "dual_model_boundary_agreed",
        "member_nonmember_disjoint",
        "article_acceptance_bound",
        "source_evidence_bound",
        "master_membership_is_atomic",
        "targeted_recall_closed",
    ]
    status: Literal["pass"] = "pass"
    checked_record_ids: list[str]
    detail: str


class Matthew16ViewpointPromotionProposal(StrictPromotionModel):
    schema_version: Literal["wang_matthew16_viewpoint_promotion_proposal_v1"] = (
        "wang_matthew16_viewpoint_promotion_proposal_v1"
    )
    pilot_artifact_sha256: str
    boundary_run_artifact_sha256: str
    recall_closure_packet_sha256: str
    recall_closure_claim_ids: list[str]
    source_eligibility_attestation_sha256s: list[str]
    knowledge_classification: ViewpointKnowledgeClassification
    proposed_at: str
    canonical_viewpoint: CanonicalViewpointRecord
    viewpoint_revision: ViewpointRevisionRecord
    identity_decision: ViewpointIdentityDecisionRecord
    proposition_units: list[ViewpointPropositionUnitRecord] = Field(min_length=1)
    proposition_unit_links: list[ViewpointPropositionUnitLinkRecord] = Field(min_length=1)
    excluded_proposition_unit_ids: list[str]
    quality_checks: list[PromotionQualityCheck]
    blockers: list[Literal[
        "formal_coverage_snapshot_missing",
        "formal_resolution_ledger_missing",
        "formal_quality_report_missing",
        "targeted_recall_closure_missing",
        "promotion_not_approved",
    ]]
    claim_membership_link_count: Literal[0] = 0
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "Matthew16ViewpointPromotionProposal":
        unit_ids = [item.proposition_unit_id for item in self.proposition_units]
        link_unit_ids = [item.proposition_unit_id for item in self.proposition_unit_links]
        if unit_ids != sorted(set(unit_ids)):
            raise ValueError("promotion proposition units must be sorted and unique")
        if link_unit_ids != sorted(set(link_unit_ids)):
            raise ValueError("promotion membership links must be unit-sorted and unique")
        if self.excluded_proposition_unit_ids != sorted(
            set(self.excluded_proposition_unit_ids)
        ):
            raise ValueError("excluded proposition units must be sorted and unique")
        if set(link_unit_ids) & set(self.excluded_proposition_unit_ids):
            raise ValueError("member and excluded proposition units overlap")
        if sorted(link_unit_ids + self.excluded_proposition_unit_ids) != unit_ids:
            raise ValueError("promotion does not close the proposition unit universe")
        if self.blockers != sorted(set(self.blockers)):
            raise ValueError("promotion blockers must be sorted and unique")
        check_codes = [item.code for item in self.quality_checks]
        if check_codes != sorted(set(check_codes)):
            raise ValueError("promotion quality checks must be sorted and unique")
        if any(item.effective_state != "proposed" for item in self.proposition_units):
            raise ValueError("proposal may not activate proposition unit master data")
        if any(item.effective_state != "proposed" for item in self.proposition_unit_links):
            raise ValueError("proposal may not activate viewpoint membership")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("promotion proposal artifact SHA mismatch")
        return self


def build_matthew16_viewpoint_promotion_proposal(
    *,
    pilot: Matthew16ViewpointPilotArtifact,
    boundary_run: dict[str, Any],
    evidence_packet: ViewpointIdentityReviewPacket,
    decompositions: Sequence[ClaimAtomicDecompositionArtifact],
    proposed_at: str,
) -> Matthew16ViewpointPromotionProposal:
    """Compile exact atomic master previews without authorizing or applying them."""

    if boundary_run.get("artifact_sha256") != pilot.boundary_run_artifact_sha256:
        raise ValueError("promotion boundary run does not match pilot")
    if not (
        boundary_run.get("semantic_agreement") is True
        and boundary_run.get("synthesis_eligible") is True
        and sorted(boundary_run.get("model_ids") or []) == pilot.model_ids
    ):
        raise ValueError("promotion requires the agreed dual-model boundary")
    expected_decomposition_shas = sorted(
        boundary_run.get("decomposition_artifact_sha256s") or []
    )
    if sorted(item.artifact_sha256 for item in decompositions) != expected_decomposition_shas:
        raise ValueError("promotion decomposition artifacts do not match boundary")

    decomposition_claim_ids = sorted(item.claim.claim_id for item in decompositions)
    packet_claim_ids = sorted(item.claim_id for item in evidence_packet.claims)
    candidate_claim_ids = sorted(evidence_packet.candidate.candidate_claim_ids)
    parent_packet_shas = {item.parent_packet_sha256 for item in decompositions}
    if not (
        not evidence_packet.deterministic_blockers
        and decomposition_claim_ids == packet_claim_ids == candidate_claim_ids
        and parent_packet_shas == {evidence_packet.packet_sha256}
        and all(
            any(evidence.valid_for_identity_review for evidence in claim.evidence)
            and claim.source_eligibility_attestation_sha256
            for claim in evidence_packet.claims
        )
    ):
        raise ValueError("promotion requires an exact, unblocked, source-attested recall packet")
    source_attestation_shas = sorted({
        str(claim.source_eligibility_attestation_sha256)
        for claim in evidence_packet.claims
    })

    candidate_units = {
        unit.proposition_unit_id: (unit, decomposition.artifact_sha256)
        for decomposition in decompositions
        for unit in decomposition.proposition_units
    }
    universe_ids = sorted(candidate_units)
    if universe_ids != sorted(boundary_run.get("unit_universe_ids") or []):
        raise ValueError("promotion proposition unit universe is incomplete")
    member_ids = sorted(
        item.proposition_unit.proposition_unit_id for item in pilot.members
    )
    excluded_ids = sorted(item.proposition_unit_id for item in pilot.adjacent_non_members)
    if member_ids != sorted(boundary_run.get("participant_unit_ids") or []):
        raise ValueError("promotion member boundary differs from pilot")
    if excluded_ids != sorted(boundary_run.get("adjacent_unit_ids") or []):
        raise ValueError("promotion excluded boundary differs from pilot")

    classification = classify_pilot_viewpoint(pilot)
    identity_seed = {
        "pilot_artifact_sha256": pilot.artifact_sha256,
        "boundary_run_artifact_sha256": pilot.boundary_run_artifact_sha256,
        "core_proposition": pilot.core_proposition,
        "proposition_signature": pilot.proposition_signature.model_dump(mode="json"),
        "scope": pilot.scope.model_dump(mode="json"),
    }
    viewpoint_id = f"CV-{sha256_json(identity_seed)[:20]}"
    revision_seed = {"viewpoint_id": viewpoint_id, **identity_seed}
    revision_id = f"CVR-{sha256_json(revision_seed)[:20]}"
    decision_seed = {
        "viewpoint_candidate_id": pilot.viewpoint_candidate_id,
        "viewpoint_id": viewpoint_id,
        "revision_id": revision_id,
        "boundary_run_artifact_sha256": pilot.boundary_run_artifact_sha256,
    }
    decision_id = f"VID-{sha256_json(decision_seed)[:20]}"

    decision = ViewpointIdentityDecisionRecord(
        identity_decision_id=decision_id,
        identity_candidate_id=pilot.viewpoint_candidate_id,
        decision="create_new",
        resolved_viewpoint_id=viewpoint_id,
        proposition_unit_link_decisions=[
            {"proposition_unit_id": unit_id, "link_type": "equivalent"}
            for unit_id in member_ids
        ],
        reviewer_kind="system",
        reviewer_id="matthew16_atomic_promotion_v1",
        approval_basis="dual_model_consensus",
        reason=(
            "Promotion preview only: the two boundary reviewers agreed on all "
            f"{len(universe_ids)} atomic units; formal coverage, ledger, and quality approval remain pending."
        ),
        input_sha256=pilot.boundary_run_artifact_sha256,
        review_artifact_sha256=pilot.boundary_run_artifact_sha256,
        policy_version="matthew16_atomic_promotion_v1",
        reviewer_model_ids=pilot.model_ids,
        semantic_call_artifact_sha256s=sorted(
            boundary_run.get("assessment_artifact_sha256s") or []
        ),
        created_at=proposed_at,
        review_status="candidate",
    )
    revision = ViewpointRevisionRecord(
        viewpoint_revision_id=revision_id,
        viewpoint_id=viewpoint_id,
        revision_number=1,
        core_proposition=pilot.core_proposition,
        proposition_signature=pilot.proposition_signature,
        scope=pilot.scope,
        provenance={
            "basis_identity_decision_ids": [decision_id],
            "review_artifact_sha256": pilot.boundary_run_artifact_sha256,
        },
        review_status="candidate",
    )
    viewpoint = CanonicalViewpointRecord(
        viewpoint_id=viewpoint_id,
        current_revision_id=revision_id,
        created_from_candidate_id=pilot.viewpoint_candidate_id,
        review_status="candidate",
    )

    unit_records: list[ViewpointPropositionUnitRecord] = []
    for unit_id in universe_ids:
        unit, decomposition_sha = candidate_units[unit_id]
        unit_records.append(
            ViewpointPropositionUnitRecord(
                proposition_unit_id=unit.proposition_unit_id,
                parent_claim_id=unit.parent_claim_id,
                pinned_claim_revision=unit.pinned_claim_revision,
                claim_revision_sha256=unit.claim_revision_sha256,
                source_id=unit.source_id,
                unit_statement=unit.unit_statement,
                structural_role=unit.structural_role,
                claim_statement_spans=[
                    item.model_dump(mode="json") for item in unit.claim_statement_spans
                ],
                evidence_bindings=[
                    {
                        "evidence_step_id": evidence_step_id,
                        "source_fragment_id": source_fragment_id,
                    }
                    for evidence_step_id, source_fragment_id in sorted(
                        {
                            (item.evidence_step_id, item.source_fragment_id)
                            for item in unit.evidence
                        }
                    )
                ],
                decomposition_artifact_sha256=decomposition_sha,
                effective_state="proposed",
                review_status="candidate",
            )
        )
    unit_links = [
        ViewpointPropositionUnitLinkRecord(
            viewpoint_proposition_unit_link_id=f"VPUL-{sha256_json({
                'viewpoint_id': viewpoint_id,
                'viewpoint_revision_id': revision_id,
                'proposition_unit_id': unit_id,
                'decision_id': decision_id,
            })[:20]}",
            viewpoint_id=viewpoint_id,
            validated_against_viewpoint_revision_id=revision_id,
            proposition_unit_id=unit_id,
            decision_id=decision_id,
            effective_state="proposed",
            review_status="candidate",
        )
        for unit_id in member_ids
    ]
    evidence_ids = sorted(
        {
            binding.evidence_step_id
            for unit in unit_records
            for binding in unit.evidence_bindings
        }
    )
    checks = [
        PromotionQualityCheck(
            code="article_acceptance_bound",
            checked_record_ids=[pilot.article_acceptance.draft_id],
            detail=f"Article proposition binds the exact {len(member_ids)} member units.",
        ),
        PromotionQualityCheck(
            code="atomic_universe_closed",
            checked_record_ids=universe_ids,
            detail=f"All {len(universe_ids)} reviewed units are represented exactly once.",
        ),
        PromotionQualityCheck(
            code="dual_model_boundary_agreed",
            checked_record_ids=pilot.model_ids,
            detail="Both independent reviewers agreed on every unit disposition.",
        ),
        PromotionQualityCheck(
            code="master_membership_is_atomic",
            checked_record_ids=member_ids,
            detail=f"{len(member_ids)} unit links and zero Claim-level membership links are proposed.",
        ),
        PromotionQualityCheck(
            code="member_nonmember_disjoint",
            checked_record_ids=universe_ids,
            detail=(
                f"{len(member_ids)} members and {len(excluded_ids)} excluded adjacent units "
                "are disjoint and exhaustive."
            ),
        ),
        PromotionQualityCheck(
            code="source_evidence_bound",
            checked_record_ids=evidence_ids,
            detail="Every promoted unit retains its EvidenceStep and SourceFragment bindings.",
        ),
        PromotionQualityCheck(
            code="targeted_recall_closed",
            checked_record_ids=packet_claim_ids,
            detail=(
                "The unblocked source-attested recall packet, atomic decompositions, and "
                "reviewed Claim universe are exact-equal."
            ),
        ),
    ]
    payload: dict[str, Any] = {
        "schema_version": "wang_matthew16_viewpoint_promotion_proposal_v1",
        "pilot_artifact_sha256": pilot.artifact_sha256,
        "boundary_run_artifact_sha256": pilot.boundary_run_artifact_sha256,
        "recall_closure_packet_sha256": evidence_packet.packet_sha256,
        "recall_closure_claim_ids": packet_claim_ids,
        "source_eligibility_attestation_sha256s": source_attestation_shas,
        "knowledge_classification": classification.model_dump(mode="json"),
        "proposed_at": proposed_at,
        "canonical_viewpoint": viewpoint.model_dump(mode="json"),
        "viewpoint_revision": revision.model_dump(mode="json"),
        "identity_decision": decision.model_dump(mode="json"),
        "proposition_units": [item.model_dump(mode="json") for item in unit_records],
        "proposition_unit_links": [item.model_dump(mode="json") for item in unit_links],
        "excluded_proposition_unit_ids": excluded_ids,
        "quality_checks": [item.model_dump(mode="json") for item in sorted(checks, key=lambda row: row.code)],
        "blockers": sorted([
            "formal_coverage_snapshot_missing",
            "formal_quality_report_missing",
            "formal_resolution_ledger_missing",
            "promotion_not_approved",
        ]),
        "claim_membership_link_count": 0,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    return Matthew16ViewpointPromotionProposal(
        **payload,
        artifact_sha256=sha256_json(payload),
    )
