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
from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
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
DEFAULT_POLICY = Path(__file__).resolve().parent / "policies/wang_route_resolution_policy_v8.json"
ROUTE_COLLECTIONS = {"argument_routes", "argument_route_revisions", "argument_route_attestations", "viewpoint_observation_supports"}


def verify_route_readback(store, package, retiring):
    """Compare the typed stored postimage, including importer defaults."""
    for collection in sorted(ROUTE_COLLECTIONS):
        model, id_field = KNOWLEDGE_COLLECTIONS[collection]
        for raw in package.get(collection, []):
            expected = model.model_validate(raw).model_dump(mode="json")
            observed = store.get_record(collection, expected[id_field])
            if observed is None or record_content_sha(observed) != record_content_sha(expected):
                raise ValueError(f"Route apply readback differs: {collection}/{expected[id_field]}")
    for collection, object_id in retiring:
        if collection not in ROUTE_COLLECTIONS or store.get_record(collection, object_id) is not None:
            raise ValueError(f"Route retirement readback differs: {collection}/{object_id}")


def authenticate_saved_route_review(output_dir, round_number, final, policy_path):
    """Use the common receipt verifier; a withdrawn prompt cannot become v5."""
    from backend.pipeline.viewpoint_resolution_runtime import read_saved_model_response
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        ArgumentRouteReviewV2Response, canonicalize_review,
    )
    policy = load_route_policy(policy_path)
    model = build_route_reviewer(str(policy['review']['model']), str(policy['review']['effort']),
        provider=str(policy['review']['provider']), prompt_file=str(policy['prompts']['review']),
        timeout_seconds=float(policy['call_timeout_seconds']), whole_cvp=True, observations=policy["schema_version"] == "wang_route_resolution_policy_v8")
    model.max_request_bytes = int(policy['max_request_bytes'])
    packet = json.loads((output_dir / f'route-review-packet-{round_number:03d}.json').read_text())
    raw = read_saved_model_response(model, packet, output_dir / f'raw-route-review-{round_number:03d}.json')
    from backend.api.canonical_repository.viewpoint_observation_support import ArgumentRouteReviewV3Response
    review_model = ArgumentRouteReviewV3Response if policy['schema_version'] == 'wang_route_resolution_policy_v8' else ArgumentRouteReviewV2Response
    review = review_model.model_validate(canonicalize_review(raw)[0]).model_dump(mode='json')
    if review != final['review']:
        raise ValueError('committed Route recovery differs from its actual provider review')


