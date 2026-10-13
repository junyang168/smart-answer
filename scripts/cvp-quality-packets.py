#!/usr/bin/env python3
"""Build complete zero-call material previews from a #429 preflight.

No request/intent or provider adapter exists here. History is a separate input.
The reverse comparison is a preview: independent observations must come first.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess

SPEC = importlib.util.spec_from_file_location('quality_preflight', Path(__file__).with_name('cvp-quality-preflight.py'))
PF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PF)

ROLES = {
    'equivalent_full': '整条主张与观点真值条件等价，是身份成员。',
    'equivalent_component': '结构化定位的明确成分与观点等价，是身份成员。',
    'supports': '提供具体论据，不要求独自证明全部命题，不是身份成员。',
    'extends': '保留核心但增加改变真值条件的内容，不是身份成员。',
    'qualifies': '添加条件、范围或防误解边界，不是身份成员。',
    'applies': '将判断用于具体对象或行动，不是身份成员。',
    'tension_evidence': '提供未解决张力的材料，不是身份成员。',
    'superseding_evidence': '为后期明确修正提供材料，不是身份成员。',
}


def read_full_source(source):
    path = Path(source['path'])
    raw = path.read_bytes()
    if PF.hashlib.sha256(raw).hexdigest() != source['physical_sha256']:
        raise ValueError('physical source drift')
    if source['source_type'] == 'sermon_transcript':
        parsed = json.loads(raw)
        rows = parsed.get('script') if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            raise ValueError('unknown physical transcript shape')
    elif source['source_type'] == 'notes_manuscript':
        text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
        rows = [{'text': block.strip()} for block in re.split(r'\n[ \t]*\n+', text) if block.strip()]
    else:
        raise ValueError('unknown physical source type')
    segments = []
    for ordinal, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            row = {'text': str(row)}
        text = str(row.get('text') or '')
        editorial = (str(row.get('type', '')).lower() in {'subtitle', 'comment'}
                     or str(row.get('index', '')).startswith('subtitle-')
                     or re.match(r'^#{1,6}\s+', text.strip()) is not None)
        segments.append({'physical_ordinal': ordinal, 'source_index': row.get('index'),
                         'start_time': row.get('start_time'), 'end_time': row.get('end_time'),
                         'editorial': editorial, 'physical_text': text,
                         'live_text': re.sub(r'~~([^~]+?)~~', '\n', text, flags=re.S)})
    return {'source_id': source['source_id'], 'source_type': source['source_type'],
            'complete': True, 'physical_sha256': source['physical_sha256'], 'segments': segments}


def scalar_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from scalar_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from scalar_strings(item)


def compact_preview(payload):
    """Reversible key dictionary; derived live text is rebuilt from physical text."""
    value = json.loads(PF.encoded(payload))
    def remove_derived(item):
        if isinstance(item, dict):
            if 'physical_ordinal' in item and 'physical_text' in item and 'live_text' in item:
                expected = re.sub(r'~~([^~]+?)~~', '\n', item['physical_text'], flags=re.S)
                if item['live_text'] != expected:
                    raise ValueError('unexpected source projection; cannot omit live text')
                del item['live_text']
            for v in item.values():
                remove_derived(v)
        elif isinstance(item, list):
            for v in item:
                remove_derived(v)
    remove_derived(value)
    keys = set()
    def gather(item):
        if isinstance(item, dict):
            keys.update(item)
            for v in item.values():
                gather(v)
        elif isinstance(item, list):
            for v in item:
                gather(v)
    gather(value)
    mapping = {key: f'k{n}' for n, key in enumerate(sorted(keys))}
    def pack(item):
        if isinstance(item, dict):
            return {mapping[k]: pack(v) for k, v in item.items()}
        if isinstance(item, list):
            return [pack(v) for v in item]
        return item
    result = {'codec': 'cvp_quality_preview_keys_and_derived_live_v1',
              'keys': {v: k for k, v in mapping.items()}, 'data': pack(value)}
    if expand_preview(result) != payload:
        raise ValueError('lossless preview roundtrip failed')
    return result


def expand_preview(encoded):
    def unpack(item):
        if isinstance(item, dict):
            return {encoded['keys'][k]: unpack(v) for k, v in item.items()}
        if isinstance(item, list):
            return [unpack(v) for v in item]
        return item
    result = unpack(encoded['data'])
    def restore(item):
        if isinstance(item, dict):
            if 'physical_ordinal' in item and 'physical_text' in item:
                item['live_text'] = re.sub(r'~~([^~]+?)~~', '\n', item['physical_text'], flags=re.S)
            for v in item.values():
                restore(v)
        elif isinstance(item, list):
            for v in item:
                restore(v)
    restore(result)
    return result


def query_dependencies(url, claim_ids, link_values):
    names = sorted(set(claim_ids) | set(scalar_strings(link_values)))
    literals = ','.join("'" + name.replace("'", "''") + "'" for name in names) or 'NULL'
    # Exact ID equality; no string similarity, no recursive history traversal.
    sql = f"""
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '60s';
WITH claims AS (
 SELECT o.* FROM wang_knowledge.objects o WHERE collection='claims' AND object_id IN ({literals})
), names AS (
 SELECT jsonb_path_query(payload,'$.** ? (@.type() == "string")') #>> '{{}}' AS name FROM claims
 UNION SELECT unnest(ARRAY[{literals}]::text[])
), evidence AS (
 SELECT o.* FROM wang_knowledge.objects o WHERE collection='evidence_steps' AND object_id IN (SELECT name FROM names)
), fragment_names AS (
 SELECT jsonb_path_query(payload,'$.** ? (@.type() == "string")') #>> '{{}}' AS name FROM evidence
 UNION SELECT name FROM names
), selected AS (
 SELECT * FROM claims UNION ALL SELECT * FROM evidence UNION ALL
 SELECT * FROM wang_knowledge.objects WHERE collection='source_fragments' AND object_id IN (SELECT name FROM fragment_names)
)
SELECT json_build_object('kind','object','collection',collection,'object_id',object_id,
 'revision',revision,'content_sha256',content_sha256,'retired',retired_at IS NOT NULL,
 'review_status',review_status,'payload',payload) FROM selected ORDER BY collection,object_id;
