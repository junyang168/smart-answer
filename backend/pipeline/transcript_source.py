"""Resolve the authoritative sermon transcript independently of caller order."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


TRANSCRIPT_DIRECTORY_PRIORITY = {
    "script_published": 0,
    "script_review": 1,
    "script_patched": 2,
}


def ordered_transcript_dirs(directories: Iterable[Path]) -> list[Path]:
    """Published first, reviewed second, then unknown directories in input order."""

    rows = list(directories)
    return [
        directory
        for _, directory in sorted(
            enumerate(rows),
            key=lambda row: (
                TRANSCRIPT_DIRECTORY_PRIORITY.get(row[1].name, 3),
                row[0],
            ),
        )
    ]


def resolve_transcript_path(
    transcript_id: str, directories: Iterable[Path],
) -> Path | None:
    """Return the best existing transcript under the governed stage policy."""

    for directory in ordered_transcript_dirs(directories):
        path = directory / f"{transcript_id}.json"
        if path.is_file():
            return path
    return None
