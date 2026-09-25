"""A passage as the owner types it: `太 20:28`, `太 20:17-34`, `太 19:27–20:16`, `太 20`.

Book names go through `backend.api.scripture`, which owns the book table and
its traditional/simplified/English aliases. That module's `reference_slugs`
cannot read a range that crosses chapters (`19:27-20:16` comes back empty),
and both the owner's passages and some claim `scripture_refs` do cross
chapters, so the range itself is parsed here.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from backend.api.scripture import _resolve_book_slug

# A verse number past any chapter's end: `太 20` means the whole chapter.
WHOLE_CHAPTER = 999

_DASH = r"\s*[-–—~～至]\s*"
_REFERENCE = re.compile(
    r"(?P<book>[1-3]?\s?[A-Za-z一-鿿]+?)\s*"
    r"(?P<c1>\d{1,3})(?:\s*[:：]\s*(?P<v1>\d{1,3}))?"
    rf"(?:{_DASH}(?:(?P<c2>\d{{1,3}})\s*[:：]\s*)?(?P<v2>\d{{1,3}}))?"
    r"(?!\d)"
)


@dataclass(frozen=True)
class Passage:
    book: str  # project slug, e.g. `mat`; upper-cased it is the USFM book code
    start: tuple[int, int]
    end: tuple[int, int]

    @property
    def chapters(self) -> range:
        return range(self.start[0], self.end[0] + 1)

    def overlaps(self, other: "Passage") -> bool:
        return self.book == other.book and self.start <= other.end and other.start <= self.end

    def contains_chapter(self, chapter: int) -> bool:
        return chapter in self.chapters

    @property
    def slug(self) -> str:
        """Folder-name form: `mat-20-28`, `mat-20-17-34`, `mat-19-27-20-16`, `mat-20`."""

        (c1, v1), (c2, v2) = self.start, self.end
        if v1 == 1 and v2 == WHOLE_CHAPTER:
            return f"{self.book}-{c1}" if c1 == c2 else f"{self.book}-{c1}-{c2}"
        if (c1, v1) == (c2, v2):
            return f"{self.book}-{c1}-{v1}"
        if c1 == c2:
            return f"{self.book}-{c1}-{v1}-{v2}"
        return f"{self.book}-{c1}-{v1}-{c2}-{v2}"


def _from_match(match: re.Match[str]) -> Passage | None:
    book = _resolve_book_slug(match.group("book"))
    if not book:
        return None
    c1 = int(match.group("c1"))
    v1 = match.group("v1")
    c2 = match.group("c2")
    v2 = match.group("v2")
    if v1 is None:
        # `太 20` or `太 20-21`: whole chapters. A trailing number after a bare
        # chapter is a chapter too.
        last = int(v2) if v2 else c1
        return Passage(book, (c1, 1), (last, WHOLE_CHAPTER))
    start = (c1, int(v1))
    if v2 is None:
        return Passage(book, start, start)
    end = (int(c2), int(v2)) if c2 else (c1, int(v2))
    if end < start:
        return None
    return Passage(book, start, end)


def parse_passage(text: str) -> Passage:
    """The one passage the owner named. The whole text must be that passage:
    `太 20:28；20:30` is refused rather than quietly read as 20:28."""

    match = _REFERENCE.fullmatch(text.strip())
    passage = _from_match(match) if match else None
    if passage is None:
        raise ValueError(f"expected one passage such as 太 20:17-34, got {text!r}")
    return passage


def find_passages(text: str) -> list[Passage]:
    """Every reference in a claim's `scripture_refs` entry; unknown books are skipped."""

    return [p for p in (_from_match(m) for m in _REFERENCE.finditer(text or "")) if p]
