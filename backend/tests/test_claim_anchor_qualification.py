from copy import deepcopy
import pytest
from backend.pipeline import claim_anchor_qualification as m
from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json


def saved_anchor_draft():
    from pathlib import Path
    from backend.pipeline import source_reconciliation_runner as r
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-e60b6130e0f7c81e810f4195c47f9eda0830bb1110e48c7999cc6fcfbe4e1e2a/actions/source/CVPA-c63afc3b70665e9d3209f9e1b75bda5443a5b26c3f9582a57b162e963fca4319')
    if not root.exists():pytest.skip('actual saved anchor action unavailable')
    frozen=r.body(r.read(root/'scoped-materials.json'))
    raw=r.validate_raw(root,'proposal_schema_recovery',r.body(r.read(root/'proposal_schema_recovery.packet.json')))
    return root,frozen,raw


def test_actual_saved_draft_is_full_review_only_and_raw_immutable():
    import hashlib
    from backend.pipeline import source_reconciliation_runner as r
    from backend.pipeline.source_review_input_repair import add_receipt
    root,f,raw=saved_anchor_draft()
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    original=deepcopy(raw['response'])
    proposal=r.effective_proposal(f,raw['response'])
    packet=add_receipt(f,raw['response'],r.initial_review_base(f,proposal))
    missing=packet['anchor_draft_missing_dispositions']
    assert len(missing)==2 and all(k.startswith('source_fragments/') for k in missing)
    assert len(packet['program_before_review']['records'])==2
    assert packet['unresolved_proposal_review']['content_write_authority'] is False
    with pytest.raises(ValueError,match='exactly one disposition'):c.review_packet(f,proposal)
    keys={d.record_key for d in proposal.dispositions}|set(missing)
    response={'proposal_sha256':packet['proposal_sha256'], 'verdicts':[
        {'record_key':k,'approved':True,'problems':[],'reason':'full postimage reviewed'} for k in sorted(keys)]}
    assert c.validate_review(packet,response) is False
    assert raw['response']==original
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in files.items())


@pytest.mark.parametrize('fault',['missing_existing','extra','duplicate','unresolved','foreign_quote','repeat_quote'])
def test_actual_saved_anchor_draft_negative_gates(fault):
    from backend.pipeline import source_reconciliation_runner as r
    _,f,raw=saved_anchor_draft()
    p=r.effective_proposal(f,raw['response'])
    if fault=='missing_existing':p.dispositions.pop(0)
    if fault=='extra':p.dispositions.append(p.dispositions[0].model_copy(update={'record_key':'source_fragments/foreign'}))
    if fault=='duplicate':p.dispositions.append(p.dispositions[0])
    if fault=='unresolved':p.dispositions[0].action='unresolved'
    if fault in {'foreign_quote','repeat_quote'}:
        response=deepcopy(raw['response']);row=next(x for x in response['records'] if x['collection']=='source_fragments')
        import json
        value=json.loads(row['payload_json'])
        if fault=='foreign_quote':value['source_id']='foreign';row['payload_json']=json.dumps(value)
        else:f['after'][0]['paragraphs'][value['paragraph_key']]['text']+=value['verbatim_excerpt']
        with pytest.raises(ValueError):r.effective_proposal(f,response)
        return
    with pytest.raises(ValueError):r.initial_review_base(f,p)


def test_actual_corrected_complete_postimages_require_real_final_approval():
    from backend.pipeline import source_reconciliation_runner as r
    root,f,_=saved_anchor_draft()
    if not (root/'correction.raw.json').exists():pytest.skip('actual correction not saved')
    raw=r.validate_raw(root,'correction',r.body(r.read(root/'correction.packet.json')))
    p,receipt=r.corrected_proposal(root,f,raw['response'])
    assert receipt and receipt['automatic_content_approval'] is False
    before=deepcopy(p.model_dump())
    packet=c.review_packet(f,p,complete_anchor_review=True)
    assert p.model_dump()==before
    assert len(packet['anchor_create_permission_receipt']['exact_new_fragment_ids'])==2
    assert packet['anchor_create_permission_receipt']['automatic_content_approval'] is False
    verdicts=[{'record_key':d.record_key,'approved':True,'problems':[],'reason':'own-source complete postimage approved'} for d in p.dispositions]
    answer={'proposal_sha256':packet['proposal_sha256'],'verdicts':verdicts}
    assert c.validate_review(packet,answer)
    answer['verdicts'][0].update(approved=False,problems=['real substantive source objection'])
    assert not c.validate_review(packet,answer)
    with pytest.raises(ValueError):c.review_packet(f,p)  # Legacy/default never upgrades.
    for fault in ('claim_unresolved','missing_postimage','foreign_parent','missing_disposition','words'):
        changed=p.model_copy(deep=True)
        if fault=='claim_unresolved':changed.dispositions[0].action='unresolved'
        if fault=='missing_postimage':changed.records.pop()
        if fault=='foreign_parent':changed.records[-1].payload['source_id']='foreign'
        if fault=='missing_disposition':changed.dispositions.pop()
        if fault=='words':changed.records[0].payload['statement']='new theology'
        with pytest.raises(ValueError):c.review_packet(f,changed,complete_anchor_review=True)


