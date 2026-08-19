"""Cut a source into overlapping extraction windows, and put the pieces back.

Whole-document extraction is a summarising task and behaves like one. Measured
on the 太16:21–23 母本 after #86, 134 sentences of substantive prose produced 66
represented sentences (49%), while the ledger's second pass -- same model, same
text, asked one sentence at a time -- judged 45% of what the first pass dropped
to be real material. The output was nowhere near the token ceiling, so nothing
was truncated: given the whole yard, the model picks favourites.

So the unit of extraction stops being the document. Two rules carry the design:

    see wide, fetch narrow   A window is shown `context` segments on each side
                             of the segments it is responsible for, and is told
                             to be exhaustive only inside that fetch zone.
    one owner per segment    Every segment falls in exactly one window's fetch
                             zone, so duplicate records cannot arise by
                             construction -- deduplication is a question of
                             ownership, not of similarity.

The overlap does not buy more records; it buys the model seeing *why* a record
matters. 380 locatable observation→evidence_step relations across the corpus
span 0 segments at the median, and 94.8% (講道) / 88.6% (母本) sit within 5
segments. The reading frame is `fetch + 2 * context` wide and steps by `fetch`,
so two segments at distance `d` are guaranteed visible together in some window
exactly when `d <= 2 * context`. The default frame is 15 segments wide with a
10-segment guarantee, which is the ≤10-segment row: 97.1% (講道) / 98.6% (母本).
Dropping context to 3 narrows the frame to 11 and the guarantee to 6, and costs
roughly ten points of relation coverage for a saving of nothing -- the frame is
read once and the source text is the cheap part of the call.

Headings do two different jobs, and only one of them is a boundary.

`##` is a *composition unit*. `stage1_units.json` for the 太16 母本 names four
units and they are exactly its four `##` sections, each generated in its own
pass over its own `start_line`/`end_line` range. Measured on the windowed
package, 0 of 264 in-window relations and 1 of 20 long-distance relations cross
one. So a window never spans a `##`, and its context clips to the unit: text
from the neighbouring unit is not background for this argument, it is a
different argument that happens to sit next to it.

`###` and below are editorial structure *inside* a unit -- 釋經 / 神學意義 /
生活應用 / 附錄 -- and they are not boundaries at all. Every one of those 20
long-distance relations crosses a `###`, because the skeleton files the fact
under 釋經 and the inference drawn from it under 神學意義, which the notes
prompt already says in so many words. Cutting there would sever exactly the
edges this work exists to keep. They are used instead to label a window with
where it sits, and to pull a boundary that lands near one onto it.

Sources with no headings -- 90 of 115 published transcripts -- have one unit and
window purely mechanically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

#: Markdown ATX headings. Transcripts mostly have none (90 of 115 published
#: transcripts contain zero), which is the other reason headings cannot be the
#: cut: the mechanism has to work the same way for both source types.
HEADING_PATTERN = re.compile(r"^(#{1,6})\s")

#: Defaults: a 15-segment reading frame with a 5-segment fetch zone, which
#: guarantees any pair within 10 segments is seen together. See the docstring.
DEFAULT_FETCH = 5
DEFAULT_CONTEXT = 5
#: How far a fetch boundary may move to land on a heading instead of mid-prose.
DEFAULT_SNAP = 2
#: Headings at or above this level are hard barriers: a window never spans one.
#: `##` is not a formatting choice in these manuscripts -- `stage1_units.json`
#: shows the 太16 母本's four `##` sections are its four generation units, each
#: composed independently from its own source lines. Measured on the windowed
#: package, 0 of 264 in-window relations and 1 of 20 long-distance relations
#: cross a `##`, while every long-distance relation crosses a `###`. So the unit
#: is a real edge and the editorial subheadings inside it are not.
DEFAULT_BARRIER_LEVEL = 2


@dataclass(frozen=True)
class ExtractionWindow:
    """One unit of extraction: what it may read, and what it is answerable for.

    Positions are 0-based indices into the source's segment list, and both
    ranges are half-open. `see` always contains `fetch`.
    """

    index: int
    see_start: int
    see_end: int
    fetch_start: int
    fetch_end: int
    breadcrumb: str

    def sees(self, position: int) -> bool:
        return self.see_start <= position < self.see_end

    def owns(self, position: int) -> bool:
        return self.fetch_start <= position < self.fetch_end


def segment_locator(position: int) -> str:
    """The anchor locator for a segment, by its position in the whole source.

    Deliberately global. A window shows a slice of the document but must never
    renumber it: anchors have to stay resolvable against the full source, or
    the ledger cannot place them and every downstream reader breaks.
    """

    return f"S{position + 1:04d}"


def locator_position(locator: str) -> int:
    return int(str(locator)[1:]) - 1


def heading_level(text: str) -> int | None:
    match = HEADING_PATTERN.match(str(text).lstrip())
    return len(match.group(1)) if match else None


def heading_positions(segments: Sequence[str]) -> list[int]:
    return [position for position, text in enumerate(segments) if heading_level(text) is not None]


def breadcrumb_for(segments: Sequence[str], position: int) -> str:
    """The enclosing heading chain at `position`, outermost first.

    Free context, and the part of the heading structure that is actually worth
    having: a 15-segment slice out of the middle of a manuscript reads very
    differently once the model knows it sits in 附錄 under
    二、從馬可福音現象回應Wrede的錯誤解經.
    """

    chain: dict[int, str] = {}
    for index in range(min(position + 1, len(segments))):
        level = heading_level(segments[index])
        if level is None:
            continue
        chain = {depth: title for depth, title in chain.items() if depth < level}
        chain[level] = HEADING_PATTERN.sub("", str(segments[index]).lstrip(), count=1).strip()
    return " > ".join(chain[depth] for depth in sorted(chain))


def unit_spans(segments: Sequence[str], barrier_level: int | None) -> list[tuple[int, int]]:
    """Split the source at its composition units, or return it whole.

    Sources with no headings -- 90 of 115 published transcripts -- yield one
    span, so windowing behaves exactly as it does without barriers.
    """

    if not barrier_level:
        return [(0, len(segments))]
    starts = [0]
    for position, text in enumerate(segments):
        level = heading_level(text)
        if level is not None and level <= barrier_level and position > starts[-1]:
            starts.append(position)
    return [
        (start, end)
        for start, end in zip(starts, starts[1:] + [len(segments)])
        if end > start
    ]


def _snapped_boundaries(
    segments: Sequence[str], *, fetch: int, snap: int
) -> list[int]:
    """Fetch-zone boundaries, moved onto a nearby heading where one exists.

    Snapping changes where a window starts, never how much is covered: the
    boundaries stay strictly increasing and still span the whole source, so
    every segment keeps exactly one owner.
    """

    total = len(segments)
    headings = set(heading_positions(segments))
    boundaries = [0]
    while True:
        target = boundaries[-1] + fetch
        if target >= total:
            break
        best = target
        if snap > 0:
            candidates = [
                position
                for position in range(max(target - snap, boundaries[-1] + 1), min(target + snap, total - 1) + 1)
                if position in headings
            ]
            if candidates:
                best = min(candidates, key=lambda position: (abs(position - target), position))
        boundaries.append(best)
    boundaries.append(total)
    return boundaries


def plan_windows(
    segments: Sequence[str],
    *,
    fetch: int = DEFAULT_FETCH,
    context: int = DEFAULT_CONTEXT,
    snap: int = DEFAULT_SNAP,
    barrier_level: int | None = DEFAULT_BARRIER_LEVEL,
) -> list[ExtractionWindow]:
    """Partition the source into fetch zones and give each one its reading frame.

    Fetch zones tile each composition unit separately and context clips to the
    unit, so a window is never shown text that was written by a different pass
    over different source lines. Material from the neighbouring unit is not
    background for this one; it is a different argument that happens to sit
    next to it.
    """

    if fetch < 1:
        raise ValueError("fetch must be at least 1")
    if context < 0:
        raise ValueError("context cannot be negative")
    if not segments:
        return []
    windows: list[ExtractionWindow] = []
    for unit_start, unit_end in unit_spans(segments, barrier_level):
        unit = segments[unit_start:unit_end]
        boundaries = _snapped_boundaries(unit, fetch=fetch, snap=snap)
        for index in range(len(boundaries) - 1):
            fetch_start = unit_start + boundaries[index]
            fetch_end = unit_start + boundaries[index + 1]
            windows.append(
                ExtractionWindow(
                    index=len(windows) + 1,
                    see_start=max(fetch_start - context, unit_start),
                    see_end=min(fetch_end + context, unit_end),
                    fetch_start=fetch_start,
                    fetch_end=fetch_end,
                    breadcrumb=breadcrumb_for(segments, fetch_start),
                )
            )
    return windows


def window_plan_identity(
    windows: Sequence[ExtractionWindow], *, fetch: int, context: int, snap: int,
    barrier_level: int | None = DEFAULT_BARRIER_LEVEL,
) -> dict[str, Any]:
    """The part of the window plan that has to enter the extraction fingerprint.

    Without it a rerun at a different window size reads as the same extraction
    and is skipped, which would let a whole-document package sit in staging
    labelled as the current one.
    """

    return {
        "fetch": fetch,
        "context": context,
        "snap": snap,
        "barrier_level": barrier_level,
        "window_count": len(windows),
        "fetch_boundaries": [window.fetch_start for window in windows],
    }


# --------------------------------------------------------------------------
# Reassembly
# --------------------------------------------------------------------------

#: Collections whose records carry anchors, and so can be placed on the source.
#: `claims` is not one -- a claim reaches the text only through its evidence
#: steps, and is therefore owned by the window that owns its first step.
ANCHORED_COLLECTIONS = {
    "questions": "question_id",
    "positions": "position_id",
    "observations": "observation_id",
    "evidence_steps": "evidence_step_id",
}
RELATION_COLLECTIONS = {
    "evidence_relations": "relation_id",
    "claim_relations": "claim_relation_id",
}


def namespace_response(response: dict[str, Any], window: ExtractionWindow) -> dict[str, Any]:
    """Prefix a window's short model-facing IDs so responses can be merged.

    Every window is asked for Q001/OBS001/E001 in its own little world; without
    a prefix the second window's OBS001 silently overwrites the first's.
    """

    prefix = f"W{window.index:02d}-"
    id_keys = {
        **ANCHORED_COLLECTIONS,
        "claims": "claim_id",
        **RELATION_COLLECTIONS,
    }
    renamed = {key: [dict(row) for row in (response.get(key) or [])] for key in id_keys}
    for collection, id_key in id_keys.items():
        for row in renamed[collection]:
            row[id_key] = prefix + str(row[id_key])
    for row in renamed["questions"]:
        row["answer_claim_ids"] = [prefix + value for value in row.get("answer_claim_ids") or []]
    for row in renamed["evidence_steps"]:
        row["produced_claim_ids"] = [prefix + value for value in row.get("produced_claim_ids") or []]
    for row in renamed["claims"]:
        row["evidence_step_ids"] = [prefix + value for value in row.get("evidence_step_ids") or []]
        row["opposed_position_ids"] = [prefix + value for value in row.get("opposed_position_ids") or []]
    for collection in RELATION_COLLECTIONS:
        for row in renamed[collection]:
            row["from_id"] = prefix + str(row["from_id"])
            row["to_id"] = prefix + str(row["to_id"])
    return renamed


def _record_spans(
    record: dict[str, Any], segments: Sequence[str]
) -> list[tuple[int, int, int]]:
    """Where a record sits on the source, as (segment position, start, end).

    Excerpts are already verified verbatim by window validation, so a plain
    substring search is exact here; the first occurrence is used because this
    is only ever asked to decide whether two records touch the same text.
    """

    spans: list[tuple[int, int, int]] = []
    for anchor in record.get("anchors") or []:
        position = locator_position(anchor.get("segment_index") or "S0001")
        if not 0 <= position < len(segments):
            continue
        excerpt = str(anchor.get("verbatim_excerpt") or "")
        start = segments[position].find(excerpt) if excerpt else -1
        if start < 0:
            continue
        spans.append((position, start, start + len(excerpt)))
    return spans


def _home_position(spans: Iterable[tuple[int, int, int]]) -> int | None:
    positions = [position for position, _, _ in spans]
    return min(positions) if positions else None


def _overlap(left: Sequence[tuple[int, int, int]], right: Sequence[tuple[int, int, int]]) -> int:
    total = 0
    for position, start, end in left:
        for other_position, other_start, other_end in right:
            if position != other_position:
                continue
            total += max(0, min(end, other_end) - max(start, other_start))
    return total


def merge_window_responses(
    window_responses: Sequence[tuple[ExtractionWindow, dict[str, Any]]],
    segments: Sequence[str],
) -> dict[str, Any]:
    """Reassemble one document-level response from the per-window responses.

    Ownership decides survival: a record is kept by the window whose fetch zone
    contains its first anchor. Records a window produced about someone else's
    segments are redundant -- that segment's owner was told to be exhaustive
    about it -- so they are dropped, and any relation that pointed at them is
    rewritten onto the surviving record covering the same text.

    The one exception is deliberate. If such a relation cannot be rewritten,
    the record is *promoted* and kept rather than the relation being dropped.
    A near-duplicate record is visible and cheap; a severed observation→step
    edge silently turns a load_bearing observation into an orphan, which is the
    failure this whole line of work exists to stop.
    """

    kept: dict[str, dict[str, Any]] = {}
    kept_by_collection: dict[str, list[str]] = {name: [] for name in ANCHORED_COLLECTIONS}
    orphaned: dict[str, tuple[str, dict[str, Any]]] = {}
    spans_by_id: dict[str, list[tuple[int, int, int]]] = {}
    claims: dict[str, dict[str, Any]] = {}
    orphan_claims: dict[str, dict[str, Any]] = {}
    relations: dict[str, list[dict[str, Any]]] = {name: [] for name in RELATION_COLLECTIONS}
    evidence_owner: dict[str, bool] = {}

    for window, raw_response in window_responses:
        response = namespace_response(raw_response, window)
        for collection, id_key in ANCHORED_COLLECTIONS.items():
            for row in response[collection]:
                record_id = str(row[id_key])
                spans = _record_spans(row, segments)
                spans_by_id[record_id] = spans
                home = _home_position(spans)
                row["window_index"] = window.index
                if home is not None and window.owns(home):
                    kept[record_id] = row
                    kept_by_collection[collection].append(record_id)
                    if collection == "evidence_steps":
                        evidence_owner[record_id] = True
                else:
                    orphaned[record_id] = (collection, row)
        for row in response["claims"]:
            claim_id = str(row["claim_id"])
            row["window_index"] = window.index
            first_step = next((step for step in row.get("evidence_step_ids") or []), None)
            home = _home_position(spans_by_id.get(first_step or "", []))
            if home is not None and window.owns(home):
                claims[claim_id] = row
            else:
                orphan_claims[claim_id] = row
        for collection in RELATION_COLLECTIONS:
            relations[collection].extend(response[collection])

    # Rewrite every reference to a dropped record onto the kept record that
    # covers the same text, promoting the dropped record when nothing does.
    remap: dict[str, str] = {}

    def resolve(record_id: str) -> str | None:
        if record_id in kept or record_id in claims:
            return record_id
        if record_id in remap:
            return remap[record_id]
        if record_id in orphan_claims:
            # A claim has no anchors of its own, so it is matched by the steps
            # it was built from: two windows that reached the same conclusion
            # cited overlapping evidence to get there.
            steps = {
                resolved
                for resolved in (resolve(step) for step in orphan_claims[record_id].get("evidence_step_ids") or [])
                if resolved
            }
            match = next(
                (
                    claim_id
                    for claim_id, claim in claims.items()
                    if steps & {resolve(step) for step in claim.get("evidence_step_ids") or []}
                ),
                None,
            )
            if match is not None:
                remap[record_id] = match
            return match
        entry = orphaned.get(record_id)
        if entry is None:
            return None
        collection, row = entry
        spans = spans_by_id.get(record_id) or []
        best_id, best_overlap = None, 0
        for candidate_id in kept_by_collection[collection]:
            score = _overlap(spans, spans_by_id.get(candidate_id) or [])
            if score > best_overlap:
                best_id, best_overlap = candidate_id, score
        if best_id is None:
            row["window_promoted"] = True
            kept[record_id] = row
            kept_by_collection[collection].append(record_id)
            remap[record_id] = record_id
            return record_id
        remap[record_id] = best_id
        return best_id

    merged_relations: dict[str, list[dict[str, Any]]] = {name: [] for name in RELATION_COLLECTIONS}
    for collection, id_key in RELATION_COLLECTIONS.items():
        seen: set[tuple[str, str, str]] = set()
        for row in relations[collection]:
            from_id, to_id = resolve(str(row["from_id"])), resolve(str(row["to_id"]))
            if from_id is None or to_id is None or from_id == to_id:
                continue
            signature = (from_id, to_id, str(row["relation_type"]))
            if signature in seen:
                continue
            seen.add(signature)
            merged_relations[collection].append({**row, "from_id": from_id, "to_id": to_id})

    # Claims survive on their evidence. A claim whose steps all belong to other
    # windows is that window's claim, made again; its own steps were already
    # kept there, so dropping it loses no material.
    surviving_claims: list[dict[str, Any]] = []
    for row in claims.values():
        steps = [resolve(step) for step in row.get("evidence_step_ids") or []]
        row["evidence_step_ids"] = list(dict.fromkeys(step for step in steps if step and step in kept))
        row["opposed_position_ids"] = list(dict.fromkeys(
            position for position in (resolve(value) for value in row.get("opposed_position_ids") or [])
            if position and position in kept
        ))
        if row["evidence_step_ids"]:
            surviving_claims.append(row)
    claim_ids = {str(row["claim_id"]) for row in surviving_claims}
    merged_relations["claim_relations"] = [
        row for row in merged_relations["claim_relations"]
        if row["from_id"] in claim_ids and row["to_id"] in claim_ids
    ]

    merged: dict[str, Any] = {
        collection: [kept[record_id] for record_id in kept_by_collection[collection]]
        for collection in ANCHORED_COLLECTIONS
    }
    # What the merge decided, in the package rather than in nobody's head. A
    # rise in `promoted` means windows are disagreeing about the same text; a
    # rise in `dropped_unreferenced` means the overlap is producing work that is
    # thrown away. Neither is visible from the record counts alone.
    promoted = sum(1 for row in kept.values() if row.get("window_promoted"))
    merge_summary = {
        "windows": len(window_responses),
        "records_kept": len(kept),
        "records_promoted": promoted,
        "records_dropped_as_duplicate": len(orphaned) - promoted,
        "claims_dropped_as_duplicate": len(orphan_claims),
        "relations_rewritten": sum(1 for source, target in remap.items() if source != target),
    }
    for collection in ANCHORED_COLLECTIONS:
        for row in merged[collection]:
            row.pop("window_index", None)
            row.pop("window_promoted", None)
    for row in merged["questions"]:
        row["answer_claim_ids"] = list(dict.fromkeys(
            value for value in row.get("answer_claim_ids") or [] if value in claim_ids
        ))
    for row in merged["evidence_steps"]:
        row["produced_claim_ids"] = list(dict.fromkeys(
            value for value in row.get("produced_claim_ids") or [] if value in claim_ids
        ))
    for row in surviving_claims:
        row.pop("window_index", None)
    merged["claims"] = surviving_claims
    merged["evidence_relations"] = merged_relations["evidence_relations"]
    merged["claim_relations"] = merged_relations["claim_relations"]
    merged["merge_summary"] = merge_summary
    return merged
