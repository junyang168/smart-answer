from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from backend.pipeline import cvp_contextual_summary as summary
from backend.api.canonical_repository.cvp_recall import seal
from test_cvp_materials import MaterialStore
from test_cvp_recall_stages import Client,review_client


def fixture_bundle(monkeypatch):
    store=MaterialStore()
    monkeypatch.setattr('backend.pipeline.source_editorial_transition.verify_current_source_binding',lambda x:x)
    return store,summary.prepare(store,store.heads[0],source_loader=store.load_source)


def test_full_claim_fields_and_only_relevant_sections_are_carried(monkeypatch):
    store,bundle=fixture_bundle(monkeypatch)
    claims=[r['content'] for r in bundle['material']['records'] if r['collection']=='claims']
    assert claims==[store.records['claims','C1']['payload'],store.records['claims','C2']['payload']]
    assert len(bundle['material']['source_sections'])==2
    assert all([p['paragraph_key'] for p in s['sections'][0]['paragraphs']]==['S0003','S0004']
               for s in bundle['material']['source_sections'])
    store.records['viewpoint_claim_links','L3']=store.state({'claim_id':'C0','viewpoint_id':'V1','effective_state':'active'})
    with pytest.raises(ValueError,match='membership changed'):summary.guard(store,bundle)


def test_same_complete_material_reaches_both_models_and_replay_is_zero_call(tmp_path,monkeypatch):
    _,bundle=fixture_bundle(monkeypatch)
    p=Client(lambda v:{'summaries':[{'cvp_ref':'v1','summary':'论'*500}]});r=review_client()
    kwargs=dict(proposer=p,reviewer=r,root=tmp_path,check_current=lambda bs:None,max_calls=2)
    result=summary.run([bundle],**kwargs)
    assert len(result['cards'][0]['summary'])==500
    pp=json.loads(p.requests[0]['user_prompt']);rp=json.loads(r.requests[0]['user_prompt'])
    assert pp['viewpoints']==rp['viewpoints']
    assert summary.run([bundle],**kwargs)==result
    assert len(p.requests)==len(r.requests)==1


def test_rejected_summary_gets_bounded_revision_with_same_source(tmp_path,monkeypatch):
    _,bundle=fixture_bundle(monkeypatch)
    p=Client();calls=[]
    def review(v):
        calls.append(v)
        return {'verdicts':[{'cvp_ref':'v1','decision':'reject' if len(calls)==1 else 'approve','reason':'补充论据'}]}
    r=review_client(review)
    result=summary.run([bundle],proposer=p,reviewer=r,root=tmp_path,check_current=lambda bs:None,max_calls=4)
    assert result['call_slots_used']==4
    revised=json.loads(p.requests[1]['user_prompt'])
    assert revised['previous']['verdicts'][0]['decision']=='reject'
    assert calls[0]['viewpoints']==calls[1]['viewpoints']


def test_overlength_and_insufficient_capacity_fail_without_truncation(tmp_path,monkeypatch):
    _,bundle=fixture_bundle(monkeypatch)
    p=Client();r=review_client()
    with pytest.raises(ValueError,match='capacity'):
        summary.run([bundle],proposer=p,reviewer=r,root=tmp_path,check_current=lambda bs:None,max_request_bytes=1)
    assert not p.requests and not r.requests
    packet={'viewpoints':[{'cvp_ref':'v1'}]}
    a,_=summary.adapters(p,r,packet,500000)
    with pytest.raises(ValidationError):a._response_model.model_validate({'summaries':[{'cvp_ref':'v1','summary':'字'*501}]})
