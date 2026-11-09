import pytest
from backend.pipeline.transcript_speaker_batch import insert_labels, protection_reasons, plan_windows


def test_protects_all_reviews_and_edits_even_when_not_confirmed():
    row={'index':'7','text':'改過'}
    assert protection_reasons(row,{'reviews':{'p:7':{'decision':'pending'}}},'原文') == ['existing_review_record','existing_text_edit']
    assert protection_reasons({'index':'7','text':'原文'},{'reviews':{}},'原文') == []


def test_insertions_preserve_exact_text_and_collapse_same_role():
    turns=[{'role':'professor','text':'好。'},{'role':'audience','text':'甲？'},{'role':'audience','text':'乙？'},{'role':'professor','text':'對。'}]
    assert insert_labels('好。甲？乙？對。',turns)=='**教授：**好。\n\n**聽眾：**甲？乙？\n\n**教授：**對。'
    with pytest.raises(ValueError): insert_labels('不能改字',turns)


def test_single_professor_unchanged_and_unknown_visible():
    assert insert_labels('原文',[{'role':'professor','text':'原文'}])=='原文'
    assert insert_labels('不清楚',[{'role':'unknown','text':'不清楚'}])=='**說話者未明：**不清楚'


def test_complete_audio_coverage_and_unique_paragraph_ownership():
    paragraphs=[{'id':'a','start_ms':1000,'end_ms':310000},{'id':'b','start_ms':300000,'end_ms':320000}]
    windows=plan_windows(paragraphs,650000)
    assert [(w['core_start_ms'],w['core_end_ms']) for w in windows]==[(0,300000),(300000,600000),(600000,650000)]
    assert [p['id'] for w in windows for p in w['paragraphs']]==['a','b']
    assert windows[1]['clip_start_ms']<300000
    assert windows[0]['clip_end_ms']>=315000


def test_apply_plan_preserves_reviews_edits_and_concurrent_changes():
    from copy import deepcopy
    from backend.pipeline.transcript_speaker_batch import prepare_operations
    initial={'script':[{'index':str(i),'text':'甲乙'} for i in range(1,6)],
             'context':{'reviews':{'p:1':{'decision':'confirmed'}},
                        'paragraph_bindings':{str(i):{'source_paragraph_id':str(i)} for i in range(1,6)}}}
    current=deepcopy(initial)
    current['context']['reviews']['p:2']={'decision':'confirmed'}
    current['script'][2]['text']='人工修改'
    current['context']['paragraph_bindings']['4']['end_ms']=9000
    source={'paragraphs':[{'id':str(i),'text':'甲乙'} for i in range(1,6)]}
    candidates={str(i):{'turns':[{'role':'audience','text':'甲'},{'role':'professor','text':'乙'}]} for i in range(1,6)}
    result=prepare_operations(initial,current,source,candidates)
    assert result['operations']==[{'kind':'text','id':'5','text':'**聽眾：**甲\n\n**教授：**乙'}]
    assert len(result['skipped'])==4
    assert current['script'][0]['text']=='甲乙'
    assert current['context']['reviews']=={'p:1':{'decision':'confirmed'},'p:2':{'decision':'confirmed'}}


def test_noisy_source_is_not_sent_as_a_speaker_target(tmp_path,monkeypatch):
    import numpy as np
    import backend.pipeline.transcript_speaker_batch as module
    from backend.pipeline.slide_change_pilot import save_json,read_json
    from backend.pipeline.transcript_audio_pilot import write_audio
    source=tmp_path/'source';source.mkdir();output=tmp_path/'speakers';snapshot=tmp_path/'snapshot.json'
    paragraphs=[dict(id='a',text='清楚。',start_ms=0,end_ms=1000),dict(id='noise',text='未確認粗稿',start_ms=1000,end_ms=2000)]
    save_json(source/'full_transcript.json',dict(paragraphs=paragraphs,timing_quality={'source_holds':{'noise':{'text_confirmed':False}}}))
    save_json(source/'master.json',dict(duration_ms=3000));write_audio(source/'master.wav',np.zeros(48000,dtype=np.int16))
    save_json(snapshot,dict(script=[dict(index=p['id'],text=p['text']) for p in paragraphs],context=dict(revision=0,reviews={},paragraph_bindings={p['id']:{'source_paragraph_id':p['id']} for p in paragraphs})))
    class Reviewer:
        model='fake';config={}
        def __init__(self,*a):self.config={}
        def generate(self,contents):return {}
        def text(self,raw):
            import json
            return json.dumps(dict(results=[dict(paragraph_id='a',limitations='',turns=[dict(text='清楚。',role='professor',confidence='clear',clip_start_s=0,clip_end_s=1,acoustic_basis='清楚主講聲音')])],missing_speech=[]))
        def usage(self,raw):return {}
    seen=[]
    def saved(folder,request,*a,**k):
        seen.extend(p['paragraph_id'] for p in request['material']['paragraphs']);return {}
    monkeypatch.setattr(module,'VisionReviewer',Reviewer);monkeypatch.setattr(module,'saved_call',saved)
    result=module.run(source,snapshot,output,workers=1,thinking_level='LOW')
    assert result['complete'] and seen==['a']
    answer=read_json(output/'w000/candidate.json')
    assert answer['accepted'][1]['turns'][0]['role']=='unknown'
    assert answer['accepted'][1]['turns'][0]['text']=='未確認粗稿'
