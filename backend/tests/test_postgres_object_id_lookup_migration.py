"""Existing migration 005 must upgrade a populated store without changing content."""
from pathlib import Path

import psycopg
import pytest

from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import (
    _temporary_postgres, postgres_store,
)


MIGRATION = Path(__file__).parents[1] / 'api/canonical_repository/migrations/005_global_object_id_uniqueness.sql'
INDEXES = ('objects_object_id_global_unique_idx', 'source_documents_current_transcript_identity_unique_idx')


def legacy_store(store, *, duplicate_id=False, duplicate_source=False):
    records = {
        ('claims', 'C'): {'claim_id': 'C', 'revision': 1, 'evidence_step_ids': ['E']},
        ('evidence_steps', 'E'): {'evidence_step_id': 'E', 'revision': 1, 'source_id': 'S'},
        ('source_documents', 'S'): {'source_id': 'S', 'revision': 1,
                                  'source_type': 'sermon_transcript', 'transcript_id': 'T'},
    }
    if duplicate_id:
        records['observations', 'C'] = {'observation_id': 'C', 'revision': 1}
    if duplicate_source:
        records['source_documents', 'S2'] = {**records['source_documents', 'S'], 'source_id': 'S2'}
    with store.connect() as conn:
        for index in INDEXES:
            conn.execute('DROP INDEX wang_knowledge.' + index)
        for (collection, oid), payload in records.items():
            conn.execute('''INSERT INTO wang_knowledge.objects
                (collection,object_id,revision,review_status,visibility,content_sha256,payload)
                VALUES (%s,%s,1,'candidate','internal',%s,%s::jsonb)''',
                (collection, oid, record_content_sha(payload), canonical_json(payload)))
    return records


def index_names(store):
    with store.connect() as conn:
        return {row[0] for row in conn.execute('''SELECT indexname FROM pg_indexes
            WHERE schemaname='wang_knowledge' AND tablename='objects' ''').fetchall()}


def test_populated_upgrade_is_idempotent_and_preserves_complete_context(postgres_store):
    store = postgres_store
    records = legacy_store(store)
    with store.connect() as conn:
        before = store._existing_cvp_context(conn, {'claims': {'C': records['claims', 'C']}})
    assert set(before) == set(records)
    for _ in range(2):
        with store.connect() as conn:
            conn.execute(MIGRATION.read_text())
    assert set(INDEXES) <= index_names(store)
    with store.connect() as conn:
        after = store._existing_cvp_context(conn, {'claims': {'C': records['claims', 'C']}})
        index = conn.execute('''SELECT i.indisunique,i.indisvalid,i.indisready,
            pg_get_indexdef(i.indexrelid) FROM pg_index i
            WHERE i.indexrelid='wang_knowledge.objects_object_id_global_unique_idx'::regclass''').fetchone()
    assert after == before
    assert index[:3] == (True, True, True)
    assert '(object_id)' in index[3]


@pytest.mark.parametrize('fault', ['duplicate_id', 'duplicate_source'])
def test_legacy_identity_conflict_rolls_back_both_indexes(postgres_store, fault):
    store = postgres_store
    legacy_store(store, **{fault: True})
    with store.connect() as conn:
        before = conn.execute('SELECT collection,object_id,content_sha256 FROM wang_knowledge.objects ORDER BY 1,2').fetchall()
    with pytest.raises(psycopg.errors.RaiseException):
        with store.connect() as conn:
            conn.execute(MIGRATION.read_text())
    assert not set(INDEXES) & index_names(store)
    with store.connect() as conn:
        assert conn.execute('SELECT collection,object_id,content_sha256 FROM wang_knowledge.objects ORDER BY 1,2').fetchall() == before


def test_upgraded_lookup_still_rejects_cross_collection_id_reuse(postgres_store):
    store = postgres_store
    legacy_store(store)
    with store.connect() as conn:
        conn.execute(MIGRATION.read_text())
    payload = {'observation_id': 'C', 'revision': 1}
    with pytest.raises(psycopg.errors.UniqueViolation):
        with store.connect() as conn:
            conn.execute('''INSERT INTO wang_knowledge.objects
                (collection,object_id,revision,review_status,visibility,content_sha256,payload)
                VALUES ('observations','C',1,'candidate','internal',%s,%s::jsonb)''',
                (record_content_sha(payload), canonical_json(payload)))
