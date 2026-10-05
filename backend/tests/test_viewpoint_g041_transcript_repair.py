from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.pipeline import viewpoint_g041_transcript_repair as m


def case(monkeypatch, tmp_path):
    old_text='old quote'; new_text='new quote'
    projection=SimpleNamespace(body_sha256='new-body',spoken_text_sha256='new-text',
        editorial_structure_sha256='structure',editorial_topology_sha256='topology',
        spoken_rows=[{'text':new_text,'index':1,'start_time':0,'end_time':202},
            *[{'text':'unchanged','index':n,'start_time':n,'end_time':n+1} for n in range(2,8)]])
    monkeypatch.setattr(m,'verify_source',lambda report:(projection,[old_text,*['unchanged']*6]))
    monkeypatch.setattr(m,'stable_decided_at',lambda root:'2026-10-05T00:00:00Z')
    source={'source_id':m.SOURCE,'revision':1,'source_sha256':'old-body','source_body_sha256':'old-body',
        'source_file_sha256':'old-file','source_text_sha256':'old-text','anchor_binding_sha256':'old-body'}
    states={('source_documents',m.SOURCE):source}
    report={'source_versions':{'registry_source':source,'registry_source_payload_sha256':m.sha256_json(source),
        'old_body_sha256':'old-body','new_file_sha256':'new-file'},
        'all_source_fragment_inventory':[],'changed_paragraph_claims':[],
        'changed_paragraph_evidence':[],'active_links':[]}
    proposal={'proposed_exact_quote_changes':[],'proposed_evidence_changes':[],
        'proposed_claim_anchor_metadata_changes':[]}
    for n in range(596):
        changed=n<5; paragraph='S0001' if changed else 'S0002'
        quote=old_text if changed else 'unchanged'
        row={'fragment_id':f'F{n}','source_id':m.SOURCE,'revision':1,'source_sha256':'old-body',
            'paragraph_key':paragraph,'paragraph_text_sha256':m.text_sha(quote),
            'verbatim_excerpt':quote,'verbatim_excerpt_sha256':m.text_sha(quote),
            'source_segment_index':1 if changed else 2,'media_time':0 if changed else 2,'media_end_time':202 if changed else 3}
        states['source_fragments',row['fragment_id']]=row
        report['all_source_fragment_inventory'].append([row['fragment_id'],1,m.sha256_json(row),not changed])
        if changed:proposal['proposed_exact_quote_changes'].append({'fragment_id':row['fragment_id'],
            'before_quote':quote,'before_payload_sha256':m.sha256_json(row),'proposed_after_quote':new_text})
    for n in range(7):
        cid=f'C{n}'; eid=f'E{n}'
        c={'claim_id':cid,'revision':1,'statement':'never edit','review_status':'ai_consensus_reviewed',
            'occurrences':[{'anchors':[{'evidence_id':eid,'proposed_highlight':{'text':old_text,'status':'proposed'}}]}]}
        e={'evidence_step_id':eid,'revision':1,'speaker':'professor','statement':'old evidence','produced_claim_ids':[]}
        for collection,p,key in [('claims',c,'changed_paragraph_claims'),('evidence_steps',e,'changed_paragraph_evidence')]:
            oid=p['claim_id'] if collection=='claims' else p['evidence_step_id']
            states[collection,oid]=p;report[key].append({'object_id':oid,'payload':p,'payload_sha256':m.sha256_json(p)})
        if n<2:
            proposal['proposed_claim_anchor_metadata_changes'].append({'claim_id':cid,'evidence_id':eid,
                'quote':old_text,'proposed_after_quote':new_text})
            proposal['proposed_evidence_changes'].append({'evidence_step_id':eid,
                'before_payload_sha256':m.sha256_json(e),'changed_fields':{'statement':'new evidence'}})
    link={'viewpoint_claim_link_id':m.LINK,'claim_id':'C1','viewpoint_id':'V1','revision':1,
        'pinned_claim_revision':1,'link_type':'qualifies','effective_state':'active',
        'component_locator':{'claim_sha256':m.semantic_record_sha(states['claims','C1']),
            'statement_component':'never edit','canonical_spans':[{'start_char':0,'end_char':10,'exact_text':'never edit'}]}}
    states['viewpoint_claim_links',m.LINK]=link
    report['active_links']=[{'object_id':m.LINK,'payload':link,'payload_sha256':m.sha256_json(link)}]
    states['canonical_viewpoints','V1']={'viewpoint_id':'V1','current_revision_id':'R1'}
    states['viewpoint_revisions','R1']={'viewpoint_revision_id':'R1','statement':'viewpoint unchanged'}
    return states,report,proposal,tmp_path


