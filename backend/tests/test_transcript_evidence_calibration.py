import pytest
from backend.pipeline.transcript_evidence_alignment import SourceAlignment,verify_preview
from backend.pipeline.transcript_evidence_calibration import ContextCalibration,content_quote
from backend.pipeline.transcript_evidence_migration import expanded_source
from backend.tests.test_transcript_evidence_migration import row,snapshot
from backend.tests.test_transcript_evidence_alignment import PREFIX,SUFFIX


def calibrate(quote,newtext,oldtext=None,new_clock=(15,25),old_clock=(10,100)):
    oldtext=oldtext or PREFIX+quote+SUFFIX
    old=snapshot([row(oldtext,start_time=old_clock[0],end_time=old_clock[1])])
    rows=[row(newtext,10,start_time=new_clock[0],end_time=new_clock[1])] if isinstance(newtext,str) else newtext
    new=snapshot(rows);doc={'script':rows};expanded,parts=expanded_source(doc)
    engine=SourceAlignment(old,new,expanded,parts)
    f={'source_id':'S','paragraph_key':'S0001','source_sha256':old['body_sha256'],'verbatim_excerpt':quote,
       'media_time':old_clock[0],'media_end_time':old_clock[1]}
    result=ContextCalibration(engine).refine(f,{'status':'exception','reason':'no_unique_exact_context'})
    return result,old,new,doc


def test_question_answer_prefix_becomes_metadata_not_lost_quotation():
    quote='（答：）所以你們是誰？'
    result,old,new,doc=calibrate(quote,PREFIX+'所以你們是誰？'+SUFFIX)
    assert result['status']=='auto_rebind'
    assert result['metadata_projection']['removed_prefix']=='（答：）'
    assert result['proposed_fields']['verbatim_excerpt'].rstrip('？')=='所以你們是誰'
    report={'fragments':[{**result,'quote':quote,'old_media_time':10,'old_media_end_time':100}]}
    assert verify_preview(report,old,new,doc)['literal_checks']['automatic_quotes']==1


def test_time_position_is_required_and_time_alone_is_insufficient():
    quote='（答：）所以你們是誰？'
    result,*_=calibrate(quote,PREFIX+'所以你們是誰？'+SUFFIX,new_clock=(400,410))
    assert result['status']=='exception'
    result,*_=calibrate('對嘛。','對嘛。',oldtext='對嘛。')
    assert result['status']=='exception'


def test_deleted_question_uses_original_characters_and_time_not_bible_inference():
    quote='（問：）那你什麼時候講這個？'
    result,old,new,doc=calibrate(quote,PREFIX+'~~那你什麼時候講？~~'+SUFFIX)
    assert result['status']=='source_deleted' and result['matched_deleted_character_count']>0
    assert result['candidate_quote']==''
    assert not result['calibration']['audio_position']['audio_relistened']
    verify_preview({'fragments':[{**result,'quote':quote,'old_media_time':10,'old_media_end_time':100}]},old,new,doc)


def test_partial_strikethrough_does_not_delete_the_live_teaching():
    quote='教授說這是一個非常重要的判斷，要留意經文的理由。'
    result,old,new,doc=calibrate(quote,PREFIX+quote.replace('非常重要的','~~非常~~重要的')+SUFFIX)
    assert result['status']=='located_review'
    assert result['matched_deleted_character_count']>0
    assert '重要的判斷' in result['candidate_quote']
    assert 'proposed_fields' not in result
    verify_preview({'fragments':[{**result,'quote':quote,'old_media_time':10,'old_media_end_time':100}]},old,new,doc)


def test_repeated_words_can_collapse_only_if_all_locate_to_same_paragraph():
    quote='問題很大。';old=PREFIX+quote+'這是中間一段具有獨特上下文的解釋。'+quote+SUFFIX
    result,*_=calibrate(quote,old,oldtext=old)
    assert result['status']=='auto_rebind'
    assert result['calibration']['old_occurrence_count']==2
    rows=[row(PREFIX+quote,10,start_time=15,end_time=20),
        row('這是中間一段具有獨特上下文的解釋。'+quote+SUFFIX,11,start_time=20,end_time=25)]
    result,*_=calibrate(quote,rows,oldtext=old)
    assert result['status']=='exception' and len(result['calibration_alternatives'])==2


