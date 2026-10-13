#!/usr/bin/env python3
"""Expand nominated master-data pairs to complete members and physical originals."""
from __future__ import annotations

import argparse
from collections import defaultdict
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess

SPEC = importlib.util.spec_from_file_location('quality_history', Path(__file__).with_name('cvp-quality-history.py'))
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
PF, PK = H.PF, H.PK


def full_view(vid, objects, dependencies, sources):
    view = objects['canonical_viewpoints', vid]['payload']
    if view.get('identity_status') != 'active':
        raise ValueError('candidate is not a current active master record')
    cvr = objects['viewpoint_revisions', view['current_revision_id']]['payload']
    links = [r['payload'] for (c, _), r in objects.items() if c == 'viewpoint_claim_links'
             and r['payload']['viewpoint_id'] == vid and r['payload'].get('effective_state') == 'active']
    claim_ids = {l['claim_id'] for l in links}
    rows = {(r['collection'], r['object_id']): r for r in dependencies}
    claims = [rows['claims', cid]['payload'] for cid in sorted(claim_ids)]
    refs = set(PK.scalar_strings(claims + links))
    evidence = [r['payload'] for r in dependencies if r['collection'] == 'evidence_steps' and r['object_id'] in refs]
    refs.update(PK.scalar_strings(evidence))
    fragments = [r['payload'] for r in dependencies if r['collection'] == 'source_fragments' and r['object_id'] in refs]
    aliases = defaultdict(set)
    source_map = {s['source_id']: s for s in sources}
    for s in sources:
        for name in (s['source_id'], s.get('transcript_id')):
            if name:
                aliases[name].add(s['source_id'])
    own = set()
    for claim in claims:
        for occurrence in claim.get('occurrences', []):
            name = occurrence.get('source_id') or occurrence.get('transcript_id')
            if name not in aliases:
                raise ValueError('unresolved candidate member original')
            own.update(aliases[name])
    if any(not source_map[s].get('eligible') or 'path' not in source_map[s] for s in own):
        raise ValueError('candidate original outside eligible source corpus')
    source_identity = [{'source_id': sid, 'physical_source_key': source_map[sid]['path'],
                        'canonical_source_id': source_map[sid]['canonical_source_id']} for sid in sorted(own)]
    link_fields = ('viewpoint_claim_link_id', 'viewpoint_id', 'claim_id', 'link_type', 'pinned_claim_revision',
                   'validated_against_viewpoint_revision_id', 'component_locator', 'evidence_bindings', 'occurrence_refs')
    return H.blind({'stage': 'full_candidate_masterdata_material', 'roles': PK.ROLES, 'viewpoint': cvr,
        'links': [{k: l.get(k) for k in link_fields} for l in links], 'claims': claims,
        'evidence': evidence, 'fragments': fragments, 'complete_own_sources': [PK.read_full_source(source_map[s]) for s in sorted(own)],
        'physical_source_identity': source_identity, 'quality_result': None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    parser.add_argument('--candidate-file', required=True, type=Path)
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('UUID-verified independent staging required')
    if not args.candidate_file.resolve().is_relative_to(root / 'masterdata-candidate-discovery-v1'):
        raise ValueError('exact saved candidate discovery required')
    nominations = json.loads(args.candidate_file.read_text())
    ids = {p[k] for p in nominations['pairs'] for k in ('left_viewpoint_id', 'right_viewpoint_id')}
    baseline = json.loads((root / 'baseline.json').read_text())
    snapshot = json.loads((root / 'snapshot.json').read_text())
    if PF.sha(snapshot) != baseline['snapshot_sha256']:
        raise ValueError('snapshot binding mismatch')
    objects = {(r['collection'], r['object_id']): r for r in snapshot if r['kind'] == 'object'}
    if any(('canonical_viewpoints', vid) not in objects for vid in ids):
        raise ValueError('foreign nominated master ID')
    links = [r['payload'] for (c, _), r in objects.items() if c == 'viewpoint_claim_links'
             and r['payload']['viewpoint_id'] in ids and r['payload'].get('effective_state') == 'active']
    cids = {l['claim_id'] for l in links}
    url = PF.settings()['KNOWLEDGE_DATABASE_URL']
    with PF.LOCK.open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('initial SQL/source drift')
        deps = PK.query_dependencies(url, cids, links)
        for r in deps:
            old = objects.get((r['collection'], r['object_id']))
            if old and (r['revision'], r['content_sha256'], r['retired']) != (old['revision'], old['content_sha256'], old['retired']):
                raise ValueError('candidate dependency generation drift')
        materials, restrictions = {}, []
        for vid in sorted(ids):
            try:
                materials[vid] = full_view(vid, objects, deps, baseline['sources'])
            except (ValueError, KeyError) as error:
                restrictions.append({'viewpoint_id': vid, 'state': 'not_assessable_material',
                                     'reason': str(error), 'retained_in_candidate_denominator': True})
        if any(H.blind_leaks(value) for value in materials.values()):
            raise ValueError('old review metadata leaked')
        if PF.sha(deps) != PF.sha(PK.query_dependencies(url, cids, links)) or PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('final SQL/dependency/source drift')
        out = root / 'masterdata-extended-material-v1'
        out.mkdir(exist_ok=False)
        entries = []
        for vid, value in materials.items():
            name = vid + '.forward.json'
            PF.write_new(out / name, value)
            entries.append({'file': name, 'material_sha256': PF.sha(value), 'file_sha256': PF.digest(out / name),
                            'full_own_sources': len(value['complete_own_sources'])})
        PF.write_new(out / 'manifest.json', {'baseline_sha256': PF.sha(baseline), 'candidate_file_sha256': PF.digest(args.candidate_file),
            'candidate_file': str(args.candidate_file.resolve()), 'entries': entries, 'dependency_sha256': PF.sha(deps),
            'restrictions': restrictions,
            'dependency_pins': [{k: r[k] for k in ('collection', 'object_id', 'revision', 'content_sha256', 'retired')} for r in deps],
            'code_sha256': PF.digest(Path(__file__)), 'new_model_calls': 0, 'registry_writes': 0})
        print(json.dumps({'complete_candidate_master_records': len(entries), 'restricted_master_records': len(restrictions),
                          'output': str(out), 'new_calls': 0}))


if __name__ == '__main__':
    main()
