"""Reserve one program-selected Claim identity on the existing SQL ledger.

Reservations grant no content approval. Retired and non-Claim object identities
also occupy their names; the final Claim transaction still checks absence.
"""
from pathlib import Path
import re

from backend.api.canonical_repository.postgres_store import (
    POSTGRES_APPLY_ADVISORY_LOCK_KEY, KNOWLEDGE_COLLECTIONS, canonical_json, sha256_json,
)

KIND = 'claim_identity_allocation'


def reserve(store, *, action_id, source_id, target_id, root):
    match = re.fullmatch(r'(.+-CL)(\d+)', target_id)
    prefix = match[1] if match else target_id + '-CL'
    identity = {'kind': KIND, 'action_id': action_id, 'source_id': source_id,
                'claim_prefix': prefix, 'root': str(Path(root).resolve())}
    run_id = 'CMID-' + sha256_json(action_id)
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        row = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s', (run_id,)).fetchone()
        if row:
            if any(row[0].get(k) != v for k, v in identity.items()):
                raise ValueError('Claim identity reservation cannot change its action/source/root')
            return row[0]['claim_id']
        # The complete registered identity set for this prefix, including
        # retirements and other collections, rather than the model's readset.
        pattern = prefix.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        used = {row[0] for row in cursor.execute(
            "SELECT object_id FROM wang_knowledge.objects WHERE collection='claims' AND object_id LIKE %s", (pattern,)).fetchall()}
        used.update(row[0]['claim_id'] for row in cursor.execute(
            "SELECT metadata FROM wang_knowledge.pipeline_runs WHERE batch_id=%s AND stage='cvp_group' AND metadata->>'kind'=%s",
            (prefix, KIND)).fetchall())
        number = max((int(oid[len(prefix):]) for oid in used
                      if oid.startswith(prefix) and oid[len(prefix):].isdigit()), default=0) + 1
        claim_id = prefix + f'{number:03d}'
        while cursor.execute('SELECT 1 FROM wang_knowledge.objects WHERE collection=ANY(%s) AND object_id=%s LIMIT 1',
                             (sorted(KNOWLEDGE_COLLECTIONS), claim_id)).fetchone():
            number += 1
            claim_id = prefix + f'{number:03d}'
        value = {**identity, 'claim_id': claim_id, 'content_approval': False}
        cursor.execute("""INSERT INTO wang_knowledge.pipeline_runs
            (run_id,batch_id,subject_kind,subject_id,stage,trigger,status,metadata,finished_at)
            VALUES (%s,%s,'batch',%s,'cvp_group','cli','succeeded',%s::jsonb,now())""",
            (run_id, prefix, target_id, canonical_json(value)))
        return claim_id


def assert_reserved(cursor, request, allocations, *, root):
    if not allocations:
        return
    row = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',
                         ('CMID-' + sha256_json(request.action_id),)).fetchone()
    expected = {row[0]['source_id']: row[0]['claim_id']} if row else None
    if (expected != allocations or row[0].get('action_id') != request.action_id
            or row[0].get('root') != str(Path(root).resolve())):
        raise ValueError('Claim creation must use this action\'s SQL-reserved identity')
