"""和合本 (traditional) cached one chapter per file under `$DATA_BASE_DIR/bible/cuv/`.

Public domain. Fetched once from bible-api.com's `cuv` translation and kept, so
slides are built from a fixed text instead of whatever the network returns on
the day. `MAT/20.json` is `{"1": "...", "2": "...", ...}`.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Callable

import httpx

from backend.bible_study.passage import Passage
from backend.bible_study.paths import cuv_dir

SOURCE_URL = "https://bible-api.com/data/cuv/{book}/{chapter}"

Fetch = Callable[[str, int], dict[str, str]]


def fetch_chapter(book: str, chapter: int) -> dict[str, str]:
    url = SOURCE_URL.format(book=book, chapter=chapter)
    for attempt in range(4):
        response = httpx.get(url, timeout=30)
        if response.status_code == 429 and attempt < 3:
            time.sleep(2 ** attempt * 3)
            continue
        response.raise_for_status()
        verses = response.json()["verses"]
        return {str(v["verse"]): v["text"].strip() for v in verses}
    raise RuntimeError(f"rate limited fetching {url}")


def chapter(book: str, chapter_number: int, *, root: Path | None = None, fetch: Fetch = fetch_chapter) -> dict[str, str]:
    """Verses of one chapter, from the cache; fetched and cached on first use."""

    code = book.upper()
    path = (root or cuv_dir()) / code / f"{chapter_number}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    verses = fetch(code, chapter_number)
    if not verses:
        raise RuntimeError(f"no verses for {code} {chapter_number}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(verses, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return verses


def ensure_cached(passage: Passage, *, root: Path | None = None, fetch: Fetch = fetch_chapter) -> list[str]:
    """Cache every chapter the passage touches; returns `MAT/20`-style keys."""

    keys = []
    for number in passage.chapters:
        chapter(passage.book, number, root=root, fetch=fetch)
        keys.append(f"{passage.book.upper()}/{number}")
    return keys


def refs_in_slides(spec: dict) -> set[tuple[str, int]]:
    """(BOOK, chapter) for every `refs`/`left`/`right` entry in a slides.json."""

    found = set()
    for slide in spec.get("slides", []):
        for key in ("refs", "left", "right"):
            for ref in slide.get(key) or []:
                book, rest = ref.split(" ", 1)
                found.add((book, int(rest.split(":")[0])))
    return found


def main(argv: list[str] | None = None) -> int:
    """Cache every chapter a study's slides.json quotes: `python -m backend.bible_study.cuv <folder>`."""

    import sys

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m backend.bible_study.cuv <study folder>", file=sys.stderr)
        return 2
    spec = json.loads((Path(args[0]) / "slides.json").read_text(encoding="utf-8"))
    for book, number in sorted(refs_in_slides(spec)):
        chapter(book, number)
        print(f"{book}/{number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
