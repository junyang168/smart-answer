"""One resumable source change: register, assess, Update in source order, maintain CVPs.

CLI: --source-id ID --root /absolute/task --instruction 'owner direction'
     [--before /preserved/file | --recovered-before /complete/projection]
No stage resets an existing action, cached reply, budget or human hold.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path

from backend.pipeline import claim_management as cm
from backend.pipeline import claim_management_intake as intake
from backend.pipeline import source_change_transition as transition
from backend.pipeline.cvp_sql_commit import binding, check_binding
from backend.pipeline.claim_management_batch import progress


@contextmanager
def production_lock():
    from backend.pipeline.source_reconciliation_runtime import maintenance_lock
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    data = Path(os.environ['DATA_BASE_DIR'])
    with maintenance_lock(), exclusive_cvp_run_lock({'global_lock_path': str(data /
            'wang-knowledge-platform/staging/cvp-production-357/registry-cvp.lock')}):
        yield


class Production:
    """Use the same production guards and actual operation executors."""
    lock = staticmethod(production_lock)

    def __init__(self, store):
        self.store = store

    def guard(self):
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        ProductionPorts(self.store)._environment_guard(require_backup=False)
        runtime = canonical_runtime()
        if not runtime['reused'] or runtime['new_model_calls']:
            raise ValueError('source change requires the existing authorized runtime proof')

    def impact(self, request, root):
        from backend.pipeline.cvp_sql_actions import execute_claim_management
        from backend.pipeline.source_reconciliation_runtime import ProductionPorts
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        ports = ProductionPorts(self.store)
        with self.lock():
            ports._environment_guard(require_backup=False)
            runtime = canonical_runtime()
            if not runtime['reused'] or runtime['new_model_calls']:
                raise ValueError('source change requires the existing authorized runtime proof')
            return execute_claim_management(self.store, request, root=root, ports=ports)

    def update(self, ids, root, **kwargs):
        return intake.run(ids, root, store=self.store, **kwargs)

    def downstream(self, ids, retired, root, instruction):
        from backend.pipeline.source_change_downstream import run
        with self.lock():
            return run(self.store, ids, retired, root, instruction=instruction)


def own_population(store, sid):
    records = store.read_affected_context([('source_documents', sid)])
    before = {cm.source.key(*key): cm.model_state(row) for key, row in records.items()}
    payloads = {key: row['payload'] for key, row in before.items()}
    keys = sorted(key for key, row in before.items() if key.startswith('claims/') and not row['retired']
        and cm.own_source_ids(row['payload'], payloads) == {sid})
    if not keys:
        raise ValueError('source change has no existing Claim owners; use source extraction')
    return before, keys


def prepare_impact(store, handoff, root, instruction):
    root = Path(root); request_path = root/'request.json'
    if request_path.exists():
        return cm.Request.model_validate_json(request_path.read_text())
    before, keys = own_population(store, handoff['source_id'])
    policy = cm.Policy.model_validate_json(intake.DEFAULT_POLICY.read_text())
    policy.policy_id = 'source-change-impact-v1'
    policy.review_purpose = 'source_impact'
    policy.source_impact_contract = 'source_change_automation_v1'
    policy.evidence_review_contract = policy.model_output_contract = policy.joint_update_contract = None
    policy.joint_update_max_targets = None
    policy.budget.max_revisions = policy.budget.gpt_calls = policy.budget.gemini_calls = 0
    policy.budget.opus_calls = 1
    policy.budget.max_request_bytes = 2000000  # full own sermon and its own knowledge, no other sermon expansion
    policy.budget.max_output_tokens = 32000
    policy = cm.Policy.model_validate(policy.model_dump(mode='json'))
    bound_policy = intake.immutable(root/'policy.json', policy.model_dump(mode='json'))
    auth = root/'authorization.json'
    request = cm.Request(action_id='source-impact-' + cm.sha256_json(str(root.resolve()))[:24],
        operation='Review', allowed_operations=['Review'], target_keys=keys,
        source_bindings={handoff['source_id']: handoff['source']}, source_transition=handoff['source_transition'],
        authorization={'path': str(auth.resolve()), 'file_sha256':'0'*64}, policy=bound_policy)
    direction = {'schema_version': cm.VERSION, 'authorized_by':'owner', 'authorization_quote':instruction,
        'human_content_approval':False, 'request':request.model_dump(mode='json', exclude={'authorization','policy'}),
        'policy':bound_policy, 'review_questions':[instruction]}
    request.authorization = cm.source.FileBinding.model_validate(intake.immutable(auth, direction))
    intake.immutable(request_path, request.model_dump(mode='json'))
    return request


def plan_paragraphs(store, request, handoff):
    """Direct anchor impacts PLUS source-wide semantic/coverage findings."""
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    if not parent or parent['result'].get('status') != 'completed':
        raise ValueError('source impact Review is not complete')
    changed = handoff['changed_paragraph_keys']
    positions = {key:i for i,key in enumerate(changed)}
    assignments = {}
    before, _ = own_population(store, handoff['source_id'])
    from backend.pipeline.claim_direct_scope import scope
    for target in request.target_keys:
        unit = request.model_copy(update={'target_keys':[target]})
        chain = scope(unit, before)
        paragraphs = {before[k]['payload'].get('paragraph_key') for k in chain['record_keys'] if k.startswith('source_fragments/')}
        hit = paragraphs & positions.keys()
        if hit:
            assignments[target] = min(hit, key=positions.get)
    for rid in parent['latest_rounds'].values():
        child = store.get_cvp_group(rid)
        # The actual reviewed raw, not a manually transcribed interpretation.
        from backend.pipeline.source_reconciliation_runner import body, read
        receipt = body(read(Path(child['stage_results']['opus']['path'])))
        from backend.pipeline.claim_model_output import review as expand_review
        candidate = cm.Candidate.model_validate(child['candidate'])
        review = expand_review(body(read(Path(receipt['materials']['path']).parent/'opus.raw.json'))['response'],
                               child['materials'], candidate)
        for item in review.source_impacts:
            if item.conclusion == 'source_uncertain':
                raise ValueError('source impact requires human clarification: ' + item.record_key)
            if item.conclusion != 'maintenance':
                continue
            hit = {c.paragraph_key for c in item.citations if c.source_id == handoff['source_id']} & positions.keys()
            key = min(hit, key=positions.get) if hit else changed[0]
            assignments[item.record_key] = min([key, assignments.get(item.record_key, key)], key=positions.get)
        for coverage in getattr(review, 'source_coverage', []):
            if coverage.conclusion != 'represented':
                for target in coverage.claim_keys:
                    assignments[target] = min([coverage.paragraph_key, assignments.get(target, coverage.paragraph_key)], key=positions.get)
    linked = {row['payload']['claim_id'] for row in store.read_cvp_links(
        claim_ids=[key.split('/',1)[1] for key in request.target_keys]).values()
        if row['payload'].get('effective_state') == 'active'}
    for key in parent['result'].get('pending_content_review_keys', []):
        if key.split('/',1)[1] in linked:
            assignments.setdefault(key, changed[0])
    groups = [{'paragraph_key': key, 'claim_ids': [t.split('/',1)[1] for t in sorted(assignments) if assignments[t] == key]}
              for key in changed if key in assignments.values()]
    return {'paragraphs': groups, 'impact_action_id':request.action_id,
            'findings': parent['findings'], 'source_transition':handoff['source_transition'],
            'downstream_claim_ids': sorted(linked | {key.split('/',1)[1] for key in assignments})}


def resolve_before(store, sid, root, before, recovered):
    saved = Path(root)/'source-handoff/intent.json'
    if saved.exists():
        intent = json.loads(saved.read_text())
        return intent['requested_before'], intent['recovered_baseline']
    if before:
        return binding(Path(before).resolve()), recovered
    doc = store.get_record_state('source_documents', sid)['payload']
    source_path = Path(doc['source_path'])
    archive = source_path.parent.parent/'wang-knowledge-platform/source-versions'/(doc['source_file_sha256']+'.json')
    if archive.is_file():
        return binding(archive), False
    raise ValueError('registered before bytes unavailable; supply a complete recovered baseline for projection verification')


def run(store, sid, root, *, instruction, before=None, recovered=False, source_confirmation=None, ports=None):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    ports = ports or Production(store)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            intent = {'source_id':sid, 'instruction':instruction, 'before':str(Path(before).resolve()) if before else None,
                      'recovered':recovered, 'source_confirmation':source_confirmation}
            intake.immutable(root/'automation.json', intent)
            saved = root/'handoff.json'
            if saved.exists():
                handoff = json.loads(saved.read_text())
            else:
                bound, recovered = resolve_before(store, sid, root, before, recovered)
                with ports.lock():
                    ports.guard()
                    handoff = transition.register(store, sid, bound, root/'source-handoff', instruction=instruction,
                        recovered=recovered, source_confirmation=source_confirmation)
                intake.immutable(saved, handoff)
            check_binding(handoff['source'])
            progress(root/'state.json', {'status':'running', 'stage':'source_impact'})
            request = prepare_impact(store, handoff, root/'impact', instruction)
            plan_path = root/'paragraphs.json'
            if not plan_path.exists():
                result = ports.impact(request, root/'impact/execution')
                if result.get('status') != 'completed':
                    return finish(root, 'source_impact', result)
            if not plan_path.exists():
                intake.immutable(plan_path, plan_paragraphs(store, request, handoff))
            plan = json.loads(plan_path.read_text())
            results = []
            for group in plan['paragraphs']:
                check_binding(handoff['source'])
                progress(root/'state.json', {'status':'running', 'stage':'claim_update', 'paragraph_key':group['paragraph_key']})
                direction = instruction + '\n完整来源变更影响审阅及覆盖义务：\n' + json.dumps(plan, ensure_ascii=False)
                item = ports.update(group['claim_ids'], root/'paragraphs'/group['paragraph_key'], instruction=direction,
                    max_workers=1, source_transition=handoff['source_transition'], predecessor_action_id=request.action_id)
                results.append(item)
                if not item.get('all_actions_completed'):
                    return finish(root, 'claim_update', {'status':'paused', 'paragraph_key':group['paragraph_key'], 'result':item})
            # Later shared edits can invalidate earlier completion. Read every
            # action and new Claim again without buying another review budget.
            active, retired = set(), set()
            for item in results:
                retired.update(k.split('/',1)[1] for k in item.get('retired_keys', []))
                active.update(item['claims'])
                active.update(k.split('/',1)[1] for k in item.get('created_keys', []))
            active.update(plan['downstream_claim_ids'])
            active -= retired
            qualifications = {cid:store.claim_management_qualification(cid) for cid in sorted(active)}
            if any(not q or not q.get('ready') for q in qualifications.values()):
                return finish(root, 'current_claims', {'status':'paused', 'qualifications':qualifications})
            check_binding(handoff['source'])
            progress(root/'state.json', {'status':'running', 'stage':'downstream'})
            downstream = ports.downstream(sorted(active), sorted(retired), root/'downstream', instruction)
            return finish(root, 'downstream', downstream, claim_ids=sorted(active), retired_claim_ids=sorted(retired))
        except BaseException as exc:
            progress(root/'state.json', {'status':'paused', 'detail':str(exc)})
            raise


def finish(root, stage, result, **extra):
    value = {'status':'completed' if stage == 'downstream' and result.get('status') == 'completed' else 'paused',
             'stage':stage, 'result':result, **extra}
    progress(Path(root)/'state.json', value)
    return value


def main():
    import argparse
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-id')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--instruction')
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--status', action='store_true')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--before', type=Path)
    group.add_argument('--recovered-before', type=Path)
    parser.add_argument('--source-confirmation')
    args = parser.parse_args()
    if not args.root.is_absolute():
        raise ValueError('absolute durable task root required')
    from backend.pipeline import claim_management_batch as batch
    if args.status:
        batches = {path.name: batch.status(path) for path in (args.root/'paragraphs').glob('*') if path.is_dir()}
        print(json.dumps({'controller':batch.status(args.root), 'batches':batches,
            'state':json.loads((args.root/'state.json').read_text()) if (args.root/'state.json').exists() else None}, ensure_ascii=False))
        return
    if not args.source_id or not args.instruction:
        parser.error('--source-id and --instruction are required')
    if args.detach:
        import sys
        args.root.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-m', 'backend.pipeline.source_change_automation', '--source-id',args.source_id,
                   '--root',str(args.root),'--instruction',args.instruction]
        for flag, value in [('--before',args.before),('--recovered-before',args.recovered_before),('--source-confirmation',args.source_confirmation)]:
            if value is not None: command.extend([flag,str(value)])
        batch.detach(command, args.root)
        return
    print(json.dumps(run(PostgresKnowledgeStore(), args.source_id, args.root, instruction=args.instruction,
        before=args.before or args.recovered_before, recovered=bool(args.recovered_before),
        source_confirmation=args.source_confirmation), ensure_ascii=False))


if __name__ == '__main__':
    main()
