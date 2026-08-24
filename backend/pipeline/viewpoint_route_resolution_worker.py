"""Resolve queued ArgumentRoutes from committed CanonicalViewpoint Registry state."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_batch_resolution import RouteResolutionWorkUnit
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.api.canonical_repository.viewpoint_route_changeset import (
    compile_argument_route_package,
)
from backend.api.canonical_repository.viewpoint_route_queue import FileRouteResolutionQueue
from backend.pipeline.viewpoint_batch_resolution_runner import (
    PROJECT_ROOT,
    _read,
    _stable_decided_at,
    _write_derived,
    _write_immutable,
    build_registry_route_packet,
    build_route_proposer,
    build_route_reconsiderer,
    build_route_reviewer,
    run_route_scope,
)
from backend.pipeline.viewpoint_scope_packet_runner import (
    SCOPE_PACKET_VERSION,
    registry_context,
)


def _current_viewpoint_revisions(store: PostgresKnowledgeStore) -> dict[str, str]:
    return {
        str(item["viewpoint_id"]): str(item["current_revision_id"])
        for item in store.list_records("canonical_viewpoints")
        if item.get("route_status") != "retired"
    }


def _existing_route_context(store: PostgresKnowledgeStore) -> list[dict[str, Any]]:
    revisions = {
        str(item["argument_route_revision_id"]): item
        for item in store.list_records("argument_route_revisions")
    }
    return [
        {
            "argument_route_id": str(route["argument_route_id"]),
            "route_revision_id": str(route["current_revision_id"]),
            "conclusion_viewpoint_revision_id": str(
                revisions[str(route["current_revision_id"])][
                    "validated_against_conclusion_viewpoint_revision_id"
                ]
            ),
            "route": route,
            "revision": revisions[str(route["current_revision_id"])],
        }
        for route in store.list_records("argument_routes")
        if route.get("route_status") == "active"
        and str(route.get("current_revision_id")) in revisions
    ]


def process_work_unit(
    *,
    work: RouteResolutionWorkUnit,
    scope_packet: dict[str, Any],
    output_dir: Path,
    store: PostgresKnowledgeStore,
    proposer: Any,
    reviewer: Any,
    reconsiderer: Any,
    apply: bool,
) -> dict[str, Any]:
    """Run one claimed unit; database mutation remains an explicit option."""

    if scope_packet.get("schema_version") != SCOPE_PACKET_VERSION:
        raise ValueError("Route worker requires the current viewpoint scope packet")
    if scope_packet.get("scope_label") != work.scope_label:
        raise ValueError("Route work and evidence packet scope differ")
    if scope_packet.get("claim_manifest_sha256") != work.scope_manifest_sha256:
        raise ValueError("Route work and Claim manifest differ")

    claims = [ReviewClaim.model_validate(item) for item in scope_packet["claims"]]
    all_context = registry_context(
        store.list_records("canonical_viewpoints"),
        store.list_records("viewpoint_revisions"),
    )
    expected = {
        item.viewpoint_id: item.viewpoint_revision_id
        for item in work.current_viewpoint_revisions
    }
    approved = [
        item for item in all_context
        if expected.get(str(item["viewpoint_id"])) == str(item["viewpoint_revision_id"])
    ]
    if len(approved) != len(expected):
        raise ValueError("Registry no longer contains the work unit conclusion cut")
    existing_routes = _existing_route_context(store)
    packet = build_registry_route_packet(
        scope_label=work.scope_label,
        approved_viewpoints=approved,
        claims=claims,
        viewpoint_claim_links=store.list_records("viewpoint_claim_links"),
        existing_routes=existing_routes,
    )
    _write_immutable(output_dir / "work-unit.json", work.model_dump(mode="json"))
    report = run_route_scope(
        scope_label=work.scope_label,
        claims=claims,
        approved_viewpoints=approved,
        effective_proposals=None,
        existing_routes=existing_routes,
        output_dir=output_dir,
        proposer=proposer,
        reviewer=reviewer,
        reconsiderer=reconsiderer,
        route_packet=packet,
    )
    if not report["passing_route_keys"]:
        result = {
            "status": "no_passing_routes",
            "change_set_id": None,
            "change_count": 0,
            "exceptions": report["exceptions"] or ["no_passing_routes"],
        }
        result["artifact_sha256"] = sha256_json(result)
        _write_derived(output_dir / "route-worker-result.json", result)
        return result
    raw_proposal = _read(output_dir / "raw-route-proposal.json")
    raw_review = _read(output_dir / "raw-route-review.json")
    package = compile_argument_route_package(
        proposal=report["_effective_proposal"],
        passing_route_keys=report["passing_route_keys"],
        passing_attestation_keys=report["passing_attestation_keys"],
        route_packet=packet,
        existing_routes=existing_routes,
        claims=claims,
        proposal_artifact_sha256=str(report["effective_route_proposal_sha256"]),
        review_artifact_sha256=str(report["route_review_sha256"]),
        proposer_model_id=str(raw_proposal["model_id"]),
        reviewer_model_id=str(raw_review["model_id"]),
        decided_at=_stable_decided_at(output_dir),
    )
    _write_immutable(output_dir / "route-change-package.json", package)
    plan = store.plan_package(package, source_kind="argument_route_resolution")
    plan_document = plan.as_dict() | {
        "schema_version": "wang_argument_route_changeset_plan_v2",
        "apply_allowed": apply,
    }
    plan_document["artifact_sha256"] = sha256_json(plan_document)
    _write_derived(output_dir / "route-change-plan.json", plan_document)
    result: dict[str, Any] = {
        "status": "planned",
        "change_set_id": plan.change_set_id,
        "change_count": len(plan.operations),
        "exceptions": report["exceptions"],
    }
    if apply:
        applied = store.apply_plan(
            plan,
            metadata={
                "route_work_unit_sha256": work.artifact_sha256,
                "route_packet_sha256": packet["packet_sha256"],
                "route_review_sha256": report["route_review_sha256"],
            },
            expected_current_viewpoint_revisions=expected,
        )
        missing = []
        for collection, id_field in (
            ("argument_routes", "argument_route_id"),
            ("argument_route_revisions", "argument_route_revision_id"),
            ("argument_route_attestations", "argument_route_attestation_id"),
        ):
            for item in package.get(collection) or []:
                if store.get_record(collection, str(item[id_field])) is None:
                    missing.append(f"{collection}:{item[id_field]}")
        if missing:
            raise ValueError("Route authority readback missing: " + ", ".join(missing))
        result |= {"status": "applied", "apply_result": applied}
    result["artifact_sha256"] = sha256_json(result)
    _write_derived(output_dir / "route-worker-result.json", result)
    return result


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}:{os.getpid()}")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--proposal-model", default="gpt-5.6-sol")
    parser.add_argument("--proposal-effort", default="high")
    parser.add_argument("--review-model", default="claude-opus-5")
    parser.add_argument("--review-effort", default="high")
    args = parser.parse_args()

    store = PostgresKnowledgeStore(args.database_url)
    queue = FileRouteResolutionQueue(args.queue_dir)
    work = queue.claim(
        worker_id=args.worker_id,
        current_viewpoint_revisions=_current_viewpoint_revisions(store),
    )
    if work is None:
        print(json.dumps({"status": "idle"}, ensure_ascii=False))
        return 0
    jobs = queue.jobs_for_work_unit(work)
    scope_packet = _read(args.packet)
    evidence_shas = {item.evidence_scope_sha256 for item in jobs}
    if evidence_shas != {str(scope_packet.get("packet_sha256"))}:
        queue.finish(work, worker_id=args.worker_id, status="exception", detail="evidence scope SHA mismatch")
        raise SystemExit("queued Route job does not bind the supplied evidence packet")
    run_dir = args.output_dir / work.artifact_sha256
    try:
        result = process_work_unit(
            work=work,
            scope_packet=scope_packet,
            output_dir=run_dir,
            store=store,
            proposer=build_route_proposer(args.proposal_model, args.proposal_effort, provider="codex"),
            reviewer=build_route_reviewer(args.review_model, args.review_effort, provider="claude"),
            reconsiderer=build_route_reconsiderer(args.proposal_model, args.proposal_effort, provider="codex"),
            apply=args.apply,
        )
    except Exception as exc:
        queue.finish(work, worker_id=args.worker_id, status="exception", detail=str(exc))
        raise
    if args.apply:
        queue.finish(
            work,
            worker_id=args.worker_id,
            status="exception" if result["exceptions"] else "resolved",
            detail="; ".join(result["exceptions"]) if result["exceptions"] else "applied and read back",
        )
    else:
        queue.release(work, worker_id=args.worker_id, detail="plan-only run; no master mutation")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
