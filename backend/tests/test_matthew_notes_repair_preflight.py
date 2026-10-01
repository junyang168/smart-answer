from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.matthew_notes_repair_preflight import build_repair_plan


def _source(root: Path, project_id: str, text: str, *, project_type: str = "sermon_note") -> dict:
    project = root / project_id
    project.mkdir()
    final = project / "final.md"
    final.write_text(text, encoding="utf-8")
    (project / "meta.json").write_text(
        json.dumps({"project_type": project_type, "audit_passed": project_type != "transcript"}),
        encoding="utf-8",
    )
    return {
        "source_id": f"notes_manuscript:{project_id}",
        "source_type": "notes_to_manuscript",
        "project_id": project_id,
        "title": project_id,
        "source_url": f"/notes/{project_id}",
    }


def _row(collection: str, object_id: str, payload: dict, revision: int = 1) -> dict:
    return {
        "collection": collection,
        "object_id": object_id,
        "revision": revision,
        "content_sha256": record_content_sha(payload),
        "payload": payload,
    }


def test_preflight_splits_linked_sources_and_explains_transcript_project(tmp_path: Path) -> None:
    notes_root = tmp_path / "notes"
    notes_root.mkdir()
    assigned = _source(notes_root, "assigned", "## Heading\n\nProfessor body.")
    book = _source(notes_root, "book", "## Book\n\nBook body.")
    _source(notes_root, "transcript-copy", "# Copy\n\nBody.", project_type="transcript")
    catalog = {
        "source_directory": [{**assigned, "assigned_chapters": [5]}],
        "book_level_sources": [{**book, "assigned_chapters": []}],
    }
    source_payload = {
        "source_id": assigned["source_id"],
        "source_type": "notes_manuscript",
        "source_path": str(notes_root / "assigned" / "final.md"),
        "source_sha256": hashlib.sha256((notes_root / "assigned" / "final.md").read_bytes()).hexdigest(),
    }
    claim = {
        "claim_id": "CL-1",
        "review_status": "candidate",
        "occurrences": [{"source_id": assigned["source_id"]}],
        "evidence_step_ids": ["E-1"],
    }
    link = {"link_id": "VCL-1", "claim_id": "CL-1", "viewpoint_id": "CV-1"}
    plan, unlinked, linked, ownership = build_repair_plan(
        catalog=catalog,
        catalog_sha256="a" * 64,
        notes_root=notes_root,
        database_rows=[
            _row("source_documents", assigned["source_id"], source_payload),
            _row("claims", "CL-1", claim),
            _row("viewpoint_claim_links", "VCL-1", link),
        ],
        output_root=tmp_path / "RB-357",
        runner_commit="b" * 40,
        worktree=tmp_path,
        generated_at="2026-09-14T00:00:00+00:00",
        batch_id_prefix="RB-MATTHEW-NOTES-REPAIR-357-TEST",
    )
    assert plan["status"] == "repair_required"
    assert plan["counts"] == {
        "catalog_authoritative_sources": 2,
        "active_source_documents": 1,
        "missing_source_documents": 1,
        "active_claims": 1,
        "candidate_claims": 1,
        "human_review_required_claims": 0,
        "unlinked_repair_sources": 1,
        "linked_repair_sources": 1,
        "disk_exclusions": 1,
        "blockers": 0,
    }
    assert linked["sources"][0]["source_id"] == assigned["source_id"]
    assert unlinked["sources"][0]["source_id"] == book["source_id"]
    assert plan["disk_exclusions"][0]["reason_code"] == "TRANSCRIPT_PROJECT_USES_SPOKEN_SOURCE_PIPELINE"
    assert ownership["issue"] == 357


def test_preflight_blocks_unexplained_disk_source(tmp_path: Path) -> None:
    notes_root = tmp_path / "notes"
    notes_root.mkdir()
    included = _source(notes_root, "included", "## Heading\n\nBody.")
    _source(notes_root, "mystery", "## Mystery\n\nBody.", project_type="sermon_note")
    plan, _, _, _ = build_repair_plan(
        catalog={"source_directory": [included], "book_level_sources": []},
        catalog_sha256="a" * 64,
        notes_root=notes_root,
        database_rows=[],
        output_root=tmp_path / "RB-357",
        runner_commit="b" * 40,
        worktree=tmp_path,
        generated_at="2026-09-14T00:00:00+00:00",
        batch_id_prefix="RB-MATTHEW-NOTES-REPAIR-357-TEST",
    )
    assert plan["status"] == "blocked"
    assert plan["blockers"] == [
        {"project_id": "mystery", "reason_code": "UNEXPLAINED_DISK_NOTES_SOURCE"}
    ]
