from copy import deepcopy
from pathlib import Path
import json
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,SyntheticPorts
from backend.tests.test_claim_joint_update import shared,bind,joint_pass,joint_reject,resolve,update
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.mark.parametrize('drift',['none','target','missing_target_pin','unverified_readback'])
def test_completed_update_refresh_uses_verified_postimage_and_rejects_later_target_drift(shared,drift):
    store,old,root,tmp=shared
    bind(old,tmp,required=[],initial='proposal')
    def propose(packet):
        value=cm.Candidate.model_validate(update(packet,['claims/A','evidence_steps/E']))
        value.finding_resolutions=resolve(packet,disposition='persistent')
        return value.model_dump(mode='json')
    def review(packet):
        if not packet['round_number']:return joint_reject(packet,'claims/C',affected=[])
        result=joint_pass(packet)
        for value in result['prior_findings']:value.update(disposition='persistent',affected_target_keys=[],affected_write_keys=[])
        for value in result['verdicts']:
            if value['record_key']=='claims/C':value.update(approved=False,problems=['unrelated context retained'])
        return result
    assert execute_claim_management(store,old,root=root,ports=SyntheticPorts(store,tmp,proposal=propose,opus=review))['status']=='completed'
    assert store.get_record_state('claims','A')['revision']==2
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    assert parent['original_goal']['original_objects']['claims/A']['revision']==1
    payload=deepcopy(store.get_record_state('evidence_steps','E')['payload']);payload['statement']+=' 后续补充'
    store.apply_plan(store.plan_package({'package_id':'later-context','evidence_steps':[payload]}))
    if drift=='target':
        payload=deepcopy(store.get_record_state('claims','A')['payload']);payload['statement']+=' another edit'
        store.apply_plan(store.plan_package({'package_id':'later-target','claims':[payload]}))
    if drift in {'missing_target_pin','unverified_readback'}:
        rid=next(iter(parent['latest_rounds'].values()));child=store.get_cvp_group(rid)
        metadata={k:v for k,v in child.items() if k not in {'run_id','status','error'}}
        if drift=='missing_target_pin':metadata['audited_pins']=[r for r in metadata['audited_pins'] if (r['collection'],r['object_id'])!=('claims','A')]
        else:metadata['program_readback_complete']=False
        with store.connect() as conn:conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(metadata),rid))
    request,next_root=operation_request((store,old,None,None,tmp,None),'Update',['A','B']);request.predecessor_action_id=old.action_id;request.permissions=[]
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text());policy.initial_candidate='proposal'
    records=store.read_claim_context([cm.source.split_key(k) for k in request.target_keys]);before={cm.source.key(*k):cm.model_state(v) for k,v in records.items()}
    metadata={k:v for k,v in parent.items() if k not in {'run_id','status','error'}}
    ext={'predecessor_action_id':old.action_id,'prior_metadata_sha256':cm.sha256_json(metadata),'budget':policy.budget.model_dump(mode='json'),'authorization_quote':'refresh current original targets without writes','basis':'qualification_refresh','current_objects_sha256':cm.sha256_json(before),'related_action_metadata_sha256':{}}
    authorize(request,policy,next_root.with_suffix('.policy.json'),ext)
    def decisions(packet):
        return [dict(finding_id=fid,disposition='persistent',reason='unchanged independent context',citations=f['citations'],affected_target_keys=[],affected_write_keys=[],impact_reason='not used by either target') for fid,f in packet['unresolved'].items()]
    ports=SyntheticPorts(store,next_root,proposal=lambda p:{'changes':[],'findings':[],'finding_resolutions':decisions(p)},
        opus=lambda p:{'complete_review':True,'findings':[],'prior_findings':decisions(p)})
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    if drift=='none':
        assert execute_claim_management(store,request,root=next_root,ports=ports)['status']=='completed'
        assert ports.calls==['gpt','opus'] and store.get_record_state('claims','A')['revision']==2
        assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B')['ready']
    else:
        with pytest.raises(ChangeSetConflict):execute_claim_management(store,request,root=next_root,ports=ports)
        assert ports.calls==[]
    assert store.get_cvp_group(parent['run_id'])['call_reservations']==parent['call_reservations']
