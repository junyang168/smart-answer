#!/usr/bin/env python3
"""Review cross-sermon master-data identity against complete physical sources."""
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
PF, H, PK = O.PF, O.H, O.PK
PROMPT = '''你评估 CanonicalViewpoint 作为 master data 的跨讲道观点识别能力，不判断教授神学对错，不补他没讲的观点。
逐篇阅读全文，按教授各篇自己的语境判断对象、谓词、条件、否定、因果、模态、人群、时段与范围。
检查同一 CVP 的身份成员是否真是同一观点；措辞不同不自动算不同，相似字句或同主题不自动算同一。
正面陈述、反方批评、口语省略和不同主语表面形式，可能是在教授自身语境中表达同一核心判断；不能仅凭「谓词字面不同」判不同。
但相互支持、前提与额外结论或改变适用条件的内容仍须区分。明确指出是表达方式不同，还是多出了一项独立教学判断。
equivalent_component 检查精确成分，不要求整条 Claim 都等价；supports/qualifies/extends/applies 等按所给 role 检查，不能冒充身份成员。
明确现有 canonical 命题有无丢失重要限定、过宽或拼入独立命题。编辑重述不是教授原话，但不因少字、排版或非连续摘引直接判语义错误。
如有 nearby_viewpoints，阅读全文及成员自己的来源，判断是不是重复 master record、漏归并，还是应保留的相似但不同观点。
身份归并需核心判断与真值条件相同；仅相容、因果支持、应用或更窄范围都不自动等价。不能用另一篇新增前提补救本篇不成立的等价。
nearby 的 same_master_identity 仅表示两个当前完整 canonical 命题身份等价；不能先改写或删除其中的条件才称为相同。若仅共享可定位的观点成分，完整命题还含独立内容或实质范围差别，使用 shared_component_distinct_records，说明共同成分及须保留的差异。
只给评估意见，不生成或执行修订，不覆盖历史人工方向；历史争议需后续上下文复核，模型异议不是自动批准。
每条 link_assessment 覆盖一个当前 link ID；每个 nearby CVP 都给 nearby_assessment。指出逐字位置和语义差异。
citations 必须含完整来源 source_id/physical_ordinal/quote；有引文差异时把引用完整性与语义判断分开。
材料有可逆 keys 字典，physical_text 为完整原文，~~文字~~为删除文字；编辑标题/被删内容/转述反方不能充作教授肯定。
source_identity_status 为 false 时，可以评估所提供文字的语义，但来源资格尚未确认；在 limitations 中披露，不能声称教授原始来源身份已验证。
reason 内引用措辞请用中文「」引号，返回完整、有效 JSON。'''


def schema():
    citation = O.schema()['properties']['observations']['items']['properties']['citations']
    def row(properties):
        properties = {**properties, 'reason': {'type': 'string'}, 'citations': citation}
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    props = {'canonical_fidelity': {'type': 'string', 'enum': ['faithful', 'over_generalized', 'unsupported', 'uncertain']},
        'canonical_reason': {'type': 'string'},
        'link_assessments': {'type': 'array', 'items': row({'viewpoint_claim_link_id': {'type': 'string'},
            'claim_id': {'type': 'string'}, 'role_status': {'type': 'string', 'enum': ['appropriate', 'misclassified', 'uncertain']},
            'identity_relation': {'type': 'string', 'enum': ['same_proposition', 'same_component', 'related_distinct', 'contradictory', 'not_identity_role', 'uncertain']}})},
        'cross_sermon_identity': {'type': 'string', 'enum': ['supported', 'over_merge', 'single_source', 'no_identity_members', 'uncertain']},
        'cross_sermon_reason': {'type': 'string'},
        'nearby_assessments': {'type': 'array', 'items': row({'viewpoint_id': {'type': 'string'},
            'relation': {'type': 'string', 'enum': ['same_master_identity', 'shared_component_distinct_records', 'related_but_distinct', 'unrelated', 'uncertain']}})},
        'limitations': {'type': 'array', 'items': {'type': 'string'}}}
    return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}