def test_explicit_verse_selects_the_right_source_clause_without_rewriting_words():
    quote='可是五章十九節，「多人（polloi）」成了罪人。'
    old=PREFIX+'羅馬書五章十八節，眾人被定罪。'+quote+SUFFIX
    new=PREFIX+'羅馬書五章十八節，所有的人被定罪。可是五章十五節，多人成為罪人。五章十九節說多人成為罪人。'+SUFFIX
    result,*_=calibrate(quote,new,oldtext=old)
    assert result['status']=='located_review'
    assert result['calibration']['matched_explicit_verse']=='rom-5-19'
    assert '十九節' in result['candidate_quote'] and '十五節' not in result['candidate_quote']
    assert result['candidate_quote'] in new and 'proposed_fields' not in result


def test_source_words_remain_source_words_even_if_verse_identifies_a_passage():
    quote='（答：）這裡不是我們原來的結論，必須保留這個條件。'
    result,*_=calibrate(quote,PREFIX+'這裡是我們原來的結論，必須保留這個條件。'+SUFFIX)
    assert result['status']=='located_review' and 'proposed_fields' not in result
    assert not result['content_equivalence_proven']
    assert content_quote('（不是這個原因）我們繼續。')[0]=='（不是這個原因）我們繼續。'


def test_verifier_rejects_fabricated_time_corroboration():
    from copy import deepcopy
    quote='（答：）所以你們是誰？'
    result,old,new,doc=calibrate(quote,PREFIX+'所以你們是誰？'+SUFFIX)
    result=deepcopy(result);result['calibration']['audio_position']['old_interval']=[0,5]
    with pytest.raises(ValueError,match='matching audio timecodes'):
        verify_preview({'fragments':[{**result,'quote':quote,'old_media_time':10,'old_media_end_time':100}]},old,new,doc)


def test_chapter_only_reference_can_corroborate_low_literal_overlap_without_inventing_verse():
    from backend.api.scripture import spoken_chapters
    assert spoken_chapters('所以你看《羅馬書》十二章也明講。')==[(5,'rom-12')]
    quote='所以你看羅馬書十二章也明講，保羅明講之外，所以我們中國人就不會了解這節聖經是什麼意思。'
    new=PREFIX+'所以你看《羅馬書》十二章第一節也明講，將身體獻上，整個人都要獻的。'+SUFFIX
    result,*_=calibrate(quote,new)
    assert result['status']=='located_review'
    assert result['calibration']['shared_explicit_chapters']==['rom-12']
    assert '整個人都要獻的' in result['candidate_quote'] and 'proposed_fields' not in result


def test_trailing_ellipsis_does_not_send_a_short_fragment_to_a_remote_later_ellipsis():
    quote='（問：）個別在講……'
    result,*_=calibrate(quote,PREFIX+'~~對啊，這個個別再講。~~'+SUFFIX)
    assert result['status']=='source_deleted'
    assert result['calibration']['ignored_trailing_ellipsis']=='……'
    assert result['matched_deleted_character_count']>=3


def test_bounded_local_fallback_requires_unique_time_and_context_supported_match(monkeypatch):
    quote='（問：）個別在講……';oldtext=PREFIX+quote+SUFFIX
    old=snapshot([row(oldtext,start_time=10,end_time=100)])
    rows=[row(PREFIX+'~~個別再講~~'+SUFFIX,10,start_time=15,end_time=25)]
    new=snapshot(rows);expanded,parts=expanded_source({'script':rows});engine=SourceAlignment(old,new,expanded,parts)
    monkeypatch.setattr(engine,'candidate',lambda *a,**k:None)
    fragment={'source_id':'S','paragraph_key':'S0001','source_sha256':old['body_sha256'],
        'verbatim_excerpt':quote,'media_time':10,'media_end_time':100}
    result=ContextCalibration(engine).refine(fragment,{'status':'exception','reason':'no_unique_exact_context'})
    assert result['status']=='source_deleted'
    assert result['alignment']['method']=='unique_context_time_bounded_short_match_v1'
    assert result['matched_deleted_character_count']==3
    verify_preview({'fragments':[{**result,'quote':quote,'old_media_time':10,'old_media_end_time':100}]},old,new,{'script':rows})


