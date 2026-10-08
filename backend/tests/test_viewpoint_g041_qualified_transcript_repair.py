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


@pytest.mark.parametrize('provider',['gpt','opus'])
@pytest.mark.parametrize('drift',[None,'model','backend','prompt','config','request','raw_seal',
    'response_sha','encoding_sha','encoding_file','ceiling','rejected','population','report'])
def test_saved_complete_reviews_keep_actual_identity_integrity_and_approval(monkeypatch,tmp_path,provider,drift):
    packet={'changed_paragraph_claims':[{'claim_id':f'C{n}'} for n in range(7)]}
    docs={};reviews={};raws=[];encoding={'lossless':'original'}
    adapters={}
    for label,backend,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        response={'verdicts':[{'claim_id':c['claim_id'],'approved':True,'reason':'exact original','problems':[]}
            for c in packet['changed_paragraph_claims']]}
        a=SimpleNamespace(backend=f'{backend}_subscription',prompt_sha256='prompt',generation_config_sha256='config',
            request_encoding_proof=lambda p:encoding,request_bytes=lambda p:100)
        adapters[backend]=a
        raw={'model_id':model,'backend':a.backend,'prompt_sha256':'prompt','generation_config_sha256':'config',
            'request_payload_sha256':m.old.sha256_json(packet),'request_encoding_proof_sha256':m.old.sha256_json(encoding),
            'response_sha256':m.old.sha256_json(response),'response':response}
        raw['artifact_sha256']=m.old.sha256_json(raw)
        sealed={'schema_version':'wang_model_request_encoding_proof_v1',**encoding}
        docs[f'raw-{label}.json']=raw
        docs[f'raw-{label}.request-encoding.json']={**sealed,'artifact_sha256':m.old.sha256_json(sealed)}
        reviews[label]=m.old.require_approval(response,packet);raws.append(deepcopy(raw))
    docs['review-report.json']={'status':'dual_review_approved_not_applied',
        'packet_sha256':m.old.sha256_json(packet),'reviews':reviews,'master_data_mutations':0}
    raw=docs[f'raw-{provider}.json']
    if drift in {'model','backend','prompt','config','request'}:
        raw[{'model':'model_id','backend':'backend','prompt':'prompt_sha256','config':'generation_config_sha256',
            'request':'request_payload_sha256'}[drift]]='different'
        raw['artifact_sha256']=m.old.sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'})
    elif drift=='raw_seal':raw['artifact_sha256']='different'
    elif drift=='response_sha':raw['response_sha256']='different'
    elif drift=='encoding_sha':raw['request_encoding_proof_sha256']='different'
    elif drift=='encoding_file':docs[f'raw-{provider}.request-encoding.json']['artifact_sha256']='different'
    elif drift=='ceiling':adapters['codex' if provider=='gpt' else 'claude'].request_bytes=lambda p:500001
    elif drift in {'rejected','population'}:
        if drift=='rejected':raw['response']['verdicts'][0].update(approved=False,problems=['real refusal'])
        else:raw['response']['verdicts'].pop()
        raw['response_sha256']=m.old.sha256_json(raw['response'])
        raw['artifact_sha256']=m.old.sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'})
    elif drift=='report':docs['review-report.json']['master_data_mutations']=1
    monkeypatch.setattr(m,'adapter',lambda backend,model:adapters[backend])
    monkeypatch.setattr(m.old,'read_artifact',lambda path:deepcopy(docs[path.name]))
    monkeypatch.setattr(m,'saved_before',lambda *a:pytest.fail('old full Registry predecessor reader'))
    monkeypatch.setattr(m,'reviewed_inputs',lambda *a:pytest.fail('old writer reconstruction'))
    if drift is None:assert m.saved_reviews(packet,tmp_path)==(reviews,raws)
    else:
        with pytest.raises(ValueError):m.saved_reviews(packet,tmp_path)


@pytest.mark.parametrize('drift',[None,'version','root','packet','draft','authority','reservation',
    'reservation_packet','reservation_version','reservation_root','human_approval','repeat_reviews',
    'refusal_bindings','refusal_file','raws','impact','scope','source'])
