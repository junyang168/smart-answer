import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from backend.pipeline.claim_anthropic_api import ClaimAnthropicAPI,spend_state


class Stream:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def __iter__(self):return iter([])
    def get_final_message(self):
        return SimpleNamespace(model_dump=lambda **kwargs:{'id':'msg-test','model':'claude-opus-5-5',
            'stop_reason':'end_turn','content':[{'type':'text','text':'{"ok":true}'}],
            'usage':{'input_tokens':1000,'output_tokens':100}})


def api(tmp_path, send=None):
    calls=[]
    def stream(**request):
        calls.append(request)
        return send() if send else Stream()
    client=ClaimAnthropicAPI(spend_root=tmp_path/'billing',client=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    client.call_artifacts=tmp_path/'transport'
    return client,calls


def generate(client):
    return client.generate_json('system','user',{'schema':{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}})


def test_api_preserves_structured_reply_and_replays_without_network_or_cost(tmp_path):
    client,calls=api(tmp_path)
    assert generate(client)=={'ok':True}
    assert calls[0]['output_config']['effort']=='high'
    assert calls[0]['output_config']['format']['type']=='json_schema'
    client.replay_only=True
    assert generate(client)=={'ok':True} and len(calls)==1
    state=json.loads((tmp_path/'billing/spend.json').read_text())
    assert len(state['calls'])==1
    assert next(iter(state['calls'].values()))['cost_usd']==pytest.approx(.006)
    (tmp_path/'transport/response.json').write_text('{}')
    with pytest.raises(ValueError,match='output drift'):generate(client)
    assert len(calls)==1


def test_api_spend_limit_checks_before_dispatch(tmp_path):
    client,calls=api(tmp_path)
    with spend_state(tmp_path/'billing') as state:
        state['calls']['prior']={'reserved_usd':99.99,'cost_usd':99.99}
    with pytest.raises(RuntimeError,match='spend limit'):generate(client)
    assert not calls and not (tmp_path/'transport/invocation.json').exists()


def test_api_unknown_transport_is_not_repeated_and_keeps_cost_reservation(tmp_path):
    def fail():raise TimeoutError('connection lost')
    client,calls=api(tmp_path,fail)
    with pytest.raises(TimeoutError):generate(client)
    with pytest.raises(ValueError,match='returned failure'):generate(client)
    assert len(calls)==1
    state=json.loads((tmp_path/'billing/spend.json').read_text())
    assert 'cost_usd' not in next(iter(state['calls'].values()))


def test_api_http_refusal_has_durable_exit_and_zero_cost(tmp_path):
    class Refusal(Exception):
        status_code=429
        body={'error':{'type':'rate_limit_error'}}
    def fail():raise Refusal()
    client,calls=api(tmp_path,fail)
    with pytest.raises(Refusal):generate(client)
    assert json.loads((tmp_path/'transport/exit.json').read_text())['returncode']==429
    state=json.loads((tmp_path/'billing/spend.json').read_text())
    assert next(iter(state['calls'].values()))['cost_usd']==0
    with pytest.raises(ValueError):generate(client)
    assert len(calls)==1
