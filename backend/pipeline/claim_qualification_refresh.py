"""Finish a completed batch on current shared data with ordinary read-only Updates.

One frozen pass per checkpoint. No content writes, original-action restart,
new identity allocation or retry on a second root is performed here.
"""
from pathlib import Path
import fcntl
import json

from backend.pipeline import claim_management as cm, claim_management_batch as batch
from backend.pipeline.cvp_sql_commit import binding, check_binding, decoded_pins

CONTRACT = 'claim_completion_currentness_v1'


def pin(state):
    return [state['revision'], state['content_sha256'], state['retired']]


def verify_admission(request, goal, bound):
    """The worker must consume exactly the scoped SQL state prepared here."""
    check_binding(bound)
    origin = json.loads(Path(bound['path']).read_text())
    observed = {k:pin(v) for k,v in goal['original_objects'].items()}
    if (origin['contract'] != CONTRACT or origin['target_keys'] != request.target_keys
            or origin['source_bindings'] != request.model_dump(mode='json')['source_bindings']
            or origin['current_pins'] != observed or request.permissions or request.creation_permissions
            or request.allowed_operations != ['Update'] or request.predecessor_action_id):
        raise ValueError('completion currentness input/scope or read-only authority changed')
    for bound in origin['proofs']:
        check_binding(bound)


def prepare(store, jobs, root, instruction, current):
    from backend.pipeline.claim_management_intake import immutable
    from backend.pipeline.claim_qualification_reuse import evidence
    root = Path(root)
    intent = {'instruction':instruction,'jobs':[
        {'request':request.model_dump(mode='json'),'root':str(output)} for request,output in jobs.values()]}
    immutable(root/'input.json',intent)
    if (root/'plan.json').exists():
        plan = json.loads((root/'plan.json').read_text())
        for bound in plan['inputs'] + ([plan['manifest']] if plan['manifest'] else []):
            check_binding(bound)
        return plan
    owners, writes = {}, {}
    for request, output in jobs.values():
        parent = store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
        if (not parent or parent['result']['status'] != 'completed'
                or parent['request'] != request.model_dump(mode='json') or parent['execution_root'] != str(output)):
            raise ValueError('completion refresh requires the original completed batch')
        for rid in parent['latest_rounds'].values():
            child = store.get_cvp_group(rid)
            if (child.get('outcome') != 'completed' or not child.get('program_readback_complete')
                    or not child.get('opus_approved') or child.get('revisit_required')):
                raise ValueError('completion refresh requires actual approved readback')
            for cid in child.get('change_set_ids',[]):
                history = store.get_change_history(cid)
                if not history or history['status'] != 'applied':
                    raise ValueError('completion refresh lost an original committed ChangeSet')
                for op in history['operations']:
                    writes.setdefault(cm.source.key(op['collection'],op['object_id']),[]).append(
                        [op['after_revision'],op['after_sha256'],op['operation']=='retire'])
            for key in set(request.target_keys) | set(parent.get('created_keys',[])):
                if key in current['current_qualifications'] and key in {cm.source.key(*k) for k in decoded_pins(child['audited_pins'])}:
                    owners[key] = (parent,child,request)
    staged, inputs = [], []
    for key, qualification in sorted(current['current_qualifications'].items()):
        if key in current.get('retired_keys',[]) or qualification and qualification.get('ready'):
            continue
        if not qualification or qualification.get('reason') != 'current_program_readback_required':
            raise ValueError('completion refresh cannot bypass a missing review, source decision, hold or finding: '+key)
        if key not in owners:
            raise ValueError('completion refresh has no original target/creation readback: '+key)
        parent, child, original = owners[key]
        policy = cm.Policy.model_validate(parent['policy'])
        if (policy.independent_audit_required or policy.claim_scope_contract != 'claim_direct_neighbours_v1'
                or policy.model_output_contract != 'claim_changes_v4'):
            raise ValueError('historical policy has no automatic completion currentness contract')
        proof, _ = evidence(child)
        before = {cm.source.key(*k):cm.model_state(v) for k,v in store.read_claim_context([cm.source.split_key(key)]).items()}
        audited = {cm.source.key(*k):list(v) for k,v in decoded_pins(child['audited_pins']).items()}
        if key not in before or pin(before[key]) != audited[key] or before[key]['retired'] or before[key]['payload'].get('review_status') in cm.HELD:
            raise ValueError('completion refresh cannot replace a changed/held original Claim: '+key)
        # Only this completed cohort may explain the drift. Unknown external
        # edits require explicit reconciliation, not an automatic new review.
        for dependency, old in audited.items():
            row = before.get(dependency)
            if row is None:
                state = store.get_record_state(*cm.source.split_key(dependency))
                row = cm.model_state(state) if state else None
            if row is None or pin(row) != old and pin(row) not in writes.get(dependency,[]):
                raise ValueError('completion refresh has an unowned dependency change: '+dependency)
        token = cm.sha256_json({'root':str(root),'target':key})[:24]
        directory = root/'claims'/token
        policy.initial_candidate='existing'
        policy.budget.gpt_calls=1; policy.budget.opus_calls=2
        policy.budget.max_revisions=1; policy.budget.gemini_calls=0
        policy.policy_id += '-completion-currentness-v1'
        pb = immutable(directory/'policy.json',policy.model_dump(mode='json'))
        own = cm.own_source_ids(before[key]['payload'],{k:v['payload'] for k,v in before.items()})
        sources = {sid:original.source_bindings[sid] for sid in own}
        req = cm.Request(action_id='claim-currentness-'+token,operation='Update',allowed_operations=['Update'],
            target_keys=[key],source_bindings=sources,permissions=[],creation_permissions=[],policy=pb,
            authorization={'path':str(directory/'authorization.json'),'file_sha256':'0'*64})
        # Scope is derived by the same ordinary Update authority as the worker.
        scope = set(cm.audit_keys(req,before)) | {'source_documents/'+sid for sid in sources}
        if any(k not in audited and pin(before[k]) not in writes.get(k,[]) for k in scope):
            raise ValueError('completion refresh dependency scope expanded outside the completed batch')
        origin = immutable(directory/'origin.json',{'contract':CONTRACT,'owner_action_id':parent['action_id'],
            'owner_round_id':child['run_id'],'target_keys':[key],
            'source_bindings':req.model_dump(mode='json')['source_bindings'],
            'current_pins':{k:pin(before[k]) for k in sorted(scope)},
            'proofs':[proof,child['reviewed']['evidence']['approval']]})
        auth = {'schema_version':cm.VERSION,'authorized_by':'owner','authorization_quote':instruction,
            'human_content_approval':False,'request':req.model_dump(mode='json',exclude={'authorization','policy'}),
            'policy':pb,'required_maintenance_keys':[],'claim_creation_allocations':{},'completion_refresh':origin,
            'review_questions':['同批次後續合法修改了共用資料。依當前完整自身原文、Evidence、Question、立場及直接關係，核對此 Claim 現在是否仍成立。這是完成前的只讀核對，沒有修改、新建或退休權限。若發現實質問題，依正常流程交 GPT 判定；不得為結案掩蓋問題。']}
        req.authorization = cm.source.FileBinding.model_validate(immutable(directory/'authorization.json',auth))
        cm.original_goal(req,before)  # Same admission checks, before any dispatch.
        rb = immutable(directory/'request.json',req.model_dump(mode='json'))
        staged.append({'request':rb,'root':str(directory/'execution')})
        inputs.extend([pb,origin,req.authorization.model_dump(mode='json'),rb])
    manifest = immutable(root/'manifest.json',{'jobs':staged,'max_workers':1}) if staged else None
    plan = {'manifest':manifest,'inputs':inputs}
    immutable(root/'plan.json',plan)
    return plan


