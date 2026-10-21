from copy import deepcopy
import json
import pytest
from backend.pipeline import viewpoint_g037_claim_statement_repair as m


def case(monkeypatch):
    monkeypatch.setattr(m,'verify_frozen_context',lambda p:None)
    old={'claim_id':m.CLAIM,'revision':2,'statement':m.OLD,'evidence_step_ids':['E8','E9'],'occurrences':[{'anchors':['S3']}],'review_note':'old'}
    new={**old,'statement':m.NEW,'revision':3,'review_note':'fresh','reviewed_by':'gpt+opus','reviewed_at':'date'}
    ts=[]
    for n,lid in enumerate([m.LINK,m.SECOND,*sorted(m.SUPPORTS)]):
        cid=m.CLAIM if n<2 else f'C{n}'
        before={'viewpoint_claim_link_id':lid,'revision':1,'claim_id':cid,'viewpoint_id':f'V{n}','link_type':'equivalent_component','pinned_claim_revision':2,'component_locator':{'old':True},'evidence_bindings':['E'],'decision_id':f'D{n}'}
        after={**before,'revision':2}
        if n<2:after.update(pinned_claim_revision=3,component_locator=m.locator(new,m.NEW.split('；')[1] if n==0 else m.NEW.split('；')[0]+'；'))
        else:after.update(link_type='supports',component_locator=None)
        ts.append({'claim':new if n<2 else {'claim_id':cid},'before_link':before,'after_link':after})
    return {'claim_revision':{'before':old,'after':new},'targets':ts,'policy':{'policy_id':m.VERSION,'authorized_by':'owner','authorization_quote':'同意','authorized_statement':m.NEW,'future_claim_edits':False,'evidence_source_CVP_text_writes':0},'claim_source_viewpoint_text_writes':1}


def test_exact_statement_retains_anchors_two_disjoint_components(monkeypatch):
    p=case(monkeypatch);m.validate_packet(p)
    assert p['claim_revision']['before']['occurrences']==p['claim_revision']['after']['occurrences']
    positive,negative=[t['after_link']['component_locator']['canonical_spans'][0] for t in p['targets'][:2]]
    assert negative['end_char']==positive['start_char']
    assert '也已犯罪' in positive['exact_text']


@pytest.mark.parametrize('mutation',['claim','other_field','anchors','evidence','endpoint','locator','pin','population','four_equivalent','policy','future','old'])
def test_no_scope_expansion_or_unbound_component(monkeypatch,mutation):
    p=case(monkeypatch)
    if mutation=='claim':p['claim_revision']['after']['statement']+='他沒講'
    if mutation=='other_field':p['claim_revision']['after']['claim_type']='different'
    if mutation=='anchors':p['claim_revision']['after']['occurrences']=[]
    if mutation=='evidence':p['targets'][0]['after_link']['evidence_bindings']=[]
    if mutation=='endpoint':p['targets'][0]['after_link']['viewpoint_id']='different'
    if mutation=='locator':p['targets'][0]['after_link']['component_locator']=None
    if mutation=='pin':p['targets'][1]['after_link']['pinned_claim_revision']=2
    if mutation=='population':p['targets'].pop()
    if mutation=='four_equivalent':p['targets'][2]['after_link']['link_type']='equivalent_component'
    if mutation=='policy':p['policy']['authorization_quote']='not authorized'
    if mutation=='future':p['policy']['future_claim_edits']=True
    if mutation=='old':p['claim_revision']['before']['revision']=3
    with pytest.raises(ValueError):m.validate_packet(p)


def test_reservation_once_across_roots_preserves_refusals(tmp_path):
    source=tmp_path/'g037-owner-context-equivalence-review-20261005-v1';source.mkdir()
    for n in ('packet.json','raw-gpt.json','failure.json'):(source/n).write_text('{}')
    m.reserve(tmp_path/'r1',tmp_path)
    with pytest.raises(ValueError,match='already reserved'):m.reserve(tmp_path/'r2',tmp_path)
    assert json.loads((source/'raw-gpt.json').read_text())=={}


