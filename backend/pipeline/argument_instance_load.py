"""Write one validated lecture into the new knowledge instance, in one transaction.

Rebuild design §三: a lecture's argument layer goes into ``smart_answer_knowledge_v2``
(``ARGUMENT_DATABASE_URL``) as one ChangeSet, read back before the command reports
success. The ChangeSet records where the records came from: the run directory, the
candidate GPT wrote, the owner's corrections receipt and the fidelity report, each
with its SHA.

It refuses to write when:
- the URL is the current knowledge database (``argument_review.database_url``);
- the corrections receipt does not bind this exact file;
- the fidelity report has findings or needs-a-look items not yet ruled
  (validation must pass before a lecture is written);
- this source is already in the instance and ``replaces`` does not name the
  ChangeSet that holds it now.

Replacing a lecture (design §三 step 2): every live record the old ChangeSet wrote
is retired in the new ChangeSet, as a new revision marked retired, the way
``argument_instance_correct`` retires one; a record the new run writes under the
same ID (the source document) gets a new revision instead. The new ChangeSet's
metadata names the one it replaces; the old ChangeSet and its versions stay in the
history, and the lecture is held by whichever ChangeSet wrote the source
document's current revision (``holder``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(conn, candidate_path, *, receipt_path, fidelity_path, run_dir, replaces=None, shape_path=None):
    from backend.api.canonical_repository.postgres_store import normalize_package, record_content_sha
    from backend.pipeline.knowledge_package_merge import validate_merged_package
    from backend.pipeline import argument_corrections, argument_fidelity, argument_shape
    from backend.pipeline.argument_review_binding import require_binding, sha as raw_sha
    candidate_raw = Path(candidate_path).read_bytes()
    receipt_raw = Path(receipt_path).read_bytes()
    receipt = json.loads(receipt_raw)
    original_raw = argument_corrections.verify(candidate_raw, receipt)
    original = json.loads(original_raw)
    package = json.loads(candidate_raw)
    fidelity_raw = Path(fidelity_path).read_bytes()
    fidelity = json.loads(fidelity_raw)
    require_binding(fidelity, original_raw, argument_fidelity.records(original), label='fidelity')
    open_items = [f['id'] for f in fidelity['findings'] + fidelity['needs_look'] if not f.get('ruling')]
    if open_items:
        raise ValueError(f'fidelity check has {len(open_items)} items without a ruling: {open_items[:5]}')
    if shape_path is not None:
        shape_raw = Path(shape_path).read_bytes()
        shape = json.loads(shape_raw)
        require_binding(shape, original_raw, argument_shape.rows(original), label='story')
        open_shape = [f['id'] for f in shape['findings'] + shape['needs_look'] if not f.get('ruling')]
        if open_shape:
            raise ValueError(f'story check has {len(open_shape)} Claims without a ruling: {open_shape[:5]}')
    validate_merged_package(package, native_relations=True)
    records = normalize_package(package)
    if len(records.get('source_documents', {})) != 1:
        raise ValueError('one lecture per ChangeSet')
    source_id = next(iter(records['source_documents']))
    current = holder(conn, source_id)
    if current != replaces:
        raise ValueError(f'{source_id} is held by {current}; pass replaces={current!r} to replace it'
                         if current else f'{source_id} is not in the instance; nothing to replace')
    candidate_sha = raw_sha(candidate_raw)
    change_set = f'argument-layer-{source_id}-{candidate_sha[:12]}'
    metadata = {'run_dir': str(run_dir), 'candidate_path': str(candidate_path), 'candidate_sha256': candidate_sha,
                'gpt_candidate_sha256': receipt['candidate_sha256'], 'corrections_receipt': str(receipt_path),
                'corrections_receipt_sha256': raw_sha(receipt_raw), 'fidelity_report': str(fidelity_path),
                'fidelity_report_sha256': raw_sha(fidelity_raw), 'model_calls': 0}
    if shape_path is not None:
        metadata.update(shape_report=str(shape_path), shape_report_sha256=raw_sha(shape_raw))
    if replaces:
        metadata['replaces'] = replaces
    with conn.transaction():
        conn.execute('''INSERT INTO wang_knowledge.change_sets
            (change_set_id,fingerprint_sha256,package_id,source_kind,source_sha256,status,metadata,applied_at)
            VALUES (%s,%s,%s,'argument_layer_rebuild',%s,'applied',%s,now())''',
            (change_set, candidate_sha, package['package_id'], candidate_sha, Jsonb(metadata)))
        retired = retire_lecture(conn, replaces, change_set, keep={(c, o) for c, v in records.items() for o in v}) if replaces else {}
        written = {}
        for col, values in records.items():
            for oid, payload in values.items():
                row = conn.execute('''SELECT revision FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s
                    FOR UPDATE''', (col, oid)).fetchone()
                if row and not replaces:
                    raise ValueError(f'{oid} is already in the instance')
                revision = row[0] + 1 if row else 1
                payload = dict(payload, revision=revision)
                content = record_content_sha(payload)
                if row:
                    conn.execute('''UPDATE wang_knowledge.objects SET revision=%s, review_status='candidate',
                        content_sha256=%s, payload=%s, updated_at=now(), retired_at=NULL
                        WHERE collection=%s AND object_id=%s''', (revision, content, Jsonb(payload), col, oid))
                else:
                    conn.execute('''INSERT INTO wang_knowledge.objects
                        (collection,object_id,revision,review_status,visibility,content_sha256,payload)
                        VALUES (%s,%s,1,'candidate','internal',%s,%s)''', (col, oid, content, Jsonb(payload)))
                conn.execute('''INSERT INTO wang_knowledge.object_versions
                    (collection,object_id,revision,content_sha256,payload,change_set_id)
                    VALUES (%s,%s,%s,%s,%s,%s)''', (col, oid, revision, content, Jsonb(payload), change_set))
                written[col] = written.get(col, 0) + 1
    back = dict(conn.execute('''SELECT o.collection, count(*) FROM wang_knowledge.objects o
        JOIN wang_knowledge.object_versions v USING (collection, object_id, revision)
        WHERE v.change_set_id=%s AND o.content_sha256=v.content_sha256 AND o.retired_at IS NULL GROUP BY 1''',
        (change_set,)).fetchall())
    if back != written:
        raise ValueError(f'read-back differs: wrote {written}, read {back}')
    if replaces:
        leftovers = conn.execute('''SELECT o.collection,o.object_id FROM wang_knowledge.objects o
            JOIN wang_knowledge.object_versions current USING(collection,object_id,revision)
            WHERE o.retired_at IS NULL AND current.change_set_id<>%s AND EXISTS (
                SELECT 1 FROM wang_knowledge.object_versions old WHERE old.collection=o.collection
                AND old.object_id=o.object_id AND old.change_set_id=%s)''', (change_set, replaces)).fetchall()
        if leftovers:
            raise ValueError(f'read-back: old lecture still has live records: {leftovers[:5]}')
    if replaces and holder(conn, source_id) != change_set:
        raise ValueError('read-back: the source is not held by the new ChangeSet')
    out = {'change_set_id': change_set, 'source_id': source_id, 'written': written, 'read_back': 'matches'}
    if replaces:
        out.update(replaces=replaces, retired=retired)
    return out


def holder(conn, source_id):
    """The ChangeSet that wrote the current revision of this lecture's source document, or None."""
    row = conn.execute('''SELECT v.change_set_id FROM wang_knowledge.objects o
        JOIN wang_knowledge.object_versions v USING (collection, object_id, revision)
        WHERE o.collection='source_documents' AND o.object_id=%s AND o.retired_at IS NULL''', (source_id,)).fetchone()
    return row[0] if row else None