@pytest.mark.parametrize('fault',[None,'missing_claim','foreign_run','changed_words','rejected_initial'])
def test_saved_component_selection_never_creates_or_borrows_content(monkeypatch,fault):
    from backend.pipeline import source_reconciliation_runner as r
    root,f,_=saved_anchor_draft()
    if not (root/'correction.raw.json').exists():pytest.skip('actual correction unavailable')
    raw=r.validate_raw(root,'correction',r.body(r.read(root/'correction.packet.json')))
    response=deepcopy(raw['response'])
    import json
    row=next(x for x in response['records'] if x['collection']=='claims')
    if fault=='changed_words':
        payload=json.loads(row['payload_json']);payload['statement']='other meaning';row['payload_json']=json.dumps(payload)
    if fault=='rejected_initial':
        original=r.validate_raw
        def reject(*args,**kwargs):
            value=deepcopy(original(*args,**kwargs))
            if args[1]=='initial_review':value['response']['verdicts'][0]['approved']=False
            return value
        monkeypatch.setattr(r,'validate_raw',reject)
    if fault in {'changed_words','rejected_initial'}:
        with pytest.raises(ValueError):r.corrected_proposal(root,f,response)
        return
    p,receipt=r.corrected_proposal(root,f,response)
    from backend.pipeline.claim_owner_revision import dependency_direction
    from backend.pipeline.cvp_sql_flow import binding
    value={'run_id':'RUN','reviewed':{'evidence':{'approval':{},'package':{'payload':{
        'claims':[p.records[0].payload], 'source_repair_application':{'run_id':'RUN','materials':binding(root/'scoped-materials.json')}}}}}}
    if fault=='missing_claim':value['reviewed']['evidence']['package']['payload']['claims']=[]
    if fault=='foreign_run':value['run_id']='FOREIGN'
    if fault:
        with pytest.raises(ValueError):dependency_direction(value)
    else:
        proof,cid=dependency_direction(value)
        assert cid=='CL-0007' and proof==f['manifest']['anchor_qualification']
        original=r.effective_proposal(f,r.validate_raw(root,'proposal_schema_recovery',r.body(r.read(root/'proposal_schema_recovery.packet.json')))['response'])
        assert p.records[0]==original.records[0]
        assert p.dispositions[0]==original.dispositions[0]
        assert response==raw['response']


