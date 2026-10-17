"""Read-only scheduling uses complete source membership and exact current pins."""
import hashlib
import json
from copy import deepcopy

from backend.pipeline.source_heading_inventory import build_source_heading_inventory
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.source_projection import project_script


class Store:
    def __init__(self):
        self.rows = {}

    def add(self, collection, ident, payload):
        self.rows[collection, ident] = {"payload": payload, "revision": 1,
                                       "content_sha256": record_content_sha(payload), "retired": False}

    def get_record_state(self, collection, ident):
        return deepcopy(self.rows.get((collection, ident)))

    def list_records(self, collection):
        return [deepcopy(row["payload"]) for (col, _), row in self.rows.items()
                if col == collection and not row["retired"]]


def source(store, root, sid, *, stage="script_published", headings=False, text="原話。"):
    path = root / stage / f"{sid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"index": "p1", "text": text}]
    if headings:
        rows.insert(0, {"index": "subtitle-1", "type": "subtitle", "text": "## 主題"})
    path.write_text(json.dumps({"script": rows}, ensure_ascii=False))
    store.add("source_documents", sid, {"source_id": sid, "source_type": "sermon_transcript",
        "transcript_id": sid, "source_path": str(path),
        "source_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_body_sha256": project_script(rows).body_sha256})
    return path


def test_routes_by_query_and_collects_all_occurrence_and_evidence_sources(tmp_path):
    store = Store()
    paths = [source(store, tmp_path, sid, headings=sid == "C") for sid in "ABCD"]
    store.add("claims", "matched", {"claim_id": "matched", "statement": "人子的身份",
        "occurrences": [{"source_id": "B"}, {"source_id": "C"}], "evidence_step_ids": ["E"]})
    store.add("claims", "other", {"claim_id": "other", "statement": "其他",
        "occurrences": [{"source_id": "A"}]})
    store.add("evidence_steps", "E", {"source_fragment_id": "F", "source_fragment_ids": ["G"]})
    store.add("source_fragments", "F", {"source_id": "D"})
    store.add("source_fragments", "G", {"source_id": "B"})
    before = deepcopy(store.rows), [p.read_bytes() for p in paths]
    result = build_source_heading_inventory(store)
    assert [row["source_id"] for row in result["sources"]] == ["B", "C", "D", "A"]
    assert [row["source_id"] for row in result["generation_plan"]] == ["B", "D", "A"]
    assert {row["collection"] for row in result["pins"]} == {
        "claims", "evidence_steps", "source_fragments", "source_documents"}
    assert result == build_source_heading_inventory(store)
    assert before == (store.rows, [p.read_bytes() for p in paths])
    scoped = build_source_heading_inventory(store, include_other_sources=False)
    assert [row["source_id"] for row in scoped["sources"]] == ["B", "C", "D"]


def test_published_first_and_stale_body_do_not_schedule(tmp_path):
    store = Store()
    old = source(store, tmp_path, "old", stage="script_review")
    published = old.parent.parent / "script_published" / old.name
    published.parent.mkdir()
    published.write_bytes(old.read_bytes())
    stale = source(store, tmp_path, "stale")
    stale.write_text(stale.read_text().replace("原話", "改文"))
    for sid in ["old", "stale"]:
        store.add("claims", sid, {"claim_id": sid, "statement": "人子", "occurrences": [{"source_id": sid}]})
    result = build_source_heading_inventory(store)
    assert result["generation_plan"] == []
    assert [row["status"] for row in result["sources"]] == ["stale", "stale"]
    assert result["sources"][0]["reason"] == "registered_path_is_not_published_first"


def test_notes_headings_are_inspected_but_never_scheduled(tmp_path):
    store = Store()
    path = tmp_path / "notes.md"
    path.write_text("## 主題\n\n教授原話。\n")
    from backend.pipeline.knowledge_source import markdown_source_document
    doc = {"source_id": "notes", "source_type": "notes_manuscript", "source_path": str(path)}
    payload, raw, _ = markdown_source_document(doc)
    doc.update(source_file_sha256=hashlib.sha256(raw).hexdigest(),
               source_body_sha256=project_script(payload["script"]).body_sha256)
    store.add("source_documents", "notes", doc)
    store.add("claims", "C", {"claim_id": "C", "statement": "人子", "occurrences": [{"source_id": "notes"}]})
    result = build_source_heading_inventory(store)
    assert result["sources"][0]["status"] == "has_headings"
    assert result["generation_plan"] == []


