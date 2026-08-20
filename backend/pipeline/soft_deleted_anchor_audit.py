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

#: Records that reach the source text directly, through `source_fragment_ids`.
ANCHORED_COLLECTIONS = ("evidence_steps", "observations", "questions", "position_nodes")


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


@dataclass
class AnchorAudit:
    """What stands on deleted text, and what falls with it."""

    #: fragment_id -> the transcript it quotes.
    deleted_fragments: dict[str, str] = field(default_factory=dict)
    #: (collection, object_id) -> how many of its anchors are deleted, of how many.
    owners: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)
    #: Claims that would keep no live evidence step.
    orphaned_claims: list[str] = field(default_factory=list)
    #: (collection, relation_id) whose endpoint is retired by this closure.
    dangling_relations: list[tuple[str, str]] = field(default_factory=list)
    #: Fragments whose source could not be read, so nothing can be said of them.
    unresolved_fragments: int = 0

    @property
    def orphaned_owners(self) -> list[tuple[str, str]]:
        """Records every one of whose anchors is deleted text."""

        return [key for key, (bad, total) in self.owners.items() if bad == total]

    @property
    def weakened_owners(self) -> list[tuple[str, str]]:
        """Records that keep at least one live anchor."""

        return [key for key, (bad, total) in self.owners.items() if bad < total]

    def retirement_closure(self) -> list[tuple[str, str]]:
        """Everything a retirement would have to cover, in dependency order.

        Fragments first, then the records left with no anchor at all, then the
        claims left with no evidence, then the relations whose endpoint has
        just gone. A record that keeps a live anchor is not here: it loses a
        citation, not its footing.
        """

        keys = [("source_fragments", fragment) for fragment in sorted(self.deleted_fragments)]
        keys += sorted(self.orphaned_owners)
        keys += [("claims", claim) for claim in sorted(self.orphaned_claims)]
        keys += sorted(self.dangling_relations)
        return keys

    def as_dict(self) -> dict[str, Any]:
        by_transcript: dict[str, int] = {}
        for transcript in self.deleted_fragments.values():
            by_transcript[transcript] = by_transcript.get(transcript, 0) + 1
        return {
            "deleted_fragments": len(self.deleted_fragments),
            "by_transcript": dict(sorted(by_transcript.items(), key=lambda row: -row[1])),
            "owners_citing_deleted_text": len(self.owners),
            "owners_with_no_live_anchor": len(self.orphaned_owners),
            "owners_weakened_but_standing": len(self.weakened_owners),
            "claims_with_no_live_evidence": len(self.orphaned_claims),
            "relations_left_dangling": len(self.dangling_relations),
            "unresolved_fragments": self.unresolved_fragments,
            "retirement_closure": len(self.retirement_closure()),
        }


def audit(
    *,
    fragments: Mapping[str, Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Mapping[str, Any]]],
    claims: Mapping[str, Mapping[str, Any]],
    segments_by_source: Mapping[str, Sequence[str]],
    relations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> AnchorAudit:
    """Compute the audit from records already in hand.

    Takes plain mappings rather than a store so the arithmetic can be tested
    without a database: which records stand on deleted text is a property of
    the records and the source, and nothing else.
    """

    result = AnchorAudit()
    for fragment_id, payload in fragments.items():
        source_id = str(payload.get("source_id") or "")
        segments = segments_by_source.get(source_id)
        if segments is None:
            result.unresolved_fragments += 1
            continue
        if excerpt_is_deleted(
            str(payload.get("verbatim_excerpt") or ""),
            segments,
            str(payload.get("paragraph_key") or ""),
        ):
            result.deleted_fragments[fragment_id] = source_id

    for collection in ANCHORED_COLLECTIONS:
        for object_id, payload in (owners.get(collection) or {}).items():
            cited = [str(value) for value in (payload.get("source_fragment_ids") or [])]
            deleted = [value for value in cited if value in result.deleted_fragments]
            if deleted:
                result.owners[(collection, object_id)] = (len(deleted), len(cited))

    retired_steps = {
        object_id for collection, object_id in result.orphaned_owners
        if collection == "evidence_steps"
    }
    for claim_id, payload in claims.items():
        steps = [str(value) for value in (payload.get("evidence_step_ids") or [])]
        if steps and all(step in retired_steps for step in steps):
            result.orphaned_claims.append(claim_id)

    # An edge whose endpoint has been retired is not a weaker edge, it is an
    # edge to nothing. Leaving these behind is how a store keeps reporting a
    # relation count that nothing can be traversed.
    retired = {object_id for _, object_id in result.retirement_closure()}
    for collection, rows in (relations or {}).items():
        for relation_id, payload in rows.items():
            endpoints = {str(payload.get("from_id") or ""), str(payload.get("to_id") or "")}
            if endpoints & retired:
                result.dangling_relations.append((collection, relation_id))
    return result
