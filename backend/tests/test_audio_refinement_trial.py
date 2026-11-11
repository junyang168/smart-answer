import pytest
from backend.pipeline.audio_refinement_trial import apply_edits,validate,assemble_partial


def edit(old,new,support='clear',occurrence=1):
    return dict(old=old,new=new,support=support,occurrence=occurrence,audio_quote=new,reason='test')


def test_joint_edit_keeps_uncertain_original_and_repeated_phrase():
    assert apply_edits('好好，卡扎少了。',[
        edit('卡扎少了','kata sarka'),edit('好好','好','uncertain')])=='好好，kata sarka。'


def test_joint_rejects_ambiguous_location_and_overlapping_edits():
    with pytest.raises(ValueError):apply_edits('原文',[edit('沒有','替換')])
    with pytest.raises(ValueError):apply_edits('原文字',[edit('原文','甲'),edit('文字','乙')])


def test_speaker_cannot_silently_omit_or_rewrite_corrected_words():
    chunk=dict(id='c1',text='卡扎少了。好好。')
    row=dict(id='c1',edits=[edit('卡扎少了','kata sarka')],limitations=[],
        turns=[dict(text='kata sarka。好。',role='professor',basis='voice')])
    with pytest.raises(ValueError,match='changes corrected speech'):validate({'chunks':[row]},[chunk])
    row['turns'][0]['text']='kata sarka。好好。'
    assert validate({'chunks':[row]},[chunk])[0]['raw_text']==chunk['text']


def test_joint_preserves_both_overlapping_chunks():
    chunks=[dict(id='c1',text='前句。接縫。'),dict(id='c2',text='接縫。後句。')]
    answer={'chunks':[dict(id=c['id'],edits=[],limitations=[],turns=[dict(
        text=c['text'],role='unknown',basis='unclear')]) for c in chunks]}
    assert [r['text'] for r in validate(answer,chunks)]==[c['text'] for c in chunks]
    with pytest.raises(ValueError,match='coverage'):validate({'chunks':answer['chunks'][:1]},chunks)


def test_partial_candidate_does_not_accept_unlocatable_edits_or_echo_words():
    chunks=[dict(id='c1',text='好，原文。')]
    answer={'chunks':[dict(id='c1',edits=[edit('原文','本文'),edit('猜的','補的')],limitations=[],
        turns=[dict(text='好，本文。多加一句。',role='professor',basis='voice')])]}
    rows,issues=assemble_partial(answer,chunks)
    assert rows[0]['text']=='好，本文。'
    assert rows[0]['turns'][0]['role']=='unknown'
    assert [i['kind'] for i in issues]==['unapplied_edit','speaker_unresolved']


def test_seam_rejects_losing_unique_speech_even_if_timestamps_look_valid():
    from backend.pipeline.transcript_text_layout import verify_seam_coverage
    with pytest.raises(ValueError,match='unmatched left speech'):
        verify_seam_coverage('他為了要做','所以啊，從他成為的那方面說',
            '我們的救主，他必須成為人。所以啊，','成為人。')
    with pytest.raises(ValueError,match='unmatched right speech'):
        verify_seam_coverage('前文結束。','後文開始。','','還有獨有文字。')


def test_seam_accepts_duplicate_preserved_on_either_side_but_not_remote_match():
    from backend.pipeline.transcript_text_layout import verify_seam_coverage
    verify_seam_coverage('前文。重複句。','後文。','','重複句，')
    verify_seam_coverage('前文。','重複句，後文。','重複句。','')
    with pytest.raises(ValueError):verify_seam_coverage('前文。','甲'*50+'重複句。','重複句。','')


def test_stitch_does_not_save_a_draft_after_losing_unique_seam_text(tmp_path):
    from backend.pipeline.transcript_text_layout import stitch,text_sha
    from backend.pipeline.slide_change_pilot import save_json
    chunks=[]
    for name,text,core_start,core_end,items in [
        ('a','甲乙丙',0,16000,[('甲',100,200),('乙',1200,1300),('丙',1400,1500)]),
        ('b','丙丁',32000,48000,[('丙',100,200),('丁',2100,2200)])]:
        chunks.append(dict(id=name,text=text,audio_sha256=name,sample_start=0,
            core_start_sample=core_start,core_end_sample=core_end,start_ms=0,end_ms=3000))
        save_json(tmp_path/f'alignment/{name}.json',dict(identity=dict(
            text_sha256=text_sha(text),audio_sha256=name),items=[dict(text=t,start_ms=a,end_ms=b) for t,a,b in items]))
    save_json(tmp_path/'asr.json',dict(chunks=chunks))
    with pytest.raises(ValueError,match='unmatched left speech'):stitch(tmp_path)
    assert not (tmp_path/'timed_text.json').exists()
