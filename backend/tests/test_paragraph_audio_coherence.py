import copy
import pytest
from backend.pipeline.paragraph_audio_coherence import validate,needs_coherence


def test_omitted_observations_preserve_occurrence_pairs_and_never_retry_reviewed_ranges():
    from backend.pipeline.paragraph_audio_coherence import observed_ranges,recovery_cases
    row=dict(id='p',status='withheld',observations=[dict(start_ms=100,end_ms=800),
        dict(start_ms=1000,end_ms=2000),dict(start_ms=1000,end_ms=2000),
        dict(start_ms=50,end_ms=3000,start_error='wrong'),dict(start_ms=4000,end_ms=3900)])
    assert observed_ranges(row,5000)==[(100,800),(1000,2000)]
    source=dict(records={'s':dict(duration_ms=5000,paragraphs=[dict(id='p',start_ms=100,end_ms=800,text='原文不變')])})
    assessment=dict(lectures={'s':dict(paragraphs=[row])})
    parent=dict(cases=[dict(id='old',lecture='s',paragraph_id='p',candidates=[dict(start_ms=100,end_ms=800)])])
    before=copy.deepcopy((source,assessment,parent))
    cases=recovery_cases(source,assessment,parent)
    assert cases==[dict(id='Ls-p-omitted-observations',lecture='s',paragraph_id='p',reference='原文不變',ranges=[(1000,2000)])]
    assert (source,assessment,parent)==before
    assert (100,2000) not in cases[0]['ranges']  # Never combine different occurrences.
    assessment['lectures']['s']['paragraphs'][0]['status']='qualified'
    assert recovery_cases(source,assessment,parent)==[]


def test_unique_retained_phrase_uses_original_acoustic_units_only(tmp_path):
    import hashlib
    from backend.pipeline.paragraph_audio_coherence import retained_phrase_range
    from backend.pipeline.slide_change_pilot import save_json,file_sha
    (tmp_path/'master.wav').write_bytes(b'source')
    save_json(tmp_path/'master.json',{'source_sha256':'source'})
    root=tmp_path/'windows/w00/audio';root.mkdir(parents=True)
    text='好了时间到了所以我们一起祷告'
    audio=root/'chunk.wav';audio.write_bytes(b'original audio')
    chunk=dict(id='c0',text=text,audio='chunk.wav',sample_start=0,sample_end=160000)
    save_json(root/'asr.json',{'chunks':[chunk]})
    raw=dict(identity=dict(text_sha256=hashlib.sha256(text.encode()).hexdigest(),audio_sha256=file_sha(audio)),items=[dict(text=c,start_ms=i*100,end_ms=(i+1)*100) for i,c in enumerate(text)])
    save_json(root/'alignment/c0.json',raw)
    record=dict(source=str(tmp_path),source_sha256='source',duration_ms=40000)
    p=dict(window_id='w00',source_window_origin_sample=320000,text='所以我們一起禱告。',original_text=text)
    result=retained_phrase_range(record,p)
    assert result['start_ms']==20600 and result['end_ms']==21400
    assert result['derivation']=='original_acoustic_tokens_for_unique_retained_phrase'
    assert len(result['evidence'])==4
    assert retained_phrase_range(record,{**p,'original_text':text+text}) is None
    assert retained_phrase_range(record,{**p,'text':'不同的話'}) is None
    audio.write_bytes(b'changed')
    with pytest.raises(ValueError,match='alignment changed'):retained_phrase_range(record,p)


def test_opposite_edge_does_not_silently_qualify_different_occurrence():
    row={'observations':[{'start_ms':1000,'end_ms':5000},{'start_ms':1080,'end_ms':5080}],
         'start_ms':1000,'end_ms':35000,'end_audio_witness':{'raw_response_sha256':'separate_clip'}}
    assert needs_coherence(row)
    row['start_audio_witness']={'raw_response_sha256':'other_clip'}
    assert needs_coherence(row)  # two isolated positives cannot establish a continuous range
    row['end_ms']=5200
    assert not needs_coherence(row)


def test_complete_candidate_is_selected_as_one_unit_with_actual_heard_edges():
    case={'candidates':[{'id':'c0'},{'id':'c1'}]}
    answer={'selected_id':'c1','reason':'前後句完整，沒有上一段的句尾',
            'candidates':[{'id':'c0','verdict':'different_occurrence','heard_opening':'好','heard_ending':'看這個東西'},
                          {'id':'c1','verdict':'clean_complete_paragraph','heard_opening':'好我們先休息','heard_ending':'看這個東西'}]}
    assert validate(answer,case)
    bad=copy.deepcopy(answer);bad['selected_id']='c0'
    assert not validate(bad,case)
    bad=copy.deepcopy(answer);bad['candidates'][1]['heard_opening']=''
    assert not validate(bad,case)
    bad=copy.deepcopy(answer);bad['selected_id']='invented'
    with pytest.raises(ValueError):validate(bad,case)
    bad=copy.deepcopy(answer);bad['candidates'].pop()
    with pytest.raises(ValueError):validate(bad,case)