def run(store, jobs, root, *, instruction):
    """Advance one frozen currentness pass, then reread the whole cohort."""
    root = Path(root).resolve()
    current = batch._paragraph_results(store,jobs)
    if not (root/'plan.json').exists() and current['all_actions_completed']:
        return current
    if not all(v.get('status')=='completed' for v in current['results'].values()):
        return current
    root.mkdir(parents=True,exist_ok=True)
    with (root/'currentness.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            plan = prepare(store,jobs,root,instruction,current)
            if plan['manifest']:
                _, staged = batch.load(plan['manifest']['path'],root/'batch',store=store)
                stage = batch._paragraph_results(store,staged)
                if not all(v.get('status')=='completed' for v in stage['results'].values()):
                    batch.run(plan['manifest']['path'],root/'batch')
                    stage = batch._paragraph_results(store,staged)
                current = batch._paragraph_results(store,jobs)
                if not stage['all_actions_completed']:
                    current.update(all_actions_completed=False,completion_refresh={'status':'paused','stage':stage})
            batch.progress(root/'result.json',current)
            return current
        except (ValueError,RuntimeError,OSError) as exc:
            current.update(all_actions_completed=False,completion_refresh={'status':'paused','detail':str(exc)})
            batch.progress(root/'result.json',current)
            return current


def checkpoint(path, identity, result=None):
    """A passed earlier boundary is history, not current qualification.

    A resume may reach the later boundary that caused new drift. The final
    currentness check remains mandatory; this never upgrades old proofs.
    """
    path = Path(path)
    if path.exists():
        value = batch.body(batch.read(path))
        if value['identity'] != identity or not value['result']['all_actions_completed']:
            raise ValueError('paragraph completion checkpoint identity drift')
        return True
    if result is None:
        return False
    if not result['all_actions_completed']:
        raise ValueError('cannot checkpoint an incomplete paragraph')
    batch.write(path,{'identity':identity,'result':result})
    return True
