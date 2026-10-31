"""One durable, explicitly billed Opus call. No retries or backend fallback."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path

from backend.pipeline.subscription_process import save, digest_file


def consumed_output_limit(previous, key, slot):
    """A billed, ended call with no text is spent, not an unknown review.

    Authentication only: this never retries or accepts a partial answer.
    """
    from backend.pipeline import claim_management as cm
    from backend.pipeline.source_reconciliation_runner import body, read
    from backend.pipeline.cvp_sql_commit import binding, check_binding
    policy = cm.Policy.model_validate(previous['policy'])
    if (slot['role'] != 'opus' or slot['state'] != 'started'
            or policy.models['opus']['backend'] != 'anthropic_api'):
        raise ValueError('output-limit recovery requires the original started Opus API call')
    stage = Path(previous['execution_root']) / slot['round_id']
    transport = stage / 'opus.transport'
    intent = body(read(stage/'opus.reservation.json'))
    if (intent != slot['intent'] or cm.sha256_json(intent) != slot['intent_sha256']
            or (stage/'opus.raw.json').exists()):
        raise ValueError('output-limit recovery cannot replace a returned review or changed intent')
    receipt = json.loads((transport/'exit.json').read_text())
    if receipt.get('returncode') != 0 or 'response.json' not in receipt.get('files', {}):
        raise ValueError('output-limit recovery requires a complete successful transport receipt')
    for name, digest in receipt['files'].items():
        check_binding({'path':str(transport/name),'file_sha256':digest})
    request = json.loads((transport/'request.json').read_text())
    invocation = json.loads((transport/'invocation.json').read_text())
    wire_sha = hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    message = json.loads((transport/'response.json').read_text())
    usage = message.get('usage', {})
    def text(value):
        return value if isinstance(value,str) else ''.join(b.get('text','') for b in value)
    system = text(request['system'])
    payload = json.loads(text(request['messages'][0]['content']))
    from backend.api.canonical_repository import viewpoint_request_encoding as encoding
    decoders = {'wkp_shared_shapes_json_v4':encoding.decode_shared_shapes_v4,
                'wkp_shared_shapes_json_v6':encoding.decode_shared_shapes_v6}
    if payload.get('format') in decoders:
        payload = decoders[payload['format']](payload)
    packet = body(read(stage/'opus.packet.json'))
    if (invocation != {'backend':'anthropic_api','request_sha256':wire_sha}
            or cm.sha256_json({'prompt':system}) != intent['prompt_sha256']
            or cm.sha256_json(packet) != intent['request_payload_sha256']
            or cm.sha256_json(payload) != intent['request_payload_sha256']
            or request['model'] != policy.models['opus']['model_id']
            or request['output_config']['effort'] != policy.models['opus']['effort']
            or request['max_tokens'] != policy.budget.max_output_tokens
            or message.get('model') != request['model'] or message.get('stop_reason') != 'max_tokens'
            or usage.get('output_tokens') != request['max_tokens']
            or not message.get('content')
            or any(b.get('type') != 'thinking' for b in message['content'])):
        raise ValueError('not a proved no-text Opus output-token exhaustion')
    ledger = json.loads((Path(policy.api_spend['root'])/'spend.json').read_text())['calls'][str(transport.resolve())]
    if (ledger.get('state') != 'returned' or ledger.get('request_sha256') != wire_sha
            or ledger.get('usage') != usage or not ledger.get('cost_usd',0) > 0):
        raise ValueError('output-limit recovery must preserve exact billed API usage')
    return {'slot':key,'spent_call_preserved':True,'usage':usage,
            **{name:binding(transport/name) for name in ('request.json','invocation.json','response.json','exit.json')}}


@contextmanager
def spend_state(root):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    with (root/'spend.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path=root/'spend.json'
        state=json.loads(path.read_text()) if path.exists() else {'limit_usd':100,'calls':{}}
        if state['limit_usd'] != 100:
            raise ValueError('API spend limit binding changed')
        yield state
        temp=path.with_suffix('.partial')
        with temp.open('w') as stream:
            json.dump(state,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)


class ClaimAnthropicAPI:
    backend='anthropic_api'
    # Official Opus 5.5 standard/global rates, verified 2026-10-29.
    INPUT=4/1_000_000
    OUTPUT=20/1_000_000

    def __init__(self, *, model='claude-opus-5-5', reasoning_effort='high',
                 max_output_tokens=32000, timeout_seconds=600, spend_root=None, client=None, cache_enabled=False):
        from anthropic import Anthropic
        if model!='claude-opus-5-5' or reasoning_effort not in {'high','medium'}:
            raise ValueError('API bridge preserves exact Opus model and effort')
        if not spend_root or not Path(spend_root).is_absolute():
            raise ValueError('explicit absolute API spend root required')
        self.model=model;self.reasoning_effort=reasoning_effort
        self.max_output_tokens=max_output_tokens;self.timeout_seconds=timeout_seconds
        self.spend_root=Path(spend_root)
        self.cache_enabled=cache_enabled
        self.client=client or Anthropic(max_retries=0,timeout=timeout_seconds)
        self.last_raw_response=None

    def generate_json(self, system_prompt, user_prompt, json_schema, temperature=0,
                      timeout_seconds=None, **unused):
        from anthropic.lib._parse._transform import transform_schema
        schema=json_schema.get('schema',json_schema)
        request={'model':self.model,'max_tokens':self.max_output_tokens,'system':system_prompt,
            'messages':[{'role':'user','content':user_prompt}],
            'output_config':{'effort':self.reasoning_effort,
                'format':{'type':'json_schema','schema':transform_schema(schema)}}}
        if self.cache_enabled:
            # Exact same strings, explicit 5-minute provider cache breakpoints.
            control={'type':'ephemeral','ttl':'5m'}
            request['system']=[{'type':'text','text':system_prompt,'cache_control':control}]
            request['messages'][0]['content']=[{'type':'text','text':user_prompt,'cache_control':control}]
        root=Path(self.call_artifacts);root.mkdir(parents=True,exist_ok=True)
        wire=json.dumps(request,ensure_ascii=False,sort_keys=True).encode()
        intent={'backend':self.backend,'request_sha256':hashlib.sha256(wire).hexdigest()}
        invocation=root/'invocation.json';receipt=root/'exit.json';response=root/'response.json'
        if invocation.exists():
            if json.loads(invocation.read_text())!=intent or not receipt.exists():
                raise ValueError('API intent differs or has no exit receipt; no retry')
            exit_state=json.loads(receipt.read_text())
            if (exit_state.get('returncode')!=0 or not response.exists()
                    or exit_state['files'].get('response.json')!=digest_file(response)):
                raise ValueError('API returned failure or output drift; no retry')
            message=json.loads(response.read_text())
        else:
            if getattr(self,'replay_only',False):raise ValueError('API reservation has no invocation; no retry')
            # Conservative reservation: every serialized UTF-8 byte as one input
            # token, plus schema/service overhead, and all possible output tokens.
            reserve=(len(wire)+8192)*(5/1_000_000 if self.cache_enabled else self.INPUT)+self.max_output_tokens*self.OUTPUT
            key=str(root.resolve())
            with spend_state(self.spend_root) as state:
                if key in state['calls']:raise ValueError('API cost reservation already exists; no retry')
                if sum(c.get('cost_usd',c['reserved_usd']) for c in state['calls'].values())+reserve>state['limit_usd']:
                    raise RuntimeError('API campaign spend limit reached; no call dispatched')
                state['calls'][key]={'reserved_usd':reserve,'state':'reserved','request_sha256':intent['request_sha256']}
            save(root/'request.json',request);save(invocation,intent)
            (root/'stdout.log').touch(exist_ok=False);(root/'stderr.log').touch(exist_ok=False)
            exit_state={'returncode':None}
            try:
                # Streaming retains the exact returned envelope before decoding.
                with self.client.messages.stream(**request) as stream:
                    with (root/'events.jsonl').open('x') as events:
                        for event in stream:
                            events.write(event.model_dump_json()+'\n'); events.flush();os.fsync(events.fileno())
                    message=stream.get_final_message().model_dump(mode='json')
                save(response,message)
                usage=message.get('usage',{})
                creation=usage.get('cache_creation') or {}
                writes=usage.get('cache_creation_input_tokens',0)
                hour=creation.get('ephemeral_1h_input_tokens',0)
                short=creation.get('ephemeral_5m_input_tokens',writes-hour)
                if short+hour != writes:raise ValueError('cache token accounting does not reconcile')
                cost=(usage.get('input_tokens',0)*self.INPUT+usage.get('output_tokens',0)*self.OUTPUT
                    +short*5/1_000_000+hour*8/1_000_000+usage.get('cache_read_input_tokens',0)*.20/1_000_000)
                with spend_state(self.spend_root) as state:
                    state['calls'][key].update(state='returned',cost_usd=cost,usage=usage)
                exit_state={'returncode':0}
            except Exception as exc:
                status=getattr(exc,'status_code',None)
                body=getattr(exc,'body',None)
                detail=body.get('error',{}) if isinstance(body,dict) else {}
                error_type=detail.get('type',type(exc).__name__) if isinstance(detail,dict) else type(exc).__name__
                error={'is_error':True,'api_error_status':status,
                       'api_error':error_type,'result':'Anthropic API request failed'}
                save(root/'error.json',error)
                (root/'stderr.log').write_text(json.dumps(error))
                (root/'stdout.log').write_text(json.dumps(error))
                # A returned HTTP refusal is known; a disconnected stream is not.
                exit_state={'returncode':status if status and status>=400 else None,
                            'error_type':type(exc).__name__}
                if status and status>=400:
                    with spend_state(self.spend_root) as state:
                        state['calls'][key].update(state='http_refusal',cost_usd=0)
                raise
            finally:
                files=[p for p in (root/'stdout.log',root/'stderr.log',response,root/'error.json') if p.exists()]
                save(receipt,{**exit_state,'files':{p.name:digest_file(p) for p in files},
                    'finished_at':datetime.now(timezone.utc).isoformat()})
        self.last_raw_response=message
        if message.get('stop_reason')!='end_turn':
            raise RuntimeError('API output is incomplete or refused; saved original envelope')
        texts=[b.get('text','') for b in message.get('content',[]) if b.get('type')=='text']
        result=json.loads(''.join(texts))
        if not isinstance(result,dict):raise ValueError('API response must be a JSON object')
        return result
