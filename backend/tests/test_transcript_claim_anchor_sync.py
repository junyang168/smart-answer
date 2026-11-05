from copy import deepcopy
import pytest

from backend.tests.test_transcript_evidence_apply import relocation
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres
from backend.pipeline import transcript_evidence_apply as ea, transcript_claim_anchor_sync as a
from backend.pipeline import transcript_claim_migration as m


def anchor(eid,quote,paragraph='S0001',time=0):
    return {'evidence_id':eid,'paragraph_key':paragraph,'media_time':time,
        'speaker':'professor','stance':'asserted','assertive':True,'evidence_type':'reasoning',
        'discourse_role':'既有論據角色','proposed_highlight':{'text':quote,'status':'proposed'}}


@pytest.fixture
def synced_evidence(relocation):
    store,eroot,old,published=relocation
    c=store.get_record_state('claims','C')['payload']
    c['occurrences']=[{'source_id':'S','transcript_id':'lecture','anchors':[
        anchor('E','教授原話',time=0),anchor('E','第二處是明確刪除的內容。','S0002',5)]}]
    store.apply_plan(store.plan_package({'claims':[c]}))
    ea.prepare(store,eroot,source_id='S',old_paths=[old],published=published,instruction='先搬遷Evidence')
    ea.apply(store,eroot)
    return store,eroot.parent/'anchors',eroot,published


def prepare(case):
    store,root,eroot,published=case
    return a.prepare(store,root,source_id='S',evidence_run=eroot,published=published,
                     instruction='只同步Claim原文定位；排除刪除與口頭語，不改論點或批准。')


def test_real_sql_sync_and_lost_response_recovery_preserve_content(synced_evidence):
    store,root,_,published=synced_evidence
    before=store.get_record_state('claims','C')['payload']; ev=store.get_record_state('evidence_steps','E')
    raw=published.read_bytes();p=prepare(synced_evidence)
    assert prepare(synced_evidence)==p
    report=m.read(root/'comparison.json')
    assert report['summary']=={'exact_current_evidence':1,'source_deleted':1}
    assert a.apply(store,root)['status']=='applied'
    assert a.apply(store,root)['status']=='already_applied'
    after=store.get_record_state('claims','C')['payload']
    assert after['statement']==before['statement']
    assert after['review_status']==before['review_status']
    assert store.get_record_state('evidence_steps','E')==ev and published.read_bytes()==raw
    anchors=after['occurrences'][0]['anchors']
    assert len(anchors)==1 and anchors[0]['media_time']==1 and anchors[0]['media_end_time']==5
    assert anchors[0]['source_fragment_id']=='F1'
    assert '第二處是明確刪除的內容' not in str(after)
    assert after['occurrences'][0]['anchor_migration_notes'][0]['reason']=='source_deleted'
    history=store.get_change_history(p['change_set_id'])
    assert history['operations'][0]['before']['occurrences'][0]['anchors'][1]['proposed_highlight']['text']=='第二處是明確刪除的內容。'


def test_fresh_rerun_is_a_read_only_noop(synced_evidence):
    store,root,eroot,published=synced_evidence
    prepare(synced_evidence);a.apply(store,root)
    nextroot=root.parent/'repeat'
    a.prepare(store,nextroot,source_id='S',evidence_run=eroot,published=published,instruction='再次同步')
    report=m.read(nextroot/'comparison.json')
    assert report['updates']=={}
    before=store.get_record_state('claims','C')
    assert a.apply(store,nextroot)['status']=='already_synchronized'
    assert store.get_record_state('claims','C')==before


def test_concurrent_claim_edit_is_not_overwritten(synced_evidence):
    store,root,_,_=synced_evidence;prepare(synced_evidence)
    c=store.get_record_state('claims','C')['payload'];c['statement']='另一個已保存的修改'
    store.apply_plan(store.plan_package({'claims':[c]}))
    with pytest.raises(ValueError,match='records changed'):a.apply(store,root)
    assert store.get_record_state('claims','C')['payload']['statement']=='另一個已保存的修改'


def test_forged_claim_words_and_anchor_role_are_rejected(synced_evidence):
    _,root,_,_=synced_evidence;prepare(synced_evidence)
    records=m.read(root/'records-before.json');report=m.read(root/'comparison.json')
    source=records['source_documents/S']['payload'];snapshot=ea.snap(source['source_path'])
    bad=deepcopy(report);bad['updates']['claims/C']['statement']='不准改'
    with pytest.raises(ValueError,match='Claim content'):a.validate_projection(records,bad,snapshot)
    bad=deepcopy(report);bad['anchors'][0]['after'][0]['assertive']=False
    with pytest.raises(ValueError,match='discourse annotation'):a.validate_projection(records,bad,snapshot)