def resume_applied_cvp_route(store, viewpoint_id, output_dir, *, material_sha256=None, policy_path=DEFAULT_POLICY):
    """Finish a committed job before consulting its now-changed route baseline."""
    from backend.pipeline.cvp_sql_commit import plan_from_document
    output_dir = Path(output_dir)
    plan_path = output_dir / "route-change-plan.json"
    if not plan_path.exists():
        return None
    plan = plan_from_document(json.loads(plan_path.read_text()))
    history = store.get_change_history(plan.change_set_id)
    if not history or history['status'] != 'applied':
        return None
    def sealed(name, field):
        value = json.loads((output_dir / name).read_text())
        if value.get(field) != sha256_json({k:v for k,v in value.items() if k != field}):
            raise ValueError('saved Route recovery artifact changed: ' + name)
        return value
    material = sealed('cvp-route-materials.json', 'artifact_sha256')
    packet = sealed('route-packet.json', 'packet_sha256')
    run = sealed('route-cvp-run.json', 'artifact_sha256')
    package = json.loads((output_dir / 'route-change-package.json').read_text())
    metadata = history.get('metadata', {})
    if (material['viewpoint_id'] != viewpoint_id
            or (material_sha256 is not None and material['artifact_sha256'] != material_sha256)
            or run['route_packet_sha256'] != packet['packet_sha256']
            or history['source_kind'] != 'argument_route_resolution_v2'
            or history['source_sha256'] != plan.source_sha256
            or plan.source_sha256 != sha256_json(package)
            or history['fingerprint_sha256'] != plan.fingerprint_sha256
            or metadata.get('route_review_sha256') != run['review_sha256']
            or metadata.get('route_material_sha256') != material['artifact_sha256']
            or not run['rounds'] or run['rounds'][-1]['outcome'] != 'pass'):
        raise ValueError('committed Route recovery differs from its original approval')
    final = json.loads((output_dir / f"route-review-{run['rounds'][-1]['round']:03d}.json").read_text())
    if (final['review_sha256'] != run['review_sha256']
            or sha256_json(final['review']) != run['review_sha256']
            or final['validation']['outcome'] != 'pass'
            or final['review']['route_evidence_packet_sha256'] != packet['packet_sha256']):
        raise ValueError('committed Route recovery lacks its exact passing review')
    authenticate_saved_route_review(output_dir, run['rounds'][-1]['round'], final, policy_path)
    store.verify_current_read_pins({(r['collection'],r['object_id']):
        (r['revision'],r['content_sha256'],False) for r in material['record_pins']
        if r['collection'] not in ROUTE_COLLECTIONS})
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    for source in material['physical_sources']:
        verify_current_source_binding({'path':source['path'],'file_sha256':source['file_sha256']})
    links = {oid for (col,oid),row in store.read_cvp_links(viewpoint_ids=[viewpoint_id]).items()
             if row['payload'].get('effective_state') == 'active'}
    if links != {r['viewpoint_claim_link_id'] for r in material['links']}:
        raise ValueError('committed Route recovery membership changed')
    retiring = [(op.collection,op.object_id) for op in plan.operations if op.operation == 'retire']
    verify_route_readback(store, package, retiring)
    expected_routes = {r['argument_route_id'] for r in material['existing_routes']}
    expected_attestations = {r['argument_route_attestation_id'] for r in material['existing_attestations']}
    expected_observations = {r['observation_support_id'] for r in material.get('existing_observation_supports', [])}
    for collection, ident in retiring:
        if collection == 'argument_routes':expected_routes.discard(ident)
        if collection == 'argument_route_attestations':expected_attestations.discard(ident)
        if collection == 'viewpoint_observation_supports':expected_observations.discard(ident)
    expected_routes.update(r['argument_route_id'] for r in package.get('argument_routes',[]))
    expected_attestations.update(r['argument_route_attestation_id'] for r in package.get('argument_route_attestations',[]))
    expected_observations.update(r['observation_support_id'] for r in package.get('viewpoint_observation_supports', []))
    current = store.read_cvp_route_records(viewpoint_id)
    if ({oid for col,oid in current if col == 'argument_routes'} != expected_routes
            or {oid for col,oid in current if col == 'argument_route_attestations'} != expected_attestations
            or {oid for col,oid in current if col == 'viewpoint_observation_supports'} != expected_observations):
        raise ValueError('committed Route recovery current route population changed')
    report = {'schema_version':'wang_cvp_route_worker_result_v2', 'viewpoint_id':viewpoint_id,
        'material_sha256':material['artifact_sha256'], 'route_packet_sha256':packet['packet_sha256'],
        'review_sha256':run['review_sha256'], 'change_set_id':plan.change_set_id,
        'change_count':len(plan.operations), 'status':'applied_readback_verified',
        'recovered_from_applied':True, 'model_calls':0, 'semantic_writes':0}
    write_derived(output_dir / 'route-worker-result.json', report | {'artifact_sha256':sha256_json(report)})
    return report


