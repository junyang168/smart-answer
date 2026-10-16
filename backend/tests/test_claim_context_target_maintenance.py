"""A saved context objection may become an explicit writable Claim target."""
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,SyntheticPorts
from backend.tests.test_claim_joint_update import shared
from backend.tests.test_claim_owner_call_extension import owner_review_follow_up,authorize,decision


@pytest.mark.parametrize('owner_review_follow_up',['qualification_refresh'],indirect=True)
@pytest.mark.parametrize('bind_maintenance',[False,True])
def test_saved_claim_objection_requires_explicit_target_and_exact_required_maintenance(owner_review_follow_up,bind_maintenance):
    store,old,parent,_,_,_,_,_,_=owner_review_follow_up
    root=Path(parent['execution_root']).parent
    request,next_root=operation_request((store,old,None,None,root,None),'Update',['C'])
    request.permissions.append(cm.source.Permission(collection='evidence_steps',object_id='E',fields=['statement']))
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text());policy.initial_candidate='proposal'
    authorize(request,policy,next_root.with_suffix('.policy.json'))
    auth_path=Path(request.authorization.path);auth=json.loads(auth_path.read_text())
    auth.update(required_maintenance_keys=['evidence_steps/E'] if bind_maintenance else [],context_review_actions={old.action_id:list(parent['context_findings'])})
    auth_path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
    original={cid:store.get_record_state('claims',cid) for cid in ['A','B','C']}
    def decisions(packet):
        return [dict(decision(fid),affected_target_keys=['claims/C'],affected_write_keys=['claims/C']) for fid in packet['unresolved']]
    def propose(packet):
        assert packet['unresolved']==parent['context_findings']
        return {'changes':[{'record_key':'claims/C','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),'reason':'correct the saved finding','citations':decision('unused')['citations']}],
                'findings':[],'finding_resolutions':decisions(packet)}
    ports=SyntheticPorts(store,next_root,proposal=propose,opus=lambda p:{'complete_review':True,'findings':[],'prior_findings':decisions(p)})
    if not bind_maintenance:
        from backend.api.canonical_repository.postgres_store import ChangeSetConflict
        with pytest.raises(ChangeSetConflict,match='unrelated writes'):execute_claim_management(store,request,root=next_root,ports=ports)
        assert ports.calls==[] and store.get_cvp_group(parent['run_id'])==parent
        return
    result=execute_claim_management(store,request,root=next_root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus'] and store.claim_management_qualification('C')['ready']
    assert store.get_record_state('claims','C')['revision']==original['C']['revision']+1
    assert all(store.get_record_state('claims',cid)==original[cid] for cid in ['A','B'])
    prior=store.get_cvp_group(parent['run_id']);assert prior['call_reservations']==parent['call_reservations']
    assert prior['context_maintained_by']=='CMA-'+cm.sha256_json(request.action_id)


@pytest.mark.parametrize('change_target',[False,True])
def test_readonly_permission_refusal_can_handoff_all_findings_to_new_exact_scope(action,change_target):
    from backend.tests.test_claim_management_execution_postgres import seed_claims
    store,_,_,_,root,_=action;seed_claims(store,['C','D'])
    old,oldroot=operation_request(action,'Update',['C']);old.permissions=[]
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text())
    policy.initial_candidate='proposal'
    authorize(old,policy,root/'readonly-handoff-policy.json')
    def reject(packet):
        return {'complete_review':True,'findings':[{'target_key':'claims/C','component':'statement','category':'content',
            'reason':'synthetic clarification is needed','acceptance_condition':'correct wording','human_question':None,
            'citations':decision('unused')['citations'],'affected_target_keys':['claims/C'],
            'affected_write_keys':[],'impact_reason':'the explicit target wording is wrong'}],'prior_findings':[]}
    def initial(packet):
        return {'changes':[],'findings':reject(packet)['findings'] if not packet['unresolved'] else [],
            'finding_resolutions':[dict(decision(fid),disposition='persistent',affected_target_keys=['claims/C'],
                affected_write_keys=[],impact_reason='the target wording needs correction') for fid in packet['unresolved']]}
    def confirm(packet):
        return {'complete_review':True,'findings':[],'prior_findings':[dict(decision(fid),
            disposition='persistent',affected_target_keys=['claims/C'],affected_write_keys=[],
            impact_reason='the target wording needs correction') for fid in packet['unresolved']]}
    result=execute_claim_management(store,old,root=oldroot,ports=SyntheticPorts(store,root,proposal=initial,opus=confirm))
    assert result['reason']=='permission_required',result
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    request,newroot=operation_request(action,'Update',['D' if change_target else 'C'])
    policy.initial_candidate='proposal';authorize(request,policy,root/'writable-handoff-policy.json')
    path=Path(request.authorization.path);auth=json.loads(path.read_text());auth.update(
        required_maintenance_keys=[],context_review_actions={old.action_id:list(parent['findings'])})
    path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(path))
    def resolutions(packet):
        return [dict(decision(fid),disposition='resolved') for fid in packet['unresolved']]
    def propose(packet):
        assert packet['unresolved']==parent['findings']
        return {'changes':[{'record_key':'claims/C','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),
            'reason':'correct all imported findings','citations':decision('unused')['citations']}],
            'findings':[],'finding_resolutions':resolutions(packet)}
    ports=SyntheticPorts(store,root,proposal=propose,opus=lambda p:{'complete_review':True,'findings':[],'prior_findings':resolutions(p)})
    if change_target:
        from backend.api.canonical_repository.postgres_store import ChangeSetConflict
        with pytest.raises(ChangeSetConflict):execute_claim_management(store,request,root=newroot,ports=ports)
        assert ports.calls==[] and store.get_cvp_group(parent['run_id'])==parent
    else:
        result=execute_claim_management(store,request,root=newroot,ports=ports)
        assert result['status']=='completed',result
        assert store.claim_management_qualification('C')['ready']
        prior=store.get_cvp_group(parent['run_id']);assert prior['call_reservations']==parent['call_reservations']
        assert prior['result']==parent['result']
