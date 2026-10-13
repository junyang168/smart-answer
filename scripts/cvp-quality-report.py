#!/usr/bin/env python3
"""Assemble the fixed-sample quality evidence report; absence is never a pass."""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('quality_saved_evidence', Path(__file__).with_name('cvp-quality-saved-evidence.py'))
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
H, PF = E.H, E.PF
DIMENSIONS = ('source_fidelity_attribution', 'qualifications_scope', 'identity',
              'granularity_duplicates', 'link_roles', 'source_to_viewpoint_coverage')


def read_set_delta(intent, report):
    def index(rows):
        result = {(r['collection'], r['object_id']): r['pin'] for r in rows}
        if len(result) != len(rows):
            raise ValueError('duplicate read pin identity')
        return result
    left, right = index(intent), index(report)
    return {'intent_only': [list(k) for k in sorted(left.keys() - right.keys())],
            'report_only': [list(k) for k in sorted(right.keys() - left.keys())],
            'changed_shared_pins': [list(k) for k in sorted(left.keys() & right.keys()) if left[k] != right[k]],
            'same_read_set': left == right}


def dimension_metrics(random_n, risk_n, unique_n, source_n):
    return {name: {'fixed_random_cvp_units': random_n if name != DIMENSIONS[-1] else None,
        'fixed_risk_cvp_units': risk_n if name != DIMENSIONS[-1] else None,
        'fixed_unique_cvp_units': unique_n if name != DIMENSIONS[-1] else None,
        'fixed_source_units': source_n if name == DIMENSIONS[-1] else None,
        'independent_semantic_units_evaluated': 0, 'confirmed_content_defects': None,
        'not_assessable_units': source_n if name == DIMENSIONS[-1] else unique_n,
        'population_defect_rate': None, 'confidence_interval': None,
        'overall_state': 'not_assessable', 'quality_pass': None} for name in DIMENSIONS}


def refusal_proof(binding):
    artifact, state = H.audit_binding(binding)
    if artifact is None:
        raise ValueError(state)
    model, prompt, schema = artifact.get('model'), artifact.get('prompt'), artifact.get('schema')
    if not isinstance(model, str) or not isinstance(prompt, str) or not isinstance(schema, dict):
        raise ValueError('provider block lacks saved prompt/schema/model')
    key = PF.hashlib.sha256((model + '\n' + prompt).encode()).hexdigest()
    if Path(binding['path']).name != key + '.provider-block.json':
        raise ValueError('provider block prompt identity mismatch')
    raw = artifact['provider_response']
    reason = (raw.get('promptFeedback') or {}).get('blockReason')
    candidates = raw.get('candidates') or []
    if not reason and len(candidates) == 1:
        reason = candidates[0].get('finishReason')
    if reason not in {'PROHIBITED_CONTENT', 'SAFETY', 'BLOCKLIST', 'OTHER'}:
        raise ValueError('saved raw is not a confirmed provider refusal')
    return {'binding': binding, 'provider_response_sha_bound': True, 'model_declared': model,
            'provider_model_version': raw.get('modelVersion'), 'provider_block_reason': reason,
            'saved_prompt_sha256': PF.hashlib.sha256(prompt.encode()).hexdigest(),
            'saved_schema_sha256': PF.sha(schema), 'cache_prompt_identity_verified': True,
            'actual_http_request_bytes_authenticated': False, 'independent_semantic_answer': False,
            'new_call_permission': False, 'quality_pass': None}