def enqueue_current_cvp_routes(
    store: PostgresKnowledgeStore, viewpoint_ids: list[str], queue_dir: Path,
    *, policy_path: Path = DEFAULT_POLICY,
) -> list[str]:
    """Idempotently queue current route material after a CVP commit/readback."""

    policy = load_route_policy(policy_path)
    if policy["schema_version"] not in {"wang_route_resolution_policy_v5", "wang_route_resolution_policy_v8"}:
        raise ValueError("whole-CVP Route enqueue requires v5 or v8 policy")
    policy_sha = route_policy_fingerprint(
        policy, prompt_sha256s=route_policy_prompt_sha256s(
            policy, prompt_dir=Path(__file__).resolve().parent / "prompts"
        )
    )
    queue = CvpRouteQueue(queue_dir)
    jobs = []
    for viewpoint_id in sorted(set(viewpoint_ids)):
        material = prepare_cvp_route_materials(store, viewpoint_id, include_context=policy["schema_version"] == "wang_route_resolution_policy_v8")
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
    repair_evidence_input: bool = False,
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, Any]:
    """Build from current master data; write only after a whole-set pass."""

    output_dir = output_dir.resolve()

    def guard() -> None:
        if ownership_guard is not None:
            ownership_guard()

    guard()
    policy = load_route_policy(policy_path)
    observations_enabled = policy['schema_version'] == 'wang_route_resolution_policy_v8'
    from backend.pipeline.viewpoint_route_evidence_recovery import (
        REPAIR_DIRECTORY, prepare_evidence_input_recovery,
    )
    execution_dir = output_dir / REPAIR_DIRECTORY if repair_evidence_input else output_dir
    recovered = resume_applied_cvp_route(store, viewpoint_id, execution_dir, policy_path=policy_path) if apply else None
    if recovered is not None:
        return recovered
    materials = prepare_cvp_route_materials(store, viewpoint_id, include_context=observations_enabled)
    if not observations_enabled and materials.get('existing_observation_supports'):
        raise ValueError('v5 cannot maintain a CVP with v8 observation support records')
    claims = [ReviewClaim.model_validate(item) for item in materials["claims"]]
    saved_packet_path = output_dir / "route-packet.json"
    use_claim_evidence = (repair_evidence_input or not saved_packet_path.exists()
                         or bool(json.loads(saved_packet_path.read_text()).get("evidence_binding_contract")))
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
        claim_evidence_for_intermediate_steps=use_claim_evidence,
        observation_supports=observations_enabled,
        existing_observation_supports=materials.get('existing_observation_supports'),
        unusable_context_claims=materials.get('unusable_context_claims'),
    )
    if packet["membership_ledger"]["out_of_scope_members"]:
        raise ValueError("complete CVP Route packet omitted an active member")
    guard()
    recovery_seed = None
    if repair_evidence_input:
        recovery_seed = prepare_evidence_input_recovery(
            output_dir, packet, materials, proposer=proposer, reviewer=reviewer,
            max_correction_rounds=max_correction_rounds)
    output_dir = execution_dir
    write_immutable(output_dir / "cvp-route-materials.json", materials)
    result = run_cvp_route_scope(
        route_packet=packet, existing_routes=materials["existing_routes"],
        output_dir=output_dir, proposer=proposer, reviewer=reviewer,
        reconsiderer=reconsiderer, max_correction_rounds=max_correction_rounds,
        recovery_seed=recovery_seed,
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
        **({"execution_root": str(output_dir)} if repair_evidence_input else {}),
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
        if observations_enabled:
            scope_sets['observation_ids'] = sorted(o['observation_support_id']
                for o in materials.get('existing_observation_supports', []))
        applied = store.apply_plan(
            plan, metadata={"route_material_sha256": materials["artifact_sha256"],
                            "route_review_sha256": result["review_sha256"]},
            expected_current_route_material_pins=material_pins,
            expected_route_scope_sets=scope_sets,
            expected_current_viewpoint_revisions={viewpoint_id: materials["viewpoint_revision_id"]},
        )
        guard()
        verify_route_readback(store, package, retiring)
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
    parser.add_argument("--job-id", help="Resume one exact existing queued job")
    parser.add_argument("--repair-evidence-input", action="store_true",
                        help="Continue the saved initial review after the Claim-evidence boundary fix")
    args = parser.parse_args()
    if args.repair_evidence_input and (args.action != "run" or not args.job_id or not args.retry_exceptions):
        parser.error("evidence input repair requires run, an exact job ID and --retry-exceptions")
    policy = load_route_policy(args.route_policy)
    if policy["schema_version"] not in {"wang_route_resolution_policy_v5", "wang_route_resolution_policy_v8"}:
        parser.error("whole-CVP worker requires Route policy v5 or v8")
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
            material = prepare_cvp_route_materials(store, viewpoint_id, include_context=policy["schema_version"] == "wang_route_resolution_policy_v8")
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
                          resume_planned=args.apply, job_id=args.job_id)
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
        from backend.pipeline.viewpoint_route_evidence_recovery import REPAIR_DIRECTORY
        active_output = args.output_dir / job['job_id']
        if args.repair_evidence_input:
            active_output = active_output / REPAIR_DIRECTORY
        recovered = resume_applied_cvp_route(store, job['viewpoint_id'], active_output,
            material_sha256=job['material_sha256'], policy_path=args.route_policy) if args.apply else None
        if recovered is not None:
            guard()
            queue.finish(job['job_id'], worker_id=args.worker_id, epoch=epoch,
                         status='complete', detail=recovered['status'])
            print(json.dumps(recovered, ensure_ascii=False))
            return 0
        current = prepare_cvp_route_materials(store, job["viewpoint_id"], include_context=policy["schema_version"] == "wang_route_resolution_policy_v8")
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
                          whole_cvp=True, observations=policy["schema_version"] == "wang_route_resolution_policy_v8")
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
            repair_evidence_input=args.repair_evidence_input, policy_path=args.route_policy,
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
