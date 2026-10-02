"""Build a study's source list: Carson pages and Wang claims for one passage.

    python -m backend.bible_study.sources 2026-10-02 "太 20:28"

Creates `$DATA_BASE_DIR/bible-study/2026-10-02-mat-20-28/sources.json`, caches
the 和合本 chapters the passage touches, and prints a summary. The owner may
edit `sources.json` by hand afterwards, so an existing one is kept unless
`--refresh` is given (the old file is then saved beside it).

Read-only against `wang_knowledge`: nothing from a study is written back into
the claim layer. Carson's text is not copied here, only page references.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date as Date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Callable, Iterable

from backend.api.reference_commentary import chapters_of, missing_pages
from backend.bible_study import cuv
from backend.bible_study.passage import Passage, find_passages, parse_passage
from backend.bible_study.paths import load_env, studies_dir
from backend.config.reference_commentary_paths import reference_commentary_paths
from backend.reference_commentary.store import VolumeStore, page_sort_key
from backend.reference_commentary.volumes import VOLUMES

# Carson's commentary on the shelf is Matthew only.
CARSON_BOOK = "mat"

ClaimRows = Iterable[tuple[str, str, dict[str, Any]]]  # (claim_id, review_status, payload)


@dataclass(frozen=True)
class Study:
    date: str
    passage: Passage
    directory: Path


def study_for(date: str, passage_text: str, root: Path | None = None) -> Study:
    Date.fromisoformat(date)  # rejects anything but YYYY-MM-DD
    passage = parse_passage(passage_text)
    return Study(date, passage, (root or studies_dir()) / f"{date}-{passage.slug}")


# --- Carson -----------------------------------------------------------------


def _section_overlaps(section: str, passage: Passage) -> bool:
    return any(passage.overlaps(p) for p in find_passages(f"太 {section}"))


def carson_pages(passage: Passage, commentary_root: Path | None = None) -> dict[str, Any]:
    if passage.book != CARSON_BOOK:
        return {"pages": [], "missing_pages": [], "chapters_without_pages": [], "note": "no Carson commentary for this book"}
    root = commentary_root or reference_commentary_paths().root
    pages: list[dict[str, Any]] = []
    for volume in VOLUMES:
        if not any(c in volume.chapters for c in passage.chapters):
            continue
        store = VolumeStore(root / volume.volume_id, volume.volume_id)
        for record in store.pages():
            chapters = chapters_of(record)
            if not any(passage.contains_chapter(c) for c in chapters):
                continue
            sections = record.get("passages") or []
            pages.append(
                {
                    "volume_id": volume.volume_id,
                    "printed_page": record["printed_page"],
                    "chapters": chapters,
                    "sections": sections,
                    "status": record["status"],
                }
            )
    pages.sort(key=lambda p: (p["volume_id"], page_sort_key(p["printed_page"])))
    # A verse heading governs the pages after it until the next heading, so a
    # page that continues 20:20-28 without a heading of its own still discusses
    # 20:28. Carried within a run of consecutive pages only: across a gap the
    # missing page may have started a new section.
    previous: dict[str, Any] | None = None
    for page in pages:
        adjacent = previous and previous["volume_id"] == page["volume_id"] and not missing_pages([previous, page])
        page["continues"] = (previous["sections"] or previous["continues"])[-1:] if adjacent else []
        page["discusses_passage"] = any(_section_overlaps(s, passage) for s in page["continues"] + page["sections"])
        previous = page
    covered = {c for p in pages for c in p["chapters"]}
    return {
        "pages": pages,
        "missing_pages": missing_pages(pages),
        "chapters_without_pages": [c for c in passage.chapters if c not in covered],
    }


# --- Wang claims --------------------------------------------------------------


def load_claim_rows(passage: Passage) -> ClaimRows:
    """Live claims whose refs mention a chapter the passage touches (prefilter only)."""

    import psycopg

    from backend.api.canonical_repository.postgres_store import database_url_from_env

    load_env()
    patterns = [f"%{c}:%" for c in passage.chapters] + [f"%{c}：%" for c in passage.chapters]
    with psycopg.connect(database_url_from_env()) as conn:
        rows = conn.execute(
            """
            SELECT object_id, review_status, payload
              FROM wang_knowledge.objects
             WHERE collection = 'claims'
               AND retired_at IS NULL
               AND payload->>'scripture_refs' LIKE ANY(%s)
            """,
            (patterns,),
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _sermons_of(payload: dict[str, Any]) -> list[str]:
    names = []
    for occurrence in payload.get("occurrences") or []:
        name = occurrence.get("lecture") or occurrence.get("source_id")
        if name and name not in names:
            names.append(name)
    return names


def wang_claims(passage: Passage, rows: ClaimRows) -> dict[str, Any]:
    claims = []
    for claim_id, review_status, payload in rows:
        matched = [
            ref for ref in payload.get("scripture_refs") or [] if any(passage.overlaps(p) for p in find_passages(ref))
        ]
        if not matched:
            continue
        claims.append(
            {
                "claim_id": claim_id,
                "statement": payload.get("statement", ""),
                "claim_type": payload.get("claim_type"),
                "review_status": review_status,
                "scripture_refs": matched,
                "sermons": _sermons_of(payload),
            }
        )
    claims.sort(key=lambda c: ((c["sermons"] or [""])[0], c["claim_id"]))
    counts = Counter(s for c in claims for s in c["sermons"])
    return {
        "claims": claims,
        "sermons": [{"sermon": name, "claims": n} for name, n in sorted(counts.items())],
    }


# --- the study folder ---------------------------------------------------------


def build(
    study: Study,
    *,
    passage_text: str,
    refresh: bool = False,
    claim_rows: Callable[[Passage], ClaimRows] = load_claim_rows,
    commentary_root: Path | None = None,
    cuv_root: Path | None = None,
    fetch: cuv.Fetch = cuv.fetch_chapter,
) -> dict[str, Any]:
    target = study.directory / "sources.json"
    if target.exists() and not refresh:
        raise FileExistsError(f"{target} exists; it may have hand edits. Use --refresh to rebuild it.")
    passage = study.passage
    sources = {
        "date": study.date,
        "passage": {"text": passage_text, "book": passage.book, "start": list(passage.start), "end": list(passage.end)},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "carson": carson_pages(passage, commentary_root),
        "wang": wang_claims(passage, claim_rows(passage)),
        "cuv_chapters": cuv.ensure_cached(passage, root=cuv_root, fetch=fetch),
    }
    study.directory.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target.rename(target.with_name(f"sources.{stamp}.json"))
    target.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sources


def summary(study: Study, sources: dict[str, Any]) -> str:
    carson = sources["carson"]
    wang = sources["wang"]
    lines = [f"study folder: {study.directory}"]
    if carson.get("note"):
        lines.append(f"Carson: {carson['note']}")
    else:
        pages = carson["pages"]
        on_passage = [p["printed_page"] for p in pages if p["discusses_passage"]]
        lines.append(
            f"Carson: {len(pages)} pages in the passage's chapters"
            + (f", {len(on_passage)} discuss the passage itself (pp. {', '.join(on_passage)})" if on_passage else "")
        )
        if carson["chapters_without_pages"]:
            lines.append(f"  NOT SCANNED: chapter(s) {', '.join(map(str, carson['chapters_without_pages']))}")
        if carson["missing_pages"]:
            lines.append(f"  missing pages: {', '.join(map(str, carson['missing_pages']))}")
    lines.append(f"Wang: {len(wang['claims'])} claims from {len(wang['sermons'])} sermons")
    for sermon in wang["sermons"]:
        lines.append(f"  {sermon['claims']:3d}  {sermon['sermon']}")
    lines.append(f"和合本 cached: {', '.join(sources['cuv_chapters'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("date", help="study date, YYYY-MM-DD")
    parser.add_argument("passage", help="e.g. 太 20:28, 太 20:17-34, 太 19:27–20:16")
    parser.add_argument("--refresh", action="store_true", help="rebuild an existing sources.json (old one kept)")
    args = parser.parse_args(argv)
    try:
        study = study_for(args.date, args.passage)
        sources = build(study, passage_text=args.passage, refresh=args.refresh)
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(summary(study, sources))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
