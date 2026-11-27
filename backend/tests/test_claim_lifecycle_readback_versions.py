"""A second retirement must verify the preserved payload, not its storage age."""
from copy import deepcopy
from pathlib import Path
import pytest

from backend.api.canonical_repository.postgres_store import build_revival_plan
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request, seed_claims, SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.mark.parametrize('fault',[None,'payload','revision','sha','retired','missing'])
def test_retire_revive_retire_reads_exact_payload_and_storage_versions(action,monkeypatch,fault):
    store=action[0];seed_claims(store,['C'])
    original=deepcopy(store.get_record_state('claims','C')['payload'])
    keys=[('claims','C')]
    store.apply_plan(store.plan_retirement(keys,reason='synthetic earlier withdrawal',package_id='first-retirement'))
    store.apply_plan(build_revival_plan(keys,{keys[0]:store.get_record_state(*keys[0])},
        reason='synthetic earlier recovery',package_id='restore-original-payload'))
    getter=store.get_record_state
    assert getter(*keys[0])['revision']==3 and getter(*keys[0])['payload']==original
    request,root=operation_request(action,'Retire',['C'])
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_opus_only_v1.json').read_text())
    authorize(request,policy,root.with_suffix('.policy.json'))
    def propose(packet):
        candidate=cm.existing_candidate(packet['before'])
        candidate.dispositions=[d for d in candidate.dispositions if d.record_key!='claims/C']+[
            cm.ObjectDisposition(record_key='claims/C',action='retire',reason='synthetic retirement',
                citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')])]
        return candidate.model_dump(mode='json')
    if fault:
        def corrupted(collection,object_id):
            state=getter(collection,object_id)
            if collection!='claims' or object_id!='C' or not state or not state['retired']:return state
            value=deepcopy(state)
            if fault=='missing':return None
            if fault=='payload':value['payload']['statement']='unexpected mutation'
            if fault=='revision':value['revision']+=1
            if fault=='sha':value['content_sha256']='0'*64
            if fault=='retired':value['retired']=False
            return value
        monkeypatch.setattr(store,'get_record_state',corrupted)
    ports=SyntheticPorts(store,root,proposal=propose)
    result=execute_claim_management(store,request,root=root,ports=ports)
    if fault:
        assert result['status']=='paused' and result['reason']=='engineering_required',result
        assert 'postimage' in result['detail']
    else:
        assert result['status']=='completed',result
        before_calls=list(ports.calls)
        assert execute_claim_management(store,request,root=root,ports=ports)==result
        assert ports.calls==before_calls
    actual=getter(*keys[0])
    assert actual['payload']==original and actual['revision']==4 and actual['retired']
    assert ports.calls==['gpt','opus']
