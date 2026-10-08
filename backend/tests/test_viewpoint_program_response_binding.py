from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_response_binding import fields, wire_schema, bind_response
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    CoverageCanonicalViewpointReviewResponse as Review,
    CanonicalViewpointReconsiderationResponse as Correction,
    CanonicalViewpointProposalResponse as Proposal,
    validate_review,
)
from backend.pipeline.source_reconciliation import Review as SourceReview
from backend.pipeline import viewpoint_resolution_runtime as runtime
from backend.tests.test_viewpoint_required_cvp_coverage import proposal, payload, _claim
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def setup_review():
    p=proposal('new_viewpoint')
    claim=_claim('C1','判斷').model_copy(update={'claim_revision_sha256':'a'*64})
    request={'batch_id':'b','proposal_sha256':sha256_json(p.model_dump(mode='json')),
             'packet':{'claims':[claim.model_dump(mode='json')]}}
    response=Review.model_validate(payload(p,coverage_claim_sha256='a'*64)).model_dump(mode='json')
    response.pop('proposal_sha256');response['change_reviews'][0].pop('coverage_claim_sha256')
    return p,claim,request,response


def adapter(model, response, calls):
    def generate_json(**kw):
        calls.append(kw)
        return deepcopy(response)
    client=SimpleNamespace(model='test-opus',backend='test-subscription',reasoning_effort='high',
        max_output_tokens=64000,generate_json=generate_json)
    result=StructuredJsonReviewerAdapter(client=client,prompt='original prompt',response_model=model,
        schema_name='test',compact_payload=True,shared_text_payload=True)
    result.max_request_bytes=500000
    return result


def test_wire_schema_removes_all_echo_sha_not_content_fields():
    schema=wire_schema(Review)
    assert 'proposal_sha256' not in schema['properties']
    row=schema['$defs']['CoverageReviewedChange']
    assert 'coverage_claim_sha256' not in row['properties']
    assert {'decision','claim_id','component_index','coverage_reason','coverage_evidence_step_ids'}<=set(row['properties'])
    assert not {'proposal_sha256','review_sha256'} & set(wire_schema(Correction)['properties'])
    assert 'proposal_sha256' not in wire_schema(SourceReview)['properties']
    assert not any(fields(Proposal)) and wire_schema(Proposal)==Proposal.model_json_schema()


def test_new_call_keeps_real_wire_raw_and_derives_pins_without_recall(tmp_path):
    p,c,request,response=setup_review();calls=[];a=adapter(Review,response,calls);cache=tmp_path/'raw.json'
    bound,n,_=runtime.call_model(a,request,cache)
    assert n==1 and len(calls)==1
    assert 'proposal_sha256' not in calls[0]['json_schema']['schema']['properties']
    assert json.loads(cache.read_text())['response']==response
    assert bound['proposal_sha256']==request['proposal_sha256']
    assert bound['change_reviews'][0]['coverage_claim_sha256']=='a'*64
    assert validate_review(review=Review.model_validate(bound),proposal=p,proposal_sha256=request['proposal_sha256'],claims=[c])['outcome']=='pass'
    before={f.name:f.read_bytes() for f in tmp_path.iterdir()}
    assert runtime.call_model(a,request,cache)==(bound,0,0.0)
    assert len(calls)==1 and before=={f.name:f.read_bytes() for f in tmp_path.iterdir()}
    assert runtime.read_saved_model_response(a,request,cache)==response


@pytest.mark.parametrize('old_hash',['a'*63,'a'*64+'3f4','different-version'])
def test_authenticated_legacy_answer_pins_derived_raw_untouched(tmp_path,old_hash):
    _,_,request,response=setup_review();response['proposal_sha256']='old-echo'
    response['change_reviews'][0]['coverage_claim_sha256']=old_hash
    calls=[];a=adapter(Review,response,calls);cache=tmp_path/'raw.json'
    # Persist the actual OLD wire request/response through its original identity.
    raw=deepcopy(response);identity=runtime.model_request_identity(a,request)
    artifact=dict(schema_version='wang_canonical_viewpoint_batch_raw_response_v1',**identity,
        wall_seconds=0,response_sha256=sha256_json(raw),response=raw)
    artifact['artifact_sha256']=sha256_json(artifact);runtime.write_immutable(cache,artifact)
    original=cache.read_bytes()
    result,n,_=runtime.call_model(a,request,cache)
    assert n==0 and calls==[] and cache.read_bytes()==original
    expected=deepcopy(raw);expected['proposal_sha256']=request['proposal_sha256'];expected['change_reviews'][0]['coverage_claim_sha256']='a'*64
    assert result==expected


@pytest.mark.parametrize('fault',['request','response','seal','model','config'])
def test_cached_identity_tampering_stops_before_derivation(tmp_path,fault):
    _,_,request,response=setup_review();calls=[];a=adapter(Review,response,calls);cache=tmp_path/'raw.json'
    runtime.call_model(a,request,cache)
    artifact=json.loads(cache.read_text())
    if fault=='request':request['proposal_sha256']='changed'
    elif fault=='response':artifact['response']['novelty_review']['reason']='tampered'
    elif fault=='seal':artifact['artifact_sha256']='wrong'
    elif fault=='model':artifact['model_id']='other'
    else:artifact['generation_config_sha256']='other'
    if fault!='request':cache.write_text(json.dumps(artifact))
    with pytest.raises(ValueError):runtime.call_model(a,request,cache)
    assert len(calls)==1


