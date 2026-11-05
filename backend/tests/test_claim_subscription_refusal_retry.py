"""An owner-requested same-task retry retains a closed Claude refusal as spent."""
import json
import subprocess
from pathlib import Path
from copy import deepcopy
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.claim_review_recovery import consumed_subscription_refusal
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,seed_claims,SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize

@pytest.fixture
def refused(action,monkeypatch):
    store=action[0];seed_claims(store,['C']);old,root=operation_request(action,'Update',['C'])
    policy=cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text());policy.initial_candidate='existing'
    policy.independent_audit=False;policy.budget.gemini_calls=0;policy.budget.gpt_calls=3;policy.budget.opus_calls=3;policy.budget.max_revisions=2
    policy.models['opus']['backend']='claude_subscription';authorize(old,policy,root.with_suffix('.policy.json'))
    output=json.dumps({'is_error':True,'stop_reason':'refusal','terminal_reason':'api_error','result':'synthetic provider refusal','usage':{'input_tokens':2,'output_tokens':10}})
    class FailedClient(ClaudeSubscriptionClient):
        def _verify_subscription_login(self):pass
        def generate_json(self,*args,**kwargs):
            def fail(command,**options):
                options['stdout'].write(output)
                return subprocess.CompletedProcess(command,1)
            with monkeypatch.context()as patch:
                patch.setattr('backend.pipeline.subscription_process.subprocess.run',fail)
                return super().generate_json(*args,**kwargs)
    class FailedPorts(SyntheticPorts):
        def claim_management_adapter(self,role,policy):
            adapter=claim_management_model_adapter(role,policy)
            adapter._client=FailedClient(model=policy.models[role]['model_id'],reasoning_effort=policy.models[role]['effort'],executable='/bin/echo')
            return adapter
    result=execute_claim_management(store,old,root=root,ports=FailedPorts(store,root));assert result['reason']=='model_failed',result
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id));previous={k:v for k,v in parent.items()if k not in {'run_id','status','error'}}
    key,slot=next((k,v)for k,v in previous['call_reservations'].items()if v['state']=='started')
    new,nroot=operation_request(action,'Update',['C']);new.predecessor_action_id=old.action_id;new.permissions=old.permissions
    follow=policy.model_copy(deep=True);follow.policy_id+='-owner-retry';follow.budget.opus_calls=2
    authorize(new,follow,nroot.with_suffix('.policy.json'))
    auth=json.loads(Path(new.authorization.path).read_text());auth['subscription_refusal_retry']={'predecessor_action_id':old.action_id,'prior_metadata_sha256':cm.sha256_json(previous),'slot':key,'failure':binding(root/slot['round_id']/'opus.failure.json'),'authorization_quote':'再試'}
    Path(new.authorization.path).write_text(json.dumps(auth));new.authorization=cm.source.FileBinding.model_validate(binding(Path(new.authorization.path)))
    return store,old,root,previous,key,slot,new,nroot,follow


def test_closed_refusal_one_explicit_retry_completes_without_erasing_or_enlarging_budget(refused):
    store,old,root,previous,key,slot,new,nroot,policy=refused
    proof=cm.continuation_budget(previous,new,policy)
    assert proof['spent_generations']['opus']==1 and proof['remaining_generations']['opus']==2
    assert proof['subscription_refusal_retry']['failures'][key]['provider_stop_reason']=='refusal'
    assert proof['original_goal']['subscription_refusal_retries']==[key]
    ports=SyntheticPorts(store,nroot);result=execute_claim_management(store,new,root=nroot,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus']
    assert execute_claim_management(store,new,root=nroot,ports=ports)==result and ports.calls==['opus']
    original=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    assert original['call_reservations'][key]==previous['call_reservations'][key]
    assert (root/slot['round_id']/'opus.failure.json').exists()


@pytest.mark.parametrize('fault',['missing_exit','stdout','input','complete_report','other_error','no_direction','policy_change','budget_increase','output_changed','repeat','repeat_via_continuation'])
def test_retry_rejects_unknown_tampering_new_instructions_or_unbounded_repetition(refused,fault):
    _,_,root,previous,key,slot,new,nroot,policy=refused;stage=root/slot['round_id'];transport=stage/'opus.transport'
    if fault=='missing_exit':(transport/'exit.json').unlink()
    elif fault=='stdout':(transport/'stdout.log').write_text('{}')
    elif fault=='input':
        p=transport/'invocation.json';v=json.loads(p.read_text());v['input_sha256']='0'*64;p.write_text(json.dumps(v))
    elif fault=='complete_report':(stage/'opus.raw.json').write_text('{}')
    elif fault=='other_error':
        # Even an explicitly authorized retry does not turn an unknown network stop into a refusal.
        p=transport/'stdout.log';p.write_text(p.read_text().replace('refusal','network_error'))
    else:
        auth=json.loads(Path(new.authorization.path).read_text())
        if fault=='no_direction':auth.pop('subscription_refusal_retry')
        elif fault=='policy_change':policy.initial_candidate='proposal'
        elif fault=='budget_increase':policy.budget.opus_calls=3
        elif fault=='output_changed':policy.budget.max_output_tokens-=1
        elif fault=='repeat_via_continuation':
            previous['continuation']={'original_goal':{'subscription_refusal_retries':['already-used']}}
            auth['subscription_refusal_retry']['prior_metadata_sha256']=cm.sha256_json(previous)
        else:
            previous['original_goal']['subscription_refusal_retries']=['already-used']
            auth['subscription_refusal_retry']['prior_metadata_sha256']=cm.sha256_json(previous)
        Path(new.policy.path).write_text(policy.model_dump_json());new.policy=cm.source.FileBinding.model_validate(binding(Path(new.policy.path)))
        auth['policy']=new.policy.model_dump(mode='json');Path(new.authorization.path).write_text(json.dumps(auth));new.authorization=cm.source.FileBinding.model_validate(binding(Path(new.authorization.path)))
    with pytest.raises((ValueError,OSError)):
        cm.continuation_budget(previous,new,policy)


@pytest.mark.parametrize('drift',['record','human_fact'])
def test_same_task_retry_refuses_changed_model_facts_before_any_call(refused,drift):
    store,old,root,previous,key,slot,new,nroot,policy=refused
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    if drift=='record':
        p=store.get_record_state('source_documents','S')['payload'];p['title']='changed source metadata'
        store.apply_plan(store.plan_package({'package_id':'external-drift','source_documents':[p]}))
    else:
        from backend.tests.test_claim_human_source_decisions import facts,save
        _,entries=facts(old,root.parent);save(store,entries)
    ports=SyntheticPorts(store,nroot)
    with pytest.raises(ChangeSetConflict):execute_claim_management(store,new,root=nroot,ports=ports)
    assert ports.calls==[]
    assert not store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id)).get('continued_by')