def test_full_exact_population_and_no_semantic_claim_cvp_edits(monkeypatch,tmp_path):
    states,r,p,root=case(monkeypatch,tmp_path);before=deepcopy(states)
    package,packet=m.materialize(states,r,p,root)
    assert states==before
    assert len(package['source_fragments'])==596
    assert all(f['revision']==2 and f['source_sha256']=='new-body' for f in package['source_fragments'])
    assert all(c['statement']=='never edit' for c in package['claims'])
    assert package['viewpoint_claim_links'][0]['link_type']=='qualifies'
    assert packet['mechanical_proof']['human_holds_released']==0
    assert packet['mechanical_proof']['claim_statement_changes']==0
    assert packet['draft_package_sha256']==m.sha256_json(package)


@pytest.mark.parametrize('mutation',['missing_fragment','added_fragment','fragment_revision','quote_hash','source_sha',
    'paragraph_hash','source_coordinate','start_time','end_time','missing_quote','source_predecessor',
    'evidence_predecessor','claim_statement','anchor_ambiguous','anchor_quote','human_hold','extra_link','pinned_consumer'])
def test_materializer_fails_closed(monkeypatch,tmp_path,mutation):
    s,r,p,root=case(monkeypatch,tmp_path)
    if mutation=='missing_fragment':del s['source_fragments','F0']
    elif mutation=='added_fragment':s['source_fragments','extra']={**s['source_fragments','F0'],'fragment_id':'extra'}
    elif mutation=='source_predecessor':s['source_documents',m.SOURCE]={**s['source_documents',m.SOURCE],'revision':2}
    elif mutation=='evidence_predecessor':s['evidence_steps','E0']={**s['evidence_steps','E0'],'revision':2}
    elif mutation=='claim_statement':s['claims','C0']={**s['claims','C0'],'statement':'new theology'}
    elif mutation in {'anchor_ambiguous','anchor_quote','human_hold'}:
        c=deepcopy(s['claims','C0']);s['claims','C0']=c
        if mutation=='anchor_ambiguous':c['occurrences'][0]['anchors']*=2
        elif mutation=='anchor_quote':c['occurrences'][0]['anchors'][0]['proposed_highlight']['text']='elsewhere'
        else:c['review_status']='human_review_required'
        # Rebind fixture predecessor to test the separate anchor/hold gate.
        r['changed_paragraph_claims'][0].update(payload=c,payload_sha256=m.sha256_json(c))
    elif mutation=='extra_link':s['viewpoint_claim_links','extra']={**s['viewpoint_claim_links',m.LINK],'viewpoint_claim_link_id':'extra'}
    elif mutation=='pinned_consumer':s['argument_routes','route']={'claim_id':'C0'}
    elif mutation=='missing_quote':p['proposed_exact_quote_changes'][0]['proposed_after_quote']='not in source'
    else:
        field={'fragment_revision':'revision','quote_hash':'verbatim_excerpt_sha256','source_sha':'source_sha256',
            'paragraph_hash':'paragraph_text_sha256','source_coordinate':'source_segment_index',
            'start_time':'media_time','end_time':'media_end_time'}[mutation]
        f=deepcopy(s['source_fragments','F0']);f[field]=999 if field in {'revision','source_segment_index','media_time','media_end_time'} else 'drift'
        s['source_fragments','F0']=f
        if mutation!='fragment_revision':
            r['all_source_fragment_inventory'][0][2]=m.sha256_json(f)
            p['proposed_exact_quote_changes'][0]['before_payload_sha256']=m.sha256_json(f)
    with pytest.raises(ValueError):m.materialize(s,r,p,root)


@pytest.mark.parametrize('mutation',[None,'refusal','informational_problem','missing','duplicate','foreign','extra_field'])
def test_one_complete_fresh_review_cannot_be_partial_or_forged(mutation):
    packet={'changed_paragraph_claims':[{'claim_id':f'C{n}'} for n in range(7)]}
    response={'verdicts':[{'claim_id':f'C{n}','approved':True,'reason':'exact','problems':[]} for n in range(7)]}
    if mutation=='refusal':response['verdicts'][0]['approved']=False
    if mutation=='informational_problem':response['verdicts'][0]['problems']=['not approved']
    if mutation=='missing':response['verdicts'].pop()
    if mutation=='duplicate':response['verdicts'][-1]['claim_id']='C0'
    if mutation=='foreign':response['verdicts'][-1]['claim_id']='outside973'
    if mutation=='extra_field':response['approved']=True
    if mutation is None:assert m.require_approval(response,packet)==response
    else:
        with pytest.raises(ValueError):m.require_approval(response,packet)


def test_model_transport_is_separate_new_subscription_identity(monkeypatch):
    monkeypatch.setattr(m,'subscription_client',lambda provider,model,effort:SimpleNamespace(model=model,
        backend=f'{provider}_subscription',reasoning_effort=effort))
    for provider,model in [('codex','gpt-6.1-sol'),('claude','claude-opus-5-5')]:
        a=m.adapter(provider,model)
        assert a.model_id==model and a.max_request_bytes==500000
        assert a.backend==f'{provider}_subscription'
        assert a.request_encoding_proof({'same':['long content '*100]*4})
