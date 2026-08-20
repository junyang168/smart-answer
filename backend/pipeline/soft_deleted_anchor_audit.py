"""Which live records in the store stand on text a proofreader deleted.

#102 stopped every reader quoting soft-deleted transcript text. It could not
reach what had already been written down: the authoring store holds records
whose `verbatim_excerpt` was cut from a span struck through in the source, and
a code fix does not retract them.

Deliberately reads the transcript **raw**. Everywhere else in the pipeline
`live_script` is applied on load and the markers are gone by the time anyone
sees the text -- which is the point of #102, and exactly wrong here, because
this module's question is *where the deleted text was*.

Nothing here writes. The closure it computes is what a retirement change set
would have to cover, and computing that is a separate act from applying it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backend.pipeline.knowledge_source import SOFT_DELETION
from backend.pipeline.record_withdrawal import (  # noqa: F401  (re-exported)
    ANCHORED_COLLECTIONS,
    Withdrawal,
    closure_from_fragments,
)


def struck_spans(text: str) -> list[tuple[int, int]]:
    """Half-open character ranges of `text` that the proofreader deleted."""

    return [(match.start(1), match.end(1)) for match in SOFT_DELETION.finditer(str(text or ""))]


def segment_texts(transcript_path: Path) -> list[str]:
    """The segments of one transcript, markers and all."""

    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    script = payload.get("script") if isinstance(payload, dict) else payload
    return [str((row or {}).get("text") or "") for row in (script or [])]


def excerpt_is_deleted(excerpt: str, segments: Sequence[str], paragraph_key: str) -> bool:
    """Whether this excerpt sits inside a struck span of its segment.

    The claimed segment is tried first and the whole transcript second: an
    anchor whose `paragraph_key` no longer resolves is still an anchor, and
    the question is whether the text it quotes was deleted, not whether the
    record remembers where it was.
    """

    if not excerpt:
        return False
    claimed = int(paragraph_key[1:]) - 1 if paragraph_key[1:].isdigit() else None
    ordinals: Iterable[int] = (
        [claimed, *range(len(segments))] if claimed is not None else range(len(segments))
    )
    for ordinal in ordinals:
        if not 0 <= ordinal < len(segments):
            continue
        text = segments[ordinal]
        start = text.find(excerpt)
        if start < 0:
            continue
        end = start + len(excerpt)
        if any(start < stop and begin < end for begin, stop in struck_spans(text)):
            return True
    return False


def audit(
    *,
    fragments: Mapping[str, Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Mapping[str, Any]]],
    claims: Mapping[str, Mapping[str, Any]],
    segments_by_source: Mapping[str, Sequence[str]],
    relations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> Withdrawal:
    """Which live records stand on deleted text, and what falls with them.

    The seed is this module's business; closing it over the records that
    depend on it is `record_withdrawal`'s, because a re-extraction withdrawing
    its predecessor asks the identical question.
    """

    deleted: dict[str, str] = {}
    unresolved = 0
    for fragment_id, payload in fragments.items():
        source_id = str(payload.get("source_id") or "")
        segments = segments_by_source.get(source_id)
        if segments is None:
            unresolved += 1
            continue
        if excerpt_is_deleted(
            str(payload.get("verbatim_excerpt") or ""),
            segments,
            str(payload.get("paragraph_key") or ""),
        ):
            deleted[fragment_id] = source_id
    return closure_from_fragments(
        deleted, owners=owners, claims=claims, relations=relations,
        unresolved_fragments=unresolved,
    )
