from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.pipeline import viewpoint_g041_qualified_transcript_repair as m


def case(monkeypatch, tmp_path):
    monkeypatch.setattr(m.old,'stable_decided_at',lambda root:'2026-10-05T00:00:00Z')
    monkeypatch.setattr(m.old,'file_sha256',lambda path:'rules-sha')
    claim={'claim_id':m.CLAIM,'revision':1,'statement':m.OLD_STATEMENT,
        'review_status':'ai_consensus_reviewed','occurrences':[{'anchors':[{'unchanged':'exact'}]}]}
    evidence={'evidence_step_id':m.EVIDENCE,'revision':1,'statement':m.EVIDENCE_BEFORE,
        'produced_claim_ids':[], 'speaker':'professor'}
    states={('claims',m.CLAIM):claim,('evidence_steps',m.EVIDENCE):evidence}
    for lid, kind in m.LINKS.items():
        quote='女子是不得不陷入姦淫。' if kind=='supports' else m.OLD_STATEMENT.split('，而女子')[0]
        start=m.OLD_STATEMENT.index(quote)
        states['viewpoint_claim_links',lid]={'claim_id':m.CLAIM,'viewpoint_claim_link_id':lid,
            'revision':1,'pinned_claim_revision':1,'link_type':kind,'viewpoint_id':'target',
            'effective_state':'active','evidence_bindings':[{'old':'binding'}],
            'component_locator':{'claim_sha256':m.old.semantic_record_sha(claim),
                'statement_component':quote,'canonical_spans':[{'start_char':start,'end_char':start+len(quote),'exact_text':quote}]}}
    package={'package_id':'old','source_documents':[{'unchanged':'source'}],
        'source_fragments':[{'unchanged':'fragment'}],'claims':[], 'evidence_steps':[], 'viewpoint_claim_links':[]}
    packet={'mechanical_proof':{'human_holds_released':0,'frozen_claims':973,'frozen_groups':156}}
    return states,package,packet,tmp_path


def test_exact_two_semantic_fields_and_dependent_locator_changes(monkeypatch,tmp_path):
    s,p,k,r=case(monkeypatch,tmp_path); originals=deepcopy((s,p,k))
    package,packet=m.extend(s,p,k,r)
    assert (s,p,k)==originals
    assert package['claims'][0]['statement']==m.STATEMENT
    assert package['claims'][0]['occurrences']==s['claims',m.CLAIM]['occurrences']
    assert package['evidence_steps'][0]=={**s['evidence_steps',m.EVIDENCE], 'statement':m.EVIDENCE_AFTER,'revision':2}
    assert package['source_documents']==p['source_documents']
    assert package['source_fragments']==p['source_fragments']
    for link in package['viewpoint_claim_links']:
        prior=s['viewpoint_claim_links',link['viewpoint_claim_link_id']]
        for key in ('link_type','viewpoint_id','evidence_bindings'):
            assert link[key]==prior[key]
        assert link['pinned_claim_revision']==2
        locator=link['component_locator'];span=locator['canonical_spans'][0]
        assert m.STATEMENT[span['start_char']:span['end_char']]==span['exact_text']==locator['statement_component']
        if link['link_type']=='supports':assert span['exact_text']=='女子是不得不陷入姦淫。'
        else:assert '男子首次結婚' in span['exact_text']
    assert packet['draft_package_sha256']==m.old.sha256_json(package)
    assert packet['mechanical_proof']['claim_statement_changes']==1
    assert packet['mechanical_proof']['human_holds_released']==0


@pytest.mark.parametrize('mutation',['claim_statement','claim_revision','hold','evidence_statement',
    'evidence_revision','duplicate_claim','duplicate_evidence','missing_link','extra_link','type',
    'link_revision','pin','component','span','claim_sha','consumer'])
