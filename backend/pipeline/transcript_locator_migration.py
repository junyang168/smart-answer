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
            calibration_path=None, jev_impact=False):
    with run_lock(root) as root:
        intent = {'source_id': source_id, 'old_paths': [str(Path(p).resolve()) for p in old_paths],
                  'published': str(Path(published).resolve()),
                  'instruction_file': str(Path(instruction_file).resolve()),
                  'calibration': str(Path(calibration_path).resolve()) if calibration_path else None}
        if jev_impact:
            intent['jev_impact'] = True
        if (root / 'migration.json').exists():
            p = load(root)
            if p['intent'] != intent:
                raise ValueError('migration intent differs; preserve the existing run')
            return m.read(root / 'preview.json')
        start = perf_counter()
        inputs = [m.binding(Path(p)) for p in
                  [*old_paths, published, instruction_file, *([calibration_path] if calibration_path else [])]]
        evidence.prepare(store, root / 'evidence', source_id=source_id,
                         old_paths=old_paths, published=published,
                         instruction=Path(instruction_file).read_text(), calibration_path=calibration_path,
                         jev_impact=jev_impact)
        report = m.read(root / 'evidence/comparison.json')
        exceptions = [row for row in report['fragments'] if row['status'] not in RESOLVED]
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
    parser.add_argument('command', choices=['prepare', 'run', 'verify'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source-id'); parser.add_argument('--published', type=Path)
    parser.add_argument('--old-source', type=Path, action='append', default=[])
    parser.add_argument('--instruction-file', type=Path); parser.add_argument('--calibration', type=Path)
    parser.add_argument('--jev-impact', action='store_true')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store = PostgresKnowledgeStore()
    if args.command == 'prepare':
        if not all([args.source_id, args.published, args.instruction_file]):
            parser.error('prepare requires source-id, published and instruction-file')
        result = prepare(store, args.root, source_id=args.source_id, old_paths=args.old_source,
                         published=args.published, instruction_file=args.instruction_file,
                         calibration_path=args.calibration, jev_impact=args.jev_impact)
    else:
        with execution_locks(args.root):
            result = (run if args.command == 'run' else verify)(store, args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
