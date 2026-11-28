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


def test_missing_word_end_preserves_text_and_marks_timing_for_review():
    raw={'model':audio.ASR_MODEL,'status':'completed','steps':[{'content':[{'annotations':[
        {'type':'word_info','text':'原文','start_offset':'2s'},
        {'type':'word_info','text':'後文','start_offset':'3s','end_offset':'4s'}]}]}]}
    found=audio.words(raw,'0',0,45,0,45)
    assert found[0]=={'text':'原文','speaker':'w0:unknown','start':2.0,'end':3.0,'timing_review':True}
    assert found[1]['timing_review'] is False
    # Incomplete tail in the overlap remains outside this window's owned range.
    raw['steps'][0]['content'][0]['annotations']=[{'type':'word_info','text':'尾端','start_offset':'183.4s'}]
    assert audio.words(raw,'0',0,180,0,184)==[]


def test_fresh_stage_worker_loads_backend_and_root_model_environment(monkeypatch):
    from backend.pipeline.media_batch.worker import load_model_environment
    import dotenv
    paths=[]
    monkeypatch.setattr(dotenv,'load_dotenv',lambda path:paths.append(path))
    load_model_environment()
    assert paths[0]==paths[1].parent/'backend/.env' and paths[1].name=='.env'


def test_sol_media_transport_is_subscription_only_and_durable(tmp_path,monkeypatch):
    import openai
    from backend.pipeline.slide_pilot_models import VisionReviewer
    from backend.pipeline.media_model_calls import saved_call
    from backend.pipeline import codex_subscription_client as transport
    monkeypatch.setattr(openai,'OpenAI',lambda *a,**k:pytest.fail('API client forbidden'))
    monkeypatch.setenv('OPENAI_API_KEY','must-not-use-api')
    calls=[]
    class Client:
        def __init__(self,**options):
            calls.append(options);self.last_usage={'input_tokens':4,'output_tokens':3};self.cli_identity={'model':'gpt-6.1-sol'}
        def generate_json(self,prompt,content,schema,**kw):
            assert prompt=='原提示' and content=='原輸入' and schema=={'type':'object'}
            assert self.call_artifacts==tmp_path/'calls'/request_hash/'subscription'
            assert self.capture_usage_events
            return {'o':['原結果']}
    monkeypatch.setattr(transport,'CodexSubscriptionClient',Client)
    model=VisionReviewer('openai','原提示',{'type':'object'})
    model.config['reasoning']={'effort':'medium'}
    request={'model':model.model,'input':'原輸入','config':model.config}
    from backend.pipeline.slide_change_pilot import object_sha
    request_hash=object_sha(request)
    def invoke():return model.generate([{'role':'user','content':'原輸入'}])
    raw=saved_call(tmp_path,request,.6,invoke)
    assert raw['transport']=='codex_subscription' and json.loads(model.text(raw))=={'o':['原結果']}
    assert saved_call(tmp_path,request,.6,invoke)==raw and len(calls)==1
    assert calls[0]['model']=='gpt-6.1-sol' and calls[0]['reasoning_effort']=='medium'
    from backend.pipeline.media_batch.costs import estimate
    with pytest.raises(ValueError,match='Subscription usage'):estimate(request,raw)


def test_flash_images_use_subscription_without_api_credentials(tmp_path,monkeypatch):
    from backend.pipeline.slide_pilot_models import VisionReviewer
    from backend.pipeline.media_model_calls import saved_call
    from backend.pipeline import media_flash_subscription
    from google.genai import types
    calls=[]
    class Flash:
        def __init__(self,folder):self.folder=folder
        def start(self):self.capabilities={'image':True,'audio':False}
        def close(self):pass
        def generate(self,model,parts,prompt,schema):
            calls.append((model,parts,prompt,schema))
            assert self.folder.name=='subscription'
            assert parts==[{'type':'image','mimeType':'image/png','data':'aW1hZ2U='}]
            return {'model':'gemini-3.8-flash','status':'completed','transport':'agy_subscription','answer':{'ok':True}}
    monkeypatch.setattr(media_flash_subscription,'FlashSubscription',Flash)
    adapter=VisionReviewer('gemini','原提示',{'type':'object'})
    adapter.client=None
    adapter.config['thinking_config']={'thinking_level':'LOW'}
    request={'model':adapter.model,'config':adapter.config}
    raw=saved_call(tmp_path,request,.1,lambda:adapter.generate([types.Part.from_bytes(data=b'image',mime_type='image/png')]))
    assert json.loads(adapter.text(raw))=={'ok':True}
    assert calls[0][0]=='gemini-3.8-flash-low'
    assert adapter.count([types.Part.from_text(text='內容')])==len('內容'.encode())


@pytest.mark.parametrize('audio_supported',[False,True])
def test_flash_audio_probes_native_capability_before_choosing_transport(tmp_path,monkeypatch,audio_supported):
    from backend.pipeline.slide_pilot_models import VisionReviewer
    from backend.pipeline.media_model_calls import saved_call
    from backend.pipeline import media_flash_subscription
    from google.genai import types
    from types import SimpleNamespace
    seen=[]
    class Flash:
        def __init__(self,folder):pass
        def start(self):self.capabilities={'image':True,'audio':audio_supported};seen.append('probe')
        def close(self):seen.append('close')
        def generate(self,model,parts,prompt,schema):
            assert parts==[{'type':'audio','mimeType':'audio/mp3','data':'YXVkaW8='}]
            seen.append('subscription')
            return {'transport':'agy_subscription','status':'completed','model':'gemini-3.8-flash','answer':{}}
    monkeypatch.setattr(media_flash_subscription,'FlashSubscription',Flash)
    def api(**kwargs):
        seen.append('api')
        return SimpleNamespace(model_dump=lambda **kw:{'model_version':'gemini-3.8-flash'})
    adapter=VisionReviewer('gemini','source',{})
    adapter.client=SimpleNamespace(models=SimpleNamespace(generate_content=api))
    raw=saved_call(tmp_path,{'model':adapter.model,'audio':'bound source'},.6,
        lambda:adapter.generate([types.Part.from_bytes(data=b'audio',mime_type='audio/mp3')]))
    assert seen[0]=='probe' and 'close' in seen
    if audio_supported:
        assert raw['transport']=='agy_subscription' and 'api' not in seen
    else:
        assert raw['transport']=='gemini_api' and raw['transport_reason']=='subscription_audio_capability_false'
        assert raw['subscription_capabilities']['audio'] is False and 'subscription' not in seen
