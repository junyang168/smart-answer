"""Compile argument routes from a finished identity batch, one source at a time.

The identity pass says which Claims support which viewpoint.  This pass says how
the professor actually argued it — in one sermon, in the order he said it.

Each source gets its own call with only its own material, so a premise cannot
be borrowed from another sermon to make an argument look complete.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_argument_routes import (
    RouteProposalResponse,
    validate_route_proposal,
)
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    BatchResolutionError,
    CanonicalViewpointProposalResponse,
    canonicalize_proposal,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import (
    ReviewClaim,
    StructuredJsonReviewerAdapter,
)
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
CALL_TIMEOUT_SECONDS = 900.0

ROUTE_BEARING = frozenset({"member_existing", "support_existing", "new_viewpoint"})


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


def build_router(model: str, reasoning_effort: str) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=ClaudeSubscriptionClient(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=CALL_TIMEOUT_SECONDS,
        ),
        prompt=(PROMPT_DIR / "canonical_viewpoint_argument_route.md").read_text(
            encoding="utf-8"
        ),
        response_model=RouteProposalResponse,
        schema_name="wang_canonical_viewpoint_route_proposal_v1",
    )


def source_slices(
    *, proposal: CanonicalViewpointProposalResponse, claims: dict[str, ReviewClaim]
) -> dict[str, dict[str, Any]]:
    """Split one identity batch into per-source route inputs.

    A component's conclusion key is either the existing viewpoint revision it
    was linked to, or the batch-local key of the new viewpoint it proposed.
    """

    slices: dict[str, dict[str, Any]] = {}
    for decision in proposal.claim_decisions:
        claim = claims.get(decision.claim_id)
        if claim is None:
            continue
        for index, component in enumerate(decision.components):
            if component.disposition not in ROUTE_BEARING:
                continue
            entry = slices.setdefault(
                claim.source_id,
                {"claim_ids": set(), "components": [], "conclusions": set(), "member_steps": set()},
            )
            key = (
                component.target_viewpoint_revision_id
                or component.local_new_viewpoint_key
                or ""
            )
            entry["claim_ids"].add(decision.claim_id)
            entry["conclusions"].add(key)
            for step_id in component.evidence_step_ids:
                entry["components"].append((decision.claim_id, index, step_id))
                if component.disposition in {"member_existing", "new_viewpoint"}:
                    entry["member_steps"].add(step_id)
    return slices


def run_source(
    *,
    source_id: str,
    claims: list[ReviewClaim],
    entry: dict[str, Any],
    candidate_labels: dict[str, str],
    output_dir: Path,
    router: Any,
) -> dict[str, Any]:
    packet = {
        "schema_version": "wang_canonical_viewpoint_route_packet_v1",
        "source_id": source_id,
        "boundary_note": (
            "本 packet 只含这一篇来源的材料。别篇的推理步骤不在这里，也不得引用。"
        ),
        "conclusions": [
            # A conclusion the model cannot read is one it cannot honestly route
            # to. Existing viewpoints carry their core proposition from the
            # batch packet's registry context; new ones carry the candidate's.
            {"conclusion_key": key, "core_proposition": candidate_labels[key]}
            for key in sorted(entry["conclusions"])
        ],
        "claims": [
            {
                "claim_id": claim.claim_id,
                "statement": claim.statement,
                "evidence": [
                    {
                        "evidence_step_id": item.evidence_step_id,
                        "evidence_statement": item.evidence_statement,
                        "verbatim_excerpt": item.verbatim_excerpt,
                        "paragraph_key": item.paragraph_key,
                    }
                    for item in claim.evidence
                ],
            }
            for claim in claims
        ],
    }
    packet["packet_sha256"] = sha256_json(packet)
    _write_immutable(output_dir / "route-packet.json", packet)

    cache = output_dir / "raw-route.json"
    if cache.exists():
        cached = _read(cache)
        # A cached response answers the packet it was asked about. Reusing it
        # after the packet changed replays an answer to a different question,
        # which is how a fix to the packet would have silently kept the old
        # output. Fail closed and name the file to remove.
        if cached.get("packet_sha256") != packet["packet_sha256"]:
            raise ValueError(
                f"{cache} answers an older packet; delete it to re-ask this source"
            )
        raw, calls, seconds = cached["response"], 0, 0.0
    else:
        started = time.monotonic()
        raw = dict(router.generate(packet))
        seconds = round(time.monotonic() - started, 3)
        artifact = {
            "schema_version": "wang_canonical_viewpoint_route_raw_response_v1",
            "source_id": source_id,
            "packet_sha256": packet["packet_sha256"],
            "model_id": router.model_id,
            "backend": router.backend,
            "prompt_sha256": router.prompt_sha256,
            "wall_seconds": seconds,
            "response": raw,
        }
        artifact["artifact_sha256"] = sha256_json(artifact)
        _write_immutable(cache, artifact)
        calls = 1

    proposal = RouteProposalResponse.model_validate(canonicalize_proposal(raw)[0])
    source_steps = {
        item.evidence_step_id for claim in claims for item in claim.evidence
    }
    validation = validate_route_proposal(
        proposal=proposal,
        source_id=source_id,
        source_evidence_step_ids=sorted(source_steps),
        conclusion_keys=sorted(entry["conclusions"]),
        member_evidence_step_ids=sorted(entry["member_steps"]),
        identity_components=entry["components"],
    )
    payload = proposal.model_dump(mode="json")
    _write_immutable(
        output_dir / "routes.json",
        {
            "schema_version": "wang_canonical_viewpoint_route_envelope_v1",
            "source_id": source_id,
            "packet_sha256": packet["packet_sha256"],
            "routes_sha256": sha256_json(payload),
            "routes": payload,
            "validation_report": validation,
        },
    )
    return {**validation, "calls_executed": calls, "wall_seconds": seconds}


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True, help="a finished identity batch")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--effort", choices=("high", "xhigh", "max"), default="high")
    parser.add_argument("--source-id", action="append", help="limit to these sources")
    args = parser.parse_args()

    packet = _read(args.batch_dir / "batch-packet.json")
    claims = {item["claim_id"]: ReviewClaim.model_validate(item) for item in packet["claims"]}

    # Prefer the revised proposal when the batch went through reconsideration:
    # routes must be built from the judgments that survived review.
    revised = args.batch_dir / "reconsideration.json"
    if revised.exists():
        raw_proposal = _read(revised)["reconsideration"]["revised_proposal"]
    else:
        raw_proposal = _read(args.batch_dir / "proposal.json")["proposal"]
    proposal = CanonicalViewpointProposalResponse.model_validate(raw_proposal)
    labels = {
        item.local_key: item.core_proposition for item in proposal.new_viewpoint_candidates
    }
    # Existing viewpoints are named by revision id in the proposal; their wording
    # lives in the batch packet's registry context. Without it the model sees a
    # bare id and cannot tell what it would be routing to.
    for item in packet.get("registry_context") or []:
        revision_id = str(item.get("viewpoint_revision_id") or "")
        if revision_id:
            labels[revision_id] = str(item.get("core_proposition") or revision_id)

    slices = source_slices(proposal=proposal, claims=claims)
    wanted = set(args.source_id or slices)
    router = build_router(args.model, args.effort)

    reports = []
    for source_id in sorted(slices):
        if source_id not in wanted:
            continue
        entry = slices[source_id]
        source_claims = [claims[cid] for cid in sorted(entry["claim_ids"])]
        out = args.output_dir / source_id.replace("/", "_").replace(":", "_")
        try:
            reports.append(
                run_source(
                    source_id=source_id,
                    claims=source_claims,
                    entry=entry,
                    candidate_labels=labels,
                    output_dir=out,
                    router=router,
                )
            )
        except BatchResolutionError as exc:
            bundle = {
                "schema_version": "wang_canonical_viewpoint_route_exception_v1",
                "source_id": source_id,
                "findings": exc.findings,
            }
            bundle["artifact_sha256"] = sha256_json(bundle)
            _write_immutable(out / "exception.json", bundle)
            print(json.dumps(bundle, ensure_ascii=False, indent=2))
            return 1

    summary = {
        "schema_version": "wang_canonical_viewpoint_route_run_v1",
        "source_count": len(reports),
        "attestation_count": sum(item["attestation_count"] for item in reports),
        "full_count": sum(item["full_count"] for item in reports),
        "partial_count": sum(item["partial_count"] for item in reports),
        "inference_patterns": sorted({p for item in reports for p in item["inference_patterns"]}),
        "calls_executed": sum(item["calls_executed"] for item in reports),
        "wall_seconds_total": round(sum(item["wall_seconds"] for item in reports), 3),
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "route-run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
