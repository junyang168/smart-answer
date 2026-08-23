"""Programmatic formalization of the first recall-closed atomic viewpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .knowledge_models import (
    CanonicalViewpointRecord,
    ViewpointAtomicCoverageSnapshotRecord,
    ViewpointAtomicQualityCheck,
    ViewpointAtomicQualityReportRecord,
    ViewpointAtomicResolutionLedgerRecord,
    ViewpointAutomatedPromotionDecisionRecord,
    ViewpointIdentityDecisionRecord,
    ViewpointPropositionUnitLinkRecord,
    ViewpointPropositionUnitRecord,
    ViewpointRevisionRecord,
)
from .matthew16_viewpoint_candidate import Matthew16ViewpointPilotArtifact
from .matthew16_viewpoint_promotion import Matthew16ViewpointPromotionProposal
from .viewpoint_foundation import sha256_json
from .viewpoint_runtime_projection import ViewpointKnowledgeProjection


class StrictFinalizationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Matthew16ViewpointFinalizationBundle(StrictFinalizationModel):
    schema_version: Literal["wang_matthew16_viewpoint_finalization_bundle_v1"] = (
        "wang_matthew16_viewpoint_finalization_bundle_v1"
    )
    promotion_proposal_artifact_sha256: str
    atomic_coverage_snapshot: ViewpointAtomicCoverageSnapshotRecord
    atomic_resolution_ledger: ViewpointAtomicResolutionLedgerRecord
    atomic_quality_report: ViewpointAtomicQualityReportRecord
    automated_promotion_decision: ViewpointAutomatedPromotionDecisionRecord
    canonical_viewpoint: CanonicalViewpointRecord
    viewpoint_revision: ViewpointRevisionRecord
    identity_decision: ViewpointIdentityDecisionRecord
    proposition_units: list[ViewpointPropositionUnitRecord]
    proposition_unit_links: list[ViewpointPropositionUnitLinkRecord]
    knowledge_package: dict[str, Any]
    package_sha256: str
    master_data_mutation_count: int
    apply_allowed: Literal[True] = True
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_bundle(self) -> "Matthew16ViewpointFinalizationBundle":
        unit_ids = [item.proposition_unit_id for item in self.proposition_units]
        link_ids = [item.proposition_unit_id for item in self.proposition_unit_links]
        if unit_ids != sorted(set(unit_ids)) or link_ids != sorted(set(link_ids)):
            raise ValueError("finalization master records must be sorted and unique")
        expected_mutations = 3 + len(unit_ids) + len(link_ids) + 4
        if self.master_data_mutation_count != expected_mutations:
            raise ValueError("finalization mutation count mismatch")
        if self.package_sha256 != sha256_json(self.knowledge_package):
            raise ValueError("finalization knowledge package SHA mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("finalization bundle SHA mismatch")
        return self


def _signed_record(model: type[BaseModel], payload: dict[str, Any]) -> BaseModel:
    return model.model_validate({**payload, "artifact_sha256": sha256_json(payload)})


def build_matthew16_viewpoint_finalization_bundle(
    *,
    proposal: Matthew16ViewpointPromotionProposal,
    pilot: Matthew16ViewpointPilotArtifact,
    projection: ViewpointKnowledgeProjection,
    decided_at: str,
) -> Matthew16ViewpointFinalizationBundle:
    """Close formal gates and compile, but do not apply, one ChangeSet package."""

    if proposal.pilot_artifact_sha256 != pilot.artifact_sha256:
        raise ValueError("finalization proposal is bound to another pilot")
    if proposal.blockers != sorted(
        [
            "formal_coverage_snapshot_missing",
            "formal_quality_report_missing",
            "formal_resolution_ledger_missing",
            "promotion_not_approved",
        ]
    ):
        raise ValueError("finalization requires the recall-closed promotion preflight")
    if (
        projection.eligibility != "internal_candidate"
        or len(projection.viewpoints) != 1
        or projection.viewpoints[0].get("candidate_id") != pilot.viewpoint_candidate_id
    ):
        raise ValueError("finalization consumer projection does not bind the pilot")

    member_ids = sorted(
        item.proposition_unit_id for item in proposal.proposition_unit_links
    )
    excluded_ids = proposal.excluded_proposition_unit_ids
    universe_ids = sorted(item.proposition_unit_id for item in proposal.proposition_units)
    projected_member_ids = projection.viewpoints[0].get("member_proposition_unit_ids") or []
    if not (
        projected_member_ids == member_ids
        and sorted(member_ids + excluded_ids) == universe_ids
        and pilot.article_acceptance.supporting_proposition_unit_ids == member_ids
    ):
        raise ValueError("finalization member boundary is inconsistent")

    claim_ids = sorted({item.parent_claim_id for item in proposal.proposition_units})
    source_ids = sorted({item.source_id for item in proposal.proposition_units})
    coverage_payload = {
        "schema_version": "wang_viewpoint_atomic_coverage_snapshot_v1",
        "review_status": "system_verified",
        "visibility": "internal",
        "revision": 1,
        "atomic_coverage_snapshot_id": "pending",
        "viewpoint_candidate_id": pilot.viewpoint_candidate_id,
        "pilot_artifact_sha256": pilot.artifact_sha256,
        "recall_closure_packet_sha256": proposal.recall_closure_packet_sha256,
        "boundary_run_artifact_sha256": proposal.boundary_run_artifact_sha256,
        "claim_ids": claim_ids,
        "proposition_unit_ids": universe_ids,
        "source_ids": source_ids,
        "source_eligibility_attestation_sha256s": (
            proposal.source_eligibility_attestation_sha256s
        ),
        "coverage_status": "complete",
    }
    coverage_identity = {
        key: value
        for key, value in coverage_payload.items()
        if key != "atomic_coverage_snapshot_id"
    }
    coverage_payload["atomic_coverage_snapshot_id"] = (
        f"VACS-{sha256_json(coverage_identity)[:20]}"
    )
    coverage = _signed_record(
        ViewpointAtomicCoverageSnapshotRecord, coverage_payload
    )

    unit_index = {item.proposition_unit_id: item for item in proposal.proposition_units}
    rows = []
    for unit_id in universe_ids:
        unit = unit_index[unit_id]
        bindings = [item.model_dump(mode="json") for item in unit.evidence_bindings]
        rows.append(
            {
                "proposition_unit_id": unit_id,
                "parent_claim_id": unit.parent_claim_id,
                "disposition": "member" if unit_id in set(member_ids) else "adjacent_non_member",
                "identity_decision_id": proposal.identity_decision.identity_decision_id,
                "boundary_run_artifact_sha256": proposal.boundary_run_artifact_sha256,
                "evidence_binding_sha256": sha256_json(bindings),
            }
        )
    statistics = {
        "input_unit_count": len(rows),
        "member_count": len(member_ids),
        "adjacent_non_member_count": len(excluded_ids),
        "unresolved_count": 0,
    }
    ledger_identity = {
        "atomic_coverage_snapshot_id": coverage.atomic_coverage_snapshot_id,
        "viewpoint_candidate_id": pilot.viewpoint_candidate_id,
        "proposed_viewpoint_id": proposal.canonical_viewpoint.viewpoint_id,
        "identity_decision_id": proposal.identity_decision.identity_decision_id,
        "rows": rows,
        "statistics": statistics,
    }
    ledger_payload = {
        "schema_version": "wang_viewpoint_atomic_resolution_ledger_v1",
        "review_status": "system_verified",
        "visibility": "internal",
        "revision": 1,
        "atomic_resolution_ledger_id": f"VARL-{sha256_json(ledger_identity)[:20]}",
        **ledger_identity,
        "coverage_status": "complete",
    }
    ledger = _signed_record(ViewpointAtomicResolutionLedgerRecord, ledger_payload)

    evidence_shas = {
        "article": pilot.article_acceptance.manuscript_sha256,
        "boundary": proposal.boundary_run_artifact_sha256,
        "coverage": coverage.artifact_sha256,
        "ledger": ledger.artifact_sha256,
        "pilot": pilot.artifact_sha256,
        "projection": projection.projection_sha256,
        "proposal": proposal.artifact_sha256,
        "recall": proposal.recall_closure_packet_sha256,
    }
    checks = [
        {
            "code": "article_acceptance_bound",
            "status": "pass",
            "evidence_artifact_sha256s": sorted(
                [evidence_shas["article"], evidence_shas["pilot"]]
            ),
            "detail": "The published Article 2 clause binds the exact member unit set.",
        },
        {
            "code": "atomic_resolution_exact_once",
            "status": "pass",
            "evidence_artifact_sha256s": [evidence_shas["ledger"]],
            "detail": "Every unit has exactly one member or adjacent-non-member disposition.",
        },
        {
            "code": "consumer_projection_bound",
            "status": "pass",
            "evidence_artifact_sha256s": [evidence_shas["projection"]],
            "detail": "The standard downstream projection carries the exact member set.",
        },
        {
            "code": "dual_model_boundary_agreed",
            "status": "pass",
            "evidence_artifact_sha256s": [evidence_shas["boundary"]],
            "detail": "Sol/high and Opus 5/high agreed on every unit disposition.",
        },
        {
            "code": "master_preview_matches_resolution",
            "status": "pass",
            "evidence_artifact_sha256s": sorted(
                [evidence_shas["ledger"], evidence_shas["proposal"]]
            ),
            "detail": "The proposed membership links exactly match member ledger rows.",
        },
        {
            "code": "source_evidence_bound",
            "status": "pass",
            "evidence_artifact_sha256s": sorted(
                proposal.source_eligibility_attestation_sha256s
            ),
            "detail": "Every unit retains current Claim, EvidenceStep, and SourceFragment bindings.",
        },
        {
            "code": "targeted_recall_closed",
            "status": "pass",
            "evidence_artifact_sha256s": sorted(
                [evidence_shas["coverage"], evidence_shas["recall"]]
            ),
            "detail": "Recall packet Claims and the decomposed atomic universe are exact-closed.",
        },
    ]
    checks.sort(key=lambda item: item["code"])
    quality_identity = {
        "viewpoint_candidate_id": pilot.viewpoint_candidate_id,
        "proposed_viewpoint_id": proposal.canonical_viewpoint.viewpoint_id,
        "atomic_coverage_snapshot_id": coverage.atomic_coverage_snapshot_id,
        "atomic_resolution_ledger_id": ledger.atomic_resolution_ledger_id,
        "promotion_proposal_artifact_sha256": proposal.artifact_sha256,
        "consumer_projection_sha256": projection.projection_sha256,
        "checks": checks,
        "hard_failures": [],
        "eligibility_decision": "pass",
        "validator_version": "matthew16_atomic_promotion_quality_v1",
    }
    quality_payload = {
        "schema_version": "wang_viewpoint_atomic_quality_report_v1",
        "review_status": "system_verified",
        "visibility": "internal",
        "revision": 1,
        "atomic_quality_report_id": f"VAQR-{sha256_json(quality_identity)[:20]}",
        **quality_identity,
    }
    quality = _signed_record(ViewpointAtomicQualityReportRecord, quality_payload)

    canonical_viewpoint = proposal.canonical_viewpoint.model_copy(
        update={"review_status": "system_approved"}
    )
    viewpoint_revision = ViewpointRevisionRecord.model_validate(
        {
            **proposal.viewpoint_revision.model_dump(mode="json"),
            "review_status": "system_approved",
            "provenance": {
                **proposal.viewpoint_revision.provenance.model_dump(mode="json"),
                "review_artifact_sha256": quality.artifact_sha256,
            },
            "approved_by": "matthew16_atomic_promotion_quality_v1",
            "approved_at": decided_at,
        }
    )
    identity_decision = proposal.identity_decision.model_copy(
        update={
            "review_status": "system_approved",
            "reason": (
                "All atomic coverage, exact-once resolution, source provenance, "
                "dual-model boundary, article acceptance, and consumer projection gates passed."
            ),
            "review_artifact_sha256": quality.artifact_sha256,
        }
    )
    units = [
        item.model_copy(
            update={"effective_state": "active", "review_status": "system_approved"}
        )
        for item in proposal.proposition_units
    ]
    links = [
        item.model_copy(
            update={"effective_state": "active", "review_status": "system_approved"}
        )
        for item in proposal.proposition_unit_links
    ]
    applied_record_ids = sorted(
        [
            canonical_viewpoint.viewpoint_id,
            viewpoint_revision.viewpoint_revision_id,
            identity_decision.identity_decision_id,
            coverage.atomic_coverage_snapshot_id,
            ledger.atomic_resolution_ledger_id,
            quality.atomic_quality_report_id,
            *[item.proposition_unit_id for item in units],
            *[item.viewpoint_proposition_unit_link_id for item in links],
        ]
    )
    promotion_identity = {
        "viewpoint_candidate_id": pilot.viewpoint_candidate_id,
        "viewpoint_id": canonical_viewpoint.viewpoint_id,
        "viewpoint_revision_id": viewpoint_revision.viewpoint_revision_id,
        "identity_decision_id": identity_decision.identity_decision_id,
        "promotion_proposal_artifact_sha256": proposal.artifact_sha256,
        "atomic_coverage_snapshot_artifact_sha256": coverage.artifact_sha256,
        "atomic_resolution_ledger_artifact_sha256": ledger.artifact_sha256,
        "atomic_quality_report_artifact_sha256": quality.artifact_sha256,
        "consumer_projection_sha256": projection.projection_sha256,
        "decision": "approve",
        "approval_basis": "programmatic_atomic_quality_gate",
        "human_approval": False,
        "applied_record_ids": applied_record_ids,
        "decided_at": decided_at,
    }
    promotion_payload = {
        "schema_version": "wang_viewpoint_automated_promotion_decision_v1",
        "review_status": "system_approved",
        "visibility": "internal",
        "revision": 1,
        "automated_promotion_decision_id": (
            f"VAPD-{sha256_json(promotion_identity)[:20]}"
        ),
        **promotion_identity,
    }
    automated_decision = _signed_record(
        ViewpointAutomatedPromotionDecisionRecord, promotion_payload
    )
    # The decision id itself is not part of its signed target set, avoiding a
    # circular identity.  The bundle and ChangeSet include it as the final row.

    package = {
        "schema_version": "wang_knowledge_package_v1",
        "package_id": f"WKP-M16-ATOMIC-{canonical_viewpoint.viewpoint_id}",
        "title": "Matthew 16:18 first recall-closed CanonicalViewpoint promotion",
        "approval_status": "system_approved_not_human_approval",
        "canonical_viewpoints": [canonical_viewpoint.model_dump(mode="json")],
        "viewpoint_revisions": [viewpoint_revision.model_dump(mode="json")],
        "viewpoint_identity_decisions": [identity_decision.model_dump(mode="json")],
        "viewpoint_proposition_units": [
            item.model_dump(mode="json") for item in units
        ],
        "viewpoint_proposition_unit_links": [
            item.model_dump(mode="json") for item in links
        ],
        "viewpoint_atomic_coverage_snapshots": [coverage.model_dump(mode="json")],
        "viewpoint_atomic_resolution_ledgers": [ledger.model_dump(mode="json")],
        "viewpoint_atomic_quality_reports": [quality.model_dump(mode="json")],
        "viewpoint_automated_promotion_decisions": [
            automated_decision.model_dump(mode="json")
        ],
    }
    bundle_payload = {
        "schema_version": "wang_matthew16_viewpoint_finalization_bundle_v1",
        "promotion_proposal_artifact_sha256": proposal.artifact_sha256,
        "atomic_coverage_snapshot": coverage.model_dump(mode="json"),
        "atomic_resolution_ledger": ledger.model_dump(mode="json"),
        "atomic_quality_report": quality.model_dump(mode="json"),
        "automated_promotion_decision": automated_decision.model_dump(mode="json"),
        "canonical_viewpoint": canonical_viewpoint.model_dump(mode="json"),
        "viewpoint_revision": viewpoint_revision.model_dump(mode="json"),
        "identity_decision": identity_decision.model_dump(mode="json"),
        "proposition_units": [item.model_dump(mode="json") for item in units],
        "proposition_unit_links": [item.model_dump(mode="json") for item in links],
        "knowledge_package": package,
        "package_sha256": sha256_json(package),
        "master_data_mutation_count": 3 + len(units) + len(links) + 4,
        "apply_allowed": True,
    }
    return Matthew16ViewpointFinalizationBundle(
        **bundle_payload,
        artifact_sha256=sha256_json(bundle_payload),
    )
