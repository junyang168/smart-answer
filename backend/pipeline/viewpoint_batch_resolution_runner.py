"""Resolve one CanonicalViewpoint scope, then its approved ArgumentRoutes.

Batches are resumable at batch granularity: a finished batch writes immutable
artifacts and a rerun reuses them without spending a call.  Within a batch a
model call is atomic — a transport failure loses that call, so batches stay
small enough that losing one is cheap.

Both models are reached through their subscription CLIs.  No API-key client is
constructed here, and there is no fallback that would reach one.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    BatchResolutionError,
    CanonicalViewpointProposalResponse,
    CanonicalViewpointReconsiderationResponse,
    ArgumentRouteReconsiderationResponse,
    ArgumentRouteProposalResponse,
    ArgumentRouteReviewResponse,
    CanonicalViewpointReviewResponse,
    ClaimGroupingResponse,
    DEFAULT_BATCH_SIZE,
    RouteComponentBinding,
    anchor_proposal_spans,
    batches_from_groups,
    build_batch_packet,
    canonicalize_proposal,
    component_key,
    repair_grouping,
    split_batches,
    validate_grouping,
    validate_proposal,
    validate_reconsideration,
    validate_route_reconsideration,
    validate_route_proposal,
    validate_route_review,
    validate_review,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import (
    ReviewClaim,
    StructuredJsonReviewerAdapter,
)
from backend.pipeline.viewpoint_scope_packet_runner import SCOPE_PACKET_VERSION
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

#: The 62-Claim POC ran over ten minutes against this ceiling; batches are
#: sized so a single call stays far below it.
CALL_TIMEOUT_SECONDS = 900.0


class RouteStageNotReadyError(ValueError):
    """CVP apply/readback has not yet produced the formal Route input cut."""


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _proposal_client(
    provider: str, model: str, reasoning_effort: str
) -> ClaudeSubscriptionClient | CodexSubscriptionClient:
    client_type = {
        "claude": ClaudeSubscriptionClient,
        "codex": CodexSubscriptionClient,
    }.get(provider)
    if client_type is None:
        raise ValueError(f"unsupported proposal provider: {provider}")
    return client_type(
        model=model,
        reasoning_effort=reasoning_effort,
        timeout_seconds=CALL_TIMEOUT_SECONDS,
    )


def build_proposer(
    model: str, reasoning_effort: str, *, provider: str = "claude"
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_proposal_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_proposal.md").read_text(
            encoding="utf-8"
        ),
        response_model=CanonicalViewpointProposalResponse,
        schema_name="wang_canonical_viewpoint_proposal_v1",
    )


def build_reviewer(model: str, reasoning_effort: str) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=CodexSubscriptionClient(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        ),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_review.md").read_text(
            encoding="utf-8"
        ),
        response_model=CanonicalViewpointReviewResponse,
        schema_name="wang_canonical_viewpoint_review_v1",
    )


def build_grouper(model: str, reasoning_effort: str) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=ClaudeSubscriptionClient(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        ),
        prompt=(PROMPT_DIR / "canonical_viewpoint_claim_grouping.md").read_text(
            encoding="utf-8"
        ),
        response_model=ClaimGroupingResponse,
        schema_name="wang_canonical_viewpoint_claim_grouping_v1",
    )


def build_reconsiderer(
    model: str, reasoning_effort: str, *, provider: str = "claude"
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_proposal_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_reconsideration.md").read_text(
            encoding="utf-8"
        ),
        response_model=CanonicalViewpointReconsiderationResponse,
        schema_name="wang_canonical_viewpoint_reconsideration_v1",
    )


def build_route_proposer(
    model: str, reasoning_effort: str, *, provider: str = "claude"
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_proposal_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_routes.md").read_text(encoding="utf-8"),
        response_model=ArgumentRouteProposalResponse,
        schema_name="wang_argument_route_proposal_v1",
    )


def build_route_reviewer(model: str, reasoning_effort: str) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=CodexSubscriptionClient(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        ),
        prompt=(PROMPT_DIR / "canonical_viewpoint_route_review.md").read_text(
            encoding="utf-8"
        ),
        response_model=ArgumentRouteReviewResponse,
        schema_name="wang_argument_route_review_v1",
    )


def build_route_reconsiderer(
    model: str, reasoning_effort: str, *, provider: str = "claude"
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_proposal_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "canonical_viewpoint_route_reconsideration.md").read_text(
            encoding="utf-8"
        ),
        response_model=ArgumentRouteReconsiderationResponse,
        schema_name="wang_argument_route_reconsideration_v1",
    )


def build_route_packet(
    *,
    scope_label: str,
    approved_viewpoints: list[dict[str, Any]],
    effective_proposals: list[CanonicalViewpointProposalResponse],
    claims: list[ReviewClaim],
    existing_routes: list[dict[str, Any]],
    local_candidate_revision_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compile complete scope evidence for all explicitly approved CVPs.

    Intelligent Claim groups end at CVP approval.  This compiler walks every
    effective CVP proposal and every Claim in the scope again, retaining even
    background/objection/connective components that may carry an argument step.
    New local viewpoint keys are rejected: the caller must apply/read back CVPs
    before Route generation can begin.
    """

    approved_index: dict[str, dict[str, Any]] = {}
    for item in approved_viewpoints:
        revision_id = str(item.get("viewpoint_revision_id") or "")
        if not revision_id:
            raise ValueError("approved viewpoint context is missing viewpoint_revision_id")
        if revision_id in approved_index:
            raise ValueError(f"duplicate approved viewpoint revision {revision_id}")
        approved_index[revision_id] = dict(item)
    if not approved_index:
        raise RouteStageNotReadyError(
            "Route Proposal requires at least one approved viewpoint revision"
        )

    resolved_local = local_candidate_revision_map or {}
    components: dict[str, RouteComponentBinding] = {}
    claim_index = {item.claim_id: item for item in claims}
    unresolved_local_candidates: list[str] = []
    all_local_candidates: set[str] = set()
    for proposal in effective_proposals:
        for item in proposal.new_viewpoint_candidates:
            map_key = f"{proposal.batch_id}:{item.local_key}"
            all_local_candidates.add(map_key)
            if map_key not in resolved_local:
                unresolved_local_candidates.append(map_key)
        for decision in proposal.claim_decisions:
            claim = claim_index.get(decision.claim_id)
            if claim is None:
                raise ValueError(f"effective proposal references Claim outside scope: {decision.claim_id}")
            for component in decision.components:
                disposition = component.disposition
                target_revision = component.target_viewpoint_revision_id
                if disposition == "new_viewpoint":
                    map_key = f"{proposal.batch_id}:{component.local_new_viewpoint_key}"
                    target_revision = resolved_local.get(map_key)
                    if not target_revision:
                        continue
                    if target_revision not in approved_index:
                        raise ValueError(
                            f"mapped viewpoint revision {target_revision} is absent from approved cut"
                        )
                    disposition = "member_existing"
                binding = RouteComponentBinding(
                    claim_component_key=component_key(claim, component),
                    claim_id=claim.claim_id,
                    source_id=claim.source_id,
                    disposition=disposition,
                    target_viewpoint_revision_id=target_revision,
                    statement_component=component.statement_component(),
                    spans=component.spans,
                    evidence_step_ids=component.evidence_step_ids,
                    source_fragment_ids=component.source_fragment_ids,
                )
                prior = components.get(binding.claim_component_key)
                if prior is not None and prior != binding:
                    raise ValueError(
                        f"Claim component key collision: {binding.claim_component_key}"
                    )
                components[binding.claim_component_key] = binding
    if unresolved_local_candidates:
        raise RouteStageNotReadyError(
            "Route Proposal cannot use unapplied local CVP candidates: "
            + ", ".join(sorted(set(unresolved_local_candidates)))
        )
    extra_mappings = sorted(set(resolved_local) - all_local_candidates)
    if extra_mappings:
        raise ValueError(
            "approved viewpoint cut maps no local candidate: "
            + ", ".join(extra_mappings)
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
    ambiguous_sources = {
        source_id: values
        for source_id, values in source_revisions.items()
        if len(values) != 1
    }
    if ambiguous_sources:
        raise ValueError(
            "Route scope does not pin exactly one revision per source: "
            + json.dumps(ambiguous_sources, ensure_ascii=False, sort_keys=True)
        )

    packet = {
        "schema_version": "wang_argument_route_scope_packet_v1",
        "scope_label": scope_label,
        "approved_viewpoint_revision_ids": sorted(approved_index),
        "approved_viewpoint_set_sha256": sha256_json(
            [approved_index[key] for key in sorted(approved_index)]
        ),
        "single_source_note": (
            "每个 attestation 的 Claim、EvidenceStep、SourceFragment 必须同属一篇来源。"
            "不得从两篇拼出一条谁都没讲完整的论证。"
        ),
        "approved_viewpoints": [approved_index[key] for key in sorted(approved_index)],
        "claim_components": [
            components[key].model_dump(mode="json") for key in sorted(components)
        ],
        "claims": [claim.model_dump(mode="json") for claim in sorted(claims, key=lambda x: x.claim_id)],
        "source_revisions": {
            source_id: values[0] for source_id, values in source_revisions.items()
        },
        "existing_routes": sorted(
            [dict(item) for item in existing_routes],
            key=lambda item: str(item.get("route_revision_id") or ""),
        ),
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet


def approved_viewpoints_for_route(
    *,
    effective_proposals: list[CanonicalViewpointProposalResponse],
    registry_context: list[dict[str, Any]],
    approved_cut: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Select the approved CVPs actually established as identities in scope.

    `registry_context` is an open retrieval set and may contain many merely
    nearby viewpoints.  Only revisions receiving an approved member component
    belong to this Route run.  New local candidates remain visible to
    `build_route_packet`, which defers until their formal revisions exist.
    """

    if approved_cut is not None:
        return [dict(item) for item in approved_cut["approved_viewpoints"]]

    local_candidates = sorted(
        {
            item.local_key
            for proposal in effective_proposals
            for item in proposal.new_viewpoint_candidates
        }
    )
    if local_candidates:
        raise RouteStageNotReadyError(
            "Route Proposal cannot use unapplied local CVP candidates: "
            + ", ".join(local_candidates)
        )
    revision_ids = {
        str(component.target_viewpoint_revision_id)
        for proposal in effective_proposals
        for decision in proposal.claim_decisions
        for component in decision.components
        if component.disposition == "member_existing"
        and component.target_viewpoint_revision_id
    }
    context = {
        str(item.get("viewpoint_revision_id") or ""): dict(item)
        for item in registry_context
        if item.get("viewpoint_revision_id")
    }
    missing = sorted(revision_ids - set(context))
    if missing:
        raise ValueError(
            "member revisions are absent from Registry context: " + ", ".join(missing)
        )
    return [context[key] for key in sorted(revision_ids)]


def load_approved_viewpoint_cut(path: Path, *, scope_label: str) -> dict[str, Any]:
    """Validate the apply/readback bridge from local keys to formal CVR ids."""

    payload = _read(path)
    if payload.get("schema_version") != "wang_approved_viewpoint_scope_cut_v1":
        raise ValueError(f"{path} is not a wang_approved_viewpoint_scope_cut_v1")
    if payload.get("scope_label") != scope_label:
        raise ValueError(f"approved viewpoint cut belongs to {payload.get('scope_label')}")
    body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    if payload.get("artifact_sha256") != sha256_json(body):
        raise ValueError("approved viewpoint cut SHA mismatch")
    viewpoints = list(payload.get("approved_viewpoints") or [])
    revision_ids = [str(item.get("viewpoint_revision_id") or "") for item in viewpoints]
    if not revision_ids or revision_ids != sorted(set(revision_ids)):
        raise ValueError("approved viewpoint cut revisions must be nonempty, sorted and unique")
    bindings = list(payload.get("candidate_revision_bindings") or [])
    mapping: dict[str, str] = {}
    for item in bindings:
        key = f"{item.get('batch_id')}:{item.get('local_new_viewpoint_key')}"
        revision_id = str(item.get("viewpoint_revision_id") or "")
        if key in mapping:
            raise ValueError(f"duplicate approved candidate binding {key}")
        if revision_id not in revision_ids:
            raise ValueError(f"candidate binding {key} targets revision outside approved cut")
        mapping[key] = revision_id
    return {
        "approved_viewpoints": viewpoints,
        "local_candidate_revision_map": mapping,
        "artifact_sha256": payload["artifact_sha256"],
    }


def _call(adapter: Any, payload: dict[str, Any], cache: Path) -> tuple[dict[str, Any], int, float]:
    """Return (raw response, calls executed, wall seconds).

    A cached response replays at zero cost, which is what makes a partly
    finished scope resumable.
    """

    request_sha = sha256_json(payload)
    if cache.exists():
        artifact = _read(cache)
        if artifact.get("request_payload_sha256") != request_sha:
            raise ValueError(f"cached response belongs to another request payload: {cache}")
        expected_generation = {
            "model_id": adapter.model_id,
            "backend": adapter.backend,
            "prompt_sha256": adapter.prompt_sha256,
            "generation_config_sha256": adapter.generation_config_sha256,
        }
        mismatched = [
            key
            for key, expected in expected_generation.items()
            if artifact.get(key) != expected
        ]
        if mismatched:
            raise ValueError(
                f"cached response belongs to another generation config "
                f"({', '.join(mismatched)}): {cache}"
            )
        response = dict(artifact.get("response") or {})
        if artifact.get("response_sha256") != sha256_json(response):
            raise ValueError(f"cached response SHA mismatch: {cache}")
        body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        if artifact.get("artifact_sha256") != sha256_json(body):
            raise ValueError(f"cached response artifact SHA mismatch: {cache}")
        return response, 0, 0.0
    started = time.monotonic()
    raw = dict(adapter.generate(payload))
    elapsed = round(time.monotonic() - started, 3)
    artifact = {
        "schema_version": "wang_canonical_viewpoint_batch_raw_response_v1",
        "model_id": adapter.model_id,
        "backend": adapter.backend,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
        "request_payload_sha256": request_sha,
        "wall_seconds": elapsed,
        "response_sha256": sha256_json(raw),
        "response": raw,
    }
    artifact["artifact_sha256"] = sha256_json(artifact)
    _write_immutable(cache, artifact)
    return raw, 1, elapsed


def run_batch(
    *,
    batch_id: str,
    scope_label: str,
    claims: list[ReviewClaim],
    registry_context: list[dict[str, Any]],
    pending_candidates: list[dict[str, Any]],
    output_dir: Path,
    proposer: Any,
    reviewer: Any,
    reconsiderer: Any = None,
) -> dict[str, Any]:
    packet = build_batch_packet(
        batch_id=batch_id,
        scope_label=scope_label,
        claims=claims,
        registry_context=registry_context,
        pending_candidates=pending_candidates,
    )
    _write_immutable(output_dir / "batch-packet.json", packet)

    raw_proposal, proposal_calls, proposal_seconds = _call(
        proposer, packet, output_dir / "raw-proposal.json"
    )
    canonical_proposal, normalization_changes = canonicalize_proposal(raw_proposal)
    canonical_proposal, span_changes = anchor_proposal_spans(
        canonical_proposal,
        claim_statements={item.claim_id: item.statement for item in claims},
    )
    normalization_changes = sorted({*normalization_changes, *span_changes})
    proposal = CanonicalViewpointProposalResponse.model_validate(canonical_proposal)
    validation = validate_proposal(
        proposal=proposal,
        batch_id=batch_id,
        claims=claims,
        registry_revision_ids=[
            str(item["viewpoint_revision_id"])
            for item in registry_context
            if item.get("viewpoint_revision_id")
        ],
    )
    proposal_payload = proposal.model_dump(mode="json")
    proposal_sha = sha256_json(proposal_payload)
    _write_immutable(
        output_dir / "proposal.json",
        {
            "schema_version": "wang_canonical_viewpoint_proposal_envelope_v1",
            "batch_id": batch_id,
            "packet_sha256": packet["packet_sha256"],
            "proposal_sha256": proposal_sha,
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

    review_packet = {
        "batch_id": batch_id,
        "packet": packet,
        "proposal_sha256": proposal_sha,
        "proposal": proposal_payload,
    }
    raw_review, review_calls, review_seconds = _call(
        reviewer, review_packet, output_dir / "raw-review.json"
    )
    review = CanonicalViewpointReviewResponse.model_validate(raw_review)
    review_validation = validate_review(
        review=review,
        proposal=proposal,
        proposal_sha256=proposal_sha,
    )
    review_payload = review.model_dump(mode="json")
    _write_immutable(
        output_dir / "review.json",
        {
            "schema_version": "wang_canonical_viewpoint_review_envelope_v1",
            "batch_id": batch_id,
            "proposal_sha256": proposal_sha,
            "review_sha256": sha256_json(review_payload),
            "review": review_payload,
            "validation_report": review_validation,
        },
    )

    review_sha = sha256_json(review_payload)
    reconsideration_report: dict[str, Any] | None = None
    reconsideration_calls = 0
    reconsideration_seconds = 0.0
    effective_proposal = proposal
    effective_validation = validation
    if review_validation["correction_required"] and reconsiderer is not None:
        reconsider_packet = {
            "batch_id": batch_id,
            "packet": packet,
            "proposal_sha256": proposal_sha,
            "proposal": proposal_payload,
            "review_sha256": review_sha,
            "review": review_payload,
        }
        raw_reconsideration, reconsideration_calls, reconsideration_seconds = _call(
            reconsiderer, reconsider_packet, output_dir / "raw-reconsideration.json"
        )
        canonical_reconsideration, reconsideration_normalization = canonicalize_proposal(
            raw_reconsideration
        )
        canonical_reconsideration, reconsideration_span_changes = anchor_proposal_spans(
            canonical_reconsideration,
            claim_statements={item.claim_id: item.statement for item in claims},
        )
        reconsideration = CanonicalViewpointReconsiderationResponse.model_validate(
            canonical_reconsideration
        )
        reconsideration_report = validate_reconsideration(
            reconsideration=reconsideration,
            proposal=proposal,
            review=review,
            proposal_sha256=proposal_sha,
            review_sha256=review_sha,
        )
        # The revision faces the same deterministic gates as the first pass;
        # accepting a finding cannot smuggle a bad span or a stale target past
        # checks the original proposal had to clear.
        revised_validation = validate_proposal(
            proposal=reconsideration.revised_proposal,
            batch_id=batch_id,
            claims=claims,
            registry_revision_ids=[
                str(item["viewpoint_revision_id"])
                for item in registry_context
                if item.get("viewpoint_revision_id")
            ],
        )
        if reconsideration_report["outcome"] == "resolved":
            effective_proposal = reconsideration.revised_proposal
            effective_validation = revised_validation
        _write_immutable(
            output_dir / "reconsideration.json",
            {
                "schema_version": "wang_canonical_viewpoint_reconsideration_envelope_v1",
                "batch_id": batch_id,
                "proposal_sha256": proposal_sha,
                "review_sha256": review_sha,
                "reconsideration": reconsideration.model_dump(mode="json"),
                "validation_report": reconsideration_report,
                "revised_validation_report": revised_validation,
                "normalization": {
                    "changed_paths": sorted(
                        {
                            *reconsideration_normalization,
                            *reconsideration_span_changes,
                        }
                    ),
                    "reader_visible_text_changed": False,
                    "truth_conditions_changed": False,
                },
            },
        )

    report = {
        "schema_version": "wang_canonical_viewpoint_batch_run_v1",
        "batch_id": batch_id,
        "scope_label": scope_label,
        "claim_count": len(claims),
        "component_count": effective_validation["component_count"],
        "disposition_counts": effective_validation["disposition_counts"],
        "new_viewpoint_candidate_count": effective_validation[
            "new_viewpoint_candidate_count"
        ],
        "outcome": review_validation["outcome"],
        "reconsideration_required": review_validation["reconsideration_required"],
        "novelty_status": review_validation["novelty_status"],
        "review_decision_counts": review_validation["decision_counts"],
        "packet_sha256": packet["packet_sha256"],
        "proposal_sha256": proposal_sha,
        "review_sha256": review_sha,
        "effective_proposal_sha256": sha256_json(
            effective_proposal.model_dump(mode="json")
        ),
        "reconsideration_outcome": (
            reconsideration_report["outcome"] if reconsideration_report else None
        ),
        "escalations": (
            reconsideration_report["escalations"] if reconsideration_report else []
        ),
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    report["artifact_sha256"] = sha256_json(report)
    # Rewritten, not immutable. The batch's semantic artifacts — packet,
    # proposal, review, reconsideration — are the immutable record; this file
    # is wholly derived from them. Freezing a derived summary means any change
    # to its shape blocks reruns of batches whose model calls are all cached,
    # which is how adding the reconsideration fields broke a completed batch.
    (output_dir / "batch-run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Wall time and call counts decide the real batch ceiling, so they must be
    # recorded — but they differ every execution, which is exactly why they are
    # not part of the immutable result. Kept as an append-only execution log.
    measurements = {
        "proposal_calls_executed": proposal_calls,
        "proposal_wall_seconds": proposal_seconds,
        "review_calls_executed": review_calls,
        "review_wall_seconds": review_seconds,
        "reconsideration_calls_executed": reconsideration_calls,
        "reconsideration_wall_seconds": reconsideration_seconds,
        "call_timeout_seconds": CALL_TIMEOUT_SECONDS,
        "claim_count": len(claims),
        "component_count": validation["component_count"],
    }
    log_path = output_dir / "measurements.json"
    history = _read(log_path)["executions"] if log_path.exists() else []
    history.append(measurements)
    log_path.write_text(
        json.dumps(
            {
                "schema_version": "wang_canonical_viewpoint_batch_measurements_v1",
                "batch_id": batch_id,
                "executions": history,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        **report,
        "measurements": measurements,
        # In-memory orchestration input only. The immutable proposal and
        # reconsideration envelopes above remain the auditable artifacts.
        "_effective_proposal": effective_proposal,
    }


def run_route_scope(
    *,
    scope_label: str,
    claims: list[ReviewClaim],
    approved_viewpoints: list[dict[str, Any]],
    effective_proposals: list[CanonicalViewpointProposalResponse],
    existing_routes: list[dict[str, Any]],
    local_candidate_revision_map: dict[str, str] | None = None,
    output_dir: Path,
    proposer: Any,
    reviewer: Any,
    reconsiderer: Any = None,
) -> dict[str, Any]:
    """Propose and review routes after the whole approved CVP set is frozen.

    This function deliberately has no Registry writer.  It compiles validated,
    review-bound no-apply artifacts; a production ChangeSet builder can consume
    only the passing object keys reported here.  Route exceptions are isolated
    from the already-approved CVPs.
    """

    packet = build_route_packet(
        scope_label=scope_label,
        approved_viewpoints=approved_viewpoints,
        effective_proposals=effective_proposals,
        claims=claims,
        existing_routes=existing_routes,
        local_candidate_revision_map=local_candidate_revision_map,
    )
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

    review_packet = {
        "scope_label": scope_label,
        "route_evidence_packet_sha256": packet["packet_sha256"],
        "route_evidence_packet": packet,
        "route_proposal_sha256": proposal_sha,
        "route_proposal": proposal_payload,
    }
    raw_review, review_calls, review_seconds = _call(
        reviewer, review_packet, output_dir / "raw-route-review.json"
    )
    review = ArgumentRouteReviewResponse.model_validate(raw_review)
    review_validation = validate_route_review(
        review=review,
        proposal=proposal,
        route_proposal_sha256=proposal_sha,
        route_evidence_packet_sha256=packet["packet_sha256"],
    )
    review_payload = review.model_dump(mode="json")
    review_sha = sha256_json(review_payload)
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
    report = {
        "schema_version": "wang_argument_route_scope_run_v1",
        "scope_label": scope_label,
        "approved_viewpoint_count": len(packet["approved_viewpoint_revision_ids"]),
        "route_count": len(effective_proposal.argument_route_candidates),
        "attestation_count": len(effective_proposal.source_route_attestations),
        "passing_route_keys": passing_routes,
        "passing_attestation_keys": passing_attestations,
        "exceptions": sorted(set(exceptions)),
        "approved_cvps_unchanged": True,
        "route_evidence_packet_sha256": packet["packet_sha256"],
        "route_proposal_sha256": proposal_sha,
        "route_review_sha256": review_sha,
        "effective_route_proposal_sha256": sha256_json(
            effective_proposal.model_dump(mode="json")
        ),
        "reconsideration_outcome": (
            reconsideration_report["outcome"] if reconsideration_report else None
        ),
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    report["artifact_sha256"] = sha256_json(report)
    (output_dir / "route-scope-run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    measurements = {
        "proposal_calls_executed": proposal_calls,
        "proposal_wall_seconds": proposal_seconds,
        "review_calls_executed": review_calls,
        "review_wall_seconds": review_seconds,
        "reconsideration_calls_executed": reconsideration_calls,
        "reconsideration_wall_seconds": reconsideration_seconds,
        "call_timeout_seconds": CALL_TIMEOUT_SECONDS,
    }
    return {**report, "measurements": measurements}


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True, help="scope packet from viewpoint_scope_packet_runner")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--proposal-provider",
        choices=("claude", "codex"),
        default="claude",
        help="subscription CLI used for CVP/Route proposal and correction; never falls back",
    )
    parser.add_argument("--proposal-model", default="claude-opus-5")
    parser.add_argument("--proposal-effort", choices=("high", "xhigh", "max"), default="xhigh")
    parser.add_argument("--review-model", default="gpt-5.6-sol")
    parser.add_argument("--review-effort", choices=("high", "xhigh"), default="high")
    parser.add_argument(
        "--max-batches",
        type=int,
        help="stop after this many batches; 0 groups the scope and stops",
    )
    parser.add_argument(
        "--no-routes",
        action="store_true",
        help="stop after viewpoints instead of asking how he argued them",
    )
    parser.add_argument(
        "--approved-viewpoint-cut",
        type=Path,
        help=(
            "SHA-bound apply/readback manifest mapping local CVP keys to formal "
            "ViewpointRevision ids before Route Proposal"
        ),
    )
    parser.add_argument("--route-effort", choices=("high", "xhigh", "max"), default="high")
    parser.add_argument(
        "--no-reconsider",
        action="store_true",
        help="stop after review instead of giving the proposer its one revision",
    )
    parser.add_argument(
        "--group",
        action="store_true",
        help="ask the model which Claims belong in a batch together, instead of splitting by id",
    )
    parser.add_argument(
        "--grouping",
        type=Path,
        help="reuse a grouping plan produced earlier for this scope instead of calling again",
    )
    parser.add_argument("--group-model", default="claude-opus-5")
    parser.add_argument("--group-effort", choices=("low", "medium", "high", "xhigh"), default="medium")
    parser.add_argument(
        "--group-key",
        action="append",
        help="resolve only these groups; the grouping plan itself still covers the whole scope",
    )
    args = parser.parse_args()

    scope_packet = _read(args.packet)
    if scope_packet.get("schema_version") != SCOPE_PACKET_VERSION:
        raise SystemExit(f"{args.packet} is not a {SCOPE_PACKET_VERSION}")
    scope_label = str(scope_packet["scope_label"])
    approved_cut = (
        load_approved_viewpoint_cut(
            args.approved_viewpoint_cut, scope_label=scope_label
        )
        if args.approved_viewpoint_cut
        else None
    )
    claims = {
        item["claim_id"]: ReviewClaim.model_validate(item)
        for item in scope_packet["claims"]
    }
    registry_context = list(scope_packet.get("registry_context") or [])

    if args.grouping:
        # A grouping plan belongs to the scope, not to one resolution run.
        # Re-deriving it per run costs a call and yields different group keys,
        # because the same Claims can be carved into topics more than one way.
        stored = _read(args.grouping)
        grouping = ClaimGroupingResponse.model_validate(stored["grouping"])
        repairs = list(stored.get("coverage_repairs") or [])
        validate_grouping(
            grouping=grouping, scope_label=scope_label, claim_ids=list(claims)
        )
        batches = batches_from_groups(grouping, batch_size=args.batch_size)
        if args.group_key:
            wanted = set(args.group_key)
            unknown = sorted(wanted - {item.group_key for item in grouping.groups})
            if unknown:
                raise SystemExit(f"no such group: {unknown}")
            grouping = grouping.model_copy(
                update={"groups": [g for g in grouping.groups if g.group_key in wanted]}
            )
            batches = batches_from_groups(grouping, batch_size=args.batch_size)
    elif args.group:
        # Grouping decides only what is compared together. Its output is a
        # batching plan; the rationale never reaches the proposer, because
        # telling it these Claims were grouped as related is a merge hint.
        grouper = build_grouper(args.group_model, args.group_effort)
        grouping_payload = {
            "scope_label": scope_label,
            "claims": [
                {
                    "claim_id": item.claim_id,
                    "statement": item.statement,
                    "source_id": item.source_id,
                    "scripture_refs": item.scripture_refs,
                }
                for item in claims.values()
            ],
        }
        raw_grouping, grouping_calls, grouping_seconds = _call(
            grouper, grouping_payload, args.output_dir / "raw-grouping.json"
        )
        grouping = ClaimGroupingResponse.model_validate(
            canonicalize_proposal(raw_grouping)[0]
        )
        grouping, repairs = repair_grouping(grouping=grouping, claim_ids=list(claims))
        grouping_report = validate_grouping(
            grouping=grouping, scope_label=scope_label, claim_ids=list(claims)
        )
        _write_immutable(
            args.output_dir / "grouping.json",
            {
                "schema_version": "wang_canonical_viewpoint_grouping_envelope_v1",
                "scope_label": scope_label,
                "grouping": grouping.model_dump(mode="json"),
                "coverage_repairs": repairs,
                "validation_report": grouping_report,
            },
        )
        print(
            json.dumps(
                {
                    "grouping_calls_executed": grouping_calls,
                    "grouping_wall_seconds": grouping_seconds,
                    "coverage_repairs": len(repairs),
                    **{k: grouping_report[k] for k in ("group_count", "group_sizes")},
                },
                ensure_ascii=False,
            )
        )
        if args.group_key:
            wanted = set(args.group_key)
            unknown = sorted(wanted - {item.group_key for item in grouping.groups})
            if unknown:
                raise SystemExit(f"no such group: {unknown}")
            grouping = grouping.model_copy(
                update={"groups": [g for g in grouping.groups if g.group_key in wanted]}
            )
        batches = batches_from_groups(grouping, batch_size=args.batch_size)
    else:
        batches = split_batches(sorted(claims), batch_size=args.batch_size)
    planned_batch_count = len(batches)
    if args.max_batches is not None:
        # 0 is meaningful: group the scope and stop, so the plan can be read
        # before any proposal call is spent against it.
        batches = batches[: args.max_batches]

    proposer = build_proposer(
        args.proposal_model,
        args.proposal_effort,
        provider=args.proposal_provider,
    )
    reviewer = build_reviewer(args.review_model, args.review_effort)
    reconsiderer = (
        None
        if args.no_reconsider
        else build_reconsiderer(
            args.proposal_model,
            args.proposal_effort,
            provider=args.proposal_provider,
        )
    )

    reports: list[dict[str, Any]] = []
    for ordinal, batch in enumerate(batches, start=1):
        batch_id = f"CVB-{scope_label}-{ordinal:03d}"
        batch_dir = args.output_dir / f"batch-{ordinal:03d}"
        try:
            report = run_batch(
                batch_id=batch_id,
                scope_label=scope_label,
                claims=[claims[claim_id] for claim_id in batch],
                registry_context=registry_context,
                pending_candidates=[],
                output_dir=batch_dir,
                proposer=proposer,
                reviewer=reviewer,
                reconsiderer=reconsiderer,
            )
        except BatchResolutionError as exc:
            exception_bundle = {
                "schema_version": "wang_canonical_viewpoint_batch_exception_v1",
                "batch_id": batch_id,
                "claim_ids": batch,
                "findings": exc.findings,
                "master_data_mutations": 0,
            }
            exception_bundle["artifact_sha256"] = sha256_json(exception_bundle)
            _write_immutable(batch_dir / "exception.json", exception_bundle)
            print(json.dumps(exception_bundle, ensure_ascii=False, indent=2))
            return 1
        reports.append(report)
        # Fail-stop, not a hand-off. A batch shares nothing with the next one
        # except committed Registry master data, so an unresolved batch stops
        # the scope instead of passing provisional candidates forward.
        if report["reconsideration_required"] and report.get("reconsideration_outcome") != "resolved":
            stop = {
                "schema_version": "wang_canonical_viewpoint_scope_stop_v1",
                "batch_id": batch_id,
                "reason": "batch did not resolve; scope stops here",
                "escalations": report.get("escalations") or [],
                "completed_batches": [item["batch_id"] for item in reports[:-1]],
            }
            stop["artifact_sha256"] = sha256_json(stop)
            _write_immutable(batch_dir / "scope-stop.json", stop)
            print(json.dumps(stop, ensure_ascii=False, indent=2))
            return 1

    route_stage: dict[str, Any]
    if args.no_routes:
        route_stage = {"status": "disabled"}
    elif args.group_key or len(reports) != planned_batch_count:
        route_stage = {
            "status": "not_started",
            "reason": "CVP scope is incomplete; Route Proposal requires all approved CVPs",
        }
    else:
        route_dir = args.output_dir / "routes"
        try:
            effective_proposals = [
                item["_effective_proposal"] for item in reports
            ]
            route_stage = run_route_scope(
                scope_label=scope_label,
                claims=[claims[key] for key in sorted(claims)],
                approved_viewpoints=approved_viewpoints_for_route(
                    effective_proposals=effective_proposals,
                    registry_context=registry_context,
                    approved_cut=approved_cut,
                ),
                effective_proposals=effective_proposals,
                existing_routes=list(scope_packet.get("route_registry_context") or []),
                local_candidate_revision_map=(
                    approved_cut["local_candidate_revision_map"]
                    if approved_cut
                    else None
                ),
                output_dir=route_dir,
                proposer=build_route_proposer(
                    args.proposal_model,
                    args.route_effort,
                    provider=args.proposal_provider,
                ),
                reviewer=build_route_reviewer(args.review_model, args.review_effort),
                reconsiderer=(
                    None
                    if args.no_reconsider
                    else build_route_reconsiderer(
                        args.proposal_model,
                        args.route_effort,
                        provider=args.proposal_provider,
                    )
                ),
            )
            route_status = (
                "completed_with_exceptions"
                if route_stage.get("exceptions")
                else "completed"
            )
            route_stage = {
                key: value
                for key, value in route_stage.items()
                if key != "measurements"
            } | {"status": route_status}
        except RouteStageNotReadyError as exc:
            # The no-apply POC cannot turn a local new-viewpoint key into a
            # formal CVR id. Preserve that boundary instead of fabricating one.
            route_stage = {
                "status": "awaiting_approved_cvp_apply",
                "reason": str(exc),
            }
            route_stage["artifact_sha256"] = sha256_json(route_stage)
            _write_immutable(route_dir / "route-stage-deferred.json", route_stage)
        except BatchResolutionError as exc:
            exception = {
                "schema_version": "wang_argument_route_scope_exception_v1",
                "scope_label": scope_label,
                "findings": exc.findings,
                "approved_cvps_unchanged": True,
                "master_data_mutations": 0,
            }
            exception["artifact_sha256"] = sha256_json(exception)
            _write_immutable(route_dir / "exception.json", exception)
            print(json.dumps(exception, ensure_ascii=False, indent=2))
            return 1

    summary = {
        "schema_version": "wang_canonical_viewpoint_scope_run_v1",
        "scope_label": scope_label,
        "batch_size": args.batch_size,
        "batch_count": len(reports),
        "claim_count": sum(item["claim_count"] for item in reports),
        "component_count": sum(item["component_count"] for item in reports),
        "batches_needing_reconsideration": [
            item["batch_id"] for item in reports if item["reconsideration_required"]
        ],
        "proposal_wall_seconds_total": round(
            sum(item["measurements"]["proposal_wall_seconds"] for item in reports), 3
        ),
        "review_wall_seconds_total": round(
            sum(item["measurements"]["review_wall_seconds"] for item in reports), 3
        ),
        "route_stage": route_stage,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    summary["artifact_sha256"] = sha256_json(summary)
    # Derived from the batch reports, same as batch-run.json: rewritten.
    (args.output_dir / "scope-run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
