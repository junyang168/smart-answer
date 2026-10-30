from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_model_output as wire
from backend.tests.test_claim_management import case
from backend.tests.test_claim_joint_update import shared, action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_human_source_follow_up import authorize
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.cvp_sql_actions import execute_claim_management


@pytest.fixture
def material(case):
    request,before,_,sources=case
    before=deepcopy(before)
    before['source_documents/S']={'payload':{'source_id':'S','source_type':'sermon_transcript'},'retired':False}
    return {'policy':{'model_output_contract':wire.CONTRACT},'unit_request':request.model_dump(mode='json'),
        'before':deepcopy(before),'sources':sources,'replacements':{},'unresolved':{},
        'original_goal':{'operation':'Update','target_keys':request.target_keys,'required_questions':sorted(cm.QUESTIONS['Update'])}}


def empty():return {'changes':[], 'findings':[], 'finding_resolutions':[]}


def change(key='claims/C',fields=None):
    return {'record_key':key,'fields_json':json.dumps(fields or {'statement':'乙只在特定情况下成立。'}),
        'reason':'supported correction','citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'乙只在特定情况下成立。'}]}


def test_no_change_requires_no_ids_or_source_fragment_echo(material):
    result,proof=wire.candidate(empty(),material)
    post,writes=cm.validate_candidate(cm.Request.model_validate(material['unit_request']),material['before'],result,material['sources'])
    assert not writes and post=={k:v['payload'] for k,v in material['before'].items()}
    assert {d.record_key for d in result.dispositions}==set(material['before'])
    assert proof['changed_keys']==[]


def test_field_patch_preserves_every_other_field_and_all_readonly_records(material):
    original=deepcopy(material)
    raw={**empty(),'changes':[change()]}
    candidate,proof=wire.candidate(raw,material)
    post,writes=cm.validate_candidate(cm.Request.model_validate(material['unit_request']),material['before'],candidate,material['sources'])
    assert set(writes)=={'claims/C'}
    assert post['claims/C']=={**original['before']['claims/C']['payload'],'statement':'乙只在特定情况下成立。'}
    assert post['source_fragments/F']==original['before']['source_fragments/F']['payload']
    assert material==original and proof['model_response_sha256']==cm.sha256_json(raw)


@pytest.mark.parametrize('bad', ['duplicate','absent','readonly','field','identity','provenance','hash','empty','duplicate_field','unchanged'])
def test_compact_output_cannot_expand_authority_or_hide_corruption(material,bad):
    c=change();raw={**empty(),'changes':[c]}
    if bad=='duplicate':raw['changes'].append(deepcopy(c))
    if bad=='absent':c['record_key']='claims/ABSENT'
    if bad=='readonly':c['record_key']='evidence_steps/E'
    if bad=='field':c['fields_json']='{"not_authorized":"x"}'
    if bad=='identity':c['fields_json']='{"claim_id":"other"}'
    if bad=='provenance':c['fields_json']='{"review_status":"approved"}'
    if bad=='hash':c['fields_json']='{"source_sha256":"invented"}'
    if bad=='empty':c['fields_json']='{}'
    if bad=='duplicate_field':c['fields_json']='{"statement":"a","statement":"b"}'
    if bad=='unchanged':c['fields_json']=json.dumps({'statement':material['before']['claims/C']['payload']['statement']})
    with pytest.raises(ValueError):
        candidate,_=wire.candidate(raw,material)
        cm.validate_candidate(cm.Request.model_validate(material['unit_request']),material['before'],candidate,material['sources'])


def test_review_explicit_whole_scope_approval_expands_hash_questions_and_verdicts(material):
    candidate,_=wire.candidate(empty(),material)
    review=wire.review({'complete_review':True,'findings':[],'prior_findings':[]},material,candidate)
    assert review.proposal_sha256==candidate.digest
    assert set(v.record_key for v in review.verdicts)==set(material['before'])
    assert all(v.approved for v in review.verdicts)
    assert cm.validate_review(candidate,review,set(material['before']),material['original_goal']['required_questions'],{},material['sources'])
    for raw in [{'findings':[],'prior_findings':[]},{'complete_review':False,'findings':[],'prior_findings':[]}]:
        with pytest.raises(ValueError):wire.review(raw,material,candidate)


def test_review_findings_remain_rejections_and_prior_resolution_is_required(material):
    candidate,_=wire.candidate(empty(),material)
    finding={'target_key':'claims/C','component':'statement','category':'content','reason':'real issue',
        'acceptance_condition':'repair from source','human_question':None,'citations':change()['citations'],
        'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'changes target assertion'}
    review=wire.review({'complete_review':True,'findings':[finding],'prior_findings':[]},material,candidate)
    assert next(v for v in review.verdicts if v.record_key=='claims/C').approved is False
    assert not cm.validate_review(candidate,review,set(material['before']),material['original_goal']['required_questions'],{},material['sources'])
    material['unresolved']={'F':finding}
    review=wire.review({'complete_review':True,'findings':[],'prior_findings':[]},material,candidate)
    with pytest.raises(ValueError,match='every prior'):cm.validate_review(candidate,review,set(material['before']),material['original_goal']['required_questions'],material['unresolved'],material['sources'])


@pytest.mark.parametrize('mode',['retain','patch','reject'])
def test_compact_output_real_sql_gates_and_cached_completion(shared,mode):
    store,request,root,tmp=shared
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text())
    policy.initial_candidate='proposal'
    if mode=='reject':policy.budget.max_revisions=0
    path=tmp/'compact-policy.json';path.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(path));authorize(request,{'required_maintenance_keys':['evidence_steps/E']})
    def propose(packet):
        c=change('evidence_steps/E',{'statement':'甲是假设，不是断言。'});c['citations'][0]['exact_quote']='甲是假设，不是断言。'
        return {**empty(),'changes':[c] if mode=='patch' else []}
    def opus(packet):
        assert 'candidate_sha256' not in packet
        assert all(d['action']!='retain' for d in packet['candidate']['dispositions'])
        assert 'claims/C' in packet['before'] and 'claims/C' in packet['denominator']
        findings=[]
        if mode=='reject':
            findings=[{'target_key':'claims/A','component':'statement','category':'source_uncertain','reason':'source unresolved',
              'acceptance_condition':'verify source','human_question':'Who said this?', 'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}],
              'affected_target_keys':['claims/A'],'affected_write_keys':[],'impact_reason':'target attribution cannot pass'}]
        return {'complete_review':True,'findings':findings,'prior_findings':[]}
    ports=SyntheticPorts(store,tmp,proposal=propose,opus=opus)
    before=store.get_record_state('claims','C')
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert store.get_record_state('claims','C')==before
    if mode=='reject':assert result['status']=='paused'
    else:
        assert result['status']=='completed',result
        assert store.claim_management_qualification('A')['ready']
        assert store.claim_management_qualification('B')['ready']
        calls=list(ports.calls)
        assert execute_claim_management(store,request,root=root,ports=ports)==result
        assert ports.calls==calls
    assert ports.calls==['gpt','opus']


