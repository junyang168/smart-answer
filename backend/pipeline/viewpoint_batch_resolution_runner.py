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
    CanonicalViewpointReviewResponse,
    DEFAULT_BATCH_SIZE,
    build_batch_packet,
    split_batches,
    validate_proposal,
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
    proposal = CanonicalViewpointProposalResponse.model_validate(raw_proposal)
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
        review=review, proposal=proposal, proposal_sha256=proposal_sha
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

    report = {
        "schema_version": "wang_canonical_viewpoint_batch_run_v1",
        "batch_id": batch_id,
        "scope_label": scope_label,
        "claim_count": len(claims),
        "component_count": validation["component_count"],
        "disposition_counts": validation["disposition_counts"],
        "new_viewpoint_candidate_count": validation["new_viewpoint_candidate_count"],
        "outcome": review_validation["outcome"],
        "reconsideration_required": review_validation["reconsideration_required"],
        "novelty_status": review_validation["novelty_status"],
        "review_decision_counts": review_validation["decision_counts"],
        "packet_sha256": packet["packet_sha256"],
        "proposal_sha256": proposal_sha,
        "review_sha256": sha256_json(review_payload),
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    report["artifact_sha256"] = sha256_json(report)
    _write_immutable(output_dir / "batch-run.json", report)

    # Wall time and call counts decide the real batch ceiling, so they must be
    # recorded — but they differ every execution, which is exactly why they are
    # not part of the immutable result. Kept as an append-only execution log.
    measurements = {
        "proposal_calls_executed": proposal_calls,
        "proposal_wall_seconds": proposal_seconds,
        "review_calls_executed": review_calls,
        "review_wall_seconds": review_seconds,
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


def pending_synopsis(proposal: CanonicalViewpointProposalResponse, batch_id: str) -> list[dict[str, Any]]:
    """Non-authoritative context so the next batch can see unapplied candidates.

    A later batch must not treat these as `member_existing`; they exist so it
    can notice that its own new candidate may be the same proposition and route
    that to exception instead of minting a duplicate identity.
    """

    return [
        {
            "origin_batch_id": batch_id,
            "local_key": item.local_key,
            "core_proposition": item.core_proposition,
            "polarity": item.polarity,
            "modality": item.modality,
            "scripture_scope": item.scripture_scope,
            "status": "pending_not_applied",
            "usage": "blocker_context_only",
        }
        for item in proposal.new_viewpoint_candidates
    ]


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
    parser.add_argument("--max-batches", type=int, help="stop after this many batches")
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

    batches = split_batches(sorted(claims), batch_size=args.batch_size)
    if args.max_batches:
        batches = batches[: args.max_batches]

    proposer = build_proposer(args.proposal_model, args.proposal_effort)
    reviewer = build_reviewer(args.review_model, args.review_effort)

    pending: list[dict[str, Any]] = []
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
                pending_candidates=pending,
                output_dir=batch_dir,
                proposer=proposer,
                reviewer=reviewer,
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
        # Serial checkpoint: batch N+1 sees batch N's candidates, so the same
        # truth condition is not minted twice under two local keys.
        proposal = CanonicalViewpointProposalResponse.model_validate(
            _read(batch_dir / "proposal.json")["proposal"]
        )
        pending.extend(pending_synopsis(proposal, batch_id))

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
        "pending_candidate_count": len(pending),
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
    _write_immutable(args.output_dir / "scope-run.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
