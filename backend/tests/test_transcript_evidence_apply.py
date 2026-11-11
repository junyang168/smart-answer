from copy import deepcopy
import json

import pytest

from backend.pipeline import transcript_evidence_apply as a
from backend.pipeline import transcript_claim_migration as m
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres
from backend.api.canonical_repository.postgres_store import ChangeSetConflict


@pytest.fixture
def relocation(postgres_store, tmp_path):
    store = postgres_store
    old = tmp_path/'old.json'; new = tmp_path/'new.json'; published = tmp_path/'published.json'
    old.write_text(json.dumps({'script': [
        {'index': 1, 'text': '第一處保留的教授原話，這一句有足夠的上下文。', 'start_time': 1, 'end_time': 5},
        {'index': 2, 'text': '第二處是明確刪除的內容。', 'start_time': 5, 'end_time': 10}]}))
    doc = json.loads(old.read_text()); doc['script'][0]['index'] = 10
    doc['script'][1]['text'] = '~~第二處是明確刪除的內容。~~'
    new.write_text(json.dumps(doc)); published.write_bytes(new.read_bytes())
    before, after = a.snap(old), a.snap(new)
    source = {'source_id': 'S', 'source_type': 'sermon_transcript', 'transcript_id': 'lecture',
        'source_path': str(new), 'source_file_sha256': m.sha(new.read_bytes()), 'locator_space':'spoken_body_v1',
        'source_sha256': after['body_sha256'], 'source_body_sha256': after['body_sha256']}
    fragments = [{'fragment_id': 'F1', 'source_id': 'S', 'paragraph_key': 'S0001',
        'source_sha256': before['body_sha256'], 'verbatim_excerpt': '教授原話',
        'media_time': 1, 'media_end_time': 5},
        {'fragment_id': 'F2', 'source_id': 'S', 'paragraph_key': 'S0002',
        'source_sha256': before['body_sha256'], 'verbatim_excerpt': '第二處是明確刪除的內容。',
        'media_time': 5, 'media_end_time': 10},
        {'fragment_id': 'F3', 'source_id': 'S', 'paragraph_key': 'S0001',
        'source_sha256': after['body_sha256'], 'verbatim_excerpt': '保留的教授原話',
        'anchor_state': 'source_version_bound'}]
    package = {'source_documents': [source], 'source_fragments': fragments,
        'evidence_steps': [{'evidence_step_id': 'E', 'statement': '保留論據文字',
            'source_fragment_ids': ['F1','F2'], 'produced_claim_ids': ['C']},
            {'evidence_step_id': 'D', 'statement': '歷史論據', 'source_fragment_id': 'F2'}],
        'claims': [{'claim_id': 'C', 'statement': '不可改動的主張', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S'}], 'evidence_step_ids': ['E']}]}
    store.apply_plan(store.plan_package(package))
    return store, tmp_path/'run', old, published


def prepare(case):
    store, root, old, published = case
    return a.prepare(store, root, source_id='S', old_paths=[old], published=published,
                     instruction='只用程式搬遷；保留已完成；軟刪除標記已刪除。')


def test_real_sql_relocation_preserves_claim_and_history_and_is_idempotent(relocation):
    store, root, _, published = relocation
    claim = store.get_record_state('claims','C')
    raw = published.read_bytes()
    plan = prepare(relocation)
    assert prepare(relocation) == plan
    report = m.read(root/'comparison.json')
    assert report['summary'] == {'auto_rebind': 1, 'source_deleted': 1, 'already_current': 1}
    result = a.apply(store,root)
    assert result['status'] == 'applied'
    assert a.apply(store,root)['status'] == 'already_applied'
    assert store.get_record_state('claims','C') == claim
    assert published.read_bytes() == raw
    moved = store.get_record_state('source_fragments','F1')['payload']
    assert moved['source_segment_index'] == 10 and moved['media_time'] == 1
    deleted = store.get_record_state('source_fragments','F2')['payload']
    assert deleted['anchor_state'] == 'source_deleted'
    assert deleted['verbatim_excerpt'] == '第二處是明確刪除的內容。'
    assert store.get_record_state('evidence_steps','D')['payload']['source_binding_status'] == 'source_deleted'
    evidence = store.get_record_state('evidence_steps','E')['payload']
    assert evidence['source_binding_status'] == 'partially_deleted'
    assert evidence['support_eligibility'] == 'withheld_unreviewed'
    assert evidence['valid_for_identity_review'] is False
    history = store.get_change_history(plan['change_set_id'])
    assert history['operations'] and all(op['collection'] != 'claims' for op in history['operations'])


def test_current_record_drift_is_not_overwritten(relocation):
    store, root, _, _ = relocation
    prepare(relocation)
    payload = store.get_record_state('source_fragments','F1')['payload']
    payload['verbatim_excerpt'] = '另一位同工正在修改'
    store.apply_plan(store.plan_package({'source_fragments': [payload]}))
    with pytest.raises(ValueError, match='changed since comparison'): a.apply(store,root)
    assert store.get_record_state('source_fragments','F2')['revision'] == 1


def test_source_file_drift_stops_before_write(relocation):
    store, root, _, published = relocation
    prepare(relocation); published.write_text('{}')
    with pytest.raises(ValueError): a.apply(store,root)
    assert store.get_record_state('source_fragments','F1')['revision'] == 1


def test_common_store_guards_unchanged_reads_in_same_transaction(relocation):
    store, _, _, _ = relocation
    old = store.get_record_state('claims','C')
    p = store.get_record_state('source_fragments','F1')['payload']; p['media_time'] = 2
    plan = store.plan_package({'source_fragments': [p]}, transactional=True)
    pin = (old['revision'],old['content_sha256'],False)
    changed = deepcopy(old['payload']); changed['statement'] = '並行更新'
    store.apply_plan(store.plan_package({'claims':[changed]}))
    with pytest.raises(ChangeSetConflict,match='dependency drift'):
        store.apply_plan(plan,expected_current_record_pins={('claims','C'):pin})
    assert store.get_record_state('source_fragments','F1')['payload']['media_time'] == 1


def test_changed_quote_requires_precise_unique_slice_and_withholds_qualification():
    new = {'body_sha256': 'a'*64, 'paragraphs': {'S0002':
        {'text':'前文。不是教授的斷言。後文。', 'index': 8, 'start_time': 10, 'end_time': 20}}}
    segment = {'paragraph_key':'S0002','source_segment_index':8,'verbatim_excerpt':'不是教授的斷言',
               'live_start':3,'live_end':10,'media_time':10,'media_end_time':20}
    row = {'status':'located_review','candidate_segments':[segment], 'alignment':{
        'left_anchor':{'literal':'前文'},'right_anchor':{'literal':'後文'},
        'both_endpoints_matched':True,'matched_fraction':.9,'length_ratio':1},'change_flags':['quote_text_changed']}
    f = {'media_time':10,'media_end_time':20}
    fields, changed = a.fields_for(row,f,new)
    assert changed and fields['source_binding_status'] == 'needs_claim_refresh'
    for bad in [dict(row,alignment={**row['alignment'],'both_endpoints_matched':False}),
                dict(row,candidate_segments=[segment,segment]),
                dict(row,change_flags=['quotation_boundary_requires_refresh']),
                dict(row,candidate_segments=[{**segment,'verbatim_excerpt':'捏造的引文'}])]:
        assert a.fields_for(bad,f,new) == (None,False)


def test_plan_rejects_claim_words_or_extra_fields(relocation):
    store, root, _, _ = relocation
    prepare(relocation)
    report = m.read(root/'comparison.json'); records = m.read(root/'records-before.json')
    report['updates']['claims/C'] = {**records['claims/C']['payload'],'statement':'不能改'}
    with pytest.raises(ValueError,match='permitted fields'): a.package_for(report,records)


def test_split_atomic_fragments_rebinds_complete_references_without_changing_prose(relocation):
    store,root,old,published = relocation
    before = store.get_record_state('source_fragments','F1')
    target = a.snap(published)
    calibration = root.parent/'calibration.json'
    m.immutable(calibration, {'fragments':{'source_fragments/F1':{
        'before_pin':[before['revision'],before['content_sha256']],
        'target_source_sha256':target['body_sha256'],'mode':'relocate',
        'reason':'同一引文拆為兩個可逐字核對片段，保留兩者，無新內容批准。',
        'segments':[{'paragraph_key':'S0001','verbatim_excerpt':'教授'},
                    {'paragraph_key':'S0001','verbatim_excerpt':'原話'}]}}})
    a.prepare(store,root,source_id='S',old_paths=[old],published=published,
              instruction='程式搬遷',calibration_path=calibration)
    report = m.read(root/'comparison.json')
    assert len(report['created_keys']) == 2
    a.apply(store,root)
    assert a.apply(store,root)['status'] == 'already_applied'
    f = store.get_record_state('source_fragments','F1')['payload']
    assert f['anchor_state'] == 'replaced_by_precise_fragments'
    e = store.get_record_state('evidence_steps','E')['payload']
    assert 'F1' not in e['source_fragment_ids']
    assert set(f['replacement_fragment_ids']) <= set(e['source_fragment_ids'])
    assert e['statement'] == '保留論據文字'


def test_rhetorical_fillers_are_context_and_not_evidence():
    from backend.tests.test_transcript_claim_migration import state
    from backend.tests.test_transcript_evidence_migration import snapshot,row
    new = snapshot([row('很重要。有沒有聽懂？',start_time=1,end_time=5)])
    f = {'fragment_id':'F','source_id':'S','source_sha256':new['body_sha256'],
         'anchor_state':'source_version_bound','paragraph_key':'S0001','verbatim_excerpt':'很重要。'}
    records = {'source_fragments/F':state(f),'evidence_steps/E':state({
        'evidence_step_id':'E','source_fragment_ids':['F'],'statement':'原陳述保留'})}
    report = a.compare(records,source_id='S',old_sources=[new],new=new,
                       document={'script':[row('很重要。有沒有聽懂？',start_time=1,end_time=5)]})
    evidence = report['updates']['evidence_steps/E']
    assert evidence['source_fragment_ids'] == [] and evidence['source_fragment_id'] is None
    assert evidence['context_fragment_ids'] == ['F']
    assert evidence['support_eligibility'] == 'withheld_unreviewed'
    assert report['updates']['source_fragments/F']['anchor_state'] == 'context_only'
    assert not a.context_only('這段經文很重要，因為它說明悔改的原因。')
    assert not a.context_only('不是因為每個人都得救。')
    for text in ['什麼意思？','要怎麼樣？','小小問題。','就是這樣。','Yeah，就是這樣。']:
        assert a.context_only(text)
    assert not a.context_only('你們。')
    assert not a.context_only('你們要將該撒的物歸給該撒。')
    assert not a.context_only('撒都該人不相信復活，是什麼意思？')


def test_calibrated_audience_turn_keeps_speaker_through_fragment_and_evidence():
    from backend.tests.test_transcript_claim_migration import state
    from backend.tests.test_transcript_evidence_migration import snapshot,row
    rows=[row('**教授：**問題。\n\n**聽眾：**這是聽眾的回答。',start_time=1,end_time=5)]
    new=snapshot(rows)
    f={'fragment_id':'F','source_id':'S','source_sha256':'old','paragraph_key':'S0001',
       'verbatim_excerpt':'舊辨識回答','media_time':1,'media_end_time':5}
    before=state(f)
    report=a.compare({'source_fragments/F':before,'evidence_steps/E':state({
        'evidence_step_id':'E','source_fragment_ids':['F'],'statement':'既有陳述'})},
        source_id='S',old_sources=[],new=new,document={'script':rows},calibration={'fragments':{
            'source_fragments/F':{'before_pin':[before['revision'],before['content_sha256']],
                'target_source_sha256':new['body_sha256'],'mode':'relocate','reason':'已核對相同回答位置',
                'segments':[{'paragraph_key':'S0001','verbatim_excerpt':'**聽眾：**這是聽眾的回答。'}]}}})
    assert report['updates']['source_fragments/F']['speaker']=='audience'
    assert report['updates']['evidence_steps/E']['speaker']=='audience'
    assert report['updates']['evidence_steps/E']['support_eligibility']=='withheld_unreviewed'


def test_legacy_clock_witness_is_bound_through_sql_apply(relocation):
    from backend.api.sc_api.transcript_review import atomic
    from backend.pipeline.transcript_evidence_calibration import verified_legacy_interval
    store, root, old, published = relocation
    fragment = store.get_record('source_fragments', 'F1')
    fragment.update(media_time=None, media_end_time=None, source_segment_index=1,
        paragraph_text_sha256=m.sha(a.snap(old)['paragraphs']['S0001']['text'].encode()))
    store.apply_plan(store.plan_package({'source_fragments': [fragment]}))
    backup = root.parent / 'clock-backup'
    timeline = backup / 'production/script/lecture.json'
    atomic(timeline, {'entries': [
        {'index': 1, 'start': '00:00:01,000', 'end': '00:00:05,000'},
        {'index': 2, 'start': '00:00:05,000', 'end': '00:00:10,000'}]})
    atomic(backup / 'backup.json', {'schema_version': 'transcript-promotion-backup.v1',
        'intent': {'item': 'lecture'}, 'files': {'production': {
            'script/lecture.json': {'sha256': m.sha(timeline.read_bytes())}}}})
    proof = {'item': 'lecture', 'source_file': m.binding(old),
             'timeline_file': m.binding(timeline), 'backup_manifest': m.binding(backup / 'backup.json')}
    assert verified_legacy_interval(fragment, proof)['media_end_time'] == 5
    state = store.get_record_state('source_fragments', 'F1')
    rules = root.parent / 'clock-calibration.json'
    atomic(rules, {'schema_version': 'wang_transcript_locator_calibration_v1', 'source_id': 'S',
        'fragments': {'source_fragments/F1': {'before_pin': [state['revision'], state['content_sha256']],
            'target_source_sha256': a.snap(published)['body_sha256'], 'mode': 'relocate',
            'reason': 'Original clock backup plus exact source paragraph', 'legacy_clock': proof,
            'segments': [{'paragraph_key': 'S0001', 'verbatim_excerpt': '教授原話'}]}}})
    plan = a.prepare(store, root, source_id='S', old_paths=[old], published=published,
                     instruction='Pinned technical relocation only', calibration_path=rules)
    assert m.binding(timeline) in plan['source_files']
    before = store.get_record_state('source_fragments', 'F1')
    timeline.write_text('{}')
    with pytest.raises(ValueError): a.apply(store, root)
    assert store.get_record_state('source_fragments', 'F1') == before


@pytest.mark.parametrize('text', ['什麼？', '是什麼？', '還有什麼？', '問題在哪裡？', '問題在哪？', '還有其他的……還有什麼？'])
def test_standalone_conversational_questions_are_context_only(text):
    assert a.context_only(text)


@pytest.mark.parametrize('text', ['信心是什麼？', '問題在哪裡？在於沒有信服神。', '神還有什麼應許？', '三年半，真的？', '馬可。'])
def test_substantive_questions_and_short_answers_are_not_erased(text):
    assert not a.context_only(text)
