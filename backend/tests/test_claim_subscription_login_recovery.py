"""A recorded login refusal is not an unknown or a spent model generation."""
import json

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient, ClaudeSubscriptionError
from backend.pipeline.claim_review_recovery import pre_transport_login_refusal
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request, seed_claims, SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.fixture
def login_refused(action, monkeypatch):
    store=action[0];seed_claims(store,['C'])
    request,root=operation_request(action,'Update',['C'])
    policy=cm.Policy.model_validate_json(cm.DEFAULT_POLICY.read_text())
    policy.initial_candidate='existing'
    policy.models['opus']['backend']='claude_subscription'
    authorize(request,policy,root.with_suffix('.policy.json'))
    class FailedPorts(SyntheticPorts):
        def claim_management_adapter(self,role,policy):
            adapter=claim_management_model_adapter(role,policy)
            assert role=='opus'
            adapter._client=ClaudeSubscriptionClient(model=policy.models['opus']['model_id'],executable='/bin/echo')
            return adapter
    def logged_out(self):
        raise ClaudeSubscriptionError('claude-subscription requires an active claude.ai Pro, Max, Team, or Enterprise login; received: '
            +json.dumps({'loggedIn':False,'authMethod':'none'}))
    with monkeypatch.context() as patch:
        patch.setattr(ClaudeSubscriptionClient,'_verify_subscription_login',logged_out)
        assert execute_claim_management(store,request,root=root,ports=FailedPorts(store,root))['reason']=='model_failed'
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    previous={k:v for k,v in parent.items()if k not in {'run_id','status','error'}}
    return store,request,root,policy,previous


def test_login_recovery_keeps_original_budget_and_completes_once(login_refused,action):
    store,old,root,policy,previous=login_refused
    new,next_root=operation_request(action,'Update',['C'])
    new.predecessor_action_id=old.action_id;new.permissions=old.permissions
    authorize(new,policy,next_root.with_suffix('.policy.json'))
    proof=cm.continuation_budget(previous,new,policy)
    assert proof['spent_generations']['opus']==0
    assert proof['remaining_generations']['opus']==policy.budget.opus_calls
    assert len(proof['pre_generation_schema_rejections'])==1
    ports=SyntheticPorts(store,next_root)
    result=execute_claim_management(store,new,root=next_root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus']
    assert execute_claim_management(store,new,root=next_root,ports=ports)==result
    assert ports.calls==['opus']
    assert (root/next(iter(previous['latest_rounds'].values()))/'opus.failure.json').exists()


@pytest.mark.parametrize('fault',['invocation','exit','raw','wrong_packet','logged_in','unknown_error'])
def test_login_refusal_cannot_conceal_a_sent_or_changed_call(login_refused,fault):
    _,_,root,_,previous=login_refused
    key,slot=next((k,v)for k,v in previous['call_reservations'].items()if v['state']=='started')
    stage=root/slot['round_id'];transport=stage/'opus.transport'
    from backend.pipeline.source_reconciliation_runner import body,read,write
    if fault in {'invocation','exit'}:
        transport.mkdir(exist_ok=True);(transport/(fault+'.json')).write_text('{}')
    elif fault=='raw':(stage/'opus.raw.json').write_text('{}')
    else:
        path=stage/('opus.packet.json'if fault=='wrong_packet'else 'opus.failure.json')
        value=body(read(path))
        if fault=='wrong_packet':value['candidate_sha256']='0'*64
        elif fault=='logged_in':value['error']=value['error'].replace('false','true')
        else:value['error']='network disconnected'
        path.unlink();write(path,value)
    if fault=='unknown_error':assert pre_transport_login_refusal(previous,key,slot)is None
    else:
        with pytest.raises(ValueError):pre_transport_login_refusal(previous,key,slot)
