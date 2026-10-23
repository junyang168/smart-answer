from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_g037_claim_condition_repair as m
from backend.pipeline import viewpoint_g037_claim_statement_repair as old
from backend.tests.test_viewpoint_g037_claim_statement_repair import case as old_case


def case(monkeypatch):
    p=old_case(monkeypatch)
    monkeypatch.setattr(m,'verify_frozen_context',lambda p:None)
    new=p['claim_revision']['after'];new['statement']=m.NEW
    p['policy'].update(policy_id=m.VERSION,authorized_statement=m.NEW)
    for t in p['targets'][:2]:
        t['after_link']['component_locator']=m.locator(new,m.NEW.split('；')[1] if t['before_link']['viewpoint_claim_link_id']==m.LINK else m.NEW.split('；')[0]+'；')
    return p


def test_preserves_original_necessary_condition_and_adds_timing(monkeypatch):
    p=case(monkeypatch);m.validate_packet(p)
    assert m.NEW.startswith(m.OLD[:-1]+'；')
    positive,negative=[t['after_link']['component_locator']['canonical_spans'][0] for t in p['targets'][:2]]
    assert '而是包含意志上決定與對方發生關係' in negative['exact_text']
    assert positive['exact_text']=='當人在意志上決定與對方行淫時，即使尚未實行，也已犯罪。'
    assert negative['end_char']==positive['start_char']
    assert old.NEW!=m.NEW and old.VERSION!=m.VERSION
    with pytest.raises(ValueError):old.validate_packet(p)


@pytest.mark.parametrize('mutation',['old_postimage','missing_condition','endpoint','pin','evidence','population','authority'])
def test_new_authority_does_not_expand_scope(monkeypatch,mutation):
    p=case(monkeypatch)
    if mutation=='old_postimage':p['claim_revision']['after']['statement']=old.NEW
    if mutation=='missing_condition':p['targets'][1]['after_link']['component_locator']=m.locator(p['claim_revision']['after'],'馬太福音所說的內心姦淫，不是單指看見異性並覺得對方漂亮，')
    if mutation=='endpoint':p['targets'][0]['after_link']['viewpoint_id']='foreign'
    if mutation=='pin':p['targets'][1]['after_link']['pinned_claim_revision']=2
    if mutation=='evidence':p['claim_revision']['after']['evidence_step_ids']=[]
    if mutation=='population':p['targets'].pop()
    if mutation=='authority':p['policy']['future_claim_edits']=True
    with pytest.raises(ValueError):m.validate_packet(p)


def test_separate_single_use_reservation_preserves_previous_budget(monkeypatch,tmp_path):
    source=tmp_path/'g037-owner-claim-statement-repair-20261005-v2';source.mkdir()
    for n in ('packet.json','before.json','raw-gpt.json','raw-gpt.request-encoding.json','failure.json'):(source/n).write_text('{}')
    old_budget=tmp_path/'g037-owner-context-equivalence-review-20261005-v1';old_budget.mkdir()
    reservation=old_budget/'owner-claim-statement-revision-start.json';reservation.write_text('old reservation')
    monkeypatch.setattr(m,'validate_previous_refusal',lambda base:None)
    m.reserve(tmp_path/'new',tmp_path)
    assert reservation.read_text()=='old reservation'
    with pytest.raises(ValueError,match='already reserved'):m.reserve(tmp_path/'repeat',tmp_path)


def test_previous_refusal_integrity_is_not_owner_approval(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'read_artifact',lambda path:{'artifact_sha256':'forged'})
    with pytest.raises(ValueError,match='identity drift'):m.validate_previous_refusal(tmp_path)

@pytest.mark.parametrize('mutation',[None,'model','backend','prompt','encoding','refusal','package','authority'])
def test_v2_store_contract_requires_exact_fresh_double_review_and_encoding(monkeypatch,tmp_path,mutation):
    from types import SimpleNamespace
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    from backend.pipeline import viewpoint_audit_link_repair as r
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    monkeypatch.setattr(m,'validate_previous_refusal',lambda base:None)
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
    docs[tmp_path/'g037-owner-claim-statement-repair-20261005-v2/owner-claim-condition-revision-start.json']=deepcopy(auth)
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

