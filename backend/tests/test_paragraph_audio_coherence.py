import copy
import pytest
from backend.pipeline.paragraph_audio_coherence import validate,needs_coherence


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
