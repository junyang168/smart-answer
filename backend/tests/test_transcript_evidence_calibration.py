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
