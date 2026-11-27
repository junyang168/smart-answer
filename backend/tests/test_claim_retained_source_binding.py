"""A historical false no-write completion is rejected and recovered once in SQL."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_evidence_review as evidence
from backend.pipeline import claim_review_units as units, claim_management_batch as batch
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_intake import pass_review


@pytest.fixture
def false_completion(action, monkeypatch):
    store, _, _, _, tmp, _ = action
    store.apply_plan(store.plan_package({'package_id':'old-anchor',
        'claims':[{'claim_id':'C','statement':'甲是假设。','claim_type':'exegesis',
                   'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E']}],
        'evidence_steps':[{'evidence_step_id':'E','statement':'甲是假设。','produced_claim_ids':['C'],
                          'source_fragment_ids':['F'],'speaker':'professor','stance':'asserted',
                          'support_eligibility':'eligible_candidate'}],
        'source_fragments':[{'fragment_id':'F','source_id':'S','paragraph_key':'S9999',
                             'verbatim_excerpt':'甲是假设，不是断言。'}]}))
    prepared=units.prepare(['C'],tmp/'unit',instruction='Refresh own evidence',store=store)
    request,root=next(iter(batch.load(prepared['prepared_update']['manifest']['path'],tmp/'batch',store=store)[1].values()))
    ports=SyntheticPorts(store,root,opus=pass_review)
    with monkeypatch.context() as old:
        # Reproduce the previous validator's missing retained-binding check.
        old.setattr(evidence,'assert_current_fragments',lambda *a,**k:None)
        assert execute_claim_management(store,request,root=root,ports=ports)['status']=='completed'
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    prior={k:v for k,v in parent.items() if k not in {'run_id','status','error'}}
    rid=next(iter(prior['latest_rounds'].values()))
    return store,request,root,prior,rid,tmp


def repair_direction(state):
    store,old,root,prior,rid,tmp=state
    policy=cm.Policy.model_validate(prior['policy'])
    policy.initial_candidate='proposal';policy.budget.gpt_calls=policy.budget.opus_calls=1;policy.budget.max_revisions=1
    path=tmp/'repair-policy.json';path.write_text(policy.model_dump_json())
    request=old.model_copy(deep=True);request.action_id+='-binding-repair';request.predecessor_action_id=old.action_id
    request.policy=cm.source.FileBinding.model_validate(binding(path))
    auth=json.loads(Path(old.authorization.path).read_text())
    auth.pop('reuse_initial_claim_assessments',None)
    paths=('backend/pipeline/claim_evidence_review.py','backend/pipeline/claim_model_output.py',
           'backend/pipeline/claim_management.py','backend/pipeline/claim_review_reuse.py',
           'backend/api/canonical_repository/postgres_store.py')
    implementation={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
    report=tmp/'tests.json';report.write_text(json.dumps({'defect':evidence.RETAINED_BINDING_DEFECT,
                                                       'implementation':implementation,'passed':1,'failed':0}))
    stage=root/rid
    proof={'defect':evidence.RETAINED_BINDING_DEFECT,'predecessor_action_id':old.action_id,
           'prior_metadata_sha256':cm.sha256_json(prior),'round_id':rid,'fixed_commit':'a'*40,'fixed_parent':'b'*40,
           'implementation':implementation,'test_evidence':binding(report),
           'saved_inputs':{name:binding(stage/(name+'.json'))
                          for name in ('materials','candidate','gpt.raw','opus.raw','readback','package')
                          if (stage/(name+'.json')).exists()}}
    # Initial-candidate completion has no GPT call; a completed proposal does.
    auth.update(request=request.model_dump(mode='json',exclude={'authorization','policy'}),policy=binding(path),
                own_source_binding_repair=proof,
                owner_call_extension={'basis':'remaining_findings','predecessor_action_id':old.action_id,
                    'prior_metadata_sha256':cm.sha256_json(prior),'budget':policy.budget.model_dump(mode='json'),
                    'authorization_quote':'工程修复后大模型调动应该自动增加一次',
                    'current_objects_sha256':cm.sha256_json(store.get_cvp_group(rid)['materials'].get('transaction_before',store.get_cvp_group(rid)['materials']['before'])),
                    'related_action_metadata_sha256':{}})
    path=tmp/'repair-authority.json';path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(path))
    return request,policy,auth,path


def test_current_qualification_and_completion_reject_historical_false_pass(false_completion):
    store,request,root,prior,rid,tmp=false_completion
    assert store.claim_management_qualification('C')['reason']=='current_own_source_bindings_invalid'
    with pytest.raises(ValueError,match='exact current source'):
        store.finish_claim_management_action('CMA-'+cm.sha256_json(request.action_id))
    from backend.pipeline.claim_review_reuse import build
    with pytest.raises(ValueError,match='exact current source'):
        build(store,'C',rid)
    assert store.get_cvp_group(rid)['outcome']=='completed'  # immutable historical verdict


@pytest.mark.parametrize('bad',['committed','source','budget','used','proof','valid_binding'])
def test_repair_cannot_become_a_general_retry(false_completion,bad):
    store,old,root,prior,rid,tmp=false_completion
    request,policy,auth,path=repair_direction(false_completion)
    if bad=='committed':prior['created_keys']=['claims/OTHER']
    if bad=='source':request.source_bindings={}
    if bad=='budget':policy.budget.gpt_calls=2
    if bad=='used':prior['original_goal']['engineering_repair_defects']=[evidence.RETAINED_BINDING_DEFECT]
    if bad=='proof':auth['own_source_binding_repair']['saved_inputs']={}
    if bad=='valid_binding':auth['own_source_binding_repair']['defect']='other'
    with pytest.raises(ValueError):evidence.repair_extension(prior,request,policy,auth)


def test_one_linked_engineering_pair_repairs_sql_and_preserves_old_verdict(false_completion):
    store,old,root,prior,rid,tmp=false_completion
    request,policy,auth,path=repair_direction(false_completion)
    from backend.api.canonical_repository.claim_identity_allocation import reserve,allocated_ids
    allocations={}
    for sid,grant in auth.get('claim_creation_allocations',{}).items():
        allocations[sid]=reserve(store,action_id=request.action_id,source_id=sid,target_id='C',
                                 root=tmp/'fixed-execution',count=len(allocated_ids({sid:grant})))
    auth['claim_creation_allocations']=allocations
    path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(path))
    accounting=cm.continuation_budget(prior,request,policy)
    assert accounting['completed_no_write_binding_repair']
    assert accounting['spent_generations']=={'gpt':0,'opus':1,'gemini':0}
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def propose(packet):
        return {'changes':[{'record_key':'source_fragments/F','fields_json':'{"paragraph_key":"S0001"}',
                            'reason':'restore exact own source binding','citations':[citation]}],
                'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    ports=SyntheticPorts(store,tmp/'fixed-execution',proposal=propose,opus=pass_review)
    result=execute_claim_management(store,request,root=tmp/'fixed-execution',ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    assert store.claim_management_qualification('C')['ready']
    assert store.get_record_state('source_fragments','F')['payload']['paragraph_key']=='S0001'
    historical=store.get_cvp_group(rid)
    assert historical['outcome']=='completed' and historical['candidate']['records']==[]
    previous=store.get_cvp_group('CMA-'+cm.sha256_json(old.action_id))
    assert previous['continued_by']=='CMA-'+cm.sha256_json(request.action_id)
    current=store.get_cvp_group(previous['continued_by'])
    assert evidence.RETAINED_BINDING_DEFECT in current['continuation']['engineering_repair_defects']
    with pytest.raises(ValueError):evidence.repair_extension(
        {**prior,'original_goal':{**prior['original_goal'],'engineering_repair_defects':[evidence.RETAINED_BINDING_DEFECT]}},
        request,policy,auth)
