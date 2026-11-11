import copy
import json
from pathlib import Path

import numpy as np
import pytest

from backend.pipeline.transcript_seam_repair import propose,verify_acoustics,retained_quote_supported,stitch_with_recovery
from backend.pipeline.transcript_text_layout import text_sha,stitch,SeamCoverageError
from backend.pipeline.transcript_audio_pilot import write_audio
from backend.pipeline.slide_change_pilot import file_sha,save_json,read_json


def case(tmp_path):
    chunks=[]
    for id,text,start,core_start,core_end,items in [
        ('a','甲乙丙丁戊',0,0,160000,[('甲',100,200),('乙',8000,8200),('丙',11000,11200),('丁',11200,11400),('戊',11400,11600)]),
        ('b','乙丙丁戊己庚',128000,160000,320000,[('乙',100,200),('丙',500,600),('丁',2000,2200),('戊',3000,3200),('己',4000,4200),('庚',5000,5200)])]:
        audio=tmp_path/f'{id}.wav';write_audio(audio,np.zeros(12*16000,dtype=np.int16))
        chunk=dict(id=id,text=text,audio=audio.name,audio_sha256=file_sha(audio),sample_start=start,sample_end=start+192000,
            core_start_sample=core_start,core_end_sample=core_end,start_ms=start//16,end_ms=(start+192000)//16)
        chunks.append(chunk)
        save_json(tmp_path/f'alignment/{id}.json',dict(identity=dict(text_sha256=text_sha(text),audio_sha256=chunk['audio_sha256']),
            items=[dict(text=t,start_ms=s,end_ms=e) for t,s,e in items]))
    save_json(tmp_path/'asr.json',dict(chunks=chunks))
    return chunks


def proposal(chunks,tmp_path):
    return propose(*chunks,read_json(tmp_path/'alignment/a.json')['items'],read_json(tmp_path/'alignment/b.json')['items'])


def local_result(ref):
    spans={'left':{'甲':(100,200),'乙':(8000,8200),'丙':(9000,9200),'丁':(10000,10200),'戊':(11000,11200)},
        'right':{'乙':(0,200),'丙':(1000,1200),'丁':(2000,2200),'戊':(3000,3200),'己':(4000,4200),'庚':(5000,5200)}}
    return dict(identity=dict(text_sha256=text_sha(ref['text'])),items=[dict(text=c,start_ms=spans[ref['id']][c][0],end_ms=spans[ref['id']][c][1]) for c in ref['text']])


def test_local_witnesses_must_agree_inside_actual_overlap(tmp_path):
    chunks=case(tmp_path);pair=proposal(chunks,tmp_path)
    results={r['id']:local_result(r) for r in pair['references']}
    assert pair['phrase']=='乙丙丁戊'
    assert len(verify_acoustics(pair,results))==2
    broken=copy.deepcopy(results);broken['right']['items'][1]['start_ms']=4000
    with pytest.raises(ValueError):verify_acoustics(pair,broken)
    broken=copy.deepcopy(results);broken['right']['items'][0]['end_ms']=0
    broken['right']['items'][0]['start_ms']=-100
    with pytest.raises(ValueError):verify_acoustics(pair,broken)


def test_no_fuzzy_or_repeated_short_phrase_repair(tmp_path):
    chunks=case(tmp_path);items=[read_json(tmp_path/f'alignment/{c["id"]}.json')['items'] for c in chunks]
    bad=copy.deepcopy(chunks);bad[1]['text']='異丙丁戊己庚'
    with pytest.raises(ValueError,match='exact shared'):propose(*bad,*items)
    bad=copy.deepcopy(chunks);bad[0]['core_end_sample']+=1
    with pytest.raises(ValueError,match='gap or overlap'):propose(*bad,*items)


def test_local_recovery_preserves_unique_words_and_reuses_saved_proof(tmp_path,monkeypatch):
    case(tmp_path);calls=[]
    def fake_align(folder,python):
        calls.append(folder)
        for c in read_json(folder/'asr.json')['chunks']:
            ref=dict(id=c['id'],text=c['text'])
            result=local_result(ref);result['identity']['audio_sha256']=c['audio_sha256']
            save_json(folder/f'alignment/{c["id"]}.json',result)
    monkeypatch.setattr('backend.pipeline.full_video_transcript.align',fake_align)
    with pytest.raises(SeamCoverageError):stitch(tmp_path)
    result=stitch_with_recovery(tmp_path,Path('/unused'))
    assert result['text']=='甲乙丙丁戊\n己庚'
    assert len(calls)==1
    assert stitch_with_recovery(tmp_path,Path('/unused'))['text']==result['text']
    assert len(calls)==1
    proof_ref=read_json(tmp_path/'seam-repair.json');proof=tmp_path/proof_ref['proof']
    saved=read_json(proof);evidence=tmp_path/saved['pairs'][0]['case']/'alignment/right.json'
    evidence.write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'):stitch(tmp_path)


def test_failed_local_proof_is_not_retried_or_published(tmp_path,monkeypatch):
    case(tmp_path);calls=[]
    def fake_align(folder,python):
        calls.append(folder)
        for c in read_json(folder/'asr.json')['chunks']:
            result=local_result(dict(id=c['id'],text=c['text']))
            result['identity']['audio_sha256']=c['audio_sha256']
            if c['id']=='right':result['items'][0]['start_ms']=-1
            save_json(folder/f'alignment/{c["id"]}.json',result)
    monkeypatch.setattr('backend.pipeline.full_video_transcript.align',fake_align)
    for _ in range(2):
        with pytest.raises(SeamCoverageError,match='capacity'):stitch_with_recovery(tmp_path,Path('/unused'))
    assert len(calls)==1 and not (tmp_path/'timed_text.json').exists()


def test_audio_witness_must_preserve_full_heard_prefix_and_not_reverse_rejection():
    pair={'phrase':'成為人所以啊'}
    answer=dict(verdict='exact_single_occurrence',heard_overlap='必須成為人。所以啊',reason='Audio quote')
    assert retained_quote_supported(answer,pair,'他必須成為人。所以啊，')
    assert not retained_quote_supported(answer,pair,'他成為人。所以啊，')
    assert not retained_quote_supported({**answer,'verdict':'different_or_repeated'},pair,'他必須成為人。所以啊，')
    assert not retained_quote_supported({**answer,'heard_overlap':'成為人所以啊成為人所以啊'},pair,'成為人所以啊成為人所以啊')


def test_audio_fallback_reuses_exact_answer_and_binds_actual_response(tmp_path,monkeypatch):
    case(tmp_path);model_calls=[]
    def fake_align(folder,python):
        for c in read_json(folder/'asr.json')['chunks']:
            result=local_result(dict(id=c['id'],text=c['text']))
            result['identity']['audio_sha256']=c['audio_sha256']
            result['items'][0]['start_ms']=-1
            save_json(folder/f'alignment/{c["id"]}.json',result)
    monkeypatch.setattr('backend.pipeline.full_video_transcript.align',fake_align)
    from backend.pipeline.slide_pilot_models import VisionReviewer
    def init(self,provider,prompt,schema):
        self.provider=provider;self.prompt=prompt;self.config={};self.model='gemini-3.8-flash'
    def generate(self,contents):
        model_calls.append(contents)
        answer=dict(verdict='exact_single_occurrence',heard_overlap='乙丙丁戊',reason='Synthetic audio check')
        return {'candidates':[{'finish_reason':'STOP','content':{'parts':[{'text':json.dumps(answer)}]}}]}
    monkeypatch.setattr(VisionReviewer,'__init__',init);monkeypatch.setattr(VisionReviewer,'generate',generate)
    result=stitch_with_recovery(tmp_path,Path('/unused'),tmp_path/'capacity')
    assert result['text']=='甲乙丙丁戊\n己庚'
    assert stitch_with_recovery(tmp_path,Path('/unused'),tmp_path/'capacity')['text']==result['text']
    assert len(model_calls)==1
    ref=read_json(tmp_path/'seam-repair.json');proof=read_json(tmp_path/ref['proof'])
    (tmp_path/proof['pairs'][0]['response']).write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'):stitch(tmp_path)


def test_window_joins_cannot_trim_text_or_leave_sample_gaps(tmp_path):
    from backend.pipeline.transcript_seam_repair import audit_window_coverage
    master=dict(covered_samples=32000,windows=[])
    for i in range(2):
        root=tmp_path/f'w{i}';root.mkdir()
        text='前文' if not i else '後文'
        save_json(root/'asr.json',dict(chunks=[dict(id='c',text=text,core_start_sample=0,core_end_sample=16000)]))
        save_json(root/'timed_text.json',dict(asr_sha256=file_sha(root/'asr.json'),text=text,text_sha256=text_sha(text),
            chunk_ownership=[dict(chunk_id='c',source_start=0,source_end=len(text),output_start=0,output_end=len(text),excluded_prefix='',excluded_suffix='')]))
        master['windows'].append(dict(id=f'w{i}',audio=f'w{i}',origin_sample=i*16000,end_sample=(i+1)*16000))
    assert len(audit_window_coverage(tmp_path,master)['windows'])==2
    changed=copy.deepcopy(master);changed['windows'][1]['origin_sample']+=1
    with pytest.raises(ValueError,match='sample gap'):audit_window_coverage(tmp_path,changed)
    path=tmp_path/'w1/timed_text.json';timed=read_json(path);timed['chunk_ownership'][0]['source_start']=1;save_json(path,timed)
    with pytest.raises(ValueError,match='trimmed'):audit_window_coverage(tmp_path,master)


def test_unresolved_seam_is_explicitly_reported_to_the_batch_worker(tmp_path,monkeypatch):
    from backend.pipeline.media_batch.worker import child
    payload=tmp_path/'payload.json';save_json(payload,{})
    target=tmp_path/'result.json'
    monkeypatch.setenv('MEDIA_BATCH_CONTEXT',json.dumps(dict(payload_path=str(payload),result_path=str(target),stage='speech')))
    def fail(stage,payload):raise SeamCoverageError('Uncertain shared phrase; original text retained')
    monkeypatch.setattr('backend.pipeline.media_batch.stages.execute',fail)
    with pytest.raises(SystemExit):child()
    result=read_json(target)
    assert not result['ok'] and result['needs_resolution'] and 'original text retained' in result['error']