def test_closed_exact_scope(monkeypatch,tmp_path,mutation):
    s,p,k,r=case(monkeypatch,tmp_path)
    c=s['claims',m.CLAIM];e=s['evidence_steps',m.EVIDENCE]
    lid=next(iter(m.LINKS));link=s['viewpoint_claim_links',lid]
    if mutation=='claim_statement':c['statement']='different'
    elif mutation=='claim_revision':c['revision']=2
    elif mutation=='hold':c['review_status']='human_review_required'
    elif mutation=='evidence_statement':e['statement']='different'
    elif mutation=='evidence_revision':e['revision']=2
    elif mutation=='duplicate_claim':p['claims'].append(deepcopy(c))
    elif mutation=='duplicate_evidence':p['evidence_steps'].append(deepcopy(e))
    elif mutation=='missing_link':del s['viewpoint_claim_links',lid]
    elif mutation=='extra_link':s['viewpoint_claim_links','extra']=deepcopy(link)
    elif mutation=='type':link['link_type']='equivalent_full'
    elif mutation=='link_revision':link['revision']=2
    elif mutation=='pin':link['pinned_claim_revision']=2
    elif mutation=='component':link['component_locator']['statement_component']='different'
    elif mutation=='span':link['component_locator']['canonical_spans'][0]['start_char']+=1
    elif mutation=='claim_sha':link['component_locator']['claim_sha256']='drift'
    elif mutation=='consumer':s['argument_routes','route']={'claim_id':m.CLAIM}
    with pytest.raises(ValueError):m.extend(s,p,k,r)


def test_separate_subscription_review_and_lossless_transport(monkeypatch):
    monkeypatch.setattr(m.old,'subscription_client',lambda provider,model,effort:SimpleNamespace(model=model,
        backend=f'{provider}_subscription',reasoning_effort=effort))
    for provider,model in [('codex','gpt-6.1-sol'),('claude','claude-opus-5-5')]:
        a=m.adapter(provider,model)
        assert a.model_id==model and a.max_request_bytes==500000
        assert a.request_encoding_proof({'same':['long text '*100]*3})
        assert a.prompt_sha256!=m.old.adapter(provider,model).prompt_sha256


@pytest.mark.parametrize('mutation',[None,'raw_hash','response','model','backend','prompt','config','request',
    'encoding','sealed_encoding','registry','new_opus','applied','population','new_refusal'])
def test_prior_refusal_and_actual_transport_verified(monkeypatch,mutation):
    packet={'changed_paragraph_claims':[{'claim_id':m.PREFIX+f'CL{n:03}'} for n in range(1,8)]}
    response={'verdicts':[{'claim_id':c['claim_id'],'approved':c['claim_id']!=m.CLAIM,
        'reason':'saved exact','problems':['first marriage','passive'] if c['claim_id']==m.CLAIM else []}
        for c in packet['changed_paragraph_claims']]}
    encoding={'exact':'roundtrip'}
    sealed={'schema_version':'wang_model_request_encoding_proof_v1',**encoding}
    sealed={**sealed,'artifact_sha256':m.old.sha256_json(sealed)}
    raw={'model_id':'gpt-6.1-sol','backend':'codex_subscription','prompt_sha256':'prompt',
        'generation_config_sha256':'config','request_payload_sha256':m.old.sha256_json(packet),
        'response_sha256':m.old.sha256_json(response),'response':response,
        'request_encoding_proof_sha256':m.old.sha256_json(encoding)}
    raw['artifact_sha256']=m.old.sha256_json(raw)
    monkeypatch.setattr(m,'RAW_SHA',raw['artifact_sha256'])
    docs={'packet.json':packet,'raw-gpt.json':raw,'before.json':{'collections':[],'pre_fingerprint_sha256':'pre'},
        'raw-gpt.request-encoding.json':sealed}
    exists=set()
    if mutation in {'model','backend','prompt','config','request'}:
        raw[{'model':'model_id','backend':'backend','prompt':'prompt_sha256','config':'generation_config_sha256',
            'request':'request_payload_sha256'}[mutation]]='drift'
    elif mutation=='raw_hash':raw['artifact_sha256']='drift'
    elif mutation=='response':raw['response_sha256']='drift'
    elif mutation=='encoding':raw['request_encoding_proof_sha256']=m.old.sha256_json(sealed)
    elif mutation=='sealed_encoding':sealed['artifact_sha256']='drift'
    elif mutation=='registry':docs['before.json']['pre_fingerprint_sha256']='drift'
    elif mutation=='new_opus':exists.add(m.PRIOR/'raw-opus.json')
    elif mutation=='applied':exists.add(m.PRIOR/'apply-receipt.json')
    elif mutation=='population':response['verdicts'].pop()
    elif mutation=='new_refusal':response['verdicts'][0].update(approved=False,problems=['new'])
    monkeypatch.setattr(m.old,'read_artifact',lambda path:docs[path.name])
    monkeypatch.setattr(m.old,'file_sha256',lambda path:'physical')
    monkeypatch.setattr(m.old,'fingerprint',lambda states,collections:'pre')
    monkeypatch.setattr(m.old,'adapter',lambda *args:SimpleNamespace(model_id='gpt-6.1-sol',backend='codex_subscription',
        prompt_sha256='prompt',generation_config_sha256='config',request_encoding_proof=lambda p:encoding))
    monkeypatch.setattr(m.Path,'exists',lambda path:path in exists)
    if mutation is None:assert len(m.predecessor({}))==7
    else:
        with pytest.raises(ValueError):m.predecessor({})


