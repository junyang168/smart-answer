import hashlib
import json

import pytest

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline.source_reconciliation_runner import archive_source_version
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha


def test_exact_archive_preserves_original_bytes_and_reuses_without_overwrite(tmp_path):
    raw = b'[{"text":"old"}]'
    digest = hashlib.sha256(raw).hexdigest()
    bound = {"path": str(tmp_path / "live.json"), "file_sha256": digest}
    snapshots = {bound["path"] + ":" + digest: raw.hex()}
    result = archive_source_version(bound, tmp_path / "versions", physical_snapshots=snapshots)
    assert (tmp_path / "versions" / (digest + ".json")).read_bytes() == raw
    assert archive_source_version(bound, tmp_path / "versions", physical_snapshots=snapshots) == result
    (tmp_path / "versions" / (digest + ".json")).write_bytes(b"foreign")
    with pytest.raises(ValueError, match="archive drift"):
        archive_source_version(bound, tmp_path / "versions", physical_snapshots=snapshots)


def test_archive_rejects_wrong_saved_sha_before_creation(tmp_path):
    bound = {"path": str(tmp_path / "live.json"), "file_sha256": "0" * 64}
    with pytest.raises(ValueError):
        archive_source_version(bound, tmp_path / "versions", physical_snapshots={bound["path"] + ":" + bound["file_sha256"]: b"bad".hex()})
    assert not (tmp_path / "versions").exists()


def test_reference_query_on_few_rows_is_one_pass_with_json_escaped_ids():
    class Cursor:
        def __init__(self):
            self.calls = []
        def execute(self, sql, params):
            self.calls.append((sql, params))
        def fetchone(self):
            return (10,)
        def fetchall(self):
            return [("claims", "CL", {"id": "CL"})]
    cursor = Cursor()
    ids = ['A"B', "A\\B", "A", "A"]
    result = PostgresKnowledgeStore._reference_consumers(cursor, ids, ["claims"])
    assert result == [("claims", "CL", {"id": "CL"})]
    sql, params = cursor.calls[-1]
    assert len(cursor.calls) == 2 and "jsonb_path_query" in sql and "retired_at IS NULL" in sql
    # The strategy probe reads table statistics; count(*) scanned the table (#459).
    assert 'pg_stats' in cursor.calls[0][0] and 'count(' not in cursor.calls[0][0]
    # Ids travel as bound text, never spliced into the jsonpath.
    assert params == (["claims"], sorted(set(ids)))
    assert PostgresKnowledgeStore._reference_consumers(cursor, [], ["claims"]) == []


def test_single_pass_and_indexed_batches_find_the_same_rows(postgres_store, monkeypatch):
    from backend.api.canonical_repository import postgres_store as module
    values = ['A"B', 'A\\B', 'A', 'A-extra', 'prefix A suffix'] + [f'needle-{i}' for i in range(12)]
    with postgres_store.connect() as conn, conn.cursor() as cursor:
        for i, value in enumerate(values):
            payload = {"claim_id": f"CL-{i}", "nested": [{"ids": [value]}], "revision": 1}
            cursor.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload) VALUES ('claims',%s,1,%s,%s::jsonb)",
                           (payload['claim_id'], record_content_sha(payload), canonical_json(payload)))
        ids = values[:3] + values[5:] + ['absent']
        single = PostgresKnowledgeStore._reference_consumers(cursor, ids, ['claims'])
        monkeypatch.setattr(module, "SINGLE_PASS_REFERENCE_ROWS", 0)
        batched = PostgresKnowledgeStore._reference_consumers(cursor, ids, ['claims'])
        assert single == batched
        assert sorted(row[1] for row in single) == sorted(f'CL-{i}' for i in [0, 1, 2] + list(range(5, 17)))


def test_reference_disjunction_matches_previous_exact_predicates_in_real_sql(postgres_store):
    values = ['A"B', 'A\\B', 'A', 'A-extra', 'foreign']
    with postgres_store.connect() as conn, conn.cursor() as cursor:
        for i, value in enumerate(values):
            payload = {"claim_id": f"CL-{i}", "statement": value, "nested": {"source_id": value}, "revision": 1}
            cursor.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload) VALUES ('claims',%s,1,%s,%s::jsonb)",
                           (payload['claim_id'], record_content_sha(payload), canonical_json(payload)))
        ids = values[:3] + [values[0]]
        actual = PostgresKnowledgeStore._reference_consumers(cursor, ids, ['claims'])
        cursor.execute("SELECT collection,object_id,payload FROM wang_knowledge.objects WHERE retired_at IS NULL AND collection=ANY(%s) AND (" +
                       " OR ".join("payload @? %s::jsonpath" for _ in ids) + ") ORDER BY collection,object_id",
                       (['claims'], *['$.** ? (@ == ' + json.dumps(value) + ')' for value in ids]))
        assert actual == cursor.fetchall()
        assert [row[1] for row in actual] == ['CL-0', 'CL-1', 'CL-2']