@pytest.mark.parametrize('fault',['foreign','duplicate','missing_request'])
def test_identity_errors_not_hash_matched(fault):
    _,_,request,response=setup_review()
    if fault=='foreign':response['change_reviews'][0]['claim_id']='other'
    elif fault=='duplicate':response['change_reviews']*=2
    else:request.pop('proposal_sha256')
    with pytest.raises(ValueError):bind_response(Review,response,request)


def test_content_findings_never_cleared_by_binding():
    _,_,request,response=setup_review()
    response['change_reviews'][0].update(decision='correct',finding_codes=['wrong_meaning'],correction='原文限定')
    bound=bind_response(Review,response,request)
    assert bound['change_reviews'][0]['decision']=='correct'
    assert bound['change_reviews'][0]['correction']=='原文限定'
    assert Review.model_validate(bound).outcome()=='findings'


def test_correction_and_source_review_bind_to_exact_request():
    for model,response,request in [
        (Correction,{'component_patches':[]},{'proposal_sha256':'p','review_sha256':'r'}),
        (SourceReview,{'verdicts':[]},{'proposal_sha256':'s'}),
    ]:
        result=bind_response(model,response,request)
        assert all(result[k]==v for k,v in request.items())
        assert all(result[k]==v for k,v in response.items())


def test_copy_preserves_transport_ceiling_and_has_distinct_wire_identity():
    _,_,request,response=setup_review();calls=[];a=adapter(Review,response,calls)
    injected=lambda packet: deepcopy(response)
    a.generate=injected
    bound=a.program_bound_copy()
    assert bound.generate is injected and a._program_response_binding is False
    assert bound.max_request_bytes==500000 and bound.program_bound_copy() is bound
    assert bound.prompt_sha256!=a.prompt_sha256
    assert bound.generation_config_sha256!=a.generation_config_sha256
    assert a.request_encoding_proof(request)['semantic_payload_sha256']==bound.request_encoding_proof(request)['semantic_payload_sha256']


@pytest.mark.parametrize('fault',['component','missing','evidence','hold'])
def test_program_binding_does_not_remove_population_evidence_or_hold_guards(fault):
    p,c,request,response=setup_review()
    if fault=='component':response['change_reviews'][0]['component_index']=99
    elif fault=='missing':response['change_reviews']=[]
    elif fault=='evidence':response['change_reviews'][0]['coverage_evidence_step_ids']=['foreign']
    else:c=c.model_copy(update={'evidence':[c.evidence[0].model_copy(update={'valid_for_identity_review':False})]})
    with pytest.raises(ValueError):
        validate_review(review=Review.model_validate(bind_response(Review,response,request)),
            proposal=p,proposal_sha256=request['proposal_sha256'],claims=[c])


def test_full_batch_one_correction_final_review_no_model_echo_sha(tmp_path):
    from backend.pipeline.viewpoint_batch_resolution_runner import run_batch
    from backend.tests.test_viewpoint_batch_resolution import _candidate
    original=proposal();effective=proposal('new_viewpoint');calls=[]
    def transport(model,generate):
        client=SimpleNamespace(model=model,backend='test-subscription',reasoning_effort='high',max_output_tokens=64000)
        result=StructuredJsonReviewerAdapter(client=client,prompt='test',response_model=generate[0],schema_name='test',compact_payload=True)
        def respond(request):
            calls.append(model)
            return generate[1](request)
        result.generate=respond
        return result
    def review_response(request):
        raw=payload(Proposal.model_validate(request['proposal']))
        raw.pop('proposal_sha256');raw['change_reviews'][0].pop('coverage_claim_sha256')
        return raw
    correction={'finding_dispositions':[{'claim_id':'C1','component_index':0,'disposition':'accepted','reason':'補回遺漏'}],
        'component_patches':[{'claim_id':'C1','component_index':0,'replacement_components':[
            effective.claim_decisions[0].components[0].model_dump(mode='json')]}],
        'candidate_patches':[{'action':'upsert','local_key':'NEW','candidate':_candidate('NEW')}]}
    kwargs=dict(batch_id='b',scope_label='synthetic',claims=[_claim('C1','判斷')],registry_context=[],pending_candidates=[],output_dir=tmp_path,
        proposer=transport('gpt',(Proposal,lambda _:original.model_dump(mode='json'))),
        reviewer=transport('opus',(Review,review_response)),
        reconsiderer=transport('gpt',(Correction,lambda _:deepcopy(correction))))
    report=run_batch(**kwargs)
    assert calls==['gpt','opus','gpt','opus'] and report['approval_outcome']=='pass'
    for stage in ['review','reconsideration','final-review']:
        raw=json.loads((tmp_path/('raw-'+stage+'.json')).read_text())
        assert not {'proposal_sha256','review_sha256'} & raw['response'].keys()
        assert (tmp_path/('raw-'+stage+'.program-binding.json')).exists()
    for stage in ['review','final-review']:
        envelope=json.loads((tmp_path/(stage+'.json')).read_text())
        raw=json.loads((tmp_path/('raw-'+stage+'.json')).read_text())
        assert envelope['normalization']['raw_response_sha256']==raw['response_sha256']
    again=run_batch(**kwargs)
    assert calls==['gpt','opus','gpt','opus'] and again['approval_outcome']=='pass'
