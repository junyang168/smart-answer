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
# The cache prints 着/裏/牠 where the sermons print 著/裡/他, and 祂 is a
# typesetting choice for the same pronoun: one word, two glyphs (#439 fresh
# draft: five verbatim verses failed on these alone).
_VARIANTS = str.maketrans({"着": "著", "裏": "裡", "牠": "他", "祂": "他"})
# The cache carries the translators' notes inline, 「生命〔生命：或作靈魂；下同〕的」;
# they are not part of the verse anyone reads aloud.
_TRANSLATOR_NOTE = re.compile(r"〔[^〕]*〕")

Chapter = Callable[[str, int], Mapping[str, str]]

SCENE_VERSES = 3


def normalize(text: str) -> str:
    return _NOT_WORD.sub("", _TRANSLATOR_NOTE.sub("", text)).translate(_VARIANTS)


def matches_elided(quote: str, *texts: str) -> bool:
    """Every elided segment of a quote occurs word for word in one of `texts`."""

    segments = [normalize(part) for part in _ELISION.split(quote)]
    segments = [part for part in segments if part]
    return bool(segments) and all(any(part in text for text in texts) for part in segments)


def cited_chapters(packet: Mapping[str, Any]) -> list[tuple[str, int]]:
    corpus = "\n".join(str(item.get("content")) for item in packet["source_originals"]["originals"])
    found: list[tuple[str, int]] = []
    for slug in reference_slugs(corpus):
        book, chapter = slug.split("-")[:2]
        key = (book.upper(), int(chapter))
        if key not in found:
            found.append(key)
    return found


def cited_verses(
    packet: Mapping[str, Any], chapter: Chapter = cuv.chapter, *, manuscript: str = ""
) -> list[dict[str, str]]:
    """The 和合本 text of every verse the sources cite, for the author payload.

    The scope's own passages come first: 太9:1-8 is the essay's text, but the
    sources cite only 9:7 by reference, so the author was asked to block-quote
    太9:6 with no verse to quote (#439, two rounds unfixed). A revision also
    gets the verses its draft cites (太26:28, asked for as a block, cited by
    no source) -- limited, like the check, to chapters the sources cite.
    """

    corpus = "\n".join(str(item.get("content")) for item in packet["source_originals"]["originals"])
    scope = " ".join(str(ref) for ref in (packet.get("scope") or {}).get("passage_refs") or [])
    taught = set(cited_chapters(packet))
    drafted = [slug for slug in reference_slugs(manuscript)
               if (slug.split("-")[0].upper(), int(slug.split("-")[1])) in taught]
    # The owner allows a verse's scene from its own preceding verses (路24:7
    # opens 「說：」; who speaks is in 24:4-6), so a revision gets them too.
    context = []
    for slug in drafted:
        book, number, start = slug.split("-")[:3]
        if int(start) > 1:
            context.append(f"{book}-{number}-{max(1, int(start) - SCENE_VERSES)}-{int(start) - 1}")
    drafted = [*context, *drafted]
    verses: list[dict[str, str]] = []
    for slug in dict.fromkeys([*reference_slugs(scope), *reference_slugs(corpus), *drafted]):
        parts = slug.split("-")
        book, number, start = parts[0].upper(), int(parts[1]), int(parts[2])
        end = int(parts[3]) if len(parts) > 3 else start
        text = chapter(book, number)
        lines = [text[str(v)] for v in range(start, max(start, end) + 1) if str(v) in text]
        if lines:
            verses.append({"reference": slug, "text": _TRANSLATOR_NOTE.sub("", "".join(lines))})
    return verses


def scripture_corpus(packet: Mapping[str, Any], chapter: Chapter = cuv.chapter) -> dict[str, Any]:
    """Normalized text of each cited chapter, with a SHA per chapter for the record."""

    chapters: dict[str, str] = {}
    texts: list[str] = []
    each: list[str] = []
    for book, number in cited_chapters(packet):
        verses = chapter(book, number)
        ordered = [verses[key] for key in sorted(verses, key=int)]
        chapters[f"{book}/{number}"] = sha256_text(json.dumps(verses, ensure_ascii=False, sort_keys=True))
        texts.append(normalize("".join(ordered)))
        each.extend(normalize(verse) for verse in ordered)
    return {"edition": "cuv", "chapters": chapters, "text": "|".join(texts), "verses": each}


def extends_verse(quote: str, corpus: Mapping[str, Any]) -> bool:
    """A segment holding a whole 和合本 verse plus words the verse does not have.

    The sermon is a valid source for a verse block -- his retranslations and
    his reading aloud -- but a spoken echo is not Scripture: 太26:28 read as
    「使罪得赦，為多人。」 passed as the verse because the sermon has it.
    """

    # Only a short addition counts: a block running on into the next verse in
    # his own reading (可2:10-11, 「回家去罷」) carries far more than an echo.
    for part in _ELISION.split(quote):
        segment = normalize(part)
        if segment and segment not in corpus["text"] and any(
            len(verse) >= 8 and verse in segment and len(segment) - len(verse) < len(verse) / 2
            for verse in corpus.get("verses", [])
        ):
            return True
    return False


def matches_scripture(quote: str, corpus: Mapping[str, Any]) -> bool:
    """A quote block matches when every elided segment is 和合本 word for word."""

    # A translator's note 〔…〕 is stripped for matching, never shown as the verse.
    return not _TRANSLATOR_NOTE.search(quote) and matches_elided(quote, corpus["text"])
