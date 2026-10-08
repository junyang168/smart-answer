"""Sequential, fail-closed continuation over an owner-approved accepted schedule.

Each intact group is freshly qualified/frozen, resolved by the existing runner,
and independently audited before advancing. No route worker is launched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import os
from contextlib import nullcontext

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_source_attestation_runner import _write_immutable
from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
from backend.api.canonical_repository.viewpoint_production_safety import accepted_grouping_audit_scope

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT_ROOT / "backend/.venv/bin/python"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def binding(path):
    from backend.pipeline.viewpoint_backup_archive import logical_binding_path
    from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
    path = logical_binding_path(path)
    return {"path": str(path), "file_sha256": file_sha256(path)}


def validate_schedule(schedule, manifest):
    groups = {g["group_key"]: g for g in manifest["groups"]}
    rows = schedule["schedule"]
    keys = [r["group_key"] for r in rows]
    if schedule["upstream_manifest_sha256"] != manifest["artifact_sha256"]:
        raise ValueError("schedule upstream SHA mismatch")
    if len(keys) != len(set(keys)) or set(keys) != set(groups):
        raise ValueError("schedule must cover each original accepted group exactly once")
    units = {u["unit_id"]: u for u in manifest["units"]}
    for i, row in enumerate(rows, 1):
        g = groups[row["group_key"]]
        if row["sequence"] != i or row["claim_count"] != len(g["claim_ids"]) or not 1 <= len(g["claim_ids"]) <= 20:
            raise ValueError("schedule sequence/count/capacity mismatch")
        if row["passage_key"] != units[g["unit_id"]]["passage_key"]:
            raise ValueError("schedule passage mismatch")
        if row["state"] not in {"pending", "already_complete"}:
            raise ValueError("invalid schedule state")
    for edge in schedule["precedence_annotations"]:
        if keys.index(edge["prerequisite"]) >= keys.index(edge["dependent"]):
            raise ValueError("schedule violates approved precedence")
    ids = [cid for g in groups.values() for cid in g["claim_ids"]]
    if len(ids) != len(set(ids)) or set(ids) != {c["claim_id"] for c in manifest["claim_packets"]}:
        raise ValueError("accepted grouping does not have exact unique Claim coverage")
    return groups


def validate_audit(audit, claim_ids, viewpoint_ids, *, owner_resolution_path=None):
    from backend.pipeline.viewpoint_g035_temporary_audit import validate as validate_exception
    validate_exception(audit)
    if (audit.get("meta") or {}).get("model") != "gemini-3.8-flash":
        raise ValueError("unexpected independent audit model")
    resolved_by_layer = {'3': set(), '4': set()}
    if owner_resolution_path is not None:
        from backend.pipeline.viewpoint_audit_owner_resolution import validate_resolution, validate_gpt_resolution, GPT_SCHEMA
        if read(owner_resolution_path).get('schema_version') == 'wang_owner_component_role_resolution_v1':
            from backend.pipeline.viewpoint_component_role_resolution import verify
            resolved_by_layer = verify(audit, read(owner_resolution_path))
        elif read(owner_resolution_path).get('schema_version') == GPT_SCHEMA:
            resolved_by_layer = validate_gpt_resolution(audit, owner_resolution_path)
        elif read(owner_resolution_path).get('schema_version') == 'wang_exact_reused_gpt_source_resolution_v1':
            from backend.pipeline.viewpoint_reused_source_resolution import verify
            resolved_by_layer = verify(audit,read(owner_resolution_path))
        elif read(owner_resolution_path).get('schema_version') == 'wang_exact_composed_gpt_source_resolution_v2':
            from backend.pipeline.viewpoint_reused_source_resolution import verify_composed
            resolved_by_layer = verify_composed(audit,read(owner_resolution_path))
        else:
            resolved_by_layer['4'] = validate_resolution(audit, owner_resolution_path)
    for layer, expected, id_key in (("3", set(claim_ids), "claim_id"), ("4", set(viewpoint_ids), "viewpoint_id")):
        report = audit["layers"][layer]
        resolved = resolved_by_layer[layer]
        results = report["results"]
        if (not report.get("complete") or report.get("judged") != len(expected)
                or len(results) != len(expected) or {r[id_key] for r in results} != expected
                or any(report.get(k, 0) for k in ("model_errors", "blocked", "no_source_text"))
                or report.get('disputed', 0) != len(resolved)):
            raise ValueError(f"independent layer {layer} failed or incomplete")
        allowed = {"supported"} if layer == "3" else {"same"}
        if any(r.get("verdict") not in allowed and not
               (r.get('verdict') == 'disputed' and r[id_key] in resolved) for r in results):
            raise ValueError(f"independent layer {layer} has unresolved verdict")


def command(args):
    print("COMMAND", json.dumps([str(a) for a in args]), flush=True)
    from backend.pipeline.viewpoint_validation_context import runtime_active, runtime_metrics
    local_modules = {
        'backend.pipeline.viewpoint_reviewed_cvp_prepare',
        'backend.pipeline.viewpoint_batch_resolution_runner',
    }
    if runtime_active() and len(args) >= 3 and str(args[0]) == str(PYTHON) and args[1] == '-m' and args[2] in local_modules:
        from importlib import import_module
        result = import_module(args[2]).main([str(a) for a in args[3:]])
        if result not in (None, 0):
            raise subprocess.CalledProcessError(result, args)
        output_arg = '--output-root' if args[2].endswith('_prepare') else '--output-dir'
        output = Path(args[args.index(output_arg) + 1])
        save_state(output/'validation-metrics.json', {'phases': runtime_metrics(),
            'measurement_scope': 'mechanical validation only; not a model execution receipt'})
        return
    subprocess.run([str(a) for a in args], cwd=PROJECT_ROOT, check=True)


def save_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def pause_at_safe_boundary(root, state, *, group_complete):
    from backend.pipeline.viewpoint_campaign_control import stop_requested
    if not stop_requested(root):
        return False
    save_state(root / 'progress.json', state | {
        'status': 'paused_at_safe_boundary', 'stop_requested': True,
        'current_group_completed': group_complete, 'no_next_group_started': True,
        'no_inflight_model_or_write': True,
    })
    return True


def investigate_deferred(root):
    dispositions = list((root / "production/run/batches").glob("*/batch-disposition-applied.json"))
    if len(dispositions) != 1 or not any(c["disposition"] == "deferred"
            for item in read(dispositions[0])["claims"] for c in item["components"]):
        return None
    # Trace original applied inputs/reviews before a model or human escalation.
    # Separate diagnostic output preserves existing frozen model-cache identity.
    command([PYTHON, "-m", "backend.pipeline.viewpoint_evidence_hold_trace",
        "--packet", dispositions[0].parent / "batch-packet.json",
        "--disposition", dispositions[0], "--output-root", root / "evidence-hold-trace"])
    command([PYTHON, "-m", "backend.pipeline.viewpoint_deferred_source_check",
        "--packet", dispositions[0].parent / "batch-packet.json",
        "--disposition", dispositions[0], "--output-root", root / "deferred-source-check"])
    return read(root / "deferred-source-check/handoff.json")


def discover_bound_sealed_artifact(batch, name, role, expected_sha):
    """Two historical filename conventions; exact frozen bytes, no approval."""
    suffixes={'adjudication':('ai-adjudication','adjudication'),
              'overrides':('consensus-overrides','overrides')}
    if role not in suffixes or not isinstance(expected_sha,str) or len(expected_sha)!=64:
        raise ValueError('sealed artifact lacks exact frozen SHA')
    paths=[batch/('adjudications' if role=='adjudication' else 'overrides')/(name+'.'+s+'.json') for s in suffixes[role]]
    existing=[p for p in paths if p.is_file()]
    matching=[p for p in existing if binding(p)['file_sha256']==expected_sha]
    if len(matching)!=1 or any(binding(p)['file_sha256']!=expected_sha for p in existing):
        raise ValueError(f'{role}: missing, ambiguous or drifted sealed artifact')
    return matching[0]


def discover_selection(group, inventory, random_spot_check_policy=None):
    """Discover physical inputs; proof verification is performed by the adapter.

    A matching filename or inventory status never counts as review approval.
    Missing lineage and historical human holds stop the current group.
    """
    selected, sealed = [], []
    for cid in sorted(group["claim_ids"]):
        variants = inventory[cid]["variants"]
        # Discovery is not approval: the historical bridge authenticates every
        # applied coordinate / independently adjudicated evidence-index change.
        # Never discard that lineage merely because the index gained an ID.
        usable = [v for v in variants if set(v["changed_substantive_fields"])
                  <= {"occurrences", "evidence_step_ids"}]
        if len(usable) != 1:
            raise ValueError(f"{cid}: need unique current review lineage, found {len(usable)}")
        variant = usable[0]
        if variant['contract'] == 'review_input_discovery_requires_history_validation':
            row = {'claim_id': cid}
            for key in ('review_binding', 'review_input_binding'):
                if binding(variant[key]['path']) != variant[key]:
                    raise ValueError(f'{cid}: discovered review/input SHA drift')
                row[key] = dict(variant[key])
            if random_spot_check_policy is not None:
                row['random_spot_check_policy_binding'] = binding(random_spot_check_policy)
            selected.append(row)
            continue
        candidate = Path(variant["path"])
        if binding(candidate)["file_sha256"] != variant["file_sha256"]:
            raise ValueError(f"{cid}: inventory candidate file SHA drift")
        name = candidate.name.removesuffix(".reviewed-candidate.json")
        batch = candidate.parent.parent
        review_path = batch / "reviews" / (name + ".independent-review.json")
        review = read(review_path)
        from backend.pipeline.viewpoint_lineage_inventory import bound_review_input
        input_path = bound_review_input(review)
        if input_path is None:
            raise ValueError(f'{cid}: missing or ambiguous SHA-bound historical review input')
        row = {"claim_id": cid, "review_binding": binding(review_path), "review_input_binding": binding(input_path)}
        if variant['contract'] == 'requires_historical_proof_adapter':
            row['legacy_reviewed_candidate_binding'] = binding(candidate)
        if random_spot_check_policy is not None:
            row["random_spot_check_policy_binding"] = binding(random_spot_check_policy)
        if variant["contract"] == "sealed_valid":
            application=read(candidate)['consensus_application']
            if binding(review_path)['file_sha256']!=application['review_artifact_sha256']:
                raise ValueError(f'{cid}: sealed independent review SHA drift')
            paths = {"reviewed_candidate": candidate, "independent_review": review_path,
                     **{role:discover_bound_sealed_artifact(batch,name,role,application.get(role+'_artifact_sha256'))
                        for role in ('adjudication','overrides')}}
            sealed.append({"claim_id": cid, **{k + "_path": str(p.resolve()) for k, p in paths.items()},
                           **{k + "_sha256": binding(p)["file_sha256"] for k, p in paths.items()}})
        selected.append(row)
    return selected, sealed


def lineage_preflight(schedule, groups, inventory, random_spot_check_policy=None):
    """Check every pending Claim now, without models, writes or approval.

    Uses the very same discovery path as production. Historical bridge replay
    and current Registry qualification still run at each serialized entrance.
    This report is diagnostic, never an eligibility attestation.
    """
    results = []
    for row in schedule['schedule']:
        if row['state'] == 'already_complete':
            continue
        for cid in sorted(groups[row['group_key']]['claim_ids']):
            item = {'sequence': row['sequence'], 'group_key': row['group_key'], 'claim_id': cid}
            try:
                selected, sealed = discover_selection({'claim_ids': [cid]}, inventory,
                                                      random_spot_check_policy)
                item.update(status='discovered', selection=selected[0], sealed=sealed)
            except (ValueError, KeyError, OSError, TypeError) as exc:
                item.update(status='blocked', error=str(exc))
            results.append(item)
    body = {'schema_version': 'cvp-lineage-discovery-preflight.v1',
            'eligibility_approved': False, 'registry_writes': 0, 'model_calls': 0,
            'claims_checked': len(results),
            'blocked_claims': sum(r['status'] == 'blocked' for r in results),
            'results': results}
    return body | {'artifact_sha256': sha256_json(body)}


def build_selection(group, inventory, root, random_spot_check_policy=None, source_repair_paths=()):
    fresh = []
    for path in source_repair_paths:
        package=read(path)
        version = package.get('source_repair_application',{}).get('schema_version')
        if version not in {'wang_g041_owner_qualified_transcript_reconciliation_v2', 'wang_s201129_owner_opus_source_reconciliation_v1', 'wang_owner_claim_statement_revision_v1', 'wang_source_reconciliation_v1'}:
            continue
        from backend.pipeline.viewpoint_applied_source_epochs import implementation
        epoch = implementation(binding(path))
        from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
        epoch.verified_applied(binding(path),PostgresKnowledgeStore())
        fresh.extend(epoch.selection_row(cid,package) for cid in sorted(set(group['claim_ids']) & epoch.reviewed_claim_ids(package)))
    if len(fresh)!=len({r['claim_id'] for r in fresh}):
        raise ValueError('duplicate fresh source epoch qualification')
    group={**group,'claim_ids':sorted(set(group['claim_ids'])-{r['claim_id'] for r in fresh})}
    missing = {cid for cid in group['claim_ids'] if len([
        v for v in inventory[cid]['variants'] if set(v['changed_substantive_fields'])
        <= {'occurrences', 'evidence_step_ids'}]) != 1}
    for cid in set(group['claim_ids']) - missing:
        usable = [v for v in inventory[cid]['variants'] if set(v['changed_substantive_fields'])
                  <= {'occurrences', 'evidence_step_ids'}]
        variant = usable[0]
        if variant['contract'] != 'requires_historical_proof_adapter':
            continue
        candidate = Path(variant['path'])
        if binding(candidate)['file_sha256'] != variant['file_sha256']:
            raise ValueError(f'{cid}: inventory candidate file SHA drift')
        name = candidate.name.removesuffix('.reviewed-candidate.json')
        review = read(candidate.parent.parent/'reviews'/(name+'.independent-review.json'))
        from backend.pipeline.viewpoint_lineage_inventory import bound_review_input
        path = bound_review_input(review)
        if path is None:
            missing.add(cid)
    if missing:
        from dotenv import load_dotenv
        from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
        from backend.pipeline.viewpoint_lineage_inventory import discover_review_inputs
        load_dotenv(PROJECT_ROOT / '.env')
        inventory = discover_review_inputs(claim_ids=missing, inventory=inventory,
                                          store=PostgresKnowledgeStore())
    selected, sealed = discover_selection(group, inventory, random_spot_check_policy)
    if sealed:
        path = root / "sealed-lineage.json"
        body = {"claims": sealed}
        _write_immutable(path, body | {"artifact_sha256": sha256_json(body)})
        for row in selected:
            if row["claim_id"] in {s["claim_id"] for s in sealed}:
                row["sealed_lineage_binding"] = binding(path)
    _write_immutable(root / "review-selection.json", {"claims": sorted(selected+fresh,key=lambda r:r['claim_id'])})


def campaign_policy(args):
    # No implicit historical policy upgrade. New continuation roots select and
    # physically pin the calibrated policy and shared rules explicitly.
    legacy=PROJECT_ROOT/'backend/pipeline/policies/wang_cvp_resolution_policy_v3.json'
    path=getattr(args,'cvp_policy',None)
    if path is None:
        if getattr(args,'cvp_policy_sha256',None) or getattr(args,'semantic_contract_sha256',None):
            raise ValueError('semantic SHAs without explicit policy cannot select legacy')
        return str(legacy.relative_to(PROJECT_ROOT)), {}
    contextual=PROJECT_ROOT/'backend/pipeline/policies/wang_cvp_resolution_policy_v5.json'
    coverage=PROJECT_ROOT/'backend/pipeline/policies/wang_cvp_resolution_policy_v6.json'
    if Path(path).resolve() not in {contextual.resolve(), coverage.resolve()}:
        raise ValueError('explicit campaign policy must be the authorized contextual v5 or coverage v6')
    contextual=Path(path).resolve()
    from backend.pipeline.viewpoint_semantic_contract import CONTRACT_PATH,CONTEXTUAL_SEMANTICS
    policy=binding(contextual);rules=binding(CONTRACT_PATH)
    if (policy['file_sha256']!=getattr(args,'cvp_policy_sha256',None)
            or rules['file_sha256']!=getattr(args,'semantic_contract_sha256',None)
            or read(contextual).get('semantic_contract_version')!=CONTEXTUAL_SEMANTICS):
        raise ValueError('campaign semantic policy/rules physical SHA drift')
    return str(contextual), {'cvp_policy':policy,'semantic_contract':rules}


def runner_args(root, args):
    policy,_=campaign_policy(args)
    inputs = root / "production/inputs"
    return [PYTHON, "-m", "backend.pipeline.viewpoint_batch_resolution_runner",
            "--packet", inputs / "scope-packet.json", "--freeze", inputs / "freeze.json",
            "--output-dir", root / "production/run", "--apply-authorization", inputs / "apply-authorization.json",
            "--accepted-grouping-sha", args.accepted_grouping_sha, "--grouping-repository", args.grouping_repository,
            "--cvp-policy", policy,
            "--route-policy", "backend/pipeline/policies/wang_route_resolution_policy_v1.json",
            "--proposal-provider", "codex", "--proposal-model", "gpt-6.1-sol", "--proposal-effort", "high",
            "--review-provider", "claude", "--review-model", "claude-opus-5-5", "--review-effort", "high",
            "--consolidation-provider", "claude", "--consolidation-model", "claude-opus-5-5", "--consolidation-effort", "high",
            "--group-model", "claude-opus-5-5", "--group-effort", "high", "--max-batches", "1"]


def qualify(row, group, inventory, root, args):
    policy,_=campaign_policy(args)
    inputs = root / "production/inputs"
    if (inputs / "freeze.json").exists():
        # Existing immutable phase is revalidated by the normal runner.
        return
    build_selection(group, inventory, root, getattr(args, 'random_spot_check_policy', None),
                    getattr(args,'completed_source_repair',None) or [])
    bridge=root/'historical-bridges.json'
    if bridge.exists():
        # A bridge is exchange input, never a cached current approval. The
        # prepare/freeze validators independently reconstruct its lineage
        # against current Claim/Evidence/source and hold status.
        from backend.api.canonical_repository.viewpoint_production_safety import validate_content_addressed
        cached=read(bridge)
        validate_content_addressed(cached,'artifact_sha256')
        if (cached.get('status')!='verified'
                or cached.get('selection_file_sha256')!=binding(root/'review-selection.json')['file_sha256']
                or sorted(p['claim_id'] for p in cached['proofs'])!=sorted(group['claim_ids'])):
            raise ValueError('historical bridge exchange input identity drift')
    else:
        command([PYTHON, "-m", "backend.pipeline.viewpoint_historical_review_bridge", "--selection", root / "review-selection.json", "--output", bridge])
    b=ensure_campaign_backup(args)
    prepare_command=[PYTHON, "-m", "backend.pipeline.viewpoint_reviewed_cvp_prepare",
             "--group-key", group["group_key"], "--grouping-repository", args.grouping_repository,
             "--accepted-grouping-sha", args.accepted_grouping_sha, "--historical-bridges", root / "historical-bridges.json",
             "--independent-audit", args.entrance_audit, "--output-root", root / "production",
             "--wang-repository", args.wang_repository,
             "--cvp-policy", policy,
             "--route-policy", "backend/pipeline/policies/wang_route_resolution_policy_v1.json",
             "--backup-dump", b["path"], "--backup-sha256", b["file_sha256"],
             "--global-lock", args.global_lock, "--scope-authorized-by", args.authorized_by,
             "--created-at", datetime.now(timezone.utc).isoformat()]
    for path in getattr(args,'completed_source_repair',None) or []:
        prepare_command.extend(['--completed-source-repair',path])
    if getattr(args,'completed_source_repair',None):
        prepare_command.extend(['--parent-completed-proofs',args.completed_proofs])
    command(prepare_command)


def ensure_campaign_backup(args):
    # One verified full snapshot before this serialized campaign, plus the
    # normal per-group compensatable ChangeSets/readback history. Repeating a
    # full 129MB snapshot 154 times would exhaust the local machine's disk.
    backup = args.output_root / "backup.json"
    if not backup.exists():
        label = campaign_backup_label(args.output_root)
        result = subprocess.run(["scripts/backup-registry.sh", label], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True)
        print(result.stdout, flush=True)
        match = re.search(r"backup-registry: wrote (.+\.dump) \(", result.stdout)
        if not match:
            raise ValueError("backup path not reported")
        dump = Path(match[1])
        subprocess.run(["pg_restore", "--list", str(dump)], check=True, capture_output=True)
        _write_immutable(backup, binding(dump))
    b = read(backup)
    if binding(b["path"]) != b:
        raise ValueError("campaign baseline backup SHA drift")
    return b


def campaign_backup_label(root):
    """Fit backup-registry's 61-character contract without truncated identity."""
    import hashlib
    return 'before-cvp-357-' + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:32]


