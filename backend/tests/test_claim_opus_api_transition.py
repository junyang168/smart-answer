from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.subscription_process import save,digest_file
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,seed_claims,SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.fixture
def paused(action,request):
    saved_proposal = getattr(request,"param",None)=="saved_proposal"
    store=action[0];seed_claims(store,['C'])
    old,root=operation_request(action,'Update',['C'])
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v2.json').read_text())
    if saved_proposal:policy.initial_candidate='proposal'
    authorize(old,policy,root.with_suffix('.policy.json'))
    def fail(packet):raise RuntimeError('subscription quota')
    def proposal(packet):
        payload=deepcopy(packet['before']['claims/C']['payload']);payload['statement']='甲是假设，不是断言。'
        candidate=cm.existing_candidate(packet['before'])
        candidate.records.append(cm.source.Row(collection='claims',object_id='C',payload=payload))
        candidate.dispositions=[cm.ObjectDisposition(record_key=d.record_key,action='update',reason='saved GPT proposal',
            citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')])
            if d.record_key=='claims/C' else d for d in candidate.dispositions]
        return candidate.model_dump(mode='json')
    assert execute_claim_management(store,old,root=root,ports=SyntheticPorts(store,root,opus=fail,proposal=proposal if saved_proposal else None))['status']=='paused'
    previous=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    for slot in previous['call_reservations'].values():
        if slot['state']!='started':continue
        transport=root/slot['round_id']/'opus.transport';transport.mkdir()
        (transport/'stdout.log').write_text(json.dumps({'is_error':True,'api_error_status':429,
            'api_error':'usage_limit_reached','modelUsage':{},'usage':{'input_tokens':0,'output_tokens':0}}))
        (transport/'stderr.log').touch()
        save(transport/'exit.json',{'returncode':1,'files':{name:digest_file(transport/name) for name in ('stdout.log','stderr.log')}})
    successor,next_root=operation_request(action,'Update',['C'])
    successor.predecessor_action_id=old.action_id;successor.permissions=old.permissions
    api=policy.model_dump(mode='json');api['models']['opus']['backend']='anthropic_api'
    api['api_spend']={'root':str(root/'billing'),'limit_usd':100}
    api['initial_candidate']='existing'
    api['budget']['opus_calls']-=1
    if saved_proposal:api['budget']['gpt_calls']-=1
    api=cm.Policy.model_validate(api)
    authorize(successor,api,next_root.with_suffix('.policy.json'))
    authority=json.loads(Path(successor.authorization.path).read_text())
    metadata={k:v for k,v in previous.items() if k not in {'run_id','status','error'}}
    authority['opus_api_transition']={'predecessor_action_id':old.action_id,
        'prior_metadata_sha256':cm.sha256_json(metadata),'authorization_quote':'将 opus 调用改为 Api 我有 100 刀 credit',
        'models':api.models,'api_spend':api.api_spend}
    Path(successor.authorization.path).write_text(json.dumps(authority))
    from backend.pipeline.cvp_sql_commit import binding
    successor.authorization=cm.source.FileBinding.model_validate(binding(Path(successor.authorization.path)))
    return store,old,root,metadata,successor,next_root,api


@pytest.mark.parametrize("paused",["initial","saved_proposal"],indirect=True)
def test_api_successor_keeps_spent_calls_and_candidate_without_repeating_gpt(paused):
    store,old,root,previous,successor,next_root,policy=paused
    proof=cm.continuation_budget(previous,successor,policy)
    assert proof['spent_generations']['opus']==1 and proof['opus_api_transition']
    before=store.get_record_state('claims','C')
    ports=SyntheticPorts(store,next_root)
    result=execute_claim_management(store,successor,root=next_root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus']
    if proof['spent_generations']['gpt']:
        assert store.get_record_state('claims','C')['payload']['statement']=='甲是假设，不是断言。'
    else:assert store.get_record_state('claims','C')==before
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    assert parent['continued_by']=='CMA-'+cm.sha256_json(successor.action_id)
    for key,slot in previous['call_reservations'].items():
        if slot['state']!='planned':assert parent['call_reservations'][key]==slot


def test_api_transition_rejects_unknown_call(paused):
    store,old,root,previous,successor,next_root,policy=paused
    for slot in previous['call_reservations'].values():
        if slot['state']=='started':(root/slot['round_id']/'opus.transport/exit.json').unlink()
    with pytest.raises(ValueError,match='unknown or returned'):cm.continuation_budget(previous,successor,policy)


def test_api_transition_cannot_reset_budget(paused):
    store,old,root,previous,successor,next_root,policy=paused
    policy.budget.opus_calls+=1
    with pytest.raises(ValueError,match='reset or increase'):cm.continuation_budget(previous,successor,policy)
