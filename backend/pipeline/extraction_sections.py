"""Cut a source into the sections it was composed in, and ask about one at a time.

Whole-document extraction is a summarising task and behaves like one: on the
太16:21–23 母本 after #86, 132 sentences of substantive prose produced 66
represented sentences (50%), with output at 18,000 of a 32,000 ceiling. Nothing
was truncated -- given the whole document, the model picks favourites.

Two things were tried against that. Sliding windows (5 segments answered for, 15
visible) reached 98% in 26 calls. Asking one `##` section at a time, with a list
of its sentences appended and a verdict required for each, reached 100% in 4 --
and passed `validate_response` first try on both Opus 5 and DeepSeek v4 pro. So
the lever is the *closed question*, not the small chunk: "these 42 sentences,
account for each one" is answerable; "produce the argument layer" is not.

`##` is the right grouping boundary in the existing editorial structure; it is
not source text. The notes pipeline generates one unit per `##`
(`stage1_units.json` for this 母本
names four, and they are its four `##` sections), and the measurement agrees:
of 264 relations extraction produced within a section, 0 cross a `##`, while
every one of the 20 long-distance relations crosses a `###`. `###` is the
editorial skeleton *inside* a unit -- 釋經 / 神學意義 / 生活應用 / 附錄 -- which
files the fact under one heading and the inference drawn from it under the next.
Cutting there severs exactly the load_bearing edges this work exists to keep.

90 of 115 published transcripts carry no headings at all. Those get their
boundaries from the same subtitle generator the sermon editor already uses, and
the plan is cached: it is a model call, so an uncached rerun could resegment the
source and quietly make two extractions incomparable.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence

from backend.pipeline.source_projection import (
    EditorialHeading,
    LOCATOR_SPACE,
    heading_level,
    heading_text,
    project_script,
)

#: Headings at or above this level start a new section. See the module docstring
#: for why this is 2 and not 3.
DEFAULT_SECTION_LEVEL = 2

#: Versioned because it becomes part of the extraction identity whenever the
#: adaptive guard is enabled.  Changing how equal-sized choices are resolved
#: must invalidate the old section cache rather than silently moving anchors.
ADAPTIVE_SECTION_STRATEGY = "next_heading_then_spoken_row_balanced_min_chunks_v2"

#: How the boundaries were arrived at. Recorded on the plan because the two are
#: not equally trustworthy: one is where the author actually broke the text, the
#: other is a model's guess at where they would have.
FROM_SOURCE = "source_headings"
FROM_GENERATOR = "generated_subtitles"


@dataclass(frozen=True)
class Section:
    """One composition unit: the segments it spans, and what it is called."""

    index: int
    start: int
    end: int
    title: str
    # Set only for an internal transport split. The target sentence offsets are
    # relative to the unsplit parent H2 section, so two chunks cut from the same
    # spoken row still have disjoint, stable audit ids.
    parent_start: int | None = None
    parent_end: int | None = None
    sentence_start: int | None = None
    sentence_end: int | None = None

    @property
    def length(self) -> int:
        return self.end - self.start

    def contains(self, position: int) -> bool:
        return self.start <= position < self.end


def section_payload(section: Section) -> dict[str, Any]:
    """Stable serialized shape, omitting split-only fields on normal sections."""

    payload: dict[str, Any] = {
        "index": section.index,
        "start": section.start,
        "end": section.end,
        "title": section.title,
    }
    for field in ("parent_start", "parent_end", "sentence_start", "sentence_end"):
        value = getattr(section, field)
        if value is not None:
            payload[field] = value
    return payload


@dataclass(frozen=True)
class SectionPlan:
    sections: tuple[Section, ...]
    origin: str
    level: int = DEFAULT_SECTION_LEVEL
    max_section_sentences: int | None = None
    strategy: str | None = None
    split_lineage: tuple[dict[str, Any], ...] = ()

    def identity(self) -> dict[str, Any]:
        """Auditable plan identity, including editor-authored display labels."""

        return {
            **self.generation_identity(),
            "origin": self.origin,
            "titles_sha256": hashlib.sha256(
                json.dumps(
                    [s.title for s in self.sections], ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest(),
        }

    def generation_identity(self) -> dict[str, Any]:
        """Only the topology that changes what source text a model sees.

        Without it, a source resegmented by a later generator run reads as the
        same extraction and is skipped, leaving a package in staging that
        answers a question nobody is asking any more.

        Titles and provenance are deliberately absent.  They are editorial
        labels, not professor-authored source, and the extraction renderer does
        not send them to the model.  Renaming a heading can therefore rebuild
        package/display metadata without minting a new claim generation.
        """

        identity = {
            "section_count": len(self.sections),
            "sections": [section_generation_payload(section) for section in self.sections],
        }
        # Preserve the legacy identity for the default `##` plan.  Existing
        # completed sources must not rerun merely because an opt-in guard was
        # added for one oversized source.
        if self.level != DEFAULT_SECTION_LEVEL or self.split_lineage:
            identity["section_policy"] = {
                "level": self.level,
                "max_section_sentences": self.max_section_sentences,
                "strategy": self.strategy,
                "split_lineage": list(self.split_lineage),
            }
        return identity

    def section_of(self, position: int) -> Section | None:
        return next((s for s in self.sections if s.contains(position)), None)


def breadcrumb_for(headings: Sequence[EditorialHeading], position: int) -> str:
    """The enclosing heading chain at `position`, outermost first.

    Free context, and what the sub-headings are actually good for: a section
    reads differently once the model knows it sits in 附錄 under
    二、從馬可福音現象回應Wrede的錯誤解經.
    """

    chain: dict[int, str] = {}
    for heading in headings:
        if heading.boundary > position:
            continue
        chain = {depth: title for depth, title in chain.items() if depth < heading.level}
        chain[heading.level] = heading.title
    return " > ".join(chain[depth] for depth in sorted(chain))


def section_generation_payload(section: Section) -> dict[str, Any]:
    """The section coordinates used by extraction, without its display title."""

    payload = section_payload(section)
    payload.pop("title", None)
    return payload


def sections_from_headings(
    segments: Sequence[str], *, level: int = DEFAULT_SECTION_LEVEL
) -> list[Section]:
    """Compatibility wrapper projecting inline headings out of ``segments``."""

    projection = project_script([{"text": text} for text in segments])
    return sections_from_structure(
        len(projection.body_rows), projection.headings, level=level
    )


def sections_from_structure(
    body_length: int,
    headings: Sequence[EditorialHeading],
    *,
    level: int = DEFAULT_SECTION_LEVEL,
) -> list[Section]:
    """Split spoken body at editor-authored boundaries.

    The heading row itself is not inside either section.  Every boundary is in
    body coordinates, so adding a subtitle cannot renumber a source locator.
    """

    starts: list[int] = [0]
    titles: dict[int, tuple[int, str]] = {}
    for heading in headings:
        if heading.level <= level and heading.boundary < body_length:
            # Historical published files can carry an old overall title and a
            # later first-section title at the same H2 boundary. Both remain
            # visible editorial context; the later row is the deterministic
            # section label. New generated insertions still reject duplicate
            # boundaries before persistence.
            previous = titles.get(heading.boundary)
            if previous is None or heading.level >= previous[0]:
                titles[heading.boundary] = (heading.level, heading.title)
            if 0 < heading.boundary < body_length and heading.boundary > starts[-1]:
                starts.append(heading.boundary)
    return [
        Section(
            index=index + 1,
            start=start,
            end=end,
            title=titles.get(start, (0, ""))[1],
        )
        for index, (start, end) in enumerate(zip(starts, starts[1:] + [body_length]))
        if end > start
    ]


def has_section_headings(
    segments: Sequence[str], *, level: int = DEFAULT_SECTION_LEVEL
) -> bool:
    """Whether the source carries any heading recognized at this level.

    A lone heading at the start still proves that the source passed through the title
    workflow. It may deliberately remain one extraction section once generated
    fallback is disabled after governed persistence.
    """

    return any(
        (depth := heading_level(text)) is not None and depth <= level
        for text in segments
    )


def structure_has_section_headings(
    headings: Sequence[EditorialHeading], *, level: int = DEFAULT_SECTION_LEVEL,
    body_length: int | None = None,
) -> bool:
    """Whether editorial structure contains a section-level label."""

    return any(
        row.level <= level
        and (body_length is None or row.boundary < body_length)
        for row in headings
    )


def leading_untitled_span_end(
    segments: Sequence[str], *, level: int = DEFAULT_SECTION_LEVEL
) -> int | None:
    """Return the exclusive end of an untitled leading span, if one exists."""

    if not segments:
        return None
    for position, text in enumerate(segments):
        depth = heading_level(text)
        if depth is not None and depth <= level:
            return position or None
    return len(segments)


def leading_untitled_body_end(
    headings: Sequence[EditorialHeading],
    body_length: int,
    *,
    level: int = DEFAULT_SECTION_LEVEL,
) -> int | None:
    """Exclusive body-coordinate end of an unlabeled leading span, if any."""

    if body_length == 0:
        return None
    for heading in headings:
        if heading.level <= level:
            return heading.boundary or None
    return body_length


#: A callable that takes `[{"index": ..., "text": ...}]` and returns
#: `[{"after_index": ..., "text": "## …", "level": 1}]` -- the shape
#: `backend.pipeline.subtitle_generation.generate_subtitles` returns.
#:
#: It raises rather than returning `[]` when it fails, and this module lets that
#: through: an empty list here is one section, which is whole-document
#: extraction, which is what sectioning exists to replace. A failure that looks
#: like a short sermon is worse than a failure.
SubtitleProvider = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


class SectionBoundaryError(ValueError):
    """A generated boundary does not land on a segment of this source."""


class OversizedSectionError(ValueError):
    """A section is over its limit and has no safe next-level split."""


def validate_titled_section_plan(plan: SectionPlan, body_length: int) -> None:
    """Require a titled, contiguous partition before claim extraction.

    A title is editorial structure rather than spoken source, but an untitled
    section still means the sectioning preflight did not finish.  This check is
    origin-agnostic so legacy ``source_headings`` caches and partially headed
    published transcripts cannot bypass the stronger generated-plan contract.
    """

    if body_length == 0:
        if plan.sections:
            raise SectionBoundaryError("empty source has extraction sections")
        return
    if not plan.sections:
        raise SectionBoundaryError("section plan does not cover the spoken source")
    expected_start = 0
    for ordinal, section in enumerate(plan.sections, start=1):
        if (
            type(section.index) is not int
            or type(section.start) is not int
            or type(section.end) is not int
            or section.index != ordinal
            or section.start != expected_start
        ):
            raise SectionBoundaryError("extraction sections are not a contiguous partition")
        if (
            section.start < 0
            or section.end <= section.start
            or section.end > body_length
            or not isinstance(section.title, str)
            or not section.title.strip()
        ):
            raise SectionBoundaryError("extraction section is untitled or out of range")
        expected_start = section.end
    if expected_start != body_length:
        raise SectionBoundaryError("section plan does not cover the spoken source")


def has_transport_splits(plan: SectionPlan) -> bool:
    """Whether a cached plan needs an explicit legacy transport migration."""

    return bool(
        plan.max_section_sentences is not None
        or plan.strategy is not None
        or plan.split_lineage
        or any(
            section.parent_start is not None
            or section.parent_end is not None
            or section.sentence_start is not None
            or section.sentence_end is not None
            for section in plan.sections
        )
    )


def generated_plan_insertions(
    plan: SectionPlan, body_rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Render a frozen generated plan as persisted ``##`` editorial rows."""

    if plan.origin != FROM_GENERATOR:
        raise SectionBoundaryError("cached plan was not generated subtitles")
    if has_transport_splits(plan):
        raise SectionBoundaryError(
            "transport-split generated plan cannot be persisted as subtitles"
        )
    if not body_rows:
        raise SectionBoundaryError("cached generated plan has no spoken-source sections")
    validate_titled_section_plan(plan, len(body_rows))
    expected_start = 0
    insertions: list[dict[str, Any]] = []
    for ordinal, section in enumerate(plan.sections, start=1):
        if (
            section.start != expected_start
            or section.parent_start is not None
            or section.parent_end is not None
            or section.sentence_start is not None
            or section.sentence_end is not None
        ):
            raise SectionBoundaryError(
                "transport-split generated plan cannot be persisted as subtitles"
            )
        after_index = (
            "START"
            if section.start == 0
            else str(body_rows[section.start - 1].get("index"))
        )
        insertions.append(
            {"after_index": after_index, "text": section.title, "level": 1}
        )
        expected_start = section.end
    return insertions


