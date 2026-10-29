"""Run bound Claim requests durably; reuse the existing action/root/call budgets.

Launch with --detach (screen), inspect with --status, resume the SAME manifest.
Use --paragraphs to call ordinary batches in source paragraph order.
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
from backend.pipeline.cvp_sql_commit import check_binding

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


def load(manifest, root, *, store=None):
    from backend.pipeline import claim_management as cm
    manifest = Path(manifest)
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
        if store is None:
            from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
            store = PostgresKnowledgeStore()
        documents = {}
        for sid in request.source_bindings:
            state = store.get_record_state('source_documents', sid)
            if state is None or state['retired']:
                raise ValueError('batch source metadata is missing or retired: ' + sid)
            documents[sid] = state['payload']
        cm.load_authority(request, source_documents=documents)
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


def run(manifest, root, *, model_capacity=None, refill=None):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    from backend.pipeline.source_reconciliation_runtime import maintenance_lock
    from backend.pipeline import claim_management as cm
    manifest = Path(manifest).resolve()
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
        if refill and model_capacity is None:
            raise ValueError('continuous refill requires explicit model capacity')
        additions = root / 'dispatch-inputs'
        additions.mkdir(exist_ok=True)
        def append_manifest(bound, *, remember):
            check_binding(bound)
            supplied = Path(bound['path'])
            _, incoming = load(supplied, supplied.parent / 'batch', store=store)
            targets = {k for request, _ in jobs.values() for k in request.target_keys}
            roots = {str(path) for _, path in jobs.values()}
            for key, (request, output) in incoming.items():
                if key in jobs or targets & set(request.target_keys) or str(output) in roots:
                    raise ValueError('continuous refill duplicates an action, target or root')
                targets.update(request.target_keys); roots.add(str(output))
            if remember:
                from backend.pipeline.claim_management_intake import immutable
                immutable(additions / ('%06d.json' % len(list(additions.glob('*.json')))), bound)
            jobs.update(incoming)
            pending.extend(incoming)
        for saved in sorted(additions.glob('*.json')):
            append_manifest(json.loads(saved.read_text()), remember=False)
        source_locks = {}
        capacity = None
        blocked = {}
        if model_capacity is not None:
            from backend.pipeline.claim_model_capacity import ClaimModelCapacity
            from backend.pipeline.claim_management_intake import immutable
            capacity = ClaimModelCapacity(model_capacity)
            immutable(root / 'dispatch-capacity.json', {'manifest': binding(manifest), 'capacity': model_capacity})
            # Existing roots retain their SQL request, budget and outcome.
            # Neither completed nor paused actions are automatically restarted.
            for key, (request, action_root) in jobs.items():
                parent = store.get_cvp_group('CMA-' + cm.sha256_json(key))
                if parent:
                    if parent['request'] != request.model_dump(mode='json') or parent['execution_root'] != str(action_root):
                        raise ValueError('capacity dispatch cannot replace an existing action/root')
                    if parent['result'].get('status') in {'completed', 'paused'}:
                        results[key] = {'action_id': key, 'result': parent['result']}
                        pending.remove(key)
        (root / 'result.json').unlink(missing_ok=True)
        log = stack.enter_context((root / 'events.jsonl').open('a', buffering=1))
        def event(**value):
            log.write(json.dumps(value | {'time': datetime.now(timezone.utc).isoformat()}) + '\n')
            log.flush(); os.fsync(log.fileno())
        try:
            # A running job keeps the footprint it was launched with; pending
            # footprints are re-read only when a job exits. Re-reading every
            # job on every one-second poll raced the worker's own commit.
            scopes, stale, refill_ready = {}, True, True
            while pending or active or refill:
                if stale:
                    for key in pending:
                        request, _ = jobs[key]
                        sources = {'claim_source_execution/' + sid for sid in request.source_bindings}
                        if capacity:
                            scopes[key] = (sources, sources)
                        else:
                            records = store.read_claim_context([cm.source.split_key(k) for k in request.target_keys])
                            scopes[key] = ({cm.source.key(*k) for k in records} | sources,
                                           {p.key for p in request.permissions} | sources)
                    stale = False
                blocked = {role: json.loads((capacity.root / role / 'state.json').read_text()).get('blocked')
                           for role in ('gpt', 'opus') if capacity and (capacity.root / role / 'state.json').exists()}
                blocked = {role: reason for role, reason in blocked.items() if reason}
                if refill and refill_ready and not blocked:
                    room = model_capacity['action_window'] - len(active) - len(pending)
                    if room > 0:
                        occupied_sources = {sid for key in [*active, *pending]
                                            for sid in jobs[key][0].source_bindings}
                        supplied = refill(occupied_sources, room, jobs)
                        refill_ready = False
                        for bound in supplied:
                            append_manifest(bound, remember=True)
                        if supplied:
                            stale = True
                            continue
                if not pending and not active:
                    break
                ready = [] if blocked else selection(pending, active, scopes,
                    model_capacity['action_window'] if capacity else plan['max_workers'])
                for key in ready:
                    held = []
                    if capacity:
                        lane_root = capacity.root / 'sources'
                        lane_root.mkdir(exist_ok=True)
                        for sid in sorted(jobs[key][0].source_bindings):
                            handle = (lane_root / (cm.sha256_json(sid) + '.lock')).open('a')
                            try:
                                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError:
                                handle.close()
                                for prior in held: prior.close()
                                held = None
                                break
                            held.append(handle)
                        if held is None:
                            continue
                        source_locks[key] = held
                    token = cm.sha256_json(key)
                    output = root / (token + '.result.json')
                    output.unlink(missing_ok=True)  # derived worker receipt, never model/action artifacts
                    out = (root / (token + '.stdout.log')).open('a')
                    err = (root / (token + '.stderr.log')).open('a')
                    command = [sys.executable, '-m', MODULE, '--manifest', str(manifest), '--root', str(root), '--job', key]
                    environment = dict(os.environ, WKP_CLAIM_BATCH_LOCK_FDS=','.join(str(h.fileno()) for h in (lock, maintenance, registry)))
                    if capacity:
                        environment['WKP_CLAIM_CAPACITY_CONFIG'] = str(capacity.root / 'configuration.json')
                        environment['WKP_CLAIM_SOURCE_LOCK_FDS'] = ','.join(str(h.fileno()) for h in held)
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=environment,
                        pass_fds=(lock.fileno(), maintenance.fileno(), registry.fileno(), *(h.fileno() for h in held)))
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
                    for handle in source_locks.pop(key, []): handle.close()
                    stale = True
                    refill_ready = True
                progress(root / 'status.json', {'controller_pid': os.getpid(), 'active': {k: p[0].pid for k,p in active.items()},
                    'pending': pending, 'results': results, 'provider_blocks': blocked, 'heartbeat': datetime.now(timezone.utc).isoformat()})
                if not active and blocked:
                    event(stage='provider_dispatch_paused', reasons=blocked)
                    break
                if pending or active:
                    time.sleep(1)
            current = {key: {k: store.claim_management_qualification(k.split('/', 1)[1])
                              for k in request.target_keys} for key, (request, _) in jobs.items()}
            completion = _paragraph_results(store, jobs)
            progress(root / 'result.json', {'results': results, 'current_qualifications': current,
                'created_keys': completion['created_keys'], 'retired_keys': completion.get('retired_keys', []),
                'pending': pending, 'provider_blocks': blocked,
                'all_actions_completed': completion['all_actions_completed']})
        except BaseException as exc:
            event(stage='controller_failed', error=repr(exc))
            # Inherited locks survive even SIGKILL until the workers exit.
            raise


def load_paragraphs(manifest, root, *, store=None):
    """Prepare ordinary batches; never edit their bound Update requests."""
    from backend.pipeline import claim_management as cm
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store = store if store is not None else PostgresKnowledgeStore()
    manifest, root = Path(manifest).resolve(), Path(root).resolve()
    value = json.loads(manifest.read_text())
    if set(value) != {'source_id', 'source', 'paragraphs'} or not value['paragraphs']:
        raise ValueError('paragraph sequence requires one source and paragraph batches')
    check_binding(value['source'])
    document = store.get_record_state('source_documents', value['source_id'])
    if document is None or document['retired']:
        raise ValueError('paragraph source is missing or retired')
    snapshot = cm.source.source_snapshot(value['source'], source=document['payload'])
    positions = {key: i for i, key in enumerate(snapshot['paragraphs'])}
    entries = value['paragraphs']
    for entry in entries:
        if set(entry) != {'paragraph_key', 'batch', 'root'} or entry['paragraph_key'] not in positions:
            raise ValueError('paragraph batch must name an actual source paragraph')
        if not Path(entry['root']).is_absolute():
            raise ValueError('paragraph batch root must be absolute')
    if len({entry['paragraph_key'] for entry in entries}) != len(entries):
        raise ValueError('duplicate paragraph batch')
    entries = sorted(entries, key=lambda entry: positions[entry['paragraph_key']])
    owners, actions, roots, steps, requests, batch_inputs = {}, {}, {str(root)}, [], [], []
    batch_roots = {str(Path(entry['root']).resolve()) for entry in entries}
    if len(batch_roots) != len(entries) or str(root) in batch_roots:
        raise ValueError('paragraph batch roots must be distinct from the sequence root')
    roots.update(batch_roots)
    for entry in entries:
        check_binding(entry['batch'])
        original = json.loads(Path(entry['batch']['path']).read_text())
        if set(original) != {'jobs', 'max_workers'} or not original['jobs'] or original['max_workers'] not in (1, 2):
            raise ValueError('paragraph input must be an ordinary batch manifest')
        jobs = []
        for job in original['jobs']:
            if set(job) != {'request', 'root'} or not Path(job['root']).is_absolute():
                raise ValueError('paragraph job must bind an existing request and absolute action root')
            check_binding(job['request'])
            request = cm.Request.model_validate_json(Path(job['request']['path']).read_text())
            if (request.operation != 'Update' or not request.target_keys
                    or request.source_bindings.get(value['source_id']) != cm.source.FileBinding.model_validate(value['source'])):
                raise ValueError('paragraph Update must bind the same complete current source')
            identity = {'request': job['request'], 'root': str(Path(job['root']).resolve())}
            if request.action_id in actions:
                if actions[request.action_id] != identity:
                    raise ValueError('duplicate action changed its request or root')
                continue  # The identical whole action belongs to its earliest paragraph.
            if set(request.target_keys) & owners.keys():
                raise ValueError('overlapping Claim targets require deduplication before authorization; cannot trim a bound Update')
            if identity['root'] in roots:
                raise ValueError('duplicate action/batch/sequence root')
            roots.add(identity['root'])
            owners.update({key: entry['paragraph_key'] for key in request.target_keys})
            actions[request.action_id] = identity
            jobs.append(job)
            requests.append(job['request'])
        step_root = Path(entry['root']).resolve()
        selected = step_root / 'paragraph-input.json'
        step_jobs = {}
        if jobs:
            selected_value = {'jobs': jobs, 'max_workers': original['max_workers']}
            if selected.exists() and json.loads(selected.read_text()) != selected_value:
                raise ValueError('immutable paragraph batch input differs')
            if not selected.exists():
                step_root.mkdir(parents=True, exist_ok=True)
                progress(selected, selected_value)
            _, step_jobs = load(selected, step_root, store=store)
            batch_inputs.append(binding(selected))
        steps.append((entry['paragraph_key'], selected, step_root, step_jobs))
    plan = {'manifest': binding(manifest), 'source_id': value['source_id'], 'source': value['source'],
            'source_pin': [document['revision'], document['content_sha256']],
            'paragraphs': entries, 'claim_paragraphs': owners, 'requests': requests, 'batch_inputs': batch_inputs}
    write(root / 'plan.json', plan)
    return plan, steps


def _paragraph_guard(plan, store):
    check_binding(plan['manifest'])
    check_binding(plan['source'])
    for entry in plan['paragraphs']:
        check_binding(entry['batch'])
    for request in plan['requests']:
        check_binding(request)
    for selected in plan['batch_inputs']:
        check_binding(selected)
    document = store.get_record_state('source_documents', plan['source_id'])
    if (document is None or document['retired']
            or [document['revision'], document['content_sha256']] != plan['source_pin']):
        raise ValueError('paragraph source registration changed during sequence')


def _paragraph_results(store, jobs):
    """SQL is the completion authority, including new Claims, not a saved flag."""
    from backend.pipeline import claim_management as cm
    results, qualifications, created, retired = {}, {}, set(), set()
    for action_id, (request, action_root) in jobs.items():
        check_binding(request.authorization.model_dump())
        check_binding(request.policy.model_dump())
        parent = store.get_cvp_group('CMA-' + cm.sha256_json(action_id))
        if parent and (parent['request'] != request.model_dump(mode='json')
                       or parent['execution_root'] != str(action_root)):
            raise ValueError('paragraph recovery cannot change the original action/root/budget')
        results[action_id] = parent['result'] if parent else {'status': 'not_started'}
        created.update(key for key in (parent or {}).get('created_keys', []) if key.startswith('claims/'))
        if (parent and parent['result'].get('status') == 'completed'
                and parent['original_goal'].get('target_disposition_contract') == 'reviewed_update_or_retirement_v1'):
            candidates = []
            for rid in parent['latest_rounds'].values():
                child = store.get_cvp_group(rid)
                candidates.extend(d['record_key'] for d in child['candidate']['dispositions']
                                  if d['action'] == 'retire' and d['record_key'] in request.target_keys)
            if candidates:
                # Retired means successfully processed, never Claim-ready.
                # Consume the ordinary completion boundary, including the
                # real Opus retirement verdict and exact current readback.
                try:
                    store.finish_claim_management_action(parent['run_id'])
                    retired.update(candidates)
                except (ValueError, RuntimeError) as exc:
                    results[action_id] = {'status': 'paused', 'reason': 'retirement_readback_changed', 'detail': str(exc)}
    for key in set(k for request, _ in jobs.values() for k in request.target_keys) | created:
        qualifications[key] = store.claim_management_qualification(key.split('/', 1)[1])
    return {'results': results, 'current_qualifications': qualifications, 'created_keys': sorted(created),
            **({'retired_keys': sorted(retired)} if retired else {}),
            'all_actions_completed': all(result.get('status') == 'completed' for result in results.values())
            and all(key in retired or (q and q.get('ready')) for key, q in qualifications.items())}


def run_paragraphs(manifest, root, *, store=None):
    """Call p1's existing batch, then p2's; each batch retains its scheduler."""
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store = store if store is not None else PostgresKnowledgeStore()
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed, all_jobs = [], {}
        try:
            plan, steps = load_paragraphs(manifest, root, store=store)
            for paragraph, batch_input, batch_root, jobs in steps:
                _paragraph_guard(plan, store)
                all_jobs.update(jobs)
                from backend.pipeline.claim_qualification_refresh import run as refresh_currentness, checkpoint
                identity={'manifest':plan['manifest'],'paragraph_key':paragraph}
                passed=root/'completed-paragraphs'/(paragraph+'.json')
                if checkpoint(passed,identity,store=store,jobs=all_jobs):
                    completed.append(paragraph);continue
                current = _paragraph_results(store, jobs)
                progress(root / 'status.json', {'paragraph_key': paragraph, 'completed_paragraphs': completed})
                if jobs and not all(v.get('status') == 'completed' for v in current['results'].values()):
                    run(batch_input, batch_root)  # Reads current Claim context after the previous commit.
                    current = _paragraph_results(store, jobs)
                _paragraph_guard(plan, store)
                current = refresh_currentness(store, all_jobs, root / 'completion-currentness' / paragraph,
                    instruction='Complete the authorized paragraph Updates on current shared data; retain original action history and write authority.')
                if not current['all_actions_completed']:
                    result = {'status': 'paused', 'paragraph_key': paragraph, 'completed_paragraphs': completed,
                              'all_actions_completed': False, 'batch': current}
                    progress(root / 'result.json', result)
                    return result
                checkpoint(passed,identity,current,store=store,jobs=all_jobs)
                completed.append(paragraph)
            _paragraph_guard(plan, store)
            result = _paragraph_results(store, all_jobs)
            result.update(status='claims_completed' if result['all_actions_completed'] else 'paused',
                          completed_paragraphs=completed,
                          downstream_handoff={'claim_ids': sorted(key.split('/', 1)[1] for key in result['current_qualifications']),
                                              'action_ids': list(all_jobs), 'status': 'not_executed'})
            if result.get('retired_keys'):
                result['downstream_handoff']['retired_claim_ids'] = [key.split('/', 1)[1] for key in result['retired_keys']]
            progress(root / 'result.json', result)
            return result
        except BaseException as exc:
            progress(root / 'result.json', {'status': 'paused', 'all_actions_completed': False,
                                           'completed_paragraphs': completed, 'detail': str(exc)})
            raise


def status(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    paths = [root / 'controller.lock']
    if (root / 'intake.json').exists():
        paths.extend([root / 'intake.lock', root / 'batch/controller.lock'])
    live = False
    for path in paths:
        if not path.parent.exists():
            continue
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                live = True  # coordinator OR an inherited worker holds the lock
    progress_file = root / ('batch/status.json' if (root / 'intake.json').exists() else 'status.json')
    saved = json.loads(progress_file.read_text()) if progress_file.exists() else {}
    return {'live_lock_holder': live, 'saved_progress': saved,
            'result': json.loads((root / 'result.json').read_text()) if (root / 'result.json').exists() else None}


def detach(command, root):
    if status(root)['live_lock_holder']:
        raise ValueError('batch lock is held; do not launch a duplicate')
    logfile = str(root / 'controller.log')
    if any(c in logfile for c in ('"', '\\', '\n', '\r')):
        raise ValueError('unsafe screen logfile path')
    config = root / 'screenrc'
    config.write_text('logfile "' + logfile + '"\nlogfile flush 1\ndeflog on\n')
    subprocess.run(['screen', '-c', str(config), '-dmS', 'wkp-claims-' + root.name[:32], *command], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--claim-ids', type=Path, help='JSON list of Claim IDs; prepares and runs ordinary Updates')
    parser.add_argument('--instruction', help='the actual owner direction for the Claim ID list')
    parser.add_argument('--max-workers', type=int, choices=(1, 2), default=1)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--job')
    parser.add_argument('--model-capacity', type=Path, help='explicit shared GPT/Opus capacity configuration; legacy batches unchanged')
    parser.add_argument('--paragraphs', action='store_true', help='call ordinary batches in source paragraph order')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv('backend/.env'); load_dotenv('.env')
    model_capacity = json.loads(args.model_capacity.read_text()) if args.model_capacity else None
    if model_capacity and (args.detach or args.paragraphs or args.job):
        parser.error('--model-capacity requires direct batch or Claim list execution')
    if args.status:
        print(json.dumps(status(args.root), ensure_ascii=False)); return
    if args.claim_ids:
        if args.manifest or args.paragraphs or args.job:
            parser.error('--claim-ids cannot combine with manifest/paragraph/job')
        if not args.instruction:
            parser.error('--instruction required with --claim-ids')
        from backend.pipeline.claim_management_intake import run as run_claim_ids, prepare
        args.root = args.root.resolve()
        ids = json.loads(args.claim_ids.read_text())
        if args.detach:
            prepared = prepare(ids, args.root, instruction=args.instruction, max_workers=args.max_workers)
            # Child consumes frozen normalized IDs; caller may move its original file.
            ids_path = args.root / 'claim-ids.json'
            from backend.pipeline.claim_management_intake import immutable
            immutable(ids_path, prepared['claim_ids'])
            detach([sys.executable, '-m', MODULE, '--claim-ids', str(ids_path), '--root', str(args.root),
                    '--instruction', args.instruction, '--max-workers', str(args.max_workers)], args.root)
            return
        result = run_claim_ids(ids, args.root,
                               instruction=args.instruction, max_workers=args.max_workers, model_capacity=model_capacity)
        print(json.dumps(result, ensure_ascii=False)); return
    if not args.manifest:
        parser.error('--manifest required')
    args.manifest = args.manifest.resolve(); args.root = args.root.resolve()
    if args.paragraphs and args.job:
        parser.error('--paragraphs cannot run an individual batch worker')
    if args.detach:
        (load_paragraphs if args.paragraphs else load)(args.manifest, args.root)
        detach([sys.executable, '-m', MODULE,
                        '--manifest', str(args.manifest), '--root', str(args.root),
                        *(['--paragraphs'] if args.paragraphs else [])], args.root)
    elif args.job:
        try:
            result = worker(args.manifest, args.root, args.job)
            from backend.pipeline.claim_management import sha256_json
            progress(args.root / (sha256_json(args.job) + '.result.json'), result)
        except BaseException:
            traceback.print_exc(); raise
    else:
        if args.paragraphs:
            print(json.dumps(run_paragraphs(args.manifest, args.root), ensure_ascii=False))
        else:
            run(args.manifest, args.root, model_capacity=model_capacity)


if __name__ == '__main__':
    main()
