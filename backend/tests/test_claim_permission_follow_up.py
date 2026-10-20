"""A known relation permission gap can carry its candidate into one new pair."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_direct_scope import chain
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.fixture
def paused(chain):
    store, old, root, tmp = chain
    policy = cm.Policy.model_validate_json(Path(old.policy.path).read_text())
    policy.initial_candidate = 'proposal'
    policy.budget.gpt_calls = policy.budget.opus_calls = 1
    policy.budget.max_revisions = 1
    authorize(old, policy, tmp/'old-policy.json')
    citation = {'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    finding = {'target_key':'claim_relations/AB','component':'reason','category':'mapping',
        'reason':'relation explanation needs correction','acceptance_condition':'correct reason',
        'citations':[citation], 'affected_target_keys':['claims/A'],'affected_write_keys':[],
        'impact_reason':'A is supported by this relation'}
    change = {'record_key':'claims/A','fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),
        'reason':'preserve source qualification','citations':[citation]}
    def resolutions(packet, disposition):
        return [{'finding_id':fid,'disposition':disposition,'reason':'source supports correction',
            'citations':[citation],'affected_target_keys':['claims/A'],'affected_write_keys':[],
            'impact_reason':'A is supported by this relation'} for fid in packet['unresolved']]
    ports = SyntheticPorts(store,tmp,
        proposal=lambda p:{'changes':[change],'findings':[finding],'finding_resolutions':[]},
        opus=lambda p:{'complete_review':True,'findings':[],'prior_findings':resolutions(p,'persistent')})
    result = execute_claim_management(store,old,root=root,ports=ports)
    assert result['reason']=='permission_required', result
    parent = store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    successor = old.model_copy(deep=True)
    successor.action_id += '-explicit-permission'
    successor.predecessor_action_id = old.action_id
    successor.permissions.append(cm.source.Permission(collection='claim_relations',object_id='AB',fields=['reason']))
    successor.authorization = cm.source.FileBinding(path=str(tmp/'follow-authority.json'),file_sha256='a'*64)
    Path(successor.authorization.path).write_text(json.dumps({'schema_version':cm.VERSION,'authorized_by':'owner','human_content_approval':False,'authorization_quote':'将这个 claim 完整处理一下'}))
    before = {cm.source.key(*k):cm.model_state(v) for k,v in store.read_claim_context(
        [('claims','A'),('claim_relations','AB')]).items()}
    metadata = {k:v for k,v in parent.items() if k not in {'run_id','status','error'}}
    extension = {'basis':'permission_required','predecessor_action_id':old.action_id,
        'prior_metadata_sha256':cm.sha256_json(metadata),'budget':policy.budget.model_dump(mode='json'),
        'authorization_quote':'将这个 claim 完整处理一下','current_objects_sha256':cm.sha256_json(before),
        'related_action_metadata_sha256':{}}
    path = tmp/'follow-policy.json'; authorize(successor,policy,path,extension)
    return store,old,parent,successor,tmp,policy,extension,path,change,resolutions


@pytest.mark.parametrize('fault',[None,'missing_extension','extra_budget','unreported_write','changed_input'])
def test_permission_follow_up_preserves_old_calls_and_candidate_and_checks_promoted_scope(paused,fault):
    store,old,parent,request,tmp,policy,extension,path,change,resolutions = paused
    if fault=='missing_extension':
        p=Path(request.authorization.path);a=json.loads(p.read_text());a.pop('owner_call_extension')
        p.write_text(json.dumps(a));request.authorization=cm.source.FileBinding.model_validate(binding(p))
    elif fault=='extra_budget':
        policy.budget.gpt_calls=2;extension['budget']=policy.budget.model_dump(mode='json')
        authorize(request,policy,path,extension)
    elif fault=='unreported_write':
        request.permissions.append(cm.source.Permission(collection='evidence_steps',object_id='EB',fields=['statement']))
        authorize(request,policy,path,extension)
    elif fault=='changed_input':
        e=deepcopy(store.get_record_state('evidence_steps','EB')['payload']);e['statement']='changed'
        store.apply_plan(store.plan_package({'package_id':'drift','evidence_steps':[e]}))
    def propose(packet):
        assert 'evidence_steps/EB' in packet['before']
        assert packet['previous_candidate']['records'][0]['payload']['statement']=='甲是假设，不是断言。'
        return {'changes':[change,{'record_key':'claim_relations/AB',
            'fields_json':json.dumps({'reason':'甲是假设，不是断言。'}),'reason':'correct explanation',
            'citations':change['citations']}],'findings':[],'finding_resolutions':resolutions(packet,'resolved')}
    ports=SyntheticPorts(store,tmp,proposal=propose,
        opus=lambda p:{'complete_review':True,'findings':[],'prior_findings':resolutions(p,'resolved')})
    if fault:
        from backend.api.canonical_repository.postgres_store import ChangeSetConflict
        if fault == 'unreported_write':
            result = execute_claim_management(store,request,root=tmp/'follow',ports=ports)
            assert result['status']=='paused' and result['reason']=='engineering_required'
        else:
            with pytest.raises((ValueError,ChangeSetConflict)):
                execute_claim_management(store,request,root=tmp/'follow',ports=ports)
        assert not ports.calls
        assert store.get_cvp_group(parent['run_id'])==parent
    else:
        result=execute_claim_management(store,request,root=tmp/'follow',ports=ports)
        assert result['status']=='completed',result
        assert ports.calls==['gpt','opus']
        assert store.claim_management_qualification('A')['ready']
        assert store.get_cvp_group(parent['run_id'])['call_reservations']==parent['call_reservations']
        assert execute_claim_management(store,request,root=tmp/'follow',ports=ports)==result
        assert ports.calls==['gpt','opus']