def retire_lecture(conn, old_change_set, change_set, keep):
    """Retire every live record the old ChangeSet wrote, except those the new run rewrites."""
    from backend.api.canonical_repository.postgres_store import record_content_sha
    rows = conn.execute('''SELECT o.collection, o.object_id, o.revision, o.payload FROM wang_knowledge.objects o
        WHERE o.retired_at IS NULL AND EXISTS (
            SELECT 1 FROM wang_knowledge.object_versions v WHERE v.collection=o.collection AND v.object_id=o.object_id
            AND v.change_set_id=%s) FOR UPDATE OF o''', (old_change_set,)).fetchall()
    retired = {}
    for col, oid, revision, payload in rows:
        if (col, oid) in keep:
            continue
        new = dict(payload, revision=revision + 1, review_status='retired')
        content = record_content_sha(new)
        conn.execute('''UPDATE wang_knowledge.objects SET revision=%s, content_sha256=%s, payload=%s, updated_at=now(),
            review_status='retired', retired_at=now() WHERE collection=%s AND object_id=%s''',
            (revision + 1, content, Jsonb(new), col, oid))
        conn.execute('''INSERT INTO wang_knowledge.object_versions (collection,object_id,revision,content_sha256,payload,change_set_id)
            VALUES (%s,%s,%s,%s,%s,%s)''', (col, oid, revision + 1, content, Jsonb(new), change_set))
        retired[col] = retired.get(col, 0) + 1
    return retired


def main():
    from dotenv import load_dotenv
    import psycopg
    from backend.pipeline.argument_review import database_url
    p = argparse.ArgumentParser(description='Write one validated lecture into the new knowledge instance')
    p.add_argument('--run', required=True, help='the lecture run directory')
    p.add_argument('--skip-shape', action='store_true', help='lectures loaded before the story check existed')
    p.add_argument('--replaces', help='the ChangeSet that holds this lecture now; its records are retired')
    a = p.parse_args()
    load_dotenv('.env')
    run = Path(a.run)
    candidate = run / 'creation' / 'candidate-owner-corrected.json'
    shape = run / 'shape' / 'shape-final.json'
    if not a.skip_shape and not shape.exists():
        raise SystemExit(f'{shape} is missing: run `argument_shape --prefilter` then `--judge` first')
    with psycopg.connect(database_url()) as conn:
        print(json.dumps(load(conn, candidate, shape_path=None if a.skip_shape else shape, receipt_path=candidate.with_suffix('.receipt.json'),
                              fidelity_path=run / 'fidelity' / 'fidelity-final.json', run_dir=run,
                              replaces=a.replaces), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
