#!/usr/bin/env python3
"""#429 zero-call inventory. No backend imports, model calls or SQL writes.

This freezes a registered-source sampling frame, not a semantic quality verdict.
Historical-budget checks and complete evaluation packets remain separate stages.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import random
import re
import subprocess

REPO = Path(__file__).resolve().parents[1]
VOLUME = Path('/Volumes/Jun SSD')
UUID = '8D2A5FB1-CC0A-4230-B338-188BF47B06F3'
LOCK = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/immediate-recovery-20261002-v1/recovery.lock')
MEMBERS = {'equivalent_full', 'equivalent_component'}
STRUCTURAL_FLAGS = {'no_identity_members', 'missing_or_foreign_current_revision',
                    'missing_or_retired_claim', 'claim_version_mismatch',
                    'link_revision_mismatch', 'unresolved_member_source'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def verify_content_pins(rows):
    errors = []
    for row in rows:
        if row['kind'] != 'object':
            continue
        semantic = dict(row['payload'])
        semantic.pop('revision', None)
        if sha(semantic) != row['content_sha256']:
            errors.append((row['collection'], row['object_id']))
    if errors:
        raise ValueError(f'{len(errors)} stored semantic content SHA mismatches')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def settings():
    values = {}
    for line in (REPO / '.env').read_text().splitlines():
        match = re.match(r'^([A-Z_0-9]+)=(.*)$', line.strip())
        if match:
            values[match[1]] = match[2].strip().strip('"').strip("'")
    for key in ('KNOWLEDGE_DATABASE_URL', 'DATA_BASE_DIR'):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def query_snapshot(url):
    # One database snapshot, including the reverse frame's sources without links.
    # Use -f stdin: multiple -c statements can return only the final result.
    sql = """
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';
WITH links AS (
 SELECT payload FROM wang_knowledge.objects
 WHERE collection='viewpoint_claim_links' AND retired_at IS NULL
), member_claims AS (
 SELECT o.* FROM wang_knowledge.objects o
 WHERE o.collection='claims' AND o.object_id IN (SELECT payload->>'claim_id' FROM links)
)
SELECT json_build_object('kind','object','collection',o.collection,
 'object_id',o.object_id,'revision',o.revision,'content_sha256',o.content_sha256,
 'retired',o.retired_at IS NOT NULL,'review_status',o.review_status,'payload',o.payload)
FROM wang_knowledge.objects o WHERE
 (o.collection IN ('canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links',
 'viewpoint_relations','source_documents') AND o.retired_at IS NULL)
 OR (o.collection='claims' AND o.object_id IN (SELECT object_id FROM member_claims))
ORDER BY o.collection,o.object_id;
SELECT json_build_object('kind','claim_frame','object_id',object_id,'revision',revision,
 'content_sha256',content_sha256,'occurrences',
 (SELECT json_agg(json_build_object('source_id',item->>'source_id','transcript_id',item->>'transcript_id'))
 FROM jsonb_array_elements(COALESCE(payload->'occurrences','[]'::jsonb)) item))
FROM wang_knowledge.objects WHERE collection='claims' AND retired_at IS NULL ORDER BY object_id;
SELECT json_build_object('kind','campaign','subject_id',subject_id,'status',status,
 'metadata',json_build_object('campaign_sha256',metadata->>'campaign_sha256',
 'kind',metadata->>'kind','checkpoint',metadata->>'checkpoint','sequence',metadata->'sequence',
 'group_key',metadata->>'group_key','claim_ids',metadata->'claim_ids',
 'independent_audit_complete',metadata->'independent_audit_complete',
 'revisit_required',metadata->'revisit_required','terminal_refusal',metadata->'terminal_refusal',
 'execution_roots',metadata->'execution_roots','root',metadata->>'root',
 'owner_run_id',metadata->>'owner_run_id','action_id',metadata->>'action_id'))
 FROM wang_knowledge.pipeline_runs WHERE stage='cvp_group' ORDER BY subject_id;
