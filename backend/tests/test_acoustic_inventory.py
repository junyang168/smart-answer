"""Candidate fingerprints cannot substitute for independent waveform evidence."""
import numpy as np
from backend.pipeline.media_batch.acoustic_inventory import CandidateIndex,verify_anchors


def test_candidates_find_offset_lossy_copy_and_do_not_match_unrelated_audio():
    rng=np.random.default_rng(48)
    original=rng.integers(0,2**32,size=3000,dtype=np.uint32)
    unrelated=rng.integers(0,2**32,size=3000,dtype=np.uint32)
    query=original[260:2400].copy()
    query[::3]^=np.uint32(1<<9)
    index=CandidateIndex([('right',original),('wrong',unrelated)])
    matches=index.find(query)
    assert matches[0]['reference']=='right'
    assert matches[0]['offset_frames']==260 and matches[0]['score']>.97
    assert all(m['reference']!='wrong' for m in matches)
    assert index.find(rng.integers(0,2**32,size=1500,dtype=np.uint32))==[]


def test_silent_common_fingerprints_never_establish_identity():
    assert CandidateIndex([('silent',np.zeros(2000,np.uint32))]).find(np.zeros(1500,np.uint32))==[]


def test_independent_waveform_rejects_same_offset_but_different_audio(monkeypatch):
    from backend.pipeline.media_batch import acoustic_inventory as module
    rng=np.random.default_rng(18)
    def decode(path,start,seconds,rate=8000):
        return rng.normal(0,.1,int(seconds*rate))
    monkeypatch.setattr(module,'decode_clip',decode)
    assert not verify_anchors('query','reference',0,180,180)['verified']


def test_independent_waveform_verifies_offset_and_reports_coverage(monkeypatch):
    from backend.pipeline.media_batch import acoustic_inventory as module
    rng=np.random.default_rng(20)
    samples=rng.normal(0,.1,250*8000)
    offset=7.125
    def decode(path,start,seconds,rate=8000):
        origin=start+(offset if path=='query' else 0)
        begin=round(origin*rate)
        return samples[begin:begin+round(seconds*rate)]
    monkeypatch.setattr(module,'decode_clip',decode)
    result=verify_anchors('query','reference',7,180,250)
    assert result['verified'] and abs(result['offset_seconds']-offset)<.001
    assert len(result['anchors'])==9


def test_continuous_check_rejects_one_changed_second_and_unmatched_source_tail(monkeypatch):
    import io
    from backend.pipeline.media_batch import acoustic_inventory as module
    rng=np.random.default_rng(12)
    original=rng.integers(-10000,10000,60*8000,dtype=np.int16)
    changed=original.copy();changed[20*8000:21*8000]*=-1
    class Process:
        def __init__(self,raw):self.stdout=io.BytesIO(raw)
        def wait(self):return 0
        def poll(self):return 0
    def process(args,**kwargs):
        path=args[args.index('-i')+1]
        return Process((changed if path=='changed' else original).tobytes())
    monkeypatch.setattr(module.subprocess,'Popen',process)
    result=module.verify_continuous('original','changed',0,60,60)
    assert not result['same'] and result['mismatch_count']==1
    result=module.verify_continuous('original','original',0,70,60)
    assert not result['same'] and result['common_audio_verified']
    assert result['unmatched_query_tail_seconds']==10
    assert module.verify_continuous('original','original',0,60,60)['same']
