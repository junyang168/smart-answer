"""The simplified wire protocol keeps semantic evidence and program safeguards."""
from copy import deepcopy
import json
import pytest
from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE
from backend.pipeline.cvp_model_packet import project
from backend.pipeline.cvp_management_models import adapter,validate_response
from backend.pipeline.cvp_materials import validate_material_requests
from test_cvp_management import request,materials,proposal,passing
from test_cvp_recall_stages import Client,review_client


def current_request():
    req=request()
    return req.model_copy(update={'input':{**req.input,'recall_profile':STATEMENT_SCOPE_PROFILE}})


def test_current_flow_requires_summary_before_review():
    req,b=current_request(),materials();raw=proposal();raw.pop('summaries')
    candidate=domain.compile_candidate(req,b,raw)
    assert candidate['summaries']==[]
    assert candidate['summary_material_pending']==['nv1']
    with pytest.raises(ValueError,match='summary material'):
        domain.validate_review(req,b,candidate,passing(candidate))
    packet=domain.proposal_packet(req,b)
    a=adapter('proposal',req,b,packet,client=Client())
    schema=a._response_model.model_json_schema()
    assert 'review' not in schema['properties']
    assert 'Summary500' in schema.get('$defs',{})
    assert 'summaries' in schema['$defs']['CandidateChanges']['properties']
    assert '双审核' not in a._prompt and '备份' not in a._prompt and '500' in a._prompt
    ra=adapter('review',req,b,domain.review_packet(req,b,candidate),client=review_client())
    assert 'candidate' not in ra._response_model.model_json_schema()['properties']
    assert 'fields_json' not in ra._prompt


def test_wire_keeps_content_and_new_feedback_without_mutating_durable_packet():
    req,b=current_request(),materials()
    b['material']['claims']=deepcopy(b['material']['claims'])
    b['material']['claims'][0]['content']=deepcopy(b['claim_input']['claims'][0]['content'])
    from backend.api.canonical_repository.cvp_recall import seal
    b=seal({k:v for k,v in b.items() if k!='artifact_sha256'})
    candidate=domain.compile_candidate(req,b,proposal())
    newest={'finding_id':'new-review-issue','slot':'f9','target_refs':['nv1'],
            'category':'wording','reason':'必须保留这个新发现的限定。'}
    packet=domain.proposal_packet(req,b,previous=candidate,prior_findings=[newest])
    before=deepcopy(packet);wire=project(packet)
    assert packet==before
    assert 'request' not in wire and 'context' not in wire
    assert 'references' not in wire['previous'] and 'writes' not in wire['previous']
    assert wire['material']['claims'][0]['content']==packet['material']['claims'][0]['content']
    assert wire['input']['text_units']==packet['input']['text_units']
    assert wire['prior_findings']==[newest]
    assert wire['operation']['creation_slots']
    assert all(set(s)=={'ref','collection'} for s in wire['operation']['creation_slots'])
    assert len(wire['previous']['records'])==len(candidate['writes'])
    for original,projected in zip(candidate['writes'],wire['previous']['records'],strict=True):
        assert projected['ref']==original['ref']
        assert projected['collection']==original['collection']
        if 'core_proposition' in original['payload']:
            for field in ('core_proposition','scope','proposition_signature'):
                assert projected['payload'][field]==original['payload'][field]


def test_static_schema_does_not_replace_domain_or_material_checks():
    req,b=current_request(),materials();raw=proposal();raw.pop('summaries')
    raw['changes'][0]['ref']='nv99999'
    a=adapter('proposal',req,b,domain.proposal_packet(req,b),client=Client())
    parsed=a._response_model.model_validate({'result':'candidate','candidate':raw,'context_requests':[],'member_requests':[]})
    with pytest.raises(ValueError,match='reference'):domain.compile_candidate(req,b,parsed.candidate.model_dump())
    with pytest.raises(ValueError,match='member'):
        validate_material_requests(b,{'context_requests':[],'member_requests':[{'member_ref':'foreign'}]})


