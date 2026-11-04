from backend.pipeline.transcript_evidence_alignment import SourceAlignment
from backend.pipeline.transcript_evidence_migration import expanded_source, literal_layout
from backend.tests.test_transcript_evidence_migration import snapshot,row

PREFIX='前面先說明這個問題的歷史背景與經文脈絡，請大家注意接下來的解釋。'
SUFFIX='這裡繼續說明另一個獨立的例子，讓我們仔細閱讀下一節經文的上下文。'


def inspect(oldtext,newtext,quote):
    old=snapshot([row(oldtext)]);new_rows=[row(newtext,10,start_time=2,end_time=20)] if isinstance(newtext,str) else newtext
    new=snapshot(new_rows);expanded,rows=expanded_source({'script':new_rows})
    engine=SourceAlignment(old,new,expanded,rows)
    f={'source_id':'S','paragraph_key':'S0001','source_sha256':old['body_sha256'],'verbatim_excerpt':quote}
    return engine.refine(f,{'status':'exception','reason':'no_unique_exact_context'})


def test_global_unique_context_locates_short_unchanged_quote():
    quote='那種話講不通。'
    result=inspect(PREFIX+quote+SUFFIX,'新增的開頭。'+PREFIX+quote+SUFFIX,quote)
    assert result['status']=='auto_rebind'
    assert result['proposed_fields']['verbatim_excerpt'].rstrip('。')==quote.rstrip('。')
    assert result['alignment']['left_anchor'] and result['alignment']['right_anchor']


def test_small_word_change_is_located_but_never_automatically_approved():
    quote='我們這裡講到很多的問題，必須再仔細說明。'
    result=inspect(PREFIX+quote+SUFFIX,PREFIX+quote.replace('很多的問題','很多問題')+SUFFIX,quote)
    assert result['status']=='located_review'
    assert 'quote_text_changed' in result['change_flags'] and result['text_changes']
    assert not result['content_equivalence_proven'] and 'proposed_fields' not in result


def test_high_similarity_negation_change_still_requires_refresh():
    quote='我們從這段經文看到的結論並不是剛才那個說法，這個條件需要保留。'
    result=inspect(PREFIX+quote+SUFFIX,PREFIX+quote.replace('不是','是')+SUFFIX,quote)
    assert result['status']=='located_review'
    assert result['alignment']['matched_fraction']>.95
    assert 'negation_number_or_original_language_changed' in result['change_flags']


def test_cross_paragraph_candidate_keeps_literal_fragments_and_separate_clocks():
    quote='這是第一個重要的判斷。這是第二個需要保留的解釋。'
    result=inspect(PREFIX+quote+SUFFIX,[row(PREFIX+'這是第一個重要的判斷。',2,start_time=2,end_time=5),row('這是第二個需要保留的解釋。'+SUFFIX,3,start_time=5,end_time=10)],quote)
    assert result['status']=='located_review' and 'cross_paragraph' in result['change_flags']
    assert len(result['candidate_segments'])==2
    for segment in result['candidate_segments']:
        assert segment['media_time'] is not None and segment['verbatim_excerpt']


def test_label_after_quote_does_not_change_the_quotes_speaker():
    quote='這是一個教授先說的清楚判斷，與後來的聽眾問題無關。'
    result=inspect(PREFIX+quote+SUFFIX,PREFIX+quote+'聽眾：插話。'+SUFFIX,quote)
    assert result['status']=='auto_rebind'


def test_audience_label_is_annotation_not_an_automatic_rebinding_exception():
    quote='這是一個教授先說的清楚判斷，與後來的聽眾問題無關。'
    result=inspect(PREFIX+quote+SUFFIX,PREFIX+'聽眾：'+quote+SUFFIX,quote)
    assert result['status']=='auto_rebind'
    assert result['speaker_annotation']['speaker']=='audience'
    assert result['proposed_fields']['speaker']=='audience'


def test_bound_deleted_words_are_marked_without_reusing_them_as_live_quote():
    quote='這裡是一個重要的判斷，需要逐字檢查原來的來源。'
    result=inspect(PREFIX+quote+SUFFIX,PREFIX+quote.replace('重要的','~~重要的~~')+SUFFIX,quote)
    assert result['status']=='source_deleted'
    assert result['matched_deleted_character_count']==3
    assert '重要的' not in result['candidate_quote']
    assert result['proposed_fields']['anchor_state']=='source_deleted'


def test_new_deleted_interjection_is_not_an_old_quoted_character():
    quote='這裡是一個重要的判斷，需要逐字檢查原來的來源。'
    result=inspect(PREFIX+quote+SUFFIX,PREFIX+quote.replace('，','，~~技術故障~~')+SUFFIX,quote)
    assert result['matched_deleted_character_count']==0
    assert result['status']=='auto_rebind'
    assert literal_layout(result['candidate_quote'])[0]==literal_layout(quote)[0]


def test_changed_first_word_has_no_automatic_boundary_approval():
    quote='人的這個回應需要放在整段經文的語境裡面理解。'
    result=inspect(PREFIX+quote+SUFFIX,PREFIX+quote.replace('人的','神的',1)+SUFFIX,quote)
    assert result['status']=='located_review'
    assert 'changed_quote_boundary' in result['change_flags']


def test_missing_short_quote_or_non_unique_context_not_promoted_by_order_alone():
    result=inspect('相同。相同。','相同。','相同。')
    assert result['status']=='exception'
    result=inspect(PREFIX+'對。'+SUFFIX,PREFIX+'不對。'+SUFFIX,'對。')
    assert result['status']=='exception' # two matched characters cannot locate a one-character word


def test_literal_verifier_rejects_modified_source_slice_and_deleted_location():
    import pytest
    from copy import deepcopy
    from backend.pipeline.transcript_evidence_alignment import verify_preview
    quote='我們在這裡說明重要的條件，讓大家了解整段經文。'
    old_rows=[row(PREFIX+quote+SUFFIX)]
    new_rows=[row(PREFIX+quote.replace('重要的','~~重要的~~')+SUFFIX,10,start_time=2,end_time=20)]
    result=inspect(PREFIX+quote+SUFFIX,new_rows,quote)
    report={'fragments':[{**result,'quote':quote}]};old=snapshot(old_rows);new=snapshot(new_rows);doc={'script':new_rows}
    assert verify_preview(report,old,new,doc)['literal_checks']['candidate_segments']==1
    broken=deepcopy(report);broken['fragments'][0]['candidate_segments'][0]['verbatim_excerpt']+='捏造文字'
    with pytest.raises(ValueError,match='saved literal source slice'):verify_preview(broken,old,new,doc)
    broken=deepcopy(report);broken['fragments'][0]['deleted_character_bindings'][0]['new_offset']=0
    with pytest.raises(ValueError,match='explicit new strikethrough'):verify_preview(broken,old,new,doc)


def test_audience_speaker_does_not_claim_unlabeled_other_segment_is_audience():
    from backend.pipeline.transcript_evidence_alignment import speaker_annotation
    new={'paragraphs':{'S0001':{'text':'聽眾：請問這是什麼？'},'S0002':{'text':'這是下一個未標明講者的句子。'}}}
    annotation=speaker_annotation([{'paragraph_key':'S0001','live_start':3,'live_end':10},
        {'paragraph_key':'S0002','live_start':0,'live_end':10}],new)
    assert annotation['speaker'] is None and annotation['proposed_fields']=={}
    assert annotation['label']=='部分引文：聽眾'