@pytest.mark.parametrize('mutation',[None,'root','version','source','fragment','claim','evidence','link',
    'candidate','decision','review','authority','extra_collection'])
def test_store_contract_rebuilds_exact_body_and_fresh_identity(monkeypatch,mutation):
    expected={'package_id':'exact','source_documents':[{'revision':2}],
        'source_fragments':[{'revision':2}], 'claims':[{'statement':m.STATEMENT}],
        'evidence_steps':[{'statement':m.EVIDENCE_AFTER}], 'viewpoint_claim_links':[{'pin':2}],
        'viewpoint_identity_candidates':[{'fresh':True}], 'viewpoint_identity_decisions':[{'fresh':True}],
        'source_repair_application':{'schema_version':m.VERSION,'review_root':str(m.ROOT),
            'review_artifacts':['exact'], 'user_authorization':'continue'}}
    monkeypatch.setattr(m,'reviewed_inputs',lambda root:({}, {}, {}, {}, {}, []))
    monkeypatch.setattr(m,'package_for',lambda *args:deepcopy(expected))
    observed=deepcopy(expected)
    if mutation in {'root','version','review','authority'}:
        observed['source_repair_application'][{'root':'review_root','version':'schema_version',
            'review':'review_artifacts','authority':'user_authorization'}[mutation]]='drift'
    elif mutation=='extra_collection':observed['canonical_viewpoints']=[{'unauthorized':True}]
    elif mutation is not None:
        observed[{'source':'source_documents','fragment':'source_fragments','claim':'claims','evidence':'evidence_steps',
            'link':'viewpoint_claim_links','candidate':'viewpoint_identity_candidates','decision':'viewpoint_identity_decisions'}[mutation]]=[]
    if mutation is None:m.validate_package(observed)
    else:
        with pytest.raises(ValueError):m.validate_package(observed)


def test_complete_dry_run_json_equality_preserves_all_operations_and_events():
    in_memory={'operations':({'collection':'claims','payload':{'statement':'same','revision':2}},),
        'ignored_keys':(),'review_events':({'model':'gpt-6.1-sol','approved':True},)}
    stored={'operations':[{'collection':'claims','payload':{'statement':'same','revision':2}}],
        'ignored_keys':[],'review_events':[{'model':'gpt-6.1-sol','approved':True}]}
    assert in_memory!=stored and m.canonical_plan(in_memory)==stored
    for field in ('statement','revision'):
        altered=deepcopy(stored);altered['operations'][0]['payload'][field]='drift'
        assert m.canonical_plan(in_memory)!=altered
    altered=deepcopy(stored);altered['review_events'][0]['approved']=False
    assert m.canonical_plan(in_memory)!=altered


@pytest.mark.parametrize('apply,wrong_root',[(False,False),(True,False),(False,True),(True,True)])
def test_cli_keeps_review_and_zero_call_application_roots_separate(monkeypatch,tmp_path,apply,wrong_root):
    import sys
    root=tmp_path/'review'; application=tmp_path/'application'
    monkeypatch.setattr(m,'ROOT',root);monkeypatch.setattr(m,'APPLICATION_ROOT',application)
    monkeypatch.setattr(m.old,'BASE',tmp_path)
    (tmp_path/'immediate-recovery-20261002-v1').mkdir()
    correct=application if apply else root
    chosen=(root if apply else application) if wrong_root else correct
    calls=[]
    monkeypatch.setattr(m,'run',lambda path,review=False:calls.append(('review',path)))
    monkeypatch.setattr(m,'apply_reviewed',lambda path:calls.append(('apply',path)))
    monkeypatch.setattr(sys,'argv',['qualified-repair','--output-root',str(chosen)]+(['--apply-reviewed'] if apply else []))
    if wrong_root:
        with pytest.raises(ValueError):m.main()
        assert calls==[] and not chosen.exists()
    else:
        m.main()
        assert calls==[('apply' if apply else 'review',correct)]