def test_package_only_one_claim_six_links_fresh_identities(monkeypatch,tmp_path):
    p=case(monkeypatch)
    p['identity_predecessors']=[{'decision':{'identity_decision_id':f'D{n}','identity_candidate_id':f'I{n}'},'candidate':{'identity_candidate_id':f'I{n}','seed_relation_ids':[]}} for n in range(6)]
    for label in ('gpt','opus'):(tmp_path/f'raw-{label}.json').write_text(json.dumps({'artifact_sha256':label}))
    pkg=m.package_for(p,{'gpt':{},'opus':{}},tmp_path)
    assert set(pkg)=={'package_id','claims','viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions','source_repair_application'}
    assert len(pkg['claims'])==1 and len(pkg['viewpoint_claim_links'])==6
    assert len({d['identity_decision_id'] for d in pkg['viewpoint_identity_decisions']})==6
    assert all(d['policy_version']==m.VERSION and d['review_status']=='system_approved' for d in pkg['viewpoint_identity_decisions'])
    assert pkg['source_repair_application']['approval_status']=='not_human_approved'


@pytest.mark.parametrize('mutation',[None,'model','backend','prompt','encoding','refusal','package','authority'])
def test_store_contract_requires_exact_fresh_double_review_and_encoding(monkeypatch,tmp_path,mutation):
    from types import SimpleNamespace
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    from backend.pipeline import viewpoint_audit_link_repair as r
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    p=case(monkeypatch);p['identity_predecessors']=[{'decision':{'identity_decision_id':f'D{n}','identity_candidate_id':f'I{n}'},'candidate':{'identity_candidate_id':f'I{n}','seed_relation_ids':[]}} for n in range(6)]
    monkeypatch.setattr(r,'BASE',tmp_path)
    monkeypatch.setattr(runtime,'subscription_client',lambda provider,model,effort:SimpleNamespace(model=model,backend=f'{provider}_subscription',reasoning_effort=effort))
    root=tmp_path/'repair';docs={};reviews={}
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        a=m.review_adapter(runtime.subscription_client(provider,model,'high'))
        response={'verdicts':[{'claim_id':cid,'approved':True,'problems':[],'reason':'faithful'} for cid in sorted({t['claim']['claim_id'] for t in p['targets']})]}
        raw={'model_id':model,'backend':a.backend,'prompt_sha256':a.prompt_sha256,'generation_config_sha256':a.generation_config_sha256,'request_payload_sha256':sha256_json(p),'response':response,'response_sha256':sha256_json(response),'request_encoding_proof_sha256':sha256_json(a.request_encoding_proof(p))}
        raw['artifact_sha256']=sha256_json(raw);docs[root/f'raw-{label}.json']=raw;reviews[label]=response
        proof={'schema_version':'wang_model_request_encoding_proof_v1',**a.request_encoding_proof(p)}
        docs[root/f'raw-{label}.request-encoding.json']={**proof,'artifact_sha256':sha256_json(proof)}
    auth={'output_root':str(root),'policy_id':m.VERSION,'statement':m.NEW,'human_content_approval':False,'historical_bindings':[]}
    docs[root/'owner-authorization.json']=auth
    docs[tmp_path/'g037-owner-context-equivalence-review-20261005-v1/owner-claim-statement-revision-start.json']=deepcopy(auth)
    monkeypatch.setattr(m,'read_artifact',lambda path:deepcopy(docs[path]))
    monkeypatch.setattr(r,'read_artifact',lambda path:deepcopy(docs[path]))
    pkg=m.package_for(p,reviews,root)
    if mutation in {'model','backend','prompt'}:pkg['source_repair_application']['review_artifacts'][0][{'model':'model_id','backend':'backend','prompt':'prompt_sha256'}[mutation]]='wrong'
    if mutation=='encoding':docs[root/'raw-gpt.request-encoding.json']['wire_request_bytes']=1
    if mutation=='refusal':pkg['source_repair_application']['review_artifacts'][1]['response']['verdicts'][0]['approved']=False
    if mutation=='package':pkg['claims'][0]['statement']='wrong'
    if mutation=='authority':docs[root/'owner-authorization.json']['human_content_approval']=True
    if mutation:
        with pytest.raises(ValueError):m.validate_package(pkg)
    else:m.validate_package(pkg)


