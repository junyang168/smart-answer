import hashlib
import json

from backend.api import source_coverage_catalog
from backend.pipeline.claim_flash_completion import completion_status, save_completion, sermon_completion_status
from backend.pipeline.claim_flash_grouping import covers_all_claims
from backend.pipeline.source_projection import project_script


def test_sermon_completion_tracks_transcript_content(tmp_path):
    item = "講道 A"
    publication = tmp_path / "script_published" / f"{item}.json"
    publication.parent.mkdir()
    rows = [{"text": "原文", "start": 0, "end": 2}]
    publication.write_text(json.dumps({"script": rows}))
    doc = {"source_type": "sermon_transcript", "transcript_id": item}
    result = {
        "mode": "candidate_workspace", "status": "completed", "pending": [],
        "source_file": {"file_sha256": hashlib.sha256(publication.read_bytes()).hexdigest()},
        "source_body_sha256": project_script(rows).body_sha256,
    }
    save_completion(tmp_path, doc, result)
    assert sermon_completion_status(tmp_path, item) is None
    save_completion(tmp_path, doc, {**result, "mode": "canonical_refresh", "pending": [{"layer": "claims"}]})
    assert sermon_completion_status(tmp_path, item) is None
    save_completion(tmp_path, doc, {**result, "mode": "canonical_refresh"})
    assert sermon_completion_status(tmp_path, item) == "completed"
    publication.write_text(json.dumps({"script": rows, "metadata": {"status": "published"}}))
    assert sermon_completion_status(tmp_path, item) == "completed"
    publication.write_text(json.dumps({"script": [{**rows[0], "text": "新原文"}]}))
    assert sermon_completion_status(tmp_path, item) == "stale"


def test_catalog_includes_unextracted_manuscripts_and_reads_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(source_coverage_catalog, "_catalog_rows", lambda: [{"item": "講道 A"}])
    notes = tmp_path / "notes_to_surmon"
    notes.mkdir()
    (notes / "series_db.json").write_text(json.dumps([
        {"lectures": [{"project_ids": ["notes-a", "notes-b"]}]},
        {"project_type": "transcript", "lectures": [{"project_ids": ["derived-transcript"]}]},
    ]))
    path = notes / "notes-a" / "final.md"
    path.parent.mkdir()
    path.write_text("Notes")
    save_completion(tmp_path, {"source_type": "notes_manuscript", "project_id": "notes-a"}, {
        "mode": "canonical_refresh", "status": "completed", "pending": [],
        "source_file": {"file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
    })
    catalog = source_coverage_catalog.build([
        {"source_type": "notes_manuscript", "source_id": "SRC-A", "project_id": "notes-a"},
    ], tmp_path)
    assert [(row["file"], row["source_id"], row["argument_update_status"]) for row in catalog["entries"]] == [
        ("講道 A", None, None), ("notes-a", "SRC-A", "completed"), ("notes-b", None, None),
    ]
    path.write_text("Changed notes")
    assert completion_status(tmp_path, "notes_manuscript", "notes-a", lambda: path) == "stale"


def test_only_a_whole_source_plan_can_record_completion(monkeypatch):
    monkeypatch.setattr("backend.pipeline.claim_flash_common.belongs_to_source",
                        lambda payload, records, source_id: payload["source"] == source_id)
    records = {
        "claims/A": {"payload": {"source": "source"}},
        "claims/B": {"payload": {"source": "source"}},
        "claims/C": {"payload": {"source": "other"}},
        "claims/D": {"payload": {"source": "source"}, "retired": True},
    }
    assert not covers_all_claims(records, "source", {"groups": [{"claim_ids": ["A"]}]})
    assert covers_all_claims(records, "source", {"groups": [{"claim_ids": ["A", "B"]}]})
