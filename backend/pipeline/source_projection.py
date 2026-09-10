"""Separate professor-spoken source from editor-authored transcript structure.

Transcript JSON deliberately stores both in one ``script`` list.  Storage
co-location is not authorship: subtitle and comment rows are editorial data,
so they must never acquire source locators, sentence ids, evidence anchors, or
the content identity used to decide whether a source fragment is current.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


SOFT_DELETION = re.compile(r"~~([^~]+?)~~", re.S)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
EDITORIAL_ROW_TYPES = frozenset({"subtitle", "comment"})
EDITORIAL_ONLY_FIELDS = frozenset({"type", "user_id"})
LOCATOR_SPACE = "spoken_body_v1"


class LocatorSpaceError(ValueError):
    """A source descriptor cannot prove how its locators address the script."""


def live_text(text: str) -> str:
    """Return one segment as the proofreader sees it after soft deletion."""

    return SOFT_DELETION.sub("\n", str(text or ""))


def live_script(script: Any) -> list[dict[str, Any]]:
    """Apply soft deletion without filtering or renumbering physical rows."""

    rows: list[dict[str, Any]] = []
    for segment in script or []:
        row = dict(segment) if isinstance(segment, Mapping) else {"text": str(segment or "")}
        original_text = str(row.get("text") or "")
        level = heading_level(original_text)
        if level is None:
            row["text"] = live_text(original_text)
        else:
            # Preserve the fact that an untyped legacy row was a standalone
            # heading before soft-deletion inserted a newline into its text.
            # Normalizing whitespace inside the editorial title is safe: this
            # row never enters the spoken source projection.
            row.setdefault("type", "subtitle")
            title = " ".join(live_text(heading_text(original_text)).split())
            row["text"] = f"{'#' * level} {title}"
        rows.append(row)
    return rows


def heading_level(text: str) -> int | None:
    """Return the level of a standalone Markdown heading row."""

    match = HEADING_PATTERN.match(str(text or "").strip())
    return len(match.group(1)) if match else None


def heading_text(text: str) -> str:
    """Return a standalone Markdown heading without its marker."""

    match = HEADING_PATTERN.match(str(text or "").strip())
    return match.group(2).strip() if match else str(text or "").strip()


def is_editorial_row(row: Mapping[str, Any]) -> bool:
    """Whether a co-located row is structure/commentary rather than speech."""

    return (
        str(row.get("type") or "").strip().lower() in EDITORIAL_ROW_TYPES
        # Older editor saves used ``type=content`` for heading rows. Their
        # dedicated index namespace remains an authorship signal even for the
        # one observed legacy heading whose text is now empty.
        or str(row.get("index") or "").strip().startswith("subtitle-")
        or heading_level(str(row.get("text") or "")) is not None
    )


@dataclass(frozen=True)
class EditorialHeading:
    """One editor-authored label at a boundary in spoken-body coordinates."""

    boundary: int
    level: int
    title: str


@dataclass(frozen=True)
class SourceProjection:
    """The two semantic projections of one physically co-located script."""

    body_rows: tuple[dict[str, Any], ...]
    headings: tuple[EditorialHeading, ...]
    body_sha256: str
    editorial_structure_sha256: str


def _canonical_body_row(row: Mapping[str, Any]) -> dict[str, Any]:
    # ``type`` and ``user_id`` describe editing/UI state.  Every other field is
    # retained so a changed transcript index, timing, or future source-bearing
    # field invalidates the source identity instead of being silently ignored.
    return {
        str(key): value
        for key, value in row.items()
        if str(key) not in EDITORIAL_ONLY_FIELDS
    }


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def project_script(script: Any) -> SourceProjection:
    """Project a mixed script into spoken rows and editorial heading structure.

    Moving, adding, deleting, or renaming subtitle/comment rows cannot change
    the body rows or their coordinates.  Comments do not become model context;
    valid heading rows become labels at the next spoken row boundary.
    """

    body: list[dict[str, Any]] = []
    headings: list[EditorialHeading] = []
    for row in live_script(script):
        level = heading_level(str(row.get("text") or ""))
        if is_editorial_row(row):
            # Comments are never model context. A reviewer may begin a note
            # with Markdown syntax, but that does not turn the note into the
            # document's editorial outline.
            if (
                level is not None
                and str(row.get("type") or "").strip().lower() != "comment"
            ):
                title = heading_text(str(row.get("text") or ""))
                headings.append(
                    EditorialHeading(
                        boundary=len(body), level=level, title=title
                    )
                )
            continue
        body.append(row)

    canonical_body = [_canonical_body_row(row) for row in body]
    canonical_structure = [
        {"boundary": row.boundary, "level": row.level, "title": row.title}
        for row in headings
    ]
    return SourceProjection(
        body_rows=tuple(body),
        headings=tuple(headings),
        body_sha256=_sha256_json(canonical_body),
        editorial_structure_sha256=_sha256_json(canonical_structure),
    )


def source_uses_body_locator_space(source: Mapping[str, Any]) -> bool:
    """Return whether a descriptor explicitly uses professor-body coordinates.

    A body SHA without a locator-space declaration is not treated as an
    implicit migration. Doing so would silently reinterpret every stored
    ``S n`` from physical-row coordinates as body-row coordinates.
    """

    locator_space = str(source.get("locator_space") or "").strip()
    if locator_space and locator_space != LOCATOR_SPACE:
        raise LocatorSpaceError(f"unsupported source locator space {locator_space!r}")
    if locator_space == LOCATOR_SPACE:
        if not str(source.get("source_body_sha256") or "").strip():
            raise LocatorSpaceError(
                f"{LOCATOR_SPACE} source is missing source_body_sha256"
            )
        return True
    if str(source.get("source_body_sha256") or "").strip():
        raise LocatorSpaceError(
            "source_body_sha256 requires an explicit locator_space"
        )
    return False


def assert_locator_space_compatible(
    source: Mapping[str, Any], script: Any
) -> bool:
    """Fail closed when a legacy locator would be reinterpreted by projection.

    Legacy physical-row and ``spoken_body_v1`` coordinates coincide only when
    the script contains no editorial rows. A mixed legacy source must be
    re-extracted; readers may not guess which row an old locator meant.
    """

    uses_body_coordinates = source_uses_body_locator_space(source)
    if not uses_body_coordinates and any(
        is_editorial_row(row) for row in live_script(script)
    ):
        raise LocatorSpaceError(
            "legacy source has editorial rows but no locator_space; "
            "re-extraction required"
        )
    return uses_body_coordinates


def script_from_markdown_blocks(blocks: Iterable[str]) -> list[dict[str, Any]]:
    """Give Markdown blocks the same stable body/editorial index scheme.

    Heading rows use a separate string namespace.  Therefore inserting or
    moving a Markdown heading cannot renumber the professor-authored body rows
    or change their body identity.
    """

    body_index = 0
    heading_index = 0
    rows: list[dict[str, Any]] = []
    for block in blocks:
        text = str(block)
        if heading_level(text) is not None:
            heading_index += 1
            index: int | str = f"heading-{heading_index}"
        else:
            body_index += 1
            index = body_index
        rows.append(
            {
                "index": index,
                "start_time": None,
                "end_time": None,
                "text": text,
            }
        )
    return rows


def spoken_source_rows(script: Any) -> list[dict[str, Any]]:
    """Convenience wrapper returning only professor-spoken source rows."""

    return [dict(row) for row in project_script(script).body_rows]


def source_body_sha256(script: Any) -> str:
    """The source identity used by anchors and staleness checks."""

    return project_script(script).body_sha256


def editorial_structure_sha256(script: Any) -> str:
    """The identity of model-visible editorial boundaries and labels."""

    return project_script(script).editorial_structure_sha256