def sections_from_generator(
    segments: Sequence[str],
    provider: SubtitleProvider,
    *,
    segment_indexes: Sequence[Any] | None = None,
) -> list[Section]:
    """Ask the subtitle generator where this source breaks.

    Generated headings are editorial structure.  Whether or not they are later
    persisted in the same JSON, the returned boundaries stay in spoken-body
    coordinates and therefore never move S locators.
    """

    indexes = list(segment_indexes) if segment_indexes is not None else list(range(len(segments)))
    if len(indexes) != len(segments):
        raise ValueError("segment_indexes must cover every spoken segment")
    normalized_indexes = [str(value) for value in indexes]
    if len(normalized_indexes) != len(set(normalized_indexes)):
        raise SectionBoundaryError("spoken source indexes are not unique")
    position_by_index = {
        value: position for position, value in enumerate(normalized_indexes)
    }
    paragraphs = [
        {"index": indexes[position], "text": text}
        for position, text in enumerate(segments)
    ]
    insertions = provider(paragraphs) or []
    boundaries: dict[int, str] = {0: ""}
    for row in insertions:
        if int(row.get("level") or 0) != 1:
            continue
        raw_after = row.get("after_index")
        after = "" if raw_after is None else str(raw_after)
        # "START" means before everything; otherwise the section opens at the
        # segment following the one named.
        position = (
            0
            if after.upper() == "START"
            else _position_after(after, position_by_index, len(segments))
        )
        if position is None:
            # Not skipped. A boundary nobody can place is a section this source
            # will never be asked about, and dropping it quietly leaves the
            # package looking like the model simply proposed fewer breaks.
            raise SectionBoundaryError(
                f"generated boundary after_index {after!r} does not name a segment of "
                f"this source (it has {len(segments)})"
            )
        boundaries[position] = heading_text(str(row.get("text") or ""))
    starts = sorted(boundaries)
    return [
        Section(index=index + 1, start=start, end=end, title=boundaries[start])
        for index, (start, end) in enumerate(zip(starts, starts[1:] + [len(segments)]))
        if end > start
    ]