def fixture(monkeypatch):
    claim={'claim_id':'C','revision':1,'statement':'unchanged',
        'evidence_step_ids':['E','OTHER'],'eligible_evidence_step_ids':['OTHER'],
        'withheld_evidence_step_ids':['E'],'occurrences':[{'transcript_id':'own',
            'anchors':[{'proposed_highlight':{'text':'bad'}},{'proposed_highlight':{'text':'good'}}]}]}
    ev={'evidence_step_id':'E','revision':1,'statement':'same evidence','source_fragment_id':'F',
        'source_fragment_ids':['F'],'support_eligibility':'withheld_unreviewed','valid_for_identity_review':False}
    old={'claims/C':claim,'evidence_steps/E':ev,'source_fragments/F':{'fragment_id':'F','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'own'}}
    f={'manifest':{'source_id':'S','before_source':{'path':'x','file_sha256':'0'*64},
        'current_sources':[{'path':'x','file_sha256':'0'*64}], 'authorization':{'path':'a','file_sha256':'0'*64},
        'authorized_paragraph_keys':[], 'permissions':[{'collection':'claims','object_id':'C','fields':['occurrences']}],
        'groups':[{'group_key':'G','claim_ids':['C']}],'campaign_claim_ids':['C'],
        'accepted_membership':{'path':'m','file_sha256':'0'*64},'created_at':'test',
        'anchor_qualification':{'path':'d','file_sha256':'0'*64}},
        'before_records':c.rows(old),'after':[{'body_sha256':'b','paragraphs':{'S0001':{'text':'own'}}}], 'context_sources':[]}
    monkeypatch.setattr(m,'direction',lambda *a,**kw:{'claim_id':'C','evidence_id':'E','withdraw_anchor_quote':'bad'})
    monkeypatch.setattr(c,'containing_anchor_fragments',lambda *a,**kw:[{}])
    new=deepcopy(claim);new['occurrences'][0]['anchors'].pop(0)
    new['eligible_evidence_step_ids'].append('E');new['withheld_evidence_step_ids']=[]
    nev={**ev,'support_eligibility':'eligible_with_label','valid_for_identity_review':True}
    return f,claim,new,ev,nev


def test_exact_anchor_and_qualification_only(monkeypatch):
    f,old,new,ev,nev=fixture(monkeypatch)
    assert m.validate_delta(f,old,new,'claims/C')
    assert not m.validate_delta(f,ev,nev,'evidence_steps/E')
    assert old['occurrences'][0]['anchors'][0]['proposed_highlight']['text']=='bad'


@pytest.mark.parametrize('fault',['words','other_anchor','all_anchors','occurrence_identity','ownership','bucket','binding','evidence_words','not_eligible','not_valid','foreign'])
def test_scope_counterexamples(monkeypatch,fault):
    f,old,new,ev,nev=fixture(monkeypatch);k='claims/C'
    if fault=='words':new['statement']='changed'
    if fault=='other_anchor':new['occurrences'][0]['anchors'][0]['proposed_highlight']['text']='changed'
    if fault=='all_anchors':new['occurrences'][0]['anchors']=[]
    if fault=='occurrence_identity':new['occurrences'][0]['transcript_id']='foreign'
    if fault=='ownership':new['evidence_step_ids']=[]
    if fault=='bucket':new['eligible_evidence_step_ids']=['E']
    if fault in {'binding','evidence_words','not_eligible','not_valid'}:
        k='evidence_steps/E';old,new=ev,nev
        if fault=='binding':new['source_fragment_id']='OTHER'
        if fault=='evidence_words':new['statement']='changed'
        if fault=='not_eligible':new['support_eligibility']='eligible'
        if fault=='not_valid':new['valid_for_identity_review']=False
    if fault=='foreign':k='claims/FOREIGN'
    with pytest.raises(ValueError):m.validate_delta(f,old,new,k)


def test_binding_physical_proof_required(monkeypatch):
    f,old,new,ev,nev=fixture(monkeypatch)
    monkeypatch.setattr(c,'containing_anchor_fragments',lambda *a,**kw:[])
    with pytest.raises(ValueError):m.validate_delta(f,ev,nev,'evidence_steps/E')


def test_legacy_manifest_shape_unchanged(monkeypatch):
    f,*_=fixture(monkeypatch);f['manifest'].pop('anchor_qualification')
    assert 'anchor_qualification' not in c.Manifest.model_validate(f['manifest']).model_dump()


@pytest.mark.parametrize('fault',[None,'owner','seal','human_approval','shared_owner','pin','human_hold',
    'human_reviewer','substantive','human_reason','statement','ambiguous_anchor','extra_permission','extra_correction'])
def test_authority_never_waives_content_or_real_holds(monkeypatch,fault):
    import json
    f,claim,_,ev,_=fixture(monkeypatch)
    # Restore real direction validator after the delta-only fixture.
    monkeypatch.undo()
    claim.update(review_status='human_review_required',reviewed_by='independent_ai_review')
    ev.update(review_status='independent_terminal_reviewed',produced_claim_ids=['C'])
    permissions=[{'collection':'claims','object_id':'C','fields':['occurrences','eligible_evidence_step_ids','withheld_evidence_step_ids']},
        {'collection':'evidence_steps','object_id':'E','fields':['support_eligibility','valid_for_identity_review']}]
    f['manifest']['permissions']=permissions
    d={'schema_version':m.VERSION,'authorized_by':'owner','authorization_quote':'resolve exact anchor and qualification',
        'human_content_approval':False,'source_id':'S','claim_id':'C','evidence_id':'E','maximum_corrections':1,
        'withdraw_anchor_quote':'bad','original_review':{'path':'r','file_sha256':'0'*64},
        'before_pins':{'claims/C':[1,record_content_sha(claim),False],'evidence_steps/E':[1,record_content_sha(ev),False]}}
    review={'claim_reviews':[{'claim_id':'C','decision':'changes_suggested','human_review_reason':'',
        'proposed_statement':'','issues':[{'issue_type':'insufficient_anchor','severity':'low'}]}]}
    if fault=='owner':d['authorized_by']='system'
    if fault=='human_approval':d['human_content_approval']=True
    if fault=='shared_owner':ev['produced_claim_ids'].append('OTHER')
    if fault=='pin':d['before_pins']['claims/C'][0]=2
    if fault=='human_hold':claim['review_status']='human_hold'
    if fault=='human_reviewer':claim['reviewed_by']='owner'
    if fault=='substantive':review['claim_reviews'][0]['issues'][0]['issue_type']='source_conflict'
    if fault=='human_reason':review['claim_reviews'][0]['human_review_reason']='actual unresolved attribution'
    if fault=='statement':review['claim_reviews'][0]['proposed_statement']='different teaching'
    if fault=='ambiguous_anchor':claim['occurrences'][0]['anchors'].append(deepcopy(claim['occurrences'][0]['anchors'][0]))
    if fault=='extra_permission':permissions[0]['fields'].append('statement')
    if fault=='extra_correction':d['maximum_corrections']=2
    d['artifact_sha256']=sha256_json(d)
    if fault=='seal':d['artifact_sha256']='0'*64
    monkeypatch.setattr(c,'bound_bytes',lambda b,*a:json.dumps(review if (b.path if isinstance(b,c.FileBinding) else b['path'])=='r' else d).encode())
    manifest=c.Manifest.model_validate(f['manifest'])
    if fault:
        with pytest.raises(ValueError):m.direction(manifest,{'claims/C':claim,'evidence_steps/E':ev})
    else:assert m.direction(manifest,{'claims/C':claim,'evidence_steps/E':ev})==d
