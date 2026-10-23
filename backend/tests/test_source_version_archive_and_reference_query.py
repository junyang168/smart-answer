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


def test_reference_query_one_exact_disjunction_with_json_escaped_ids():
    class Cursor:
        def execute(self, sql, params):
            self.sql, self.params = sql, params
        def fetchall(self):
            return [("claims", "CL", {"id": "CL"})]
    cursor = Cursor()
    ids = ['A"B', "A\\B", "A", "A"]
    result = PostgresKnowledgeStore._reference_consumers(cursor, ids, ["claims"])
    assert result == [("claims", "CL", {"id": "CL"})]
    assert cursor.sql.count("@?") == 1
    assert cursor.params == (["claims"], "$.** ? (" + " || ".join("@ == " + json.dumps(x) for x in ids) + ")")
    assert "retired_at IS NULL" in cursor.sql
    assert PostgresKnowledgeStore._reference_consumers(cursor, [], ["claims"]) == []


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
