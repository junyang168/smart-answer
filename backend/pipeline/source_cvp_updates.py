"""Continue completed source Claim work through existing CVP and Route runners.

This is a caller, not another content executor. Requests, calls, revisions,
transactions and Route jobs remain owned by their existing implementations.
"""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_recall import seal, STATEMENT_SCOPE_PROFILE
from backend.pipeline import claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch
from backend.pipeline import cvp_management_runner as cvp
from backend.pipeline.cvp_sql_commit import check_binding
from backend.pipeline.cvp_update_preparation import affected_update_viewpoints, prepare_update_input, update_permissions

POLICIES = Path(__file__).parent / 'policies'


@contextmanager
def execution_guard(store):
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts, maintenance_lock
    from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock
    from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
    ProductionPorts(store)._environment_guard(require_backup=False)
    runtime = canonical_runtime()
    if not runtime['reused'] or runtime['new_model_calls']:
        raise ValueError('source continuation requires the existing authorized runtime proof')
    with maintenance_lock() as maintenance, exclusive_cvp_run_lock({'global_lock_path': str(
            Path(os.environ['DATA_BASE_DIR']) / 'wang-knowledge-platform/staging/cvp-production-357/registry-cvp.lock')}) as registry:
        yield maintenance, registry


def prepare(store, claim_result, root, *, source_plan, instruction):
    """Freeze the downstream denominator once, without invoking a model."""
    root = Path(root)
    check_binding(source_plan)
    ids = claim_result['downstream_handoff']['claim_ids']
    retired = claim_result['downstream_handoff']['retired_claim_ids']
    if not claim_result.get('all_actions_completed') or claim_result.get('unowned_paragraphs'):
        raise ValueError('source Claim work is incomplete')
    intent = {'source_plan':source_plan, 'claim_ids':ids, 'retired_claim_ids':retired, 'instruction':instruction}
    intake.immutable(root/'input.json',intent)
    if (root/'plan.json').exists():
        plan = json.loads((root/'plan.json').read_text())
        for bound in plan['policies']:
            check_binding(bound)
        return plan
    for cid in ids:
        if not (store.claim_management_qualification(cid) or {}).get('ready'):
            raise ValueError('source Claim is not currently qualified: '+cid)
    policies = []
    for name in ('wang_cvp_resolution_policy_v7.json','wang_cvp_claim_input_v1.json'):
        policies.append(intake.immutable(root/name,json.loads((POLICIES/name).read_text())))
    vids = affected_update_viewpoints(store,[*ids,*retired])
    linked = {r['payload']['claim_id'] for r in store.read_cvp_links(viewpoint_ids=vids).values()
              if not r.get('retired') and r['payload'].get('effective_state') in {'active','invalidated'}}
    plan = {**intent, 'viewpoint_ids':vids, 'candidate_claim_ids':sorted(set(ids)-linked), 'policies':policies,
            'budget':{'max_calls':20,'max_revisions':4,'max_request_bytes':600000,'max_output_tokens':32000}}
    intake.immutable(root/'plan.json',plan)
    return plan


def request(store, plan, directory, *, viewpoint_ids=(), claim_ids=()):
    """Prepare a normal maintain or generation request at its first use."""
    path = directory/'request.json'
    if path.exists():
        return domain.Request.model_validate_json(path.read_text())
    base = seal({'schema_version':'wang_cvp_recall_preflight_v1', 'claim_ids':list(claim_ids),
        'grouping':{'scope_label':'source change','groups':[{'group_key':'source-coverage',
            'claim_ids':list(claim_ids),'rationale':'變更來源中尚未連接的完整 Claim 判斷。'}]},
        'cvp_policy':plan['policies'][0]['path'],'claim_input_policy':plan['policies'][1]['path'],
        'index_root':None,'recall_profile':STATEMENT_SCOPE_PROFILE,
        'recall_request_bytes':plan['budget']['max_request_bytes'],'recall_max_calls':20})
    views = list(viewpoint_ids)
    inp = prepare_update_input(store,base,viewpoint_ids=views,replacement_claim_ids=claim_ids) if views else base
    token = cvp.sha256_json({'root':str(directory.resolve()),'source_plan':plan['source_plan']})[:24]
    value = {'schema_version':domain.VERSION,'action_id':'source-cvp-'+token,'input':inp,
        'mode':'maintain' if views else 'create','maintenance_viewpoint_ids':views,
        'permissions':update_permissions(store,views) if views else [],'budget':plan['budget'],
        'creation_slots':{'canonical_viewpoints':20,'viewpoint_revisions':20,
                          'viewpoint_claim_links':40,'viewpoint_relations':20}}
    authorization = {'schema_version':'wang_cvp_management_authorization_v1','authorized_by':'owner',
        **{k:value[k] for k in ('action_id','mode','maintenance_viewpoint_ids','permissions','creation_slots','budget')},
        'input_sha256':inp['artifact_sha256']}
    value['authorization'] = intake.immutable(directory/'authorization.json',authorization)
    # Parse before persisting; an invalid candidate cohort never gets a task.
    result = domain.Request.model_validate(value)
    intake.immutable(path,result.model_dump(mode='json'))
    return result