def _position_after(
    after_index: str, position_by_index: dict[str, int], total: int
) -> int | None:
    """The segment a section opens at, or None if the index names no segment.

    `total` is allowed as a result: a heading proposed after the last segment
    opens a section with nothing in it, which the caller's `end > start` filter
    drops. That is a heading with no content, not a heading with no home, and
    only the second one is a fault worth failing the source over.
    """

    if after_index not in position_by_index:
        return None
    position = position_by_index[after_index] + 1
    return position if 0 < position <= total else None


def plan_sections(
    segments: Sequence[str],
    *,
    headings: Sequence[EditorialHeading] = (),
    segment_indexes: Sequence[Any] | None = None,
    level: int = DEFAULT_SECTION_LEVEL,
    provider: SubtitleProvider | None = None,
    sentence_counts: Sequence[int] | None = None,
    max_section_sentences: int | None = None,
) -> SectionPlan:
    """Section spoken source rows, generating boundaries only when it has none.

    A source that already carries `##` is never sent to the generator: those
    headings are where the text was actually composed, and a model's guess does
    not improve on that.

    Once the generator has been asked, the plan says so even if it came back
    with nothing to mark. A one-section plan recorded as `source_headings` is
    the report that hid this whole problem: it reads as a source that happens to
    have one section, when what happened is that the boundaries were generated
    and the generation had nothing in it.
    """

    if max_section_sentences is not None:
        if max_section_sentences <= 0:
            raise ValueError("max_section_sentences must be positive")
        if sentence_counts is None or len(sentence_counts) != len(segments):
            raise ValueError(
                "sentence_counts must cover every segment when adaptive sectioning is enabled"
            )
    if not segments:
        return SectionPlan(
            sections=(), origin=FROM_SOURCE, level=level,
            max_section_sentences=max_section_sentences,
            strategy=ADAPTIVE_SECTION_STRATEGY if max_section_sentences is not None else None,
        )
    sections = sections_from_structure(len(segments), headings, level=level)
    origin = FROM_SOURCE
    if not structure_has_section_headings(
        headings, level=level, body_length=len(segments)
    ) and provider is not None:
        generated = sections_from_generator(
            segments, provider, segment_indexes=segment_indexes
        )
        origin = FROM_GENERATOR
        if generated:
            sections = generated
    base = SectionPlan(
        sections=tuple(sections), origin=origin, level=level,
    )
    if max_section_sentences is None:
        return base
    return apply_section_limit(
        base,
        sentence_counts or (),
        headings=headings,
        max_section_sentences=max_section_sentences,
    )


