"""Correct a lecture already in the knowledge instance, without regenerating it.

Rebuild design §三: a problem found in one lecture is repaired by a programmatic
correction, not a new model run. A correction file names each change against the
exact revision it was read from:

- ``update``: replace the named fields of one record (``set``);
- ``retire``: retire one record.

All changes go in one ChangeSet (source_kind ``argument_layer_correction``) with a
new revision per record in ``object_versions``. Before commit the lecture's live
records are checked for broken references (a relation, Claim anchor or Evidence
link pointing at a retired or missing record); after commit every change is read
back. No model call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb

ID_FIELDS = {'claims': 'claim_id', 'evidence_steps': 'evidence_step_id', 'observations': 'observation_id',
             'position_nodes': 'position_id', 'questions': 'question_id', 'knowledge_relations': 'relation_id',
             'source_fragments': 'fragment_id', 'source_documents': 'source_id'}


def dangling(records: dict[str, list[dict]]) -> list[str]:
    """References in one lecture's live records that point at no live record."""
    live = {r.get(ID_FIELDS[c]) for c, rows in records.items() if c in ID_FIELDS for r in rows}
    out = []
    def need(owner, ref):
        if ref and ref not in live:
            out.append(f'{owner} -> {ref}')
    for r in records.get('knowledge_relations', []):
        need(r['relation_id'], r['from_id']); need(r['relation_id'], r['to_id'])
    for c in ('claims', 'evidence_steps', 'observations', 'position_nodes', 'questions'):
        for r in records.get(c, []):
            oid = r[ID_FIELDS[c]]
            for f in r.get('source_fragment_ids') or []: need(oid, f)
            for f in ('evidence_step_ids', 'produced_claim_ids', 'opposed_position_ids', 'answer_claim_ids'):
                for x in r.get(f) or []: need(oid, x)
            for o in r.get('occurrences') or []:
                for a in o.get('anchors') or []:
                    for f in ('evidence_id', 'observation_id', 'source_fragment_id'): need(oid, a.get(f))
    return out


def correct(conn, lecture_change_set: str, corrections_path: Path) -> dict:
    from backend.api.canonical_repository.postgres_store import record_content_sha
    from backend.pipeline.chapter_slide_claims import instance_records
    raw = Path(corrections_path).read_bytes()
    corrections = json.loads(raw)
    if corrections['lecture_change_set_id'] != lecture_change_set:
        raise ValueError('corrections are for another lecture')
    digest = hashlib.sha256(raw).hexdigest()
    change_set = f"argument-correction-{lecture_change_set.removeprefix('argument-layer-')[:40]}-{digest[:12]}"
    written = []
    with conn.transaction():
        conn.execute('''INSERT INTO wang_knowledge.change_sets
            (change_set_id,fingerprint_sha256,package_id,source_kind,source_sha256,status,metadata,applied_at)
            VALUES (%s,%s,%s,'argument_layer_correction',%s,'applied',%s,now())''',
            (change_set, digest, lecture_change_set, digest,
             Jsonb({'lecture_change_set_id': lecture_change_set, 'corrections_path': str(corrections_path),
                    'corrections_sha256': digest, 'authority': corrections['authority'], 'model_calls': 0})))
        for op in corrections['operations']:
            row = conn.execute('''SELECT revision, content_sha256, payload FROM wang_knowledge.objects
                WHERE collection=%s AND object_id=%s AND retired_at IS NULL FOR UPDATE''',
                (op['collection'], op['object_id'])).fetchone()
            if not row:
                raise ValueError(f"{op['object_id']}: not a live record")
            revision, content, payload = row
            if content.strip() != op['before_sha256']:
                raise ValueError(f"{op['object_id']}: changed since the correction was written")
            new = dict(payload, revision=revision + 1)
            if op['op'] == 'update':
                new.update(op['set'])
                retired = False
            elif op['op'] == 'retire':
                new['review_status'] = 'retired'
                retired = True
            else:
                raise ValueError(f"unknown operation {op['op']}")
            after = record_content_sha(new)
            conn.execute(f'''UPDATE wang_knowledge.objects SET revision=%s, content_sha256=%s, payload=%s, updated_at=now()
                {", review_status='retired', retired_at=now()" if retired else ''}
                WHERE collection=%s AND object_id=%s''', (revision + 1, after, Jsonb(new), op['collection'], op['object_id']))
            conn.execute('''INSERT INTO wang_knowledge.object_versions (collection,object_id,revision,content_sha256,payload,change_set_id)
                VALUES (%s,%s,%s,%s,%s,%s)''', (op['collection'], op['object_id'], revision + 1, after, Jsonb(new), change_set))
            written.append((op['collection'], op['object_id'], revision + 1, after, retired))
        broken = dangling(instance_records(conn, lecture_change_set)[0])
        if broken:
            raise ValueError(f'correction would leave broken references: {broken[:5]}')
    for collection, oid, revision, after, retired in written:
        back = conn.execute('''SELECT o.revision, o.content_sha256, o.retired_at IS NOT NULL FROM wang_knowledge.objects o
            JOIN wang_knowledge.object_versions v USING (collection, object_id, revision)
            WHERE o.collection=%s AND o.object_id=%s AND v.change_set_id=%s''', (collection, oid, change_set)).fetchone()
        if not back or back[0] != revision or back[1].strip() != after or back[2] != retired:
            raise ValueError(f'read-back differs for {oid}')
    return {'change_set_id': change_set, 'operations': len(written), 'read_back': 'matches'}


def main():
    from dotenv import load_dotenv
    import psycopg
    from backend.pipeline.argument_review import database_url
    p = argparse.ArgumentParser(description='Correct a lecture already in the knowledge instance')
    p.add_argument('--lecture-change-set', required=True)
    p.add_argument('--corrections', required=True, type=Path)
    a = p.parse_args()
    load_dotenv('.env')
    with psycopg.connect(database_url()) as conn:
        print(json.dumps(correct(conn, a.lecture_change_set, a.corrections), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
