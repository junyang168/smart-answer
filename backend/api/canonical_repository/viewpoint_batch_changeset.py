"""Compile a reviewed CVP batch into the existing atomic knowledge package.

The semantic models stop at proposal and review.  This module alone assigns
master-data ids, builds the necessary identity audit records, and emits a
package consumable by :class:`PostgresKnowledgeStore`.  It never writes a
database; planning/apply/readback remain explicit store operations.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from collections.abc import Mapping, Sequence
from typing import Any

from .knowledge_models import (
    CanonicalViewpointRecord,
    ViewpointClaimLinkRecord,
    ViewpointIdentityCandidateRecord,
    ViewpointIdentityDecisionRecord,
    ViewpointPropositionSignature,
    ViewpointRelationRecord,
    ViewpointRevisionRecord,
    ViewpointScope,
    ViewpointStructureRecord,
    ViewpointStructureRevisionRecord,
)
from .viewpoint_batch_resolution import (
    CanonicalViewpointProposalResponse,
    CanonicalViewpointReconsiderationResponse,
    CanonicalViewpointReviewResponse,
    NewViewpointCandidate,
    ProposedComponent,
    ProposedViewpointRevision,
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


def _revision_signature(
    revised: ProposedViewpointRevision,
) -> ViewpointPropositionSignature:
    return ViewpointPropositionSignature(
        subject=revised.subject,
        predicate=revised.predicate,
        object=revised.object,
        polarity=revised.polarity,
        modality=revised.modality,
        conditions=sorted(set(revised.conditions)),
        population_scope=sorted(set(revised.population_scope)),
    )


def _revision_scope(revised: ProposedViewpointRevision) -> ViewpointScope:
    return ViewpointScope(scripture_scope=sorted(set(revised.scripture_scope)))


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
    revision_dependents: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
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
    # Only reviewed revisions reach master data: passed outright, or flagged
    # `correct` and then accepted in the one correction round. Gating on `pass`
    # alone silently dropped a revision the reviewer had asked to reword and the
    # proposer had reworded, so the batch reported success while writing nothing.
    approved_revision_targets = {
        item.target_viewpoint_revision_id
        for item in review.revision_reviews
        if item.decision == "pass"
    }
    if reconsideration is not None:
        approved_revision_targets |= {
            item.target_viewpoint_revision_id
            for item in reconsideration.revision_dispositions
            if item.disposition == "accepted"
        }
    # The effective proposal is what gets written, so a revision surviving into
    # it without approval is a contradiction, not something to drop quietly.
    unapproved = sorted(
        item.target_viewpoint_revision_id
        for item in proposal.viewpoint_revisions
        if item.target_viewpoint_revision_id not in approved_revision_targets
    )
    if unapproved:
        raise CvpBatchChangeSetError(
            f"viewpoint revisions are not reviewer-approved: {', '.join(unapproved)}"
        )
    revisions_by_target = {
        item.target_viewpoint_revision_id: item
        for item in proposal.viewpoint_revisions
    }
    dependents = {key: list(value) for key, value in (revision_dependents or {}).items()}
    confirmed_by_target = {
        item.target_viewpoint_revision_id: set(item.confirmed_dependent_ids)
        for item in review.revision_reviews
    }
    for target in revisions_by_target:
        pinned = {dependent_id(item) for item in dependents.get(target, [])}
        unconfirmed = sorted(pinned - confirmed_by_target.get(target, set()))
        if unconfirmed:
            raise CvpBatchChangeSetError(
                f"{target}: revision strands unconfirmed records: {', '.join(unconfirmed)}"
            )

    # Each target is one identity decision. Multiple components from the same
    # Claim and target are combined into one durable locator.
    grouped: dict[tuple[str, str], list[tuple[ReviewClaim, ProposedComponent]]] = defaultdict(list)
    for decision in proposal.claim_decisions:
        claim = claim_index[decision.claim_id]
        for component in decision.components:
            if component.disposition not in LINK_TYPES:
                continue
            # support/qualification/tension may attach to a viewpoint this same
            # batch is creating, in which case they group under its local key and
            # resolve to the allocated CVP id in the same transaction.
            if component.local_new_viewpoint_key:
                target = ("new", str(component.local_new_viewpoint_key))
            else:
                target = ("existing", str(component.target_viewpoint_revision_id))
            grouped[target].append((claim, component))

    # local key or existing revision -> the ids this transaction allocates,
    # so relations and structures can point at viewpoints created in the same
    # ChangeSet without a second apply.
    resolved_targets: dict[tuple[str, str], tuple[str, str]] = {}
    # superseded revision id -> the revision replacing it, and the viewpoint it
    # belongs to, so dependents can be re-pointed once the ids are allocated.
    resolved_revision_by_target: dict[str, str] = {}
    viewpoint_id_by_revision: dict[str, str] = {
        str(key): str(value["viewpoint_id"]) for key, value in registry_by_revision.items()
    }
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
            candidate_viewpoint_ids = [viewpoint_id]
            action = "match_existing"
            revised = revisions_by_target.get(target_key)
            if revised is None:
                revision_id = target_key
                signature = ViewpointPropositionSignature.model_validate(
                    context["proposition_signature"]
                )
            else:
                # The identity is unchanged -- same viewpoint_id -- but its
                # wording moves to a new revision that supersedes the one the
                # packet offered. Everything this batch writes must then bind to
                # the new revision, which is why resolved_targets carries it.
                superseded_number = context.get("revision_number")
                if superseded_number is None:
                    raise CvpBatchChangeSetError(
                        f"Registry context for {target_key} carries no revision_number"
                    )
                signature = _revision_signature(revised)
                revision_seed = {
                    "viewpoint_id": viewpoint_id,
                    "revision_number": int(superseded_number) + 1,
                    "core_proposition": revised.core_proposition,
                    "proposition_signature": signature.model_dump(mode="json"),
                    "scope": _revision_scope(revised).model_dump(mode="json"),
                }
                revision_id = f"CVR-{sha256_json(revision_seed)[:20]}"
                resolved_revision_by_target[target_key] = revision_id

        resolved_targets[target] = (viewpoint_id, revision_id)

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
        elif target_key in revisions_by_target:
            revised = revisions_by_target[target_key]
            context = registry_by_revision[target_key]
            revisions_out.append(
                ViewpointRevisionRecord(
                    viewpoint_revision_id=revision_id,
                    viewpoint_id=viewpoint_id,
                    # The store revision and the semantic revision number are
                    # one and the same; the record refuses them out of step.
                    revision=int(context["revision_number"]) + 1,
                    revision_number=int(context["revision_number"]) + 1,
                    core_proposition=revised.core_proposition,
                    proposition_signature=signature,
                    scope=_revision_scope(revised),
                    supersedes_revision_id=target_key,
                    provenance={
                        "basis_identity_decision_ids": [decision_id],
                        "review_artifact_sha256": review_artifact_sha256,
                        "revision_reason": revised.revision_reason,
                    },
                    approved_by=CVP_BATCH_CHANGESET_POLICY_VERSION,
                    approved_at=decided_at,
                    review_status="system_approved",
                ).model_dump(mode="json")
            )
            # Emitted so the package moves the pointer; the ChangeSet layer
            # diffs it against the store and renders it as an update.
            viewpoints_out.append(
                CanonicalViewpointRecord(
                    viewpoint_id=viewpoint_id,
                    current_revision_id=revision_id,
                    created_from_candidate_id=str(
                        context.get("created_from_candidate_id") or candidate_id
                    ),
                    review_status="system_approved",
                ).model_dump(mode="json")
            )

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

    def _resolve(endpoint: tuple[str, str]) -> tuple[str, str]:
        ids = resolved_targets.get(endpoint)
        if ids is not None:
            return ids
        # A relation may point at a committed viewpoint this batch attaches no
        # Claim to -- drawing the boundary against a neighbour is exactly that
        # case, and it is the whole point of the edge. The packet already
        # carries the neighbour, so the endpoint resolves without a component.
        kind, key = endpoint
        if kind == "existing":
            context = registry_by_revision.get(key)
            if context is not None:
                return str(context["viewpoint_id"]), key
        raise CvpBatchChangeSetError(
            f"{key} has no viewpoint in this ChangeSet"
        )

    relations_out: list[dict[str, Any]] = []
    for relation in proposal.viewpoint_relations:
        source, target = relation.endpoints()
        source_viewpoint_id, source_revision_id = _resolve(source)
        target_viewpoint_id, target_revision_id = _resolve(target)
        relation_seed = {
            "policy_version": CVP_BATCH_CHANGESET_POLICY_VERSION,
            "batch_id": proposal.batch_id,
            "source": source_revision_id,
            "target": target_revision_id,
            "relation_type": relation.relation_type,
        }
        relations_out.append(
            ViewpointRelationRecord(
                viewpoint_relation_id=f"VREL-{sha256_json(relation_seed)[:20]}",
                source_viewpoint_id=source_viewpoint_id,
                target_viewpoint_id=target_viewpoint_id,
                validated_source_viewpoint_revision_id=source_revision_id,
                validated_target_viewpoint_revision_id=target_revision_id,
                relation_type=relation.relation_type,
                reason=relation.reason,
                effective_state="active",
                review_status="system_approved",
            ).model_dump(mode="json")
        )

    structures_out: list[dict[str, Any]] = []
    structure_revisions_out: list[dict[str, Any]] = []
    for structure in proposal.structures:
        focal = [
            {
                "viewpoint_revision_id": _resolve(item.endpoint())[1],
                "structure_role": item.structure_role,
            }
            for item in structure.focal
        ]
        structure_seed = {
            "policy_version": CVP_BATCH_CHANGESET_POLICY_VERSION,
            "batch_id": proposal.batch_id,
            "central_synthesis": structure.central_synthesis,
            "focal": focal,
        }
        structure_id = f"VS-{sha256_json(structure_seed)[:20]}"
        revision_seed = {"structure_id": structure_id, **structure_seed}
        structure_revision_id = f"VSR-{sha256_json(revision_seed)[:20]}"
        structures_out.append(
            ViewpointStructureRecord(
                structure_id=structure_id,
                current_revision_id=structure_revision_id,
                effective_state="active",
                review_status="system_approved",
            ).model_dump(mode="json")
        )
        structure_revisions_out.append(
            ViewpointStructureRevisionRecord(
                structure_revision_id=structure_revision_id,
                structure_id=structure_id,
                revision_number=1,
                central_synthesis=structure.central_synthesis,
                focal_viewpoints=focal,
                unresolved_items=structure.unresolved_items,
                scope_manifest_sha256=scope_manifest_sha256,
                review_status="system_approved",
            ).model_dump(mode="json")
        )

    # Re-point what the superseded wording left behind. Everything here was
    # named by the reviewer as still holding, so the pointer move records a
    # reading that happened rather than asserting one that did not.
    route_revisions_out: list[dict[str, Any]] = []
    routes_out: list[dict[str, Any]] = []
    attestations_out: list[dict[str, Any]] = []
    bumped_route_revisions: dict[str, str] = {}
    for target, items in sorted(dependents.items()):
        if target not in revisions_by_target:
            continue
        new_revision_id = resolved_revision_by_target.get(target)
        if new_revision_id is None:
            continue
        for item in items:
            kind = str(item["record_kind"])
            record = deepcopy(dict(item["record"]))
            if kind == "viewpoint_claim_link":
                record["validated_against_viewpoint_revision_id"] = new_revision_id
                links_out.append(record)
            elif kind == "viewpoint_relation":
                for side in ("source", "target"):
                    if record.get(f"validated_{side}_viewpoint_revision_id") == target:
                        record[f"validated_{side}_viewpoint_revision_id"] = new_revision_id
                relations_out.append(record)
            elif kind == "argument_route_revision":
                previous_id = str(record["argument_route_revision_id"])
                record["validated_against_conclusion_viewpoint_revision_id"] = new_revision_id
                for node in record.get("ordered_inference_nodes") or []:
                    if node.get("conclusion_viewpoint_revision_id") == target:
                        node["conclusion_viewpoint_revision_id"] = new_revision_id
                bumped_number = int(record["revision_number"]) + 1
                record["revision"] = bumped_number
                record["revision_number"] = bumped_number
                record["supersedes_revision_id"] = previous_id
                seed = {
                    "policy_version": CVP_BATCH_CHANGESET_POLICY_VERSION,
                    "argument_route_id": record["argument_route_id"],
                    "revision_number": bumped_number,
                    "conclusion_viewpoint_revision_id": new_revision_id,
                }
                record["argument_route_revision_id"] = f"ARR-{sha256_json(seed)[:20]}"
                record["review_artifact_sha256"] = review_artifact_sha256
                record["approved_by"] = CVP_BATCH_CHANGESET_POLICY_VERSION
                record["approved_at"] = decided_at
                bumped_route_revisions[previous_id] = str(record["argument_route_revision_id"])
                route_revisions_out.append(record)
                routes_out.append(
                    {
                        "argument_route_id": record["argument_route_id"],
                        "current_revision_id": record["argument_route_revision_id"],
                        "conclusion_viewpoint_id": viewpoint_id_by_revision[target],
                        "route_status": "active",
                        "review_status": "system_approved",
                        "schema_version": "wang_argument_route_v1",
                    }
                )
            elif kind == "argument_route_attestation":
                attestations_out.append(record)
    for record in attestations_out:
        previous_id = str(record.get("validated_against_route_revision_id"))
        bumped = bumped_route_revisions.get(previous_id)
        if bumped is None:
            raise CvpBatchChangeSetError(
                f"{record.get('argument_route_attestation_id')}: attestation pins a route "
                "revision this ChangeSet does not move"
            )
        record["validated_against_route_revision_id"] = bumped

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
        "viewpoint_relations": sorted(
            relations_out, key=lambda item: item["viewpoint_relation_id"]
        ),
        "viewpoint_structures": sorted(
            structures_out, key=lambda item: item["structure_id"]
        ),
        "viewpoint_structure_revisions": sorted(
            structure_revisions_out, key=lambda item: item["structure_revision_id"]
        ),
        "viewpoint_claim_links": sorted(
            links_out, key=lambda item: item["viewpoint_claim_link_id"]
        ),
        "argument_routes": sorted(
            routes_out, key=lambda item: item["argument_route_id"]
        ),
        "argument_route_revisions": sorted(
            route_revisions_out, key=lambda item: item["argument_route_revision_id"]
        ),
        "argument_route_attestations": sorted(
            attestations_out, key=lambda item: item["argument_route_attestation_id"]
        ),
    }


#: Record kinds that pin themselves to a viewpoint revision and therefore go
#: stale the moment that revision is superseded.  `viewpoint_runtime_projection`
#: enforces each of these, so a revision that leaves any of them behind cannot
#: be applied at all.
REVISION_DEPENDENT_COLLECTIONS = (
    "viewpoint_claim_links",
    "viewpoint_relations",
    "argument_route_revisions",
)


def load_revision_dependents(
    *, store: Any, target_revision_ids: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """Every committed record that pins itself to these viewpoint revisions.

    Superseding a viewpoint's wording strands them all, and the projection
    validator refuses the write rather than let a record claim it was checked
    against wording nobody checked it against.  They are loaded so the reviewer
    can confirm each one still holds; the ChangeSet only re-points what the
    reviewer confirmed.
    """

    wanted = set(target_revision_ids)
    if not wanted:
        return {}

    found: dict[str, list[dict[str, Any]]] = {key: [] for key in wanted}

    def _add(revision_id: str, kind: str, record: Mapping[str, Any]) -> None:
        if revision_id in wanted:
            found[revision_id].append({"record_kind": kind, "record": dict(record)})

    for record in store.list_records("viewpoint_claim_links"):
        if record.get("effective_state") == "active":
            _add(
                str(record.get("validated_against_viewpoint_revision_id")),
                "viewpoint_claim_link",
                record,
            )
    for record in store.list_records("viewpoint_relations"):
        if record.get("effective_state") != "active":
            continue
        for side in ("source", "target"):
            _add(
                str(record.get(f"validated_{side}_viewpoint_revision_id")),
                "viewpoint_relation",
                record,
            )
    route_revisions = store.list_records("argument_route_revisions")
    routes = {
        str(item["argument_route_id"]): item
        for item in store.list_records("argument_routes")
    }
    stale_route_revisions: set[str] = set()
    for record in route_revisions:
        route = routes.get(str(record.get("argument_route_id"))) or {}
        if route.get("current_revision_id") != record.get("argument_route_revision_id"):
            continue
        revision_id = str(
            record.get("validated_against_conclusion_viewpoint_revision_id")
        )
        if revision_id in wanted:
            stale_route_revisions.add(str(record["argument_route_revision_id"]))
        _add(revision_id, "argument_route_revision", record)
    # An attestation pins the route revision, so bumping the route to follow the
    # viewpoint strands the attestation in turn.
    for record in store.list_records("argument_route_attestations"):
        route_revision_id = str(record.get("validated_against_route_revision_id"))
        if route_revision_id not in stale_route_revisions:
            continue
        for revision_id, items in found.items():
            if any(
                item["record_kind"] == "argument_route_revision"
                and item["record"].get("argument_route_revision_id") == route_revision_id
                for item in items
            ):
                found[revision_id].append(
                    {"record_kind": "argument_route_attestation", "record": dict(record)}
                )

    for items in found.values():
        items.sort(key=lambda item: (item["record_kind"], sha256_json(item["record"])))
    return found


DEPENDENT_ID_FIELDS = {
    "viewpoint_claim_link": "viewpoint_claim_link_id",
    "viewpoint_relation": "viewpoint_relation_id",
    "argument_route_revision": "argument_route_revision_id",
    "argument_route_attestation": "argument_route_attestation_id",
}


def dependent_id(item: Mapping[str, Any]) -> str:
    kind = str(item["record_kind"])
    return str(item["record"][DEPENDENT_ID_FIELDS[kind]])