def test_completed_parent_never_qualifies_changed_claim_for_new_scope(monkeypatch):
    from backend.pipeline import viewpoint_reviewed_cvp_prepare as r
    from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
    p=case(monkeypatch);pkg={'claims':[p['claim_revision']['after']],'source_repair_application':{'schema_version':m.VERSION,'review_packet':p}}
    monkeypatch.setattr(r,'validate_store_package_authorization',lambda p:None)
    monkeypatch.setattr(r,'normalize_package',lambda p:{})
    with pytest.raises(CvpProductionBlocked,match='completed and never selected'):
        r.completed_repair_parent_snapshot({'claim_packets':[{'claim_id':m.CLAIM}]},{},[pkg],{m.CLAIM},{m.CLAIM})


@pytest.mark.parametrize('mutation',[None,'claim','link','evidence','dependent','membership'])
def test_completed_parent_exact_postimage_and_retained_evidence(monkeypatch,mutation):
    from backend.pipeline import viewpoint_reviewed_cvp_prepare as r
    from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
    from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
    p=case(monkeypatch);old,new=p['claim_revision']['before'],p['claim_revision']['after']
    ev={'evidence_step_id':'E','statement':'unchanged'};fr={'fragment_id':'F','text':'unchanged'}
    for t in p['targets']:t['evidence']=[{'step':ev,'fragment':fr}]
    p['preserved_claim_dependents']=[{'collection':'questions','object_id':'Q','payload':{'question_id':'Q','review_status':'candidate'}}]
    normalized={'claims':{m.CLAIM:new},'viewpoint_claim_links':{m.LINK:p['targets'][0]['after_link']}}
    records={}
    def add(c,o,v):records[c,o]={'revision':v.get('revision',1),'payload':deepcopy(v),'content_sha256':semantic_record_sha(v),'retired':False}
    for c,objects in normalized.items():
        for o,v in objects.items():add(c,o,v)
    add('evidence_steps','E',ev);add('source_fragments','F',fr);add('questions','Q',p['preserved_claim_dependents'][0]['payload'])
    manifest={'claim_packets':[{'claim_id':m.CLAIM,'claim_revision':2,'claim_content_sha256':semantic_record_sha(old)}]}
    pkg={'source_repair_application':{'schema_version':m.VERSION,'review_packet':p}}
    monkeypatch.setattr(r,'validate_store_package_authorization',lambda p:None)
    monkeypatch.setattr(r,'normalize_package',lambda p:normalized)
    monkeypatch.setattr(r,'verify_parent_current_pins',lambda m,rs:None)
    if mutation=='claim':records['claims',m.CLAIM]['payload']['statement']='wrong'
    if mutation=='link':records['viewpoint_claim_links',m.LINK]['retired']=True
    if mutation=='evidence':records['evidence_steps','E']['payload']['statement']='wrong'
    if mutation=='dependent':records['questions','Q']['payload']['review_status']='approved'
    if mutation=='membership':manifest['claim_packets'][0]['claim_revision']=1
    if mutation:
        with pytest.raises(CvpProductionBlocked):r.completed_repair_parent_snapshot(manifest,records,[pkg],{m.CLAIM},set())
    else:assert r.completed_repair_parent_snapshot(manifest,records,[pkg],{m.CLAIM},set())==[m.CLAIM]
