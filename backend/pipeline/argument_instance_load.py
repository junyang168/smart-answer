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
- this source is already in the instance. Retiring an earlier version in the same
  ChangeSet (design §三 step 2) is added when a lecture is first rewritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(conn, candidate_path, *, receipt_path, fidelity_path, run_dir):
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
    present = conn.execute("SELECT count(*) FROM wang_knowledge.objects WHERE collection='source_documents' AND object_id=%s",
                           (source_id,)).fetchone()[0]
    if present:
        raise ValueError(f'{source_id} is already in the instance; retiring a version is not built yet')
    candidate_sha = sha(candidate_path)
    change_set = f'argument-layer-{source_id}-{candidate_sha[:12]}'
    metadata = {'run_dir': str(run_dir), 'candidate_path': str(candidate_path), 'candidate_sha256': candidate_sha,
                'gpt_candidate_sha256': receipt['candidate_sha256'], 'corrections_receipt': str(receipt_path),
                'corrections_receipt_sha256': sha(receipt_path), 'fidelity_report': str(fidelity_path),
                'fidelity_report_sha256': sha(fidelity_path), 'model_calls': 0}
    with conn.transaction():
        conn.execute('''INSERT INTO wang_knowledge.change_sets
            (change_set_id,fingerprint_sha256,package_id,source_kind,source_sha256,status,metadata,applied_at)
            VALUES (%s,%s,%s,'argument_layer_rebuild',%s,'applied',%s,now())''',
            (change_set, candidate_sha, package['package_id'], candidate_sha, Jsonb(metadata)))
        written = {}
        for col, values in records.items():
            for oid, payload in values.items():
                content = record_content_sha(payload)
                conn.execute('''INSERT INTO wang_knowledge.objects
                    (collection,object_id,revision,review_status,visibility,content_sha256,payload)
                    VALUES (%s,%s,1,'candidate','internal',%s,%s)''', (col, oid, content, Jsonb(payload)))
                conn.execute('''INSERT INTO wang_knowledge.object_versions
                    (collection,object_id,revision,content_sha256,payload,change_set_id)
                    VALUES (%s,%s,1,%s,%s,%s)''', (col, oid, content, Jsonb(payload), change_set))
                written[col] = written.get(col, 0) + 1
    back = dict(conn.execute('''SELECT o.collection, count(*) FROM wang_knowledge.objects o
        JOIN wang_knowledge.object_versions v USING (collection, object_id, revision)
        WHERE v.change_set_id=%s AND o.content_sha256=v.content_sha256 GROUP BY 1''', (change_set,)).fetchall())
    if back != written:
        raise ValueError(f'read-back differs: wrote {written}, read {back}')
    return {'change_set_id': change_set, 'source_id': source_id, 'written': written, 'read_back': 'matches'}


def main():
    from dotenv import load_dotenv
    import psycopg
    from backend.pipeline.argument_review import database_url
    p = argparse.ArgumentParser(description='Write one validated lecture into the new knowledge instance')
    p.add_argument('--run', required=True, help='the lecture run directory')
    a = p.parse_args()
    load_dotenv('.env')
    run = Path(a.run)
    candidate = run / 'creation' / 'candidate-owner-corrected.json'
    with psycopg.connect(database_url()) as conn:
        print(json.dumps(load(conn, candidate, receipt_path=candidate.with_suffix('.receipt.json'),
                              fidelity_path=run / 'fidelity' / 'fidelity-final.json', run_dir=run), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