def initialize_validation(args):
    """Authenticate immutable history once; no model or Registry mutation.

    No whole-collection prewarm here: it was redundant with group entry and
    loaded unrelated payloads. Actual qualification/write/current guards remain
    responsible for their required readsets; this is NOT a release receipt.
    """
    from backend.pipeline.viewpoint_validation_context import ValidationContext
    from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization
    from backend.pipeline.viewpoint_applied_source_epochs import compose,has_applied_source_frame,consume_completed_source_frame
    from backend.pipeline.viewpoint_grouping_repository import SharedGroupingRepository
    start = time.perf_counter()
    paths = getattr(args, 'completed_source_repair', None) or []
    with ValidationContext('campaign startup authentication'):
        for path in paths:
            package=read(path)
            if (package.get('source_repair_application') or {}).get('schema_version')=='wang_source_reconciliation_v1':
                from backend.pipeline.source_reconciliation_epoch import load_completed
                load_completed(binding(path))
            elif has_applied_source_frame(path,package):
                from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
                consume_completed_source_frame(binding(path),PostgresKnowledgeStore())
            else:
                validate_store_package_authorization(package)
        SharedGroupingRepository(args.grouping_repository).load(
            args.accepted_grouping_sha, source_epoch_binding=compose(paths))
    return {'startup_seconds': time.perf_counter()-start,
            'scope': 'history/grouping authentication; no whole-collection SQL prewarm',
            'model_calls': 0, 'Registry_writes': 0}


