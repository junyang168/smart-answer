#!/usr/bin/env python3
"""Read exact CVP history references and complete reverse member closures.

Produces zero-call preparation evidence. A saved audit report is a collision
warning, not authentication of provider raw or a new six-dimension verdict.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import subprocess

SPEC = importlib.util.spec_from_file_location('quality_packets', Path(__file__).with_name('cvp-quality-packets.py'))
PK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PK)
PF = PK.PF
CAMPAIGN = 'a155c59979a3a1fe65fccd3368c9abca8ea0f1d4238b1624f4a7cfdbdc522ced'
OLD_STAGING = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging')
FORBIDDEN = {'review_status', 'review_note', 'reviewed_by', 'reviewed_at', 'approved_by', 'approved_at',
             'provenance', 'review_provenance', 'metrics', 'decision_id', 'approval_basis',
             'review_artifact_sha256', 'rejected_highlights', 'reviewer_model_ids',
             'ai_adjudication', 'approval_status'}


def blind_leaks(value, path='$'):
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f'{path}.{key}'
            if key in FORBIDDEN and item is not None:
                result.append(child)
            result.extend(blind_leaks(item, child))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            result.extend(blind_leaks(item, f'{path}[{i}]'))
    return result


def blind(value):
    if isinstance(value, dict):
        return {key: blind(item) for key, item in value.items() if key not in FORBIDDEN}
    if isinstance(value, list):
        return [blind(item) for item in value]
    return value


def audit_binding(binding):
    if not isinstance(binding, dict) or not binding.get('path') or not binding.get('file_sha256'):
        return None, 'missing_exact_audit_binding'
    path = Path(binding['path'])
    if not path.resolve().is_relative_to(OLD_STAGING.resolve()):
        return None, 'outside_old_staging'
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        return None, 'missing_or_oversized_audit'
    if PF.digest(path) != binding['file_sha256']:
        return None, 'audit_file_sha_mismatch'
    return json.loads(path.read_text()), 'saved_report_file_verified'


def match_audit(viewpoint_id, audit, current_objects):
    scope = audit.get('meta', {}).get('registry_read_scope', {})
    results = [r for r in audit.get('layers', {}).get('4', {}).get('results', [])
               if r.get('viewpoint_id') == viewpoint_id]
    pins = scope.get('read_pins') or []
    differences = []
    for row in pins:
        key = (row.get('collection'), row.get('object_id'))
        current = current_objects.get(key)
        if current is None:
            differences.append({'key': list(key), 'state': 'not_in_current_preparation_read_set'})
        elif row.get('pin') != [current['revision'], current['content_sha256'], current['retired']]:
            differences.append({'key': list(key), 'state': 'current_pin_differs'})
    return {'result_count': len(results), 'saved_verdicts': [r.get('verdict') for r in results],
            'revision_ids': [r.get('revision_id') for r in results],
            'historical_complete_members_declared': scope.get('complete_target_members'),
            'pin_count': len(pins), 'pin_differences': differences,
            'same_complete_current_read_set': bool(results and pins and not differences and scope.get('complete_target_members')),
            'provider_raw_authenticated': False, 'fresh_blind_evidence': False,
            'new_forward_calls_allowed': False}


def query_history(url, decision_ids, object_ids):
    def values(ids):
        return ','.join("'" + str(i).replace("'", "''") + "'" for i in sorted(ids) if i) or 'NULL'
    sql = f"""
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='60s';
SELECT json_build_object('kind','object','collection',collection,'object_id',object_id,
 'revision',revision,'content_sha256',content_sha256,'retired',retired_at IS NOT NULL,'payload',payload)
FROM wang_knowledge.objects WHERE collection='viewpoint_identity_decisions' AND object_id IN ({values(decision_ids)}) ORDER BY object_id;
SELECT json_build_object('kind','review_event','review_event_id',review_event_id,
 'collection',collection,'object_id',object_id,'object_revision',object_revision,
 'reviewer_kind',reviewer_kind,'reviewer_id',reviewer_id,'decision',decision,'artifact',artifact)
FROM wang_knowledge.review_events WHERE object_id IN ({values(object_ids)}) ORDER BY review_event_id;
SELECT json_build_object('kind','group','subject_id',subject_id,'status',status,
 'sequence',metadata->'sequence','claim_ids',metadata->'claim_ids','execution_roots',metadata->'execution_roots',
 'audit',metadata->'completion'->'audit','owner_resolution_path',metadata->'completion'->>'owner_resolution_path',
 'independent_audit_complete',metadata->'independent_audit_complete','revisit_required',metadata->'revisit_required')
FROM wang_knowledge.pipeline_runs WHERE stage='cvp_group' AND metadata->>'campaign_sha256'='{CAMPAIGN}'
 AND metadata ? 'sequence' ORDER BY (metadata->>'sequence')::integer;