def apply_section_limit(
    plan: SectionPlan,
    sentence_counts: Sequence[int],
    *,
    headings: Sequence[EditorialHeading],
    max_section_sentences: int,
) -> SectionPlan:
    """Split oversized sections at semantic headings, then spoken-row boundaries.

    The next-level headings remain the preferred atoms. Some reviewed sermons,
    however, have only generated H2 structure and a single H2 can still contain
    hundreds of audited sentences. A boundary between two spoken rows changes
    neither row, source coordinate nor source identity, so it is the deterministic
    fallback. If one storage row itself exceeds the limit, its audited sentence
    sequence is partitioned without changing the row, source locator or source
    file; the package records which extraction section produced every fragment
    so cross-section recovery can distinguish two chunks of the same row.

    A cap that changes nothing returns the original plan byte-for-byte at the
    identity level. This is a transport guard, not a reason to invalidate every
    completed extraction in the corpus.
    """

    if max_section_sentences <= 0:
        raise ValueError("max_section_sentences must be positive")
    result: list[Section] = []
    lineage: list[dict[str, Any]] = []
    for section in plan.sections:
        total = sum(sentence_counts[section.start:section.end])
        if total <= max_section_sentences:
            result.append(section)
            continue

        heading_starts = [section.start] + sorted({
            heading.boundary
            for heading in headings
            if heading.level == plan.level + 1
            and section.start < heading.boundary < section.end
        })
        heading_ends = heading_starts[1:] + [section.end]
        heading_weights = [
            sum(sentence_counts[start:end])
            for start, end in zip(heading_starts, heading_ends)
        ]
        if len(heading_starts) > 1 and max(heading_weights) <= max_section_sentences:
            starts = heading_starts
            ends = heading_ends
            atom_weights = heading_weights
            boundary_kind = f"h{plan.level + 1}"
        else:
            starts = list(range(section.start, section.end))
            ends = [start + 1 for start in starts]
            atom_weights = [sentence_counts[start] for start in starts]
            if atom_weights and max(atom_weights) > max_section_sentences:
                groups = _balanced_minimum_groups(
                    tuple(1 for _ in range(total)), max_section_sentences
                )
                parts = len(groups)
                parent_counts = sentence_counts[section.start:section.end]
                for part, (sentence_start, sentence_end) in enumerate(groups, start=1):
                    start, end = _row_span_for_sentence_range(
                        section.start,
                        parent_counts,
                        sentence_start,
                        sentence_end,
                    )
                    title = (
                        section.title
                        if start == section.start
                        else breadcrumb_for(headings, start) or section.title
                    )
                    result.append(Section(
                        index=0,
                        start=start,
                        end=end,
                        title=title,
                        parent_start=section.start,
                        parent_end=section.end,
                        sentence_start=sentence_start,
                        sentence_end=sentence_end,
                    ))
                    lineage.append({
                        "parent_section_index": section.index,
                        "part": part,
                        "parts": parts,
                        "start": start,
                        "end": end,
                        "sentence_start": sentence_start,
                        "sentence_end": sentence_end,
                        "boundary_kind": "sentence",
                    })
                continue
            boundary_kind = "spoken_row"
        groups = _balanced_minimum_groups(tuple(atom_weights), max_section_sentences)
        parts = len(groups)
        parent_prefix = [0]
        for count in sentence_counts[section.start:section.end]:
            parent_prefix.append(parent_prefix[-1] + count)
        for part, (atom_start, atom_end) in enumerate(groups, start=1):
            start = starts[atom_start]
            end = ends[atom_end - 1]
            title = section.title if start == section.start else breadcrumb_for(headings, start)
            sentence_start = parent_prefix[start - section.start]
            sentence_end = parent_prefix[end - section.start]
            result.append(Section(
                index=0,
                start=start,
                end=end,
                title=title or section.title,
                parent_start=section.start,
                parent_end=section.end,
                sentence_start=sentence_start,
                sentence_end=sentence_end,
            ))
            lineage.append({
                "parent_section_index": section.index,
                "part": part,
                "parts": parts,
                "start": start,
                "end": end,
                "sentence_start": sentence_start,
                "sentence_end": sentence_end,
                "boundary_kind": boundary_kind,
            })

    if not lineage:
        return plan
    sections = tuple(
        replace(row, index=index)
        for index, row in enumerate(result, start=1)
    )
    lineage_by_span = {
        (
            row["start"],
            row["end"],
            row.get("sentence_start"),
            row.get("sentence_end"),
        ): row
        for row in lineage
    }
    normalized_lineage = tuple(
        {
            **lineage_by_span[
                (
                    section.start,
                    section.end,
                    section.sentence_start,
                    section.sentence_end,
                )
            ],
            "section_index": section.index,
        }
        for section in sections
        if (
            section.start,
            section.end,
            section.sentence_start,
            section.sentence_end,
        ) in lineage_by_span
    )
    return SectionPlan(
        sections=sections,
        origin=plan.origin,
        level=plan.level,
        max_section_sentences=max_section_sentences,
        strategy=ADAPTIVE_SECTION_STRATEGY,
        split_lineage=normalized_lineage,
    )


