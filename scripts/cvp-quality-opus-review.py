#!/usr/bin/env python3
"""Read-only Opus semantic evaluation, with every invocation saved separately."""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

SPEC = importlib.util.spec_from_file_location('quality_runner', Path(__file__).with_name('cvp-quality-coverage-runner.py'))
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
PF, H, PK = R.PF, R.H, R.PK
MODEL = 'claude-opus-5-5'
PROMPT = '''你评估知识库是否忠实记录教授的教导，不判断其神学对错，不补他未回答的问题。
必须读完整目标来源及每个关联观点成员的完整自身来源。现有 observations 是另一模型的观察，不是黄金记录。
逐条检查观察表达的意思、对象、条件、否定、因果、模态和范围是否得到完整原文支持，再检查当前 Claim/CVP 的覆盖。
逐字引用与语义忠实度分开：少字、非连续摘引、去掉排版不自动说明观点错误。明确省略是否改变教导。
删除线、编辑文字、反方转述不能被当成教授肯定的依据；但一个引文有问题不妨碍核对其判断或其他观察。
忠实概括是编辑重述，不是教授原话。判断语义差异须提供完整语境中的精确段落和原文，不以字符串差异、票数或同主题作裁决。
当前 Claim 有记录但无 CVP 时明示 claim_only；未核实生产 disposition 不得称为生成失败。连接按实际 role 判读，支持不能冒充等价。
只保存评估意见，不生成内容修订或写库指令。历史管理异议另标 historical_context_required，不在此改变旧人工方向。
覆盖所有输入 observation_id；发现另外的重要未记录判断可放 additional_findings。无法确定就明示 uncertain。
每条 reason 要说明实质依据；citations 提供目标或成员完整来源的 source_id、physical_ordinal、quote。
材料使用可逆 keys 字典，physical_text 是完整原文，~~文字~~为删除文字。返回完整 JSON。'''


def schema():
    citation = {'type': 'object', 'properties': {'source_id': {'type': 'string'},
        'physical_ordinal': {'type': 'integer'}, 'quote': {'type': 'string'}},
        'required': ['source_id', 'physical_ordinal', 'quote'], 'additionalProperties': False}
    row = {'type': 'object', 'properties': {'observation_id': {'type': 'string'},
        'meaning_status': {'type': 'string', 'enum': ['faithful', 'changed_meaning', 'uncertain']},
        'quote_status': {'type': 'string', 'enum': ['verbatim', 'presentation', 'abridged', 'stitched_across_deleted', 'not_grounded', 'uncertain']},
        'coverage_status': {'type': 'string', 'enum': ['covered_by_cvp', 'claim_only', 'partial', 'unrecorded', 'uncertain', 'historical_context_required']},
        'reason': {'type': 'string'}, 'record_ids': {'type': 'array', 'items': {'type': 'string'}},
        'citations': {'type': 'array', 'items': citation}}, 'additionalProperties': False}
    row['required'] = list(row['properties'])
    return {'type': 'object', 'properties': {'observations': {'type': 'array', 'items': row},
        'additional_findings': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'description': {'type': 'string'}, 'reason': {'type': 'string'}, 'citations': {'type': 'array', 'items': citation}},
            'required': ['description', 'reason', 'citations'], 'additionalProperties': False}},
        'limitations': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['observations', 'additional_findings', 'limitations'], 'additionalProperties': False}


def validate(value, packet):
    expected = {r['observation_id'] for r in packet['independent_observations']['observations']}
    actual = [r['observation_id'] for r in value['observations']]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('foreign/duplicate/incomplete observation denominator')
    ids = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith('_id') and isinstance(item, str):
                    ids.add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect({k: v for k, v in packet.items() if k != 'independent_observations'})
    sources = {s['source_id']: s for s in packet['complete_other_member_sources'] + [packet['complete_target_source']]}
    bindings = []
    for row in value['observations'] + value['additional_findings']:
        if not set(row.get('record_ids', [])).issubset(ids):
            raise ValueError('foreign record ID')
        citations = []
        for c in row['citations']:
            if c['source_id'] not in sources:
                raise ValueError('foreign citation source')
            evidence = R.CB.observation_evidence({'observation_id': row.get('observation_id', 'additional'), 'citations': [c]}, sources[c['source_id']])
            citations.append({'source_id': c['source_id'], **evidence['citations'][0]})
        bindings.append({'observation_id': row.get('observation_id'), 'citations': citations,
                         'all_reviewer_citations_bound': bool(citations) and all(c['citation_integrity'] == 'bound' for c in citations)})
    return bindings