def route_job(store, root, job_id, *, lock_fds=()):
    """Consume one exact job through the existing CLI, including its lease."""
    from backend.pipeline.viewpoint_route_cvp_worker import resume_applied_cvp_route
    queue = root/'route-queue-v2'
    state = json.loads((queue/'states'/(job_id+'.json')).read_text())
    job = json.loads((queue/'jobs'/(job_id+'.json')).read_text())
    output = root/'arguments'/job_id
    if state['status']=='complete':
        return resume_applied_cvp_route(store,job['viewpoint_id'],output,
                                       material_sha256=job['material_sha256']) is not None
    recovery = state['status'] not in {'queued','planned'}
    if recovery:
        # A crash after SQL commit can lose queue.finish. Authenticate the
        # applied approval/readback before allowing the existing CLI to finish
        # that exact job; unknown or merely returned calls do not qualify.
        if (state['status'] not in {'exception','running'}
                or not (output/'route-change-plan.json').exists()
                or state['status']=='running' and datetime.fromisoformat(state['lease_expires_at'])>datetime.now(timezone.utc)):
            return False
        if resume_applied_cvp_route(store,job['viewpoint_id'],output,
                                   material_sha256=job['material_sha256']) is None:
            return False
    command = [sys.executable,'-m','backend.pipeline.viewpoint_route_cvp_worker','run',
        '--queue-dir',str(queue),'--output-dir',str(root/'arguments'),
        '--job-id',job_id,'--apply']
    if recovery and state['status']=='exception':command.append('--retry-exceptions')
    with (root/(job_id+'.stdout.log')).open('a') as out, (root/(job_id+'.stderr.log')).open('a') as err:
        code = subprocess.run(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,pass_fds=lock_fds,
            env=dict(os.environ,KNOWLEDGE_DATABASE_URL=store.database_url)).returncode
    state = json.loads((queue/'states'/(job_id+'.json')).read_text())
    return (code==0 and state['status']=='complete' and
            resume_applied_cvp_route(store,job['viewpoint_id'],output,
                                    material_sha256=job['material_sha256']) is not None)


def execute(store, req, root, *, clients=None, lock_fds=()):
    parent = store.get_cvp_group('CVPM-'+cvp.sha256_json(req.action_id))
    generation_complete = 'update' not in req.input and parent and parent['result']['status']=='completed'
    if generation_complete:
        cvp.verify_completed_action(store,parent)
        result = parent['result']
    else:
        result = cvp.run(store,req,root=root,execute=True,clients=clients)
    ready = result.get('cvp_status',result['status'])=='completed'
    if not ready:
        return result
    if 'update' in req.input:
        jobs = result['update']['argument_jobs']
    else:
        # Generation uses the same existing route handoff as maintain.
        from backend.pipeline.viewpoint_route_cvp_worker import handoff_completed_cvp_groups
        parent = store.get_cvp_group('CVPM-'+cvp.sha256_json(req.action_id))
        handoff_completed_cvp_groups(store,parent,root,track_dependencies=True)
        jobs = {}
        for unit in parent['units'].values():
            step = store.get_cvp_group(unit['completed_step'])
            handoff = json.loads((Path(step['materials']['step_root'])/'route-handoff.json').read_text())
            if handoff['issues']:
                return {**result,'status':'arguments_pending','issues':handoff['issues']}
            jobs.update({jid:None for jid in handoff['job_ids']})
    for jid in jobs:
        batch.progress(root/'status.json',{'stage':'arguments','job_id':jid})
        if not route_job(store,root,jid,lock_fds=lock_fds):
            return {**result,'status':'arguments_pending','pending_job_id':jid}
    # Reauthenticate completed SQL afterimages and current Route handoffs.
    if 'update' in req.input:
        result = cvp.run(store,req,root=root,execute=True,clients=clients)
    else:
        cvp.verify_completed_action(store,parent)
        result = {**result,'arguments_completed':True}
    return result


def run(store, claim_result, root, *, source_plan, instruction, guard_source, clients=None):
    root = Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        guard_source()
        plan = prepare(store,claim_result,root,source_plan=source_plan,instruction=instruction)
        results = {}
        if not plan['viewpoint_ids'] and not plan['candidate_claim_ids']:
            value = {'status':'completed','results':{},'claim_ids':plan['claim_ids'],
                     'retired_claim_ids':plan['retired_claim_ids'],'independent_audit':'not_performed'}
            batch.progress(root/'result.json',value)
            return value
        with execution_guard(store) as handles:
            # One ordinary dependency unit includes existing endpoints and all
            # current source candidates not already members. They do not inherit
            # membership: GPT/Opus must decide each component's actual role.
            output = root/'cvp'
            req = request(store,plan,output,viewpoint_ids=plan['viewpoint_ids'],
                          claim_ids=plan['candidate_claim_ids'])
            batch.progress(root/'status.json',{'stage':'cvp_update',
                'viewpoint_ids':plan['viewpoint_ids'],'candidate_claim_ids':plan['candidate_claim_ids']})
            result = execute(store,req,output,clients=clients,
                             lock_fds=tuple(h.fileno() for h in (lock,*handles)))
            results['cvp'] = result
            if result['status']!='completed':
                value = {'status':'paused','stage':'arguments' if result['status']=='arguments_pending' else 'cvp_update',
                         'results':results}
                batch.progress(root/'result.json',value);return value
            guard_source()
            for cid in plan['claim_ids']:
                if not (store.claim_management_qualification(cid) or {}).get('ready'):
                    raise ValueError('source Claim qualification changed during CVP work: '+cid)
            value = {'status':'completed','results':results,'claim_ids':plan['claim_ids'],
                     'retired_claim_ids':plan['retired_claim_ids'],'independent_audit':'not_performed'}
            batch.progress(root/'result.json',value)
            return value
