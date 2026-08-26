"""Resolve one CanonicalViewpoint scope and enqueue committed CVPs for Route work.

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
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    BatchResolutionError,
    CanonicalViewpointProposalResponse,
    CanonicalViewpointReconsiderationResponse,
    IdentityConsolidationResponse,
    CanonicalViewpointReviewResponse,
    ClaimGroupingResponse,
    DEFAULT_BATCH_SIZE,
    anchor_proposal_spans,
    apply_consolidation,
    apply_reconsideration_patches,
    batches_from_groups,
    build_batch_packet,
    build_cvp_batch_readback_receipt,
    build_route_resolution_job,
    canonicalize_proposal,
    canonicalize_review,
    component_key,
    repair_grouping,
    split_batches,
    validate_grouping,
    validate_consolidation,
    validate_consolidation_fallback,
    validate_proposal,
    validate_reconsideration,
    validate_review,
)
from backend.api.canonical_repository.viewpoint_batch_changeset import (
    compile_cvp_batch_package,
    load_revision_dependents,
)
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_route_queue import (
    FileRouteResolutionQueue,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import (
    ReviewClaim,
    StructuredJsonReviewerAdapter,
)
from backend.pipeline.viewpoint_scope_packet_runner import (
    SCOPE_PACKET_VERSION,
    registry_context as load_registry_context,
)
from backend.pipeline.viewpoint_route_policy import (
    DEFAULT_ROUTE_POLICY_PATH,
    load_route_policy,
    route_policy_fingerprint,
    route_policy_prompt_sha256s,
)
from backend.pipeline.viewpoint_resolution_runtime import (
    CALL_TIMEOUT_SECONDS,
    PROJECT_ROOT,
    PROMPT_DIR,
    call_model as _call,
    read_artifact as _read,
    recorded_model_executions as _recorded_model_executions,
    stable_decided_at as _stable_decided_at,
    subscription_client as _subscription_client,
    write_current_state as _write_current_state,
    write_derived as _write_derived,
    write_immutable as _write_immutable,
)

def build_proposer(
    model: str, reasoning_effort: str, *, provider: str = "claude"
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_subscription_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_proposal.md").read_text(
            encoding="utf-8"
        ),
        response_model=CanonicalViewpointProposalResponse,
        schema_name="wang_canonical_viewpoint_proposal_v1",
    )


def build_reviewer(
    model: str, reasoning_effort: str, *, provider: str = "claude"
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_subscription_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_review.md").read_text(
            encoding="utf-8"
        ),
        response_model=CanonicalViewpointReviewResponse,
        schema_name="wang_canonical_viewpoint_review_v1",
    )


def build_consolidator(
    model: str, reasoning_effort: str, *, provider: str = "claude"
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_subscription_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "canonical_viewpoint_identity_consolidation.md").read_text(
            encoding="utf-8"
        ),
        response_model=IdentityConsolidationResponse,
        schema_name="wang_canonical_viewpoint_identity_consolidation_v1",
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
        client=_subscription_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_reconsideration.md").read_text(
            encoding="utf-8"
        ),
        response_model=CanonicalViewpointReconsiderationResponse,
        schema_name="wang_canonical_viewpoint_reconsideration_v3",
    )


def build_consolidation_packet(
    *,
    proposal: CanonicalViewpointProposalResponse,
    claims: Sequence[ReviewClaim],
    registry_context: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Candidates with the words they rest on, plus the Registry to check against.

    Each candidate carries the preacher's own verbatim rather than only the
    proposer's normalized wording: normalization is what splits one package of
    claims into several, and the raw sentences are what show it was one.
    """

    claim_index = {item.claim_id: item for item in claims}
    members: dict[str, list[dict[str, Any]]] = {}
    for decision in proposal.claim_decisions:
        claim = claim_index[decision.claim_id]
        for component in decision.components:
            key = component.local_new_viewpoint_key
            if not key or component.disposition != "new_viewpoint":
                continue
            steps = set(component.evidence_step_ids)
            members.setdefault(str(key), []).append(
                {
                    "claim_id": claim.claim_id,
                    "source_id": claim.source_id,
                    "statement": claim.statement,
                    "verbatim": sorted(
                        {
                            item.verbatim_excerpt
                            for item in claim.evidence
                            if item.evidence_step_id in steps and item.verbatim_excerpt
                        }
                    ),
                }
            )

    packet = {
        "schema_version": "wang_canonical_viewpoint_consolidation_packet_v1",
        "batch_id": proposal.batch_id,
        "new_viewpoint_candidates": [
            {
                **{
                    key: value
                    for key, value in item.model_dump(mode="json").items()
                    if key != "novelty_comparison"
                },
                "attested_by": members.get(item.local_key, []),
            }
            for item in proposal.new_viewpoint_candidates
        ],
        "registry_viewpoints": [dict(item) for item in registry_context],
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet


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
    consolidator: Any = None,
    dependents_loader: Any = None,
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
    registry_revision_ids = [
        str(item["viewpoint_revision_id"])
        for item in registry_context
        if item.get("viewpoint_revision_id")
    ]
    validation = validate_proposal(
        proposal=proposal,
        batch_id=batch_id,
        claims=claims,
        registry_revision_ids=registry_revision_ids,
    )

    # Identity, asked on its own. The proposer decides it while also carving
    # components, assigning roles, writing relations and building a structure,
    # and it duplicated a viewpoint it had explicitly compared against.
    consolidation_calls = 0
    consolidation_seconds = 0.0
    consolidation: IdentityConsolidationResponse | None = None
    if consolidator is not None and proposal.new_viewpoint_candidates and registry_context:
        consolidation_packet = build_consolidation_packet(
            proposal=proposal, claims=claims, registry_context=registry_context
        )
        raw_consolidation, consolidation_calls, consolidation_seconds = _call(
            consolidator, consolidation_packet, output_dir / "raw-consolidation.json"
        )
        consolidation = IdentityConsolidationResponse.model_validate(raw_consolidation)
        consolidation_report = validate_consolidation(
            consolidation=consolidation,
            proposal=proposal,
            registry_revision_ids=registry_revision_ids,
        )
        consolidated = apply_consolidation(
            consolidation=consolidation, proposal=proposal
        )
        if consolidated != proposal:
            # The merged proposal is what the reviewer sees, so it must satisfy
            # the same deterministic contract the original did.
            validation = validate_proposal(
                proposal=consolidated,
                batch_id=batch_id,
                claims=claims,
                registry_revision_ids=registry_revision_ids,
            )
        proposal = consolidated
        _write_immutable(
            output_dir / "consolidation.json",
            {
                "schema_version": "wang_canonical_viewpoint_consolidation_envelope_v1",
                "batch_id": batch_id,
                "packet_sha256": consolidation_packet["packet_sha256"],
                "consolidation": consolidation.model_dump(mode="json"),
                "validation_report": consolidation_report,
            },
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

    # A revision supersedes wording other records were checked against. The
    # reviewer sees those records, because it is the one asserting the truth
    # condition did not move, and the ChangeSet re-points only what it confirms.
    revision_dependents: dict[str, Any] = {}
    if proposal.viewpoint_revisions and dependents_loader is not None:
        revision_dependents = dependents_loader(
            [item.target_viewpoint_revision_id for item in proposal.viewpoint_revisions]
        )
    review_packet = {
        "batch_id": batch_id,
        "packet": packet,
        "proposal_sha256": proposal_sha,
        "proposal": proposal_payload,
        "revision_dependents": revision_dependents,
    }
    raw_review, review_calls, review_seconds = _call(
        reviewer, review_packet, output_dir / "raw-review.json"
    )
    canonical_review, review_normalization_changes = canonicalize_review(raw_review)
    review = CanonicalViewpointReviewResponse.model_validate(canonical_review)
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
            "normalization": {
                "raw_response_sha256": sha256_json(dict(raw_review)),
                "changed_paths": review_normalization_changes,
                "reader_visible_text_changed": False,
                "truth_conditions_changed": False,
            },
        },
    )

    review_sha = sha256_json(review_payload)
    reconsideration_report: dict[str, Any] | None = None
    reconsideration: CanonicalViewpointReconsiderationResponse | None = None
    reconsideration_calls = 0
    reconsideration_seconds = 0.0
    effective_proposal = proposal
    effective_validation = validation
    if review_validation["correction_required"] and reconsiderer is not None:
        # Identity consolidation's verdicts are an input to the correction, not
        # just a gate after it. A candidate ruled the same viewpoint as a
        # committed one has to end up connected to that exact revision when the
        # merge does not land, and twice in one scope the proposer answered by
        # moving the committed viewpoint's edges onto the candidate instead --
        # coherent if they are the same thing, but it records no connection
        # between them and the batch stops with zero mutations. Stating the
        # obligation next to the findings beats burying it in the prompt.
        connection_required = [
            {
                "matched_viewpoint_revision_id": str(item.target_viewpoint_revision_id),
                "ruled_same_as_candidate": item.local_key,
            }
            for item in (consolidation.verdicts if consolidation is not None else [])
            if item.verdict != "new" and item.target_viewpoint_revision_id
        ]
        reconsider_packet = {
            "batch_id": batch_id,
            "packet": packet,
            "proposal_sha256": proposal_sha,
            "proposal": proposal_payload,
            "review_sha256": review_sha,
            "review": review_payload,
            "connection_required": connection_required,
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
        revised_proposal = apply_reconsideration_patches(
            reconsideration=reconsideration,
            proposal=proposal,
            review=review,
        )
        # The revision faces the same deterministic gates as the first pass;
        # accepting a finding cannot smuggle a bad span or a stale target past
        # checks the original proposal had to clear.
        revised_validation = validate_proposal(
            proposal=revised_proposal,
            batch_id=batch_id,
            claims=claims,
            registry_revision_ids=[
                str(item["viewpoint_revision_id"])
                for item in registry_context
                if item.get("viewpoint_revision_id")
            ],
        )
        if reconsideration_report["outcome"] == "resolved":
            effective_proposal = revised_proposal
            effective_validation = revised_validation
        _write_immutable(
            output_dir / "reconsideration.json",
            {
                "schema_version": "wang_canonical_viewpoint_reconsideration_envelope_v1",
                "batch_id": batch_id,
                "proposal_sha256": proposal_sha,
                "review_sha256": review_sha,
                "reconsideration": reconsideration.model_dump(mode="json"),
                "effective_proposal": revised_proposal.model_dump(mode="json"),
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

    # Checked on whatever the batch actually settled on, so a merge dropped in
    # the correction round is caught as surely as one dropped in review.
    consolidation_fallback_report = None
    if consolidation is not None:
        consolidation_fallback_report = validate_consolidation_fallback(
            consolidation=consolidation, proposal=effective_proposal
        )

    recorded_model_executions = _recorded_model_executions(
        output_dir,
        raw_artifacts={
            "proposal": "raw-proposal.json",
            "consolidation": "raw-consolidation.json",
            "review": "raw-review.json",
            "reconsideration": "raw-reconsideration.json",
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
        "viewpoint_revision_count": effective_validation["viewpoint_revision_count"],
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
        "consolidation_fallback": consolidation_fallback_report,
        "recorded_model_executions": recorded_model_executions,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    report["artifact_sha256"] = sha256_json(report)
    # Rewritten, not immutable. The batch's semantic artifacts — packet,
    # proposal, review, reconsideration — are the immutable record; this file
    # is wholly derived from them. Freezing a derived summary means any change
    # to its shape blocks reruns of batches whose model calls are all cached,
    # which is how adding the reconsideration fields broke a completed batch.
    _write_derived(output_dir / "batch-run.json", report)
    superseded = []
    if (output_dir / "exception.json").exists():
        superseded.append("exception.json")
    superseded.extend(
        str(path.relative_to(output_dir))
        for path in sorted((output_dir / "exceptions").glob("*.json"))
    )
    resolved = not review_validation["reconsideration_required"] or bool(
        reconsideration_report and reconsideration_report["outcome"] == "resolved"
    )
    _write_current_state(
        output_dir,
        schema_version="wang_canonical_viewpoint_batch_current_state_v1",
        identity={"batch_id": batch_id},
        status="resolved" if resolved else "requires_exception",
        authoritative_artifact="batch-run.json",
        authoritative_artifact_sha256=report["artifact_sha256"],
        superseded_artifacts=superseded if resolved else [],
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
        "_original_proposal": proposal,
        "_review": review,
        "_reconsideration": reconsideration,
        "_revision_dependents": revision_dependents,
        "_effective_validation": effective_validation,
    }
def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True, help="scope packet from viewpoint_scope_packet_runner")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--proposal-provider",
        choices=("claude", "codex"),
        default="codex",
        help="subscription CLI used for CVP proposal and correction; never falls back",
    )
    parser.add_argument("--proposal-model", default="gpt-5.6-sol")
    parser.add_argument("--proposal-effort", choices=("high", "xhigh", "max"), default="high")
    parser.add_argument(
        "--review-provider",
        choices=("claude", "codex"),
        default="claude",
        help="subscription CLI used for CVP review; never falls back",
    )
    parser.add_argument("--review-model", default="claude-opus-5")
    parser.add_argument("--review-effort", choices=("high", "xhigh"), default="high")
    parser.add_argument(
        "--max-batches",
        type=int,
        help="stop after this many batches; 0 groups the scope and stops",
    )
    parser.add_argument(
        "--route-policy",
        type=Path,
        default=DEFAULT_ROUTE_POLICY_PATH,
        help="versioned policy used to fingerprint every enqueued Route job",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "explicitly apply each passing CVP ChangeSet, verify authority readback, "
            "and enqueue route work; default is plan-only and fail-stop"
        ),
    )
    parser.add_argument("--database-url")
    parser.add_argument(
        "--no-reconsider",
        action="store_true",
        help="stop after review instead of giving the proposer its one revision",
    )
    parser.add_argument(
        "--no-consolidate",
        action="store_true",
        help="skip the identity-only pass over this batch's new viewpoints",
    )
    parser.add_argument(
        "--consolidation-provider", choices=("claude", "codex"), default="claude"
    )
    parser.add_argument(
        "--consolidation-model",
        default="claude-opus-5",
        help=(
            "identity-only pass; on the 2026-08-25 calibration Opus caught the "
            "duplicate in 3 of 3 runs and Sol in 0 of 3"
        ),
    )
    parser.add_argument(
        "--consolidation-effort", choices=("high", "xhigh"), default="high"
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
    route_policy = load_route_policy(args.route_policy)
    route_policy_sha256 = route_policy_fingerprint(
        route_policy,
        prompt_sha256s=route_policy_prompt_sha256s(
            route_policy, prompt_dir=PROMPT_DIR
        ),
    )
    claims = {
        item["claim_id"]: ReviewClaim.model_validate(item)
        for item in scope_packet["claims"]
    }
    registry_context = list(scope_packet.get("registry_context") or [])
    store = PostgresKnowledgeStore(args.database_url)
    route_queue = FileRouteResolutionQueue(args.output_dir / "route-queue")
    enqueued_route_jobs: list[dict[str, Any]] = []
    awaiting_cvp_apply = False
    master_data_mutations = 0

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
    if args.max_batches is not None:
        # 0 is meaningful: group the scope and stop, so the plan can be read
        # before any proposal call is spent against it.
        batches = batches[: args.max_batches]

    proposer = build_proposer(
        args.proposal_model,
        args.proposal_effort,
        provider=args.proposal_provider,
    )
    reviewer = build_reviewer(
        args.review_model,
        args.review_effort,
        provider=args.review_provider,
    )
    reconsiderer = (
        None
        if args.no_reconsider
        else build_reconsiderer(
            args.proposal_model,
            args.proposal_effort,
            provider=args.proposal_provider,
        )
    )

    consolidator = (
        None
        if args.no_consolidate
        else build_consolidator(
            args.consolidation_model,
            args.consolidation_effort,
            provider=args.consolidation_provider,
        )
    )

    reports: list[dict[str, Any]] = []
    completed_batches: list[str] = []
    for ordinal, batch in enumerate(batches, start=1):
        batch_id = f"CVB-{scope_label}-{ordinal:03d}"
        batch_dir = args.output_dir / f"batch-{ordinal:03d}"

        # An applied batch is finished master data, not work to redo. Resume
        # runs on the model-call cache, and a prompt edit invalidates it -- so
        # without this, editing a prompt to unblock a later batch re-derives an
        # earlier one that is already committed, and any drift in the new answer
        # is written as a second set of records.
        state_path = batch_dir / "current-state.json"
        if state_path.exists():
            state = _read(state_path)
            if str(state.get("status", "")).startswith("applied"):
                completed_batches.append(batch_id)
                # Its records are in the store, and the next batch is entitled
                # to see them: the serial checkpoint is the whole reason a later
                # batch can match what an earlier one committed. Skipping the
                # work must not skip the handoff.
                registry_context = load_registry_context(
                    store.list_records("canonical_viewpoints"),
                    store.list_records("viewpoint_revisions"),
                )
                print(
                    json.dumps(
                        {
                            "batch_id": batch_id,
                            "skipped": "already applied",
                            "authoritative_artifact": state.get("authoritative_artifact"),
                        },
                        ensure_ascii=False,
                    )
                )
                continue

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
                consolidator=consolidator,
                dependents_loader=lambda targets: load_revision_dependents(
                    store=store, target_revision_ids=targets
                ),
            )
        except (BatchResolutionError, ValidationError) as exc:
            findings = (
                exc.findings
                if isinstance(exc, BatchResolutionError)
                else [
                    f"model_response_schema_invalid:{item['loc']}:{item['msg']}"
                    for item in exc.errors(include_url=False)
                ]
            )
            exception_bundle = {
                "schema_version": "wang_canonical_viewpoint_batch_exception_v1",
                "batch_id": batch_id,
                "claim_ids": batch,
                "findings": findings,
                "master_data_mutations": 0,
            }
            exception_bundle["artifact_sha256"] = sha256_json(exception_bundle)
            exception_name = f"exceptions/{exception_bundle['artifact_sha256']}.json"
            _write_immutable(batch_dir / exception_name, exception_bundle)
            _write_current_state(
                batch_dir,
                schema_version="wang_canonical_viewpoint_batch_current_state_v1",
                identity={"batch_id": batch_id},
                status="exception",
                authoritative_artifact=exception_name,
                authoritative_artifact_sha256=exception_bundle["artifact_sha256"],
            )
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
                "completed_batches": [
                    *completed_batches,
                    *(item["batch_id"] for item in reports[:-1]),
                ],
            }
            stop["artifact_sha256"] = sha256_json(stop)
            _write_immutable(batch_dir / "scope-stop.json", stop)
            print(json.dumps(stop, ensure_ascii=False, indent=2))
            return 1

        raw_proposal = _read(batch_dir / "raw-proposal.json")
        raw_review = _read(batch_dir / "raw-review.json")
        correction = report["_reconsideration"]
        effective_raw = (
            _read(batch_dir / "raw-reconsideration.json")
            if correction is not None
            else raw_proposal
        )
        package = compile_cvp_batch_package(
            proposal=report["_effective_proposal"],
            review=report["_review"],
            reviewed_proposal=(
                report["_original_proposal"] if correction is not None else None
            ),
            reconsideration=correction,
            deterministic_validation_sha256=report["_effective_validation"][
                "artifact_sha256"
            ],
            scope_manifest_sha256=str(scope_packet["claim_manifest_sha256"]),
            claims=[claims[claim_id] for claim_id in batch],
            registry_context=registry_context,
            revision_dependents=report["_revision_dependents"],
            proposal_artifact_sha256=str(effective_raw["artifact_sha256"]),
            review_artifact_sha256=str(raw_review["artifact_sha256"]),
            proposer_model_id=str(effective_raw["model_id"]),
            reviewer_model_id=str(raw_review["model_id"]),
            decided_at=_stable_decided_at(batch_dir),
        )
        _write_immutable(batch_dir / "cvp-change-package.json", package)
        plan = store.plan_package(package, source_kind="cvp_batch_resolution")
        plan_document = plan.as_dict()
        plan_document["schema_version"] = "wang_cvp_batch_changeset_plan_v1"
        plan_document["apply_allowed"] = bool(args.apply)
        plan_document["artifact_sha256"] = sha256_json(plan_document)
        _write_derived(batch_dir / "cvp-change-plan.json", plan_document)

        if not args.apply:
            awaiting_cvp_apply = True
            _write_current_state(
                batch_dir,
                schema_version="wang_canonical_viewpoint_batch_current_state_v1",
                identity={"batch_id": batch_id},
                status="awaiting_cvp_apply",
                authoritative_artifact="cvp-change-plan.json",
                authoritative_artifact_sha256=plan_document["artifact_sha256"],
            )
            break

        apply_result = store.apply_plan(
            plan,
            metadata={
                "scope_label": scope_label,
                "batch_id": batch_id,
                "proposal_artifact_sha256": effective_raw["artifact_sha256"],
                "review_artifact_sha256": raw_review["artifact_sha256"],
            },
        )
        if apply_result.get("status") == "applied":
            master_data_mutations += len(plan.operations)
        expected_revisions = {
            str(item["viewpoint_id"]): str(item["current_revision_id"])
            for item in package["canonical_viewpoints"]
        }
        current_by_viewpoint = {
            str(item["viewpoint_id"]): str(item["viewpoint_revision_id"])
            for item in registry_context
        }
        for decision in package["viewpoint_identity_decisions"]:
            viewpoint_id = str(decision["resolved_viewpoint_id"])
            if viewpoint_id not in expected_revisions:
                expected_revisions[viewpoint_id] = current_by_viewpoint[viewpoint_id]
        observed_revisions: dict[str, str] = {}
        for viewpoint_id in expected_revisions:
            record = store.get_record("canonical_viewpoints", viewpoint_id)
            if record:
                observed_revisions[viewpoint_id] = str(record["current_revision_id"])
        receipt = build_cvp_batch_readback_receipt(
            scope_label=scope_label,
            scope_manifest_sha256=str(scope_packet["claim_manifest_sha256"]),
            triggering_cvp_batch_id=batch_id,
            cvp_changeset_id=plan.change_set_id,
            cvp_changeset_sha256=plan.fingerprint_sha256,
            expected_current_revisions=expected_revisions,
            observed_current_revisions=observed_revisions,
        )
        _write_immutable(
            batch_dir / "cvp-readback-receipt.json",
            receipt.model_dump(mode="json"),
        )
        apply_result_document = {
            "schema_version": "wang_cvp_batch_apply_result_v1",
            "batch_id": batch_id,
            "change_set_id": plan.change_set_id,
            "result": apply_result,
        }
        apply_result_document["artifact_sha256"] = sha256_json(
            apply_result_document
        )
        _write_derived(batch_dir / "cvp-apply-result.json", apply_result_document)
        route_job = build_route_resolution_job(
            receipt=receipt,
            evidence_scope_sha256=str(scope_packet["packet_sha256"]),
            route_policy_fingerprint_sha256=route_policy_sha256,
        )
        route_job_payload = route_job.model_dump(mode="json")
        route_queue.enqueue(route_job)
        enqueued_route_jobs.append(route_job_payload)
        _write_current_state(
            batch_dir,
            schema_version="wang_canonical_viewpoint_batch_current_state_v1",
            identity={"batch_id": batch_id},
            status="applied_and_route_enqueued",
            authoritative_artifact="cvp-readback-receipt.json",
            authoritative_artifact_sha256=receipt.artifact_sha256,
        )
        registry_context = load_registry_context(
            store.list_records("canonical_viewpoints"),
            store.list_records("viewpoint_revisions"),
        )

    route_stage: dict[str, Any]
    if awaiting_cvp_apply:
        route_stage = {
            "status": "awaiting_cvp_apply",
            "reason": "plan-only run stops before the next serial CVP batch",
        }
    elif args.apply:
        route_stage = {
            "status": "queued",
            "job_ids": [item["job_id"] for item in enqueued_route_jobs],
        }
    else:
        route_stage = {
            "status": "not_enqueued",
            "reason": "plan-only CVP execution does not create Route work; use the queue worker after CVP apply/readback",
        }

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
        "latest_execution_proposal_wall_seconds_total": round(
            sum(item["measurements"]["proposal_wall_seconds"] for item in reports), 3
        ),
        "latest_execution_review_wall_seconds_total": round(
            sum(item["measurements"]["review_wall_seconds"] for item in reports), 3
        ),
        "cvp_model_calls_recorded_total": sum(
            item["recorded_model_executions"]["calls_recorded_total"]
            for item in reports
        ),
        "cvp_model_wall_seconds_recorded_total": round(
            sum(
                item["recorded_model_executions"]["wall_seconds_recorded_total"]
                for item in reports
            ),
            3,
        ),
        "route_stage": route_stage,
        "master_data_mutations": master_data_mutations,
        "apply_allowed": bool(args.apply),
    }
    summary["artifact_sha256"] = sha256_json(summary)
    # Derived from the batch reports, same as batch-run.json: rewritten.
    _write_derived(args.output_dir / "scope-run.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
