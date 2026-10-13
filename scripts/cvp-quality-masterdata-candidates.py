#!/usr/bin/env python3
"""Nominate cross-CVP identity candidates; full-source adjudication stays separate."""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

SPEC = importlib.util.spec_from_file_location('quality_opus', Path(__file__).with_name('cvp-quality-opus-review.py'))
O = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(O)
PF = O.PF
PROMPT = '''这些是教授观点的 current master records。你的任务仅是找需要完整原文核查的跨 CVP 候选。
阅读全部 current core proposition、scope、signature，识别措辞不同但可能是同一核心判断的记录，以及相似却可能应保持不同的记录。
不要只靠同主题或共同经文；考察对象、谓词、否定、条件、因果、模态、人群、时段。观点粒度/范围不同通常不能直接合并。
还没有双方的完整原文，不能宣布任何归并或缺陷；possible_same_identity 只是候选，related_distinct_control 是对照。
优先跨不同物理来源的候选，不把同一原件的 alias 当跨讲道重复。只引用输入中的精确 viewpoint_id。
合理列出需要核查的强候选与困难的相似但不同对照，不以凑满数量为目标。理由明确指出待核实的同一谓词或区别。
这是候选发现，不修改知识库，不判断神学正确，不补教授未讲的内容。reason 内用中文「」引号，返回完整 JSON。'''


def overview(snapshot, sources):
    units, _ = PF.inventory(snapshot, sources)
    objects = {(r['collection'], r['object_id']): r for r in snapshot if r['kind'] == 'object'}
    records = []
    for unit in sorted(units, key=lambda u: u['viewpoint_id']):
        vid = unit['viewpoint_id']
        cvr = objects['viewpoint_revisions', unit['current_revision_id']]['payload']
        records.append({'viewpoint_id': vid, 'viewpoint_revision_id': unit['current_revision_id'],
            'core_proposition': cvr['core_proposition'], 'scope': cvr['scope'],
            'proposition_signature': cvr.get('proposition_signature'),
            'identity_source_keys': unit['identity_source_keys'], 'identity_claim_count': len(unit['identity_claim_ids'])})
    return records


def validate(value, records):
    ids = {r['viewpoint_id'] for r in records}
    pairs = []
    seen = set()
    for pair in value['pairs']:
        left, right = pair['left_viewpoint_id'], pair['right_viewpoint_id']
        key = tuple(sorted((left, right)))
        if left == right or not {left, right}.issubset(ids) or key in seen:
            raise ValueError('duplicate/self/foreign master-data pair')
        seen.add(key)
        pairs.append({**pair, 'full_source_verified': False, 'identity_verdict': None,
                      'quality_defect_confirmed': False})
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('UUID-verified independent staging required')
    baseline = json.loads((root / 'baseline.json').read_text())
    snapshot = json.loads((root / 'snapshot.json').read_text())
    if PF.sha(snapshot) != baseline['snapshot_sha256']:
        raise ValueError('snapshot binding mismatch')
    records = overview(snapshot, baseline['sources'])
    if len(records) != baseline['counts']['active_viewpoints']:
        raise ValueError('full active master-data frame mismatch')
    url = PF.settings()['KNOWLEDGE_DATABASE_URL']
    def stable():
        if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('SQL/source drift')
    item = {'type': 'object', 'properties': {'left_viewpoint_id': {'type': 'string'}, 'right_viewpoint_id': {'type': 'string'},
        'candidate_kind': {'type': 'string', 'enum': ['possible_same_identity', 'related_distinct_control']},
        'reason': {'type': 'string'}}, 'required': ['left_viewpoint_id', 'right_viewpoint_id', 'candidate_kind', 'reason'], 'additionalProperties': False}
    schema = {'type': 'object', 'properties': {'pairs': {'type': 'array', 'items': item},
        'limitations': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['pairs', 'limitations'], 'additionalProperties': False}
    work = root / 'masterdata-candidate-discovery-v1'
    work.mkdir(exist_ok=True)
    with (work / 'allocation.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        number = 1
        while (work / f'attempt-{number:03}').exists():
            number += 1
        attempt = work / f'attempt-{number:03}'
        attempt.mkdir()
    cli, env = shutil.which('claude'), O.environment()
    if not cli:
        raise ValueError('Claude CLI unavailable')
    auth = subprocess.run([cli, 'auth', 'status'], env=env, capture_output=True, text=True, timeout=30)
    status = json.loads(auth.stdout or '{}')
    if auth.returncode or status.get('loggedIn') is not True or status.get('authMethod') != 'claude.ai':
        raise ValueError('active Claude subscription required')
    request = {'model': O.MODEL, 'effort': 'high', 'system_prompt': PROMPT, 'schema': schema,
        'user_prompt': json.dumps({'master_records': records}, ensure_ascii=False, separators=(',', ':')),
        'baseline_sha256': PF.sha(baseline), 'candidate_discovery_only': True, 'fixed_total_call_limit': None,
        'independent_from_original_generation': False, 'registry_writes': 0}
    PF.write_new(attempt / 'request.json', request)
    stable()
    command = [cli, '--print', '--safe-mode', '--disable-slash-commands', '--no-session-persistence',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--tools', '', '--permission-mode', 'dontAsk',
        '--model', O.MODEL, '--effort', 'high', '--system-prompt', PROMPT, '--output-format', 'json',
        '--json-schema', json.dumps(schema, ensure_ascii=False)]
    PF.write_new(attempt / 'intent.json', {'request_sha256': PF.sha(request), 'code_sha256': PF.digest(Path(__file__)),
        'maximum_cli_invocations_this_attempt': 1, 'automatic_retry': False})
    print(json.dumps({'state': 'invoking', 'active_master_records': len(records), 'model': O.MODEL, 'effort': 'high'}), flush=True)
    try:
        completed = subprocess.run(command, input=request['user_prompt'], env=env, cwd=attempt,
            capture_output=True, text=True, timeout=900)
    except Exception as error:
        PF.write_new(attempt / 'outcome-unknown.json', {'exception_type': type(error).__name__, 'automatic_retry': False})
        raise ValueError('candidate-discovery outcome unknown; retained') from None
    PF.write_new(attempt / 'provider-response.json', {'stdout': completed.stdout, 'stderr': completed.stderr, 'returncode': completed.returncode})
    wrapper = json.loads(completed.stdout)
    if completed.returncode or wrapper.get('is_error') or not isinstance(wrapper.get('structured_output'), dict):
        PF.write_new(attempt / 'no-complete-answer.json', {'state': 'no_complete_answer', 'is_error': wrapper.get('is_error')})
        print(json.dumps({'state': 'no_complete_answer', 'attempt_directory': str(attempt)}), flush=True)
        return
    result = wrapper['structured_output']
    pairs = validate(result, records)
    stable()
    PF.write_new(attempt / 'candidates.json', {'pairs': pairs, 'limitations': result['limitations'],
        'active_master_records': len(records), 'model_usage': wrapper.get('modelUsage'), 'num_turns': wrapper.get('num_turns'),
        'provider_response_sha256': PF.digest(attempt / 'provider-response.json'), 'registry_writes': 0})
    print(json.dumps({'state': 'answered', 'candidate_pairs': len(pairs), 'attempt_directory': str(attempt)}), flush=True)


if __name__ == '__main__':
    main()
