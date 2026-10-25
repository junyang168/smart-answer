"""Returned proposals are recovered without retry or an output-contract upgrade."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.claim_model_output import retained_fragment_identity, candidate as expand_candidate
from backend.pipeline.claim_review_recovery import recover_returned_candidate, CANDIDATE_IDENTITY_VERSION
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_management import case
from backend.tests.test_claim_joint_update import shared, action, postgres_store, _temporary_postgres, joint_pass
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_management_human_source_follow_up import authorize


def wrong_reply(before):
    reply = cm.existing_candidate(before).model_dump(mode='json')
    row = next(d for d in reply['dispositions'] if d['record_key']=='source_fragments/F')
    row.update(record_key='source_fragments/WRONG', citations=[{'source_id':'S','paragraph_key':'S0001','exact_quote':before['source_fragments/F']['payload']['verbatim_excerpt'],'source_epoch':'current'}])
    reply['finding_resolutions']=[]
    return reply


def test_identity_correction_changes_one_retain_key_and_nothing_else(case):
    _,before,_,sources=case;raw=wrong_reply(before);saved=deepcopy(raw)
    fixed,mapping=retained_fragment_identity(raw,before)
    expected=deepcopy(raw);expected['dispositions'][-1]['record_key']='source_fragments/F'
    assert fixed.model_dump(mode='json')==expected
    assert raw==saved and mapping[0]['after']=='source_fragments/F'
    material={'policy':{},'before':before,'sources':sources,'candidate_identity_recovery':{
        'rule':'retained_fragment_exact_citation_v1','raw_response_sha256':cm.sha256_json(raw),
        'before_sha256':cm.sha256_json(before),'corrections':mapping}}
    result,proof=expand_candidate(raw,material)
    assert result==fixed and proof['identity_recovery']==material['candidate_identity_recovery']
    material['candidate_identity_recovery']['raw_response_sha256']='0'*64
    with pytest.raises(ValueError,match='proof drift'):expand_candidate(raw,material)


@pytest.mark.parametrize('bad',['duplicate','correct','update','claim_id','approximate','old_source','ambiguous','elsewhere','extra_citation','wrong_source'])
def test_identity_recovery_refuses_ambiguous_or_semantic_edits(case,bad):
    _,before,_,_=case;before=deepcopy(before);raw=wrong_reply(before);row=raw['dispositions'][-1]
    if bad=='duplicate':raw['dispositions'].append(deepcopy(row))
    if bad=='correct':row['record_key']='source_fragments/F'
    if bad=='update':row['action']='update'
    if bad=='claim_id':row['record_key']='claims/WRONG'
    if bad=='approximate':row['citations'][0]['exact_quote']='甲是假设'
    if bad=='old_source':row['citations'][0]['source_epoch']='before'
    if bad=='ambiguous':before['source_fragments/F2']=deepcopy(before['source_fragments/F'])
    if bad=='elsewhere':raw['replacements']={'source_fragments/WRONG':['claims/C']}
    if bad=='extra_citation':row['citations'].append(deepcopy(row['citations'][0]))
    if bad=='wrong_source':row['citations'][0]['source_id']='OTHER'
    with pytest.raises(ValueError):retained_fragment_identity(raw,before)


@pytest.fixture
def returned(shared):
    store,request,root,tmp=shared
    store.apply_plan(store.plan_package({'package_id':'test-fragment-dependency',
        'source_fragments':[{'fragment_id':'F','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设，不是断言。'}],
        'evidence_steps':[{**store.get_record_state('evidence_steps','E')['payload'],'source_fragment_ids':['F']}]}))
    policy=cm.Policy.model_validate_json((Path(cm.__file__).parent/'policies/wang_claim_management_joint_update_v2.json').read_text())
    policy.initial_candidate='proposal'
    path=tmp/'legacy-returned-policy.json';path.write_text(policy.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(path));authorize(request,{'required_maintenance_keys':['evidence_steps/E']})
    ports=SyntheticPorts(store,tmp,proposal=lambda packet:wrong_reply(packet['before']),opus=joint_pass)
    first=execute_claim_management(store,request,root=root,ports=ports)
    assert first['reason']=='engineering_required' and ports.calls==['gpt']
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));rid=next(iter(parent['latest_rounds'].values()))
    return store,request,root,tmp,ports,parent,rid


def direction_for(facts):
    store,request,root,tmp,ports,parent,rid=facts
    with store.connect() as conn:
        child=conn.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',(rid,)).fetchone()[0]
        parent=conn.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',(parent['run_id'],)).fetchone()[0]
    path=tmp/'exact-candidate-recovery.json'
    path.write_text(json.dumps({'schema_version':CANDIDATE_IDENTITY_VERSION,'authorized_by':'owner',
        'round_id':rid,'authorization_quote':'continue with the uniquely cited ID correction and original remaining review',
        'human_content_approval':False,'prior_metadata_sha256':cm.sha256_json(child),'prior_parent_sha256':cm.sha256_json(parent),
        'original_raw':binding(root/rid/'gpt.raw.json')}))
    return binding(path)


def test_recover_same_answer_then_only_original_unused_opus_and_readback(returned):
    store,request,root,tmp,ports,parent,rid=returned
    slots=deepcopy(parent['call_reservations']);raw=(root/rid/'gpt.raw.json').read_bytes()
    before={k:cm.model_state(v) for k,v in store.read_claim_context([('claims','A')]).items()}
    direction=direction_for(returned)
    recovered=recover_returned_candidate(store,rid,authorization=direction)
    assert recover_returned_candidate(store,rid,authorization=direction)==recovered
    child=store.get_cvp_group(rid)
    assert cm.model_packet(child['materials'])==cm.model_packet(child['engineering_candidate_recoveries'][0]['original_round']['materials'])
    assert store.get_cvp_group(parent['run_id'])['call_reservations']==slots
    assert ports.calls==['gpt']
    assert (root/rid/'gpt.raw.json').read_bytes()==raw
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus']
    assert store.claim_management_qualification('A')['ready'] and store.claim_management_qualification('B')['ready']
    assert not (store.claim_management_qualification('C') or {}).get('ready')
    assert {k:cm.model_state(v) for k,v in store.read_claim_context([('claims','A')]).items()}==before
    assert execute_claim_management(store,request,root=root,ports=ports)==result
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('bad',['unknown','raw_drift','source_drift','stale_direction','spent_review'])
def test_recovery_refuses_without_more_calls_or_sql_state_changes(returned,bad):
    store,request,root,tmp,ports,parent,rid=returned
    if bad=='spent_review':
        with store.connect() as conn:
            metadata=conn.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',(parent['run_id'],)).fetchone()[0]
            metadata['call_reservations'][rid+':opus']['state']='started'
            conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(metadata),parent['run_id']))
    direction=direction_for(returned)
    if bad=='unknown':(root/rid/'gpt.raw.json').unlink()
    if bad=='raw_drift':(root/rid/'gpt.raw.json').write_text('{}')
    if bad=='source_drift':Path(next(iter(request.source_bindings.values())).path).write_text('changed source')
    if bad=='stale_direction':
        with store.connect() as conn:
            metadata=conn.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',(rid,)).fetchone()[0]
            metadata['unexpected_state_change']=True
            conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',(json.dumps(metadata),rid))
    saved_child=deepcopy(store.get_cvp_group(rid));saved_parent=deepcopy(store.get_cvp_group(parent['run_id']))
    with pytest.raises((ValueError,KeyError,FileNotFoundError)):recover_returned_candidate(store,rid,authorization=direction)
    assert store.get_cvp_group(rid)==saved_child and store.get_cvp_group(parent['run_id'])==saved_parent
    assert ports.calls==['gpt']


def test_retained_quote_script_repair_preserves_source_and_meaning(case):
    from backend.pipeline.claim_model_output import retained_candidate_projection
    _,before,_,sources=case;before=deepcopy(before);sources=deepcopy(sources)
    before['source_fragments/F']['payload']['verbatim_excerpt']='马可记载'
    sources['S']['paragraphs']['S0001']['text']='马可记载。马可另外说。'
    raw=wrong_reply(before)
    # This second citation is an annotation on an unchanged object, not a finding.
    raw['dispositions'][0]['citations']=[{'source_id':'S','paragraph_key':'S0001','exact_quote':'馬可记载'}]
    original=deepcopy(raw);frozen=deepcopy(sources)
    candidate,proof=retained_candidate_projection(raw,before,sources)
    assert len(proof)==2 and proof[-1]['after']=='马可记载'
    assert candidate.dispositions[0].citations[0].exact_quote=='马可记载'
    assert raw==original and sources==frozen


@pytest.mark.parametrize('problem',['words','homograph','punctuation','duplicate','actual_write'])
def test_script_repair_cannot_hide_substantive_or_ambiguous_quotes(case,problem):
    from backend.pipeline.claim_model_output import retained_candidate_projection
    _,before,_,sources=case;before=deepcopy(before);sources=deepcopy(sources)
    before['source_fragments/F']['payload']['verbatim_excerpt']='甲是假设。'
    sources['S']['paragraphs']['S0001']['text']='甲是假设。马可记载。'
    raw=wrong_reply(before);row=raw['dispositions'][0]
    row['citations']=[{'source_id':'S','paragraph_key':'S0001','exact_quote':'馬可记载'}]
    if problem=='words':row['citations'][0]['exact_quote']='马可否认'
    if problem=='homograph':sources['S']['paragraphs']['S0001']['text']='甲是假设。發。';row['citations'][0]['exact_quote']='髮'
    if problem=='punctuation':row['citations'][0]['exact_quote']='马可，记载'
    if problem=='duplicate':sources['S']['paragraphs']['S0001']['text']+='马可记载。'
    if problem=='actual_write':row['action']='update'
    with pytest.raises(ValueError):retained_candidate_projection(raw,before,sources)
