#!/usr/bin/env python3
"""Freeze a reviewable proposal for new source-coverage questions; never calls HTTP."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

SPEC = importlib.util.spec_from_file_location('quality_history', Path(__file__).with_name('cvp-quality-history.py'))
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
PK, PF = H.PK, H.PF

MODEL = 'gemini-3.8-flash'
MAX_REQUEST_BYTES = 2500000
MAX_OUTPUT_TOKENS = 16384
MAX_OBSERVATION_BYTES = 131072
OBS_PROMPT = '''你是独立的原文覆盖评估者。只记录教授实际讲出的重要判断及限定，不判断其神学正确与否，不补他未回答的问题。
本阶段只读完整来源，不读已有 Claim/CVP、旧审核或生产结论。先逐段阅读完整正文，区分教授肯定、转述/反驳、编辑文字及未决。
列出重要且可复用的教学判断和必要限定，每条引用完整来源中未被删除的连续逐字文本及物理段序号；编辑标题/备注不能作证据。
这只是评估观察，不是新增 Claim。保持所有重要判断的覆盖；无法确认时明示 uncertain，不生成调和结论。返回完整 JSON。
材料使用可逆字段字典；keys 将别名映射为字段原名。physical_text 是完整原文，~~文字~~已被校对删除；live_text 可由此逐值重建。
'''
COVERAGE_PROMPT = '''你是独立的来源到观点覆盖评估者。只核对上阶段独立观察在当前知识库的明确去向及重要限定的承载，不裁决神学正确与否，不生成修订。
完整阅读实际 Claim、Evidence、当前 CVP 全命题、所有有效连接/身份成员和每个成员完整自身正文。判断观察是 covered/partial/not_recorded/uncertain/not_assessable，并给逐字位置和精确记录 ID。
没有 CVP 的来源或 Claim 不等于抽取失败；disposition 未核实时只报告 coverage gap/uncertain，不能宣称教授没有讲或生产错误。
旧身份等价、连接异议、人工保留及耗尽同题不得在这里重新仲裁。若覆盖疑点涉及这些问题，返回 requires_historical_context；不重新给该旧问题 pass/reject。
不以本源观察重判别篇自身语境；反方/编辑/删除线不算教授证据。材料使用可逆字段字典，完整 source physical_text 保持原样。
'''


def response_schema(stage):
    if stage == 'observe':
        item = {'type': 'OBJECT', 'properties': {
            'observation_id': {'type': 'STRING'}, 'judgment': {'type': 'STRING'},
            'qualifications': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
            'attribution': {'type': 'STRING', 'enum': ['professor_explicit', 'argument_conclusion', 'unresolved']},
            'citations': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
                'physical_ordinal': {'type': 'INTEGER'}, 'quote': {'type': 'STRING'}},
                'required': ['physical_ordinal', 'quote']}},
            'status': {'type': 'STRING', 'enum': ['observed', 'uncertain']}},
            'required': ['observation_id', 'judgment', 'qualifications', 'attribution', 'citations', 'status']}
        return {'type': 'OBJECT', 'properties': {'observations': {'type': 'ARRAY', 'items': item},
                'limitations': {'type': 'ARRAY', 'items': {'type': 'STRING'}}}, 'required': ['observations', 'limitations']}
    return {'type': 'OBJECT', 'properties': {'coverage': {'type': 'ARRAY', 'items': {'type': 'OBJECT',
        'properties': {'observation_id': {'type': 'STRING'},
        'status': {'type': 'STRING', 'enum': ['covered', 'partial', 'not_recorded', 'uncertain', 'not_assessable', 'requires_historical_context']},
        'record_ids': {'type': 'ARRAY', 'items': {'type': 'STRING'}}, 'reason': {'type': 'STRING'},
        'citations': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
            'source_id': {'type': 'STRING'}, 'physical_ordinal': {'type': 'INTEGER'}, 'quote': {'type': 'STRING'}},
            'required': ['source_id', 'physical_ordinal', 'quote']}}},
        'required': ['observation_id', 'status', 'record_ids', 'reason', 'citations']}},
        'limitations': {'type': 'ARRAY', 'items': {'type': 'STRING'}}}, 'required': ['coverage', 'limitations']}


def request(stage, material):
    prompt = OBS_PROMPT if stage == 'observe' else COVERAGE_PROMPT
    return {'contents': [{'role': 'user', 'parts': [{'text': prompt + '\n' + PF.encoded(material).decode()}]}],
            'generationConfig': {'temperature': 0, 'maxOutputTokens': MAX_OUTPUT_TOKENS,
                                 'responseMimeType': 'application/json', 'responseSchema': response_schema(stage)}}


def protection_paths(snapshot, sources, rows):
    protected_ids = set()
    for row in snapshot:
        if row['kind'] == 'campaign' and row['metadata'].get('sequence') in {35, 37, 120} and row['metadata'].get('revisit_required'):
            protected_ids.update(row['metadata']['claim_ids'])
    by_id = {r['object_id']: r for r in rows if r.get('collection') == 'claims'}
    aliases = {}
    for source in sources:
        for name in (source['source_id'], source.get('transcript_id')):
            if name:
                aliases.setdefault(name, set()).add(source.get('path'))
    paths = set()
    for cid in protected_ids:
        if cid not in by_id:
            raise ValueError('protected original Claim source unresolved; no call proposal')
        occurrences = by_id[cid]['payload'].get('occurrences') or []
        if not occurrences:
            raise ValueError('protected original Claim has no source')
        for occurrence in occurrences:
            name = occurrence.get('source_id') or occurrence.get('transcript_id')
            if name not in aliases or None in aliases[name]:
                raise ValueError('protected source unresolved; no call proposal')
            paths.update(aliases[name])
    return paths, protected_ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', type=Path, required=True)
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('required external SSD identity mismatch')
    out = root / 'coverage-call-proposal-v1'
    if out.exists():
        raise ValueError('immutable call proposal already exists')
    baseline = json.loads((root / 'baseline.json').read_text())
    snapshot = json.loads((root / 'snapshot.json').read_text())
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    manifest = json.loads((root / 'history-and-closure-v1/manifest.json').read_text())
    if PF.sha(snapshot) != baseline['snapshot_sha256'] or PF.sha(baseline) != plan['baseline_sha256']:
        raise ValueError('snapshot binding mismatch')
    if manifest['baseline_sha256'] != PF.sha(baseline) or manifest['plan_sha256'] != PF.sha(plan):
        raise ValueError('closure binding mismatch')
    url = PF.settings()['KNOWLEDGE_DATABASE_URL']
    if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
        raise ValueError('baseline drift')
    protected_ids = {cid for r in snapshot if r['kind'] == 'campaign'
                     and r['metadata'].get('sequence') in {35, 37, 120} and r['metadata'].get('revisit_required')
                     for cid in r['metadata']['claim_ids']}
    rows = PK.query_dependencies(url, protected_ids, [])
    protected_paths, _ = protection_paths(snapshot, baseline['sources'], rows)
    source_paths = {s['source_id']: s.get('path') for s in baseline['sources']}
    original_manifest = json.loads((root / 'packet-previews/manifest.json').read_text())
    original_entries = {e['file']: e for e in original_manifest['entries']}
    closure_entries = {e['file']: e for e in manifest['entries']}
    entries = []
    out.mkdir()
    for n, unit in enumerate(plan['reverse_sources']['selected'], 1):
        prefix = f'source-{n:02}'
        observe_name = prefix + '.observe.json'
        comparison_name = prefix + '.complete-comparison-preview.json'
        observation = json.loads((root / 'packet-previews' / observe_name).read_text())
        compact = json.loads((root / 'history-and-closure-v1' / comparison_name).read_text())
        if PF.sha(observation) != original_entries[observe_name]['material_sha256'] or PF.sha(compact) != closure_entries[comparison_name]['encoded_sha256']:
            raise ValueError('material preview binding mismatch')
        comparison = PK.expand_preview(compact)
        comparison['complete_target_source'] = observation['complete_source']
        own_protected = unit['path'] in protected_paths
        context_protected = any(source_paths[s['source_id']] in protected_paths for s in comparison['complete_other_member_sources'])
        status = 'restricted_historical_provider_block_source' if own_protected else 'proposed_pending_authorization'
        entry = {'source_unit': prefix, 'source_ids': unit['source_ids'], 'observe_status': status,
                 'comparison_status': 'restricted_historical_provider_block_context' if own_protected or context_protected else 'conditional_on_valid_independent_observations',
                 'maximum_observe_calls': 0 if own_protected else 1,
                 'maximum_comparison_calls': 0 if own_protected or context_protected else 1,
                 'historical_identity_retries': 0, 'maximum_observation_bytes': MAX_OBSERVATION_BYTES}
        packed_observation = PK.compact_preview(observation)
        body = PF.encoded(request('observe', packed_observation))
        if len(body) > MAX_REQUEST_BYTES:
            entry.update(observe_status='restricted_request_size', maximum_observe_calls=0, maximum_comparison_calls=0)
        if entry['maximum_observe_calls']:
            PF.write_new(out / (prefix + '.observe-request.json'), request('observe', packed_observation))
            entry.update(observe_request_bytes=len(body), observe_request_sha256=PF.hashlib.sha256(body).hexdigest())
        if entry['maximum_comparison_calls']:
            comparison['independent_observations'] = []
            template = request('compare', PK.compact_preview(comparison))
            template_bytes = len(PF.encoded(template))
            # Observation bytes can double when nested as escaped JSON text.
            worst_case = template_bytes + 2 * MAX_OBSERVATION_BYTES
            if worst_case > MAX_REQUEST_BYTES:
                entry.update(comparison_status='restricted_request_size', maximum_comparison_calls=0)
            else:
                PF.write_new(out / (prefix + '.comparison-template.json'), template)
                entry.update(comparison_template_bytes=template_bytes, comparison_request_maximum_bytes=worst_case,
                             comparison_context_sha256=PF.sha(comparison))
        entries.append(entry)
    if PF.sha(PK.query_dependencies(url, protected_ids, [])) != PF.sha(rows) or PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
        raise ValueError('final protected-source/baseline drift')
    proposal = {'schema_version': 'cvp_quality_coverage_call_proposal_v1', 'baseline_sha256': PF.sha(baseline),
        'sample_plan_sha256': PF.sha(plan), 'closure_manifest_sha256': PF.sha(manifest),
        'code_sha256': PF.digest(Path(__file__)), 'model': MODEL, 'backend': 'google_generative_language_rest',
        'endpoint_without_credentials': f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent',
        'temperature': 0, 'maximum_output_tokens_per_call': MAX_OUTPUT_TOKENS,
        'maximum_request_bytes_per_call': MAX_REQUEST_BYTES, 'automatic_retry': False,
        'parallelism': 1, 'maximum_calls_per_source_stage': 1, 'maximum_forward_calls': 0,
        'proposed_maximum_new_calls': sum(e['maximum_observe_calls'] + e['maximum_comparison_calls'] for e in entries),
        'authorized_new_calls': 0, 'entries': entries,
        'protected_source_policy': 'conservative source/context restriction for groups 35/37/120; no resampling or provider switch',
        'once_identity': 'baseline/source physical SHA/stage/prompt/schema/model/config; names and roots cannot reset budget',
        'recovery': 'save exact request before intent; saved raw consumed once; unknown intent never resent; rejection/no answer never replaced',
        'authorization_required': True, 'monetary_estimate': None,
        'quality_scope': 'new source-to-current-viewpoint coverage questions only; not a rerun of historical identity/link decisions',
        'runtime_prerequisites': ['programmatic validation of source citations and complete observation IDs',
             'read-only baseline/closure pins before each call', 'exact request bytes/intents/raw persistence',
             'final review of blindness and protected question masks', 'user approval of this bounded call plan'],
        'quality_evaluation_complete': False}
    PF.write_new(out / 'call-proposal.json', proposal)
    print(json.dumps({'output': str(out), 'maximum_new_calls_proposed': proposal['proposed_maximum_new_calls'],
        'observe_calls': sum(e['maximum_observe_calls'] for e in entries),
        'comparison_calls': sum(e['maximum_comparison_calls'] for e in entries),
        'restricted_source_units': [e['source_unit'] for e in entries if not e['maximum_observe_calls']],
        'authorized_new_calls': 0}))


if __name__ == '__main__':
    main()
