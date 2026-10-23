"""和合本 as the second source of truth for Scripture quote blocks (#439).

The author rule requires every verse an essay argues from to stand as a full
quote block, but the quote check only knew the sermons: 約5:27 and 太9:6 are
cited by the professor and never read aloud in full, so a verbatim block of
either failed and the author had to push the verse into a footnote. The owner
kept the rule (2026-10-22); this module makes it satisfiable.

The text is the fixed 和合本 cache in `backend.bible_study.cuv`, limited to the
chapters the packet's sources cite -- the Scripture the professor actually
taught from, not the whole Bible. Each chapter is SHA-bound in the report.
Only quote blocks may match here; inline 「」 quotes are his words and stay
checked against the sermons alone.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping

from backend.api.scripture import reference_slugs
from backend.bible_study import cuv
from backend.pipeline.matthew_exposition_authoring import sha256_text

# Editions punctuate differently (the sermons' 「看哪、我們上耶路撒冷去、」 vs the
# cache's commas); the words are what has to be verbatim.
_NOT_WORD = re.compile(r"[^0-9A-Za-z㐀-鿿]")
_ELISION = re.compile(r"……|…|⋯")

Chapter = Callable[[str, int], Mapping[str, str]]


def normalize(text: str) -> str:
    return _NOT_WORD.sub("", text)


def cited_chapters(packet: Mapping[str, Any]) -> list[tuple[str, int]]:
    corpus = "\n".join(str(item.get("content")) for item in packet["source_originals"]["originals"])
    found: list[tuple[str, int]] = []
    for slug in reference_slugs(corpus):
        book, chapter = slug.split("-")[:2]
        key = (book.upper(), int(chapter))
        if key not in found:
            found.append(key)
    return found


def cited_verses(packet: Mapping[str, Any], chapter: Chapter = cuv.chapter) -> list[dict[str, str]]:
    """The 和合本 text of every verse the sources cite, for the author payload."""

    corpus = "\n".join(str(item.get("content")) for item in packet["source_originals"]["originals"])
    verses: list[dict[str, str]] = []
    for slug in reference_slugs(corpus):
        parts = slug.split("-")
        book, number, start = parts[0].upper(), int(parts[1]), int(parts[2])
        end = int(parts[3]) if len(parts) > 3 else start
        text = chapter(book, number)
        lines = [text[str(v)] for v in range(start, max(start, end) + 1) if str(v) in text]
        if lines:
            verses.append({"reference": slug, "text": "".join(lines)})
    return verses


def scripture_corpus(packet: Mapping[str, Any], chapter: Chapter = cuv.chapter) -> dict[str, Any]:
    """Normalized text of each cited chapter, with a SHA per chapter for the record."""

    chapters: dict[str, str] = {}
    texts: list[str] = []
    for book, number in cited_chapters(packet):
        verses = chapter(book, number)
        ordered = [verses[key] for key in sorted(verses, key=int)]
        chapters[f"{book}/{number}"] = sha256_text(json.dumps(verses, ensure_ascii=False, sort_keys=True))
        texts.append(normalize("".join(ordered)))
    return {"edition": "cuv", "chapters": chapters, "text": "|".join(texts)}


def matches_scripture(quote: str, corpus: Mapping[str, Any]) -> bool:
    """A quote block matches when every elided segment is 和合本 word for word."""

    segments = [normalize(part) for part in _ELISION.split(quote)]
    segments = [part for part in segments if part]
    return bool(segments) and all(part in corpus["text"] for part in segments)