COMMIT;
"""
    result = subprocess.run(['psql', url, '-XAtq', '-v', 'ON_ERROR_STOP=1', '-f', '-'],
                            input=sql, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise ValueError('read-only dependency query failed')
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    PF.verify_content_pins(rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    parser.add_argument('--compact-existing', action='store_true', help='verify and encode saved previews without rebuilding them')
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('independent SSD staging required')
    baseline = json.loads((root / 'baseline.json').read_text())
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    snapshot = json.loads((root / 'snapshot.json').read_text())
    if PF.sha(snapshot) != baseline['snapshot_sha256'] or PF.sha(baseline) != plan['baseline_sha256']:
        raise ValueError('preflight binding mismatch')
    if PF.verify_sources(baseline['sources']):
        raise ValueError('source/metadata drift')
    if args.compact_existing:
        parent = root / 'packet-previews'
        manifest = json.loads((parent / 'manifest.json').read_text())
        if manifest['baseline_sha256'] != PF.sha(baseline) or manifest['plan_sha256'] != PF.sha(plan):
            raise ValueError('preview parent binding mismatch')
        target = root / 'compact-previews'
        if target.exists():
            raise ValueError('compact previews already exist')
        target.mkdir()
        entries = []
        for entry in manifest['entries']:
            payload = json.loads((parent / entry['file']).read_text())
            if PF.sha(payload) != entry['material_sha256']:
                raise ValueError('saved preview drift')
            packed = compact_preview(payload)
            PF.write_new(target / entry['file'], packed)
            entries.append({**entry, 'original_bytes': entry['bytes'], 'bytes': len(PF.encoded(packed)),
                            'encoded_sha256': PF.sha(packed), 'exact_roundtrip_verified': True})
        PF.write_new(target / 'manifest.json', {'parent_manifest_sha256': PF.sha(manifest),
            'code_sha256': PF.digest(Path(__file__)), 'entries': entries, 'new_model_calls': 0,
            'complete_runtime_request': False})
        print(json.dumps({'compact_output': str(target), 'max_forward_bytes': max(e['bytes'] for e in entries
                        if e['file'].endswith('forward.json')), 'roundtrips_verified': len(entries), 'new_model_calls': 0}))
        return
    out = root / 'packet-previews'
    if out.exists():
        raise ValueError('packet preview already exists; preserve immutable evidence')
    url = PF.settings()['KNOWLEDGE_DATABASE_URL']
    if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256']:
        raise ValueError('SQL baseline drift')
    objects = {(r['collection'], r['object_id']): r for r in snapshot if r['kind'] == 'object'}
    units = {u['viewpoint_id']: u for u in plan['random_viewpoints']['selected'] + plan['risk_viewpoints']}
    sources = {s['source_id']: s for s in baseline['sources']}
    aliases = {}
    for source in sources.values():
        aliases.setdefault(source['source_id'], set()).add(source['source_id'])
        if source.get('transcript_id'):
            aliases.setdefault(source['transcript_id'], set()).add(source['source_id'])
    def source_ids(claim):
        names = set()
        for occurrence in claim.get('occurrences') or []:
            name = occurrence.get('source_id') or occurrence.get('transcript_id')
            if name not in aliases:
                raise ValueError('unresolved own source identity')
            names.update(aliases[name])
        return names
    reverse_source_ids = {sid for u in plan['reverse_sources']['selected'] for sid in u['source_ids']}
    reverse_claim_ids = set()
    for row in snapshot:
        if row['kind'] == 'claim_frame' and source_ids(row).intersection(reverse_source_ids):
            reverse_claim_ids.add(row['object_id'])
    selected_links = [objects['viewpoint_claim_links', lid] for u in units.values() for lid in u['active_link_ids']]
    claim_ids = reverse_claim_ids | {l['payload']['claim_id'] for l in selected_links}
    dependency_rows = query_dependencies(url, claim_ids, [l['payload'] for l in selected_links])
    dependencies = {(r['collection'], r['object_id']): r for r in dependency_rows}
    # Exact current member pins, never silently substitute a new version.
    for key, row in dependencies.items():
        if key in objects and (row['content_sha256'], row['revision'], row['retired']) != (
                objects[key]['content_sha256'], objects[key]['revision'], objects[key]['retired']):
            raise ValueError('dependency drift')
    def records_for_claims(ids):
        claims = [dependencies['claims', cid] for cid in sorted(ids)]
        names = set(scalar_strings([c['payload'] for c in claims]))
        evidence = [r for (c, oid), r in dependencies.items() if c == 'evidence_steps' and oid in names]
        fragment_ids = names | set(scalar_strings([e['payload'] for e in evidence]))
        fragments = [r for (c, oid), r in dependencies.items() if c == 'source_fragments' and oid in fragment_ids]
        return claims, evidence, fragments
    out.mkdir()
    manifests = []
    def save(name, payload):
        PF.write_new(out / name, payload)
        manifests.append({'file': name, 'material_sha256': PF.sha(payload), 'bytes': len(PF.encoded(payload)),
                          'model_call_authorized': False, 'history_collision_status': 'not_checked'})
    for vid, unit in sorted(units.items()):
        links = [objects['viewpoint_claim_links', lid]['payload'] for lid in unit['active_link_ids']]
        claims, evidence, fragments = records_for_claims({l['claim_id'] for l in links})
        own = set().union(*(source_ids(c['payload']) for c in claims)) if claims else set()
        if any('path' not in sources[sid] for sid in own):
            raise ValueError('missing full source; keep selected unit restricted')
        view = objects['canonical_viewpoints', vid]['payload']
        revision = objects.get(('viewpoint_revisions', view['current_revision_id']))
        if revision is None:
            raise ValueError('missing current CVR')
        # Approval/provenance and old reasoning are withheld from blind material.
        revision_fields = ('viewpoint_revision_id', 'viewpoint_id', 'core_proposition', 'scope',
                           'proposition_signature', 'not_a_direct_quote', 'attribution_subject', 'representation_kind')
        link_fields = ('viewpoint_claim_link_id', 'viewpoint_id', 'claim_id', 'link_type', 'pinned_claim_revision',
                       'component_locator', 'evidence_bindings', 'occurrence_refs')
        def blind(row):
            return {k: v for k, v in row['payload'].items() if k not in {
                'review_status', 'review_note', 'reviewed_by', 'reviewed_at', 'approved_by', 'approved_at',
                'provenance', 'metrics', 'review_provenance'}}
        save(f'{vid}.forward.json', {'stage': 'forward_material_preview', 'roles': ROLES,
            'viewpoint': {k: revision['payload'].get(k) for k in revision_fields},
            'links': [{k: l.get(k) for k in link_fields} for l in links],
            'claims': [blind(r) for r in claims], 'evidence': [blind(r) for r in evidence],
            'fragments': [blind(r) for r in fragments],
            'complete_own_sources': [read_full_source(sources[sid]) for sid in sorted(own)],
            'quality_result': None})
    for n, unit in enumerate(plan['reverse_sources']['selected'], 1):
        sid = sorted(unit['source_ids'])[0]
        prefix = f'source-{n:02}'
        save(f'{prefix}.observe.json', {'stage': 'reverse_observation_material_preview',
            'complete_source': read_full_source(sources[sid]), 'quality_result': None})
        ids = {r['object_id'] for r in snapshot if r['kind'] == 'claim_frame' and source_ids(r).intersection(unit['source_ids'])}
        claims, evidence, fragments = records_for_claims(ids)
        links = [r for (c, _), r in objects.items() if c == 'viewpoint_claim_links'
                 and r['payload']['claim_id'] in ids and r['payload'].get('effective_state') == 'active']
        linked_ids = {r['payload']['viewpoint_id'] for r in links}
        save(f'{prefix}.comparison-context.json', {'stage': 'reverse_comparison_context_preview',
             'source_ids': unit['source_ids'], 'claims': claims, 'evidence': evidence, 'fragments': fragments,
             'links': links, 'connected_viewpoint_ids': sorted(linked_ids),
             'required_before_call': ['independent complete-source observations', 'all connected CVPs and complete other members',
                                      'approved blind projection', 'exact historical-collision proof'], 'quality_result': None})
    if PF.sha(query_dependencies(url, claim_ids, [l['payload'] for l in selected_links])) != PF.sha(dependency_rows):
        raise ValueError('dependency drift after packet construction')
    if PF.sha(PF.query_snapshot(url)) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
        raise ValueError('SQL/source drift after packet construction')
    PF.write_new(out / 'manifest.json', {'schema_version': 'cvp_quality_packet_previews_v1',
        'baseline_sha256': PF.sha(baseline), 'plan_sha256': PF.sha(plan), 'code_sha256': PF.digest(Path(__file__)),
        'preflight_code_sha256': PF.digest(Path(PF.__file__)), 'entries': manifests,
        'unique_forward_units': len(units), 'reverse_source_units': len(plan['reverse_sources']['selected']),
        'new_model_calls': 0, 'full_read_sets_stable': True,
        'complete_runtime_request': False, 'quality_evaluation_complete': False})
    print(json.dumps({'output': str(out), 'forward_units': len(units), 'reverse_sources': len(plan['reverse_sources']['selected']),
                      'largest_forward_material_bytes': max(e['bytes'] for e in manifests if e['file'].endswith('forward.json')),
                      'new_model_calls': 0}))


if __name__ == '__main__':
    main()
