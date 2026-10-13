#!/usr/bin/env python3
"""Summarize authenticated master-data opinions without turning them into approvals."""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('quality_masterdata', Path(__file__).with_name('cvp-quality-masterdata-review.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
PF = M.PF


def authenticate(attempt):
    request = json.loads((attempt / 'request.json').read_text())
    intent = json.loads((attempt / 'intent.json').read_text())
    assessment = json.loads((attempt / 'assessment.json').read_text())
    raw_file = attempt / 'provider-response.json'
    raw = json.loads(raw_file.read_text())
    wrapper = json.loads(raw['stdout'])
    if (intent['request_sha256'] != PF.sha(request)
            or assessment['provider_response_sha256'] != PF.digest(raw_file)
            or raw['returncode'] or wrapper.get('is_error')
            or wrapper.get('structured_output') != assessment['result']
            or request['model'] != M.O.MODEL or request['effort'] != 'high'
            or M.O.MODEL not in (wrapper.get('modelUsage') or {})
            or assessment['model_usage'] != wrapper['modelUsage']):
        raise ValueError('request/provider/model/result binding failed')
    return {'attempt': str(attempt), 'assessment_sha256': PF.digest(attempt / 'assessment.json'),
            'request_sha256': PF.digest(attempt / 'request.json'),
            'provider_response_sha256': PF.digest(raw_file),
            'protocol': {'prompt_sha256': PF.sha(request['system_prompt']),
                         'schema_sha256': PF.sha(request['schema']), 'code_sha256': intent['code_sha256']},
            'result': assessment['result'], 'citation_bindings': assessment['citation_bindings'],
            'viewpoint_id': request['viewpoint_id'], 'neighbor_ids': request['neighbor_ids'],
            'num_turns': wrapper.get('num_turns'), 'independent_from_original_generation': False,
            'opinion_is_confirmed_defect': False, 'registry_writes': 0}


def aggregate(ids, opinions):
    results = [opinions[v][-1]['result'] for v in ids if v in opinions]
    return {'fixed_denominator': len(ids), 'complete_opinions': len(results),
            'without_complete_opinion': sorted(set(ids) - opinions.keys()),
            'canonical_fidelity_opinions': dict(Counter(r['canonical_fidelity'] for r in results)),
            'cross_sermon_identity_opinions': dict(Counter(r['cross_sermon_identity'] for r in results)),
            'link_role_opinions': dict(Counter(l['role_status'] for r in results for l in r['link_assessments'])),
            'confirmed_defect_count': None, 'population_accuracy': None, 'population_recall': None}


def build(root):
    baseline = json.loads((root / 'baseline.json').read_text())
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    random = [v['viewpoint_id'] for v in plan['random_viewpoints']['selected']]
    risk = [v['viewpoint_id'] for v in plan['risk_viewpoints']]
    fixed = set(random + risk)
    manifest = json.loads((root / 'masterdata-extended-material-v1/manifest.json').read_text())
    nominated_file = Path(manifest['candidate_file'])
    if PF.digest(nominated_file) != manifest['candidate_file_sha256']:
        raise ValueError('nomination binding changed')
    nominations = json.loads(nominated_file.read_text())['pairs']
    materials = {e['file'].removesuffix('.forward.json') for e in manifest['entries']}
    single, paired, all_attempts = {}, {}, []
    for attempt in sorted((root / 'masterdata-opus-evaluation-v1').glob('CV-*-attempt-*')):
        if not attempt.is_dir():
            continue
        if not (attempt / 'assessment.json').exists():
            all_attempts.append({'attempt': str(attempt), 'state': 'without_complete_authenticated_opinion',
                                 'retained': True})
            continue
        value = authenticate(attempt)
        all_attempts.append(value)
        if value['neighbor_ids']:
            key = (value['viewpoint_id'], tuple(value['neighbor_ids']))
            paired.setdefault(key, []).append(value)
        elif value['viewpoint_id'] in fixed:
            single.setdefault(value['viewpoint_id'], []).append(value)
    pair_rows = []
    for pair in nominations:
        left, right = pair['left_viewpoint_id'], pair['right_viewpoint_id']
        attempts = paired.get((left, (right,)), [])
        state = ('source_provenance_not_assessable' if not {left, right}.issubset(materials)
                 else 'complete_model_opinion' if attempts else 'without_complete_opinion')
        pair_rows.append({'nomination': pair, 'state': state, 'retained_in_denominator': True,
                          'attempts': attempts,
                          'latest_relation_opinion': attempts[-1]['result']['nearby_assessments'][0]['relation'] if attempts else None,
                          'confirmed_duplicate': None, 'merge_approved': False})
    source_by_id = {s['source_id']: s for s in baseline['sources']}
    source_qualification = []
    snapshot = json.loads((root / 'snapshot.json').read_text())
    objects = {(r['collection'], r['object_id']): r['payload'] for r in snapshot if r['kind'] == 'object'}
    history_file = root / 'history-and-closure-v1/history.json'
    history = json.loads(history_file.read_text())
    decisions = {r['object_id']: r['payload'] for r in history['rows'] if r.get('collection') == 'viewpoint_identity_decisions'}
    historical_context = []
    for vid in sorted(fixed):
        material = json.loads((root / 'packet-previews' / (vid + '.forward.json')).read_text())
        sources = material['complete_own_sources']
        unqualified = [s['source_id'] for s in sources if not source_by_id[s['source_id']]['eligible']]
        source_qualification.append({'viewpoint_id': vid, 'unqualified_source_ids': unqualified,
                                     'all_registered_original_sources_eligible': not unqualified})
        revision = objects['viewpoint_revisions', material['viewpoint']['viewpoint_revision_id']]
        basis = revision.get('provenance', {}).get('basis_identity_decision_ids', [])
        old_ids = {link['claim_id'] for d in basis if d in decisions
                   for link in decisions[d].get('claim_link_decisions', [])
                   if link.get('link_type') in PF.MEMBERS}
        current_ids = {link['claim_id'] for link in material['links'] if link['link_type'] in PF.MEMBERS}
        historical_context.append({'viewpoint_id': vid, 'basis_decision_ids': basis,
                                   'basis_member_claims_not_current_identity_members': sorted(old_ids - current_ids),
                                   'original_basis_content_semantically_reassessed': False,
                                   'current_anchor_gap_is_not_proof_professor_never_taught_it': True})
    return {'format': 'cvp-masterdata-supplemental-assessment.v1', 'baseline_sha256': PF.sha(baseline),
            'model': M.O.MODEL, 'effort': 'high', 'fixed_total_call_limit': None,
            'independent_from_original_generation': False, 'registry_writes': 0,
            'fixed_sample': aggregate(sorted(fixed), single),
            'random_sample': aggregate(random, single), 'risk_sample': aggregate(risk, single),
            'fixed_sample_source_qualification': source_qualification,
            'historical_context_binding': {'file': str(history_file), 'sha256': PF.digest(history_file),
                                            'records': historical_context},
            'candidate_pairs': {'fixed_denominator': len(pair_rows), 'records': pair_rows,
                                'states': dict(Counter(r['state'] for r in pair_rows)),
                                'latest_relation_opinions': dict(Counter(r['latest_relation_opinion'] for r in pair_rows if r['latest_relation_opinion'])),
                                'not_a_population_recall_estimate': True},
            'all_attempts': all_attempts, 'quality_accepted': False,
            'limitations': ['Model opinions require source-context and historical-direction adjudication.',
                            'Original generation used Claude; these opinions are supplemental, not independent.',
                            'Shared components are not automatic equivalence of complete master records.',
                            'Citation integrity annotations do not determine semantic correctness.',
                            'Candidate nominations are not an exhaustive census of missed identities.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    args = parser.parse_args()
    root = args.preflight.resolve()
    if not root.is_relative_to((PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429').resolve()):
        raise ValueError('independent staging required')
    report = build(root)
    path = root / 'masterdata-opus-evaluation-v1' / ('report-' + PF.sha(report)[:20] + '.json')
    PF.write_new(path, report)
    print(json.dumps({'report': str(path), 'fixed_sample': report['fixed_sample'],
                      'pairs': {k: v for k, v in report['candidate_pairs'].items() if k != 'records'}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
