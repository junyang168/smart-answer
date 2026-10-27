"""Own excerpts, quotation authorship and full-source fidelity are separate."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from backend.pipeline import claim_management as cm, claim_evidence_review as er
from backend.pipeline import claim_model_output as output
from backend.tests.test_claim_management import case
from backend.pipeline.source_reconciliation_runner import binding


@pytest.fixture
def material(case, tmp_path):
    request, before, candidate, sources = deepcopy(case)
    p = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v6.json').read_text())
    path = tmp_path/'policy.json';path.write_text(p.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    before['source_documents/S'] = {'payload':{'source_id':'S'},'retired':False}
    e = before['evidence_steps/E']['payload']
    e.update(speaker='professor', stance='asserted', support_eligibility='eligible_candidate')
    return {'policy': p.model_dump(mode='json'), 'unit_request': request.model_dump(mode='json'),
            'before': before, 'unresolved': {}, 'sources': sources,
            'original_goal': {'operation':'Update','required_questions': sorted(cm.QUESTIONS['Update'] | er.QUESTIONS)}}, candidate


def answer(**fields):
    return {'complete_review': True, 'findings': [], 'prior_findings': [], 'binding_assessments': [{
        'claim_key': 'claims/C', 'claim_use':'professor_judgment', 'sufficient':True,
        'evidence_keys':['evidence_steps/E'], 'reason':'Bound F explicitly states the complete judgment.', **fields}]}


def test_full_paragraph_and_observation_do_not_expand_own_bound_excerpts(material):
    m, candidate = material
    m['sources']['S']['paragraphs']['S0001']['text'] += '田地是世界，好種是天國之子。'
    m['before']['observations/O'] = {'payload':{'source_fragment_ids':['FO']},'retired':False}
    m['before']['source_fragments/FO'] = {'payload':{'source_id':'S','verbatim_excerpt':'田地是世界，好種是天國之子。'},'retired':False}
    table = er.evidence_table(cm.Request.model_validate(m['unit_request']), m['before'], candidate)
    assert table['claims/C']['evidence_steps/E']['fragment_keys'] == ['source_fragments/F']
    assert not {'observations/O','source_fragments/FO'} & set(table['claims/C'])
    with pytest.raises(ValueError,match='borrowed'):
        er.validate_assessments(er.BindingFindingsReview.model_validate(answer(evidence_keys=['source_fragments/FO'])),m,candidate)


def test_old_compact_pass_no_longer_suffices_for_new_policy(material):
    m,candidate=material
    with pytest.raises(ValueError,match='binding_assessments'):
        output.review({'complete_review':True,'findings':[],'prior_findings':[]},m,candidate)
    assert output.review(answer(),m,candidate).verdicts[0].reason == answer()['binding_assessments'][0]['reason']
    m['policy'].pop('evidence_review_contract')
    assert output.review({'complete_review':True,'findings':[],'prior_findings':[]},m,candidate)


@pytest.mark.parametrize('problem',['context','no_fragments','foreign','wrong_owner','missing_claim','duplicate','no_finding'])
def test_binding_counterexamples_cannot_pass(material, problem):
    m,candidate=material;raw=answer();e=m['before']['evidence_steps/E']['payload']
    if problem=='context':e['support_eligibility']='context_only'
    if problem=='quote':e.update(speaker='quoted_source',stance='quoted')
    if problem=='no_fragments':e['source_fragment_ids']=[]
    if problem=='foreign':m['before']['source_fragments/F']['payload']['source_id']='OTHER'
    if problem=='wrong_owner':e['produced_claim_ids']=[]
    if problem=='missing_claim':raw['binding_assessments']=[]
    if problem=='duplicate':raw['binding_assessments']*=2
    if problem=='no_finding':raw['binding_assessments'][0]['sufficient']=False
    with pytest.raises(ValueError):
        er.validate_assessments(er.BindingFindingsReview.model_validate(raw),m,candidate)


def test_quotation_report_keeps_context_eligibility(material):
    m,candidate=material;e=m['before']['evidence_steps/E']['payload']
    e.update(support_eligibility='context_only',speaker='quoted_source',stance='quoted')
    result=output.review(answer(claim_use='quotation_report',reason='Records that the professor quoted the verse, without adding an interpretation.'),m,candidate)
    assert all(v.approved for v in result.verdicts)
    assert e['support_eligibility']=='context_only'


def test_candidate_postimage_replaces_bound_fragment_and_preserves_old_policy(material):
    m,candidate=material;old=deepcopy(m)
    f=deepcopy(m['before']['source_fragments/F']['payload']);f['verbatim_excerpt']='甲是假设。乙只在特定情况下成立。'
    candidate.records=[cm.source.Row(collection='source_fragments',object_id='F',payload=f)]
    packet={**m,'claim_scope':{'content_claim_keys':['claims/C']}}
    result=er.add_packet(packet,candidate)
    assert result['own_evidence_bindings']['claims/C']['evidence_steps/E']['fragment_keys']==['source_fragments/F']
    assert m['before']==old['before']
    old['policy'].pop('evidence_review_contract');assert er.add_packet(old)==old


def test_new_review_schema_is_bound_without_altering_old_response_schema(material):
    m,candidate=material
    packet={**m,'candidate':candidate.model_dump(mode='json'),'denominator':list(m['before'])}
    adapter=SimpleNamespace(generation_config_sha256='old')
    result=output.scoped_adapter(adapter,packet)
    assert 'binding_assessments' in result._response_model.model_json_schema()['required']
    packet['policy'].pop('evidence_review_contract')
    result=output.scoped_adapter(adapter,packet)
    assert 'binding_assessments' not in result._response_model.model_json_schema()['properties']


def test_scripture_used_as_evidence_is_not_demoted_because_it_is_quoted(material):
    m,candidate=material
    m['before']['evidence_steps/E']['payload'].update(
        speaker='quoted_source',stance='quoted',support_eligibility='eligible_candidate')
    # Eligibility records reviewed argumentative use, not who first said the
    # quotation. The independent reviewer must still justify ALL components.
    result=output.review(answer(reason='The professor expressly uses this quoted scripture to establish this judgment; its words remain attributed to scripture.'),m,candidate)
    assert all(v.approved for v in result.verdicts)
    assert m['before']['evidence_steps/E']['payload']['speaker']=='quoted_source'


from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request, SyntheticPorts
from backend.tests.test_claim_management_human_source_follow_up import authorize
from backend.pipeline.cvp_sql_actions import execute_claim_management


def test_real_sql_readback_retains_the_explicit_binding_assessment(action):
    store, _, _, _, root, _ = action
    store.apply_plan(store.plan_package({'package_id':'own-binding-case',
        'claims':[{'claim_id':'C','statement':'甲是假设。','claim_type':'exegesis',
            'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E']}],
        'evidence_steps':[{'evidence_step_id':'E','statement':'甲是假设。','produced_claim_ids':['C'],
            'support_eligibility':'eligible_candidate','speaker':'professor','stance':'asserted','source_fragment_ids':['F']}],
        'source_fragments':[{'fragment_id':'F','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设，不是断言。'}]}))
    request,output_root=operation_request(action,'Update',['C'])
    p=cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v6.json').read_text())
    path=root/'own-policy.json';path.write_text(p.model_dump_json())
    request.policy=cm.source.FileBinding.model_validate(binding(path));authorize(request,{'required_maintenance_keys':[]})
    def opus(packet):
        assert er.QUESTIONS <= set(packet['required_questions'])
        assert packet['own_evidence_bindings']['claims/C']['evidence_steps/E']['fragment_keys']==['source_fragments/F']
        return answer()
    ports=SyntheticPorts(store,root,opus=opus)
    result=execute_claim_management(store,request,root=output_root,ports=ports)
    assert result['status']=='completed',result
    q=store.claim_management_qualification('C')
    assert q['ready'] and er.QUESTIONS <= set(q['question_scope'])
    child=store.get_cvp_group(q['run_id'])
    raw=json.loads((output_root/q['run_id']/'opus.raw.json').read_text())
    assert raw['response']['binding_assessments'][0]['reason']==answer()['binding_assessments'][0]['reason']
    assert ports.calls==['opus']
    assert execute_claim_management(store,request,root=output_root,ports=ports)==result
    assert ports.calls==['opus']


@pytest.mark.parametrize('target,passes',[('evidence_steps/E',True),('claims/C',True),('evidence_steps/OTHER',False)])
def test_insufficient_binding_may_be_named_on_the_claims_own_evidence(material,target,passes):
    # #459: Opus named the gap on the Claim's own Evidence step, not the Claim.
    m,candidate=material;raw=answer(sufficient=False)
    raw['findings']=[{'target_key':target,'component':'statement','category':'evidence','reason':'bound excerpts cover only half',
        'acceptance_condition':'bind the rest of the judgement','citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'田地'}],
        'affected_target_keys':[],'affected_write_keys':[],'impact_reason':'the Claim is not yet covered'}]
    review=er.BindingFindingsReview.model_validate(raw)
    if passes:
        er.validate_assessments(review,m,candidate)
    else:
        with pytest.raises(ValueError,match='concrete Claim finding'):er.validate_assessments(review,m,candidate)


@pytest.mark.parametrize('target,accepted',[
    ('claims/C',True),('evidence_steps/E',True),('source_fragments/F',True),
    ('observations/O',False),('questions/Q',False),('source_fragments/NEIGHBOUR',False)])
def test_insufficient_binding_accepts_only_its_claim_steps_or_bound_excerpts(material,target,accepted):
    m,candidate=material;raw=answer(sufficient=False)
    raw['findings']=[{'target_key':target,'component':'binding','category':'evidence',
        'reason':'Exact bound excerpt does not cover this judgment',
        'acceptance_condition':'repair the actual evidence binding',
        'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设。'}],
        'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'own Evidence gap'}]
    parsed=er.BindingFindingsReview.model_validate(raw)
    if accepted:
        er.validate_assessments(parsed,m,candidate)
    else:
        with pytest.raises(ValueError,match='insufficient own Evidence'):
            er.validate_assessments(parsed,m,candidate)