COMMIT;
"""
    result = subprocess.run(['psql', url, '-XAtq', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                            input=sql, text=True, capture_output=True, timeout=90)
    if result.returncode:
        # Database stderr can contain credentials; keep the failure message safe.
        raise ValueError('read-only database snapshot failed')
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def source_path(payload, data_dir):
    declared = payload.get('source_path')
    if declared and Path(declared).is_file():
        return Path(declared).resolve(), 'declared_path'
    transcript = payload.get('transcript_id', '')
    for directory in ('script_published', 'script_review', 'script_patched'):
        candidate = data_dir / directory / f'{transcript}.json'
        if transcript and candidate.is_file():
            return candidate.resolve(), 'transcript_id_fallback'
    return None, 'missing_physical_source'


def physical_inventory(documents, data_dir):
    sources = []
    for row in documents:
        payload = row['payload']
        path, resolution = source_path(payload, data_dir)
        entry = {'source_id': row['object_id'], 'source_type': payload.get('source_type', 'unknown'),
                 'transcript_id': payload.get('transcript_id'), 'resolution': resolution,
                 'canonical_source_id': payload.get('canonical_source_id'), 'eligible': False}
        if path:
            entry.update(path=str(path), physical_sha256=digest(path), bytes=path.stat().st_size)
            # Source identity comes from project metadata, never its directory name.
            if entry['source_type'] == 'notes_manuscript':
                metadata_path = path.parent / 'meta.json'
                entry['project_metadata_path'] = str(metadata_path)
                if metadata_path.is_file():
                    metadata = json.loads(metadata_path.read_text())
                    entry['project_metadata_sha256'] = digest(metadata_path)
                    entry['project_type'] = metadata.get('project_type', 'unknown')
                    entry['eligible'] = entry['project_type'] != 'transcript' and entry['project_type'] != 'unknown'
                    entry['eligibility_reason'] = ('editorial_view' if entry['project_type'] == 'transcript'
                                                   else 'unknown_project_type' if entry['project_type'] == 'unknown' else 'project_metadata')
                else:
                    entry['eligibility_reason'] = 'missing_project_metadata'
            elif entry['source_type'] == 'sermon_transcript':
                entry.update(eligible=True, eligibility_reason='registered_sermon_transcript')
            else:
                entry['eligibility_reason'] = 'unknown_source_type'
        sources.append(entry)
    return sources


def inventory(rows, sources):
    objects = [r for r in rows if r['kind'] == 'object']
    by_collection = defaultdict(dict)
    for row in objects:
        by_collection[row['collection']][row['object_id']] = row
    aliases = defaultdict(set)
    source_by_id = {s['source_id']: s for s in sources}
    for source in sources:
        for name in (source['source_id'], source.get('transcript_id')):
            if name:
                aliases[name].add(source['source_id'])
    def claim_sources(claim):
        result = set()
        unresolved = []
        for occurrence in claim.get('occurrences') or []:
            name = occurrence.get('source_id') or occurrence.get('transcript_id')
            ids = aliases.get(name, set())
            if not ids:
                unresolved.append(str(name))
            for source_id in ids:
                source = source_by_id[source_id]
                # Same physical original is one source; missing paths stay distinct.
                result.add(source.get('path') or source_id)
        return result, unresolved
    current_links = [r for r in by_collection['viewpoint_claim_links'].values()
                     if r['payload'].get('effective_state') == 'active']
    links_by_view = defaultdict(list)
    for row in current_links:
        links_by_view[row['payload']['viewpoint_id']].append(row)
    units, findings = [], []
    for row in by_collection['canonical_viewpoints'].values():
        payload = row['payload']
        if payload.get('identity_status') != 'active':
            continue
        vid = row['object_id']
        links = links_by_view[vid]
        members = [r for r in links if r['payload'].get('link_type') in MEMBERS]
        source_keys, missing_sources, types = set(), [], set()
        flags = []
        cvr = by_collection['viewpoint_revisions'].get(payload.get('current_revision_id'))
        if cvr is None or cvr['payload'].get('viewpoint_id') != vid:
            flags.append('missing_or_foreign_current_revision')
        if not members:
            flags.append('no_identity_members')
        for link in links:
            lp = link['payload']
            claim = by_collection['claims'].get(lp.get('claim_id'))
            if claim is None or claim['retired']:
                flags.append('missing_or_retired_claim')
                continue
            if lp.get('pinned_claim_revision') != claim['revision']:
                flags.append('claim_version_mismatch')
            keys, unresolved = claim_sources(claim['payload'])
            missing_sources.extend(unresolved)
            if lp.get('validated_against_viewpoint_revision_id') != payload.get('current_revision_id'):
                flags.append('link_revision_mismatch')
            if lp.get('link_type') == 'equivalent_component':
                flags.append('component_role')
            if lp.get('link_type') not in MEMBERS:
                flags.append('non_identity_role')
            if link in members:
                source_keys.update(keys)
                for key in keys:
                    types.update(s['source_type'] for s in sources if (s.get('path') or s['source_id']) == key)
        if missing_sources:
            flags.append('unresolved_member_source')
        unique_claims = sorted({r['payload']['claim_id'] for r in members})
        source_class = 'unknown' if not source_keys or missing_sources else ('cross_source' if len(source_keys) > 1 else 'single_source')
        size_class = '0' if not unique_claims else ('1' if len(unique_claims) == 1 else '2-4' if len(unique_claims) < 5 else '5+')
        unit = {'viewpoint_id': vid, 'current_revision_id': payload.get('current_revision_id'),
                'identity_claim_ids': unique_claims, 'active_link_ids': sorted(r['object_id'] for r in links),
                'identity_source_keys': sorted(source_keys), 'source_types': sorted(types),
                'stratum': '|'.join(('+'.join(sorted(types)) or 'unknown', source_class, size_class)),
                'risk_flags': sorted(set(flags)), 'generation_profile': 'unknown',
                'history_budget_status': 'not_checked'}
        units.append(unit)
        findings.extend({'viewpoint_id': vid, 'kind': flag, 'semantic_verdict': None} for flag in sorted(set(flags)))
    return sorted(units, key=lambda u: u['viewpoint_id']), findings


def risk_sample(units, target):
    if target < 0:
        raise ValueError('risk target must be nonnegative')
    # Structural risks outrank ubiquitous component/role complexity labels.
    return sorted((u for u in units if u['risk_flags']), key=lambda u: (
        -len(STRUCTURAL_FLAGS.intersection(u['risk_flags'])),
        -len(u['risk_flags']), -len(u['identity_claim_ids']), u['viewpoint_id']))[:target]


def stratified_sample(units, seed, target, id_key, stratum_key='stratum', minimum=1):
    if target < 1:
        raise ValueError('sample target must be positive')
    strata = defaultdict(list)
    for unit in sorted(units, key=lambda u: u[id_key]):
        strata[unit[stratum_key]].append(unit)
    if not strata:
        return {'strata': [], 'selected': []}
    target = min(target, len(units))
    if target < sum(min(minimum, len(v)) for v in strata.values()):
        raise ValueError('target must cover every nonempty stratum')
    allocation = {key: min(minimum, len(value)) for key, value in strata.items()}
    while sum(allocation.values()) < target:
        candidates = [key for key in strata if allocation[key] < len(strata[key])]
        key = max(candidates, key=lambda k: (len(strata[k]) / allocation[k], k))
        allocation[key] += 1
    selected, summary = [], []
    for key in sorted(strata):
        rng = random.Random(f'{seed}:{key}')
        members = rng.sample(strata[key], allocation[key])
        probability = allocation[key] / len(strata[key])
        selected.extend({**u, 'selection': 'random', 'inclusion_probability': probability} for u in members)
        summary.append({'stratum': key, 'population': len(strata[key]), 'n': allocation[key],
                        'inclusion_probability': probability, 'census': allocation[key] == len(strata[key])})
    return {'strata': summary, 'selected': sorted(selected, key=lambda u: u[id_key])}


def verify_sources(sources):
    drift = []
    for source in sources:
        for path_field, sha_field in (('path', 'physical_sha256'), ('project_metadata_path', 'project_metadata_sha256')):
            if sha_field in source:
                path = Path(source[path_field])
                if not path.is_file() or digest(path) != source[sha_field]:
                    drift.append({'source_id': source['source_id'], 'path': str(path)})
    return drift


def write_new(path, value):
    with path.open('xb') as stream:
        stream.write(encoded(value) + b'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=42920261012)
    parser.add_argument('--viewpoints', type=int, default=60)
    parser.add_argument('--sources', type=int, default=12)
    parser.add_argument('--risks', type=int, default=20)
    args = parser.parse_args()
    info = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(VOLUME)]))
    if not os.path.ismount(VOLUME) or info.get('VolumeUUID') != UUID or info.get('ReadOnlyVolume'):
        raise ValueError('required external SSD unavailable or wrong identity')
    out = args.out.absolute()
    allowed = VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    if not out.resolve().is_relative_to(allowed.resolve()) or out.exists():
        raise ValueError('output must be a new independent #429 SSD staging directory')
    cfg = settings()
    with LOCK.open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        rows = query_snapshot(cfg['KNOWLEDGE_DATABASE_URL'])
        verify_content_pins(rows)
        sources = physical_inventory([r for r in rows if r.get('collection') == 'source_documents'], Path(cfg['DATA_BASE_DIR']))
        units, screening = inventory(rows, sources)
        random_sample = stratified_sample(units, args.seed, args.viewpoints, 'viewpoint_id')
        # Risk sample records all screening flags and remains a separate population.
        risks = risk_sample(units, args.risks)
        physical_frame = {}
        for source in sources:
            if source['eligible']:
                physical_frame.setdefault(source['path'], {'path': source['path'], 'source_ids': [], 'stratum': source['source_type']})['source_ids'].append(source['source_id'])
        reverse_sample = stratified_sample(list(physical_frame.values()), args.seed, args.sources, 'path', minimum=4)
        drift = verify_sources(sources)
        second_rows = query_snapshot(cfg['KNOWLEDGE_DATABASE_URL'])
        if drift or sha(rows) != sha(second_rows):
            raise ValueError('SQL/source baseline drift; no frozen sample produced')
        out.mkdir(parents=True)
        write_new(out / 'snapshot.json', rows)
        baseline = {'schema_version': 'cvp_quality_preflight_baseline_v1', 'generated_at': datetime.now(timezone.utc).isoformat(),
                    'code_sha256': digest(Path(__file__)), 'snapshot_sha256': sha(rows),
                    'counts': {'active_viewpoints': len(units), 'active_links': sum(len(u['active_link_ids']) for u in units),
                               'registered_source_records': len(sources), 'eligible_physical_sources': len(physical_frame),
                               'active_claim_frame': sum(r['kind'] == 'claim_frame' for r in rows)},
                    'sources': sources, 'scope': 'all current active Registry CVPs; reverse frame limited to registered physical sources',
                    'exclusions': [s for s in sources if not s['eligible']], 'sql_and_physical_stability': True,
                    'ssd_volume_uuid': UUID, 'worker_lock': str(LOCK), 'worker_lock_state': 'shared_nonblocking_acquired'}
        write_new(out / 'baseline.json', baseline)
        plan = {'schema_version': 'cvp_quality_zero_call_plan_v1', 'baseline_sha256': sha(baseline),
                'seed': args.seed, 'stable_sort': 'exact IDs / physical paths ascending', 'replacement': False,
                'sample_size_rationale': f'{args.viewpoints} random CVPs is an initial six-dimension quality baseline, not certification. The default 60 targets roughly a 5% defect level (simple-random zero-defect one-sided 95% bound approximately 4.9%); actual stratification requires design-weighted intervals. {args.sources} complete sources bound reverse-review workload, minimum four per available source-type stratum to include notes; no zero-omission claim.',
                'authorized_new_model_calls': 0, 'provider': None, 'request_budget': None,
                'historical_collision_check': 'pending; samples are never authorization to call',
                'acceptance': 'preflight only; no semantic dimensions assessed; sample provisional until full identity/provenance/budget frame verified',
                'random_viewpoints': random_sample, 'risk_viewpoints': risks, 'reverse_sources': reverse_sample,
                'risk_selection': 'structural failure flags first, then distinct role-complexity flags, identity-member count, exact ID tiebreak; overlaps disclosed, no population extrapolation',
                'remaining': ['corpus outside registered sources', 'full dependency/anchor closure', 'complete forward/reverse packets',
                              'generation profiles and historical budget collision proofs', 'approved exact model/config/bytes/cost caps',
                              'six-dimension judgments and historical context review', 'weighted uncertainty metrics and final version checks']}
        write_new(out / 'evaluation-plan.json', plan)
        write_new(out / 'screening.json', screening)
        print(json.dumps({'output': str(out), 'counts': baseline['counts'], 'random_viewpoints': len(random_sample['selected']),
                          'risk_viewpoints': len(risks), 'reverse_sources': len(reverse_sample['selected']),
                          'new_model_calls': 0, 'quality_evaluation_complete': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
