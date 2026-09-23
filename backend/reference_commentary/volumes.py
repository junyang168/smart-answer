"""Which printed volume a scan belongs to.

The owner does not name the volume per upload: the inbox folder decides it.
`<inbox>/Matthew/ch21/` is Matthew chapter 21, which is in v. 2.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Volume:
    volume_id: str
    work_id: str
    title: str
    chapters: range
    isbn: str | None
    # v. 2's copyright page has not been photographed yet (design §9 open
    # question 1), so its identity is recorded as unconfirmed.
    confirmed: bool


CARSON_MATTHEW = "carson-ebc-matthew"

VOLUMES: tuple[Volume, ...] = (
    Volume(
        volume_id="carson-ebc-matthew-1995-v1",
        work_id=CARSON_MATTHEW,
        title="D. A. Carson, Matthew, EBC (Zondervan 1995), v. 1: Matthew 1–12",
        chapters=range(1, 13),
        isbn="0-310-49961-5",
        confirmed=True,
    ),
    Volume(
        volume_id="carson-ebc-matthew-1995-v2",
        work_id=CARSON_MATTHEW,
        title="D. A. Carson, Matthew, EBC (Zondervan 1995), v. 2: Matthew 13–28",
        chapters=range(13, 29),
        isbn=None,
        confirmed=False,
    ),
)

# Inbox book folder name -> work.
BOOK_FOLDERS = {"matthew": CARSON_MATTHEW}

_CHAPTER_FOLDER = re.compile(r"^ch(?:apter)?[ _-]?(\d{1,2})$", re.IGNORECASE)


def volume_by_id(volume_id: str) -> Volume:
    for volume in VOLUMES:
        if volume.volume_id == volume_id:
            return volume
    raise KeyError(volume_id)


def volume_for_chapter(work_id: str, chapter: int) -> Volume:
    for volume in VOLUMES:
        if volume.work_id == work_id and chapter in volume.chapters:
            return volume
    raise KeyError(f"{work_id} chapter {chapter}")


def resolve_folder(book: str, chapter_folder: str) -> tuple[Volume, int]:
    """Map `Matthew`, `ch21` to (volume, 21). Raises ValueError when unknown."""

    work_id = BOOK_FOLDERS.get(book.strip().lower())
    if work_id is None:
        raise ValueError(f"unknown book folder {book!r}; expected one of {sorted(BOOK_FOLDERS)}")
    match = _CHAPTER_FOLDER.match(chapter_folder.strip())
    if not match:
        raise ValueError(f"chapter folder {chapter_folder!r} should look like ch21")
    chapter = int(match.group(1))
    try:
        return volume_for_chapter(work_id, chapter), chapter
    except KeyError:
        raise ValueError(f"{book} has no chapter {chapter}") from None
