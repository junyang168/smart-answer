"""Synthetic exact statement authority; no live content/model/database calls."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.pipeline import claim_statement_revision as c
from backend.api.canonical_repository.postgres_store import normalize_package, build_change_set_plan
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
from backend.api.canonical_repository.viewpoint_batch_changeset import reuse_exact_current_links, CvpBatchChangeSetError
from backend.api.canonical_repository.knowledge_models import ViewpointIdentityCandidateRecord, ViewpointIdentityDecisionRecord


@pytest.fixture
def scenario(tmp_path,monkeypatch):
    claim=normalize_package({'claims':[{'claim_id':'CL-test','statement':'甲決定乙；丙。','claim_type':'exegesis',
        'review_status':'ai_consensus_reviewed','evidence_step_ids':[]} ]})['claims']['CL-test']
    membership=tmp_path/'membership.json'
    membership.write_text(json.dumps({'claim_packets':[{'claim_id':'CL-test','claim_revision':claim['revision'],
        'claim_content_sha256':semantic_record_sha(claim)}]}))
    authorization={'claim_id':'CL-test','before_revision':claim['revision'],'before_sha256':semantic_record_sha(claim),
        'old_text':'決定','new_text':'影響','start_char':1,'authorization_quote':'只改此词','authorization_id':'owner-synthetic-1',
        'accepted_membership':c.binding(membership)}
    root=tmp_path/'run';root.mkdir()
    monkeypatch.setattr(c,'source_context',lambda states,cl:{'evidence':[],'fragments':[],'sources':[]})
    states={('claims','CL-test'):claim}
    return states,authorization,root


def test_exact_statement_version_and_no_other_content_edits(scenario):
    states,auth,root=scenario;packet=c.prepare(states,auth,root)
    c.validate_packet(packet)
    old,new=packet['before_claim'],packet['after_claim']
    assert new['statement']=='甲影響乙；丙。'
    assert new['revision']==old['revision']+1
    assert new['review_status']=='independent_terminal_reviewed'
    assert packet['full_group_independent_audit_complete'] is False
    assert states['claims','CL-test']==old


@pytest.mark.parametrize('fault',['old_sha','revision','claim_id','extra_scope','range','same','human_hold','empty_authorization'])
def test_authorization_and_real_hold_fail_closed(scenario,fault):
    states,auth,root=scenario
    if fault=='old_sha':auth['before_sha256']='foreign'
    elif fault=='revision':auth['before_revision']+=1
    elif fault=='claim_id':auth['claim_id']='foreign'
    elif fault=='extra_scope':auth['source_write']=True
    elif fault=='range':auth['start_char']=2
    elif fault=='same':auth['new_text']=auth['old_text']
    elif fault=='empty_authorization':auth['authorization_quote']=''
    else:
        states['claims','CL-test']['review_status']='human_review_required'
        auth['before_sha256']=semantic_record_sha(states['claims','CL-test'])
    with pytest.raises((ValueError,KeyError)):c.prepare(states,auth,root)


@pytest.mark.parametrize('fault',['statement','evidence','metadata','revision','consumer','source','membership'])
def test_frozen_packet_scope_tamper_rejected(scenario,monkeypatch,fault):
    states,auth,root=scenario;packet=c.prepare(states,auth,root)
    if fault=='statement':packet['after_claim']['statement']+='未授权新主张'
    elif fault=='evidence':packet['after_claim']['evidence_step_ids']=['foreign']
    elif fault=='metadata':packet['after_claim']['reviewed_by']='human'
    elif fault=='revision':packet['after_claim']['revision']+=1
    elif fault=='consumer':packet['reverse_consumers']=[{'collection':'questions','object_id':'Q','payload':{}}]
    elif fault=='source':packet['own_source_context']['sources']=[{'unbound':True}]
    else:Path(auth['accepted_membership']['path']).write_text('{}')
    with pytest.raises(ValueError):c.validate_packet(packet)


def link(claim,oid='VCL-test'):
    return normalize_package({'viewpoint_claim_links':[{'viewpoint_claim_link_id':oid,'viewpoint_id':'CV-test',
        'validated_against_viewpoint_revision_id':'CVR-test','claim_id':claim['claim_id'],'pinned_claim_revision':claim['revision'],
        'link_type':'equivalent_component','component_locator':{'claim_sha256':semantic_record_sha(claim),
        'statement_component':'丙。','canonical_spans':[{'start_char':5,'end_char':7,'exact_text':'丙。'}]},
        'evidence_bindings':[{'evidence_step_id':'E-test','source_fragment_id':'FR-test'}],
        'decision_id':'VID-test','review_status':'system_approved'}]})['viewpoint_claim_links'][oid]


def test_existing_predicate_and_exact_locator_preserved(scenario):
    states,auth,root=scenario;old=states['claims','CL-test'];before=link(old)
    new=c.derive_claim(old,auth,root);after=c.derive_links([before],new)[0]
    assert after['pinned_claim_revision']==new['revision']
    assert after['component_locator']['claim_sha256']==semantic_record_sha(new)
    for field in ['evidence_bindings','link_type','viewpoint_id','validated_against_viewpoint_revision_id']:
        assert after[field]==before[field]
    assert after['component_locator']['canonical_spans']==before['component_locator']['canonical_spans']
    new['statement']='甲影響乙；丁。'
    with pytest.raises(ValueError,match='changes an existing component'):c.derive_links([before],new)


@pytest.mark.parametrize('fault',['foreign','retired','length'])
def test_dependency_rebind_rejects_wrong_scope(scenario,fault):
    states,auth,root=scenario;old=states['claims','CL-test'];before=link(old)
    if fault=='foreign':before['claim_id']='foreign'
    elif fault=='retired':before['effective_state']='retired'
    else:auth['new_text']='被另外的因素影響'
    with pytest.raises(ValueError):c.derive_links([before],c.derive_claim(old,auth,root))


def test_plan_requires_exact_postimage_and_population(scenario,monkeypatch):
    states,auth,root=scenario;packet=c.prepare(states,auth,root)
    raw={'artifact_sha256':'synthetic-review','response':{'verdicts':[{'claim_id':'CL-test','approved':True,'reason':'来源支持','problems':[]}]}}
    # Pure package reconstruction uses physical files; this fixture does not
    # pretend to carry production model approval.
    monkeypatch.setattr(c,'binding',lambda path:{'path':str(path),'file_sha256':'test'})
    package=c.package_for(packet,raw)
    monkeypatch.setattr(c,'validate_package',lambda p:None)
    from backend.api.canonical_repository import reviewed_candidate_contract as contract
    monkeypatch.setattr(contract,'validate_source_repair_application',lambda p:None)
    existing={k:{'payload':p,'revision':p['revision'],'content_sha256':semantic_record_sha(p),'retired':False} for k,p in states.items()}
    plan=json.loads(json.dumps(build_change_set_plan(package,existing,source_kind=c.VERSION).as_dict()))
    c.validate_plan(plan,package)
    bad=deepcopy(plan);bad['operations'][0]['payload']['statement']='未审核'
    with pytest.raises(ValueError):c.validate_plan(bad,package)
    bad=deepcopy(plan);bad['operations'].append(deepcopy(bad['operations'][0]))
    with pytest.raises(ValueError):c.validate_plan(bad,package)


def test_fresh_exact_links_retain_old_ids_not_old_review_claiming(scenario):
    states,auth,root=scenario;old=link(states['claims','CL-test']);fresh=deepcopy(old)
    fresh.update(viewpoint_claim_link_id='VCL-fresh',decision_id='VID-fresh',revision=1,occurrence_refs=['fresh'])
    assert reuse_exact_current_links([fresh],[old])==[old]
    for field,value in [('link_type','supports'),('pinned_claim_revision',2),('viewpoint_id','CV-other')]:
        changed=deepcopy(fresh);changed[field]=value
        with pytest.raises(CvpBatchChangeSetError):reuse_exact_current_links([changed],[old])
    with pytest.raises(CvpBatchChangeSetError):reuse_exact_current_links([], [old])
    with pytest.raises(CvpBatchChangeSetError):reuse_exact_current_links([fresh],[old,deepcopy(old)])


def test_scoped_provenance_requires_exact_new_single_opus_contract():
    value={'identity_decision_id':'VID-test','identity_candidate_id':'VIC-test','decision':'match_existing',
        'resolved_viewpoint_id':'CV-test','claim_link_decisions':[{'claim_id':'CL-test','link_type':'supports'}],
        'input_sha256':'input','reason':'真实审核','created_at':'2026-10-07T00:00:00Z',
        'review_status':'system_approved','reviewer_kind':'system','reviewer_id':c.REVIEWER,
        'approval_basis':'owner_scoped_independent_terminal_review','policy_version':c.VERSION,
        'reviewer_model_ids':['claude-opus-5-5'],'semantic_call_artifact_sha256s':['raw'],'review_artifact_sha256':'raw'}
    ViewpointIdentityDecisionRecord.model_validate(value)
    for field,new in [('reviewer_id','human'),('reviewer_model_ids',['gpt-6.1-sol']),('policy_version','unregistered')]:
        with pytest.raises(ValueError):ViewpointIdentityDecisionRecord.model_validate({**value,field:new})


@pytest.mark.parametrize('fault',[None,'model','backend','prompt','config','request','response','raw_sha','refusal','budget','report'])
def test_exact_saved_review_validation_never_calls_or_accepts_drift(scenario,monkeypatch,fault):
    from backend.pipeline.viewpoint_resolution_runtime import call_model, write_immutable
    states,auth,root=scenario;packet=c.prepare(states,auth,root)
    actual=c.adapter(c.subscription_client('claude','claude-opus-5-5','high'))
    response={'verdicts':[{'claim_id':'CL-test','approved':fault!='refusal','reason':'逐字支持',
                          'problems':['拒绝'] if fault=='refusal' else []}]}
    actual.generate=lambda p:response
    write_immutable(root/'packet.json',packet)
    reserve={'policy':c.VERSION,'packet_sha256':c.sha256_json(packet),'prompt_sha256':actual.prompt_sha256,
        'config_sha256':actual.generation_config_sha256,'model':actual.model_id,'backend':actual.backend,
        'request_bytes':actual.request_bytes(packet),'authorization_id':auth['authorization_id'],'GPT_new_calls':0,'calls_allowed':1}
    write_immutable(root/'review-reservation.json',reserve)
    marker=root/'marker.json';write_immutable(marker,{**reserve,'root':str(root)})
    write_immutable(root/'review-budget-proof.json',{'authorization_id':auth['authorization_id'],'reservation':c.binding(marker)})
    _,calls,_=call_model(actual,packet,root/'raw-opus.json');assert calls==1
    report={'status':'Opus_approved_not_applied','packet_sha256':c.sha256_json(packet),'review':response,
            'raw_binding':c.binding(root/'raw-opus.json'),'calls_executed':1}
    write_immutable(root/'review-report.json',report)
    if fault in {'model','backend','prompt','config','request','response','raw_sha'}:
        raw=json.loads((root/'raw-opus.json').read_text())
        field={'model':'model_id','backend':'backend','prompt':'prompt_sha256','config':'generation_config_sha256',
               'request':'request_payload_sha256','response':'response_sha256','raw_sha':'artifact_sha256'}[fault]
        raw[field]='drift';(root/'raw-opus.json').write_text(json.dumps(raw))
    elif fault=='budget':marker.write_text('{}')
    elif fault=='report':report['review']['verdicts'][0]['reason']='漂移';(root/'review-report.json').write_text(json.dumps(report))
    monkeypatch.setattr(c,'adapter',lambda client:actual)
    if fault is None:
        saved,_=c.reviewed_inputs(root);assert saved==packet
    else:
        with pytest.raises(ValueError):c.reviewed_inputs(root)


def test_physical_original_projection_survives_exact_json_roundtrip(tmp_path,monkeypatch):
    import hashlib
    path=tmp_path/'source.json';path.write_text('{"script":[{"text":"甲。"}]}')
    claim={'claim_id':'CL','evidence_step_ids':['E'],'occurrences':[{'source_id':'SRC'}]}
    states={('evidence_steps','E'):{'evidence_step_id':'E','produced_claim_ids':['CL'],'source_fragment_ids':['FR']},
        ('source_fragments','FR'):{'fragment_id':'FR','source_id':'SRC','paragraph_key':'S0001','verbatim_excerpt':'甲。',
            'source_sha256':'body','verbatim_excerpt_sha256':hashlib.sha256('甲。'.encode()).hexdigest(),
            'paragraph_text_sha256':hashlib.sha256('甲。'.encode()).hexdigest()},
        ('source_documents','SRC'):{'source_id':'SRC','source_body_sha256':'body'}}
    monkeypatch.setattr(c,'load_context_source',lambda source:(path,c.file_sha256(path),
        SimpleNamespace(body_sha256='body',spoken_rows=({'text':'甲。'},))))
    context=c.source_context(states,claim)
    assert context==json.loads(json.dumps(context))
    states['source_fragments','FR']['verbatim_excerpt']='乙。'
    with pytest.raises(ValueError):c.source_context(states,claim)
