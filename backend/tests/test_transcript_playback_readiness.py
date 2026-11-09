import copy
import pytest
from backend.pipeline.transcript_playback_readiness import base_rows,usable,require_control


def test_brief_overlap_does_not_erase_strict_verdict_or_omit_content_gate():
    rows=[dict(id='a',status='qualified',start_ms=1000,end_ms=5000),dict(id='b',status='withheld',start_ms=4800,end_ms=8000)]
    before=copy.deepcopy(rows);lecture=dict(paragraphs=rows,duration_ms=10000)
    whole={'b':dict(start_ms=4800,end_ms=8000,raw_response_sha256='real-complete-audio-review')}
    result=base_rows(lecture,whole)
    assert result[1]['status']=='ready_with_context' and result[1]['strict_status']=='withheld'
    assert result[1]['start_ms']==4800 and result[1]['context_overlap_ms']==200
    assert rows==before
    assert base_rows(lecture,{})[1]['status']=='blocked'  # Boundary leakage is not completeness evidence.
    whole['b']['start_ms']=500
    assert base_rows(lecture,whole)[1]['status']=='blocked'  # Long unrelated overlap is not a warning.


def answer(verdict='complete',context='brief'):
    return dict(selected_id='c1',reason='本段完整，前方僅有少量鄰段聲音',candidates=[
        dict(id='c0',verdict='missing_content',clipped_word=False,leading_context='none',trailing_context='none',heard_opening='',heard_ending='尾句'),
        dict(id='c1',verdict=verdict,clipped_word=False,leading_context=context,trailing_context='none',heard_opening='本段開頭',heard_ending='本段結尾')])


def test_new_policy_accepts_brief_context_but_not_missing_content_wrong_occurrence_or_clipped_words():
    case=dict(candidates=[dict(id='c0'),dict(id='c1')]);a=answer()
    assert usable(a,case)['id']=='c1'
    for verdict in ['missing_content','wrong_occurrence','text_audio_mismatch','unclear']:
        assert usable(answer(verdict),case) is None
    assert usable(answer(context='substantial'),case) is None
    bad=copy.deepcopy(a);bad['candidates'][1]['clipped_word']=True
    assert usable(bad,case) is None
    bad=copy.deepcopy(a);bad['candidates'][1]['heard_opening']=''
    assert usable(bad,case) is None
    bad=copy.deepcopy(a);bad['selected_id']='invented'
    with pytest.raises(ValueError):usable(bad,case)
    require_control(a,case)
    bad=copy.deepcopy(a);bad['candidates'][0]['verdict']='complete'
    with pytest.raises(ValueError,match='calibration failed'):require_control(bad,case)
