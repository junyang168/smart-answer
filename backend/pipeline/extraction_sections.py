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

`##` is the right cut because it is where the manuscript was written. The
notes pipeline generates one unit per `##` (`stage1_units.json` for this 母本
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
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence

HEADING_PATTERN = re.compile(r"^(#{1,6})\s")

#: Headings at or above this level start a new section. See the module docstring
#: for why this is 2 and not 3.
DEFAULT_SECTION_LEVEL = 2

#: Versioned because it becomes part of the extraction identity whenever the
#: adaptive guard is enabled.  Changing how equal-sized choices are resolved
#: must invalidate the old section cache rather than silently moving anchors.
ADAPTIVE_SECTION_STRATEGY = "next_heading_balanced_min_chunks_v1"

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

    @property
    def length(self) -> int:
        return self.end - self.start

    def contains(self, position: int) -> bool:
        return self.start <= position < self.end


@dataclass(frozen=True)
class SectionPlan:
    sections: tuple[Section, ...]
    origin: str
    level: int = DEFAULT_SECTION_LEVEL
    max_section_sentences: int | None = None
    strategy: str | None = None

    def identity(self) -> dict[str, Any]:
        """What has to enter the extraction fingerprint.

        Without it, a source resegmented by a later generator run reads as the
        same extraction and is skipped, leaving a package in staging that
        answers a question nobody is asking any more.
        """

        identity = {
            "origin": self.origin,
            "section_count": len(self.sections),
            "boundaries": [section.start for section in self.sections],
            "titles_sha256": hashlib.sha256(
                json.dumps([s.title for s in self.sections], ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:16],
        }
        # Preserve the legacy identity for the default `##` plan.  Existing
        # completed sources must not rerun merely because an opt-in guard was
        # added for one oversized source.
        if self.level != DEFAULT_SECTION_LEVEL or self.max_section_sentences is not None:
            identity["section_policy"] = {
                "level": self.level,
                "max_section_sentences": self.max_section_sentences,
                "strategy": self.strategy,
            }
        return identity

    def section_of(self, position: int) -> Section | None:
        return next((s for s in self.sections if s.contains(position)), None)


def heading_level(text: str) -> int | None:
    match = HEADING_PATTERN.match(str(text).lstrip())
    return len(match.group(1)) if match else None


def heading_text(text: str) -> str:
    return HEADING_PATTERN.sub("", str(text).lstrip(), count=1).strip()


def breadcrumb_for(segments: Sequence[str], position: int) -> str:
    """The enclosing heading chain at `position`, outermost first.

    Free context, and what the sub-headings are actually good for: a section
    reads differently once the model knows it sits in 附錄 under
    二、從馬可福音現象回應Wrede的錯誤解經.
    """

    chain: dict[int, str] = {}
    for index in range(min(position + 1, len(segments))):
        level = heading_level(segments[index])
        if level is None:
            continue
        chain = {depth: title for depth, title in chain.items() if depth < level}
        chain[level] = heading_text(segments[index])
    return " > ".join(chain[depth] for depth in sorted(chain))


def sections_from_headings(
    segments: Sequence[str], *, level: int = DEFAULT_SECTION_LEVEL
) -> list[Section]:
    """Split at the headings the source already carries."""

    starts: list[int] = [0]
    titles: dict[int, str] = {}
    for position, text in enumerate(segments):
        depth = heading_level(text)
        if depth is not None and depth <= level:
            titles[position] = heading_text(text)
            if position > starts[-1]:
                starts.append(position)
    return [
        Section(index=index + 1, start=start, end=end, title=titles.get(start, ""))
        for index, (start, end) in enumerate(zip(starts, starts[1:] + [len(segments)]))
        if end > start
    ]


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


def sections_from_generator(
    segments: Sequence[str], provider: SubtitleProvider
) -> list[Section]:
    """Ask the subtitle generator where this source breaks.

    The generated titles are *not* written back into the source. Inserting them
    would shift every S-number after the insertion point, which moves anchors,
    the ledger's inventory, and `source_sha256` -- and only the boundaries are
    needed here. The titles ride along on the plan instead.
    """

    paragraphs = [{"index": str(position), "text": text} for position, text in enumerate(segments)]
    insertions = provider(paragraphs) or []
    boundaries: dict[int, str] = {0: ""}
    for row in insertions:
        if int(row.get("level") or 0) != 1:
            continue
        after = str(row.get("after_index") or "")
        # "START" means before everything; otherwise the section opens at the
        # segment following the one named.
        position = 0 if after.upper() == "START" else _position_after(after, len(segments))
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


def _position_after(after_index: str, total: int) -> int | None:
    """The segment a section opens at, or None if the index names no segment.

    `total` is allowed as a result: a heading proposed after the last segment
    opens a section with nothing in it, which the caller's `end > start` filter
    drops. That is a heading with no content, not a heading with no home, and
    only the second one is a fault worth failing the source over.
    """

    if not after_index.lstrip("-").isdigit():
        return None
    position = int(after_index) + 1
    return position if 0 < position <= total else None


def plan_sections(
    segments: Sequence[str],
    *,
    level: int = DEFAULT_SECTION_LEVEL,
    provider: SubtitleProvider | None = None,
    sentence_counts: Sequence[int] | None = None,
    max_section_sentences: int | None = None,
) -> SectionPlan:
    """Section the source, generating boundaries only when it has none.

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
    sections = sections_from_headings(segments, level=level)
    origin = FROM_SOURCE
    if len(sections) <= 1 and provider is not None:
        generated = sections_from_generator(segments, provider)
        origin = FROM_GENERATOR
        if len(generated) > 1:
            sections = generated
    if max_section_sentences is not None:
        sections = _split_oversized_sections(
            segments, sections, sentence_counts or (), level=level,
            max_section_sentences=max_section_sentences,
        )
    return SectionPlan(
        sections=tuple(sections), origin=origin, level=level,
        max_section_sentences=max_section_sentences,
        strategy=ADAPTIVE_SECTION_STRATEGY if max_section_sentences is not None else None,
    )


def _split_oversized_sections(
    segments: Sequence[str], sections: Sequence[Section], sentence_counts: Sequence[int],
    *, level: int, max_section_sentences: int,
) -> list[Section]:
    """Split only oversized sections, at the next heading depth.

    The next-level headings are atomic boundaries, not an instruction to make
    one model call per heading.  Contiguous atoms are grouped into the fewest
    chunks that fit the limit; among equally small plans the most balanced one
    wins.  This keeps the normal case at two calls.
    """

    result: list[Section] = []
    for section in sections:
        total = sum(sentence_counts[section.start:section.end])
        if total <= max_section_sentences:
            result.append(section)
            continue

        starts = [section.start] + [
            position
            for position in range(section.start + 1, section.end)
            if heading_level(segments[position]) == level + 1
        ]
        if len(starts) == 1:
            raise OversizedSectionError(
                f"section {section.index} has {total} sentences (limit "
                f"{max_section_sentences}) but no level-{level + 1} heading boundary"
            )
        ends = starts[1:] + [section.end]
        atom_weights = [sum(sentence_counts[start:end]) for start, end in zip(starts, ends)]
        if max(atom_weights) > max_section_sentences:
            position = atom_weights.index(max(atom_weights))
            raise OversizedSectionError(
                f"section {section.index} has a level-{level + 1} block with "
                f"{atom_weights[position]} sentences (limit {max_section_sentences})"
            )
        groups = _balanced_minimum_groups(tuple(atom_weights), max_section_sentences)
        for atom_start, atom_end in groups:
            start = starts[atom_start]
            end = ends[atom_end - 1]
            title = section.title if start == section.start else breadcrumb_for(segments, start)
            result.append(Section(index=0, start=start, end=end, title=title))

    return [
        Section(index=index, start=row.start, end=row.end, title=row.title)
        for index, row in enumerate(result, start=1)
    ]


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
) -> SectionPlan | None:
    """A generated plan is only reusable for the exact source it was made from."""

    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("source_sha256") != source_sha256:
        return None
    cached_level = int(payload.get("section_level", DEFAULT_SECTION_LEVEL))
    cached_max = payload.get("max_section_sentences")
    cached_strategy = payload.get("section_strategy")
    expected_strategy = (
        ADAPTIVE_SECTION_STRATEGY if max_section_sentences is not None else None
    )
    if (cached_level, cached_max, cached_strategy) != (
        level, max_section_sentences, expected_strategy,
    ):
        return None
    return SectionPlan(
        sections=tuple(Section(**row) for row in payload["sections"]),
        origin=str(payload.get("origin") or FROM_SOURCE),
        level=cached_level,
        max_section_sentences=cached_max,
        strategy=cached_strategy,
    )


def save_plan(path: Path, plan: SectionPlan, source_sha256: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source_sha256": source_sha256,
                "origin": plan.origin,
                "section_level": plan.level,
                "max_section_sentences": plan.max_section_sentences,
                "section_strategy": plan.strategy,
                "sections": [vars(section) for section in plan.sections],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


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

    Sections do not overlap, so this is concatenation and nothing else -- no
    ownership rule, no span matching, no dedup. That is the whole reason the
    section is a better unit than the overlapping window it replaced.
    """

    combined: dict[str, Any] = {key: [] for key in ID_KEYS}
    for section, response in responses:
        renamed = namespace_response(response, section)
        for collection in ID_KEYS:
            combined[collection].extend(renamed[collection])
    return combined
