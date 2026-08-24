"""Run one CanonicalViewpoint batch: propose, validate, review.

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
    ArgumentRouteProposalResponse,
    EXISTING_DISPOSITIONS,
    CanonicalViewpointReviewResponse,
    ClaimGroupingResponse,
    DEFAULT_BATCH_SIZE,
    batches_from_groups,
    build_batch_packet,
    canonicalize_proposal,
    repair_grouping,
    split_batches,
    validate_grouping,
    validate_proposal,
    validate_reconsideration,
    validate_route_proposal,
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


def build_proposer(model: str, reasoning_effort: str) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=ClaudeSubscriptionClient(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        ),
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


def build_reconsiderer(model: str, reasoning_effort: str) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=ClaudeSubscriptionClient(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        ),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_reconsideration.md").read_text(
            encoding="utf-8"
        ),
        response_model=CanonicalViewpointReconsiderationResponse,
        schema_name="wang_canonical_viewpoint_reconsideration_v1",
    )


def build_route_proposer(model: str, reasoning_effort: str) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=ClaudeSubscriptionClient(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        ),
        prompt=(PROMPT_DIR / "canonical_viewpoint_batch_routes.md").read_text(encoding="utf-8"),
        response_model=ArgumentRouteProposalResponse,
        schema_name="wang_canonical_viewpoint_route_proposal_v1",
    )


def build_route_packet(
    *,
    batch_id: str,
    proposal: CanonicalViewpointProposalResponse,
    claims: list[ReviewClaim],
    registry_context: list[dict[str, Any]],
) -> dict[str, Any]:
    """The route pass sees settled conclusions, not the identity question again.

    Only route-bearing components come through: a component ruled
    ``no_registry_assertion`` or ``deferred`` has no conclusion to argue toward.
    """

    bearing = EXISTING_DISPOSITIONS | {"new_viewpoint"}
    labels = {
        item.local_key: item.core_proposition for item in proposal.new_viewpoint_candidates
    }
    for item in registry_context:
        revision_id = str(item.get("viewpoint_revision_id") or "")
        if revision_id:
            labels[revision_id] = str(item.get("core_proposition") or revision_id)

    conclusions: dict[str, dict[str, Any]] = {}
    components: list[dict[str, Any]] = []
    claim_index = {item.claim_id: item for item in claims}
    for decision in proposal.claim_decisions:
        claim = claim_index.get(decision.claim_id)
        if claim is None:
            continue
        for index, component in enumerate(decision.components):
            if component.disposition not in bearing:
                continue
            key = str(
                component.target_viewpoint_revision_id or component.local_new_viewpoint_key
            )
            conclusions.setdefault(
                key, {"conclusion_key": key, "core_proposition": labels.get(key, key)}
            )
            components.append(
                {
                    "claim_component_key": f"{decision.claim_id}#{index}",
                    "claim_id": decision.claim_id,
                    "source_id": claim.source_id,
                    "component_text": component.statement_component(),
                    "disposition": component.disposition,
                    "conclusion_key": key,
                    "evidence_step_ids": component.evidence_step_ids,
                    "source_fragment_ids": component.source_fragment_ids,
                }
            )

    packet = {
        "schema_version": "wang_canonical_viewpoint_route_packet_v1",
        "batch_id": batch_id,
        "single_source_note": (
            "每个 attestation 的 Claim、EvidenceStep、SourceFragment 必须同属一篇来源。"
            "不得从两篇拼出一条谁都没讲完整的论证。"
        ),
        "settled_conclusions": [conclusions[key] for key in sorted(conclusions)],
        "route_bearing_components": components,
        "claims": [
            {
                "claim_id": claim.claim_id,
                "source_id": claim.source_id,
                "statement": claim.statement,
                "evidence": [
                    {
                        "evidence_step_id": item.evidence_step_id,
                        "source_fragment_id": item.source_fragment_id,
                        "evidence_statement": item.evidence_statement,
                        "verbatim_excerpt": item.verbatim_excerpt,
                        "paragraph_key": item.paragraph_key,
                    }
                    for item in claim.evidence
                ],
            }
            for claim in claims
            if claim.claim_id in {item["claim_id"] for item in components}
        ],
        "existing_routes": [dict(item) for item in registry_context if item.get("route_revision_id")],
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet


def _call(adapter: Any, payload: dict[str, Any], cache: Path) -> tuple[dict[str, Any], int, float]:
    """Return (raw response, calls executed, wall seconds).

    A cached response replays at zero cost, which is what makes a partly
    finished scope resumable.
    """

    if cache.exists():
        return _read(cache)["response"], 0, 0.0
    started = time.monotonic()
    raw = dict(adapter.generate(payload))
    elapsed = round(time.monotonic() - started, 3)
    artifact = {
        "schema_version": "wang_canonical_viewpoint_batch_raw_response_v1",
        "model_id": adapter.model_id,
        "backend": adapter.backend,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
        "request_payload_sha256": sha256_json(payload),
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
    route_proposer: Any = None,
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

    # Routes are a second call against settled conclusions. Asking for both in
    # one response timed out at 900s on this same 14-Claim batch.
    route_report: dict[str, Any] | None = None
    route_calls = 0
    route_seconds = 0.0
    route_payload: dict[str, Any] | None = None
    if route_proposer is not None:
        route_packet = build_route_packet(
            batch_id=batch_id,
            proposal=proposal,
            claims=claims,
            registry_context=registry_context,
        )
        _write_immutable(output_dir / "route-packet.json", route_packet)
        raw_routes, route_calls, route_seconds = _call(
            route_proposer, route_packet, output_dir / "raw-routes.json"
        )
        routes = ArgumentRouteProposalResponse.model_validate(
            canonicalize_proposal(raw_routes)[0]
        )
        route_report = validate_route_proposal(
            routes=routes,
            batch_id=batch_id,
            claims=claims,
            known_revisions=[
                str(item["viewpoint_revision_id"])
                for item in registry_context
                if item.get("viewpoint_revision_id")
            ],
            settled_conclusion_keys=[
                str(item["conclusion_key"]) for item in route_packet["settled_conclusions"]
            ],
        )
        route_payload = routes.model_dump(mode="json")
        _write_immutable(
            output_dir / "routes.json",
            {
                "schema_version": "wang_canonical_viewpoint_route_envelope_v1",
                "batch_id": batch_id,
                "route_packet_sha256": route_packet["packet_sha256"],
                "proposal_sha256": proposal_sha,
                "routes_sha256": sha256_json(route_payload),
                "routes": route_payload,
                "validation_report": route_report,
            },
        )

    review_packet = {
        "batch_id": batch_id,
        "packet": packet,
        "proposal_sha256": proposal_sha,
        "proposal": proposal_payload,
        "routes": route_payload,
    }
    raw_review, review_calls, review_seconds = _call(
        reviewer, review_packet, output_dir / "raw-review.json"
    )
    review = CanonicalViewpointReviewResponse.model_validate(raw_review)
    review_validation = validate_review(
        review=review,
        proposal=proposal,
        proposal_sha256=proposal_sha,
        routes=routes if route_proposer is not None else None,
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
    if review_validation["reconsideration_required"] and reconsiderer is not None:
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
        reconsideration = CanonicalViewpointReconsiderationResponse.model_validate(
            canonicalize_proposal(raw_reconsideration)[0]
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
            },
        )

    report = {
        "schema_version": "wang_canonical_viewpoint_batch_run_v1",
        "batch_id": batch_id,
        "scope_label": scope_label,
        "claim_count": len(claims),
        "component_count": validation["component_count"],
        "disposition_counts": validation["disposition_counts"],
        "new_viewpoint_candidate_count": validation["new_viewpoint_candidate_count"],
        "route_count": route_report["route_count"] if route_report else 0,
        "attestation_count": route_report["attestation_count"] if route_report else 0,
        "full_attestation_count": route_report["full_count"] if route_report else 0,
        "outcome": review_validation["outcome"],
        "reconsideration_required": review_validation["reconsideration_required"],
        "novelty_status": review_validation["novelty_status"],
        "review_decision_counts": review_validation["decision_counts"],
        "packet_sha256": packet["packet_sha256"],
        "proposal_sha256": proposal_sha,
        "review_sha256": review_sha,
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
        "route_calls_executed": route_calls,
        "route_wall_seconds": route_seconds,
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
    return {**report, "measurements": measurements}


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True, help="scope packet from viewpoint_scope_packet_runner")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
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
    if args.max_batches is not None:
        # 0 is meaningful: group the scope and stop, so the plan can be read
        # before any proposal call is spent against it.
        batches = batches[: args.max_batches]

    proposer = build_proposer(args.proposal_model, args.proposal_effort)
    reviewer = build_reviewer(args.review_model, args.review_effort)
    reconsiderer = (
        None if args.no_reconsider else build_reconsiderer(args.proposal_model, args.proposal_effort)
    )
    route_proposer = (
        None if args.no_routes else build_route_proposer(args.proposal_model, args.route_effort)
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
                route_proposer=route_proposer,
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