def validate(value, packet):
    links = {l['viewpoint_claim_link_id']: l['claim_id'] for l in packet['target']['links']}
    rows = value['link_assessments']
    if len(rows) != len(links) or {r['viewpoint_claim_link_id'] for r in rows} != set(links):
        raise ValueError('link denominator incomplete or foreign')
    if any(links[r['viewpoint_claim_link_id']] != r['claim_id'] for r in rows):
        raise ValueError('link/claim identity mismatch')
    neighbors = {v['viewpoint']['viewpoint_id'] for v in packet['nearby_viewpoints']}
    if len(value['nearby_assessments']) != len(neighbors) or {r['viewpoint_id'] for r in value['nearby_assessments']} != neighbors:
        raise ValueError('nearby CVP denominator incomplete or foreign')
    sources = {s['source_id']: s for v in [packet['target']] + packet['nearby_viewpoints'] for s in v['complete_own_sources']}
    bindings = []
    for row in rows + value['nearby_assessments']:
        citations = []
        for c in row['citations']:
            if c['source_id'] not in sources:
                raise ValueError('foreign citation source')
            citations.append(O.R.CB.observation_evidence({'observation_id': 'masterdata', 'citations': [c]}, sources[c['source_id']]))
        bindings.append({'record_id': row.get('viewpoint_claim_link_id', row.get('viewpoint_id')),
            'citation_states': citations, 'semantic_judgment_not_decided_by_literal_match': True})
    return bindings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    parser.add_argument('--viewpoint', required=True)
    parser.add_argument('--neighbor', action='append', default=[])
    parser.add_argument('--material-dir', type=Path)
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('UUID-verified independent staging required')
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    selected = {v['viewpoint_id'] for v in plan['random_viewpoints']['selected'] + plan['risk_viewpoints']}
    material_root = root / 'packet-previews'
    if args.material_dir is None:
        if args.viewpoint not in selected or not set(args.neighbor).issubset(selected - {args.viewpoint}):
            raise ValueError('explicit current fixed-sample targets required')
        manifest = json.loads((material_root / 'manifest.json').read_text())
    else:
        material_root = args.material_dir.resolve()
        if material_root != root / 'masterdata-extended-material-v1':
            raise ValueError('exact expanded candidate material required')
        manifest = json.loads((material_root / 'manifest.json').read_text())
        baseline_value = json.loads((root / 'baseline.json').read_text())
        if manifest['baseline_sha256'] != PF.sha(baseline_value):
            raise ValueError('expanded candidate baseline differs')
        candidate_file = Path(manifest['candidate_file'])
        if not candidate_file.resolve().is_relative_to(root / 'masterdata-candidate-discovery-v1') or PF.digest(candidate_file) != manifest['candidate_file_sha256']:
            raise ValueError('candidate nomination binding changed')
        nominations = json.loads(candidate_file.read_text())['pairs']
        pairs = {tuple(sorted((p['left_viewpoint_id'], p['right_viewpoint_id']))) for p in nominations}
        if not args.neighbor or any(tuple(sorted((args.viewpoint, n))) not in pairs for n in args.neighbor):
            raise ValueError('pair lacks exact saved nomination')
    if args.viewpoint in args.neighbor or len(args.neighbor) != len(set(args.neighbor)):
        raise ValueError('self/duplicate neighbor')
    by_name = {e['file']: e for e in manifest['entries']}
    def read(vid):
        name = vid + '.forward.json'
        value = json.loads((material_root / name).read_text())
        if PF.sha(value) != by_name[name]['material_sha256']:
            raise ValueError('full source packet changed')
        return H.blind(value)
    packet = {'target': read(args.viewpoint), 'nearby_viewpoints': [read(v) for v in args.neighbor]}
    if H.blind_leaks(packet):
        raise ValueError('old adjudication metadata leaked')
    baseline = json.loads((root / 'baseline.json').read_text())
    source_metadata = {s['source_id']: s for s in baseline['sources']}
    for v in [packet['target']] + packet['nearby_viewpoints']:
        v['source_identity_status'] = [{'source_id': s['source_id'],
            'eligible_registered_original': source_metadata[s['source_id']]['eligible'],
            'reason': source_metadata[s['source_id']]['eligibility_reason']}
            for s in v['complete_own_sources']]
    url = PF.settings()['KNOWLEDGE_DATABASE_URL']
    def stable():
        if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('current SQL/source drift')
        claim_ids = {c['claim_id'] for v in [packet['target']] + packet['nearby_viewpoints'] for c in v['claims']}
        link_values = [l for v in [packet['target']] + packet['nearby_viewpoints'] for l in v['links']]
        current_dependencies = {(r['collection'], r['object_id']): H.blind(r['payload'])
                                for r in PK.query_dependencies(url, claim_ids, link_values)}
        for v in [packet['target']] + packet['nearby_viewpoints']:
            for field, collection, id_key in (('claims', 'claims', 'claim_id'), ('evidence', 'evidence_steps', 'evidence_step_id'),
                                               ('fragments', 'source_fragments', 'fragment_id')):
                for expected in v[field]:
                    if current_dependencies.get((collection, expected[id_key])) != H.blind(expected):
                        raise ValueError('complete member dependency changed')
    cli, env = shutil.which('claude'), O.environment()
    if not cli:
        raise ValueError('Claude CLI unavailable')
    auth = subprocess.run([cli, 'auth', 'status'], env=env, capture_output=True, text=True, timeout=30)
    status = json.loads(auth.stdout or '{}')
    if auth.returncode or status.get('loggedIn') is not True or status.get('authMethod') != 'claude.ai':
        raise ValueError('active Claude subscription required')
    work = root / 'masterdata-opus-evaluation-v1'
    work.mkdir(exist_ok=True)
    with (work / 'allocation.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        number = 1
        while (work / f'{args.viewpoint}-attempt-{number:03}').exists():
            number += 1
        attempt = work / f'{args.viewpoint}-attempt-{number:03}'
        attempt.mkdir()
    request = {'model': O.MODEL, 'effort': 'high', 'system_prompt': PROMPT, 'schema': schema(),
        'user_prompt': json.dumps(PK.compact_preview(packet), ensure_ascii=False, separators=(',', ':')),
        'baseline_sha256': PF.sha(baseline), 'viewpoint_id': args.viewpoint, 'neighbor_ids': args.neighbor,
        'material_manifest_sha256': PF.digest(material_root / 'manifest.json'),
        'fixed_total_call_limit': None, 'independent_from_original_generation': False,
        'authorization': {'model': '可以用 Claude Opus 5.5 high. 最新的，很强', 'count': '调用大模型次数不应受限',
            'focus': '这是 master data 啊'}, 'registry_writes': 0}
    PF.write_new(attempt / 'request.json', request)
    stable()
    command = [cli, '--print', '--safe-mode', '--disable-slash-commands', '--no-session-persistence',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--tools', '', '--permission-mode', 'dontAsk',
        '--model', O.MODEL, '--effort', 'high', '--system-prompt', PROMPT, '--output-format', 'json',
        '--json-schema', json.dumps(schema(), ensure_ascii=False)]
    PF.write_new(attempt / 'intent.json', {'request_sha256': PF.sha(request), 'cli_sha256': PF.digest(Path(cli)),
        'maximum_cli_invocations_this_attempt': 1, 'automatic_retry': False, 'code_sha256': PF.digest(Path(__file__))})
    print(json.dumps({'viewpoint': args.viewpoint, 'neighbors': args.neighbor, 'state': 'invoking', 'model': O.MODEL, 'effort': 'high'}), flush=True)
    try:
        completed = subprocess.run(command, input=request['user_prompt'], env=env, cwd=attempt,
            capture_output=True, text=True, timeout=900)
    except Exception as error:
        PF.write_new(attempt / 'outcome-unknown.json', {'exception_type': type(error).__name__, 'automatic_retry': False})
        raise ValueError('Claude outcome unknown; saved attempt retained') from None
    PF.write_new(attempt / 'provider-response.json', {'stdout': completed.stdout, 'stderr': completed.stderr, 'returncode': completed.returncode})
    wrapper = json.loads(completed.stdout)
    if completed.returncode or wrapper.get('is_error') or not isinstance(wrapper.get('structured_output'), dict):
        PF.write_new(attempt / 'no-complete-answer.json', {'state': 'no_complete_answer', 'is_error': wrapper.get('is_error')})
        print(json.dumps({'viewpoint': args.viewpoint, 'state': 'no_complete_answer', 'attempt_directory': str(attempt)}), flush=True)
        return
    value = wrapper['structured_output']
    bindings = validate(value, packet)
    stable()
    PF.write_new(attempt / 'assessment.json', {'model_requested': O.MODEL, 'effort': 'high', 'model_usage': wrapper.get('modelUsage'),
        'num_turns': wrapper.get('num_turns'), 'result': value, 'citation_bindings': bindings,
        'independent_from_original_generation': False, 'provider_response_sha256': PF.digest(attempt / 'provider-response.json'),
        'registry_writes': 0})
    print(json.dumps({'viewpoint': args.viewpoint, 'state': 'answered', 'cross_sermon_identity': value['cross_sermon_identity'],
        'canonical_fidelity': value['canonical_fidelity'], 'attempt_directory': str(attempt)}), flush=True)


if __name__ == '__main__':
    main()
