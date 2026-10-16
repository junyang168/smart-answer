"""Explicit owner permission can continue a lost call without erasing it."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,seed_claims,SyntheticPorts,rejected


def authorize(request,policy,path,extension=None):
    path.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(path))
    auth_path=Path(request.authorization.path);auth=json.loads(auth_path.read_text())
    auth.update(request=request.model_dump(mode='json',exclude={'authorization','policy'}),policy=request.policy.model_dump(mode='json'),required_maintenance_keys=[])
    if extension is not None:auth['owner_call_extension']=extension
    auth_path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))


@pytest.fixture
def lost_update(action):
    store=action[0];seed_claims(store,['C'])
    request,root=operation_request(action,'Update',['C'])
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v2.json').read_text())
    authorize(request,policy,root.with_suffix('.policy.json'))
    def fail(packet):raise RuntimeError('synthetic lost reply')
    def review(packet):
        value=rejected(packet)
        value['findings'][0].update(affected_target_keys=['claims/C'],affected_write_keys=[],impact_reason='target wording')
        return value
    ports=SyntheticPorts(store,root,proposal=fail,opus=review)
    assert execute_claim_management(store,request,root=root,ports=ports)['reason']=='model_failed'
    assert execute_claim_management(store,request,root=root,ports=ports)['reason']=='call_uncertain'
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    successor,next_root=operation_request(action,'Update',['C']);successor.predecessor_action_id=request.action_id
    successor.permissions=request.permissions
    new=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text())
    new.initial_candidate='proposal';new.budget.gpt_calls=3;new.budget.opus_calls=3;new.budget.max_revisions=2
    metadata={k:v for k,v in parent.items() if k not in {'run_id','status','error'}}
    extension={'predecessor_action_id':request.action_id,'prior_metadata_sha256':cm.sha256_json(metadata),
        'budget':new.budget.model_dump(mode='json'),'authorization_quote':'允许你调用多次'}
    path=next_root.with_suffix('.policy.json');authorize(successor,new,path,extension)
    return store,request,root,parent,successor,next_root,new,extension,path


def decision(fid):
    return {'finding_id':fid,'disposition':'resolved','reason':'complete source settles the original question',
        'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}],
        'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'original target inspected'}


def test_owner_extension_reuses_findings_with_compact_output_and_preserves_unknown_call(lost_update):
    store,request,root,old,successor,next_root,policy,extension,path=lost_update
    original=store.get_record_state('claims','C')
    def propose(packet):
        assert packet['previous_candidate'] and packet['unresolved']
        assert packet['continuation']['preserved_unknown_calls']
        return {'changes':[],'findings':[],'finding_resolutions':[decision(fid) for fid in packet['unresolved']]}
    def review(packet):return {'complete_review':True,'findings':[],'prior_findings':[decision(fid) for fid in packet['unresolved']]}
    ports=SyntheticPorts(store,next_root,proposal=propose,opus=review)
    result=execute_claim_management(store,successor,root=next_root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    assert store.get_record_state('claims','C')==original and store.claim_management_qualification('C')['ready']
    current=store.get_cvp_group(old['run_id'])
    for key,slot in old['call_reservations'].items():
        if slot['state']!='planned':assert current['call_reservations'][key]==slot
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(successor.action_id))
    assert parent['continuation']['owner_call_extension']==extension
    assert execute_claim_management(store,successor,root=next_root,ports=ports)==result
    assert ports.calls==['gpt','opus']
    third,third_root=operation_request((store,request,None,None,next_root,None),'Update',['C'])
    third.predecessor_action_id=request.action_id;third.permissions=request.permissions
    authorize(third,policy,third_root.with_suffix('.policy.json'),extension)
    with pytest.raises(ValueError):execute_claim_management(store,third,root=third_root,ports=ports)
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('change',['missing_permission','metadata','budget','quote','predecessor','widen_permissions',
    'missing_intent','changed_intent','returned_raw','returned_exit','wrong_initial'])
def test_extension_never_infers_owner_permission_or_discards_results(lost_update,change):
    store,request,root,old,successor,next_root,policy,extension,path=lost_update
    metadata={k:v for k,v in old.items() if k not in {'run_id','status','error'}}
    slot=next(v for v in old['call_reservations'].values() if v['role']=='gpt')
    stage=root/slot['round_id']
    if change=='missing_permission':
        auth_path=Path(successor.authorization.path);auth=json.loads(auth_path.read_text());auth.pop('owner_call_extension')
        auth_path.write_text(json.dumps(auth));successor.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
    else:
        if change=='metadata':extension['prior_metadata_sha256']='0'*64
        elif change=='budget':extension['budget']['gpt_calls']=1
        elif change=='quote':extension['authorization_quote']=''
        elif change=='predecessor':extension['predecessor_action_id']='different'
        elif change=='widen_permissions':successor.permissions[0].fields.append('claim_type')
        elif change=='missing_intent':(stage/'gpt.reservation.json').unlink()
        elif change=='changed_intent':(stage/'gpt.reservation.json').write_text('{}')
        elif change=='returned_raw':(stage/'gpt.raw.json').write_text('{}')
        elif change=='returned_exit':(stage/'gpt.transport').mkdir();(stage/'gpt.transport/exit.json').write_text('{}')
        elif change=='wrong_initial':policy.initial_candidate='existing'
        authorize(successor,policy,path,extension)
    with pytest.raises(ValueError):cm.continuation_budget(metadata,successor,policy)
    assert store.get_cvp_group(old['run_id'])==old


@pytest.mark.parametrize('change',['source','target','new_scope'])
def test_owner_extension_cannot_silently_cross_changed_sources_or_scope(lost_update,change):
    store,request,root,old,successor,next_root,policy,extension,path=lost_update
    if change=='source':
        source=Path(request.source_bindings['S'].path);source.write_text(source.read_text()+' ')
    elif change=='target':
        payload=deepcopy(store.get_record_state('claims','C')['payload']);payload['statement']='changed while paused'
        store.apply_plan(store.plan_package({'package_id':'target-drift','claims':[payload]}))
    else:
        seed_claims(store,['D'])
        store.apply_plan(store.plan_package({'package_id':'scope-drift','claim_relations':[
            {'claim_relation_id':'NEW','from_id':'D','to_id':'C','relation_type':'supports'}]}))
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    with pytest.raises((ValueError,ChangeSetConflict)):
        execute_claim_management(store,successor,root=next_root,ports=SyntheticPorts(store,next_root))
    assert store.get_cvp_group(old['run_id'])==old
    assert store.get_cvp_group('CMA-'+cm.sha256_json(successor.action_id)) is None