def test_legacy_policy_schema_and_old_reply_interpretation_stay_exact(material):
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v2.json').read_text())
    assert 'model_output_contract' not in policy.model_dump(mode='json')
    material['policy']=policy.model_dump(mode='json')
    legacy=cm.existing_candidate(material['before']).model_dump(mode='json')
    expected=cm.normalize_candidate(legacy,material['sources'])
    assert wire.candidate(legacy,material)==expected


def test_compact_review_cannot_pass_write_that_hurts_readonly_owner(shared):
    store,request,root,tmp=shared
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text())
    policy.initial_candidate='proposal';policy.budget.max_revisions=0
    path=tmp/'compact-owner-policy.json';path.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(path));authorize(request,{'required_maintenance_keys':['evidence_steps/E']})
    before=store.get_record_state('evidence_steps','E')
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def propose(packet):return {**empty(),'changes':[dict(change('evidence_steps/E',{'statement':'甲是假设，不是断言。'}),citations=[citation])]}
    def opus(packet):return {'complete_review':True,'prior_findings':[],'findings':[{
        'target_key':'claims/C','component':'shared_evidence','category':'evidence','reason':'shared write loses readonly support',
        'acceptance_condition':'preserve all owners','human_question':None,'citations':[citation],
        'affected_target_keys':[],'affected_write_keys':['evidence_steps/E'],'impact_reason':'actual shared write breaks readonly owner'}]}
    ports=SyntheticPorts(store,tmp,proposal=propose,opus=opus)
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']=='paused'
    assert store.get_record_state('evidence_steps','E')==before
    assert ports.calls==['gpt','opus']