def test_cross_paragraph_source_expands_anchor_and_keeps_annotation(synced_evidence):
    store,root,eroot,published=synced_evidence
    records={a.cm.source.key(*k):a.cm.model_state(v) for k,v in store.read_affected_context([('source_documents','S')]).items()}
    original=records['source_fragments/F1']['payload'];second=deepcopy(records['source_fragments/F1'])
    second['payload']={**original,'fragment_id':'F4','verbatim_excerpt':'第一處保留的',
        'verbatim_excerpt_sha256':m.sha('第一處保留的'.encode())}
    records['source_fragments/F4']=second
    records['evidence_steps/E']['payload']['source_fragment_ids']=['F4','F1']
    records['source_fragments/FOLD']={**second,'payload':{**original,'fragment_id':'FOLD',
        'anchor_state':'replaced_by_precise_fragments','replacement_fragment_ids':['F4','F1']}}
    prior=m.read(eroot/'records-before.json')
    prior['evidence_steps/E']['payload']['source_fragment_ids']=['FOLD']
    prior['source_fragments/FOLD']={**second,'payload':{**original,'fragment_id':'FOLD','verbatim_excerpt':'舊版整段原話'}}
    records['claims/C']['payload']['occurrences'][0]['anchors']=[anchor('E','舊版整段原話')]
    snapshot=ea.snap(published)
    report=a.compile_projection(records,prior,source_id='S',snapshot=snapshot)
    new=report['updates']['claims/C']['occurrences'][0]['anchors']
    assert len(new)==2 and {x['source_fragment_id'] for x in new}=={'F4','F1'}
    assert all(x['assertive'] is True and x['discourse_role']=='既有論據角色' for x in new)


def test_foreign_occurrence_and_superseded_history_are_preserved(synced_evidence):
    store,_,eroot,published=synced_evidence
    records={a.cm.source.key(*k):a.cm.model_state(v) for k,v in store.read_affected_context([('source_documents','S')]).items()}
    foreign={'source_id':'FOREIGN','anchors':[anchor('missing','別篇原話')]}
    records['claims/C']['payload']['occurrences'].append(foreign)
    old=deepcopy(records['claims/C']);old['payload']['claim_id']='OLD';old['payload']['review_status']='superseded'
    records['claims/OLD']=old
    report=a.compile_projection(records,m.read(eroot/'records-before.json'),source_id='S',snapshot=ea.snap(published))
    assert report['updates']['claims/C']['occurrences'][-1]==foreign
    assert 'claims/OLD' not in report['updates'] and report['skipped_superseded_claims']==['claims/OLD']


def test_legacy_source_name_alias_is_not_skipped(synced_evidence):
    store,_,eroot,published=synced_evidence
    records={a.cm.source.key(*k):a.cm.model_state(v) for k,v in store.read_affected_context([('source_documents','S')]).items()}
    records['claims/C']['payload']['occurrences'][0]['source_id']='lecture'
    report=a.compile_projection(records,m.read(eroot/'records-before.json'),source_id='S',snapshot=ea.snap(published))
    assert 'claims/C' in report['updates']
    assert report['updates']['claims/C']['occurrences'][0]['source_id']=='lecture'
    assert not a.own_occurrence({'source_id':'FOREIGN','transcript_id':'lecture'},records['source_documents/S']['payload'])


def test_historical_ai_review_is_not_recreated_and_store_rejects_authority_bypass(synced_evidence):
    store,root,_,_=synced_evidence
    store.record_review('claims','C',decision='ai_consensus_reviewed',reviewer_kind='ai',reviewer_id='synthetic-test-review',reason='historical test fixture')
    before=store.get_record_state('claims','C')['payload']
    p=prepare(synced_evidence);a.apply(store,root)
    after=store.get_record_state('claims','C')['payload']
    for field in ['review_status','reviewed_by','reviewed_at','review_note','statement']:
        assert after[field]==before[field]
    assert store.get_change_history(p['change_set_id'])['review_events']==[]
    with pytest.raises(ValueError,match='guarded metadata plan'):
        store.plan_package(p['package'])
    from backend.api.canonical_repository.postgres_store import build_change_set_plan
    with pytest.raises(ValueError,match='another source authority'):
        build_change_set_plan(p['package'],{})


def test_forged_sealed_package_cannot_change_claim_prose(synced_evidence):
    store,_,_,_=synced_evidence
    p=prepare(synced_evidence);package=deepcopy(p['package'])
    package['claims'][0]['statement']='擅自改寫的教導'
    with pytest.raises(ValueError,match='deterministic source projection'):
        store.plan_package(package,source_kind=a.VERSION,transactional=True)


def test_existing_fragment_identity_does_not_expand_again_into_overlapping_fragments(synced_evidence):
    store,root,eroot,published=synced_evidence
    records={a.cm.source.key(*k):a.cm.model_state(v) for k,v in store.read_affected_context([('source_documents','S')]).items()}
    f4=deepcopy(records['source_fragments/F1'])
    f4['payload'].update(fragment_id='F4',verbatim_excerpt='保留的教授原話',verbatim_excerpt_sha256=m.sha('保留的教授原話'.encode()))
    records['source_fragments/F4']=f4
    ev=deepcopy(records['evidence_steps/E']['payload']);ev['source_fragment_ids']=['F1','F4']
    current_anchor=anchor('E','教授原話');current_anchor['source_fragment_id']='F1'
    ids,_,_=a.select_fragments(current_anchor,ev,records,m.read(eroot/'records-before.json'),
        records['source_documents/S']['payload'],ea.snap(published))
    assert ids==['F1']


def test_direct_store_apply_requires_exact_read_set(synced_evidence):
    store,root,_,_=synced_evidence;p=prepare(synced_evidence)
    plan=store.plan_package(p['package'],source_kind=a.VERSION,transactional=True)
    before=store.get_record_state('claims','C')
    with pytest.raises(ValueError,match='exact plan and read pins'):
        store.apply_plan(plan,metadata={'migration_plan':m.binding(root/'plan.json')})
    assert store.get_record_state('claims','C')==before
