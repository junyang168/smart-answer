from copy import deepcopy
import hashlib,json
from pathlib import Path
import pytest
from backend.pipeline import verified_source_quote_sync as q
from backend.pipeline import claim_management as cm
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_postgres import postgres_store,_temporary_postgres


def sample():
    sha=lambda x:hashlib.sha256(x.encode()).hexdigest()
    before={'body_sha256':'a'*64,'file':{'path':'/before','file_sha256':'b'*64},'paragraphs':{
        'S0001':{'index':10,'text':'前。旧说法。后。'},'S0002':{'index':20,'text':'同句。同句。'}}}
    current=deepcopy(before);current['body_sha256']='c'*64;current['file']={'path':'/current','file_sha256':'d'*64};current['paragraphs']['S0001']['text']='前。新说法。后。'
    verification={'schema_version':'wang_human_source_transcription_correction_v1','verified_by':'user','claim_content_approval':False,
        'source_id':'S','paragraph_key':'S0001','before':'旧说法','after':'新说法'}
    doc={'source_id':'S','transcript_id':'alias','source_body_sha256':current['body_sha256'],'source_file_sha256':current['file']['file_sha256']}
    fragment={'fragment_id':'F','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'旧说法。',
        'source_sha256':before['body_sha256'],'source_segment_index':10,'paragraph_text_sha256':sha(before['paragraphs']['S0001']['text']),
        'verbatim_excerpt_sha256':sha('旧说法。'),'review_status':'candidate','revision':1}
    claim={'claim_id':'C','claim_type':'exegesis','statement':'判断不变。','scripture_refs':['章号未核实'],'review_status':'candidate','revision':1,
        'occurrences':[{'source_id':'alias','anchors':[{'paragraph_key':'S0001','evidence_id':'E','proposed_highlight':{'text':'旧说法。','status':'proposed'}}]}]}
    return verification,before,current,doc,[fragment],[claim]


def test_exact_verified_copy_changes_quotes_not_claim_judgments_and_is_idempotent():
    args=sample();saved=deepcopy(args);compiled=q.compile_sync(*args)
    assert args==saved
    assert compiled['literal_quote_edits']==['source_fragments/F','claims/C']
    claim=compiled['records'][1]['payload']
    assert claim['statement']==args[-1][0]['statement'] and claim['scripture_refs']==['章号未核实']
    assert claim['occurrences'][0]['anchors'][0]['proposed_highlight']['text']=='新说法。'
    assert not compiled['claim_qualification_granted'] and not compiled['semantic_review_complete']
    again=list(args);again[-2]=[compiled['records'][0]['payload']];again[-1]=[claim]
    assert q.compile_sync(*again)['records']==[]
    blocked=q.compile_sync(*args,linked_claim_ids=['C'])
    assert blocked['pending_linked_claim_ids']==['C'] and len(blocked['records'])==1


@pytest.mark.parametrize('bad',['extra_source_change','wrong_source','old_hash','source_index','ambiguous_correction','wrong_anchor','claim_approval'])
def test_source_quote_sync_rejects_unverified_or_drifting_scope(bad):
    args=list(sample())
    if bad=='extra_source_change':args[2]['paragraphs']['S0002']['text']='别的变化'
    if bad=='wrong_source':args[4][0]['source_id']='foreign'
    if bad=='old_hash':args[4][0]['source_sha256']='e'*64
    if bad=='source_index':args[4][0]['source_segment_index']=99
    if bad=='ambiguous_correction':args[1]['paragraphs']['S0001']['text']+='旧说法'
    if bad=='wrong_anchor':args[5][0]['occurrences'][0]['anchors'][0]['paragraph_key']='S0002'
    if bad=='claim_approval':args[0]['claim_content_approval']=True
    with pytest.raises(ValueError):q.compile_sync(*args)


def test_unchanged_duplicate_quote_keeps_coordinates_and_only_updates_source_binding():
    args=list(sample());fragment=deepcopy(args[4][0]);fragment.update(fragment_id='D',paragraph_key='S0002',verbatim_excerpt='同句。',
        source_segment_index=20,paragraph_text_sha256=hashlib.sha256('同句。同句。'.encode()).hexdigest(),
        verbatim_excerpt_sha256=hashlib.sha256('同句。'.encode()).hexdigest())
    args[4].append(fragment)
    post=next(r['payload'] for r in q.compile_sync(*args)['records'] if r['object_id']=='D')
    assert post['verbatim_excerpt']==fragment['verbatim_excerpt'] and post['source_segment_index']==20
    assert {k for k in post if post[k]!=fragment.get(k)}=={'source_sha256'}