def validate_completed_seed(seed, groups, completed_groups, *, binding_verifier=None):
    """One startup's exact completed inputs; no cross-startup approval cache.

    Preserve every original group and semantic gate. Repeated references to
    identical immutable bytes do not require rereading those bytes per group;
    conflicting pins stop before IO. This is NOT a new completion proof.
    """
    rows=seed['groups']
    if (len(rows)!=len({row['group_key'] for row in rows})
            or {row['group_key'] for row in rows}!=set(completed_groups)):
        raise ValueError('completed-group proofs differ from schedule')
    pins={};references=0
    for row in rows:
        for pin in row['bindings']:
            references+=1
            if pin['path'] in pins and pins[pin['path']]!=pin:
                raise ValueError('conflicting completed-group evidence SHA')
            pins[pin['path']]=pin
    for path,pin in pins.items():
        if (binding_verifier or binding)(path)!=pin:
            raise ValueError('completed-group evidence SHA drift')
    from backend.pipeline.viewpoint_g035_temporary_audit import validate_completion_disclosure
    documents={}
    def audit_at(path):
        # Isolate the per-group validators from the parse cache: a validator
        # must not mutate another group's original audit representation.
        from copy import deepcopy
        if path not in documents:documents[path]=read(path)
        return deepcopy(documents[path])
    for row in rows:
        audited=audit_at(row['audit_path'])
        validate_completion_disclosure(row,audited)
        claim_audit=audit_at(row.get('claim_audit_path',row['audit_path']))
        validate_audit({'meta':audited['meta'],'layers':{
            '3':claim_audit['layers']['3'],'4':audited['layers']['4']}},
            groups[row['group_key']]['claim_ids'],row['viewpoint_ids'],
            owner_resolution_path=row.get('owner_resolution_path'))
    return {'groups':len(rows),'binding_references':references,
            'unique_physical_bindings':len(pins),'unique_audit_documents':len(documents)}