def test_schema_independent_of_reference_inventory():
    req,b=current_request(),materials();one=adapter('proposal',req,b,domain.proposal_packet(req,b),client=Client())
    other=req.model_copy(update={'creation_slots':{**req.creation_slots,'canonical_viewpoints':17}})
    two=adapter('proposal',other,b,domain.proposal_packet(other,b),client=Client())
    assert one._response_model.model_json_schema()==two._response_model.model_json_schema()


def test_summary_hides_only_unloaded_members_and_keeps_request_refs():
    packet=domain.proposal_packet(current_request(),materials())
    packet['material']['viewpoints']=[{'cvp_ref':'v1','contextual_summary':{'text':'背景'}}]
    packet['material']['links']=[{'cvp_ref':'v1','claim_ref':'member'}]
    packet['material']['claims'].append({'claim_ref':'member','target':False,
        'support_loaded':False,'content':{'statement':'成员表述'}})
    before=deepcopy(packet)
    wire=project(packet)
    assert wire['material']['claims'][-1]=={'claim_ref':'member','target':False,
        'support_loaded':False,'content_available_on_request':True}
    assert packet==before
    packet['material']['claims'][-1]['support_loaded']=True
    assert project(packet)['material']['claims'][-1]['content']=={'statement':'成员表述'}
    packet['material']['claims'][-1]['support_loaded']=False
    packet['material']['viewpoints'][0].pop('contextual_summary')
    assert project(packet)['material']['claims'][-1]['content']=={'statement':'成员表述'}


def test_wire_keeps_source_text_and_locators_without_transport_coordinates():
    packet=domain.proposal_packet(current_request(),materials())
    paragraph={'paragraph_key':'S0004','row':{'text':'人子就到了。','index':4,
        'start_time':12.5,'end_time':14.0}}
    packet['material']['sources']=[{'source_ref':'s1','sections':[{'section_ref':'p1',
        'title':'人子來到','paragraphs':[paragraph]}]}]
    before=deepcopy(packet)
    wire=project(packet)
    assert wire['material']['sources'][0]['sections'][0]['paragraphs']==[
        {'paragraph_key':'S0004','row':{'text':'人子就到了。'}}]
    assert packet==before


def test_wire_keeps_fragment_evidence_and_link_semantics_without_transport_fields():
    packet=domain.proposal_packet(current_request(),materials())
    packet['material']['fragments']=[{'fragment_ref':'f1','content':{
        'fragment_id':'f1','source_id':'s1','paragraph_key':'S0004',
        'verbatim_excerpt':'人子就到了。','media_time':12.5,
        'source_segment_index':4,'schema_version':1,'review_status':'candidate'}}]
    packet['material']['evidence']=[{'evidence_ref':'e1','content':{
        'evidence_step_id':'e1','statement':'人子就到了。',
        'source_fragment_ids':['f1'],'stance':'asserted','review_status':'candidate'}}]
    packet['material']['links']=[{'link_ref':'l1','claim_ref':'c1','cvp_ref':'v1',
        'content':{'viewpoint_claim_link_id':'l1','claim_id':'c1',
            'viewpoint_id':'v1','link_type':'qualifies',
            'component_locator':{'statement_component':'人子就到了。'},
            'evidence_bindings':[{'evidence_step_id':'e1','source_fragment_id':'f1'}],
            'occurrence_refs':['OCC-1'],'review_status':'system_approved'}}]
    before=deepcopy(packet)
    wire=project(packet)['material']
    assert packet==before
    assert wire['fragments']==[{'fragment_ref':'f1','content':{
        'source_id':'s1','paragraph_key':'S0004','verbatim_excerpt':'人子就到了。'}}]
    assert wire['evidence']==[{'evidence_ref':'e1','content':{
        'statement':'人子就到了。','source_fragment_ids':['f1'],'stance':'asserted'}}]
    assert wire['links']==[{'link_ref':'l1','claim_ref':'c1','cvp_ref':'v1',
        'content':{'claim_id':'c1','viewpoint_id':'v1','link_type':'qualifies',
            'component_locator':{'statement_component':'人子就到了。'},
            'evidence_bindings':[{'evidence_step_id':'e1','source_fragment_id':'f1'}]}}]