def test_completed_embedded_review_preserves_exact_owner_scope_and_original_refusals(monkeypatch,drift):
    draft={'entire':'original draft'};raws=[{'exact':'actual saved raw'}]
    pins=[{'path':'original-refusal','file_sha256':'physical'}]
    packet={'draft_package_sha256':m.old.sha256_json(draft),'owner_authorization':'bounded continue',
        'prior_refusal_bindings':pins,
        'impact_binding':{'path':str(m.old.IMPACT/'impact-report.json'),'file_sha256':m.old.REPORT_SHA},
        'scope_proposal_binding':{'path':str(m.old.IMPACT/'reconciliation-scope-proposal.json'),'file_sha256':m.old.PROPOSAL_SHA}}
    reservation={'packet_sha256':m.old.sha256_json(packet),'schema_version':m.VERSION,'output_root':str(m.ROOT),
        'human_content_approval':False,'fresh_reviews_per_provider':1,'prior_refusal_bindings':pins}
    docs={'packet.json':deepcopy(packet),'draft-package.json':draft,'owner-authorization.json':deepcopy(reservation),
        'owner-qualified-transcript-start.json':deepcopy(reservation)}
    app={'schema_version':m.VERSION,'review_root':str(m.ROOT),'review_packet':packet,
        'user_authorization':'bounded continue','review_artifacts':raws}
    if drift in {'version','root','packet','authority','raws'}:
        app[{'version':'schema_version','root':'review_root','packet':'review_packet',
            'authority':'user_authorization','raws':'review_artifacts'}[drift]]='different'
    elif drift=='draft':draft['entire']='different'
    elif drift=='reservation':docs['owner-authorization.json']['packet_sha256']='different'
    elif drift in {'reservation_packet','reservation_version','reservation_root','human_approval','repeat_reviews','refusal_bindings'}:
        key={'reservation_packet':'packet_sha256','reservation_version':'schema_version','reservation_root':'output_root',
            'human_approval':'human_content_approval','repeat_reviews':'fresh_reviews_per_provider','refusal_bindings':'prior_refusal_bindings'}[drift]
        for name in ('owner-authorization.json','owner-qualified-transcript-start.json'):
            docs[name][key]={'human_approval':True,'repeat_reviews':2,'refusal_bindings':[]}.get(drift,'different')
    elif drift in {'impact','scope'}:
        key='impact_binding' if drift=='impact' else 'scope_proposal_binding'
        docs['packet.json'][key]['file_sha256']='different'
        app['review_packet']=docs['packet.json']
        for name in ('owner-authorization.json','owner-qualified-transcript-start.json'):
            docs[name]['packet_sha256']=m.old.sha256_json(docs['packet.json'])
    calls=[]
    def verify(pin):
        if drift=='refusal_file':raise ValueError('original refusal physical SHA drift')
        calls.append(pin)
    from backend.pipeline import source_reconciliation as c
    monkeypatch.setattr(c,'verify_file',verify)
    monkeypatch.setattr(m.old,'read_artifact',lambda path:deepcopy(docs[path.name]))
    monkeypatch.setattr(m,'saved_reviews',lambda p:({},raws))
    monkeypatch.setattr(m.old,'frozen_inputs',lambda:({'exact':'scope'},{}))
    def verify_source(report):
        if drift=='source':raise ValueError('physical source changed')
        assert report=={'exact':'scope'}
    monkeypatch.setattr(m.old,'verify_source',verify_source)
    monkeypatch.setattr(m,'saved_before',lambda *a:pytest.fail('historical full Registry reader'))
    monkeypatch.setattr(m,'reviewed_inputs',lambda *a:pytest.fail('historical writer reconstruction'))
    if drift is None:
        assert m.completed_review_inputs({'source_repair_application':app})==packet
        assert calls==pins
    else:
        with pytest.raises(ValueError):m.completed_review_inputs({'source_repair_application':app})


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
