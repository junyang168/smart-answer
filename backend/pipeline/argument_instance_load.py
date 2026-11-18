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


def load(conn, candidate_path, *, receipt_path, fidelity_path, run_dir, replaces=None):
    from backend.api.canonical_repository.postgres_store import normalize_package, record_content_sha
    from backend.pipeline.knowledge_package_merge import validate_merged_package
    package = json.loads(Path(candidate_path).read_text())
    receipt = json.loads(Path(receipt_path).read_text())
    if receipt['corrected_sha256'] != sha(candidate_path):
        raise ValueError('corrections receipt does not bind this candidate file')
    fidelity = json.loads(Path(fidelity_path).read_text())
    open_items = [f['id'] for f in fidelity['findings'] + fidelity['needs_look'] if not f.get('ruling')]
    if open_items:
        raise ValueError(f'fidelity check has {len(open_items)} items without a ruling: {open_items[:5]}')
    validate_merged_package(package, native_relations=True)
    records = normalize_package(package)
    if len(records.get('source_documents', {})) != 1:
        raise ValueError('one lecture per ChangeSet')
    source_id = next(iter(records['source_documents']))
    current = holder(conn, source_id)
    if current != replaces:
        raise ValueError(f'{source_id} is held by {current}; pass replaces={current!r} to replace it'
                         if current else f'{source_id} is not in the instance; nothing to replace')
    candidate_sha = sha(candidate_path)
    change_set = f'argument-layer-{source_id}-{candidate_sha[:12]}'
    metadata = {'run_dir': str(run_dir), 'candidate_path': str(candidate_path), 'candidate_sha256': candidate_sha,
                'gpt_candidate_sha256': receipt['candidate_sha256'], 'corrections_receipt': str(receipt_path),
                'corrections_receipt_sha256': sha(receipt_path), 'fidelity_report': str(fidelity_path),
                'fidelity_report_sha256': sha(fidelity_path), 'model_calls': 0}
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
    rows = conn.execute('''SELECT o.collection, o.object_id, o.revision, o.payload FROM wang_knowledge.object_versions v
        JOIN wang_knowledge.objects o USING (collection, object_id)
        WHERE v.change_set_id=%s AND v.revision=1 AND o.retired_at IS NULL FOR UPDATE OF o''', (old_change_set,)).fetchall()
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
    p.add_argument('--replaces', help='the ChangeSet that holds this lecture now; its records are retired')
    a = p.parse_args()
    load_dotenv('.env')
    run = Path(a.run)
    candidate = run / 'creation' / 'candidate-owner-corrected.json'
    with psycopg.connect(database_url()) as conn:
        print(json.dumps(load(conn, candidate, receipt_path=candidate.with_suffix('.receipt.json'),
                              fidelity_path=run / 'fidelity' / 'fidelity-final.json', run_dir=run,
                              replaces=a.replaces), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
