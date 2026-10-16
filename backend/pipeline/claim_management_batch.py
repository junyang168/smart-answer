"""Run bound Claim requests durably; reuse the existing action/root/call budgets.

Launch with --detach (screen), inspect with --status, resume the SAME manifest.
No autonomous retry, model-driven repair or new content authority is created.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from backend.pipeline.source_reconciliation_runner import binding, body, read, write
from backend.pipeline.cvp_sql_flow import check_binding

MODULE = 'backend.pipeline.claim_management_batch'


def progress(path, value):
    path = Path(path)
    temp = path.with_suffix('.partial')
    with temp.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def selection(pending, active, footprints, capacity):
    """Recompute pending scope after each exit, never wait on a wave barrier."""
    occupied = [footprints[k] for k in active]
    selected = []
    for key in pending:
        reads, writes = footprints[key]
        if len(active) + len(selected) >= capacity:
            break
        if any(writes & r or w & reads for r, w in occupied):
            continue
        selected.append(key)
        occupied.append((reads, writes))
    return selected


def load(manifest, root):
    from backend.pipeline import claim_management as cm
    plan = json.loads(Path(manifest).read_text())
    if set(plan) != {'jobs', 'max_workers'} or plan['max_workers'] not in (1, 2) or not plan['jobs']:
        raise ValueError('batch requires explicit jobs and max_workers 1 or 2')
    jobs = {}
    roots, targets = set(), set()
    for job in plan['jobs']:
        if set(job) != {'request', 'root'}:
            raise ValueError('batch job must bind an existing request and action root')
        check_binding(job['request'])
        request = cm.Request.model_validate_json(Path(job['request']['path']).read_text())
        action_root = Path(job['root'])
        if not action_root.is_absolute() or request.action_id in jobs or str(action_root.resolve()) in roots:
            raise ValueError('duplicate action/root or relative execution root')
        if request.operation != 'Update' or len(request.target_keys) > 2 or targets & set(request.target_keys):
            raise ValueError('batch requires disjoint Update targets, maximum two per action')
        # Authenticate original authorization/policy without changing either.
        cm.load_authority(request)
        jobs[request.action_id] = (request, action_root.resolve())
        roots.add(str(action_root.resolve())); targets.update(request.target_keys)
    write(Path(root) / 'plan.json', {'manifest': binding(manifest), 'jobs': plan['jobs'],
                                    'max_workers': plan['max_workers']})
    return plan, jobs


def worker(manifest, root, key):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_actions import execute_claim_management
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    from backend.pipeline import claim_management as cm
    from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
    descriptors = [int(v) for v in os.environ.get('WKP_CLAIM_BATCH_LOCK_FDS', '').split(',') if v]
    if len(descriptors) != 3:
        raise ValueError('worker requires inherited coordinator locks')
    data = Path(os.environ['DATA_BASE_DIR'])
    paths = [Path(root) / 'controller.lock',
        Path(os.environ.get('WANG_CVP_RECOVERY_ROOT', str(data / 'wang-knowledge-platform/staging/cvp-production-357/immediate-recovery-20261002-v1'))) / 'recovery.lock',
        data / 'wang-knowledge-platform/staging/cvp-production-357/registry-cvp.lock']
    for fd, path in zip(descriptors, paths):
        info = os.fstat(fd)
        # A separately opened descriptor must be unable to acquire this lock.
        with path.open('a') as probe:
            if (os.fstat(probe.fileno()).st_dev, os.fstat(probe.fileno()).st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('inherited lock identity drift')
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            raise ValueError('worker inherited an unlocked descriptor')
    _, jobs = load(manifest, root)
    request, action_root = jobs[key]
    store = PostgresKnowledgeStore()
    ports = ProductionPorts(store)
    ports._environment_guard(require_backup=False)
    runtime = canonical_runtime()
    if not runtime['reused'] or runtime['new_model_calls']:
        raise ValueError('batch requires the existing authorized runtime proof')
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(key))
    uncertain = []
    for slot in (parent or {}).get('call_reservations', {}).values():
        if slot['state'] != 'started':
            continue
        stage = action_root / slot['round_id']
        if not (stage / (slot['role'] + '.raw.json')).exists() and not (stage / (slot['role'] + '.transport/exit.json')).exists():
            uncertain.append(slot['round_id'] + ':' + slot['role'])
    result = ({'status': 'paused', 'reason': 'call_uncertain', 'slots': uncertain} if uncertain else
              execute_claim_management(store, request, root=action_root, ports=ports))
    return {'action_id': key, 'result': result,
            'qualifications': {k: store.claim_management_qualification(k.split('/', 1)[1]) for k in request.target_keys}}


def run(manifest, root):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    from backend.pipeline.source_reconciliation_runtime import maintenance_lock
    from backend.pipeline import claim_management as cm
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        lock = stack.enter_context((root / 'controller.lock').open('a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        maintenance = stack.enter_context(maintenance_lock())
        registry = stack.enter_context(exclusive_cvp_run_lock({'global_lock_path': str(Path(os.environ['DATA_BASE_DIR']) /
            'wang-knowledge-platform/staging/cvp-production-357/registry-cvp.lock')}))
        plan, jobs = load(manifest, root)
        store = PostgresKnowledgeStore()
        pending, active, results = list(jobs), {}, {}
        (root / 'result.json').unlink(missing_ok=True)
        log = stack.enter_context((root / 'events.jsonl').open('a', buffering=1))
        def event(**value):
            log.write(json.dumps(value | {'time': datetime.now(timezone.utc).isoformat()}) + '\n')
            log.flush(); os.fsync(log.fileno())
        try:
            while pending or active:
                scopes = {}
                for key in [*pending, *active]:
                    request, _ = jobs[key]
                    records = store.read_claim_context([cm.source.split_key(k) for k in request.target_keys])
                    scopes[key] = ({cm.source.key(*k) for k in records}, {p.key for p in request.permissions})
                for key in selection(pending, active, scopes, plan['max_workers']):
                    token = cm.sha256_json(key)
                    output = root / (token + '.result.json')
                    output.unlink(missing_ok=True)  # derived worker receipt, never model/action artifacts
                    out = (root / (token + '.stdout.log')).open('a')
                    err = (root / (token + '.stderr.log')).open('a')
                    command = [sys.executable, '-m', MODULE, '--manifest', str(manifest), '--root', str(root), '--job', key]
                    environment = dict(os.environ, WKP_CLAIM_BATCH_LOCK_FDS=','.join(str(h.fileno()) for h in (lock, maintenance, registry)))
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=environment,
                        pass_fds=(lock.fileno(), maintenance.fileno(), registry.fileno()))
                    active[key] = (process, out, err, output)
                    pending.remove(key); event(stage='job_started', action_id=key, pid=process.pid)
                for key, (process, out, err, output) in list(active.items()):
                    code = process.poll()
                    if code is None:
                        continue
                    out.close(); err.close()
                    results[key] = (json.loads(output.read_text()) if output.exists() and code == 0 else
                        {'action_id': key, 'result': {'status': 'paused', 'reason': 'worker_exited', 'returncode': code}})
                    event(stage='job_finished', action_id=key, result=results[key]['result'])
                    del active[key]
                progress(root / 'status.json', {'controller_pid': os.getpid(), 'active': {k: p[0].pid for k,p in active.items()},
                    'pending': pending, 'results': results, 'heartbeat': datetime.now(timezone.utc).isoformat()})
                if active:
                    time.sleep(1)
            current = {key: {k: store.claim_management_qualification(k.split('/', 1)[1])
                              for k in request.target_keys} for key, (request, _) in jobs.items()}
            progress(root / 'result.json', {'results': results, 'current_qualifications': current,
                'all_actions_completed': all(v['result'].get('status') == 'completed' for v in results.values())
                    and all(q and q.get('ready') for values in current.values() for q in values.values())})
        except BaseException as exc:
            event(stage='controller_failed', error=repr(exc))
            # Inherited locks survive even SIGKILL until the workers exit.
            raise


def status(root):
    root = Path(root)
    with (root / 'controller.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            live = False
        except BlockingIOError:
            live = True  # coordinator OR an inherited worker holds the lock
    saved = json.loads((root / 'status.json').read_text()) if (root / 'status.json').exists() else {}
    return {'live_lock_holder': live, 'saved_progress': saved,
            'result': json.loads((root / 'result.json').read_text()) if (root / 'result.json').exists() else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--job')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv('backend/.env'); load_dotenv('.env')
    if args.status:
        print(json.dumps(status(args.root), ensure_ascii=False)); return
    if not args.manifest:
        parser.error('--manifest required')
    args.manifest = args.manifest.resolve(); args.root = args.root.resolve()
    if args.detach:
        load(args.manifest, args.root)
        if status(args.root)['live_lock_holder']:
            raise ValueError('batch lock is held; do not launch a duplicate')
        logfile = str(args.root / 'controller.log')
        if any(c in logfile for c in ('"', '\\', '\n', '\r')):
            raise ValueError('unsafe screen logfile path')
        config = args.root / 'screenrc'
        config.write_text('logfile "' + logfile + '"\nlogfile flush 1\ndeflog on\n')
        subprocess.run(['screen', '-c', str(config), '-dmS', 'wkp-claims-' + args.root.name[:32], sys.executable, '-m', MODULE,
                        '--manifest', str(args.manifest), '--root', str(args.root)], check=True)
    elif args.job:
        try:
            result = worker(args.manifest, args.root, args.job)
            from backend.pipeline.claim_management import sha256_json
            progress(args.root / (sha256_json(args.job) + '.result.json'), result)
        except BaseException:
            traceback.print_exc(); raise
    else:
        run(args.manifest, args.root)


if __name__ == '__main__':
    main()
