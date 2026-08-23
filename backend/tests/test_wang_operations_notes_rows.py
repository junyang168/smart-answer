"""The operations overview uses the same notes membership as public series."""

from __future__ import annotations

import json
from pathlib import Path

from backend.api.wang_operations import _notes_rows


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _project(
    root: Path,
    project_id: str,
    *,
    title: str,
    bible_verse: str | None = None,
    final: bool = False,
    unified: bool = False,
) -> None:
    project = root / project_id
    project.mkdir(parents=True)
    meta = {"id": project_id, "title": title, "project_type": "sermon_note"}
    if bible_verse is not None:
        meta["bible_verse"] = bible_verse
    _write_json(project / "meta.json", meta)
    if final:
        (project / "final.md").write_text(f"# {title}\n", encoding="utf-8")
    if unified:
        (project / "unified_source.md").write_text(f"draft: {title}\n", encoding="utf-8")


def test_notes_rows_follow_sermon_note_series_membership(tmp_path: Path) -> None:
    root = tmp_path / "notes_to_surmon"
    _project(
        root,
        "published",
        title="正式母本",
        bible_verse="太 5:1-12",
        final=True,
        unified=True,
    )
    _project(root, "awaiting-final", title="待定稿", bible_verse="太 6", unified=True)
    _project(root, "unlinked-final", title="孤立定稿", final=True)
    _project(root, "unlinked-unified", title="孤立草稿", unified=True)
    _project(root, "transcript-view", title="逐字稿衍生", final=True)

    _write_json(
        root / "series_db.json",
        [
            {
                "id": "public-notes",
                "project_type": "sermon_note",
                "lectures": [
                    {"id": "one", "project_ids": ["published", "awaiting-final"]},
                    # A duplicate link must not produce a duplicate overview row.
                    {"id": "two", "project_ids": ["published"]},
                ],
            },
            {
                "id": "transcripts",
                "project_type": "transcript",
                "lectures": [{"id": "three", "project_ids": ["transcript-view"]}],
            },
        ],
    )

    rows = _notes_rows(tmp_path)

    assert [row["source_id"] for row in rows] == ["published", "awaiting-final"]
    assert rows[0]["source_path"] == root / "published" / "final.md"
    assert rows[0]["manuscript_file"] == "final.md"
    assert rows[0]["book"] == "馬太福音"
    assert rows[0]["chapter"] == 5
    # unified_source.md does not make a source extractable; final.md does.
    assert rows[1]["source_path"] is None
    assert rows[1]["manuscript_file"] is None


def test_linked_structural_project_without_verse_stays_unplaced(tmp_path: Path) -> None:
    root = tmp_path / "notes_to_surmon"
    _project(root, "structure", title="全書結構", final=True)
    # Legacy series omit project_type; lecture_manager treats them as sermon_note.
    _write_json(
        root / "series_db.json",
        [{"id": "legacy-notes", "lectures": [{"id": "one", "project_ids": ["structure"]}]}],
    )

    rows = _notes_rows(tmp_path)

    assert len(rows) == 1
    assert rows[0]["source_id"] == "structure"
    assert rows[0]["source_path"] == root / "structure" / "final.md"
    assert rows[0]["book"] is None
    assert rows[0]["chapter"] is None
