"""No network: durable adaptive ASR and incremental stage orchestration."""
import json
from pathlib import Path
import pytest
from backend.pipeline import transcript_gemini_audio as audio
from backend.pipeline import transcript_gemini_proofreading as flow
from backend.pipeline.media_batch.runtime import WaitingCapacity


def asr_fixture(tmp_path, monkeypatch, fail=None):
    monkeypatch.setattr(audio.subprocess, 'check_output', lambda *a, **k: b'180')
    def extract(media, target, start, end):
        target.write_text(json.dumps([start, end]))
    monkeypatch.setattr(audio, 'extract', extract)
    calls=[]
    def invoke(path):
        calls.append(path.name)
        if fail and path.name == fail:
            raise TimeoutError('unknown provider outcome')
        return {'model': audio.ASR_MODEL, 'status':'completed', 'steps':[
            {'content':[{'annotations':[{'type':'word_info', 'text':path.stem,
                'speaker':'0', 'start_offset':'2.1s', 'end_offset':'2.2s'}]}]}]}
    monkeypatch.setattr(audio, 'asr_call', invoke)
    return calls


def test_adaptive_tree_replay_uses_only_leaf_answers_and_never_redispatches(tmp_path, monkeypatch):
    calls=asr_fixture(tmp_path, monkeypatch)
    first=audio.transcribe(tmp_path/'source.mp4',tmp_path)
    assert len(calls)==7
    assert len(first['words'])==4
    assert [n['start'] for n in first['coverage']]==[0,45,90,135]
    assert json.loads((tmp_path/'node-0.json').read_text())['children']==['0a','0b']
    assert audio.transcribe(tmp_path/'source.mp4',tmp_path)==first
    assert len(calls)==7


def test_unknown_child_blocks_resume_without_repeating_completed_parent(tmp_path, monkeypatch):
    calls=asr_fixture(tmp_path, monkeypatch, 'asr-0aa.mp3')
    with pytest.raises(TimeoutError): audio.transcribe(tmp_path/'source.mp4',tmp_path)
    count=len(calls)
    with pytest.raises(ValueError,match='never resend'):
        audio.transcribe(tmp_path/'source.mp4',tmp_path)
    assert len(calls)==count


def test_capacity_wait_does_not_poison_legacy_intent(tmp_path, monkeypatch):
    from backend.pipeline import media_model_calls
    monkeypatch.setattr(media_model_calls,'saved_call',lambda *a,**k: (_ for _ in ()).throw(WaitingCapacity('busy')))
    class Model:
        model='fixture'; prompt='source'; config={}
    with pytest.raises(WaitingCapacity):
        flow.saved_response(tmp_path,'flash-0',Model(),{},lambda: pytest.fail('no call'))
    assert not (tmp_path/'flash-0.request.json').exists()


def test_one_stage_checkpoints_can_resume_through_all_remaining_stages(tmp_path, monkeypatch):
    media=tmp_path/'source.mp3';media.write_bytes(b'source')
    cues=[{'id':'1','start':0,'end':4,'speaker':'sp','text':'問題。'}]
    calls=[]
    monkeypatch.setattr(flow,'speaker_roles',lambda *a: calls.append('speakers') or {'sp':'professor'})
    monkeypatch.setattr(flow,'repetition_proposals',lambda *a: calls.append('repetitions') or [])
    out=tmp_path/'out'
    flow.run(cues,media,out,through='speakers')
    assert not (out/'layout.json').exists()
    result=flow.run(cues,media,out,through='layout')
    assert calls==['speakers','repetitions']
    assert result['paragraphs'][0]['text']=='問題。'


def test_completed_empty_asr_answer_preserves_uncovered_leaf(tmp_path, monkeypatch):
    calls=asr_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(audio,'asr_call',lambda path: {'model':audio.ASR_MODEL,
        'status':'completed','usage':{'total_output_tokens':0}})
    assert audio.words({'model':audio.ASR_MODEL,'status':'completed',
        'usage':{'total_output_tokens':0}},'0',0,45,0,45)==[]
    with pytest.raises(ValueError,match='empty-output proof'):
        audio.words({'model':audio.ASR_MODEL,'status':'completed'},'0',0,45,0,45)
    # Empty speech cannot become a completed whole-sermon transcript.
    with pytest.raises(ValueError,match='No word-timed transcript'):
        audio.transcribe(tmp_path/'source.mp4',tmp_path)
    assert json.loads((tmp_path/'node-0aa.json').read_text())['children']==[]


def test_engineering_recovery_binds_exact_plan_code_and_saved_answers(tmp_path):
    from backend.pipeline.media_batch.engineering_recovery import verify
    from backend.pipeline.media_batch.store import key
    from backend.pipeline.media_batch.runtime import atomic,file_sha
    payload={'out':str(tmp_path),'config':{'implementation':'original'}}
    with pytest.raises(ValueError,match='changed after planning'):verify(payload,'corrected')
    proof=tmp_path/'saved.json';proof.write_text('{}')
    atomic(tmp_path/'engineering-recovery.json',dict(original_implementation='original',
        corrected_implementation='corrected',payload_sha256=key(payload),proof={'saved.json':file_sha(proof)}))
    verify(payload,'corrected')
    with pytest.raises(ValueError,match='exact plan'):verify(payload,'another change')
    proof.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='proof changed'):verify(payload,'corrected')
