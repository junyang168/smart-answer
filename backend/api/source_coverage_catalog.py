"""Place every source of the corpus in the sermon catalog, extracted or not.

The coverage overview lists the twenty-five sources that have a claim layer.
That answers "how much of this source got in" but not "how much of the corpus
has been looked at at all" — and the corpus is 203 sermons, so the answer today
is about a tenth of it.  A flat list of the twenty-five cannot show the other
181, and the books nobody has touched are exactly what a work queue needs.

So this module projects the sermon catalog: every sermon, where it sits in
scripture and under which topics, and whether a source document exists for it.
The catalog service stays the authority — nothing here decides a passage or a
topic, it only reads what `/resources/sermons` already reads, so the two pages
cannot disagree.  Ordering is deliberately not done here: the canonical book
order lives in one place (`web/src/app/utils/bible-order.ts`) and both pages
use it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from backend.api.sermon_search.bible_refs import ALIAS_TO_BOOK, normalize_ref


def _catalog_rows() -> list[dict[str, Any]]:
    # Imported lazily: the manager loads the whole sermon corpus at import
    # time, and a coverage request that never reaches the catalog should not
    # pay for it.
    from backend.api.sc_api.sermon_manager import sermonManager

    return [sermon.model_dump(mode="json") for sermon in sermonManager.get_sermons("")]


def _notes_placement(document: dict[str, Any], data_base_path: Path) -> dict[str, Any]:
    """Where a manuscript sits in scripture, from its own metadata.

    A manuscript is not a sermon and is not in the sermon catalog, so its
    `bible_verse` is the only statement anyone has made about its passage.
    `太 16` resolves to a chapter; a bare `太` resolves only to a book, and it
    stays at book level — guessing the chapter would make a judgement nobody
    has made look like one that has.
    """
    project_id = str(document.get("project_id") or "").strip()
    meta_path = data_base_path / "notes_to_surmon" / project_id / "meta.json"
    verse = ""
    if project_id and meta_path.is_file():
        try:
            verse = str(json.loads(meta_path.read_text(encoding="utf-8")).get("bible_verse") or "").strip()
        except (json.JSONDecodeError, OSError):
            verse = ""
    reference = normalize_ref(verse) if verse else None
    if reference is not None:
        return {
            "bible_verse": verse,
            "book": reference.book_zh,
            "chapter": reference.chapter_start,
            "display": reference.raw,
        }
    # `normalize_ref` needs a chapter, so a bare book name resolves to nothing
    # and the manuscript would vanish from every scripture view.  The book on
    # its own is a real statement; the chapter is the part nobody made.
    alias = ALIAS_TO_BOOK.get(verse.lower())
    return {
        "bible_verse": verse,
        "book": alias[1] if alias else None,
        "chapter": None,
        "display": verse or None,
    }


def build(sources: list[dict[str, Any]], data_base_path: Path) -> dict[str, Any]:
    """One row per sermon in the catalog, plus the manuscripts beside it."""
    extracted_by_transcript = {
        str(source["transcript_id"]): source["source_id"]
        for source in sources
        if source.get("transcript_id") and source.get("source_type") != "notes_manuscript"
    }

    entries: list[dict[str, Any]] = []
    for row in _catalog_rows():
        passage = row.get("catalog_primary_passage") or {}
        item = str(row.get("item") or "")
        entries.append(
            {
                "kind": "sermon",
                "item": item,
                # The transcript's own file name.  A title is editorial and two
                # sermons can share one; this is what names the file on disk.
                "file": item,
                "title": row.get("title") or item,
                "book": passage.get("book"),
                "chapter": passage.get("chapter"),
                "verse_start": passage.get("verse_start"),
                "display": passage.get("display"),
                "topics": [str(topic) for topic in row.get("topic") or []],
                "organization_mode": row.get("organization_mode"),
                "organization_mode_label": row.get("organization_mode_label"),
                "deliver_date": row.get("deliver_date"),
                "source_id": extracted_by_transcript.get(item),
            }
        )

    for source in sources:
        if source.get("source_type") != "notes_manuscript":
            continue
        placement = _notes_placement(source, data_base_path)
        entries.append(
            {
                "kind": "notes_manuscript",
                "item": None,
                "file": source.get("project_id"),
                "title": source.get("title") or source["source_id"],
                "book": placement["book"],
                "chapter": placement["chapter"],
                "verse_start": None,
                "display": placement["display"],
                "topics": [],
                "organization_mode": None,
                "organization_mode_label": None,
                "deliver_date": None,
                "source_id": source["source_id"],
            }
        )

    return {"entries": entries, "totals": _totals(entries)}


def _totals(entries: list[dict[str, Any]]) -> dict[str, int]:
    sermons = [entry for entry in entries if entry["kind"] == "sermon"]
    return {
        "catalog_sermons": len(sermons),
        "catalog_sermons_extracted": sum(1 for entry in sermons if entry["source_id"]),
        "catalog_books": len({entry["book"] for entry in sermons if entry["book"]}),
        "catalog_books_extracted": len(
            {entry["book"] for entry in sermons if entry["book"] and entry["source_id"]}
        ),
        "notes_manuscripts": sum(1 for entry in entries if entry["kind"] == "notes_manuscript"),
    }


def unplaced(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Catalog rows with no book, which no scripture view can show."""
    return [entry for entry in entries if not entry.get("book")]


def find(entries: list[dict[str, Any]], source_id: str) -> Optional[dict[str, Any]]:
    return next((entry for entry in entries if entry.get("source_id") == source_id), None)
