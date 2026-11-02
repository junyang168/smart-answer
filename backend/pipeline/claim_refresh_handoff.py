"""Operator views over existing Claim batches; no new execution authority."""
import json
from collections import Counter
from pathlib import Path

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_commit import check_binding
from backend.pipeline.source_reconciliation_runner import body, read


def completion_details(qualifications, results=None, retired_keys=()):
    """Describe existing decisions without making a content/qualification vote."""
    retired = set(retired_keys)
    return {
        'unfinished_claims': {key: q or {'ready': False, 'reason': 'no_current_qualification'}
            for key, q in sorted(qualifications.items()) if key not in retired and not (q and q.get('ready'))},
        'unfinished_actions': {key: result for key, result in (results or {}).items()
                               if result.get('status') != 'completed'},
    }


def saved_inputs(root):
    """Read exactly the saved batch, including accepted refill manifests."""
    root = Path(root).resolve()
    plan = body(read(root/'plan.json'))
    check_binding(plan['manifest'])
    manifest = json.loads(Path(plan['manifest']['path']).read_text())
    if manifest != {k: plan[k] for k in ('jobs', 'max_workers')}:
        raise ValueError('saved batch manifest differs from its original plan')
    manifests = [plan['manifest']]
    for path in sorted((root/'dispatch-inputs').glob('*.json')):
        bound = json.loads(path.read_text())
        check_binding(bound)
        manifests.append(bound)
    capacity_path = root/'dispatch-capacity.json'
    capacity = None
    if capacity_path.exists():
        saved = json.loads(capacity_path.read_text())
        if saved['manifest'] != plan['manifest']:
            raise ValueError('saved capacity belongs to a different manifest')
        capacity = saved['capacity']
    jobs = {}
    for bound in manifests:
        value = json.loads(Path(bound['path']).read_text())
        for job in value['jobs']:
            check_binding(job['request'])
            request = cm.Request.model_validate_json(Path(job['request']['path']).read_text())
            if request.action_id in jobs:
                raise ValueError('saved batch repeats an action')
            jobs[request.action_id] = (request, Path(job['root']).resolve(), job['request'])
    return root, plan['manifest'], capacity, jobs


def inspect(root, *, store=None, check_completion=True):
    """Read SQL and known artifacts only; never start, repair or approve work."""
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    root, manifest, capacity, jobs = saved_inputs(root)
    store = store if store is not None else PostgresKnowledgeStore()
    actions, qualifications = {}, {}
    for action_id, (request, execution, bound) in jobs.items():
        input_errors = []
        for file in [request.authorization, request.policy, *request.source_bindings.values()]:
            try:
                check_binding(file.model_dump(mode='json'))
            except (ValueError, OSError) as exc:
                input_errors.append({'path': file.path, 'detail': str(exc)})
        policy = cm.Policy.model_validate_json(Path(request.policy.path).read_text())
        parent = store.get_cvp_group('CMA-'+cm.sha256_json(action_id))
        if parent and (parent['request'] != request.model_dump(mode='json')
                       or parent['execution_root'] != str(execution)):
            input_errors.append({'detail': 'saved action request/root differs from SQL; do not resume'})
        slots = (parent or {}).get('call_reservations', {})
        budget = {}
        for role in cm.MODELS:
            counts = Counter(v['state'] for v in slots.values() if v['role'] == role)
            budget[role] = {'limit': policy.budget.limit(role), 'reservations_by_state': dict(counts),
                'unreserved': policy.budget.limit(role)-sum(n for state,n in counts.items() if state != 'cancelled')}
        rounds = {}
        round_ids = list((parent or {}).get('latest_rounds', {}).values())
        round_ids.extend(v['round_id'] for v in slots.values())
        for rid in dict.fromkeys(round_ids):
            row = store.get_cvp_group(rid)
            stage = Path((row or {}).get('recovery_root', execution/rid))
            roles = {}
            for role in cm.MODELS:
                paths = {name: stage/(role+suffix) for name,suffix in (
                    ('reservation','.reservation.json'),('packet','.packet.json'),
                    ('raw','.raw.json'),('failure','.failure.json'),
                    ('invocation','.transport/invocation.json'),('exit','.transport/exit.json'))}
                roles[role] = {'artifacts': {name: str(path) for name,path in paths.items() if path.exists()}}
                if paths['failure'].exists():
                    roles[role]['error'] = body(read(paths['failure'])).get('error')
                # File presence is a fact, not a verdict about generation or retry safety.
                roles[role]['reply_saved'] = paths['raw'].exists()
                roles[role]['transport_started'] = paths['invocation'].exists()
                roles[role]['transport_exited'] = paths['exit'].exists()
            rounds[rid] = {'checkpoint': (row or {}).get('checkpoint'), 'outcome': (row or {}).get('outcome'),
                'opus_approved': (row or {}).get('opus_approved', False),
                'program_readback_complete': (row or {}).get('program_readback_complete', False),
                'change_set_ids': (row or {}).get('change_set_ids', []), 'roles': roles,
                'stage_results': (row or {}).get('stage_results', {})}
        actions[action_id] = {'request_binding': bound, 'request': request.model_dump(mode='json'),
            'input_errors': input_errors,
            'execution_root': str(execution), 'result': (parent or {}).get('result', {'status':'not_started'}),
            'continued_by': (parent or {}).get('continued_by'), 'budget': budget,
            'prior_spent_generations': (parent or {}).get('continuation', {}).get('spent_generations', {}),
            'models': policy.models, 'rounds': rounds}
        if check_completion:
            keys = set(request.target_keys) | {k for k in (parent or {}).get('created_keys', []) if k.startswith('claims/')}
            for key in sorted(keys - qualifications.keys()):
                try:
                    qualifications[key] = store.claim_management_qualification(key.split('/',1)[1])
                except (ValueError, RuntimeError, OSError) as exc:
                    qualifications[key] = {'ready': False, 'reason': 'qualification_check_failed', 'detail': str(exc)}
    result = {'root': str(root), 'manifest': manifest, 'model_capacity': capacity,
        'actions': actions, 'completion_checked': check_completion, 'qualifications': qualifications,
        'model_calls': 0, 'content_writes': 0}
    if check_completion:
        result.update(completion_details(qualifications, {k:v['result'] for k,v in actions.items()}))
    return result


def resume(root, *, store=None):
    """Use saved inputs and the original executor; paused work stays governed there."""
    from backend.pipeline import claim_management_batch as batch
    # Inspect first so no execution starts after an input/root binding mismatch.
    before = inspect(root, store=store, check_completion=False)
    errors = {key: row['input_errors'] for key,row in before['actions'].items() if row['input_errors']}
    if errors:
        raise ValueError('saved batch bindings changed; do not resume: '+json.dumps(errors, ensure_ascii=False))
    batch.run(before['manifest']['path'], before['root'], model_capacity=before['model_capacity'])
    after = inspect(root, store=store)
    # The inspection itself is read-only; the dispatched executor may have
    # called models or committed content. Never report those as zero here.
    after.pop('model_calls')
    after.pop('content_writes')
    after['execution_dispatched'] = True
    return after
