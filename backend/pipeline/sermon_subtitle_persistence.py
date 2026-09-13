"""Apply generated sermon subtitles without changing any existing paragraph.

This module owns the deterministic part of the write. Authorization and the
actual save remain in ``SermonManager`` so the extraction runner cannot acquire
a second, ungoverned path to ``script_review``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.pipeline.source_projection import (
    heading_level,
    source_body_sha256,
    spoken_source_rows,
)


class SubtitlePersistenceError(RuntimeError):
    """Generated subtitles could not be safely persisted."""


class SubtitleBodyMutationError(SubtitlePersistenceError):
    """A proposed or saved transcript changed something besides subtitles."""


def body_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return professor-spoken rows; editorial rows are not source body."""

    return spoken_source_rows(rows)


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
    seen_boundaries: set[tuple[str, int]] = set()
    for ordinal, insertion in enumerate(insertions, start=1):
        raw_after = insertion.get("after_index")
        after = "" if raw_after is None else str(raw_after).strip()
        after = "START" if after.upper() == "START" else after
        if after != "START" and after not in known:
            raise SubtitlePersistenceError(
                f"generated subtitle after_index {after!r} does not name a sermon paragraph"
            )
        try:
            level = int(insertion.get("level"))
        except (TypeError, ValueError) as exc:
            raise SubtitlePersistenceError("generated subtitle level is not an integer") from exc
        boundary = (after, level)
        if boundary in seen_boundaries:
            raise SubtitlePersistenceError(
                f"duplicate generated subtitle boundary {boundary!r}"
            )
        seen_boundaries.add(boundary)
        generated_index = f"subtitle-pipeline-{source_sha256[:12]}-{ordinal:02d}"
        if generated_index in known:
            raise SubtitlePersistenceError(
                f"generated subtitle index {generated_index!r} already exists"
            )
        accepted.append(
            {
                "index": generated_index,
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
    """Prove the save inserted subtitles and changed no pre-existing row."""

    before_rows = [dict(row) for row in before]
    after_rows = [dict(row) for row in after]
    before_indexes = [str(row.get("index")) for row in before_rows]
    after_indexes = [str(row.get("index")) for row in after_rows]
    if len(after_indexes) != len(set(after_indexes)):
        raise SubtitlePersistenceError("saved sermon paragraph indexes are not unique")
    known = set(before_indexes)
    inserted = [row for row in after_rows if str(row.get("index")) not in known]
    preserved = [row for row in after_rows if str(row.get("index")) in known]
    if preserved != before_rows:
        raise SubtitleBodyMutationError("saved sermon differs from the pre-save sermon rows")
    if len(inserted) != expected_insertions or any(
        str(row.get("type") or "") != "subtitle" for row in inserted
    ):
        raise SubtitlePersistenceError(
            "saved sermon did not contain exactly the generated subtitle insertions"
        )


def write_back_generated_subtitles(
    source_path: Path,
    *,
    expected_source_sha256: str,
    insertions: Sequence[Mapping[str, Any]],
    actor_id: str,
) -> dict[str, Any]:
    """Write generated headings to one review transcript and verify the result.

    This is the shared write operation. Interactive callers enforce sermon ACL
    before entering it; the local extraction CLI requires its explicit
    ``--write-back-generated-subtitles`` operator flag. Neither caller gets a
    separate insertion implementation.
    """

    if source_path.parent.name != "script_review":
        raise SubtitlePersistenceError(
            "generated subtitles can only be written back to a script_review source"
        )
    before_raw = source_path.read_bytes()
    before_sha256 = hashlib.sha256(before_raw).hexdigest()
    if before_sha256 != expected_source_sha256:
        raise SubtitlePersistenceError(
            f"sermon changed before subtitle write-back: expected {expected_source_sha256}, "
            f"found {before_sha256}"
        )
    before = json.loads(before_raw)
    if not isinstance(before, list):
        raise SubtitlePersistenceError("script_review sermon must be a JSON array")
    updated = apply_insertions(
        before,
        insertions,
        source_sha256=before_sha256,
        user_id=actor_id,
    )
    verify_saved_result(before, updated, expected_insertions=len(insertions))

    # Imported lazily so deterministic insertion tests do not initialize the
    # web application. This writer preserves every mapping field and replaces
    # the file atomically.
    from backend.api.sc_api.script_delta import ScriptDelta

    written_sha256 = ScriptDelta.save_rows(
        str(source_path.parent.parent),
        source_path.stem,
        "script_review",
        updated,
        expected_current_sha256=before_sha256,
    )
    if not str(written_sha256 or ""):
        raise SubtitlePersistenceError("atomic sermon save did not return its committed SHA")
    expected_committed_sha256 = hashlib.sha256(
        json.dumps(updated, ensure_ascii=False, indent=4).encode("UTF-8")
    ).hexdigest()
    if written_sha256 != expected_committed_sha256:
        # This is a broken writer contract, not an ordinary later commit.
        # Reload only on this exceptional path so validation can still report
        # whether source text or the authorized heading was altered.
        committed_raw = source_path.read_bytes()
        try:
            committed = json.loads(committed_raw)
        except json.JSONDecodeError as exc:
            raise SubtitlePersistenceError("committed sermon is not valid JSON") from exc
        if not isinstance(committed, list):
            raise SubtitlePersistenceError("committed script_review sermon is not a JSON array")
        verify_saved_result(before, committed, expected_insertions=len(insertions))
        if committed != updated:
            raise SubtitlePersistenceError(
                "committed sermon differs from the exact authorized subtitle application"
            )
        raise SubtitlePersistenceError(
            "atomic sermon save returned a SHA for different bytes"
        )
    # ``save_rows`` verifies the exact bytes while it still holds the shared
    # writer lock.  Re-reading here would be a race: a later legitimate editor
    # save could land after our commit and make this completed operation look
    # failed, inviting a duplicate retry.  The returned SHA names this commit,
    # not whichever newer commit happens to be current when the caller resumes.
    committed_sha256 = written_sha256
    return {
        "source_path": str(source_path),
        "before_source_sha256": before_sha256,
        "after_source_sha256": committed_sha256,
        "before_body_sha256": source_body_sha256(before),
        "after_body_sha256": source_body_sha256(updated),
        "insertions": len(insertions),
        "actor_id": actor_id,
    }
