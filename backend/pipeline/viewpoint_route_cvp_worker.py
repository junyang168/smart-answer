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
    response_path = output_dir / f'raw-route-review-{round_number:03d}.json'
    source = final.get('saved_model_response_source')
    if source is not None:
        from backend.pipeline.cvp_sql_commit import check_binding
        if set(source) != {'request', 'response', 'authorization'}:
            raise ValueError('saved Route review source bindings are incomplete')
        for pin in source.values():
            if Path(pin['path']).resolve().parent != output_dir.resolve():
                raise ValueError('saved Route review source belongs to another job')
            check_binding(pin)
        if json.loads(Path(source['request']['path']).read_text()) != packet:
            raise ValueError('saved Route review source request differs from final packet')
        response_path = Path(source['response']['path'])
    from backend.pipeline.viewpoint_route_validation_repair import (
        read_validated_changes, REVIEW_REPAIR_INSTRUCTION,
    )
    raw, _ = read_validated_changes(model, packet, response_path,
        repair_instruction=REVIEW_REPAIR_INSTRUCTION)
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
    if run.get('continued_execution_bindings'):
        from backend.pipeline.cvp_sql_commit import check_binding
        for pin in run['continued_execution_bindings']:
            check_binding(pin)
    if run.get('correction_round_extension_sha256'):
        from backend.pipeline.viewpoint_route_round_extension import round_extension, FILENAME
        extension = json.loads((output_dir/FILENAME).read_text())
        extension = round_extension(output_dir, extension['job'], load_route_policy(policy_path))
        if extension['artifact_sha256'] != run['correction_round_extension_sha256']:
            raise ValueError('committed Route round extension differs from its reservation')
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
    if final.get('saved_model_response_source') is not None:
        source_sha = sha256_json(final['saved_model_response_source'])
        if (run.get('saved_review_source_sha256') != source_sha
                or metadata.get('route_approval_source_sha256') != source_sha):
            raise ValueError('committed Route review source differs from recorded approval')
    if (final['review_sha256'] != run['review_sha256']
            or sha256_json(final['review']) != run['review_sha256']
            or final['validation']['outcome'] != 'pass'
            or final['review']['route_evidence_packet_sha256'] != packet['packet_sha256']):
        raise ValueError('committed Route recovery lacks its exact passing review')
    authenticate_saved_route_review(output_dir, run['rounds'][-1]['round'], final, policy_path)
    store.verify_current_read_pins({(r['collection'],r['object_id']):
        (r['revision'],r['content_sha256'],bool(r.get('retired'))) for r in material['record_pins']
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
    for route in package.get('argument_routes', []):
        if route['route_status'] == 'active':
            expected_routes.add(route['argument_route_id'])
        else:
            expected_routes.discard(route['argument_route_id'])
    expected_attestations.update(r['argument_route_attestation_id'] for r in package.get('argument_route_attestations',[]))
    expected_observations.update(r['observation_support_id'] for r in package.get('viewpoint_observation_supports', []))
    current = store.read_cvp_route_records(viewpoint_id)
    if ({oid for (col,oid), row in current.items() if col == 'argument_routes'
         and row['payload']['route_status'] == 'active'} != expected_routes
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
        material = prepare_cvp_route_materials(store, viewpoint_id,
            include_context=policy["schema_version"] == "wang_route_resolution_policy_v8",
            include_connection_judgments=True)
        job = make_cvp_route_job(viewpoint_id, material["artifact_sha256"], policy_sha)
        queue.enqueue(job)
        jobs.append(job["job_id"])
    return jobs


def route_affected_viewpoints(package: dict[str, Any]) -> list[str]:
    """CVPs whose head, revision or active member link was written."""

    ids = {row["viewpoint_id"] for col in ("canonical_viewpoints", "viewpoint_revisions")
           for row in package.get(col, [])}
    ids.update(row["viewpoint_id"] for row in package.get("viewpoint_claim_links", []))
    return sorted(ids)


def handoff_cvp_route_jobs(
    store: PostgresKnowledgeStore, package: dict[str, Any],
    queue_dir: Path, issue_dir: Path, *, update_viewpoint_ids=(),
) -> dict[str, Any]:
    """Keep a committed CVP independent from later Route material failures."""

    jobs = []
    issues = {}
    inactive = {}
    dependencies = {}
    ids = set(route_affected_viewpoints(package)) | set(update_viewpoint_ids)
    # A moved link affects both old and new targets. The complete update scope
    # carries the old endpoint even when it has no business writes.
    previous_path = issue_dir / 'route-handoff.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    if previous and previous.get('artifact_sha256') != sha256_json({
            k:v for k,v in previous.items() if k!='artifact_sha256'}):
        raise ValueError('saved Route handoff changed')
    pending = sorted(ids)
    while pending:
        viewpoint_id = pending.pop(0)
        try:
            if not update_viewpoint_ids:
                jobs.extend(enqueue_current_cvp_routes(store, [viewpoint_id], queue_dir))
                continue
            row = store.get_record_state('canonical_viewpoints', viewpoint_id)
            if not row or row.get('retired'):
                raise ValueError('Route handoff current viewpoint is missing or retired')
            view = row['payload']
            if update_viewpoint_ids and view['identity_status'] != 'active':
                # Existing runtime projection excludes inactive conclusions.
                # Close their current use, retaining historical route records;
                # never send them to the active-CVP model worker.
                inactive[viewpoint_id] = {'identity_status':view['identity_status'],
                    'revision':row['revision'], 'content_sha256':row['content_sha256'],
                    'current_use':'closed_by_inactive_conclusion'}
                survivor = view.get('redirect_to_viewpoint_id')
                if survivor and survivor not in ids:
                    ids.add(survivor);pending.append(survivor)
                continue
            policy = load_route_policy(DEFAULT_POLICY)
            policy_sha = route_policy_fingerprint(policy, prompt_sha256s=route_policy_prompt_sha256s(
                policy, prompt_dir=Path(__file__).resolve().parent/'prompts'))
            material = prepare_cvp_route_materials(store, viewpoint_id,
                include_context=policy['schema_version']=='wang_route_resolution_policy_v8',
                include_connection_judgments=True)
            # Route writes change their own baseline. They must not create a
            # new update job when the CVP/Claim/source dependencies are intact.
            basis = sha256_json({'policy_sha256':policy_sha, 'current_input':{
                field:material.get(field) for field in ('viewpoint','revision','links','claims',
                    'source_sections','physical_sources','unavailable_evidence','approved_connection_judgments')}})
            old = previous.get('dependencies',{}).get(viewpoint_id)
            queue = CvpRouteQueue(queue_dir)
            state_path = queue.states / (old['job_id']+'.json') if old else None
            if old and old['dependency_sha256']==basis and state_path.exists():
                job_id=old['job_id']
                visited=set()
                while True:
                    if job_id in visited:
                        raise ValueError('Route handoff supersession cycle')
                    visited.add(job_id)
                    state=json.loads((queue.states/(job_id+'.json')).read_text())
                    if state['status']!='superseded':break
                    detail=state.get('detail') or ''
                    if not detail.startswith('replaced by CRJ-'):
                        raise ValueError('superseded Route job lacks its replacement identity')
                    successor=detail.removeprefix('replaced by ')
                    next_job=json.loads((queue.jobs/(successor+'.json')).read_text())
                    if (next_job.get('viewpoint_id')!=viewpoint_id or next_job.get('policy_sha256')!=policy_sha
                            or next_job.get('job_id')!=successor or next_job.get('artifact_sha256')!=sha256_json({
                                k:v for k,v in next_job.items() if k!='artifact_sha256'})):
                        raise ValueError('Route replacement differs from the bound handoff')
                    job_id=successor
            else:
                job=make_cvp_route_job(viewpoint_id,material['artifact_sha256'],policy_sha)
                queue.enqueue(job);job_id=job['job_id']
            jobs.append(job_id)
            dependencies[viewpoint_id]={'dependency_sha256':basis,'job_id':job_id}
        except Exception as exc:
            issues[viewpoint_id] = str(exc)
    report = {"schema_version": "wang_cvp_route_handoff_v2",
              "viewpoint_ids": sorted(ids), "job_ids": sorted(set(jobs)), "issues": issues}
    if update_viewpoint_ids:
        report.update(dependencies=dependencies,inactive_viewpoints=inactive)
    write_derived(previous_path, report | {"artifact_sha256": sha256_json(report)})
    return report


def handoff_completed_cvp_groups(store: PostgresKnowledgeStore, parent: dict[str, Any], root: Path, *, track_dependencies=False) -> None:
    """Retry the idempotent Route handoff after a completed CVP action resumes."""

    for unit in parent.get("units", {}).values():
        if unit.get("status") != "completed" or not unit.get("completed_step"):
            continue
        step = store.get_cvp_group(unit["completed_step"])
        package = step["reviewed"]["evidence"]["package"]["payload"]
        handoff_cvp_route_jobs(
            store,
            package,
            root / "route-queue-v2",
            Path(step["materials"]["step_root"]),
            update_viewpoint_ids=(route_affected_viewpoints(package) if track_dependencies else
                parent["request"]["input"].get("update",{}).get("viewpoint_ids",())),
        )


def cvp_update_completion(store, parent, root):
    """Report both stages without relabeling a CVP-only completion as finished."""
    if parent['result'].get('status')!='completed':
        return {'status':'cvp_pending','cvp_status':parent['result'].get('status')}
    states={};issues={};inactive={}
    for unit in parent['units'].values():
        step=store.get_cvp_group(unit['completed_step'])
        path=Path(step['materials']['step_root'])/'route-handoff.json'
        if not path.exists():
            issues[unit['completed_step']]='missing Route handoff'
            continue
        report=json.loads(path.read_text())
        if report.get('artifact_sha256')!=sha256_json({k:v for k,v in report.items() if k!='artifact_sha256'}):
            raise ValueError('Route handoff report changed')
        issues.update(report['issues']);inactive.update(report.get('inactive_viewpoints',{}))
        for job_id in report['job_ids']:
            path=Path(root)/'route-queue-v2'/'states'/(job_id+'.json')
            states[job_id]=json.loads(path.read_text())['status'] if path.exists() else 'missing'
    complete=not issues and all(state=='complete' for state in states.values())
    return {'status':'completed' if complete else 'arguments_pending', 'cvp_status':'completed',
            'argument_jobs':states, 'issues':issues, 'inactive_viewpoints':inactive}


def process_cvp_route(
    *, store: PostgresKnowledgeStore, viewpoint_id: str, output_dir: Path,
    proposer: Any, reviewer: Any, reconsiderer: Any,
    max_correction_rounds: int, apply: bool = False,
    round_extension: dict[str, Any] | None = None,
    ownership_guard: Callable[[], None] | None = None,
    repair_evidence_input: bool = False,
    repair_connection_context: bool = False,
    include_connection_judgments: bool = False,
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
    from backend.pipeline.viewpoint_route_connection_recovery import DIRECTORY, prepare_connection_continuation
    if repair_connection_context and repair_evidence_input:
        raise ValueError('Route continuations cannot be combined')
    execution_dir = output_dir / REPAIR_DIRECTORY if repair_evidence_input else output_dir
    if repair_connection_context:execution_dir = output_dir / DIRECTORY
    if repair_connection_context and (execution_dir/'route-change-plan.json').exists():
        from backend.pipeline.viewpoint_route_connection_recovery import verify_connection_reservation
        verify_connection_reservation(output_dir, execution_dir=execution_dir)
    recovered = resume_applied_cvp_route(store, viewpoint_id, execution_dir, policy_path=policy_path) if apply else None
    if recovered is not None:
        return recovered
    materials = prepare_cvp_route_materials(store, viewpoint_id, include_context=observations_enabled,
        include_connection_judgments=include_connection_judgments or repair_connection_context)
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
        program_bound_observation_retirements=observations_enabled and (
            not saved_packet_path.exists()
            or 'observation_retirement_binding' in json.loads(saved_packet_path.read_text())),
        existing_observation_supports=materials.get('existing_observation_supports'),
        unusable_context_claims=materials.get('unusable_context_claims'),
        approved_connection_judgments=materials.get('approved_connection_judgments'),
    )
    if packet["membership_ledger"]["out_of_scope_members"]:
        raise ValueError("complete CVP Route packet omitted an active member")
    guard()
    recovery_seed = None
    connection_seed = None
    if repair_evidence_input:
        recovery_seed = prepare_evidence_input_recovery(
            output_dir, packet, materials, proposer=proposer, reviewer=reviewer,
            max_correction_rounds=max_correction_rounds)
    if repair_connection_context:
        connection_seed = prepare_connection_continuation(output_dir, packet, materials,
            proposer=proposer, reviewer=reviewer, reconsiderer=reconsiderer,
            max_correction_rounds=max_correction_rounds)
    output_dir = execution_dir
    write_immutable(output_dir / "cvp-route-materials.json", materials)
    result = run_cvp_route_scope(
        route_packet=packet, existing_routes=materials["existing_routes"],
        output_dir=output_dir, proposer=proposer, reviewer=reviewer,
        reconsiderer=reconsiderer, max_correction_rounds=max_correction_rounds,
        recovery_seed=recovery_seed,
        connection_seed=connection_seed,
        **({'round_extension':round_extension} if round_extension else {}),
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
                     (row["revision"], row["content_sha256"], bool(row.get("retired")))
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
        **({"execution_root": str(output_dir)} if repair_evidence_input or repair_connection_context else {}),
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
    parser.add_argument('--repair-connection-context', action='store_true',
                        help='Continue the exact saved CVP exception with approved connection context')
    parser.add_argument('--round-extension', type=Path,
                        help='Owner authorization for one extra correction/review in this exact saved job')
    args = parser.parse_args()
    if args.repair_evidence_input and (args.action != "run" or not args.job_id or not args.retry_exceptions):
        parser.error("evidence input repair requires run, an exact job ID and --retry-exceptions")
    if args.repair_connection_context and (args.action != 'run' or not args.job_id
                                          or not args.retry_exceptions or args.repair_evidence_input):
        parser.error('connection continuation requires run, exact job ID, --retry-exceptions and no other repair')
    if args.round_extension and (args.action != 'run' or not args.job_id or not args.retry_exceptions
                                or args.repair_evidence_input or args.repair_connection_context):
        parser.error('round extension requires run, exact job ID, --retry-exceptions and no input repair')
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
            material = prepare_cvp_route_materials(store, viewpoint_id,
                include_context=policy["schema_version"] == "wang_route_resolution_policy_v8",
                include_connection_judgments=True)
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
        if args.repair_connection_context:
            from backend.pipeline.viewpoint_route_connection_recovery import DIRECTORY, verify_connection_reservation
            original = json.loads((active_output/'cvp-route-materials.json').read_text())
            if original['artifact_sha256'] != job['material_sha256']:
                raise ValueError('connection continuation original material differs from queued job')
            if (active_output/'connection-continuation-reservation.json').exists():
                verify_connection_reservation(active_output, original_material_sha256=job['material_sha256'])
            active_output = active_output / DIRECTORY
        recovered = resume_applied_cvp_route(store, job['viewpoint_id'], active_output,
            material_sha256=None if args.repair_connection_context else job['material_sha256'],
            policy_path=args.route_policy) if args.apply else None
        if recovered is not None:
            guard()
            queue.finish(job['job_id'], worker_id=args.worker_id, epoch=epoch,
                         status='complete', detail=recovered['status'])
            print(json.dumps(recovered, ensure_ascii=False))
            return 0
        current = prepare_cvp_route_materials(store, job["viewpoint_id"],
            include_context=policy["schema_version"] == "wang_route_resolution_policy_v8",
            include_connection_judgments=True)
        use_judgments = True
        if current['artifact_sha256'] != job['material_sha256'] and not args.repair_connection_context:
            legacy = prepare_cvp_route_materials(store, job['viewpoint_id'],
                include_context=policy['schema_version'] == 'wang_route_resolution_policy_v8')
            if legacy['artifact_sha256'] == job['material_sha256']:
                current, use_judgments = legacy, False
        if current["artifact_sha256"] != job["material_sha256"] and not args.repair_connection_context:
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
        from backend.pipeline.viewpoint_route_round_extension import round_extension
        extension = round_extension(active_output, job, policy, args.round_extension)
        result = process_cvp_route(
            store=store, viewpoint_id=job["viewpoint_id"],
            output_dir=args.output_dir / job["job_id"],
            proposer=OwnershipGuardedAdapter(adapters["proposal"], guard),
            reviewer=OwnershipGuardedAdapter(adapters["review"], guard),
            reconsiderer=OwnershipGuardedAdapter(adapters["correction"], guard),
            max_correction_rounds=(extension['effective_max_correction_rounds'] if extension
                                   else int(policy["max_correction_rounds"])),
            round_extension=extension,
            apply=args.apply, ownership_guard=guard,
            repair_evidence_input=args.repair_evidence_input, policy_path=args.route_policy,
            repair_connection_context=args.repair_connection_context,
            include_connection_judgments=use_judgments,
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
