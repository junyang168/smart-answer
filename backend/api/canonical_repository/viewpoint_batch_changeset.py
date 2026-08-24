"""Compile a reviewed CVP batch into the existing atomic knowledge package.

The semantic models stop at proposal and review.  This module alone assigns
master-data ids, builds the necessary identity audit records, and emits a
package consumable by :class:`PostgresKnowledgeStore`.  It never writes a
database; planning/apply/readback remain explicit store operations.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .knowledge_models import (
    CanonicalViewpointRecord,
    ViewpointClaimLinkRecord,
    ViewpointIdentityCandidateRecord,
    ViewpointIdentityDecisionRecord,
    ViewpointPropositionSignature,
    ViewpointRevisionRecord,
    ViewpointScope,
)
from .viewpoint_batch_resolution import (
    CanonicalViewpointProposalResponse,
    CanonicalViewpointReconsiderationResponse,
    CanonicalViewpointReviewResponse,
    NewViewpointCandidate,
    ProposedComponent,
    apply_reconsideration_patches,
    validate_reconsideration,
    validate_review,
)
from .viewpoint_foundation import sha256_json
from .viewpoint_resolution import ReviewClaim

CVP_BATCH_CHANGESET_POLICY_VERSION = "cvp_batch_changeset_v1"

LINK_TYPES = {
    "member_existing": "equivalent_component",
    "new_viewpoint": "equivalent_component",
    "support_existing": "supports",
    "qualification_existing": "qualifies",
    "tension_existing": "tension_evidence",
}


class CvpBatchChangeSetError(ValueError):
    pass


def _candidate_signature(candidate: NewViewpointCandidate) -> ViewpointPropositionSignature:
    return ViewpointPropositionSignature(
        subject=candidate.subject,
        predicate=candidate.predicate,
        object=candidate.object,
        polarity=candidate.polarity,
        modality=candidate.modality,
        conditions=sorted(set(candidate.conditions)),
        population_scope=sorted(set(candidate.population_scope)),
    )


def _candidate_scope(candidate: NewViewpointCandidate) -> ViewpointScope:
    return ViewpointScope(scripture_scope=sorted(set(candidate.scripture_scope)))


def compile_cvp_batch_package(
    *,
    proposal: CanonicalViewpointProposalResponse,
    review: CanonicalViewpointReviewResponse,
    reviewed_proposal: CanonicalViewpointProposalResponse | None = None,
    reconsideration: CanonicalViewpointReconsiderationResponse | None = None,
    deterministic_validation_sha256: str,
    scope_manifest_sha256: str,
    claims: Sequence[ReviewClaim],
    registry_context: Sequence[Mapping[str, Any]],
    proposal_artifact_sha256: str,
    review_artifact_sha256: str,
    proposer_model_id: str,
    reviewer_model_id: str,
    decided_at: str,
) -> dict[str, Any]:
    """Create one deterministic, no-write CVP package from a passing review."""

    review_target = reviewed_proposal or proposal
    review_target_sha256 = sha256_json(review_target.model_dump(mode="json"))
    review_validation = validate_review(
        review=review,
        proposal=review_target,
        proposal_sha256=review_target_sha256,
    )
    if review_validation["outcome"] != "pass":
        if reconsideration is None or reviewed_proposal is None:
            raise CvpBatchChangeSetError(
                "CVP ChangeSet requires a passing review or one resolved correction"
            )
        correction_validation = validate_reconsideration(
            reconsideration=reconsideration,
            proposal=reviewed_proposal,
            review=review,
            proposal_sha256=review_target_sha256,
            review_sha256=sha256_json(review.model_dump(mode="json")),
        )
        effective_correction = apply_reconsideration_patches(
            reconsideration=reconsideration,
            proposal=reviewed_proposal,
            review=review,
        )
        if (
            correction_validation["outcome"] != "resolved"
            or effective_correction != proposal
        ):
            raise CvpBatchChangeSetError(
                "CVP ChangeSet correction is unresolved or differs from the effective proposal"
            )
    elif reviewed_proposal is not None or reconsideration is not None:
        raise CvpBatchChangeSetError("passing review must not carry a correction round")
    if proposer_model_id == reviewer_model_id:
        raise CvpBatchChangeSetError("proposal and independent review require different models")

    claim_index = {item.claim_id: item for item in claims}
    if sorted(claim_index) != sorted(item.claim_id for item in proposal.claim_decisions):
        raise CvpBatchChangeSetError("effective proposal does not exactly cover its Claim batch")
    registry_by_revision = {
        str(item["viewpoint_revision_id"]): dict(item) for item in registry_context
    }
    new_candidates = {item.local_key: item for item in proposal.new_viewpoint_candidates}

    # Each target is one identity decision. Multiple components from the same
    # Claim and target are combined into one durable locator.
    grouped: dict[tuple[str, str], list[tuple[ReviewClaim, ProposedComponent]]] = defaultdict(list)
    for decision in proposal.claim_decisions:
        claim = claim_index[decision.claim_id]
        for component in decision.components:
            if component.disposition not in LINK_TYPES:
                continue
            if component.disposition == "new_viewpoint":
                target = ("new", str(component.local_new_viewpoint_key))
            else:
                target = ("existing", str(component.target_viewpoint_revision_id))
            grouped[target].append((claim, component))

    candidates_out: list[dict[str, Any]] = []
    decisions_out: list[dict[str, Any]] = []
    viewpoints_out: list[dict[str, Any]] = []
    revisions_out: list[dict[str, Any]] = []
    links_out: list[dict[str, Any]] = []

    for target, components in sorted(grouped.items()):
        target_kind, target_key = target
        component_facts = sorted(
            (
                claim.claim_id,
                claim.claim_revision_sha256,
                component.disposition,
                component.canonical_spans(),
            )
            for claim, component in components
        )
        generation_fingerprint = sha256_json(
            {
                "policy_version": CVP_BATCH_CHANGESET_POLICY_VERSION,
                "batch_id": proposal.batch_id,
                "proposal_artifact_sha256": proposal_artifact_sha256,
                "review_artifact_sha256": review_artifact_sha256,
                "deterministic_validation_sha256": deterministic_validation_sha256,
                "target": list(target),
                "components": component_facts,
            }
        )
        candidate_claim_ids = sorted({item[0].claim_id for item in components})

        if target_kind == "new":
            semantic = new_candidates.get(target_key)
            if semantic is None:
                raise CvpBatchChangeSetError(f"missing new viewpoint candidate {target_key}")
            candidate_viewpoint_ids: list[str] = []
            action = "create_new"
            signature = _candidate_signature(semantic)
            scope = _candidate_scope(semantic)
            viewpoint_seed = {
                "policy_version": CVP_BATCH_CHANGESET_POLICY_VERSION,
                "core_proposition": semantic.core_proposition,
                "proposition_signature": signature.model_dump(mode="json"),
                "scope": scope.model_dump(mode="json"),
                "component_facts": component_facts,
            }
            viewpoint_id = f"CV-{sha256_json(viewpoint_seed)[:20]}"
            revision_seed = {
                "viewpoint_id": viewpoint_id,
                "revision_number": 1,
                "core_proposition": semantic.core_proposition,
                "proposition_signature": signature.model_dump(mode="json"),
                "scope": scope.model_dump(mode="json"),
            }
            revision_id = f"CVR-{sha256_json(revision_seed)[:20]}"
        else:
            context = registry_by_revision.get(target_key)
            if context is None:
                raise CvpBatchChangeSetError(
                    f"target revision {target_key} is absent from Registry context"
                )
            viewpoint_id = str(context["viewpoint_id"])
            revision_id = target_key
            candidate_viewpoint_ids = [viewpoint_id]
            action = "match_existing"
            signature = ViewpointPropositionSignature.model_validate(
                context["proposition_signature"]
            )

        candidate_identity = {
            "claims": candidate_claim_ids,
            "viewpoints": candidate_viewpoint_ids,
            "relations": [],
            "action": action,
            "blockers": [],
            "coverage_snapshot_id": None,
            "generation_fingerprint": generation_fingerprint,
            "scope_manifest_sha256": scope_manifest_sha256,
        }
        candidate_id = f"VIC-{sha256_json(candidate_identity)[:20]}"
        candidate_record = ViewpointIdentityCandidateRecord(
            identity_candidate_id=candidate_id,
            candidate_claim_ids=candidate_claim_ids,
            candidate_viewpoint_ids=candidate_viewpoint_ids,
            proposed_action=action,
            proposed_proposition_signature=signature,
            scope_manifest_sha256=scope_manifest_sha256,
            generation_fingerprint=generation_fingerprint,
        )
        candidates_out.append(candidate_record.model_dump(mode="json"))

        # The decision authorizes each distinct Claim/link-type pair. Locators
        # retain the finer component boundary on the actual links.
        authorized = sorted(
            {
                (claim.claim_id, LINK_TYPES[component.disposition])
                for claim, component in components
            }
        )
        decision_seed = {
            "candidate_id": candidate_id,
            "viewpoint_id": viewpoint_id,
            "revision_id": revision_id,
            "generation_fingerprint": generation_fingerprint,
            "decided_at": decided_at,
        }
        decision_id = f"VID-{sha256_json(decision_seed)[:20]}"
        decision_record = ViewpointIdentityDecisionRecord(
            identity_decision_id=decision_id,
            identity_candidate_id=candidate_id,
            decision=action,
            resolved_viewpoint_id=viewpoint_id,
            claim_link_decisions=[
                {"claim_id": claim_id, "link_type": link_type}
                for claim_id, link_type in authorized
            ],
            reviewer_kind="system",
            reviewer_id=CVP_BATCH_CHANGESET_POLICY_VERSION,
            approval_basis="dual_model_consensus",
            reason=(
                "Independent semantic review passed and deterministic component "
                "validation succeeded."
            ),
            input_sha256=generation_fingerprint,
            review_artifact_sha256=review_artifact_sha256,
            policy_version=CVP_BATCH_CHANGESET_POLICY_VERSION,
            reviewer_model_ids=sorted({proposer_model_id, reviewer_model_id}),
            semantic_call_artifact_sha256s=sorted(
                {proposal_artifact_sha256, review_artifact_sha256}
            ),
            created_at=decided_at,
            review_status="system_approved",
        )
        decisions_out.append(decision_record.model_dump(mode="json"))

        if target_kind == "new":
            revision = ViewpointRevisionRecord(
                viewpoint_revision_id=revision_id,
                viewpoint_id=viewpoint_id,
                revision_number=1,
                core_proposition=semantic.core_proposition,
                proposition_signature=signature,
                scope=scope,
                provenance={
                    "basis_identity_decision_ids": [decision_id],
                    "review_artifact_sha256": review_artifact_sha256,
                },
                approved_by=CVP_BATCH_CHANGESET_POLICY_VERSION,
                approved_at=decided_at,
                review_status="system_approved",
            )
            viewpoint = CanonicalViewpointRecord(
                viewpoint_id=viewpoint_id,
                current_revision_id=revision_id,
                created_from_candidate_id=candidate_id,
                review_status="system_approved",
            )
            revisions_out.append(revision.model_dump(mode="json"))
            viewpoints_out.append(viewpoint.model_dump(mode="json"))

        per_claim: dict[tuple[str, str], list[ProposedComponent]] = defaultdict(list)
        for claim, component in components:
            per_claim[(claim.claim_id, LINK_TYPES[component.disposition])].append(component)
        for (claim_id, link_type), claim_components in sorted(per_claim.items()):
            claim = claim_index[claim_id]
            spans = sorted(
                {
                    (span.start_char, span.end_char, span.exact_text)
                    for component in claim_components
                    for span in component.spans
                }
            )
            durable_link_type = link_type
            locator = None
            if durable_link_type != "equivalent_full":
                locator = {
                    "statement_component": "".join(item[2] for item in spans),
                    "claim_sha256": claim.claim_revision_sha256,
                    "canonical_spans": [
                        {"start_char": start, "end_char": end, "exact_text": text}
                        for start, end, text in spans
                    ],
                }
            occurrence_seed = {
                "claim_id": claim.claim_id,
                "claim_revision_sha256": claim.claim_revision_sha256,
                "evidence": sorted(
                    {
                        (step_id, fragment_id)
                        for component in claim_components
                        for step_id in component.evidence_step_ids
                        for fragment_id in component.source_fragment_ids
                    }
                ),
            }
            selected_steps = {
                value
                for component in claim_components
                for value in component.evidence_step_ids
            }
            selected_fragments = {
                value
                for component in claim_components
                for value in component.source_fragment_ids
            }
            evidence_bindings = sorted(
                {
                    (item.evidence_step_id, item.source_fragment_id)
                    for item in claim.evidence
                    if item.evidence_step_id in selected_steps
                    and item.source_fragment_id in selected_fragments
                }
            )
            if not evidence_bindings:
                raise CvpBatchChangeSetError(
                    f"{claim_id}: component has no exact EvidenceStep/SourceFragment pair"
                )
            link_seed = {
                "viewpoint_id": viewpoint_id,
                "viewpoint_revision_id": revision_id,
                "claim_id": claim_id,
                "link_type": durable_link_type,
                "component_locator": locator,
                "decision_id": decision_id,
            }
            link = ViewpointClaimLinkRecord(
                viewpoint_claim_link_id=f"VCL-{sha256_json(link_seed)[:20]}",
                viewpoint_id=viewpoint_id,
                validated_against_viewpoint_revision_id=revision_id,
                claim_id=claim_id,
                pinned_claim_revision=claim.pinned_claim_revision,
                link_type=durable_link_type,
                component_locator=locator,
                evidence_bindings=[
                    {
                        "evidence_step_id": step_id,
                        "source_fragment_id": fragment_id,
                    }
                    for step_id, fragment_id in evidence_bindings
                ],
                occurrence_refs=[f"OCC-{sha256_json(occurrence_seed)[:20]}"],
                decision_id=decision_id,
                effective_state="active",
                review_status="system_approved",
            )
            links_out.append(link.model_dump(mode="json"))

    package_identity = {
        "policy_version": CVP_BATCH_CHANGESET_POLICY_VERSION,
        "batch_id": proposal.batch_id,
        "proposal_artifact_sha256": proposal_artifact_sha256,
        "review_artifact_sha256": review_artifact_sha256,
        "deterministic_validation_sha256": deterministic_validation_sha256,
    }
    return {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"CVP-BATCH-{sha256_json(package_identity)[:20]}",
        "viewpoint_identity_candidates": sorted(
            candidates_out, key=lambda item: item["identity_candidate_id"]
        ),
        "viewpoint_identity_decisions": sorted(
            decisions_out, key=lambda item: item["identity_decision_id"]
        ),
        "canonical_viewpoints": sorted(
            viewpoints_out, key=lambda item: item["viewpoint_id"]
        ),
        "viewpoint_revisions": sorted(
            revisions_out, key=lambda item: item["viewpoint_revision_id"]
        ),
        "viewpoint_claim_links": sorted(
            links_out, key=lambda item: item["viewpoint_claim_link_id"]
        ),
    }
