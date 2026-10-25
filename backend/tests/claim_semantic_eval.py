"""Opt-in, read-only semantic probe; never creates Claim qualifications.

Uses the production Opus prompt, with a small evaluation-only response schema.
This tests semantic judgment, not the complete production packet/SQL workflow.
Expected answers and synthetic/real labels are never sent to the model.
"""
import argparse
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.pipeline import claim_management as cm
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter

FIXTURE = Path(__file__).parent / 'fixtures/claim_management/semantic_scope.json'


class Judgment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    case_id: str
    supported: bool
    reason: str


class Results(BaseModel):
    model_config = ConfigDict(extra='forbid')
    judgments: list[Judgment]


def inputs(cases):
    return [{k: c[k] for k in ('case_id', 'source', 'statement')} for c in cases]


def score(cases, response):
    rows = Results.model_validate(response).judgments
    if len(rows) != len(cases) or {r.case_id for r in rows} != {c['case_id'] for c in cases}:
        raise ValueError('missing, duplicate or foreign case judgment')
    expected = {c['case_id']: c['expected_supported'] for c in cases}
    if any(not r.reason.strip() for r in rows):
        raise ValueError('judgment needs a reason')
    return {'total': len(rows), 'correct': sum(r.supported == expected[r.case_id] for r in rows),
            'false_accepts': [r.case_id for r in rows if r.supported and not expected[r.case_id]],
            'false_rejects': [r.case_id for r in rows if not r.supported and expected[r.case_id]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--prompt', type=Path, help='Frozen composed baseline prompt')
    parser.add_argument('--packet', type=Path, help='Replay an archived production packet, with its original schema; never commit results')
    parser.add_argument('--live', action='store_true', help='Explicitly make one Opus call; no retries')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required for a model evaluation')
    if args.packet and args.prompt:
        parser.error('--packet uses the current production prompt; do not combine with --prompt')
    args.root.mkdir(parents=True, exist_ok=False)
    cases = json.loads(FIXTURE.read_text())['cases']
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/'wang_claim_management_joint_update_v6.json').read_text())
    adapter = claim_management_model_adapter('opus', policy)
    prompt = args.prompt.read_text() if args.prompt else adapter._base_prompt
    prompt += '\n本次仅为隔离语义测试，不执行生产操作。每个 case 独立判断所给 source 能否支持 statement 的完整意思；其他 case 不可互作证据。只输出 judgments（case_id、supported、简短 reason），替代生产输出结构。没有 Evidence 对象，无需判断绑定完整性；不要虚构缺包异议。'
    payload = json.dumps(inputs(cases), ensure_ascii=False)
    def save(name, value):
        (args.root/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    from dotenv import load_dotenv
    load_dotenv('backend/.env'); load_dotenv('.env')
    if args.packet:
        from backend.pipeline.claim_model_output import scoped_adapter
        packet = json.loads(args.packet.read_text())
        policy = cm.Policy.model_validate(packet['policy'])
        adapter = scoped_adapter(claim_management_model_adapter('opus', policy, live=True), packet)
        adapter._client.call_artifacts = args.root/'transport'
        save('intent.json', {'purpose':'isolated archived packet replay, no production writes or qualification',
            'calls':1, 'packet_path':str(args.packet),
            'packet_sha256':hashlib.sha256(args.packet.read_bytes()).hexdigest(),
            'prompt_sha256':adapter.prompt_sha256, 'model':policy.models['opus']['model_id']})
        (args.root/'system-prompt.txt').write_text(adapter._prompt)
        save('request.json', packet)
        response = adapter.generate(packet)
        save('response.json', response)
        adapter._response_model.model_validate(response)
        save('metadata.json', adapter._client.last_metadata)
        print(json.dumps({'findings':len(response['findings']), 'schema_valid':True}))
        return
    (args.root/'system-prompt.txt').write_text(prompt)
    save('request.json', inputs(cases))
    save('intent.json', {'model':policy.models['opus']['model_id'], 'calls':1,
        'purpose':'isolated semantic evaluation, no production writes or qualification',
        'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
        'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest()})
    from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
    client = ClaudeSubscriptionClient(model=policy.models['opus']['model_id'], reasoning_effort='high')
    client.call_artifacts = args.root/'transport'
    response = client.generate_json(prompt, payload, Results.model_json_schema())
    save('response.json', response)
    save('metadata.json', client.last_metadata)
    result = score(cases, response)
    save('result.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
