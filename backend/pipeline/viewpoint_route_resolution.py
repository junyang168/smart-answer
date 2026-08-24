"""ArgumentRoute packet compilation and reviewed resolution service.

This module has no Registry writer. The queue worker is the only execution
entry point; this service transforms a committed Registry cut into reviewed,
SHA-bound Route artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    BatchResolutionError,
    ArgumentRouteReconsiderationResponse,
    ArgumentRouteProposalResponse,
    ArgumentRouteReviewResponse,
    RouteComponentBinding,
    canonicalize_proposal,
    canonicalize_review,
    component_key_from_spans,
    validate_route_proposal,
    validate_route_review,
    validate_route_reconsideration,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import (
    ReviewClaim,
    StructuredJsonReviewerAdapter,
)
from backend.pipeline.viewpoint_resolution_runtime import (
    CALL_TIMEOUT_SECONDS,
    PROMPT_DIR,
    call_model as _call,
    read_artifact as _read,
    recorded_model_executions as _recorded_model_executions,
    subscription_client as _subscription_client,
    write_current_state as _write_current_state,
    write_derived as _write_derived,
    write_immutable as _write_immutable,
)

DEFAULT_ROUTE_REVIEW_TARGETS_PER_BATCH = 12

_REGISTRY_LINK_DISPOSITIONS = {
    "equivalent_full": "member_existing",
    "equivalent_component": "member_existing",
    "supports": "support_existing",
    "extends": "extension_existing",
    "qualifies": "qualification_existing",
    "applies": "application_existing",
    "tension_evidence": "tension_existing",
}


class RouteStageNotReadyError(ValueError):
    """Committed Registry state cannot yet form a formal Route input cut."""


def build_route_proposer(
    model: str,
    reasoning_effort: str,
    *,
    provider: str = "claude",
    prompt_file: str = "canonical_viewpoint_batch_routes.md",
    timeout_seconds: float = CALL_TIMEOUT_SECONDS,
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_subscription_client(
            provider, model, reasoning_effort, timeout_seconds=timeout_seconds
        ),
        prompt=(PROMPT_DIR / prompt_file).read_text(encoding="utf-8"),
        response_model=ArgumentRouteProposalResponse,
        schema_name="wang_argument_route_proposal_v1",
    )


def build_route_reviewer(
    model: str,
    reasoning_effort: str,
    *,
    provider: str = "claude",
    prompt_file: str = "canonical_viewpoint_route_review.md",
    timeout_seconds: float = CALL_TIMEOUT_SECONDS,
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_subscription_client(
            provider, model, reasoning_effort, timeout_seconds=timeout_seconds
        ),
        prompt=(PROMPT_DIR / prompt_file).read_text(
            encoding="utf-8"
        ),
        response_model=ArgumentRouteReviewResponse,
        schema_name="wang_argument_route_review_v1",
    )


def build_route_reconsiderer(
    model: str,
    reasoning_effort: str,
    *,
    provider: str = "claude",
    prompt_file: str = "canonical_viewpoint_route_reconsideration.md",
    timeout_seconds: float = CALL_TIMEOUT_SECONDS,
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_subscription_client(
            provider, model, reasoning_effort, timeout_seconds=timeout_seconds
        ),
        prompt=(PROMPT_DIR / prompt_file).read_text(
            encoding="utf-8"
        ),
        response_model=ArgumentRouteReconsiderationResponse,
        schema_name="wang_argument_route_reconsideration_v1",
    )



def build_registry_route_packet(
    *,
    scope_label: str,
    approved_viewpoints: list[dict[str, Any]],
    claims: list[ReviewClaim],
    viewpoint_claim_links: list[dict[str, Any]],
    existing_routes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild Route evidence solely from committed Registry state.

    The async worker must not depend on a prior CVP model response.  Current
    revisions and their active Claim links are the semantic handoff; the full
    scope Claims remain available so source-local bridges and objections are
    not hidden merely because they are not CVP members.
    """

    approved_index: dict[str, dict[str, Any]] = {}
    revision_to_viewpoint: dict[str, str] = {}
    for raw in approved_viewpoints:
        item = dict(raw)
        revision_id = str(item.get("viewpoint_revision_id") or "")
        viewpoint_id = str(item.get("viewpoint_id") or "")
        if not revision_id or not viewpoint_id:
            raise ValueError("approved Registry viewpoint lacks identity or revision")
        if revision_id in approved_index:
            raise ValueError(f"duplicate approved viewpoint revision {revision_id}")
        approved_index[revision_id] = item
        revision_to_viewpoint[revision_id] = viewpoint_id
    if not approved_index:
        raise RouteStageNotReadyError(
            "Route Proposal requires at least one committed viewpoint revision"
        )

    claim_index = {item.claim_id: item for item in claims}
    if len(claim_index) != len(claims):
        raise ValueError("Route scope contains duplicate Claims")
    components: dict[str, RouteComponentBinding] = {}
    out_of_scope_members: list[dict[str, Any]] = []
    unattestable_in_scope_members: list[dict[str, Any]] = []

    for raw in viewpoint_claim_links:
        if raw.get("effective_state") != "active":
            continue
        revision_id = str(raw.get("validated_against_viewpoint_revision_id") or "")
        if revision_id not in approved_index:
            continue
        if str(raw.get("viewpoint_id") or "") != revision_to_viewpoint[revision_id]:
            raise ValueError(f"{raw.get('viewpoint_claim_link_id')}: viewpoint/revision mismatch")
        link_type = str(raw.get("link_type") or "")
        disposition = _REGISTRY_LINK_DISPOSITIONS.get(link_type)
        if disposition is None:
            # superseding evidence is historical identity evidence, not a
            # source-local route component for the current conclusion.
            continue
        claim_id = str(raw.get("claim_id") or "")
        claim = claim_index.get(claim_id)
        if claim is None:
            # The Registry is corpus-wide while this packet is one evidence
            # scope. Preserve the membership denominator without importing
            # out-of-scope prose as evidence for this run.
            out_of_scope_members.append(
                {
                    "viewpoint_revision_id": revision_id,
                    "viewpoint_claim_link_id": str(raw.get("viewpoint_claim_link_id") or ""),
                    "claim_id": claim_id,
                    "pinned_claim_revision": int(raw.get("pinned_claim_revision") or 0),
                    "link_type": link_type,
                    "reason": "claim_absent_from_route_evidence_scope",
                }
            )
            continue
        if int(raw.get("pinned_claim_revision") or 0) != claim.pinned_claim_revision:
            raise ValueError(f"{raw.get('viewpoint_claim_link_id')}: stale Claim revision")

        locator = raw.get("component_locator")
        if locator:
            if str(locator.get("claim_sha256") or "") != claim.claim_revision_sha256:
                raise ValueError(f"{raw.get('viewpoint_claim_link_id')}: stale Claim SHA")
            spans = [dict(item) for item in locator.get("canonical_spans") or []]
            statement_component = str(locator.get("statement_component") or "")
        else:
            spans = [{"start_char": 0, "end_char": len(claim.statement), "exact_text": claim.statement}]
            statement_component = claim.statement
        if not spans or statement_component != "".join(str(item["exact_text"]) for item in spans):
            raise ValueError(f"{raw.get('viewpoint_claim_link_id')}: invalid component locator")
        for span in spans:
            start, end = int(span["start_char"]), int(span["end_char"])
            if claim.statement[start:end] != str(span["exact_text"]):
                raise ValueError(f"{raw.get('viewpoint_claim_link_id')}: locator text mismatch")

        evidence_pairs = sorted(
            {
                (str(item["evidence_step_id"]), str(item["source_fragment_id"]))
                for item in raw.get("evidence_bindings") or []
            }
        )
        allowed_pairs = {
            (item.evidence_step_id, item.source_fragment_id) for item in claim.evidence
        }
        if not evidence_pairs:
            # Pre-v2 links cannot prove component evidence. They remain master
            # provenance but are not silently upgraded into Route evidence.
            unattestable_in_scope_members.append(
                {
                    "viewpoint_revision_id": revision_id,
                    "viewpoint_claim_link_id": str(raw.get("viewpoint_claim_link_id") or ""),
                    "claim_id": claim_id,
                    "pinned_claim_revision": claim.pinned_claim_revision,
                    "claim_revision_sha256": claim.claim_revision_sha256,
                    "link_type": link_type,
                    "reason": "active_member_has_no_exact_evidence_bindings",
                }
            )
            continue
        if any(pair not in allowed_pairs for pair in evidence_pairs):
            raise ValueError(f"{raw.get('viewpoint_claim_link_id')}: invalid evidence binding")
        key = component_key_from_spans(
            claim_id=claim.claim_id,
            claim_revision_sha256=claim.claim_revision_sha256,
            canonical_spans=spans,
        )
        binding = RouteComponentBinding(
            claim_component_key=key,
            claim_id=claim.claim_id,
            source_id=claim.source_id,
            disposition=disposition,
            target_viewpoint_revision_id=revision_id,
            viewpoint_claim_link_id=str(raw.get("viewpoint_claim_link_id") or "") or None,
            occurrence_ref_ids=sorted(str(item) for item in raw.get("occurrence_refs") or []),
            statement_component=statement_component,
            spans=spans,
            evidence_step_ids=sorted({item[0] for item in evidence_pairs}),
            source_fragment_ids=sorted({item[1] for item in evidence_pairs}),
        )
        prior = components.get(key)
        if prior is not None and prior != binding:
            raise ValueError(f"Claim component key has conflicting Registry links: {key}")
        components[key] = binding

    # Full Claims are included even when their components have no Registry
    # assertion. This is how the Route model can see source-local bridge,
    # objection and connective material without seeing old CVP proposals.
    for claim in claims:
        spans = [{"start_char": 0, "end_char": len(claim.statement), "exact_text": claim.statement}]
        key = component_key_from_spans(
            claim_id=claim.claim_id,
            claim_revision_sha256=claim.claim_revision_sha256,
            canonical_spans=spans,
        )
        if key not in components:
            components[key] = RouteComponentBinding(
                claim_component_key=key,
                claim_id=claim.claim_id,
                source_id=claim.source_id,
                disposition="no_registry_assertion",
                statement_component=claim.statement,
                spans=spans,
                evidence_step_ids=sorted({item.evidence_step_id for item in claim.evidence}),
                source_fragment_ids=sorted({item.source_fragment_id for item in claim.evidence}),
            )

    source_revisions = {
        claim.source_id: sorted(
            {
                evidence.source_sha256
                for scoped_claim in claims
                if scoped_claim.source_id == claim.source_id
                for evidence in scoped_claim.evidence
            }
        )
        for claim in claims
    }
    ambiguous = {key: value for key, value in source_revisions.items() if len(value) != 1}
    if ambiguous:
        raise ValueError(
            "Route scope does not pin exactly one revision per source: "
            + json.dumps(ambiguous, ensure_ascii=False, sort_keys=True)
        )

    packet = {
        "schema_version": "wang_argument_route_scope_packet_v2",
        "scope_label": scope_label,
        "approved_viewpoint_revision_ids": sorted(approved_index),
        "approved_viewpoint_set_sha256": sha256_json(
            [approved_index[key] for key in sorted(approved_index)]
        ),
        "registry_handoff": True,
        "single_source_note": (
            "每个 attestation 的 Claim、EvidenceStep、SourceFragment 必须同属一篇来源。"
            "不得从两篇拼出一条谁都没讲完整的论证。"
        ),
        "approved_viewpoints": [approved_index[key] for key in sorted(approved_index)],
        "membership_ledger": {
            "out_of_scope_members": sorted(
                out_of_scope_members,
                key=lambda item: (
                    item["viewpoint_revision_id"],
                    item["claim_id"],
                    item["viewpoint_claim_link_id"],
                ),
            ),
            "unattestable_in_scope_members": sorted(
                unattestable_in_scope_members,
                key=lambda item: (
                    item["viewpoint_revision_id"],
                    item["claim_id"],
                    item["viewpoint_claim_link_id"],
                ),
            ),
            "no_route_semantics": "no_attested_route_in_this_evidence_scope",
        },
        "claim_components": [
            components[key].model_dump(mode="json") for key in sorted(components)
        ],
        "claims": [claim.model_dump(mode="json") for claim in sorted(claims, key=lambda item: item.claim_id)],
        "source_revisions": {key: value[0] for key, value in source_revisions.items()},
        "existing_routes": sorted(
            [dict(item) for item in existing_routes],
            key=lambda item: str(item.get("route_revision_id") or item.get("argument_route_revision_id") or ""),
        ),
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet



def build_route_review_batches(
    *,
    proposal: ArgumentRouteProposalResponse,
    route_packet: dict[str, Any],
    route_proposal_sha256: str,
    max_targets: int = DEFAULT_ROUTE_REVIEW_TARGETS_PER_BATCH,
) -> list[dict[str, Any]]:
    """Create exact-once bounded review targets with sufficient read context."""

    if max_targets < 1:
        raise ValueError("Route review batch size must be positive")
    attestations_by_route: dict[str, list[Any]] = {}
    for item in proposal.source_route_attestations:
        attestations_by_route.setdefault(item.route_ref.key(), []).append(item)
    ordered_targets: list[tuple[str, str]] = []
    for route in proposal.argument_route_candidates:
        ordered_targets.append(("route", route.local_route_key))
        ordered_targets.extend(
            ("attestation", item.local_attestation_key)
            for item in sorted(
                attestations_by_route.get(route.local_route_key, []),
                key=lambda value: value.local_attestation_key,
            )
        )
    ordered_targets.extend(
        ("no_route", item.viewpoint_revision_id)
        for item in proposal.viewpoints_with_no_route
    )
    if len(ordered_targets) != len(set(ordered_targets)):
        raise ValueError("Route review targets must be unique")

    routes = {item.local_route_key: item for item in proposal.argument_route_candidates}
    attestations = {
        item.local_attestation_key: item for item in proposal.source_route_attestations
    }
    no_routes = {
        item.viewpoint_revision_id: item for item in proposal.viewpoints_with_no_route
    }
    packet_components = {
        str(item["claim_component_key"]): dict(item)
        for item in route_packet["claim_components"]
    }
    packet_claims = {str(item["claim_id"]): dict(item) for item in route_packet["claims"]}
    packet_viewpoints = {
        str(item["viewpoint_revision_id"]): dict(item)
        for item in route_packet["approved_viewpoints"]
    }

    batches = []
    for offset in range(0, len(ordered_targets), max_targets):
        targets = ordered_targets[offset : offset + max_targets]
        target_set = set(targets)
        context_route_keys = {
            key for kind, key in targets if kind == "route"
        } | {
            attestations[key].route_ref.key()
            for kind, key in targets
            if kind == "attestation"
        }
        context_routes = [routes[key] for key in sorted(context_route_keys)]
        context_attestations = {
            key: value
            for key, value in attestations.items()
            if ("attestation", key) in target_set
            or (
                value.route_ref.key() in context_route_keys
                and ("route", value.route_ref.key()) in target_set
            )
        }
        target_no_routes = [
            no_routes[key] for kind, key in targets if kind == "no_route"
        ]
        component_keys = {
            component_key_value
            for item in context_attestations.values()
            for binding in item.step_bindings
            for component_key_value in binding.claim_component_keys
        } | {
            str(item.terminal_claim_component_key)
            for item in context_attestations.values()
            if item.terminal_claim_component_key
        }
        claim_ids = {
            claim_id
            for item in context_attestations.values()
            for claim_id in item.claim_ids
        }
        # A no-route decision is a claim about the complete scope, so it alone
        # receives the complete evidence context. Route/attestation batches are
        # sliced to the exact source-local objects they inspect.
        complete_evidence = bool(target_no_routes)
        selected_claims = (
            list(packet_claims.values())
            if complete_evidence
            else [packet_claims[key] for key in sorted(claim_ids)]
        )
        selected_components = (
            list(packet_components.values())
            if complete_evidence
            else [packet_components[key] for key in sorted(component_keys)]
        )
        viewpoint_revision_ids = {
            item.conclusion_ref.key() for item in context_routes
        } | {item.viewpoint_revision_id for item in target_no_routes}
        evidence_context = {
            "schema_version": "wang_argument_route_review_evidence_context_v1",
            "scope_label": route_packet["scope_label"],
            "approved_viewpoints": [
                packet_viewpoints[key] for key in sorted(viewpoint_revision_ids)
            ],
            "claim_components": selected_components,
            "claims": selected_claims,
            "source_revisions": {
                source_id: source_sha
                for source_id, source_sha in route_packet["source_revisions"].items()
                if complete_evidence
                or any(item["source_id"] == source_id for item in selected_claims)
            },
            "existing_routes": route_packet["existing_routes"],
            "membership_ledger": route_packet["membership_ledger"],
        }
        batch = {
            "schema_version": "wang_argument_route_review_batch_v1",
            "batch_id": f"RRB-{offset // max_targets + 1:03d}",
            "route_proposal_sha256": route_proposal_sha256,
            "route_evidence_packet_sha256": route_packet["packet_sha256"],
            "review_targets": [
                {"target_kind": kind, "target_key": key} for kind, key in targets
            ],
            "route_proposal_context": {
                "argument_route_candidates": [
                    item.model_dump(mode="json") for item in context_routes
                ],
                "source_route_attestations": [
                    context_attestations[key].model_dump(mode="json")
                    for key in sorted(context_attestations)
                ],
                "viewpoints_with_no_route": [
                    item.model_dump(mode="json") for item in target_no_routes
                ],
            },
            "route_evidence_context": evidence_context,
        }
        batch["batch_sha256"] = sha256_json(batch)
        batches.append(batch)
    return batches


def run_route_scope(
    *,
    scope_label: str,
    claims: list[ReviewClaim],
    existing_routes: list[dict[str, Any]],
    output_dir: Path,
    proposer: Any,
    reviewer: Any,
    reconsiderer: Any = None,
    route_packet: dict[str, Any],
    review_targets_per_batch: int = DEFAULT_ROUTE_REVIEW_TARGETS_PER_BATCH,
    call_timeout_seconds: float = CALL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Propose and review routes after the whole approved CVP set is frozen.

    This function deliberately has no Registry writer.  It compiles validated,
    review-bound no-apply artifacts; a production ChangeSet builder can consume
    only the passing object keys reported here.  Route exceptions are isolated
    from the already-approved CVPs.
    """

    packet = route_packet
    if packet.get("scope_label") != scope_label:
        raise ValueError("prebuilt Route packet belongs to another scope")
    _write_immutable(output_dir / "route-packet.json", packet)
    raw_proposal, proposal_calls, proposal_seconds = _call(
        proposer, packet, output_dir / "raw-route-proposal.json"
    )
    canonical, normalization_changes = canonicalize_proposal(raw_proposal)
    proposal = ArgumentRouteProposalResponse.model_validate(canonical)
    component_bindings = [
        RouteComponentBinding.model_validate(item)
        for item in packet["claim_components"]
    ]
    validation = validate_route_proposal(
        routes=proposal,
        scope_label=scope_label,
        claims=claims,
        approved_viewpoint_revision_ids=packet["approved_viewpoint_revision_ids"],
        known_route_revision_ids=[
            str(item.get("route_revision_id") or item.get("argument_route_revision_id"))
            for item in existing_routes
            if item.get("route_revision_id") or item.get("argument_route_revision_id")
        ],
        known_route_conclusions={
            str(item.get("route_revision_id") or item.get("argument_route_revision_id")): str(
                item.get("conclusion_viewpoint_revision_id") or ""
            )
            for item in existing_routes
            if item.get("route_revision_id") or item.get("argument_route_revision_id")
        },
        component_bindings=component_bindings,
    )
    proposal_payload = proposal.model_dump(mode="json")
    proposal_sha = sha256_json(proposal_payload)
    _write_immutable(
        output_dir / "route-proposal.json",
        {
            "schema_version": "wang_argument_route_proposal_envelope_v1",
            "scope_label": scope_label,
            "route_evidence_packet_sha256": packet["packet_sha256"],
            "route_proposal_sha256": proposal_sha,
            "proposal": proposal_payload,
            "validation_report": validation,
            "normalization": {
                "raw_response_sha256": sha256_json(dict(raw_proposal)),
                "changed_paths": normalization_changes,
                "reader_visible_text_changed": False,
                "truth_conditions_changed": False,
            },
        },
    )

    review_batches = build_route_review_batches(
        proposal=proposal,
        route_packet=packet,
        route_proposal_sha256=proposal_sha,
        max_targets=review_targets_per_batch,
    )
    _write_immutable(
        output_dir / "route-review-batches.json",
        {
            "schema_version": "wang_argument_route_review_batch_manifest_v1",
            "route_proposal_sha256": proposal_sha,
            "route_evidence_packet_sha256": packet["packet_sha256"],
            "max_targets_per_batch": review_targets_per_batch,
            "batches": review_batches,
        },
    )
    review_parts = []
    review_calls = 0
    review_seconds = 0.0
    review_normalization_changes: list[str] = []
    raw_review_shas = []
    for index, review_packet in enumerate(review_batches, start=1):
        raw_review_path = output_dir / f"raw-route-review-{index:03d}.json"
        raw_review, calls, seconds = _call(
            reviewer,
            review_packet,
            raw_review_path,
        )
        review_calls += calls
        review_seconds += seconds
        raw_review_shas.append(str(_read(raw_review_path)["artifact_sha256"]))
        canonical_review, changed = canonicalize_review(raw_review)
        review_normalization_changes.extend(
            f"batch-{index:03d}:{path}" for path in changed
        )
        part = ArgumentRouteReviewResponse.model_validate(canonical_review)
        expected_targets = {
            (str(item["target_kind"]), str(item["target_key"]))
            for item in review_packet["review_targets"]
        }
        validate_route_review(
            review=part,
            proposal=proposal,
            route_proposal_sha256=proposal_sha,
            route_evidence_packet_sha256=packet["packet_sha256"],
            expected_targets=expected_targets,
            allowed_claim_component_keys={
                str(item["claim_component_key"])
                for item in packet["claim_components"]
            },
        )
        review_parts.append(part)
    review = ArgumentRouteReviewResponse(
        route_proposal_sha256=proposal_sha,
        route_evidence_packet_sha256=packet["packet_sha256"],
        change_reviews=sorted(
            [item for part in review_parts for item in part.change_reviews],
            key=lambda item: (item.target_kind, item.target_key),
        ),
        cvp_re_review_exceptions=sorted(
            {
                (
                    item.viewpoint_revision_id,
                    item.finding_code,
                    item.triggering_target_kind,
                    item.triggering_target_key,
                ): item
                for part in review_parts
                for item in part.cvp_re_review_exceptions
            }.values(),
            key=lambda item: (
                item.viewpoint_revision_id,
                item.finding_code,
                item.triggering_target_kind,
                item.triggering_target_key,
            ),
        ),
        cross_source_composition_found=any(
            part.cross_source_composition_found for part in review_parts
        ),
        reason="；".join(
            f"{review_batches[index]['batch_id']}：{part.reason}"
            for index, part in enumerate(review_parts)
        ),
    )
    review_validation = validate_route_review(
        review=review,
        proposal=proposal,
        route_proposal_sha256=proposal_sha,
        route_evidence_packet_sha256=packet["packet_sha256"],
        allowed_claim_component_keys={
            str(item["claim_component_key"])
            for item in packet["claim_components"]
        },
    )
    review_payload = review.model_dump(mode="json")
    review_sha = sha256_json(review_payload)
    recorded_review_wall_seconds = round(
        sum(
            float(_read(output_dir / f"raw-route-review-{index:03d}.json")["wall_seconds"])
            for index in range(1, len(review_batches) + 1)
        ),
        3,
    )
    raw_review_manifest_body = {
        "schema_version": "wang_argument_route_raw_review_manifest_v1",
        "request_payload_sha256": sha256_json(
            [item["batch_sha256"] for item in review_batches]
        ),
        "model_id": reviewer.model_id,
        "backend": reviewer.backend,
        "prompt_sha256": reviewer.prompt_sha256,
        "generation_config_sha256": reviewer.generation_config_sha256,
        "wall_seconds": recorded_review_wall_seconds,
        "calls_recorded": len(raw_review_shas),
        "raw_batch_artifact_sha256s": raw_review_shas,
        "response_sha256": review_sha,
    }
    _write_immutable(
        output_dir / "raw-route-review-manifest.json",
        raw_review_manifest_body
        | {"artifact_sha256": sha256_json(raw_review_manifest_body)},
    )
    _write_immutable(
        output_dir / "route-review.json",
        {
            "schema_version": "wang_argument_route_review_envelope_v1",
            "scope_label": scope_label,
            "route_evidence_packet_sha256": packet["packet_sha256"],
            "route_proposal_sha256": proposal_sha,
            "route_review_sha256": review_sha,
            "review": review_payload,
            "validation_report": review_validation,
            "normalization": {
                "raw_response_sha256": sha256_json(raw_review_shas),
                "changed_paths": review_normalization_changes,
                "reader_visible_text_changed": False,
                "truth_conditions_changed": False,
            },
        },
    )

    effective_proposal = proposal
    reconsideration_report: dict[str, Any] | None = None
    reconsideration_calls = 0
    reconsideration_seconds = 0.0
    if review_validation["reconsideration_required"] and reconsiderer is not None:
        reconsider_packet = {
            "scope_label": scope_label,
            "route_evidence_packet_sha256": packet["packet_sha256"],
            "route_evidence_packet": packet,
            "route_proposal_sha256": proposal_sha,
            "route_proposal": proposal_payload,
            "route_review_sha256": review_sha,
            "route_review": review_payload,
        }
        raw_reconsideration, reconsideration_calls, reconsideration_seconds = _call(
            reconsiderer,
            reconsider_packet,
            output_dir / "raw-route-reconsideration.json",
        )
        reconsideration = ArgumentRouteReconsiderationResponse.model_validate(
            canonicalize_proposal(raw_reconsideration)[0]
        )
        reconsideration_report = validate_route_reconsideration(
            reconsideration=reconsideration,
            proposal=proposal,
            review=review,
            route_proposal_sha256=proposal_sha,
            route_review_sha256=review_sha,
        )
        revised_validation = validate_route_proposal(
            routes=reconsideration.revised_proposal,
            scope_label=scope_label,
            claims=claims,
            approved_viewpoint_revision_ids=packet["approved_viewpoint_revision_ids"],
            known_route_revision_ids=[
                str(item.get("route_revision_id") or item.get("argument_route_revision_id"))
                for item in existing_routes
                if item.get("route_revision_id") or item.get("argument_route_revision_id")
            ],
            known_route_conclusions={
                str(item.get("route_revision_id") or item.get("argument_route_revision_id")): str(
                    item.get("conclusion_viewpoint_revision_id") or ""
                )
                for item in existing_routes
                if item.get("route_revision_id") or item.get("argument_route_revision_id")
            },
            component_bindings=component_bindings,
        )
        if reconsideration_report["outcome"] == "resolved":
            effective_proposal = reconsideration.revised_proposal
        _write_immutable(
            output_dir / "route-reconsideration.json",
            {
                "schema_version": "wang_argument_route_reconsideration_envelope_v1",
                "scope_label": scope_label,
                "route_proposal_sha256": proposal_sha,
                "route_review_sha256": review_sha,
                "reconsideration": reconsideration.model_dump(mode="json"),
                "validation_report": reconsideration_report,
                "revised_validation_report": revised_validation,
            },
        )

    corrected = {
        (item.target_kind, item.target_key)
        for item in review.change_reviews
        if item.decision == "correct"
    }
    correction_resolved = bool(
        reconsideration_report and reconsideration_report["outcome"] == "resolved"
    )
    passed = {
        (item.target_kind, item.target_key)
        for item in review.change_reviews
        if item.decision == "pass"
    }
    if correction_resolved:
        passed |= corrected
    exceptions = sorted(
        f"{item.target_kind}:{item.target_key}:{item.decision}"
        for item in review.change_reviews
        if item.decision in {"reject", "defer"}
    )
    if corrected and not correction_resolved:
        exceptions.extend(sorted(f"{kind}:{key}:unresolved" for kind, key in corrected))

    route_for_attestation = {
        item.local_attestation_key: item.route_ref.local_route_key
        for item in effective_proposal.source_route_attestations
    }
    candidate_routes = {key for kind, key in passed if kind == "route"}
    candidate_attestations = {
        key for kind, key in passed if kind == "attestation"
    }
    passing_attestations = sorted(
        key
        for key in candidate_attestations
        if route_for_attestation.get(key) in candidate_routes
    )
    attested_routes = {
        str(route_for_attestation[key]) for key in passing_attestations
    }
    orphaned_routes = sorted(candidate_routes - attested_routes)
    exceptions.extend(f"route:{key}:no_passing_attestation" for key in orphaned_routes)
    passing_routes = sorted(candidate_routes - set(orphaned_routes))
    recorded_model_executions = _recorded_model_executions(
        output_dir,
        raw_artifacts={
            "proposal": "raw-route-proposal.json",
            "review": "raw-route-review-manifest.json",
            "reconsideration": "raw-route-reconsideration.json",
        },
    )
    report = {
        "schema_version": "wang_argument_route_scope_run_v1",
        "scope_label": scope_label,
        "approved_viewpoint_count": len(packet["approved_viewpoint_revision_ids"]),
        "route_count": len(effective_proposal.argument_route_candidates),
        "attestation_count": len(effective_proposal.source_route_attestations),
        "passing_route_keys": passing_routes,
        "passing_attestation_keys": passing_attestations,
        "exceptions": sorted(set(exceptions)),
        "cvp_re_review_exceptions": [
            item.model_dump(mode="json") for item in review.cvp_re_review_exceptions
        ],
        "cvp_mutations_proposed": 0,
        "route_evidence_packet_sha256": packet["packet_sha256"],
        "route_proposal_sha256": proposal_sha,
        "route_review_sha256": review_sha,
        "effective_route_proposal_sha256": sha256_json(
            effective_proposal.model_dump(mode="json")
        ),
        "reconsideration_outcome": (
            reconsideration_report["outcome"] if reconsideration_report else None
        ),
        "recorded_model_executions": recorded_model_executions,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    report["artifact_sha256"] = sha256_json(report)
    _write_derived(output_dir / "route-scope-run.json", report)
    superseded = []
    if (output_dir / "exception.json").exists():
        superseded.append("exception.json")
    superseded.extend(
        str(path.relative_to(output_dir))
        for path in sorted((output_dir / "exceptions").glob("*.json"))
    )
    _write_current_state(
        output_dir,
        schema_version="wang_argument_route_scope_current_state_v1",
        identity={"scope_label": scope_label},
        status="resolved" if not report["exceptions"] else "completed_with_exceptions",
        authoritative_artifact="route-scope-run.json",
        authoritative_artifact_sha256=report["artifact_sha256"],
        superseded_artifacts=superseded,
    )
    measurements = {
        "proposal_calls_executed": proposal_calls,
        "proposal_wall_seconds": proposal_seconds,
        "review_calls_executed": review_calls,
        "review_wall_seconds": review_seconds,
        "reconsideration_calls_executed": reconsideration_calls,
        "reconsideration_wall_seconds": reconsideration_seconds,
        "call_timeout_seconds": call_timeout_seconds,
    }
    return {
        **report,
        "measurements": measurements,
        "_effective_proposal": effective_proposal,
        "_route_packet": packet,
    }
