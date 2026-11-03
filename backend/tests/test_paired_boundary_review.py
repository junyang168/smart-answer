import copy
import pytest
from backend.pipeline.paired_boundary_review import validate, low_energy_candidates
from backend.pipeline.contextual_transcript_timing import verified_overlap
import numpy as np


def example():
    pair={'candidates':[{'id':'c0','time_ms':1000},{'id':'c1','time_ms':1500}]}
    answer={'relationship':'sequential','left_end_id':'c1','right_start_id':'c1',
            'heard_left_tail':'魔鬼來作王','heard_right_head':'所以從神的國度來看',
            'reason':'同一位講員前句說完再開始後句',
            'candidates':[{'id':'c0','left_complete':'no','right_complete':'no'},
                          {'id':'c1','left_complete':'yes','right_complete':'yes'}]}
    return pair,answer


def test_same_physical_cut_requires_both_sides_complete():
    pair,answer=example();assert validate(answer,pair)
    for fault in ['tail','head','incomplete','different_cut']:
        bad=copy.deepcopy(answer)
        if fault=='tail':bad['heard_left_tail']=''
        elif fault=='head':bad['heard_right_head']=''
        elif fault=='incomplete':bad['candidates'][1]['left_complete']='uncertain'
        else:bad['left_end_id']='c0';bad['candidates'][0]['left_complete']='yes'
        assert not validate(bad,pair)


def test_no_invented_candidate_and_no_unknown_side_admitted():
    pair,answer=example();answer['candidates'][1]['id']='invented'
    with pytest.raises(ValueError):validate(answer,pair)
    pair,answer=example();answer['right_start_id']='invented'
    assert not validate(answer,pair)


def test_simultaneous_voice_requires_actual_overlapping_tested_edges_and_pair_binding():
    pair,answer=example();answer['relationship']='simultaneous_speakers'
    assert not validate(answer,pair)  # equal cuts do not establish overlap
    answer['right_start_id']='c0';answer['candidates'][0]['right_complete']='yes'
    assert validate(answer,pair)
    witness={'pair_id':'ab','left_id':'a','right_id':'b','raw_response_sha256':'saved',
             'genuine_overlap':True}
    left={'id':'a','end_audio_witness':witness};right={'id':'b','start_audio_witness':witness}
    assert verified_overlap(left,right)
    assert not verified_overlap(left,{'id':'c','start_audio_witness':witness})
    assert not verified_overlap(left,{'id':'b'})


def test_silence_candidates_are_real_sample_positions_with_separation():
    audio=np.full(16000,3000,dtype=np.int16);audio[3200:6400]=0
    cuts=low_energy_candidates(audio,0,1000)
    assert cuts and 200<=cuts[0]<400
    assert all(abs(a-b)>=100 for i,a in enumerate(cuts) for b in cuts[i+1:])