def test_missing_dependency_and_inconsistent_claim_are_disclosed(tmp_path):
    store = Store()
    source(store, tmp_path, "S")
    store.add("claims", "C", {"claim_id": "C", "statement": "人子", "evidence_step_ids": ["absent"]})
    store.add("claims", "bad", {"claim_id": "bad", "statement": "人子"})
    store.rows["claims", "bad"]["content_sha256"] = "0" * 64
    result = build_source_heading_inventory(store, claim_ids=["C", "bad", "retired"])
    assert result["generation_plan"] == []
    assert {row["reason"] for row in result["issues"]} == {
        "missing_or_retired", "no_bound_source", "stored_content_sha_mismatch"}


def test_partial_headings_schedule_only_prefix_and_empty_unsupported_are_visible(tmp_path):
    store = Store()
    path = source(store, tmp_path, "partial")
    rows = [{"index": "p1", "text": "開頭。"},
            {"index": "subtitle-1", "type": "subtitle", "text": "## 後段"},
            {"index": "p2", "text": "後段。"}]
    path.write_text(json.dumps({"script": rows}, ensure_ascii=False))
    doc = store.rows["source_documents", "partial"]["payload"]
    doc.update(source_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
               source_body_sha256=project_script(rows).body_sha256)
    store.add("source_documents", "partial", doc)
    empty = source(store, tmp_path, "empty")
    empty.write_text('{"script": []}')
    doc = store.rows["source_documents", "empty"]["payload"]
    doc.update(source_file_sha256=hashlib.sha256(empty.read_bytes()).hexdigest(),
               source_body_sha256=project_script([]).body_sha256)
    store.add("source_documents", "empty", doc)
    store.add("source_documents", "unsupported", {
        "source_id": "unsupported", "source_type": "audio", "source_path": str(tmp_path / "audio")})
    for sid in ["partial", "empty", "unsupported"]:
        store.add("claims", sid, {"claim_id": sid, "statement": "人子", "occurrences": [{"source_id": sid}]})
    result = build_source_heading_inventory(store)
    assert {row["source_id"]: row["status"] for row in result["sources"]} == {
        "partial": "untitled_prefix", "empty": "empty", "unsupported": "unsupported"}
    assert len(result["generation_plan"]) == 1
    assert result["generation_plan"][0]["scope_end"] == 1
    assert result["generation_plan"][0]["path"] == str(path)
    assert result["generation_plan"][0]["source_pin"] == {
        "revision": 1, "content_sha256": record_content_sha(store.rows["source_documents", "partial"]["payload"])}


def test_sql_snapshot_batches_dependencies_in_one_read_only_transaction(tmp_path):
    base = Store()
    source(base, tmp_path, "S")
    base.add("claims", "C", {"claim_id": "C", "statement": "人子", "evidence_step_ids": ["E"]})
    base.add("claims", "unrelated", {"claim_id": "unrelated", "statement": "其他", "evidence_step_ids": ["NEVER"]})
    base.add("evidence_steps", "E", {"source_fragment_ids": ["F"]})
    base.add("source_fragments", "F", {"source_id": "S"})

    class SQLStore:
        def __init__(self):
            self.connections = 0
            self.queries = []

        def connect(self):
            self.connections += 1
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return self

        def execute(self, sql, params=None):
            self.queries.append((sql, params))
            self.params = params

        def fetchall(self):
            collection = self.params[0]
            return [(ident, row["payload"], row["revision"], row["content_sha256"])
                    for (col, ident), row in base.rows.items() if col == collection
                    and (len(self.params) == 1 or ident in self.params[1])]

    store = SQLStore()
    result = build_source_heading_inventory(store, include_other_sources=False)
    assert store.connections == 1
    assert len(store.queries) == 5
    assert store.queries[0][0] == "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert store.queries[2][1] == ["evidence_steps", ["E"]]
    assert result["claim_ids"] == ["C", "unrelated"]
    assert result["total_active_claim_count"] == 2
    assert result["matched_claim_count"] == 1
    assert result["generation_plan"][0]["source_id"] == "S"
    assert not any(row["object_id"] == "NEVER" for row in result["pins"])


