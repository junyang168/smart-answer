"""Apply generated sermon subtitles without changing any existing paragraph.

This module owns the deterministic part of the write. Authorization and the
actual save remain in ``SermonManager`` so the extraction runner cannot acquire
a second, ungoverned path to ``script_review``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from backend.pipeline.extraction_sections import heading_level


class SubtitlePersistenceError(RuntimeError):
    """Generated subtitles could not be safely persisted."""


class SubtitleBodyMutationError(SubtitlePersistenceError):
    """A proposed or saved transcript changed something besides subtitles."""


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def body_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return all non-subtitle rows exactly, for before/after proof."""

    return [
        dict(row)
        for row in rows
        if str(row.get("type") or "") != "subtitle"
        and heading_level(str(row.get("text") or "")) is None
    ]


def _heading_text(text: str, level: int) -> str:
    stripped = str(text or "").strip()
    if not stripped:
        raise SubtitlePersistenceError("generated subtitle text is empty")
    if level not in (1, 2):
        raise SubtitlePersistenceError(f"generated subtitle level must be 1 or 2, got {level!r}")
    expected = "##" if level == 1 else "###"
    if heading_level(stripped) is None:
        return f"{expected} {stripped}"
    actual = len(stripped) - len(stripped.lstrip("#"))
    if actual != len(expected):
        raise SubtitlePersistenceError(
            f"generated subtitle level {level} disagrees with heading {stripped!r}"
        )
    return stripped


def apply_insertions(
    rows: Sequence[Mapping[str, Any]],
    insertions: Sequence[Mapping[str, Any]],
    *,
    source_sha256: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """Insert every generated heading and preserve every original row verbatim."""

    original = [dict(row) for row in rows]
    indexes = [str(row.get("index")) for row in original]
    if len(indexes) != len(set(indexes)):
        raise SubtitlePersistenceError("sermon paragraph indexes are not unique")

    known = set(indexes)
    grouped: dict[str, list[dict[str, Any]]] = {}
    accepted: list[dict[str, Any]] = []
    for ordinal, insertion in enumerate(insertions, start=1):
        after = str(insertion.get("after_index") or "").strip()
        after = "START" if after.upper() == "START" else after
        if after != "START" and after not in known:
            raise SubtitlePersistenceError(
                f"generated subtitle after_index {after!r} does not name a sermon paragraph"
            )
        try:
            level = int(insertion.get("level"))
        except (TypeError, ValueError) as exc:
            raise SubtitlePersistenceError("generated subtitle level is not an integer") from exc
        accepted.append(
            {
                "index": f"subtitle-pipeline-{source_sha256[:12]}-{ordinal:02d}",
                "type": "subtitle",
                "text": _heading_text(str(insertion.get("text") or ""), level),
                "user_id": user_id,
            }
        )
        grouped.setdefault(after, []).append(accepted[-1])

    result: list[dict[str, Any]] = [dict(row) for row in grouped.get("START", [])]
    for row in original:
        result.append(row)
        result.extend(dict(heading) for heading in grouped.get(str(row.get("index")), []))

    if body_rows(result) != body_rows(original):
        raise SubtitleBodyMutationError("subtitle insertion changed existing sermon body rows")
    return result


def verify_saved_result(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    *,
    expected_insertions: int,
) -> None:
    """Prove the save changed only the requested number of subtitle rows."""

    if body_rows(after) != body_rows(before):
        raise SubtitleBodyMutationError("saved sermon body differs from the pre-save body")
    before_headings = sum(1 for row in before if heading_level(str(row.get("text") or "")))
    after_headings = sum(1 for row in after if heading_level(str(row.get("text") or "")))
    if after_headings - before_headings != expected_insertions:
        raise SubtitlePersistenceError(
            "saved sermon did not contain exactly the generated subtitle insertions"
        )