def environment():
    result = dict(os.environ)
    for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_BEDROCK_BASE_URL',
                'ANTHROPIC_VERTEX_BASE_URL', 'ANTHROPIC_FOUNDRY_BASE_URL', 'CLAUDE_CODE_OAUTH_TOKEN',
                'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY'):
        result.pop(key, None)
    result.update(CLAUDE_CODE_MAX_OUTPUT_TOKENS='32000', CLAUDE_CODE_MAX_RETRIES='0', MAX_STRUCTURED_OUTPUT_RETRIES='5')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    parser.add_argument('--unit', required=True)
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('UUID-verified independent staging required')
    if args.unit not in {f'source-{n:02}' for n in (1, 2, 4, 5, 6, 7, 8, 10, 11, 12)}:
        raise ValueError('unit lacks saved source observation; historical protected units not included')
    baseline = json.loads((root / 'baseline.json').read_text())
    cfg = PF.settings()
    def stable():
        if PF.sha(PF.query_snapshot(cfg['KNOWLEDGE_DATABASE_URL'])) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('baseline/source drift')
    execution_file = root / 'coverage-execution-v1/execution-e89c9ed11477d943e29e.json'
    execution = json.loads(execution_file.read_text())
    record = next(r for r in execution['records'] if r['source_unit'] == args.unit)
    observe_request = json.loads((root / 'coverage-call-proposal-v1' / (args.unit + '.observe-request.json')).read_text())
    source = R.material(observe_request)['complete_source']
    packet = PK.expand_preview(json.loads((root / 'history-and-closure-v1' / (args.unit + '.complete-comparison-preview.json')).read_text()))
    packet = H.blind(packet)
    packet['complete_target_source'] = source
    packet['independent_observations'] = record['observe']['result']['value']
    if H.blind_leaks(packet):
        raise ValueError('old review metadata in evaluation material')
    work = root / 'opus-semantic-evaluation-v1'
    work.mkdir(exist_ok=True)
    with (work / 'allocation.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        number = 1
        while (work / f'{args.unit}-attempt-{number:03}').exists():
            number += 1
        attempt = work / f'{args.unit}-attempt-{number:03}'
        attempt.mkdir()
    cli = shutil.which('claude')
    if not cli:
        raise ValueError('Claude CLI unavailable')
    env = environment()
    auth = subprocess.run([cli, 'auth', 'status'], env=env, capture_output=True, text=True, timeout=30)
    status = json.loads(auth.stdout or '{}')
    if auth.returncode or status.get('loggedIn') is not True or status.get('authMethod') != 'claude.ai':
        raise ValueError('active Claude subscription required')
    request = {'model': MODEL, 'effort': 'high', 'system_prompt': PROMPT, 'schema': schema(),
        'user_prompt': json.dumps(PK.compact_preview(packet), ensure_ascii=False, separators=(',', ':')),
        'parent_execution_sha256': PF.digest(execution_file), 'independent_from_original_generation': False,
        'authorization': {'model_request': '可以用 Claude Opus 5.5 high. 最新的，很强',
            'call_limit_update': '调用大模型次数不应受限', 'quote_criterion_update': '而且不能说漏字就不对吧'},
        'new_fixed_total_call_limit': None, 'registry_writes': 0}
    PF.write_new(attempt / 'request.json', request)
    PF.write_new(attempt / 'input-citation-states.json', [R.CB.observation_evidence(o, source) for o in packet['independent_observations']['observations']])
    stable()
    command = [cli, '--print', '--safe-mode', '--disable-slash-commands', '--no-session-persistence',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--tools', '', '--permission-mode', 'dontAsk',
        '--model', MODEL, '--effort', 'high', '--system-prompt', PROMPT, '--output-format', 'json',
        '--json-schema', json.dumps(schema(), ensure_ascii=False)]
    PF.write_new(attempt / 'intent.json', {'request_sha256': PF.sha(request), 'cli_sha256': PF.digest(Path(cli)),
        'maximum_cli_invocations_this_attempt': 1, 'automatic_retry': False, 'code_sha256': PF.digest(Path(__file__))})
    print(json.dumps({'unit': args.unit, 'attempt': number, 'model': MODEL, 'effort': 'high', 'state': 'invoking'}), flush=True)
    try:
        completed = subprocess.run(command, input=request['user_prompt'], env=env, cwd=attempt,
            capture_output=True, text=True, timeout=900)
    except Exception as error:
        PF.write_new(attempt / 'outcome-unknown.json', {'exception_type': type(error).__name__, 'automatic_retry': False})
        raise ValueError('Claude outcome unknown; saved attempt retained') from None
    PF.write_new(attempt / 'provider-response.json', {'stdout': completed.stdout, 'stderr': completed.stderr, 'returncode': completed.returncode})
    wrapper = json.loads(completed.stdout)
    if completed.returncode or wrapper.get('is_error') or not isinstance(wrapper.get('structured_output'), dict):
        PF.write_new(attempt / 'no-complete-answer.json', {'is_error': wrapper.get('is_error'), 'state': 'no_complete_answer'})
        print(json.dumps({'unit': args.unit, 'state': 'no_complete_answer', 'attempt_directory': str(attempt)}), flush=True)
        return
    value = wrapper['structured_output']
    bindings = validate(value, packet)
    stable()
    PF.write_new(attempt / 'assessment.json', {'model_requested': MODEL, 'effort': 'high', 'model_usage': wrapper.get('modelUsage'),
        'num_turns': wrapper.get('num_turns'), 'result': value, 'citation_bindings': bindings,
        'input_citation_failure_is_not_semantic_defect': True, 'independent_from_original_generation': False,
        'provider_response_sha256': PF.digest(attempt / 'provider-response.json'), 'registry_writes': 0})
    print(json.dumps({'unit': args.unit, 'state': 'answered', 'observation_count': len(value['observations']),
        'meaning_status_counts': {s: sum(r['meaning_status'] == s for r in value['observations']) for s in ('faithful', 'changed_meaning', 'uncertain')},
        'additional_findings': len(value['additional_findings']), 'attempt_directory': str(attempt)}), flush=True)


if __name__ == '__main__':
    main()