def test_all_context_findings_still_go_to_gpt_without_promoting_bystanders(shared):
    store,request,root,tmp=shared
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v3.json').read_text())
    path=tmp/'compact-context-policy.json';path.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(path));authorize(request,{'required_maintenance_keys':['evidence_steps/E']})
    before=store.get_record_state('claims','C')
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def opus(packet):
        issue={'target_key':'claims/C','component':'independent_issue','category':'content','reason':'unrelated readonly issue',
            'acceptance_condition':'a separate authorized cleanup','human_question':None,'citations':[citation],
            'affected_target_keys':[],'affected_write_keys':[],'impact_reason':'no impact on targets, actual writes or required maintenance'}
        prior=[{'finding_id':fid,'disposition':'persistent','reason':'confirmed but outside this write scope','citations':[citation],
            'affected_target_keys':[],'affected_write_keys':[],'impact_reason':issue['impact_reason']} for fid in packet['unresolved']]
        return {'complete_review':True,'findings':[] if prior else [issue],'prior_findings':prior}
    def propose(packet):
        assert len(packet['unresolved'])==1
        prior=[{'finding_id':fid,'disposition':'persistent','reason':'source-supported issue remains readonly','citations':[citation],
            'affected_target_keys':[],'affected_write_keys':[],'impact_reason':'does not affect target or writes'} for fid in packet['unresolved']]
        return {**empty(),'finding_resolutions':prior}
    ports=SyntheticPorts(store,tmp,proposal=propose,opus=opus)
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus','gpt','opus']
    assert store.get_record_state('claims','C')==before
    assert not (store.claim_management_qualification('C') or {}).get('ready')


def creating(material):
    """#459: a judgement only an Evidence step carries gets its own Claim in compact output."""
    request=cm.Request.model_validate(material['unit_request'])
    request.allowed_operations=['Update','Create']
    request.permissions.append(cm.source.Permission(collection='evidence_steps',object_id='E',fields=['produced_claim_ids']))
    fields=sorted({'occurrences'}|set(cm.KNOWLEDGE_COLLECTIONS['claims'][0].model_fields)-{'claim_id','review_status','revision'})
    request.creation_permissions=[cm.CreationPermission(collection='claims',source_id='S',fields=fields)]
    return {**material,'policy':{'model_output_contract':wire.CREATING_CONTRACT},'unit_request':request.model_dump(mode='json')}


def creation(object_id='C2'):
    return {'collection':'claims','object_id':object_id,'reason':'professor asserts it; no Claim carries it',
        'fields_json':json.dumps({'statement':'甲是假设。','claim_type':'exegesis','evidence_step_ids':['E'],
            'occurrences':[{'source_id':'S','anchors':[]}]},ensure_ascii=False),
        'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设。'}]}


def test_v3_creates_one_claim_and_program_expands_every_retain(material):
    material=creating(material)
    raw={**empty(),'creations':[creation()],'changes':[change('evidence_steps/E',{'produced_claim_ids':['C','C2']})]}
    candidate,proof=wire.candidate(raw,material)
    post,writes=cm.validate_candidate(cm.Request.model_validate(material['unit_request']),material['before'],candidate,material['sources'])
    assert writes['claims/C2']['action']=='create' and post['claims/C2']['claim_id']=='C2'
    assert post['evidence_steps/E']['produced_claim_ids']==['C','C2']
    assert {d.record_key for d in candidate.dispositions}==set(material['before'])|{'claims/C2'}
    assert proof['changed_keys']==['claims/C2','evidence_steps/E']


@pytest.mark.parametrize('bad',['v2','no_grant','existing','identity'])
def test_v3_creation_needs_grant_and_a_new_identity(material,bad):
    material=creating(material);raw={**empty(),'creations':[creation()]}
    if bad=='v2':material['policy']={'model_output_contract':wire.SCOPED_CONTRACT}
    if bad=='no_grant':
        request=cm.Request.model_validate(material['unit_request']);request.creation_permissions=[]
        material['unit_request']=request.model_dump(mode='json')
    if bad=='existing':raw['creations']=[creation('C')]
    if bad=='identity':raw['creations'][0]['fields_json']=json.dumps({'claim_id':'C2','statement':'甲是假设。'})
    with pytest.raises(ValueError):
        candidate,_=wire.candidate(raw,material)
        cm.validate_candidate(cm.Request.model_validate(material['unit_request']),material['before'],candidate,material['sources'])


def test_joint_scope_caps_creation_at_one_claim(material,monkeypatch,tmp_path):
    from backend.pipeline import claim_joint_update as joint
    monkeypatch.setattr(joint,'required_keys',lambda request,strict=True:[])
    material=creating(material);request=cm.Request.model_validate(material['unit_request'])
    authorization=tmp_path/'authorization.json';authorization.write_text('{"claim_creation_allocations":{}}')
    from backend.pipeline.cvp_sql_commit import binding
    request.authorization=cm.source.FileBinding.model_validate(binding(authorization))
    one,_=wire.candidate({**empty(),'creations':[creation()]},material)
    joint.validate_candidate_scope(request,material['before'],one)
    two,_=wire.candidate({**empty(),'creations':[creation(),creation('C3')]},material)
    with pytest.raises(ValueError,match='exact granted Claim identities'):
        joint.validate_candidate_scope(request,material['before'],two)
