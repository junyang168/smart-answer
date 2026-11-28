import json
from pathlib import Path
import pytest
from backend.pipeline.media_flash_subscription import FlashSubscription,SubscriptionSetupError

@pytest.fixture
def cli(tmp_path):
    path=tmp_path/'fake-agy'
    path.write_text('''#!/usr/bin/env python3
import json,sys
for line in sys.stdin:
 e=json.loads(line);m=e.get('method');i=e.get('id')
 if m=='initialize':r={'agentCapabilities':{'promptCapabilities':{'audio':False,'image':True}}}
 elif m=='authenticate':
  assert e['params']['methodId']=='oauth-personal';r={}
 elif m=='session/new':r={'sessionId':'session','models':{'availableModels':[{'modelId':'gemini-3.8-flash-low'}]}}
 elif m=='session/set_model':
  assert e['params']['modelId']=='gemini-3.8-flash-low';r={}
 elif m=='session/prompt':
  assert e['params']['prompt'][1]=={'type':'image','mimeType':'image/png','data':'aW1hZ2U='}
  print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'{"ok":true}'}}}}),flush=True)
  r={'stopReason':'end_turn'}
 else:raise RuntimeError(m)
 print(json.dumps({'jsonrpc':'2.0','id':i,'result':r}),flush=True)
''');path.chmod(0o755)
    return path


def test_native_image_subscription_preserves_bytes_model_and_durable_rpc(tmp_path,cli):
    folder=tmp_path/'call'
    raw=FlashSubscription(folder,executable=str(cli)).generate('gemini-3.8-flash-low',
        [{'type':'image','mimeType':'image/png','data':'aW1hZ2U='}], '原提示',
        {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok']})
    assert raw['transport']=='agy_subscription' and raw['answer']=={'ok':True}
    assert raw['model']=='gemini-3.8-flash'
    assert json.loads((folder/'prompt-intent.json').read_text())['model']=='gemini-3.8-flash-low'
    with pytest.raises(ValueError,match='already dispatched'):
        FlashSubscription(folder,executable=str(cli)).generate('gemini-3.8-flash-low',[],'',{})


def test_unsupported_audio_stops_before_any_model_prompt(tmp_path,cli):
    folder=tmp_path/'call'
    with pytest.raises(SubscriptionSetupError,match='audio'):
        FlashSubscription(folder,executable=str(cli)).generate('gemini-3.8-flash-low',
            [{'type':'audio','mimeType':'audio/mp3','data':'YXVkaW8='}],'原提示',{})
    assert not (folder/'prompt-intent.json').exists()
    assert 'session/prompt' not in (folder/'rpc.jsonl').read_text()


def test_unavailable_exact_model_never_silently_selects_another(tmp_path,cli):
    folder=tmp_path/'call'
    with pytest.raises(SubscriptionSetupError,match='Exact Flash'):
        FlashSubscription(folder,executable=str(cli)).generate('gemini-3.8-flash-high',[],'',{})
    assert not (folder/'prompt-intent.json').exists()
