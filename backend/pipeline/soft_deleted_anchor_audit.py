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
    """Whether every place this excerpt occurs is inside a struck span.

    Every place, not the first one. `str.find` returns an arbitrary occurrence,
    and the professor's short interjections recur many times in one segment --
    「為什麼？」 alone appears three times in a single paragraph of 太16. If one
    of those sits in a deleted span the anchor may still mean any of the
    others, and retiring a record on that is a wrong verdict reached by
    guessing which occurrence was meant. Six records were flagged this way,
    all of them `不是。` and `對不對？` and their kin, and one was retired.

    So the question is narrowed to one that can be answered without guessing:
    is there anywhere left in the source where this text survives? If not, the
    anchor quotes deleted text whichever occurrence it meant.

    `paragraph_key` is not consulted. Across the staged packages only 20% of
    claimed indices still resolve, and the answer does not depend on it.
    """

    if not excerpt:
        return False
    occurrences = surviving = 0
    for text in segments:
        spans = struck_spans(text)
        position = text.find(excerpt)
        while position >= 0:
            occurrences += 1
            end = position + len(excerpt)
            if not any(position < stop and begin < end for begin, stop in spans):
                surviving += 1
            position = text.find(excerpt, position + 1)
    return occurrences > 0 and surviving == 0


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
