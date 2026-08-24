"""Compile reviewed ArgumentRoutes into an atomic Registry package."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .knowledge_models import (
    ArgumentRouteAttestationRecord,
    ArgumentRouteRecord,
    ArgumentRouteRevisionRecord,
    ArgumentRouteSignature,
)
from .viewpoint_batch_resolution import ArgumentRouteProposalResponse
from .viewpoint_foundation import sha256_json
from .viewpoint_resolution import ReviewClaim

ROUTE_CHANGESET_POLICY_VERSION = "argument_route_changeset_v2"


class ArgumentRouteChangeSetError(ValueError):
    pass


def compile_argument_route_package(
    *,
    proposal: ArgumentRouteProposalResponse,
    passing_route_keys: Sequence[str],
    passing_attestation_keys: Sequence[str],
    route_packet: Mapping[str, Any],
    existing_routes: Sequence[Mapping[str, Any]],
    claims: Sequence[ReviewClaim],
    proposal_artifact_sha256: str,
    review_artifact_sha256: str,
    proposer_model_id: str,
    reviewer_model_id: str,
    decided_at: str,
) -> dict[str, Any]:
    """Assign durable v2 route ids after semantic and independent review."""

    if proposer_model_id == reviewer_model_id:
        raise ArgumentRouteChangeSetError("Route proposal and review require different models")
    approved_revisions = set(route_packet.get("approved_viewpoint_revision_ids") or [])
    approved_context = {
        str(item["viewpoint_revision_id"]): dict(item)
        for item in route_packet.get("approved_viewpoints") or []
    }
    if set(approved_context) != approved_revisions:
        raise ArgumentRouteChangeSetError("Route packet approved viewpoint cut is inconsistent")
    component_index = {
        str(item["claim_component_key"]): dict(item)
        for item in route_packet.get("claim_components") or []
    }
    evidence_index = {
        evidence.evidence_step_id: evidence
        for claim in claims
        for evidence in claim.evidence
    }
    existing_revision_index: dict[str, dict[str, Any]] = {}
    for raw in existing_routes:
        item = dict(raw)
        revision = item.get("revision") if isinstance(item.get("revision"), Mapping) else item
        revision_id = str(
            revision.get("argument_route_revision_id")
            or revision.get("route_revision_id")
            or ""
        )
        if revision_id:
            existing_revision_index[revision_id] = item

    route_candidates = {item.local_route_key: item for item in proposal.argument_route_candidates}
    accepted_route_keys = sorted(set(passing_route_keys))
    if any(key not in route_candidates for key in accepted_route_keys):
        raise ArgumentRouteChangeSetError("passing route key is absent from proposal")
    route_records: list[dict[str, Any]] = []
    revision_records: list[dict[str, Any]] = []
    resolved_routes: dict[str, tuple[str, str]] = {}

    for key in accepted_route_keys:
        candidate = route_candidates[key]
        conclusion_revision_id = candidate.conclusion_ref.key()
        conclusion = approved_context.get(conclusion_revision_id)
        if conclusion is None:
            raise ArgumentRouteChangeSetError(f"{key}: conclusion is outside approved cut")
        conclusion_viewpoint_id = str(conclusion["viewpoint_id"])
        if candidate.proposed_action == "defer":
            raise ArgumentRouteChangeSetError(f"{key}: deferred route cannot be applied")
        if candidate.proposed_action == "match_existing":
            existing = existing_revision_index.get(
                str(candidate.target_argument_route_revision_id)
            )
            if existing is None:
                raise ArgumentRouteChangeSetError(f"{key}: existing route revision is absent")
            revision = existing.get("revision") if isinstance(existing.get("revision"), Mapping) else existing
            route_id = str(
                existing.get("argument_route_id")
                or revision.get("argument_route_id")
                or ""
            )
            revision_id = str(candidate.target_argument_route_revision_id)
            if not route_id:
                raise ArgumentRouteChangeSetError(f"{key}: existing route identity is absent")
        else:
            nodes = []
            for node in candidate.ordered_inference_nodes:
                nodes.append(
                    {
                        "route_step_key": node.route_step_key,
                        "role": node.role,
                        "normalized_proposition": node.normalized_proposition,
                        "conclusion_viewpoint_revision_id": (
                            node.conclusion_ref.key() if node.conclusion_ref else None
                        ),
                        "required_for_full_attestation": node.required_for_full_attestation,
                    }
                )
            signature = ArgumentRouteSignature(
                inference_method_codes=sorted(set(candidate.inference_method_codes)),
                inference_method_note=candidate.inference_method_note,
                conclusion_viewpoint_id=conclusion_viewpoint_id,
            )
            identity_seed = {
                "policy_version": ROUTE_CHANGESET_POLICY_VERSION,
                "conclusion_viewpoint_id": conclusion_viewpoint_id,
                "conclusion_viewpoint_revision_id": conclusion_revision_id,
                "route_signature": signature.model_dump(mode="json"),
                "ordered_inference_nodes": nodes,
            }
            route_id = f"AR-{sha256_json(identity_seed)[:20]}"
            revision_id = f"ARR-{sha256_json({**identity_seed, 'revision_number': 1})[:20]}"
            revision = ArgumentRouteRevisionRecord(
                argument_route_revision_id=revision_id,
                argument_route_id=route_id,
                revision_number=1,
                validated_against_conclusion_viewpoint_revision_id=conclusion_revision_id,
                route_label=candidate.route_label,
                route_signature=signature,
                ordered_inference_nodes=nodes,
                review_artifact_sha256=review_artifact_sha256,
                approved_by=ROUTE_CHANGESET_POLICY_VERSION,
                approved_at=decided_at,
                review_status="system_approved",
            )
            route = ArgumentRouteRecord(
                argument_route_id=route_id,
                conclusion_viewpoint_id=conclusion_viewpoint_id,
                current_revision_id=revision_id,
                review_status="system_approved",
            )
            revision_records.append(revision.model_dump(mode="json"))
            route_records.append(route.model_dump(mode="json"))
        resolved_routes[key] = (route_id, revision_id)

    attestation_candidates = {
        item.local_attestation_key: item for item in proposal.source_route_attestations
    }
    accepted_attestation_keys = sorted(set(passing_attestation_keys))
    if any(key not in attestation_candidates for key in accepted_attestation_keys):
        raise ArgumentRouteChangeSetError("passing attestation key is absent from proposal")
    attestation_records: list[dict[str, Any]] = []
    for key in accepted_attestation_keys:
        attestation = attestation_candidates[key]
        local_route_key = attestation.route_ref.local_route_key
        if not local_route_key or local_route_key not in resolved_routes:
            raise ArgumentRouteChangeSetError(f"{key}: route did not pass review")
        route_id, revision_id = resolved_routes[local_route_key]
        terminal = component_index.get(str(attestation.terminal_claim_component_key))
        if terminal is None or not terminal.get("viewpoint_claim_link_id"):
            raise ArgumentRouteChangeSetError(f"{key}: terminal component has no active Claim link")
        occurrence_refs = sorted(terminal.get("occurrence_ref_ids") or [])
        if not occurrence_refs:
            raise ArgumentRouteChangeSetError(f"{key}: terminal Claim link has no occurrence")

        step_bindings = []
        evidence_ids: set[str] = set()
        for binding in attestation.step_bindings:
            for component_key in binding.claim_component_keys:
                if component_key not in component_index:
                    raise ArgumentRouteChangeSetError(
                        f"{key}: unknown Claim component {component_key}"
                    )
            evidence_ids.update(binding.evidence_step_ids)
            step_bindings.append(
                {
                    "route_step_key": binding.route_step_key,
                    "claim_component_keys": sorted(set(binding.claim_component_keys)),
                    "evidence_step_ids": sorted(set(binding.evidence_step_ids)),
                    "source_fragment_ids": sorted(set(binding.source_fragment_ids)),
                    "attestation_status": binding.attestation_status,
                }
            )
        scripture_refs = sorted(
            {
                ref
                for evidence_id in evidence_ids
                for ref in evidence_index[evidence_id].scripture_refs
            }
        )
        attestation_seed = {
            "policy_version": ROUTE_CHANGESET_POLICY_VERSION,
            "route_revision_id": revision_id,
            "source_id": attestation.source_id,
            "source_revision_sha256": attestation.source_revision_sha256,
            "claim_ids": sorted(set(attestation.claim_ids)),
            "step_bindings": step_bindings,
            "terminal_claim_link_id": terminal["viewpoint_claim_link_id"],
        }
        record = ArgumentRouteAttestationRecord(
            argument_route_attestation_id=f"ARA-{sha256_json(attestation_seed)[:20]}",
            argument_route_id=route_id,
            validated_against_route_revision_id=revision_id,
            source_id=attestation.source_id,
            source_revision_sha256=attestation.source_revision_sha256,
            claim_ids=sorted(set(attestation.claim_ids)),
            occurrence_ref_id=occurrence_refs[0],
            step_bindings=step_bindings,
            terminal_claim_link_id=str(terminal["viewpoint_claim_link_id"]),
            completeness=attestation.completeness,
            scripture_refs_derived=scripture_refs,
            review_artifact_sha256=review_artifact_sha256,
            review_status="system_approved",
        )
        attestation_records.append(record.model_dump(mode="json"))

    package_seed = {
        "policy_version": ROUTE_CHANGESET_POLICY_VERSION,
        "route_packet_sha256": route_packet.get("packet_sha256"),
        "proposal_artifact_sha256": proposal_artifact_sha256,
        "review_artifact_sha256": review_artifact_sha256,
        "passing_route_keys": accepted_route_keys,
        "passing_attestation_keys": accepted_attestation_keys,
    }
    return {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"ROUTE-BATCH-{sha256_json(package_seed)[:20]}",
        "argument_routes": sorted(route_records, key=lambda item: item["argument_route_id"]),
        "argument_route_revisions": sorted(
            revision_records, key=lambda item: item["argument_route_revision_id"]
        ),
        "argument_route_attestations": sorted(
            attestation_records, key=lambda item: item["argument_route_attestation_id"]
        ),
    }