def _row_span_for_sentence_range(
    parent_start: int,
    row_sentence_counts: Sequence[int],
    sentence_start: int,
    sentence_end: int,
) -> tuple[int, int]:
    """Smallest spoken-row span containing a parent-relative sentence range."""

    if sentence_start < 0 or sentence_end <= sentence_start:
        raise OversizedSectionError("invalid sentence-range partition")
    cursor = 0
    first: int | None = None
    last: int | None = None
    for offset, count in enumerate(row_sentence_counts):
        next_cursor = cursor + count
        if first is None and sentence_start < next_cursor:
            first = parent_start + offset
        if sentence_end <= next_cursor:
            last = parent_start + offset + 1
            break
        cursor = next_cursor
    if first is None or last is None:
        raise OversizedSectionError("sentence-range partition falls outside its parent section")
    return first, last


def _balanced_minimum_groups(
    weights: tuple[int, ...], limit: int,
) -> tuple[tuple[int, int], ...]:
    """Return contiguous half-open atom ranges, minimizing calls then imbalance."""

    # With positive weights, filling each group as far as possible establishes
    # the minimum group count.  The second pass keeps that count but chooses
    # boundaries closest to equal totals.  It is polynomial even if a source
    # has dozens of subheadings; enumerating every partition is not.
    minimum_groups = 0
    position = 0
    while position < len(weights):
        total = 0
        while position < len(weights) and total + weights[position] <= limit:
            total += weights[position]
            position += 1
        minimum_groups += 1

    target_total = sum(weights)

    @lru_cache(maxsize=None)
    def best(
        start: int, groups_left: int,
    ) -> tuple[int, tuple[int, ...], tuple[tuple[int, int], ...]] | None:
        if groups_left == 0:
            return (0, (), ()) if start == len(weights) else None
        if len(weights) - start < groups_left:
            return None
        choices: list[tuple[int, tuple[int, ...], tuple[tuple[int, int], ...]]] = []
        total = 0
        last_end = len(weights) - groups_left + 1
        for end in range(start + 1, last_end + 1):
            total += weights[end - 1]
            if total > limit:
                break
            suffix = best(end, groups_left - 1)
            if suffix is None:
                continue
            cost = (total * minimum_groups - target_total) ** 2 + suffix[0]
            choices.append((cost, (total,) + suffix[1], ((start, end),) + suffix[2]))
        return min(choices, default=None, key=lambda row: (row[0], row[1]))

    selected = best(0, minimum_groups)
    if selected is None:
        raise OversizedSectionError(f"no contiguous partition satisfies sentence limit {limit}")
    return selected[2]


