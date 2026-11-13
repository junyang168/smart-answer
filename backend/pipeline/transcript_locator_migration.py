"""Repeatable, model-free Evidence → Claim locator migration.

prepare is read-only; run explicitly commits two existing, independently guarded
transactions. A resumed run never replays the Evidence baseline after Claim writes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
from html import escape
import json
from pathlib import Path
from time import perf_counter

from backend.pipeline import transcript_claim_migration as m
from backend.pipeline import transcript_evidence_apply as evidence
from backend.pipeline import transcript_claim_anchor_sync as anchors

VERSION = 'wang_transcript_locator_migration_v1'
RESOLVED = {'auto_rebind', 'already_current', 'source_deleted', 'already_deleted',
            'retired', 'context_only', 'relocate_changed_quote'}


def register_equivalent_publication(store, row, published):
    """Rebind only the physical file when the registered coordinate identity matches."""
    doc = row['payload']
    if (published is None or doc.get('source_type') != 'sermon_transcript'
            or doc.get('locator_space') != 'spoken_body_v1'
            or Path(published).resolve() != Path(doc['source_path']).resolve()
            or not all(doc.get(field) for field in (
                'source_body_sha256', 'source_text_sha256'))):
        return False
    bound = m.binding(Path(published))
    snapshot = evidence.cm.source.source_snapshot(bound, source=doc, visual_context=True)
    fields = {'source_body_sha256': 'body_sha256', 'source_text_sha256': 'spoken_text_sha256',
              'source_visual_sha256': 'visual_content_sha256'}
    if any(doc.get(field) != snapshot.get(value) for field, value in fields.items()):
        return False
    if any(doc.get(field) not in {None, snapshot['body_sha256']}
           for field in ('source_sha256', 'anchor_binding_sha256')):
        return False
    sid = doc['source_id']
    from backend.pipeline.source_projection import project_script

    raw = evidence.cm.source.bound_bytes(bound)
    document = json.loads(raw)
    projection = project_script(document.get('script') if isinstance(document, dict) else document)
    updated = {**doc, 'source_file_sha256': bound['file_sha256'],
               'editorial_structure_sha256': projection.editorial_structure_sha256,
               'editorial_topology_sha256': projection.editorial_topology_sha256}
    with evidence.locks(sid, record_keys=['source_documents/'+sid]):
        plan = store.plan_package({'source_documents': [updated]}, source_kind=VERSION)
        if [(op.collection, op.object_id) for op in plan.operations] != [('source_documents', sid)]:
            raise ValueError('equivalent publication registration must update only the source document')
        evidence.cm.source.verify_file(bound)
        store.apply_plan(plan, expected_current_source_records={sid: (row['revision'], row['content_sha256'])},
                         metadata={'source_registration_only': True, 'before_file_sha256': doc['source_file_sha256'],
                                   'after_file_sha256': bound['file_sha256']})
        evidence.cm.source.verify_file(bound)
        if store.get_record_state('source_documents', sid)['payload']['source_file_sha256'] != bound['file_sha256']:
            raise ValueError('equivalent publication registration readback differs')
    return True


def locator_census(records, source, doc):
    checked = 0
    unlocated = []
    for key, state in sorted(records.items()):
        if state.get('retired'):
            continue
        payload = state['payload']
        if key.startswith('source_fragments/'):
            if (payload.get('source_id') != doc['source_id'] or payload.get('source_modality') == 'visual'
                    or payload.get('anchor_state') in {'source_deleted', 'context_only', 'replaced_by_precise_fragments'}):
                continue
            checked += 1
            try:
                if not (payload.get('anchor_state') == 'source_version_bound'
                        or payload.get('aligned_paragraphs')
                        or payload.get('source_binding_status') in {'quotation_pending_update', 'source_text_missing'}):
                    raise ValueError('no verified paragraph binding')
                paragraph = source['paragraphs'].get(payload.get('paragraph_key'))
                if payload.get('anchor_state') == 'source_version_bound' and paragraph:
                    # Legacy records may omit redundant clock/index and hash fields.
                    quote = payload.get('verbatim_excerpt') or ''
                    defaults = {'media_time': paragraph.get('start_time'), 'media_end_time': paragraph.get('end_time'),
                                'source_segment_index': paragraph['index'],
                                'paragraph_text_sha256': m.sha(paragraph['text'].encode()),
                                'verbatim_excerpt_sha256': m.sha(quote.encode())}
                    payload = {**defaults, **{field: value for field, value in payload.items()
                                              if value is not None or field not in defaults}}
                evidence.validate_fragment_locations({key: payload}, source)
            except ValueError as exc:
                unlocated.append({'object_key': key, 'paragraph_key': payload.get('paragraph_key'),
                                  'quote': payload.get('verbatim_excerpt'), 'media_time': payload.get('media_time'),
                                  'reason': str(exc)})
        elif key.startswith('claims/') and payload.get('review_status') not in {'superseded', 'retired'}:
            for occurrence in payload.get('occurrences', []):
                if not anchors.own_occurrence(occurrence, doc):
                    continue
                for anchor in occurrence.get('anchors', []):
                    checked += 1
                    pk = anchor.get('paragraph_key')
                    paragraph = source['paragraphs'].get(pk)
                    if (not paragraph or anchor.get('source_sha256') not in {None, source['body_sha256']}
                            or anchor.get('media_time') != paragraph.get('start_time')
                            or (anchor.get('media_end_time') is not None
                                and anchor['media_end_time'] != paragraph.get('end_time'))):
                        unlocated.append({'object_key': key, 'paragraph_key': pk,
                                          'quote': anchors.highlight(anchor), 'media_time': anchor.get('media_time'),
                                          'reason': 'Claim anchor paragraph or start time differs'})
    return {'checked_count': checked, 'located_count': checked-len(unlocated),
            'unlocated_count': len(unlocated), 'unlocated': unlocated}


def preflight(store, source_id, *, published=None, locator_root=None, registration_root=None, backup_manifest=None):
    """Check source identity and active locator epochs before Flash grouping."""
    from backend.pipeline import claim_flash_common as common
    from backend.pipeline import claim_flash_source as flash_source

    if registration_root is not None:
        root = Path(registration_root).resolve()
        manifest = m.load(root)
        if manifest['source_id'] != source_id or published is None:
            raise ValueError('registration plan does not match the Flash source and publication')
        target = evidence.snap(published)
        if {**evidence.snap(manifest['after']['path']), 'file':target['file']} != target:
            raise ValueError('registration plan differs from published body or clocks')
        with evidence.locks(source_id, record_keys=['source_documents/'+source_id]):
            m.register(store, root)

    row = store.get_record_state('source_documents', source_id)
    if not row or row['retired']:
        raise ValueError('missing source: '+source_id)
    doc = row['payload']
    registered = False
    try:
        try:
            bound = common.source_binding(doc)
        except ValueError:
            registered = register_equivalent_publication(store, row, published)
            if not registered:
                raise
            doc = store.get_record_state('source_documents', source_id)['payload']
            bound = common.source_binding(doc)
        if published is not None:
            target = evidence.snap(published)
            if {**evidence.snap(bound['path']), 'file':target['file']} != target:
                return {'status':'registration_required', 'reason':'registered source differs from published body or clocks'}
        records, sources = common.load_source(store, source_id, visual_context=True)
        source = flash_source.current_source(records, sources, source_id)
    except (ValueError, OSError) as exc:
        return {'status':'registration_required','reason':str(exc)}

    body_sha = source['body_sha256']
    stale = []
    for key, state in records.items():
        payload = state['payload']
        if key.startswith('source_fragments/') and payload.get('anchor_state') == 'replaced_by_precise_fragments':
            replacements = payload.get('replacement_fragment_ids') or []
            if replacements and all(
                records.get('source_fragments/'+fid, {}).get('payload', {}).get('source_id') == source_id
                and records['source_fragments/'+fid]['payload'].get('source_sha256') == body_sha
                for fid in replacements):
                continue
        if key.startswith('source_fragments/') and payload.get('anchor_state') not in {'source_deleted','context_only'}:
            if payload.get('source_sha256') and payload['source_sha256'] != body_sha:
                stale.append(key)
        if key.startswith('claims/') and payload.get('review_status') not in {'superseded','retired'}:
            if any(anchor.get('source_sha256') and anchor['source_sha256'] != body_sha
                   for occurrence in payload.get('occurrences', []) if anchors.own_occurrence(occurrence, doc)
                   for anchor in occurrence.get('anchors', [])):
                stale.append(key)
    census = locator_census(records, source, doc)
    stale = sorted(set(stale) | {item['object_key'] for item in census['unlocated']})
    if stale and registration_root is not None and locator_root is not None and not (Path(locator_root)/'migration.json').exists():
        direction = Path(registration_root)/'locator-direction.txt'
        if not direction.exists():
            direction.write_text(manifest['instruction'])
        prepare(store, locator_root, source_id=source_id, old_paths=[manifest['before']['path']],
                published=published, instruction_file=direction, backup_manifest=backup_manifest)
    if stale or locator_root is not None and (Path(locator_root)/'migration.json').exists():
        if locator_root is None or not (Path(locator_root)/'migration.json').exists():
            return {'status':'locator_required','record_keys':stale, **census}
        root = Path(locator_root).resolve()
        plan = load(root)
        if (plan['intent']['source_id'] != source_id
                or published is None or plan['intent']['published'] != str(Path(published).resolve())):
            raise ValueError('locator plan does not match the Flash source and publication')
        preview = m.read(root/'preview.json')
        if preview['exceptions']:
            return {'status':'locator_required','exceptions':preview['exceptions'],
                    'unlocated_count':len(preview['exceptions']),'preview_path':str(root/'preview.json')}
        with execution_locks(root):
            applied = run(store, root)
        result = preflight(store, source_id, published=published)
        if result['status'] == 'not_needed':
            result.update(status='migrated', migration_writes=applied['updated_evidence_records'] + applied['updated_claims'])
            result.pop('retained_count')
            result.pop('rebound_count')
        return result
    return {'status':'migrated' if registered else 'not_needed',
            'source_file': source['file'], 'source_body_sha256': body_sha,
            'locator_verified': True, 'rebound_count': 0, 'retained_count': census['located_count'],
            **census, 'records':records, 'sources':sources}


@contextmanager
def run_lock(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'run.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('this migration already has a worker') from exc
        try:
            yield root
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def load(root):
    p = m.read(root / 'migration.json')
    if p['schema_version'] != VERSION:
        raise ValueError('unsupported locator migration')
    for bound in [p['implementation'], p['evidence_plan'], p['preview'], *p['inputs']]:
        evidence.cm.source.verify_file(bound)
    return p


def prepare(store, root, *, source_id, old_paths, published, instruction_file,
            calibration_path=None, jev_impact=False, backup_manifest=None):
    with run_lock(root) as root:
        intent = {'source_id': source_id, 'old_paths': [str(Path(p).resolve()) for p in old_paths],
                  'published': str(Path(published).resolve()),
                  'instruction_file': str(Path(instruction_file).resolve()),
                  'calibration': str(Path(calibration_path).resolve()) if calibration_path else None}
        if backup_manifest:
            intent['backup_manifest'] = str(Path(backup_manifest).resolve())
        if jev_impact:
            intent['jev_impact'] = True
        if (root / 'migration.json').exists():
            p = load(root)
            if p['intent'] != intent:
                raise ValueError('migration intent differs; preserve the existing run')
            return m.read(root / 'preview.json')
        start = perf_counter()
        inputs = [m.binding(Path(p)) for p in
                  [*old_paths, published, instruction_file, *([calibration_path] if calibration_path else []),
                    *([backup_manifest] if backup_manifest else [])]]
        evidence.prepare(store, root / 'evidence', source_id=source_id,
                         old_paths=old_paths, published=published,
                         instruction=Path(instruction_file).read_text(), calibration_path=calibration_path,
                         jev_impact=jev_impact, backup_manifest=backup_manifest)
        report = m.read(root / 'evidence/comparison.json')
        exceptions = [
            row for row in report['fragments']
            if row['status'] not in RESOLVED
            and row['status'] not in {'question_context_only', 'paragraph_location'}
        ] + report.get('consumer_exceptions', [])
        preview = {'source_id': source_id, 'phase': 'evidence_preview',
                   'evidence_updates': len(report['updates']), 'exceptions': exceptions,
                   'claim_preview': 'prepared_after_verified_evidence_commit', 'model_calls': report.get('model_calls', 0)}
        m.immutable(root / 'preview.json', preview)
        m.immutable(root / 'migration.json', {'schema_version': VERSION, 'intent': intent,
                    'inputs': inputs, 'preview': m.binding(root / 'preview.json'), 'implementation': m.binding(Path(__file__)),
                    'evidence_plan': m.binding(root / 'evidence/plan.json')})
        load(root)  # Detect input drift even during preparation.
        m.immutable(root / 'prepare-metrics.json', {'elapsed_seconds': perf_counter() - start})
        return preview


def handoff(store, root, p):
    er = m.read(root / 'evidence/plan.json')
    ap = m.read(root / 'anchors/plan.json')
    expected = {'source_id': p['intent']['source_id'], 'evidence_run': str(root / 'evidence'),
                'published': p['intent']['published'],
                'instruction': Path(p['intent']['instruction_file']).read_text()}
    if ap['intent'] != expected:
        raise ValueError('Claim handoff differs from migration intent')
    for bound in [er['implementation'], er['records'], er['report'],
                  ap['implementation'], ap['records'], ap['report']]:
        evidence.cm.source.verify_file(bound)
    before = m.read(er['records']['path'])
    report = m.read(er['report']['path'])
    after = m.read(ap['records']['path'])
    history = store.get_change_history(er['change_set_id'])
    if report['updates'] and (not history or history['status'] != 'applied'
            or history['fingerprint_sha256'] != er['fingerprint_sha256']):
        raise ValueError('Evidence transaction not committed as planned')
    ops = {op['collection'] + '/' + op['object_id']: op for op in history['operations']} if history else {}
    if set(ops) != set(report['updates']):
        raise ValueError('Evidence write set differs')
    expected_records = dict(before)
    for key, payload in report['updates'].items():
        op = ops[key]
        revision = before[key]['revision'] + 1 if key in before else 1
        if op['after'] != {**payload, 'revision': revision}:
            raise ValueError('Evidence committed postimage differs')
        expected_records[key] = {'payload': op['after'], 'revision': revision,
                                'retired': False, 'content_sha256': op['after_sha256']}
    if after != expected_records:
        raise ValueError('Evidence → Claim handoff preimage differs')
    return er, ap


def finish(root, p, er, verification):
    ev = m.read(er['report']['path'])
    result = {'schema_version': VERSION, 'status': 'locator_migration_verified',
              'source_id': p['intent']['source_id'],
              'evidence_change_set': er['change_set_id'] if ev['updates'] else None,
              'claim_change_set': verification['change_set_id'] if verification['updated_claims'] else None,
              'updated_evidence_records': len(ev['updates']),
              'updated_claims': verification['updated_claims'], 'exceptions': [],
              'needs_content_refresh': sorted(k for k, v in ev['updates'].items()
                  if k.startswith('evidence_steps/') and v.get('support_eligibility') == 'withheld_unreviewed'),
              'model_calls': er.get('model_calls', 0), 'claim_prose_changed': False,
              'content_requalification_complete': False, 'cvp_route_migration_complete': False}
    m.immutable(root / 'result.json', result)
    # Link to detailed stage comparisons rather than copying large quotation tables.
    body = '<meta charset="utf-8"><h1>來源定位搬遷</h1><pre>' + escape(json.dumps(result, ensure_ascii=False, indent=2)) + '</pre>'
    body += '<a href="evidence/comparison.html">Evidence 對照表</a> · <a href="anchors/comparison.html">Claim 定位對照表</a>'
    m._write_bytes(root / 'result.html', body.encode())
    return result


def run(store, root):
    with run_lock(root) as root:
        p = load(root)
        if m.read(root / 'preview.json')['exceptions']:
            raise ValueError('unresolved locators: inspect preview.json; no writes performed')
        start = perf_counter()
        # apply already includes exact current SQL readback. Do not verify again.
        if not (root / 'anchors/plan.json').exists():
            evidence.apply(store, root / 'evidence')
            anchors.prepare(store, root / 'anchors', source_id=p['intent']['source_id'],
                            evidence_run=root / 'evidence', published=p['intent']['published'],
                            instruction=Path(p['intent']['instruction_file']).read_text())
        er, _ = handoff(store, root, p)
        anchors.apply(store, root / 'anchors')
        result = finish(root, p, er, m.read(root / 'anchors/verification.json'))
        if not (root / 'run-metrics.json').exists():
            m.immutable(root / 'run-metrics.json', {'elapsed_seconds': perf_counter() - start})
        return result


def verify(store, root):
    with run_lock(root) as root:
        p = load(root)
        er, _ = handoff(store, root, p)
        return finish(root, p, er, anchors.verify(store, root / 'anchors'))


def execution_locks(root):
    """Cover both stages' possible writes before committing either stage."""
    root=Path(root).resolve();p=load(root)
    er=m.read(root/'evidence/plan.json')
    records=m.read(er['records']['path']);report=m.read(er['report']['path'])
    source_id=p['intent']['source_id'];source=records['source_documents/'+source_id]['payload']
    writes=set(report['updates']) | {'source_documents/'+source_id}
    # Anchor rows are compiled after the Evidence handoff. Reserve their own
    # Claims now, including multi-source Claims with an occurrence here.
    writes.update(key for key,state in records.items() if key.startswith('claims/')
        and not state['retired'] and state['payload'].get('review_status')!='superseded'
        and any(anchors.own_occurrence(o,source) for o in state['payload'].get('occurrences',[])))
    return evidence.locks(source_id,record_keys=writes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['migrate', 'prepare', 'candidates', 'resolve', 'run', 'verify'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source-id'); parser.add_argument('--published', type=Path)
    parser.add_argument('--old-source', type=Path, action='append', default=[])
    parser.add_argument('--instruction-file', type=Path); parser.add_argument('--calibration', type=Path)
    parser.add_argument('--jev-impact', action='store_true')
    parser.add_argument('--statement-first', action='store_true')
    parser.add_argument('--backup-manifest', type=Path)
    parser.add_argument('--registration-root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--capacity-root', type=Path, help='shared Sol concurrency directory for resolve')
    args = parser.parse_args()
    if args.command == 'candidates':
        if not args.backup_manifest or not args.output:
            parser.error('candidates requires --backup-manifest and --output')
        from backend.pipeline.transcript_locator_candidates import prepare_run_packets
        print(json.dumps(prepare_run_packets(args.root, args.backup_manifest, args.output)))
        return
    if args.command == 'resolve' and (not args.backup_manifest or not args.output):
        parser.error('resolve requires --backup-manifest and --output (new migration directory)')
    from dotenv import load_dotenv
    load_dotenv()
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store = PostgresKnowledgeStore()
    if args.command == 'migrate':
        if not args.source_id or not args.published:
            parser.error('migrate requires source-id and published')
        locator = args.root if args.registration_root or (args.root/'migration.json').exists() else None
        result = preflight(store, args.source_id, published=args.published, locator_root=locator,
                           registration_root=args.registration_root, backup_manifest=args.backup_manifest)
        result = {k:v for k,v in result.items() if k not in {'records','sources'}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] in {'not_needed','migrated'} else 2
    if args.command == 'resolve':
        import os
        if args.capacity_root:
            os.environ['MEDIA_BATCH_CAPACITY_ROOT'] = str(args.capacity_root.resolve())
        if not os.environ.get('MEDIA_BATCH_CAPACITY_ROOT'):
            parser.error('resolve requires --capacity-root or MEDIA_BATCH_CAPACITY_ROOT')
        from backend.pipeline.transcript_locator_candidates import load_run_packets
        from backend.pipeline.transcript_locator_resolution import resolve_packets
        manifest, records, new, bundle = load_run_packets(args.root, args.backup_manifest)
        intent = manifest['intent']
        prior = m.read(Path(intent['calibration'])) if intent['calibration'] else {}
        calibration = resolve_packets(args.output / 'resolution', bundle, records, new, prior,
                                      statement_first=args.statement_first)
        result = prepare(store, args.output, source_id=intent['source_id'],
                         old_paths=intent['old_paths'], published=intent['published'],
                         instruction_file=intent['instruction_file'], calibration_path=calibration,
                         jev_impact=intent.get('jev_impact', False), backup_manifest=intent.get('backup_manifest'))
    elif args.command == 'prepare':
        if not all([args.source_id, args.published, args.instruction_file]):
            parser.error('prepare requires source-id, published and instruction-file')
        result = prepare(store, args.root, source_id=args.source_id, old_paths=args.old_source,
                         published=args.published, instruction_file=args.instruction_file,
                         calibration_path=args.calibration, jev_impact=args.jev_impact,
                         backup_manifest=args.backup_manifest)
    else:
        with execution_locks(args.root):
            result = (run if args.command == 'run' else verify)(store, args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    raise SystemExit(main())
