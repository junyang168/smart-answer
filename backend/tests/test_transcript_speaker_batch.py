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