def load_cached_plan(
    path: Path, source_sha256: str, *, level: int = DEFAULT_SECTION_LEVEL,
    max_section_sentences: int | None = None,
    editorial_structure_sha256: str | None = None,
    editorial_topology_sha256: str | None = None,
    accept_any_max: bool = False,
) -> SectionPlan | None:
    """Load a plan bound to the exact spoken-source body identity."""

    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    cached_locator_space = payload.get("locator_space")
    if cached_locator_space not in {None, LOCATOR_SPACE}:
        return None
    recorded_source_sha256 = payload.get("source_body_sha256") or payload.get("source_sha256")
    if recorded_source_sha256 != source_sha256:
        return None
    recorded_structure_sha256 = payload.get("editorial_structure_sha256")
    recorded_topology_sha256 = payload.get("editorial_topology_sha256")
    if recorded_topology_sha256 is not None:
        if (
            editorial_topology_sha256 is None
            or recorded_topology_sha256 != editorial_topology_sha256
        ):
            return None
    elif (
        recorded_structure_sha256 is not None
        and editorial_structure_sha256 is not None
        and recorded_structure_sha256 != editorial_structure_sha256
    ):
        # Legacy plans have no label-free topology identity. Fail closed once;
        # the caller can deterministically rebuild a source-heading plan.
        return None
    cached_level = int(payload.get("section_level", DEFAULT_SECTION_LEVEL))
    cached_max = payload.get("max_section_sentences")
    cached_strategy = payload.get("section_strategy")
    expected_strategy = (
        ADAPTIVE_SECTION_STRATEGY if max_section_sentences is not None else None
    )
    if cached_level != level:
        return None
    if not accept_any_max and (cached_max, cached_strategy) != (
        max_section_sentences, expected_strategy,
    ):
        return None
    return SectionPlan(
        sections=tuple(Section(**row) for row in payload["sections"]),
        origin=str(payload.get("origin") or FROM_SOURCE),
        level=cached_level,
        max_section_sentences=cached_max,
        strategy=cached_strategy,
        split_lineage=tuple(payload.get("split_lineage") or ()),
    )


