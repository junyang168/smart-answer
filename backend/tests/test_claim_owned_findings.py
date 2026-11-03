"""A complete Update cannot defer defects in its own evidence as reference leads."""
import json
from pathlib import Path
import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_review_units import policy_file
from backend.tests.test_claim_direct_scope import chain


def own_issue(packet):
    result=pass_review(packet)
    result['findings']=[{'target_key':'evidence_steps/EC','category':'mapping','component':'own_evidence_mapping',
        'reason':'own evidence still needs source-supported maintenance',
        'acceptance_condition':'correct the own evidence summary',
        'affected_target_keys':[],'affected_write_keys':[],
        'impact_reason':'Claim sentences and own binding are sufficient; own Evidence still needs repair',
        'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]}]
    return result


def proposal(packet):
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    return {'changes':[{'record_key':'evidence_steps/EC',
        'fields_json':json.dumps({'statement':'甲是假设，不是断言。'}),
        'reason':'correct own data, retaining the Claim','citations':[citation]}],
        'creations':[],'retirements':[],'findings':[],
        'finding_resolutions':[{'finding_id':fid,'disposition':'resolved','reason':'own summary corrected',
            'citations':[citation],'affected_target_keys':[],'affected_write_keys':[],
            'impact_reason':'own data repaired without changing the Claim'} for fid in packet['unresolved']]}


def corrected_review(packet):
    result=pass_review(packet)
    result['prior_findings']=[{'finding_id':fid,'disposition':'resolved','reason':'verified corrected own summary',
        'affected_target_keys':[],'affected_write_keys':[], 'impact_reason':'own data repair verified',
        'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]}
        for fid in packet['unresolved']]
    return result


def creation_proposal(packet):
    from backend.api.canonical_repository.claim_identity_allocation import allocated_ids
    cid=allocated_ids(packet['original_goal']['claim_creation_allocations'])[0]
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    result=proposal(packet)
    result['changes']=[
        {'record_key':'evidence_steps/EC','fields_json':json.dumps({
            'statement':'甲是假设，不是断言。','produced_claim_ids':['C',cid]}),
         'reason':'retain the old owner and carry the independent judgement','citations':[citation]},
        {'record_key':'questions/Q','fields_json':json.dumps({'answer_claim_ids':['C',cid]}),
         'reason':'add the direct answer and retain the old answer','citations':[citation]}]
    result['creations']=[{'collection':'claims','object_id':cid,'fields_json':json.dumps({
        'statement':'甲不是断言。','claim_type':'exegesis','evidence_step_ids':['EC'],
        'occurrences':[{'source_id':'S','anchors':[]}]}),
        'reason':'independent source judgement lacked a Claim','citations':[citation]}]
    return result


def prepare(store,root):
    prepared=intake.prepare(['C','D'],root,instruction='Complete both Claims and their own data',
                            groups=[['C','D']],policy=policy_file(root.parent),store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    return next(iter(jobs.values()))


def test_own_evidence_defect_requires_correction_even_when_claim_sentence_is_sufficient(claims):
    store,root=claims;request,execution=prepare(store,root)
    def review(packet):
        return own_issue(packet) if not packet['unresolved'] else corrected_review(packet)
    ports=SyntheticPorts(store,execution,proposal=proposal,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus','gpt','opus']
    assert store.get_record_state('evidence_steps','EC')['payload']['statement']=='甲是假设，不是断言。'
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['C','D'])


def test_own_evidence_finding_reaches_actual_claim_creation_links_review_and_sql_readback(claims):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'direct-answer','questions':[
        {'question_id':'Q','text':'甲是断言吗？','answer_claim_ids':['C'],'answer_state':'answered'}]}))
    request,execution=prepare(store,root)
    before=store.get_record_state('claims','C')
    def review(packet):
        return own_issue(packet) if not packet['unresolved'] else corrected_review(packet)
    ports=SyntheticPorts(store,execution,proposal=creation_proposal,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus','gpt','opus']
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    assert len(parent['created_keys'])==1
    cid=parent['created_keys'][0].split('/',1)[1]
    assert store.get_record_state('claims',cid)['payload']['evidence_step_ids']==['EC']
    assert store.get_record_state('evidence_steps','EC')['payload']['produced_claim_ids']==['C',cid]
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==['C',cid]
    assert store.get_record_state('claims','C')==before
    assert store.claim_management_qualification(cid)['ready']
    assert all(store.claim_management_qualification(c)['ready'] for c in ['C','D'])


def test_unused_evidence_capability_does_not_make_reference_issue_an_owned_blocker(chain):
    store,request,_,root=chain
    request.policy=cm.source.FileBinding.model_validate(binding(policy_file(root)))
    request.permissions.append(cm.source.Permission(collection='evidence_steps',object_id='EB',fields=['statement']))
    before={cm.source.key(*k):v for k,v in store.read_claim_context([('claims','A')]).items()}
    candidate=cm.existing_candidate(before)
    finding={'target_key':'evidence_steps/EB','affected_target_keys':[],'affected_write_keys':[],
             'impact_reason':'unrelated reference-private issue'}
    assert not cm.finding_blocks(finding,request,candidate,records=before)


@pytest.mark.parametrize('key',['evidence_steps/EA','source_fragments/FA','claim_relations/AB'])
def test_own_argument_data_blocks_without_expanding_claim_review_membership(chain,key):
    store,request,_,root=chain
    if key=='source_fragments/FA':
        e=store.get_record_state('evidence_steps','EA')['payload']
        store.apply_plan(store.plan_package({'package_id':'own-fragment',
            'source_fragments':[{'fragment_id':'FA','source_id':'S','paragraph_key':'S0001',
                                'verbatim_excerpt':'甲是假设，不是断言。'}],
            'evidence_steps':[dict(e,source_fragment_ids=['FA'])]}))
    request.policy=cm.source.FileBinding.model_validate(binding(policy_file(root)))
    before={cm.source.key(*k):v for k,v in store.read_claim_context([('claims','A')]).items()}
    finding={'target_key':key,'affected_target_keys':[],'affected_write_keys':[],
             'impact_reason':'own argument data is incomplete, while the Claim sentence is faithful'}
    assert cm.finding_blocks(finding,request,cm.existing_candidate(before),records=before)
    from backend.pipeline.claim_direct_scope import scope
    assert scope(request,before)['content_claim_keys']==['claims/A']


def test_legacy_misclassified_own_issue_is_not_ready_and_context_cleanup_keeps_old_budget(claims,monkeypatch):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'own-question','questions':[
        {'question_id':'Q','text':'甲是什么？','answer_claim_ids':['C'],'answer_state':'answered'}]}))
    request,execution=prepare(store,root)
    original=cm.finding_blocks
    def legacy(finding,*args,**kwargs):
        return False if finding['target_key']=='evidence_steps/EC' else original(finding,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(cm,'finding_blocks',legacy)
        result=execute_claim_management(store,request,root=execution,
            ports=SyntheticPorts(store,execution,opus=own_issue))
    assert result['status']=='completed'
    assert not store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('D')['ready']
    old_id='CMA-'+cm.sha256_json(request.action_id);old=store.get_cvp_group(old_id)
    follow=root/'owned-follow-up';follow.mkdir()
    successor=request.model_copy(deep=True)
    successor.action_id+='-owned-follow-up';successor.predecessor_action_id=request.action_id
    successor.permissions=[p for p in successor.permissions if p.key=='evidence_steps/EC']
    successor.permissions.append(cm.source.Permission(collection='questions',object_id='Q',fields=['answer_claim_ids']))
    policy=cm.Policy.model_validate(old['policy']);policy.initial_candidate='proposal'
    policy.budget.gpt_calls=1;policy.budget.opus_calls=1;policy.budget.max_revisions=1
    pb=intake.immutable(follow/'policy.json',policy.model_dump(mode='json'))
    successor.policy=cm.source.FileBinding.model_validate(pb)
    from backend.api.canonical_repository.claim_identity_allocation import reserve
    allocations={'S':reserve(store,action_id=successor.action_id,source_id='S',target_id='C',root=follow/'execution')}
    auth={'schema_version':cm.VERSION,'authorized_by':'owner','human_content_approval':False,
        'authorization_quote':'Finish the saved own-data issue using only the original remaining budget',
        'request':successor.model_dump(mode='json',exclude={'authorization','policy'}),'policy':pb,
        'required_maintenance_keys':[],'claim_creation_allocations':allocations,
        'review_questions':['Finish the saved own-evidence issue; retain both Claim statements.']}
    successor.authorization=cm.source.FileBinding.model_validate(intake.immutable(follow/'authorization.json',auth))
    for index,permission in enumerate([
            cm.source.Permission(collection='questions',object_id='Q',fields=['text']),
            cm.source.Permission(collection='claims',object_id='C',fields=['statement'])]):
        invalid=successor.model_copy(deep=True)
        invalid.permissions=[p for p in successor.permissions if p.key=='evidence_steps/EC']+[permission]
        bad={**auth,'request':invalid.model_dump(mode='json',exclude={'authorization','policy'})}
        invalid.authorization=cm.source.FileBinding.model_validate(intake.immutable(follow/('invalid-%d.json'%index),bad))
        with pytest.raises(ValueError,match='completed follow-up'):
            cm.continuation_budget(old,invalid,policy)
    excessive=policy.model_copy(deep=True)
    excessive.budget.opus_calls=old['policy']['budget']['opus_calls']
    with pytest.raises(ValueError,match='reset or increase'):
        cm.continuation_budget(old,successor,excessive)
    ports=SyntheticPorts(store,follow/'execution',proposal=creation_proposal,opus=corrected_review)
    result=execute_claim_management(store,successor,root=follow/'execution',ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    saved=store.get_cvp_group('CMA-'+cm.sha256_json(successor.action_id))
    assert saved['continuation']['spent_generations']['opus']==1
    assert saved['continuation']['remaining_generations']['opus']==old['policy']['budget']['opus_calls']-1
    assert saved['policy']['budget']['opus_calls']==1
    assert len(saved['created_keys'])==1
    cid=saved['created_keys'][0].split('/',1)[1]
    assert store.claim_management_qualification(cid)['ready']
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==['C',cid]
    assert store.get_cvp_group(old_id)['result']==old['result']
    assert store.get_cvp_group(old_id)['context_findings']==old['context_findings']
    assert all(store.claim_management_qualification(cid)['ready'] for cid in ['C','D'])