COMMIT;
"""
    result = subprocess.run(['psql', url, '-XAtq', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                            input=sql, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise ValueError('read-only exact history query failed')
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    PF.verify_content_pins([r for r in rows if r['kind'] == 'object'])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', type=Path, required=True)
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('independent UUID-verified SSD staging required')
    out = root / 'history-and-closure-v1'
    if out.exists():
        raise ValueError('immutable preparation already exists')
    baseline = json.loads((root / 'baseline.json').read_text())
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    snapshot = json.loads((root / 'snapshot.json').read_text())
    if PF.sha(snapshot) != baseline['snapshot_sha256'] or PF.sha(baseline) != plan['baseline_sha256']:
        raise ValueError('baseline binding mismatch')
    url = PF.settings()['KNOWLEDGE_DATABASE_URL']
    if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
        raise ValueError('current baseline drift')
    objects = {(r['collection'], r['object_id']): r for r in snapshot if r['kind'] == 'object'}
    forward_ids = {u['viewpoint_id'] for u in plan['random_viewpoints']['selected'] + plan['risk_viewpoints']}
    reverse_contexts = []
    parent_manifest = json.loads((root / 'packet-previews/manifest.json').read_text())
    reverse_ids = set()
    reverse_claim_ids = set()
    for entry in parent_manifest['entries']:
        if entry['file'].endswith('.comparison-context.json'):
            context = json.loads((root / 'packet-previews' / entry['file']).read_text())
            if PF.sha(context) != entry['material_sha256']:
                raise ValueError('reverse context binding drift')
            reverse_contexts.append((entry['file'], context))
            reverse_ids.update(context['connected_viewpoint_ids'])
            reverse_claim_ids.update(r['object_id'] for r in context['claims'])
    target_ids = forward_ids | reverse_ids
    links = [r for (c, _), r in objects.items() if c == 'viewpoint_claim_links'
             and r['payload'].get('viewpoint_id') in target_ids and r['payload'].get('effective_state') == 'active']
    claim_ids = reverse_claim_ids | {r['payload']['claim_id'] for r in links}
    deps = PK.query_dependencies(url, claim_ids, [r['payload'] for r in links])
    for r in deps:
        key = (r['collection'], r['object_id'])
        old = objects.get(key)
        if old and [r['revision'], r['content_sha256'], r['retired']] != [old['revision'], old['content_sha256'], old['retired']]:
            raise ValueError('dependency drift')
        objects[key] = r
    decisions = {r['payload'].get('decision_id') for r in links}
    for vid in target_ids:
        view = objects['canonical_viewpoints', vid]['payload']
        cvr = objects['viewpoint_revisions', view['current_revision_id']]['payload']
        decisions.update(cvr.get('provenance', {}).get('basis_identity_decision_ids', []))
    decisions.discard(None)
    history = query_history(url, decisions, target_ids | {objects['canonical_viewpoints', v]['payload']['current_revision_id'] for v in target_ids})
    decision_rows = {r['object_id']: r for r in history if r['kind'] == 'object'}
    profiles = []
    for vid in sorted(target_ids):
        view = objects['canonical_viewpoints', vid]['payload']
        revision = objects['viewpoint_revisions', view['current_revision_id']]['payload']
        own = {r['payload'].get('decision_id') for r in links if r['payload']['viewpoint_id'] == vid}
        own.update(revision.get('provenance', {}).get('basis_identity_decision_ids', []))
        own.discard(None)
        resolved = [decision_rows[d]['payload'] for d in sorted(own) if d in decision_rows]
        profiles.append({'viewpoint_id': vid, 'decision_ids': sorted(own),
                         'missing_decision_ids': sorted(own - decision_rows.keys()),
                         'policy_versions': sorted({r.get('policy_version', 'unknown') for r in resolved}),
                         'model_ids': sorted({m for r in resolved for m in r.get('reviewer_model_ids', [])}),
                         'human_exception': any(r.get('approval_basis') == 'human_exception_review' or r.get('reviewer_kind') in {'human', 'human_editor'} for r in resolved),
                         'historical_declared_profile_only': True})
    candidates = []
    audits = []
    for group in history:
        if group['kind'] != 'group':
            continue
        report, status = audit_binding(group['audit'])
        if report is None:
            audits.append({'sequence': group['sequence'], 'binding': group['audit'], 'state': status})
            continue
        ids = {r.get('viewpoint_id') for r in report.get('layers', {}).get('4', {}).get('results', [])}
        relevant = ids & forward_ids
        if not relevant:
            continue
        audits.append({'sequence': group['sequence'], 'binding': group['audit'], 'state': status,
                       'revisit_required': group['revisit_required'], 'independent_audit_complete': group['independent_audit_complete']})
        for vid in sorted(relevant):
            candidates.append({'viewpoint_id': vid, 'sequence': group['sequence'], 'audit_binding': group['audit'],
                               'revisit_required': group['revisit_required'],
                               'independent_audit_complete': group['independent_audit_complete'],
                               'collision': match_audit(vid, report, objects)})
    sources = {s['source_id']: s for s in baseline['sources']}
    aliases = {}
    for source in sources.values():
        for name in (source['source_id'], source.get('transcript_id')):
            if name:
                aliases.setdefault(name, set()).add(source['source_id'])
    source_packets = {}
    def full_view(vid):
        view = objects['canonical_viewpoints', vid]['payload']
        own_links = [r for r in links if r['payload']['viewpoint_id'] == vid]
        own_claims = [objects['claims', cid] for cid in sorted({r['payload']['claim_id'] for r in own_links})]
        refs = set(PK.scalar_strings([r['payload'] for r in own_claims] + [r['payload'] for r in own_links]))
        evidence = [r for r in deps if r['collection'] == 'evidence_steps' and r['object_id'] in refs]
        refs.update(PK.scalar_strings([r['payload'] for r in evidence]))
        fragments = [r for r in deps if r['collection'] == 'source_fragments' and r['object_id'] in refs]
        own_sources = set()
        for claim in own_claims:
            for occurrence in claim['payload'].get('occurrences') or []:
                name = occurrence.get('source_id') or occurrence.get('transcript_id')
                if name not in aliases:
                    raise ValueError('unresolved complete member source')
                own_sources.update(aliases[name])
        for sid in own_sources:
            source_packets.setdefault(sid, PK.read_full_source(sources[sid]))
        return blind({'viewpoint': objects['viewpoint_revisions', view['current_revision_id']]['payload'],
                      'links': [r['payload'] for r in own_links], 'claims': [r['payload'] for r in own_claims],
                      'evidence': [r['payload'] for r in evidence], 'fragments': [r['payload'] for r in fragments],
                      'complete_own_source_ids': sorted(own_sources)})
    full_views = {vid: full_view(vid) for vid in sorted(target_ids)}
    out.mkdir()
    PF.write_new(out / 'history.json', {'rows': history, 'audit_bindings': audits, 'collision_candidates': candidates,
                                      'profiles': profiles, 'new_forward_model_calls_authorized': 0})
    manifests = []
    for name, context in reverse_contexts:
        linked = context['connected_viewpoint_ids']
        needed_sources = {sid for vid in linked for sid in full_views[vid]['complete_own_source_ids']}
        payload = {'stage': 'complete_reverse_comparison_context_preview', 'roles': PK.ROLES,
                   'source_ids': context['source_ids'], 'source_claims': blind([r['payload'] for r in context['claims']]),
                   'source_evidence': blind([r['payload'] for r in context['evidence']]),
                   'source_fragments': blind([r['payload'] for r in context['fragments']]),
                   'connected_complete_viewpoints': [full_views[vid] for vid in linked],
                   'complete_other_member_sources': [source_packets[sid] for sid in sorted(needed_sources)],
                   'independent_observations': None,
                   'source_disposition_status': 'not_verified; absent CVP does not prove omission',
                   'quality_result': None}
        packed = PK.compact_preview(payload)
        output_name = name.replace('.comparison-context.json', '.complete-comparison-preview.json')
        PF.write_new(out / output_name, packed)
        manifests.append({'file': output_name, 'decoded_sha256': PF.sha(payload), 'encoded_sha256': PF.sha(packed),
                          'bytes': len(PF.encoded(packed)), 'complete_viewpoint_ids': linked,
                          'unique_other_source_count': len(needed_sources), 'exact_roundtrip_verified': True,
                          'new_model_call_authorized': False})
    if PF.sha(query_history(url, decisions, target_ids | {objects['canonical_viewpoints', v]['payload']['current_revision_id'] for v in target_ids})) != PF.sha(history):
        raise ValueError('history drift')
    if PF.sha(PK.query_dependencies(url, claim_ids, [r['payload'] for r in links])) != PF.sha(deps):
        raise ValueError('dependency drift')
    if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
        raise ValueError('final current/source drift')
    PF.write_new(out / 'manifest.json', {'baseline_sha256': PF.sha(baseline), 'plan_sha256': PF.sha(plan),
        'history_sha256': PF.digest(out / 'history.json'), 'code_sha256': PF.digest(Path(__file__)),
        'entries': manifests, 'target_viewpoints': len(target_ids), 'profile_units': len(profiles),
        'history_candidate_units': len({r['viewpoint_id'] for r in candidates}),
        'source_dispositions_verified': False, 'provider_raw_authenticated': False,
        'new_model_calls': 0, 'complete_runtime_requests': False, 'quality_evaluation_complete': False})
    print(json.dumps({'output': str(out), 'target_viewpoints': len(target_ids),
        'history_candidate_units': len({r['viewpoint_id'] for r in candidates}),
        'same_current_audit_readset_units': len({r['viewpoint_id'] for r in candidates if r['collision']['same_complete_current_read_set']}),
        'reverse_contexts': len(manifests), 'max_reverse_compact_bytes': max(e['bytes'] for e in manifests),
        'sample_profile_counts': dict(Counter(p for r in profiles if r['viewpoint_id'] in forward_ids for p in r['policy_versions'])),
        'new_model_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
