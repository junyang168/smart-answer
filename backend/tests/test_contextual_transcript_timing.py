import copy,hashlib
import numpy as np
import pytest
from backend.pipeline.contextual_transcript_timing import edge_anchor,token_ranges,paragraph_groups,require_verified_timing,POLICY,timing_fingerprint,analyse,playback_bindings
from backend.pipeline.slide_change_pilot import save_json,file_sha
from backend.pipeline.transcript_audio_pilot import write_audio


def test_padding_counterexample_rejected_without_silent_time_clamp():
    bad=[{'text':'所','start_ms':0,'end_ms':3200},{'text':'以','start_ms':0,'end_ms':80},{'text':'從','start_ms':80,'end_ms':240}]
    with pytest.raises(ValueError):edge_anchor(bad,20000)
    detached=[{'text':'神','start_ms':300,'end_ms':500},{'text':'預','start_ms':15000,'end_ms':15100}]
    with pytest.raises(ValueError,match='detached'):edge_anchor(detached,20000)


def test_canonical_accent_mapping_does_not_rewrite_source():
    text='心 καρδία，主。'
    spans=token_ranges(text,['心','καρδία','主'])
    assert text[spans[1][0]:spans[1][1]]=='καρδία'
    assert [text[a:b] for a,b in spans][0]=='心'


def test_ownership_passes_cover_every_source_paragraph_once():
    rows=[{'start_ms':i*1000,'end_ms':i*1000+900} for i in range(7)]
    for offset in [0,1]:assert [i for a,b in paragraph_groups(rows,offset) for i in range(a,b)]==list(range(7))


def certified(tmp_path):
    evidence=tmp_path/'evidence';evidence.mkdir();chunks=[]
    record={'schema_version':'full-illustrated-corrected-transcript.v1','source_sha256':'a'*64,'paragraphs':[{'id':'p1','text':'所以','start_ms':380,'end_ms':1020}]}
    for i,duration in enumerate([1600,2000]):
        audio=evidence/f'{i}.wav';write_audio(audio,(np.sin(np.arange(duration*16)*.1)*5000).astype(np.int16));sha=file_sha(audio)
        chunks.append({'id':str(i),'lecture':'source','pass':i,'text':'所以','audio':audio.name,'audio_sha256':sha,'source_origin_ms':0,'sample_count':duration*16,'ranges':[{'id':'p1','start':0,'end':2,'owned':True}]})
        raw={'identity':{'text_sha256':hashlib.sha256('所以'.encode()).hexdigest(),'audio_sha256':sha,'config':{'decode_profile':'monotonic-logit-path.v1'}},'items':[{'text':'所','start_ms':500,'end_ms':700},{'text':'以','start_ms':700,'end_ms':900}]}
        raw['monotonic_items']=copy.deepcopy(raw['items']);save_json(evidence/'alignment'/f'{i}.json',raw)
    plan={'schema_version':POLICY,'chunks':chunks,'records':{'source':{'source_sha256':'a'*64,'duration_ms':2400,'paragraphs':record['paragraphs']}}}
    save_json(evidence/'plan.json',plan)
    certificate={'policy':POLICY,'fingerprint':timing_fingerprint(record),'evidence_root':'evidence','plan_sha256':file_sha(evidence/'plan.json'),'lecture_key':'source'}
    path=tmp_path/'certificate.json';save_json(path,certificate)
    record['timing_quality']={'policy':POLICY,'status':'qualified','certificate_path':'certificate.json','certificate_sha256':file_sha(path)}
    return record


def test_gate_recomputes_real_evidence_and_binds_text_times_and_media(tmp_path):
    record=certified(tmp_path);require_verified_timing(tmp_path,record)
    for key,value in [('start_ms',381),('text','改字')]:
        bad=copy.deepcopy(record);bad['paragraphs'][0][key]=value
        with pytest.raises(ValueError):require_verified_timing(tmp_path,bad)
    bad=copy.deepcopy(record);bad['source_sha256']='b'*64
    with pytest.raises(ValueError):require_verified_timing(tmp_path,bad)
    raw=tmp_path/'evidence/alignment/0.json';data=__import__('json').loads(raw.read_text());data['monotonic_items'][0]['start_ms']=0;save_json(raw,data)
    with pytest.raises(ValueError):require_verified_timing(tmp_path,record)


def test_new_machine_candidate_without_quality_cannot_hand_off(tmp_path):
    from backend.pipeline.video_transcript_handoff import export_handoff
    save_json(tmp_path/'full_transcript.json',{'schema_version':'full-illustrated-corrected-transcript.v1'})
    with pytest.raises(ValueError,match='quality gate'):export_handoff(tmp_path)
    assert not (tmp_path/'handoff').exists()


def test_producer_requires_context_verification_before_handoff(tmp_path,monkeypatch):
    import subprocess
    import json
    from backend.pipeline.contextual_transcript_timing import qualify_staged_record,token_ranges
    record={'schema_version':'full-illustrated-corrected-transcript.v1','source_sha256':'a'*64,'paragraphs':[
        {'id':f'p{i}','text':text,'start_ms':3000*(i+1),'end_ms':3000*(i+1)+600} for i,text in enumerate(['甲乙','丙丁','戊己','庚辛'])]}
    save_json(tmp_path/'master.json',{'source_sha256':'a'*64,'duration_ms':16000})
    write_audio(tmp_path/'master.wav',(np.sin(np.arange(16000*16)*.1)*5000).astype(np.int16))
    def acoustic_worker(command,check):
        root=__import__('pathlib').Path(command[command.index('--out')+1]);plan=__import__('json').loads((root/'plan.json').read_text())
        known={p['id']:p for p in record['paragraphs']}
        for chunk in plan['chunks']:
            items=[]
            for span in chunk['ranges']:
                p=known[span['id']];start=p['start_ms']-chunk['source_origin_ms']
                items += [{'text':p['text'][0],'start_ms':start,'end_ms':start+200},{'text':p['text'][1],'start_ms':start+200,'end_ms':start+600}]
            raw={'identity':{'text_sha256':hashlib.sha256(chunk['text'].encode()).hexdigest(),'audio_sha256':chunk['audio_sha256'],'config':{'decode_profile':'monotonic-logit-path.v1'}},'items':items,'monotonic_items':items}
            save_json(root/'alignment'/f'{chunk["id"]}.json',raw)
    monkeypatch.setattr(subprocess,'run',acoustic_worker)
    qualified=qualify_staged_record(tmp_path,copy.deepcopy(record),__import__('pathlib').Path('/fake/aligner'))
    assert [p['text'] for p in qualified['paragraphs']]==[p['text'] for p in record['paragraphs']]
    assert qualified['timing_quality']['status']=='qualified'
    require_verified_timing(tmp_path,qualified)


def test_failed_verification_cannot_leave_old_handoff_current(tmp_path):
    from backend.pipeline.contextual_transcript_timing import qualify_staged_record
    record={'schema_version':'full-illustrated-corrected-transcript.v1','source_sha256':'a'*64,'paragraphs':[{'id':'p','text':'甲乙','start_ms':3000,'end_ms':3600}]}
    save_json(tmp_path/'master.json',{'source_sha256':'a'*64,'duration_ms':8000})
    write_audio(tmp_path/'master.wav',np.ones(8000*16,dtype=np.int16))
    old=tmp_path/'handoff';old.mkdir();(old/'old.json').write_text('retained')
    with pytest.raises(ValueError,match='aligner-python'):qualify_staged_record(tmp_path,record,None)
    assert not old.exists()
    assert [p.read_text() for p in (tmp_path/'withheld-handoffs').glob('*/old.json')]==['retained']
