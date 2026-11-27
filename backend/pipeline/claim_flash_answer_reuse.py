"""Explicitly admit a successful identical answer without rewriting unknown calls."""
import json
import hashlib
from pathlib import Path
from copy import deepcopy
from backend.pipeline import evidence_refresh as e

CONTRACT='claim_flash_identical_returned_answer_v1'


def read(path):return json.loads(Path(path).read_text())


def visible(request):
    value=deepcopy(request)
    # Both are bookkeeping outside the model-visible prompt/schema.
    for key in ('execution_recovery','diagnostic_scope'):value.pop(key,None)
    return value


def returned_answer(root,ports,request):
    target=Path(root)/'gpt'/e.digest(request);path=target/'returned-answer-admission.json'
    if not path.exists():return None
    authority=read(path);operation=Path(ports.root).resolve()
    def inside(path):
        path=Path(path).resolve()
        if not path.is_relative_to(operation):raise ValueError('returned answer is outside the operation')
        return path
    source_path=inside(authority['source_request_path']);source=read(source_path)
    source_key=e.digest(source);key=e.digest(request)
    if (authority.get('contract')!=CONTRACT or not authority.get('owner_direction')
            or authority.get('target_request_sha256')!=key
            or authority.get('source_request_sha256')!=source_key
            or source_path.name!='request.json' or source_path.parent.name!=source_key
            or request.get('input',{}).get('task')!='evidence_fidelity_repair'
            or visible(source)!=visible(request)
            or request['identity']!=ports.identity['gpt']
            or not (target/'started.json').exists() or (target/'response.json').exists()):
        raise ValueError('returned answer admission differs from its exact stopped request')
    if read(target/'request.json')!=request:raise ValueError('returned answer target request changed')
    source_transport=inside(operation/'gpt'/source_key/'transport')
    target_transport=inside(operation/'gpt'/key/'transport')
    source_exit=read(source_transport/'exit.json');target_exit=read(target_transport/'exit.json')
    if (e.digest(source_exit)!=authority.get('source_exit_sha256')
            or e.digest(target_exit)!=authority.get('target_exit_sha256')
            or source_exit.get('returncode')!=0 or target_exit.get('error_type')!='TimeoutExpired'
            or target_exit.get('returncode') is not None or (target_transport/'response.json').exists()):
        raise ValueError('returned answer requires exact success and stopped timeout receipts')
    for transport,receipt in ((source_transport,source_exit),(target_transport,target_exit)):
        files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
               (transport/'stdout.log',transport/'stderr.log',transport/'response.json') if p.exists()}
        if receipt.get('files')!=files:raise ValueError('returned answer transport files changed')
    src_inv=read(source_transport/'invocation.json');dst_inv=read(target_transport/'invocation.json')
    def command(value,transport,invocation):
        if (not isinstance(value,list) or not all(isinstance(v,str) for v in value)
                or hashlib.sha256(json.dumps(value).encode()).hexdigest()!=invocation['command_sha256']):
            raise ValueError('returned answer command does not match its invocation')
        normalized=list(value)
        for flag,name in (('--output-schema','response-schema.json'),('--output-last-message','response.json')):
            index=normalized.index(flag)+1
            if normalized[index]!=str(transport/name):raise ValueError('returned answer command output path differs')
            normalized[index]='<'+name+'>'
        if (normalized[normalized.index('--model')+1]!=request['identity']['model']
                or 'model_reasoning_effort="'+request['identity']['effort']+'"' not in normalized):
            raise ValueError('returned answer command model or effort differs')
        return normalized
    if command(authority['source_command'],source_transport,src_inv)!=command(authority['target_command'],target_transport,dst_inv):
        raise ValueError('returned answer actual commands differ')
    if any(src_inv[k]!=dst_inv[k] for k in ('input_sha256','executable_sha256')):
        raise ValueError('returned answer actual model input or executable differs')
    if (source_transport/'response-schema.json').read_bytes()!=(target_transport/'response-schema.json').read_bytes():
        raise ValueError('returned answer actual output schema differs')
    response=read(source_path.parent/'response.json')
    if (not isinstance(response,dict) or e.digest(response)!=authority.get('response_sha256')
            or response!=read(source_transport/'response.json')):
        raise ValueError('returned answer saved response differs from successful transport')
    e.save(target/'returned-answer-proof.json',{'contract':CONTRACT,'authority':authority,
        'source_request_path':str(source_path),'response_sha256':e.digest(response),
        'model_calls':0,'old_unknown_outcome_preserved':True,'normal_validation_and_commit':'still_required'})
    return response
