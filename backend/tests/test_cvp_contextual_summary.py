from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from backend.pipeline import cvp_contextual_summary as summary
from backend.api.canonical_repository.cvp_recall import seal
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
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


def test_reviewed_cards_are_reused_and_stale_cards_are_not(tmp_path,monkeypatch):
    store,bundle=fixture_bundle(monkeypatch)
    result=summary.run([bundle],proposer=Client(),reviewer=review_client(),root=tmp_path,
                       check_current=lambda bs:None,max_calls=2)
    cards,bindings=summary.load_current_cards(store,tmp_path,{'V1'})
    assert cards=={'V1':result['cards'][0]['summary']}
    assert bindings==[bundle]
    store.heads[0]['core_proposition']='changed'
    assert summary.load_current_cards(store,tmp_path,{'V1'})==({},[])


def test_summary_cannot_be_swapped_after_review(tmp_path,monkeypatch):
    store,bundle=fixture_bundle(monkeypatch)
    result=summary.run([bundle],proposer=Client(),reviewer=review_client(),root=tmp_path,
                       check_current=lambda bs:None,max_calls=2)
    result['cards'][0]['summary']='不同摘要'
    result=seal({k:v for k,v in result.items() if k!='artifact_sha256'})
    (tmp_path/'summaries.json').write_text(json.dumps(result))
    with pytest.raises(ValueError,match='differs from proposal'):
        summary.load_current_cards(store,tmp_path,{'V1'})


def test_promotion_accepts_only_the_saved_reviewed_summary(tmp_path, monkeypatch):
    store,bundle=fixture_bundle(monkeypatch)
    material=deepcopy(bundle['material'])
    next(row['content'] for row in material['records'] if row['collection']=='canonical_viewpoints')['revision']=2
    bundle=seal({**{k:v for k,v in bundle.items() if k!='artifact_sha256'},'material':material})
    root=tmp_path/'son-of-man-context-summaries-test'
    card=summary.run([bundle],proposer=Client(),reviewer=review_client(),root=root,
                     check_current=lambda bs:None,max_calls=2)['cards'][0]
    authority=tmp_path/'authorization.json'
    write_immutable(authority,{'schema_version':'wang_cvp_contextual_summary_promotion_authorization_v1',
        'authorized_by':'owner','scope':'current source-bound Son of Man cards only'})
    old=next(row['content'] for row in material['records'] if row['collection']=='canonical_viewpoints')
    basis={'schema_version':'test-summary-basis'}
    monkeypatch.setattr('backend.api.canonical_repository.cvp_management.summary_basis_for_records',
                        lambda *args,**kwargs:basis)
    package={'package_id':'contextual-summary-V1-'+card['material_sha256'][:20],
        'canonical_viewpoints':[{**old,'revision':old['revision']+1,
            'summary':card['summary'],'summary_basis':basis}],
        'contextual_summary_application':{'schema_version':summary.VERSION,
            'root':str(root),'viewpoint_id':'V1','material_sha256':card['material_sha256'],
            'authorization':binding(authority)}}
    summary.validate_promotion_package(store,package)
    forged=deepcopy(package)
    forged['canonical_viewpoints'][0]['summary']='different'
    with pytest.raises(ValueError,match='postimage differs'):
        summary.validate_promotion_package(store,forged)


def test_material_builder_uses_summary_and_keeps_full_maintenance(tmp_path,monkeypatch):
    from test_cvp_materials import material
    from backend.pipeline.cvp_model_packet import project
    store,bundle=fixture_bundle(monkeypatch)
    summary.run([bundle],proposer=Client(),reviewer=review_client(),root=tmp_path,
                check_current=lambda bs:None,max_calls=2)
    common=material(store,summary_root=tmp_path)
    assert common['material']['viewpoints'][0]['contextual_summary']['text']
    assert common['summary_bindings']==[bundle]
    # Projection alone needs only packet material; durable records still contain
    # every member, and a requested member becomes visible again.
    packet={'schema_version':'test','material':common['material'],
        'input':common['claim_input'],'instructions':[], 'candidate':{'records':common['records']}}
    wire=project(packet)
    assert any(c.get('content_available_on_request') for c in wire['material']['claims'])
    expanded=material(store,summary_root=tmp_path,member_support_ids=['C1'])
    c=next(c for c in expanded['material']['claims'] if c['claim_ref']==expanded['references']['claims']['C1'])
    assert c['support_loaded'] and c['content']
    maintained=material(store,summary_root=tmp_path,maintenance_viewpoint_ids=['V1'])
    assert all(c['support_loaded'] for c in maintained['material']['claims'])