def test_known_editorial_transition_allows_heading_only_drift(tmp_path, monkeypatch):
    store = Store()
    path = source(store, tmp_path, "S")
    payload = json.loads(path.read_bytes())
    payload["script"].insert(0, {"type": "subtitle", "index": "subtitle-new", "text": "## 主題"})
    path.write_text(json.dumps(payload, ensure_ascii=False))
    store.add("claims", "C", {"claim_id": "C", "statement": "人子", "occurrences": [{"source_id": "S"}]})
    from backend.pipeline import source_editorial_transition
    calls = []

    def verify(binding):
        calls.append(binding)
        return {"path": str(path), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    monkeypatch.setattr(source_editorial_transition, "verify_current_source_binding", verify)
    result = build_source_heading_inventory(store)
    assert len(calls) == 1
    assert result["sources"][0]["status"] == "has_headings"
    assert result["sources"][0]["editorial_transition_verified"] is True
    assert result["generation_plan"] == []
    # A body change cannot use an editorial receipt to become current.
    path.write_text(path.read_text().replace("原話", "改文"))
    assert build_source_heading_inventory(store)["sources"][0]["status"] == "stale"
    assert len(calls) == 1



def test_store_revision_field_is_metadata_not_content_hash(tmp_path):
    store = Store()
    source(store, tmp_path, "S")
    claim = {"claim_id": "C", "statement": "人子", "revision": 42,
             "occurrences": [{"source_id": "S"}]}
    store.add("claims", "C", claim)
    assert store.rows["claims", "C"]["content_sha256"] == record_content_sha(claim)
    assert record_content_sha(claim) == record_content_sha({**claim, "revision": 43})
    result = build_source_heading_inventory(store, include_other_sources=False)
    assert result["matched_claim_count"] == 1
    assert result["ready_for_generation"] is True
    assert len(result["generation_plan"]) == 1


def test_invalid_claim_hash_cannot_report_ready_zero_work():
    store = Store()
    store.add("claims", "C", {"claim_id": "C", "statement": "人子", "revision": 1})
    store.rows["claims", "C"]["content_sha256"] = "bad"
    result = build_source_heading_inventory(store, include_other_sources=False)
    assert result["matched_claim_count"] == 0
    assert result["ready_for_generation"] is False
    assert result["blocking_issues"] == [{"key": "claims/C", "reason": "stored_content_sha_mismatch"}]


def test_occurrence_transcript_alias_and_canonical_evidence_are_one_source(tmp_path):
    store = Store()
    source(store, tmp_path, "SRC-registered")
    doc = store.rows["source_documents", "SRC-registered"]["payload"]
    # The registry, rather than the filename, is the only alias authority.
    doc["transcript_id"] = "source-transcript"
    path = tmp_path / "script_published" / "source-transcript.json"
    path.write_bytes((tmp_path / "script_published" / "SRC-registered.json").read_bytes())
    doc["source_path"] = str(path)
    store.add("source_documents", "SRC-registered", doc)
    store.add("claims", "C", {"claim_id": "C", "statement": "人子", "revision": 2,
        "occurrences": [{"source_id": "source-transcript"}], "evidence_step_ids": ["E"]})
    store.add("evidence_steps", "E", {"source_fragment_id": "F"})
    store.add("source_fragments", "F", {"source_id": "SRC-registered"})
    result = build_source_heading_inventory(store, include_other_sources=False)
    assert result["issues"] == []
    assert result["ready_for_generation"] is True
    assert [row["source_id"] for row in result["sources"]] == ["SRC-registered"]
    assert len(result["generation_plan"]) == 1
    assert result["source_alias_resolutions"] == [{"alias": "source-transcript",
        "source_id": "SRC-registered", "resolution": "unique_registered_transcript_id", "claim_ids": ["C"]}]


def test_ambiguous_registry_transcript_alias_is_not_guessed(tmp_path):
    store = Store()
    for sid in ["A", "B"]:
        source(store, tmp_path, sid)
        doc = store.rows["source_documents", sid]["payload"]
        doc["transcript_id"] = "ambiguous"
        store.add("source_documents", sid, doc)
    store.add("claims", "C", {"claim_id": "C", "statement": "人子",
                             "occurrences": [{"source_id": "ambiguous"}]})
    result = build_source_heading_inventory(store)
    assert result["generation_plan"] == []
    assert result["ready_for_generation"] is False
    assert result["sources"][0]["candidate_source_ids"] == ["A", "B"]
    assert len(result["blocking_issues"]) == 1
