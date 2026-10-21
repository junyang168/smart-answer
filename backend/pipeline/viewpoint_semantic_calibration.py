"""Bounded synthetic semantic calibration, never production approval.

Each provider gets one reserved call. Interrupted calls are not retried; valid
saved responses can be rescored. No repository/store/changeset is opened.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import shutil
import urllib.request
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_resolution_runtime import call_model, read_artifact, write_immutable, subscription_client
from backend.pipeline.viewpoint_semantic_contract import semantic_prompt, CONTRACT_PATH
from backend.pipeline.viewpoint_semantic_eval import review_cases, score
from backend.pipeline.codex_subscription_preflight import verify_runtime

ROOT = Path(__file__).resolve().parents[2]
ROLE = '''只读校准：这些是虚构材料，绝非教授引文。source 是每条完整自身语境。
逐条判定 requested_relation 是否 valid / invalid / unresolved，恰好覆盖全部编号。
判定仅针对给定实际主张及关系，不评价神学，不生成修改，不使用工具。
每条给 source 内逐字 quote 及具体 reason；不要猜测测试期望答案。
这是语义规则理解检查，不是生产提议、独立审计或写入批准。'''


class Answer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    verdict: Literal['valid', 'invalid', 'unresolved']
    quote: str
    reason: str


class Answers(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answers: list[Answer]


def blind_dataset(dataset):
    review_cases(dataset)
    result = deepcopy(dataset)
    random.Random(35720261005).shuffle(result['cases'])
    mapping = {}
    for n, row in enumerate(result['cases'], 1):
        identity = f'C{n:03d}'
        mapping[identity] = row['id']
        row['id'] = identity
    return result, mapping


def reserve(path, identity):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation, no automatic retry after interruption/failure.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(identity, stream, ensure_ascii=False, indent=2)


def reserve_call(directory, identity):
    # Code repair is not fresh model-call authorization. Keep historical
    # reservations and compare the semantic call identity across runner SHAs.
    key={k:v for k,v in identity.items() if k!='runner_sha256'}
    for path in directory.glob('*.json'):
        old=read_artifact(path)
        if {k:v for k,v in old.items() if k!='runner_sha256'}==key:
            raise FileExistsError('calibration call already reserved; no code/root reset')
    reserve(directory/f'{sha256_json(identity)}.json',identity)


def load_auditor():
    spec = importlib.util.spec_from_file_location('calibration_independent_auditor', ROOT/'scripts/audit-library.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(output, dataset_path, recovery_root, heldout_path=None):
    dataset = read_artifact(dataset_path)
    if heldout_path:
        heldout=read_artifact(heldout_path)
        review_cases(heldout)
        dataset['cases'].extend(heldout['cases'])
    blinded, mapping = blind_dataset(dataset)
    payload = review_cases(blinded)
    prompt = semantic_prompt(ROLE)
    auditor = load_auditor()
    independent_prompt = auditor.contextual_semantic_rules('wang_cvp_contextual_semantics_v2') + '\n\n## 本阶段任务与输出约束\n\n' + ROLE
    if independent_prompt != prompt:
        raise ValueError('independent rule-loader drift')
    output.mkdir(parents=True, exist_ok=True)
    frozen = {'dataset_sha256':sha256_json(dataset), 'payload_sha256':sha256_json(payload),
              'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'contract_sha256':hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
              'prompt_sha256':sha256_json({'prompt':prompt}),
              'schema_sha256':sha256_json(Answers.model_json_schema()),
              'production_approval':False, 'registry_writes':0,
              'authorization':'owner 2026-10-05 好, 你继续: read-only calibration before g037 conflict handling',
              'test_scope':'synthetic shared-rule classification, not all production-role end-to-end outputs'}
    write_immutable(output/'preflight.json', frozen)
    write_immutable(output/'blind-input.json', payload)
    write_immutable(output/'private-id-map.json', mapping)
    write_immutable(output/'private-expected-dataset.json', blinded)
    # Ignore obsolete .env executable; use the canonical installed executable
    # discovered from PATH and validate it against cached physical/runtime proof.
    codex = subscription_client('codex','gpt-6.1-sol','high')
    codex.executable = shutil.which('codex') or codex.executable
    runtime = verify_runtime(codex, recovery_root/'codex-runtime-proofs')
    write_immutable(output/'codex-runtime-preflight.json', runtime)
    opus = subscription_client('claude','claude-opus-5-5','high')
    adapters = {name:StructuredJsonReviewerAdapter(client=client,prompt=prompt,
                response_model=Answers,schema_name='wang_cvp_semantic_calibration_v1',compact_payload=True)
                for name,client in [('gpt',codex),('opus',opus)]}
    for name, adapter in adapters.items():
        adapter.max_request_bytes=500000
        if adapter.request_bytes(payload)>500000:
            raise ValueError('calibration request exceeds frozen ceiling')
    gemini_prompt=independent_prompt+'\n\nINPUT JSON:\n'+json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    gemini_schema={'type':'object','properties':{'answers':{'type':'array','items':{
        'type':'object','properties':{'id':{'type':'string'},'verdict':{'type':'string','enum':['valid','invalid','unresolved']},
        'quote':{'type':'string'},'reason':{'type':'string'}},'required':['id','verdict','quote','reason']}}},'required':['answers']}
    settings=auditor.load_settings()
    if not settings.get('GEMINI_API_KEY1'):
        raise ValueError('Gemini API credential unavailable')
    gemini_request={'contents':[{'parts':[{'text':gemini_prompt}]}],
                    'generationConfig':{'temperature':0,'responseMimeType':'application/json','responseSchema':gemini_schema}}
    if len(json.dumps(gemini_request,ensure_ascii=False).encode())>500000:
        raise ValueError('Gemini request exceeds frozen ceiling')
    write_immutable(output/'gemini-request.json',gemini_request)

    def evaluate(name):
        raw_path=output/f'raw-{name}.json'
        identity={'provider':name,**frozen}
        if name!='gemini':
            identity.update(model=adapters[name].model_id,backend=adapters[name].backend,
                            generation_config_sha256=adapters[name].generation_config_sha256)
        else:
            identity.update(model='gemini-3.8-flash',backend='gemini_api',request_sha256=sha256_json(gemini_request))
        try:
            if not raw_path.exists():
                reserve_call(recovery_root/'semantic-calibration-reservations',identity)
                write_immutable(output/f'{name}-call-start.json',identity)
            if name!='gemini':
                response,calls,elapsed=call_model(adapters[name],payload,raw_path)
                transport=adapters[name]._client.last_raw_response
                if transport is not None:
                    write_immutable(output/f'{name}-transport.json',{'raw_response':transport})
                if name=='gpt' and codex.cli_identity!=runtime['identity']['cli']:
                    raise ValueError('canonical executable identity changed')
            else:
                if raw_path.exists():
                    raw=read_artifact(raw_path)
                    if raw.get('request_sha256')!=sha256_json(gemini_request) or raw.get('artifact_sha256')!=sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'}):
                        raise ValueError('Gemini cached physical proof drift')
                else:
                    url=auditor.GEMINI_ENDPOINT.format(model='gemini-3.8-flash')+'?key='+settings['GEMINI_API_KEY1']
                    request=urllib.request.Request(url,data=json.dumps(gemini_request,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
                    # Exactly one request, no transport or content retry/fallback.
                    with urllib.request.urlopen(request,timeout=300,context=auditor._trust_store()) as reply:
                        provider=json.loads(reply.read().decode())
                    raw={'model':'gemini-3.8-flash','backend':'gemini_api','request_sha256':sha256_json(gemini_request),'provider_response':provider,'calls_recorded':1}
                    raw['artifact_sha256']=sha256_json(raw)
                    write_immutable(raw_path,raw)
                provider=raw['provider_response']
                candidates=provider.get('candidates') or []
                if provider.get('promptFeedback',{}).get('blockReason') or any(c.get('finishReason')=='SAFETY' for c in candidates):
                    raise ValueError('Gemini calibration content blocked; not a pass, no fallback')
                response=json.loads(''.join(p.get('text','') for p in candidates[0]['content']['parts']))
            parsed=Answers.model_validate(response).model_dump(mode='json')['answers']
            result=score(blinded,parsed)
            result['case_id_map']=mapping
            write_immutable(output/f'{name}-score.json',result)
            print(name,json.dumps(result,ensure_ascii=False),flush=True)
            return result
        except Exception as exc:
            if name!='gemini':
                transport=getattr(adapters[name]._client,'last_raw_response',None)
                if transport is not None:
                    write_immutable(output/f'{name}-failed-transport.json',{'raw_response':transport})
            # Do not print URLs/credentials from transport exceptions.
            write_immutable(output/f'{name}-failure.json',{'error_type':type(exc).__name__,'status':'stopped_no_retry','production_approval':False})
            print(name,'stopped_no_retry',type(exc).__name__,flush=True)
            return {'passed':False,'production_approval':False,'failed':True}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=dict(zip(['gpt','opus','gemini'],pool.map(evaluate,['gpt','opus','gemini'])))
    report={'results':results,'passed':all(r['passed'] for r in results.values()),
            'production_approval':False,'registry_writes':0,'g037_completion':False}
    write_immutable(output/'result.json',report)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--heldout',type=Path)
    parser.add_argument('--recovery-root',type=Path,required=True)
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    # Shared maintenance lock; held for the complete bounded read-only run.
    with (args.recovery_root/'recovery.lock').open('r') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=run(args.output,args.dataset,args.recovery_root,args.heldout)
    raise SystemExit(0 if result['passed'] else 2)


if __name__=='__main__':
    main()