@pytest.fixture
def verified_transition(postgres_store, tmp_path):
    store=postgres_store
    original=tmp_path/'before.json';current=tmp_path/'current.json'
    raw=json.dumps({'script':[{'text':'前。旧说法。后。','index':10}]},ensure_ascii=False)
    original.write_text(raw);current.write_text(raw)
    before=cm.source.source_snapshot(binding(original))
    doc={'source_id':'S','source_type':'sermon_transcript','transcript_id':'alias','locator_space':'spoken_body_v1','source_path':str(current),
        'source_file_sha256':binding(current)['file_sha256'],'source_body_sha256':before['body_sha256']}
    store.apply_plan(store.plan_package({'package_id':'before-source','source_documents':[doc]}))
    args=list(sample());fragment=args[4][0];fragment.update(source_sha256=before['body_sha256'],
        source_segment_index=before['paragraphs']['S0001']['index'])
    store.apply_plan(store.plan_package({'package_id':'before-quotes','source_fragments':[fragment],'claims':args[5]}))
    current.write_text(raw.replace('旧说法','新说法'))
    after=cm.source.source_snapshot(binding(current))
    verification=args[0]|{'before_source':binding(original),'proposed_current_source':binding(current)}
    authority=tmp_path/'human-verification.json';authority.write_text(json.dumps(verification,ensure_ascii=False))
    doc.update(source_file_sha256=binding(current)['file_sha256'],source_body_sha256=after['body_sha256'])
    applied=store.apply_plan(store.plan_package({'package_id':'verified-source','source_documents':[doc]}),
        metadata={'human_source_verification':binding(authority)})
    transition=tmp_path/'transition.json'
    transition.write_text(cm.SourceTransition(before_sources={'S':binding(original)},
        source_change_set_ids={'S':applied['change_set_id']}).model_dump_json())
    return store,binding(authority),binding(transition),tmp_path/'sync',current


@pytest.mark.parametrize('reply_lost',[False,True])
def test_transaction_readback_and_replay_without_second_write(verified_transition,monkeypatch,reply_lost):
    store,verification,transition,root,current=verified_transition
    original=store.apply_plan
    if reply_lost:
        def lose_reply(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('committed reply lost')
        monkeypatch.setattr(store,'apply_plan',lose_reply)
        with pytest.raises(RuntimeError,match='reply lost'):
            q.synchronize(store,verification,transition,root=root)
        monkeypatch.setattr(store,'apply_plan',original)
    result=q.synchronize(store,verification,transition,root=root)
    assert result['updated_fragments']==1 and result['updated_claim_highlights']==1
    assert not result['semantic_review_complete'] and not result['claim_qualification_granted']
    claim=store.get_record_state('claims','C')
    assert claim['revision']==2 and claim['payload']['statement']=='判断不变。'
    assert claim['payload']['scripture_refs']==['章号未核实']
    assert claim['payload']['occurrences'][0]['anchors'][0]['proposed_highlight']['text']=='新说法。'
    assert store.get_record_state('source_fragments','F')['payload']['verbatim_excerpt']=='新说法。'
    def no_write(*args,**kwargs):raise AssertionError('replay must not write')
    monkeypatch.setattr(store,'apply_plan',no_write)
    assert q.synchronize(store,verification,transition,root=root)==result
    current.write_text(current.read_text()+' ')
    with pytest.raises(ValueError,match='drifted'):
        q.synchronize(store,verification,transition,root=root)


def test_unrelated_physical_edit_rejected_before_plan(verified_transition,monkeypatch):
    store,verification,transition,root,current=verified_transition
    current.write_text(current.read_text().replace('后。','别的话。'))
    def no_write(*args,**kwargs):raise AssertionError('unverified source must not write')
    monkeypatch.setattr(store,'apply_plan',no_write)
    with pytest.raises(ValueError,match='drifted'):
        q.synchronize(store,verification,transition,root=root)
    assert store.get_record_state('claims','C')['revision']==1


def test_late_transaction_failure_rolls_back_both_quote_copies(verified_transition,monkeypatch):
    store,verification,transition,root,_=verified_transition
    original=store._invalidate_dependencies
    def fail(*args,**kwargs):raise RuntimeError('late transaction failure')
    monkeypatch.setattr(store,'_invalidate_dependencies',fail)
    with pytest.raises(RuntimeError,match='late transaction failure'):
        q.synchronize(store,verification,transition,root=root)
    assert store.get_record_state('claims','C')['revision']==1
    assert store.get_record_state('source_fragments','F')['revision']==1
    assert not (root/'complete.json').exists()
    monkeypatch.setattr(store,'_invalidate_dependencies',original)
    assert q.synchronize(store,verification,transition,root=root)['updated_fragments']==1