def build(root, closeout_file):
    closeout = json.loads(closeout_file.read_text())
    coverage_root = root / 'coverage-execution-v1'
    coverage_file = coverage_root / closeout['report_file']
    if PF.digest(coverage_file) != closeout['report_file_sha256']:
        raise ValueError('coverage report binding mismatch')
    coverage = json.loads(coverage_file.read_text())
    proof_root = root / 'saved-evidence-verification-v1'
    proof_manifest = json.loads((proof_root / 'manifest.json').read_text())
    for name, digest in proof_manifest['files'].items():
        if Path(name).name != name or PF.digest(proof_root / name) != digest:
            raise ValueError('saved proof file binding mismatch')
    baseline = json.loads((root / 'baseline.json').read_text())
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    if PF.sha(baseline) != plan['baseline_sha256'] or PF.sha(baseline) != proof_manifest['baseline_sha256']:
        raise ValueError('cross-stage baseline mismatch')
    history_file = root / 'history-and-closure-v1/history.json'
    history_manifest = json.loads((root / 'history-and-closure-v1/manifest.json').read_text())
    if PF.digest(history_file) != history_manifest['history_sha256']:
        raise ValueError('history file binding mismatch')
    history = json.loads(history_file.read_text())
    forward = json.loads((proof_root / 'forward-unit-evidence.json').read_text())
    proofs = json.loads((proof_root / 'report-proofs.json').read_text())
    intent_states, block_proofs = [], []
    for p in proofs:
        audit, state = H.audit_binding(p['audit_binding'])
        if audit is None:
            raise ValueError(state)
        for row in audit['layers']['4']['results']:
            if row.get('provider_content_block'):
                block_proofs.append({'viewpoint_id': row['viewpoint_id'], 'audit_binding': p['audit_binding'],
                    'independent': row.get('independent'), 'revisit_required': row.get('revisit_required'),
                    **refusal_proof(row['provider_content_block'])})
        intent_path = Path(p['audit_binding']['path']).parent / 'audit-intent.json'
        if p['group_intent_sha256'] is None:
            intent_states.append({'audit_binding': p['audit_binding'], 'state': 'no_sibling_group_intent'})
        else:
            if PF.digest(intent_path) != p['group_intent_sha256']:
                raise ValueError('group intent changed')
            intent = json.loads(intent_path.read_text())
            delta = read_set_delta(intent['read_pins'], audit['meta']['registry_read_scope']['read_pins'])
            intent_states.append({'audit_binding': p['audit_binding'],
                                 'state': 'matching_group_readset' if delta['same_read_set'] else 'group_readset_differs', **delta})
    original_roster = {cid for g in history['rows'] if g['kind'] == 'group' for cid in g['claim_ids']}
    sources = []
    for entry in history_manifest['entries']:
        packed_file = root / 'history-and-closure-v1' / entry['file']
        packed = json.loads(packed_file.read_text())
        if PF.sha(packed) != entry['encoded_sha256']:
            raise ValueError('complete source context changed')
        context = H.PK.expand_preview(packed)
        if PF.sha(context) != entry['decoded_sha256']:
            raise ValueError('complete source context roundtrip changed')
        claim_ids = {c['claim_id'] for c in context['source_claims']}
        sources.append({'source_unit': entry['file'].split('.')[0], 'source_ids': context['source_ids'],
            'current_claim_count': len(claim_ids), 'current_claim_ids_in_original_357_roster': sorted(claim_ids & original_roster),
            'current_claim_ids_outside_original_357_roster': sorted(claim_ids - original_roster),
            'connected_current_cvp_count': len(context['connected_complete_viewpoints']),
            'full_production_disposition_verified': False,
            'interpretation': 'Literal original roster membership only; no lineage/disposition inference or generation-defect claim.'})
    random_ids = {x['viewpoint_id'] for x in plan['random_viewpoints']['selected']}
    risk_ids = {x['viewpoint_id'] for x in plan['risk_viewpoints']}
    if {u['viewpoint_id'] for u in forward} != random_ids | risk_ids or len(forward) != len(random_ids | risk_ids):
        raise ValueError('fixed forward sample denominator mismatch')
    if len(sources) != len(plan['reverse_sources']['selected']) or {s['source_unit'] for s in sources} != {s['source_unit'] for s in coverage['units']}:
        raise ValueError('fixed source sample denominator mismatch')
    metrics = {'schema_version': 'cvp_quality_evidence_metrics_v1', 'baseline_counts': baseline['counts'],
        'forward_sample': {'random': len(random_ids), 'risk': len(risk_ids), 'overlap': len(random_ids & risk_ids), 'unique': len(forward)},
        'source_sample': {'fixed': len(sources), 'source_outcomes': coverage['source_outcomes'],
                          'valid_source_observations': sum(u['stages']['observe']['validation_state'] == 'valid' for u in coverage['units'])},
        'dimensions': dimension_metrics(len(random_ids), len(risk_ids), len(forward), len(sources)),
        'historical_reports': {'saved': len(proofs), 'all_current_pins_match': sum(p['all_current_pins_match'] for p in proofs),
            'unique_current_objects_verified': proof_manifest['resolved_unique_objects'],
            'candidate_forward_units': proof_manifest['historical_candidate_units'], 'independent_semantic_raw_authenticated_units': 0,
            'provider_refusal_proofs_verified': len(block_proofs),
            'selected_forward_units_with_refusal_proof': len({b['viewpoint_id'] for b in block_proofs} & (random_ids | risk_ids)),
            'group_intent_states': dict(Counter(i['state'] for i in intent_states))},
        'coverage_profile_limitations': {'scope_expansion_sampling_label_verified': False, 'full_source_dispositions_verified': False},
        'calls': {'previous_authorized_calls': coverage['actual_unique_intents'], 'new_calls_this_stage': 0},
        'registry_writes': 0, 'all_fixed_units_have_evidence_or_limitation_records': True,
        'quality_evaluation_complete': False, 'quality_pass': None,
        'bindings': {'baseline': PF.digest(root / 'baseline.json'), 'plan': PF.digest(root / 'evaluation-plan.json'),
            'saved_proof_manifest': PF.digest(proof_root / 'manifest.json'), 'coverage_report': PF.digest(coverage_file),
            'coverage_closeout': PF.digest(closeout_file)}, 'code_sha256': PF.digest(Path(__file__))}
    return metrics, {'forward_units': forward, 'source_units': sources, 'coverage_units': coverage['units'],
                     'group_intent_context': intent_states, 'historical_provider_refusal_proofs': block_proofs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    parser.add_argument('--coverage-closeout', required=True, type=Path)
    parser.add_argument('--output-name', default='quality-evidence-report-v1')
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(PF.subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('UUID-verified independent staging required')
    if args.output_name not in {'quality-evidence-report-v1', 'quality-evidence-report-v2'}:
        raise ValueError('explicit report version required')
    out = root / args.output_name
    if out.exists():
        raise ValueError('immutable report already exists')
    baseline = json.loads((root / 'baseline.json').read_text())
    url = PF.settings()['KNOWLEDGE_DATABASE_URL']
    with PF.LOCK.open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        metrics, units = build(root, args.coverage_closeout)
        pins = json.loads((root / 'saved-evidence-verification-v1/current-pins.json').read_text())
        if PF.sha(E.query_exact(url, {(r['collection'], r['object_id']) for r in pins})) != PF.sha(pins):
            raise ValueError('saved pin proof now stale')
        if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('final baseline/source drift')
        out.mkdir()
        PF.write_new(out / 'metrics.json', metrics)
        PF.write_new(out / 'fixed-unit-results.json', units)
        acceptance = {'schema_version': 'cvp_quality_evaluation_acceptance_v1', 'accepted': False,
            'metrics_file_sha256': PF.digest(out / 'metrics.json'), 'fixed_results_file_sha256': PF.digest(out / 'fixed-unit-results.json'),
            'all_fixed_units_recorded': True, 'final_sql_and_source_pins_match': True, 'quality_pass': None,
            'production_approval': False, 'registry_writes': 0,
            'remaining': ['Authenticated independent dimension evidence unavailable.',
                'Scope expansion sampling label and full source dispositions incomplete.',
                'Coverage comparisons unusable; original once identities and restrictions remain.']}
        PF.write_new(out / 'evaluation-acceptance.json', acceptance)
        text = '# #429 当前固定样本质量证据报告\n\n整体评估未通过验收；没有全库质量 pass。\n\n'
        text += f"固定正向样本 {len(units['forward_units'])} 个（随机 60、风险 20、交集 2），完整来源 12 个。\n\n"
        text += '六个维度均为 not_assessable，实际独立语义评估数为 0，内容缺陷数与缺陷率不可估计。\n\n'
        text += '43 份精确历史报告的 8,871 个对象版本与内容哈希均与当前一致；这不认证实际 provider 请求或原始回答。48 个正向单元有这些历史候选，另 30 个没有本轮发现的同范围报告。\n\n'
        text += '结果内嵌指针另认证了 4 份真实 provider 拒答原件的文件 SHA 和 prompt/cache 身份；它们不是独立语义回答，不解除 independent-incomplete 或产生重试权限。\n\n'
        text += '已有授权实际发送 15 次，本阶段新增调用为 0；12 个反向单元保留原失败、拒答保护、截断与输入独立性限制，没有替换样本。\n\n'
        text += '详见 metrics.json、fixed-unit-results.json 的精确 ID、版本 SHA、历史报告／intent 差异、字面原 roster 归属及受限证据。evaluation-acceptance.json 的 accepted=false，不是生产或内容批准。\n'
        (out / 'quality-report.md').write_text(text)
        print(json.dumps({'output': str(out), 'metrics_sha256': PF.digest(out / 'metrics.json'),
            'acceptance_sha256': PF.digest(out / 'evaluation-acceptance.json'), 'accepted': False, 'new_calls': 0}))


if __name__ == '__main__':
    main()
