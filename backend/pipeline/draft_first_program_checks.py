"""Program checks a draft-first manuscript must pass, beyond verbatim quotes.

Each one was done by hand in a session on the 人子 essay (#439, 2026-10-24)
and is now a blocking gate like the quote check:

- a stray character no gate saw (a Bengali sign 「়」 before the title, which
  also made the title read as a body paragraph);
- a quoted verse with no source behind it, which a reader can take for the
  article's own use of Scripture (owner: 「否则可能会被认为是文章乱用」).
"""

from __future__ import annotations

import unicodedata
from typing import Any, Mapping

from backend.pipeline.draft_first_source_binding import reader_paragraphs, scripture_bindings


def stray_characters(manuscript: str) -> list[dict[str, str]]:
    """Characters no reader can see or no word needs.

    A combining mark is a stray when nothing it could combine with precedes
    it -- Hebrew points after a Hebrew letter are words, a nukta after a
    space is not. Format characters (zero-width, bidi controls) and private
    use characters are never wanted in a published essay.
    """

    found: list[dict[str, str]] = []
    previous = ""
    for index, char in enumerate(manuscript):
        category = unicodedata.category(char)
        follows_letter = bool(previous) and unicodedata.category(previous)[0] in "LM"
        stray = (category in ("Mn", "Me") and not follows_letter) or category in ("Cf", "Co", "Cs")
        if stray and not (char == "﻿" and index == 0):
            found.append({
                "character": char,
                "codepoint": f"U+{ord(char):04X}",
                "name": unicodedata.name(char, "unnamed"),
                "context": manuscript[max(0, index - 8): index + 9].replace("\n", " "),
            })
        previous = char
    return found


def unsourced_verse_blocks(manuscript: str, packet: Mapping[str, Any]) -> list[str]:
    """Quote blocks the professor neither reads nor names anywhere in the sources."""

    return [
        paragraph["text"].splitlines()[0]
        for paragraph in reader_paragraphs(manuscript)
        if paragraph["text"].startswith(">") and not scripture_bindings(paragraph["text"], packet)
    ]


def program_check_report(manuscript: str, packet: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "stray_characters": stray_characters(manuscript),
        "unsourced_verse_blocks": unsourced_verse_blocks(manuscript, packet),
    }