def execute(args):
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    if binding(args.schedule)["file_sha256"] != args.schedule_sha256:
        raise ValueError("approved schedule file SHA mismatch")
    schedule = read(args.schedule)
    manifest = read(schedule["upstream_manifest_path"])
    groups = validate_schedule(schedule, manifest)
    if manifest["artifact_sha256"] != args.accepted_grouping_sha:
        raise ValueError("campaign grouping differs")
    inventory = {r["claim_id"]: r for r in read(args.lineage_inventory)["claims"]}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    config = {"schedule": binding(args.schedule), "lineage_inventory": binding(args.lineage_inventory),
              "entrance_audit": binding(args.entrance_audit), "completed_proofs": binding(args.completed_proofs),
              "grouping_repository": str(args.grouping_repository.resolve()), "wang_repository": str(args.wang_repository.resolve()),
              "global_lock": str(args.global_lock.resolve()), "runner_commit": commit, "authorized_by": args.authorized_by,
              "completed_source_repairs": [binding(p) for p in getattr(args,'completed_source_repair',None) or []],
              "random_spot_check_policy": binding(args.random_spot_check_policy) if getattr(args,'random_spot_check_policy',None) else None,
              "route_execution": False, "already_complete": schedule["completed_groups"]}
    if getattr(args,'completed_source_predecessor',None):
        config['completed_source_predecessors']=[binding(p) for p in args.completed_source_predecessor]
    config.update(campaign_policy(args)[1])
    if getattr(args, 'validation_runtime', False):
        config['validation_runtime'] = 'bounded_authenticated_history_v1'
        from backend.pipeline.viewpoint_validation_context import ROUTINE_VALIDATION_LIMITS
        config['mechanical_validation_limits'] = {
            'file_io_bytes': ROUTINE_VALIDATION_LIMITS.file_io_bytes,
            'wall_seconds': ROUTINE_VALIDATION_LIMITS.wall_seconds,
            'automatic_full_rebuild_fallback': False,
            'scalability_release_accepted': False}
    if getattr(args,'stop_after_sequence',None) is not None:
        if not 1<=args.stop_after_sequence<=len(schedule['schedule']):
            raise ValueError('invalid validation checkpoint sequence')
        config['validation_checkpoint_sequence']=args.stop_after_sequence
    _write_immutable(args.output_root / "campaign.json", config)
    seed = read(args.completed_proofs)
    validate_completed_seed(seed,groups,schedule['completed_groups'])
    preflight = lineage_preflight(schedule, groups, inventory,
                                 getattr(args, 'random_spot_check_policy', None))
    _write_immutable(args.output_root / 'lineage-discovery-preflight.json', preflight)
    if getattr(args, 'validation_runtime', False):
        ensure_campaign_backup(args)  # one campaign startup snapshot, not per-group entry work
        _write_immutable(args.output_root / 'validation-startups' / f'{os.getpid()}-{time.time_ns()}.json', initialize_validation(args))
    done = []
    pending_count=sum(row['state']=='pending' for row in schedule['schedule'])
    for row in schedule["schedule"]:
        if row["state"] == "already_complete":
            continue
        root = args.output_root / f"g{row['sequence']:03d}"
        completion = root / "completion.json"
        if completion.exists():
            proof = read(completion)
            if proof["group_key"] != row["group_key"] or any(binding(b["path"]) != b for b in proof["bindings"]):
                raise ValueError("completed campaign group proof drift")
            validate_audit(read(proof["audit_path"]), groups[row["group_key"]]["claim_ids"], proof["viewpoint_ids"],
                           owner_resolution_path=proof.get('owner_resolution_path'))
            done.append(row["group_key"])
            continue
        state = {"status": "qualifying", "group_key": row["group_key"], "sequence": row["sequence"], "completed_new_groups": len(done), "remaining_groups": pending_count-len(done)}
        if pause_at_safe_boundary(args.output_root, state, group_complete=False):
            return 0
        save_state(args.output_root / "progress.json", state)
        try:
            if shutil.disk_usage(args.output_root).free < 2 * 1024**3:
                raise ValueError("less than 2 GiB free disk; stop safely before another group")
            from backend.pipeline.viewpoint_validation_context import ValidationContext
            phase = ValidationContext('group entry') if getattr(args, 'validation_runtime', False) else nullcontext()
            started = time.perf_counter()
            with phase:
                qualify(row, groups[row["group_key"]], inventory, root, args)
                command(runner_args(root, args) + ["--dry-run"])
            save_state(root/'entry-validation.json', {'wall_seconds': time.perf_counter()-started,
                'model_calls': 0, 'Registry_writes': 0, 'scope': 'qualification plus dry-run, including boundary drift checks'})
            if pause_at_safe_boundary(args.output_root, state, group_complete=False):
                return 0
            if args.dry_run:
                save_state(args.output_root / "progress.json", state | {"status": "preflight_passed_no_model_calls"})
                return 0
            gate = getattr(args, '_startup_gate', None)
            if gate is not None and not gate.released:
                waiting = state | {'status':'preflight_passed_no_model_calls'}
                save_state(args.output_root/'progress.json',waiting)
                pins = [binding(args.output_root/'campaign.json'), binding(args.output_root/'backup.json'),
                        binding(root/'entry-validation.json')]
                pins += [binding(p) for p in sorted(inputs_path for inputs_path
                         in (root/'production/inputs').iterdir() if inputs_path.is_file())]
                gate.ready(waiting,pins,read(args.output_root/'backup.json'))
                from backend.pipeline.viewpoint_campaign_control import stop_requested
                gate.wait_release(lambda:stop_requested(args.output_root),os.environ['CODEX_EXECUTABLE'])
                # Same runtime, next phase. No current verdict is carried over:
                # the normal apply runner freshly verifies source/SQL/holds.
            save_state(args.output_root / "progress.json", state | {"status": "generating_reviewing"})
            command(runner_args(root, args) + ["--apply"])
            run = read(root / "production/run/scope-run.json")
            if run["disposition_status"] != "complete":
                save_state(args.output_root / "progress.json", state | {"status": "investigating_deferred_source"})
                handoff = investigate_deferred(root)
                if handoff is not None:
                    raise ValueError("source investigation completed; required actions: " +
                                     ", ".join(sorted({a["next_action"] for a in handoff["actions"]})))
                raise ValueError("scope not completely applied/readback verified")
            plans = list((root / "production/run/batches").glob("*/cvp-change-plan.json"))
            if len(plans) != 1:
                raise ValueError("expected exactly one intact group plan")
            plan = read(plans[0])
            from backend.pipeline.viewpoint_group_audit_population import group_audit_targets
            target_ids = group_audit_targets(plans[0])
            if not target_ids:
                raise ValueError("no viewpoint audit targets; needs explicit disposition review")
            audit_root = root / "independent-audit"
            save_state(args.output_root / "progress.json", state | {"status": "independent_audit", "viewpoint_ids": target_ids})
            audit_path = audit_root / "audit.json"
            if not audit_path.exists():
                command([PYTHON, "scripts/audit-library.py", "--scope", "all", "--layers", "1,2,3,4", "--claim-ids", ",".join(groups[row["group_key"]]["claim_ids"]), "--viewpoint-ids", ",".join(target_ids), "--workers", "2", "--model", "gemini-3.8-flash", "--out", audit_root, "--no-status-file", "--preserve-full-context"])
            from backend.pipeline.viewpoint_content_blocked_fallback import apply as apply_content_blocked_fallback
            audit_path = apply_content_blocked_fallback(audit_path, root/'content-blocked-fallback')
            audit = read(audit_path)
            resolution_bindings = []
            source_resolution_path = None
            from backend.pipeline.viewpoint_audit_source_adjudication import disputes, validate_population
            if disputes(audit):
                validate_population(audit, groups[row['group_key']]['claim_ids'], target_ids)
                command([PYTHON, '-m', 'backend.pipeline.viewpoint_audit_source_adjudication',
                         '--audit', audit_path, '--output-root', root / 'audit-source-adjudication', '--final-gpt'])
                handoff_path = root / 'audit-source-adjudication/gpt-final-handoff.json'
                handoff = read(handoff_path)
                if not handoff['actions'] or any(a['verdict'] != 'audit_objection_rejected' for a in handoff['actions']):
                    from backend.pipeline.viewpoint_audit_link_repair import launch_repair
                    repair_root = launch_repair(args.output_root, read(root/'audit-source-adjudication/packet.json'), handoff)
                    save_state(args.output_root/'progress.json', state | {'status':'controlled_content_repair',
                        'repair_root':str(repair_root), 'no_next_group_started':True})
                    return 0  # Parent exits; the locked repair worker owns continuation.
                from backend.pipeline.viewpoint_audit_owner_resolution import create_gpt_resolution
                from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
                source_resolution_path = root / 'gpt-source-final-resolution.json'
                resolution = create_gpt_resolution(audit_path, root / 'audit-source-adjudication',
                                                   source_resolution_path, PostgresKnowledgeStore())
                resolution_bindings = [binding(source_resolution_path), *resolution['bindings']]
                _write_immutable(root / 'audit-resolution-bindings.json', {'bindings': resolution_bindings})
            validate_audit(audit, groups[row["group_key"]]["claim_ids"], target_ids,
                           owner_resolution_path=source_resolution_path)
            mechanical = {"meta": audit["meta"], "layers": {key: audit["layers"][key] for key in ("1", "2")}}
            disposition = scope_audit_disposition(audit=mechanical,
                manifest=accepted_grouping_audit_scope(manifest, "group:" + row["group_key"]),
                audit_file_sha256=binding(audit_path)["file_sha256"], authorized_by=args.authorized_by)
            _write_immutable(root / "post-apply-audit-disposition.json", disposition)
            if disposition["status"] != "passed":
                raise ValueError("post-apply mechanical audit has in-scope/global Registry findings")
            proof = {"group_key": row["group_key"], "viewpoint_ids": target_ids, "audit_path": str(audit_path), "bindings": [binding(audit_path), binding(plans[0]), binding(plans[0].parent/'cvp-change-package.json'), binding(plans[0].parent/'cvp-readback-receipt.json'), binding(root / "production/run/scope-run.json"), *resolution_bindings], "argument_routes_completed": False}
            from backend.pipeline.viewpoint_g035_temporary_audit import validate as validate_exception, KEY as legacy_key
            from backend.pipeline.viewpoint_content_blocked_fallback import KEY as fallback_key
            exception_bindings = validate_exception(audit)
            if exception_bindings:
                proof.update(revisit_required=True, independent_audit_complete=False,
                    temporary_audit_exception_path=(audit['meta'].get(fallback_key) or audit['meta'][legacy_key])['path'])
                proof['bindings'] += exception_bindings
            if source_resolution_path is not None:
                # Compatibility key consumed by all completion/seed/prepare paths;
                # the artifact distinguishes standing GPT policy from owner case acceptance.
                proof['owner_resolution_path'] = str(source_resolution_path)
            _write_immutable(completion, proof)
            done.append(row["group_key"])
            save_state(args.output_root / "progress.json", state | {"status": "group_complete", "completed_new_groups": len(done), "remaining_groups": pending_count-len(done)})
            if pause_at_safe_boundary(args.output_root, state | {
                    'completed_new_groups': len(done), 'remaining_groups': pending_count-len(done)},
                    group_complete=True):
                return 0
            if row['sequence']==getattr(args,'stop_after_sequence',None):
                save_state(args.output_root/'progress.json',state|{'status':'owner_validation_checkpoint',
                    'completed_new_groups':len(done),'remaining_groups':pending_count-len(done),
                    'no_next_group_started':True,'checkpoint_group_completed':True})
                return 0
        except Exception as exc:
            save_state(args.output_root / "progress.json", state | {"status": "blocked", "error": str(exc), "no_next_group_started": True})
            raise
    save_state(args.output_root / "progress.json", {"status": "complete", "completed_new_groups": len(done), "remaining_groups": 0, "argument_routes_completed": False})
    if getattr(args, '_startup_gate', None) is not None and not args._startup_gate.released:
        args._startup_gate.ready(read(args.output_root/'progress.json'),
                                 [binding(args.output_root/'campaign.json')])
    return 0


def main():
    from backend.pipeline.cvp_sql_flow import main as sql_main
    return sql_main()


if __name__ == "__main__":
    sys.exit(main())