@pytest.fixture
def legacy_clock(tmp_path):
    import json
    from hashlib import sha256
    from backend.pipeline import transcript_claim_migration as m
    from backend.pipeline.transcript_evidence_apply import snap
    item = 'lecture'
    source = tmp_path / 'old.json'
    source.write_text(json.dumps({'metadata': {'item': item}, 'script': [
        {'index': '1', 'text': '這是有上下文的教授原話。', 'start_time': None, 'end_time': None}]}))
    backup = tmp_path / 'backup'
    timeline = backup / 'production/script' / (item + '.json')
    timeline.parent.mkdir(parents=True)
    timeline.write_text(json.dumps({'entries': [{'index': '1', 'start': '00:00:01,000', 'end': '00:00:05,000'}]}))
    manifest = backup / 'backup.json'
    manifest.write_text(json.dumps({'schema_version': 'transcript-promotion-backup.v1',
        'intent': {'item': item}, 'files': {'production': {
            'script/' + item + '.json': {'sha256': m.sha(timeline.read_bytes())}}}}))
    old = snap(source)
    f = {'source_sha256': old['body_sha256'], 'paragraph_key': 'S0001',
         'source_segment_index': '1', 'paragraph_text_sha256': sha256('這是有上下文的教授原話。'.encode()).hexdigest(),
         'verbatim_excerpt': '教授原話', 'media_time': None, 'media_end_time': None}
    proof = {'item': item, 'source_file': m.binding(source), 'timeline_file': m.binding(timeline),
             'backup_manifest': m.binding(manifest)}
    return f, proof


def test_verified_legacy_clock_is_recovered_without_editing_fragment(legacy_clock):
    from backend.pipeline.transcript_evidence_calibration import verified_legacy_interval
    f, proof = legacy_clock
    result = verified_legacy_interval(f, proof)
    assert result['media_time'] == 0 and result['media_end_time'] == 5
    assert f['media_time'] is None and f['media_end_time'] is None


@pytest.mark.parametrize('field,value', [('source_sha256', 'wrong'), ('paragraph_key', 'S0002'),
    ('source_segment_index', '9'), ('paragraph_text_sha256', 'wrong'),
    ('verbatim_excerpt', '另一篇的內容'), ('media_time', 300)])
def test_legacy_clock_never_changes_source_or_contradicts_existing_time(legacy_clock, field, value):
    from backend.pipeline.transcript_evidence_calibration import verified_legacy_interval
    f, proof = legacy_clock
    with pytest.raises(ValueError): verified_legacy_interval({**f, field: value}, proof)


@pytest.mark.parametrize('name', ['source_file', 'timeline_file', 'backup_manifest'])
def test_legacy_clock_requires_all_original_checksums(legacy_clock, name):
    from pathlib import Path
    from backend.pipeline.transcript_evidence_calibration import verified_legacy_interval
    f, proof = legacy_clock
    Path(proof[name]['path']).write_text('{}')
    with pytest.raises(ValueError): verified_legacy_interval(f, proof)


def test_legacy_clock_cannot_use_another_timeline_even_with_matching_bytes(legacy_clock, tmp_path):
    from pathlib import Path
    from backend.pipeline import transcript_claim_migration as m
    from backend.pipeline.transcript_evidence_calibration import verified_legacy_interval
    f, proof = legacy_clock
    outside = tmp_path / 'another-timeline.json'
    outside.write_bytes(Path(proof['timeline_file']['path']).read_bytes())
    with pytest.raises(ValueError): verified_legacy_interval(f, {**proof, 'timeline_file': m.binding(outside)})


def test_saved_locator_calibration_uses_verified_legacy_clock_and_keeps_bounds(legacy_clock):
    from backend.pipeline import transcript_evidence_apply as a
    f, proof = legacy_clock
    new = snapshot([row('這是教授的原話。', 10, start_time=1, end_time=4)])
    document = {'script': [row('這是教授的原話。', 10, start_time=1, end_time=4)]}
    state = {'payload': f, 'revision': 2, 'content_sha256': 'before'}
    rule = {'before_pin': [2, 'before'], 'target_source_sha256': new['body_sha256'],
        'mode': 'relocate', 'reason': '精確原文與原始時間軸定位，尚未批准內容',
        'legacy_clock': proof, 'segments': [{'paragraph_key': 'S0001', 'verbatim_excerpt': '教授的原話'}]}
    original = {'record_key': 'source_fragments/F', 'status': 'located_review'}
    result = a.calibrate(original, state, rule, new, document)
    assert result['status'] == 'calibrated_rebind' and not result['content_approval_granted']
    assert result['candidate_segments'][0]['media_time'] == 1
    no_proof = {k: v for k, v in rule.items() if k != 'legacy_clock'}
    with pytest.raises(ValueError, match='audio interval'): a.calibrate(original, state, no_proof, new, document)
    outside = snapshot([row('這是教授的原話。', 10, start_time=300, end_time=310)])
    with pytest.raises(ValueError, match='audio interval'):
        a.calibrate(original, state, {**rule, 'target_source_sha256': outside['body_sha256']}, outside, document)
