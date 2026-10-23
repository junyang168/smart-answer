"""Apply one reviewed, complete ArgumentRoute set for a current CVP."""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_route_cvp_queue import (
    CvpRouteQueue,
    make_cvp_route_job,
)
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.pipeline.viewpoint_resolution_runtime import stable_decided_at, write_derived, write_immutable
from backend.pipeline.viewpoint_route_cvp_resolution import (
    CvpRouteReviewIncomplete,
    compile_cvp_route_package,
    run_cvp_route_scope,
)
from backend.pipeline.viewpoint_route_materials import (
    prepare_cvp_route_materials,
    verify_cvp_route_materials,
)
from backend.pipeline.viewpoint_route_resolution import build_registry_route_packet
from backend.pipeline.viewpoint_route_resolution import (
    build_route_proposer, build_route_reviewer, build_route_reconsiderer,
)
from backend.pipeline.viewpoint_route_resolution_worker import OwnershipGuardedAdapter
from backend.pipeline.viewpoint_route_policy import (
    load_route_policy, route_policy_fingerprint, route_policy_prompt_sha256s,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = Path(__file__).resolve().parent / "policies/wang_route_resolution_policy_v5.json"


def enqueue_current_cvp_routes(
    store: PostgresKnowledgeStore, viewpoint_ids: list[str], queue_dir: Path,
    *, policy_path: Path = DEFAULT_POLICY,
) -> list[str]:
    """Idempotently queue current route material after a CVP commit/readback."""

    policy = load_route_policy(policy_path)
    if policy["schema_version"] != "wang_route_resolution_policy_v5":
        raise ValueError("whole-CVP Route enqueue requires v5 policy")
    policy_sha = route_policy_fingerprint(
        policy, prompt_sha256s=route_policy_prompt_sha256s(
            policy, prompt_dir=Path(__file__).resolve().parent / "prompts"
        )
    )
    queue = CvpRouteQueue(queue_dir)
    jobs = []
    for viewpoint_id in sorted(set(viewpoint_ids)):
        material = prepare_cvp_route_materials(store, viewpoint_id)
        job = make_cvp_route_job(viewpoint_id, material["artifact_sha256"], policy_sha)
        queue.enqueue(job)
        jobs.append(job["job_id"])
    return jobs


def route_affected_viewpoints(package: dict[str, Any]) -> list[str]:
    """CVPs whose head, revision or active member link was written."""

    ids = {row["viewpoint_id"] for row in package.get("viewpoint_revisions", [])}
    ids.update(row["viewpoint_id"] for row in package.get("viewpoint_claim_links", []))
    return sorted(ids)


def handoff_cvp_route_jobs(
    store: PostgresKnowledgeStore, package: dict[str, Any],
    queue_dir: Path, issue_dir: Path,
) -> dict[str, Any]:
    """Keep a committed CVP independent from later Route material failures."""

    jobs = []
    issues = {}
    for viewpoint_id in route_affected_viewpoints(package):
        try:
            jobs.extend(enqueue_current_cvp_routes(store, [viewpoint_id], queue_dir))
        except Exception as exc:
            issues[viewpoint_id] = str(exc)
    report = {"schema_version": "wang_cvp_route_handoff_v2",
              "viewpoint_ids": route_affected_viewpoints(package),
              "job_ids": sorted(jobs), "issues": issues}
    write_derived(issue_dir / "route-handoff.json",
                  report | {"artifact_sha256": sha256_json(report)})
    return report


def handoff_completed_cvp_groups(store: PostgresKnowledgeStore, parent: dict[str, Any], root: Path) -> None:
    """Retry the idempotent Route handoff after a completed CVP action resumes."""

    for unit in parent.get("units", {}).values():
        if unit.get("status") != "completed" or not unit.get("completed_step"):
            continue
        step = store.get_cvp_group(unit["completed_step"])
        handoff_cvp_route_jobs(
            store,
            step["reviewed"]["evidence"]["package"]["payload"],
            root / "route-queue-v2",
            Path(step["materials"]["step_root"]),
        )


def process_cvp_route(
    *, store: PostgresKnowledgeStore, viewpoint_id: str, output_dir: Path,
    proposer: Any, reviewer: Any, reconsiderer: Any,
    max_correction_rounds: int, apply: bool = False,
    ownership_guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Build from current master data; write only after a whole-set pass."""

    output_dir = output_dir.resolve()

    def guard() -> None:
        if ownership_guard is not None:
            ownership_guard()

    guard()
    materials = prepare_cvp_route_materials(store, viewpoint_id)
    write_immutable(output_dir / "cvp-route-materials.json", materials)
    claims = [ReviewClaim.model_validate(item) for item in materials["claims"]]
    packet = build_registry_route_packet(
        scope_label=f"cvp-route:{viewpoint_id}:{materials['viewpoint_revision_id']}",
        approved_viewpoints=[materials["revision"] | {
            "viewpoint_id": viewpoint_id,
            "viewpoint_revision_id": materials["viewpoint_revision_id"],
        }],
        claims=claims,
        viewpoint_claim_links=materials["links"],
        existing_routes=materials["existing_routes"],
        existing_attestations=materials["existing_attestations"],
        source_sections=materials["source_sections"],
        complete_cvp=True,
        unavailable_evidence=materials.get('unavailable_evidence'),
    )
    if packet["membership_ledger"]["out_of_scope_members"]:
        raise ValueError("complete CVP Route packet omitted an active member")
    guard()
    result = run_cvp_route_scope(
        route_packet=packet, existing_routes=materials["existing_routes"],
        output_dir=output_dir, proposer=proposer, reviewer=reviewer,
        reconsiderer=reconsiderer, max_correction_rounds=max_correction_rounds,
    )
    if result["cvp_re_review_exceptions"]:
        raise CvpRouteReviewIncomplete("route review raised CVP identity exceptions")
    package, retiring = compile_cvp_route_package(
        result=result, route_packet=packet, materials=materials,
        proposer_model_id=proposer.model_id, reviewer_model_id=reviewer.model_id,
        decided_at=stable_decided_at(output_dir),
    )
    write_immutable(output_dir / "route-change-package.json", package)
    guard()
    verify_cvp_route_materials(store, materials)
    plan = store.plan_package(package, source_kind="argument_route_resolution_v2",
                              retiring_keys=retiring, transactional=True)
    plan_context = store.read_package_cvp_context(package, retiring=retiring)
    material_pins = {(row["collection"], row["object_id"]):
                     (row["revision"], row["content_sha256"], False)
                     for row in materials["record_pins"]}
    for key, row in plan_context.items():
        pin = (row["revision"], row["content_sha256"],
               bool(row.get("retired") or row.get("retired_at")))
        if key in material_pins and material_pins[key] != pin:
            raise ValueError("Route planning dependency changed after material preparation")
        material_pins[key] = pin
    store.verify_current_read_pins(material_pins)
    write_immutable(output_dir / "route-change-plan.json", plan.as_dict())
    report = {
        "schema_version": "wang_cvp_route_worker_result_v2",
        "viewpoint_id": viewpoint_id,
        "material_sha256": materials["artifact_sha256"],
        "route_packet_sha256": packet["packet_sha256"],
        "review_sha256": result["review_sha256"],
        "change_set_id": plan.change_set_id,
        "change_count": len(plan.operations),
        "status": "planned",
    }
    if apply:
        guard()
        verify_cvp_route_materials(store, materials)
        scope_sets = {
            "viewpoint_id": viewpoint_id,
            "link_ids": sorted(item["viewpoint_claim_link_id"] for item in materials["links"]),
            "route_ids": sorted(item["argument_route_id"]
                                for item in materials["existing_routes"]),
            "attestation_ids": sorted(item["argument_route_attestation_id"]
                                      for item in materials["existing_attestations"]),
        }
        applied = store.apply_plan(
            plan, metadata={"route_material_sha256": materials["artifact_sha256"],
                            "route_review_sha256": result["review_sha256"]},
            expected_current_route_material_pins=material_pins,
            expected_route_scope_sets=scope_sets,
            expected_current_viewpoint_revisions={viewpoint_id: materials["viewpoint_revision_id"]},
        )
        guard()
        for collection, id_field in (
            ("argument_routes", "argument_route_id"),
            ("argument_route_revisions", "argument_route_revision_id"),
            ("argument_route_attestations", "argument_route_attestation_id"),
        ):
            for expected in package.get(collection, []):
                observed = store.get_record(collection, expected[id_field])
                if observed is None or record_content_sha(observed) != record_content_sha(expected):
                    raise ValueError(f"Route apply readback differs: {collection}/{expected[id_field]}")
        for collection, object_id in retiring:
            if store.get_record(collection, object_id) is not None:
                raise ValueError(f"Route retirement readback differs: {collection}/{object_id}")
        report |= {"status": "applied_readback_verified", "apply_result": applied}
    write_derived(output_dir / "route-worker-result.json",
                  report | {"artifact_sha256": sha256_json(report)})
    return report


def main() -> int:
    """Enqueue current CVPs or consume one current-material route job."""

    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enqueue", "run"))
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--viewpoint-id", action="append", default=[])
    parser.add_argument("--route-policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--database-url")
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}:{os.getpid()}")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retry-exceptions", action="store_true")
    args = parser.parse_args()
    policy = load_route_policy(args.route_policy)
    if policy["schema_version"] != "wang_route_resolution_policy_v5":
        parser.error("whole-CVP worker requires Route policy v5")
    policy_sha = route_policy_fingerprint(
        policy, prompt_sha256s=route_policy_prompt_sha256s(
            policy, prompt_dir=Path(__file__).resolve().parent / "prompts"
        )
    )
    store = PostgresKnowledgeStore(args.database_url)
    queue = CvpRouteQueue(args.queue_dir)
    if args.action == "enqueue":
        if not args.viewpoint_id or args.apply:
            parser.error("enqueue requires viewpoint IDs and does not apply")
        jobs = []
        for viewpoint_id in sorted(set(args.viewpoint_id)):
            material = prepare_cvp_route_materials(store, viewpoint_id)
            job = make_cvp_route_job(viewpoint_id, material["artifact_sha256"], policy_sha)
            queue.enqueue(job)
            jobs.append(job["job_id"])
        print(json.dumps({"status": "enqueued", "job_ids": jobs}, ensure_ascii=False))
        return 0
    if args.viewpoint_id:
        parser.error("run claims one queued CVP; do not pass viewpoint IDs")
    if args.output_dir is None:
        parser.error("run requires --output-dir")
    lease_seconds = int(policy["call_timeout_seconds"]) + 300
    claimed = queue.claim(worker_id=args.worker_id, policy_sha256=policy_sha,
                          lease_seconds=lease_seconds,
                          retry_exceptions=args.retry_exceptions,
                          resume_planned=args.apply)
    if claimed is None:
        print(json.dumps({"status": "idle"}))
        return 0
    job, epoch = claimed
    stop = threading.Event()
    heartbeat_error: list[Exception] = []

    def renew() -> None:
        while not stop.wait(60):
            try:
                queue.renew(job["job_id"], worker_id=args.worker_id,
                            epoch=epoch, lease_seconds=lease_seconds)
            except Exception as exc:  # lease loss is raised at the next guard
                heartbeat_error.append(exc)
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()

    def guard() -> None:
        if heartbeat_error:
            raise heartbeat_error[0]
        queue.assert_owner(job["job_id"], worker_id=args.worker_id, epoch=epoch)

    try:
        current = prepare_cvp_route_materials(store, job["viewpoint_id"])
        if current["artifact_sha256"] != job["material_sha256"]:
            new_job = make_cvp_route_job(job["viewpoint_id"],
                                         current["artifact_sha256"], policy_sha)
            queue.enqueue(new_job)
            guard()
            queue.finish(job["job_id"], worker_id=args.worker_id, epoch=epoch,
                         status="superseded", detail=f"replaced by {new_job['job_id']}")
            print(json.dumps({"status": "superseded", "replacement_job_id": new_job["job_id"]}))
            return 0
        adapters = {
            role: builder(str(policy[role]["model"]), str(policy[role]["effort"]),
                          provider=str(policy[role]["provider"]),
                          prompt_file=str(policy["prompts"][prompt_role]),
                          timeout_seconds=float(policy["call_timeout_seconds"]),
                          whole_cvp=True)
            for role, prompt_role, builder in (
                ("proposal", "proposal", build_route_proposer),
                ("review", "review", build_route_reviewer),
                ("correction", "correction", build_route_reconsiderer),
            )
        }
        for adapter in adapters.values():
            adapter.max_request_bytes = int(policy["max_request_bytes"])
        result = process_cvp_route(
            store=store, viewpoint_id=job["viewpoint_id"],
            output_dir=args.output_dir / job["job_id"],
            proposer=OwnershipGuardedAdapter(adapters["proposal"], guard),
            reviewer=OwnershipGuardedAdapter(adapters["review"], guard),
            reconsiderer=OwnershipGuardedAdapter(adapters["correction"], guard),
            max_correction_rounds=int(policy["max_correction_rounds"]),
            apply=args.apply, ownership_guard=guard,
        )
        guard()
        queue.finish(job["job_id"], worker_id=args.worker_id, epoch=epoch,
                     status="complete" if args.apply else "planned",
                     detail=result["status"])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        guard()
        queue.finish(job["job_id"], worker_id=args.worker_id, epoch=epoch,
                     status="exception", detail=str(exc))
        raise
    finally:
        stop.set()
        thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