def save_plan(
    path: Path,
    plan: SectionPlan,
    source_sha256: str,
    *,
    source_file_sha256: str | None = None,
    editorial_structure_sha256: str | None = None,
    editorial_topology_sha256: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            {
                "source_sha256": source_sha256,
                "source_body_sha256": source_sha256,
                "source_file_sha256": source_file_sha256,
                "editorial_structure_sha256": editorial_structure_sha256,
                "editorial_topology_sha256": editorial_topology_sha256,
                "locator_space": LOCATOR_SPACE,
                "origin": plan.origin,
                "section_level": plan.level,
                "max_section_sentences": plan.max_section_sentences,
                "section_strategy": plan.strategy,
                "split_lineage": list(plan.split_lineage),
                "sections": [section_payload(section) for section in plan.sections],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    encoded = content.encode("utf-8")
    if path.is_file() and path.read_bytes() == encoded:
        return
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


# --------------------------------------------------------------------------
# Combining
# --------------------------------------------------------------------------

#: Every collection a response carries, and the field holding its id.
ID_KEYS = {
    "questions": "question_id",
    "positions": "position_id",
    "observations": "observation_id",
    "evidence_steps": "evidence_step_id",
    "claims": "claim_id",
    "evidence_relations": "relation_id",
    "claim_relations": "claim_relation_id",
}
#: Fields that reference another record by id, and the collection they point into.
REFERENCE_KEYS = {
    "answer_claim_ids": "claims",
    "produced_claim_ids": "claims",
    "evidence_step_ids": "evidence_steps",
    "opposed_position_ids": "positions",
}


def namespace_response(response: dict[str, Any], section: Section) -> dict[str, Any]:
    """Prefix one section's short model-facing ids so responses can be combined.

    Every section is asked for Q001/OBS001/E001 in its own little world; without
    a prefix the second section's OBS001 silently overwrites the first's.
    """

    prefix = f"P{section.index:02d}-"
    out = {key: [dict(row) for row in (response.get(key) or [])] for key in ID_KEYS}
    for collection, id_key in ID_KEYS.items():
        for row in out[collection]:
            row[id_key] = prefix + str(row[id_key])
    for collection in out.values():
        for row in collection:
            for field in REFERENCE_KEYS:
                if field in row:
                    row[field] = [prefix + str(value) for value in row[field] or []]
            for field in ("from_id", "to_id"):
                if field in row:
                    row[field] = prefix + str(row[field])
    return out


def combine_sections(
    responses: Sequence[tuple[Section, dict[str, Any]]]
) -> dict[str, Any]:
    """Concatenate per-section responses into one document-level response.

    Target sentence ranges do not overlap, so this is concatenation and nothing
    else -- no ownership rule, no span matching, no dedup. Sentence-range
    transport chunks can share one storage row while still owning disjoint
    audited sentences; that is different from the overlapping window this
    design replaced.
    """

    combined: dict[str, Any] = {key: [] for key in ID_KEYS}
    for section, response in responses:
        renamed = namespace_response(response, section)
        for collection in ID_KEYS:
            combined[collection].extend(renamed[collection])
    return combined
