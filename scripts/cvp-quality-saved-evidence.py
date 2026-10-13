#!/usr/bin/env python3
"""Verify exact historical audit read sets; never execute an old audit or model."""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess

SPEC = importlib.util.spec_from_file_location('quality_history', Path(__file__).with_name('cvp-quality-history.py'))
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
PF = H.PF


def query_exact(url, keys):
    if not keys:
        return []
    def literal(value):
        return "'" + value.replace("'", "''") + "'"
    pairs = ','.join(f'({literal(c)},{literal(i)})' for c, i in sorted(keys))
    sql = f"""BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='60s';
WITH requested(collection,object_id) AS (VALUES {pairs})
SELECT json_build_object('kind','object','collection',o.collection,'object_id',o.object_id,
 'revision',o.revision,'content_sha256',o.content_sha256,'retired',o.retired_at IS NOT NULL,'payload',o.payload)
FROM wang_knowledge.objects o JOIN requested r USING(collection,object_id)
ORDER BY o.collection,o.object_id;
COMMIT;
"""
    result = subprocess.run(['psql', url, '-XAtq', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                            input=sql, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise ValueError('exact historical pin query failed')
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    PF.verify_content_pins(rows)
    return [{k: v for k, v in row.items() if k != 'payload'} for row in rows]


def compare_pins(pins, current):
    differences = []
    for row in pins:
        key = (row['collection'], row['object_id'])
        now = current.get(key)
        actual = None if now is None else [now['revision'], now['content_sha256'], now['retired']]
        if actual != row['pin']:
            differences.append({'collection': key[0], 'object_id': key[1], 'historical_pin': row['pin'],
                                'current_pin': actual, 'state': 'missing_current_object' if now is None else 'current_pin_differs'})
    return {'read_pin_count': len(pins), 'differences': differences,
            'all_current_pins_match': bool(pins) and not differences,
            'provider_raw_authenticated': False, 'fresh_blind_evidence': False,
            'new_calls_authorized': False, 'six_dimension_pass': None}


def audit_inputs(history):
    reports = {}
    for candidate in history['collision_candidates']:
        binding = candidate['audit_binding']
        report, state = H.audit_binding(binding)
        if report is None:
            raise ValueError(f'exact historical report unavailable: {state}')
        path = binding['path']
        if path in reports and reports[path]['binding'] != binding:
            raise ValueError('conflicting saved report bindings')
        reports[path] = {'binding': binding, 'audit': report}
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('UUID-verified independent staging required')
    out = root / 'saved-evidence-verification-v1'
    if out.exists():
        raise ValueError('immutable proof already exists')
    baseline = json.loads((root / 'baseline.json').read_text())
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    history = json.loads((root / 'history-and-closure-v1/history.json').read_text())
    manifest = json.loads((root / 'history-and-closure-v1/manifest.json').read_text())
    if manifest['history_sha256'] != PF.digest(root / 'history-and-closure-v1/history.json') or PF.sha(baseline) != plan['baseline_sha256']:
        raise ValueError('parent evidence binding mismatch')
    reports = audit_inputs(history)
    keys = {(p['collection'], p['object_id']) for r in reports.values()
            for p in r['audit']['meta']['registry_read_scope']['read_pins']}
    url = PF.settings()['KNOWLEDGE_DATABASE_URL']
    with PF.LOCK.open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('baseline/source drift')
        rows = query_exact(url, keys)
        current = {(r['collection'], r['object_id']): r for r in rows}
        proofs = []
        for path, entry in sorted(reports.items()):
            audit = entry['audit']
            scope = audit['meta']['registry_read_scope']
            intent_path = Path(path).parent / 'audit-intent.json'
            intent = json.loads(intent_path.read_text()) if intent_path.is_file() else None
            checks = compare_pins(scope['read_pins'], current)
            proofs.append({'audit_binding': entry['binding'], **checks,
                'model_declared': audit['meta']['model'], 'complete_target_members_declared': scope.get('complete_target_members'),
                'group_intent_sha256': PF.digest(intent_path) if intent is not None else None,
                'group_intent_pins_match_report': intent is not None and intent.get('read_pins') == scope['read_pins'],
                'group_intent_is_provider_request': False,
                'provider_proof_state': 'report_and_group_intent_only; no_explicit_provider_request_or_raw_binding',
                'member_result_count': len(audit['layers']['4']['results'])})
        by_path = {r['audit_binding']['path']: r for r in proofs}
        candidates = []
        for c in history['collision_candidates']:
            p = by_path[c['audit_binding']['path']]
            candidates.append({'viewpoint_id': c['viewpoint_id'], 'sequence': c['sequence'],
                'audit_binding': c['audit_binding'], 'all_current_pins_match': p['all_current_pins_match'],
                'complete_target_members_declared': p['complete_target_members_declared'],
                'provider_raw_authenticated': False, 'six_dimension_pass': None})
        random_ids = {u['viewpoint_id'] for u in plan['random_viewpoints']['selected']}
        risk_ids = {u['viewpoint_id'] for u in plan['risk_viewpoints']}
        profiles = {u['viewpoint_id']: u for u in history['profiles']}
        units = [{'viewpoint_id': vid, 'selection_labels': [label for label, ids in [('random', random_ids), ('risk', risk_ids)] if vid in ids],
            'historical_candidates': [c for c in candidates if c['viewpoint_id'] == vid], 'profile': profiles[vid],
            'forward_dimension_states': {name: {'state': 'not_assessable', 'reason': 'no_authenticated_complete_independent_dimension_evidence',
                'historical_report_is_quality_pass': False} for name in ('source_fidelity_attribution', 'qualifications_scope',
                'identity', 'granularity_duplicates', 'link_roles')}, 'new_model_calls': 0}
            for vid in sorted(random_ids | risk_ids)]
        if PF.sha(rows) != PF.sha(query_exact(url, keys)) or PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('final historical/current/source drift')
        out.mkdir()
        PF.write_new(out / 'current-pins.json', rows)
        PF.write_new(out / 'report-proofs.json', proofs)
        PF.write_new(out / 'forward-unit-evidence.json', units)
        result = {'schema_version': 'cvp_quality_saved_evidence_verification_v1', 'baseline_sha256': PF.sha(baseline),
            'requested_unique_objects': len(keys), 'resolved_unique_objects': len(rows), 'saved_reports': len(proofs),
            'reports_all_current_pins_match': sum(p['all_current_pins_match'] for p in proofs),
            'reports_with_current_pin_differences': sum(bool(p['differences']) for p in proofs),
            'historical_candidate_units': len({c['viewpoint_id'] for c in candidates}),
            'candidate_units_with_one_matching_full_readset': len({c['viewpoint_id'] for c in candidates if c['all_current_pins_match'] and c['complete_target_members_declared']}),
            'fixed_forward_units': len(units), 'forward_dimensions_actually_evaluated': 0,
            'provider_raw_authenticated_units': 0, 'new_model_calls': 0, 'registry_writes': 0,
            'final_sql_and_sources_match_baseline': True, 'whole_evaluation_accepted': False,
            'selection_counts': {'random': len(random_ids), 'risk': len(risk_ids), 'overlap': len(random_ids & risk_ids)},
            'difference_collections': dict(Counter(d['collection'] for p in proofs for d in p['differences'])),
            'files': {n: PF.digest(out / n) for n in ('current-pins.json', 'report-proofs.json', 'forward-unit-evidence.json')},
            'code_sha256': PF.digest(Path(__file__))}
        PF.write_new(out / 'manifest.json', result)
        print(json.dumps({'output': str(out), **result}))


if __name__ == '__main__':
    main()
