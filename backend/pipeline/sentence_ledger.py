"""Account for every sentence of a source against the argument layer.

Every gate downstream of extraction reads only the claim layer, so material
that exists in a source but never became a claim is indistinguishable from
material the professor never produced -- both render as "no material". #64 is
the worked case: the grounding gate deleted a sentence that appears verbatim
twice in the manuscript the draft was written from, and it was not wrong. Its
information was complete inside its own field of view; the field of view was.

So the denominator here is the source text, never the extraction output. A
count taken from what extraction produced cannot show what extraction missed,
and every other property of this module rests on that one.

The verdicts are deliberately three, not two:

    represented   an anchored record's span covers this sentence
    excluded      an ExclusionRecord says why it carries no argument, and who
                  decided
    unprocessed   neither -- not a judgement, an unanswered question

`unprocessed` is what makes an open-ended recall problem convergent. Extraction
does not have to be complete; it only has to be true that every sentence was
answered for, even when the answer is "deliberately unused".

Nothing here calls a model and nothing here writes to the store. Reconciliation
is arithmetic over spans; producing the missing material is the second pass,
and approving an exclusion is a person's decision.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from backend.api.canonical_repository.knowledge_models import (
    SentenceInventoryRecord,
    SentenceReconciliationRecord,
)
from backend.pipeline.sentence_ledger_vocabulary import (  # noqa: F401  (re-exported)
    AUTO_TERMINAL_REASONS,
    BULK_APPROVABLE_REASONS,
    HUMAN_ONLY_REASONS,
    REASON_CODES,
    is_terminal,
)
from backend.pipeline.base_contract_coverage import (
    ScriptureRef,
    load_bearing_flags,
    parse_scripture_refs,
    sentence_spans,
)

#: Terminal states. `unprocessed` is the only one that blocks.
REPRESENTED = "represented"
EXCLUDED = "excluded"
UNPROCESSED = "unprocessed"

#: How a `represented` verdict was reached. Only `EXACT_SPAN` may conclude it.
EXACT_SPAN = "exact_span"
PROPOSED_LINK = "proposed_link"
NO_MATCH = "none"

#: Reason codes live in their own module so extraction can name them without
#: importing the canonical-repository models. See `sentence_ledger_vocabulary`.


def sentence_id(source_id: str, segment_index: int, text: str, ordinal: int = 0) -> str:
    """A key that survives revision of everything except this sentence.

    Content-hashed rather than positional: inserting a sentence into a
    manuscript must not invalidate the verdicts recorded for the ones after it.
    """

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    suffix = f":{ordinal}" if ordinal else ""
    return f"{source_id}:{segment_index}:{digest[:12]}{suffix}"


@dataclass(frozen=True)
class AnchoredSpan:
    """A claim-layer record placed back onto the source text it was cut from."""

    record_id: str
    segment_index: int
    start: int
    end: int


def build_inventory(
    segments: Sequence[tuple[int, str]],
    *,
    source_id: str,
    source_sha256: str | None = None,
) -> list[SentenceInventoryRecord]:
    """Split each segment into sentences and address every one of them.

    `segments` is `(segment_index, text)` so the caller owns the segmentation.
    Extraction and passage scoping must agree on it -- a sentence addressed
    against one segmentation cannot be compared with a span anchored against
    another.
    """

    records: list[SentenceInventoryRecord] = []
    for segment_index, text in segments:
        seen: dict[str, int] = {}
        for start, end in sentence_spans(text):
            sentence = text[start:end]
            ordinal = seen.get(sentence, 0)
            seen[sentence] = ordinal + 1
            records.append(
                SentenceInventoryRecord(
                    sentence_id=sentence_id(source_id, segment_index, sentence, ordinal),
                    source_id=source_id,
                    segment_index=segment_index,
                    ordinal=ordinal,
                    text=sentence,
                    sentence_sha256=hashlib.sha256(sentence.encode("utf-8")).hexdigest(),
                    char_start=start,
                    char_end=end,
                    source_sha256=source_sha256,
                )
            )
    return records


def _covering(sentence: SentenceInventoryRecord, spans: Iterable[AnchoredSpan]) -> list[str]:
    """Record ids whose anchored span overlaps this sentence's span.

    Overlap, not containment: an evidence step routinely quotes a clause of a
    sentence, and a sentence routinely sits inside a longer quoted excerpt.
    Both mean the argument layer reached this text.
    """

    hits = [
        span.record_id
        for span in spans
        if span.segment_index == sentence.segment_index
        and span.start < sentence.char_end
        and sentence.char_start < span.end
    ]
    return sorted(dict.fromkeys(hits))


def reconcile(
    inventory: Sequence[SentenceInventoryRecord],
    anchored: Sequence[AnchoredSpan],
    *,
    exclusions_by_sentence: dict[str, str] | None = None,
    target: ScriptureRef | None = None,
    reconciled_against: str | None = None,
) -> list[SentenceReconciliationRecord]:
    """Give every inventory sentence exactly one verdict.

    `exclusions_by_sentence` maps a sentence id to the id of an exclusion that
    is *already terminal* -- the caller decides that, because terminality
    depends on the reason code and on whether a human approved it, neither of
    which is visible here.
    """

    exclusions = exclusions_by_sentence or {}
    rows: list[SentenceReconciliationRecord] = []
    for sentence in inventory:
        covering = _covering(sentence, anchored)
        exclusion_id = exclusions.get(sentence.sentence_id)
        if covering:
            status, match_kind = REPRESENTED, EXACT_SPAN
        elif exclusion_id:
            status, match_kind = EXCLUDED, NO_MATCH
        else:
            status, match_kind = UNPROCESSED, NO_MATCH
        flags = (
            load_bearing_flags(sentence.text, parse_scripture_refs(sentence.text), target)
            if target is not None
            else []
        )
        rows.append(
            SentenceReconciliationRecord(
                reconciliation_id=f"REC-{sentence.sentence_id}",
                sentence_id=sentence.sentence_id,
                source_id=sentence.source_id,
                status=status,
                match_kind=match_kind,
                represented_by=covering,
                exclusion_id=exclusion_id,
                triage_flags=flags,
                reconciled_against=reconciled_against,
            )
        )
    return rows


@dataclass
class LedgerSummary:
    represented: int = 0
    excluded: int = 0
    unprocessed: int = 0
    unprocessed_flagged: int = 0
    unprocessed_ids: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.represented + self.excluded + self.unprocessed

    @property
    def blocks(self) -> bool:
        return self.unprocessed > 0


#: What kind of sentence this is, structurally. The categories exist because a
#: single coverage number is not a target anyone can act on: markdown headings
#: are 51 of the 208 sentences in the 太16:21–23 母本 and are represented 0% of
#: the time by design, so they drag the total down while telling nobody
#: anything. `prose` is the denominator that matters, and #88's acceptance is
#: written against it -- which it could not be while the split was recomputed
#: by hand for each report.
HEADING = "heading"
SCRIPTURE_QUOTATION = "scripture_quotation"
LIST_ITEM = "list_item"
FRAGMENT = "fragment"
PROSE = "prose"
SENTENCE_CATEGORIES = (PROSE, HEADING, SCRIPTURE_QUOTATION, LIST_ITEM, FRAGMENT)

#: Below this many characters a prose sentence is a lead-in, not a claim about
#: anything -- "太 16:21 記載：" and its kin. Structural, not a quality judgement.
FRAGMENT_MAX_LENGTH = 12

_HEADING_PATTERN = re.compile(r"^#{1,6}\s")
_LIST_PATTERN = re.compile(r"^([-*+]|\d+[.)])\s")


def classify_sentence(segment_text: str, sentence_text: str) -> str:
    """Categorise one sentence by the block it came from, then by its own shape.

    Block first: a scripture quotation split across two sentences is still a
    quotation in both halves, and a heading is a heading however it punctuates.
    """

    head = str(segment_text).lstrip()
    if _HEADING_PATTERN.match(head):
        return HEADING
    if head.startswith(">"):
        return SCRIPTURE_QUOTATION
    if _LIST_PATTERN.match(head):
        return LIST_ITEM
    if len(str(sentence_text).strip()) < FRAGMENT_MAX_LENGTH:
        return FRAGMENT
    return PROSE


def summarise_by_category(
    inventory: Sequence[SentenceInventoryRecord],
    rows: Sequence[SentenceReconciliationRecord],
    segments: dict[int, str],
) -> dict[str, LedgerSummary]:
    """Per-category counts, so a coverage change can be read where it happened."""

    by_sentence = {row.sentence_id: row for row in rows}
    summaries = {category: LedgerSummary() for category in SENTENCE_CATEGORIES}
    for sentence in inventory:
        row = by_sentence.get(sentence.sentence_id)
        if row is None:
            continue
        category = classify_sentence(segments.get(sentence.segment_index, ""), sentence.text)
        summary = summaries[category]
        if row.status == REPRESENTED:
            summary.represented += 1
        elif row.status == EXCLUDED:
            summary.excluded += 1
        else:
            summary.unprocessed += 1
            summary.unprocessed_ids.append(sentence.sentence_id)
            if row.triage_flags:
                summary.unprocessed_flagged += 1
    return summaries


def summarise(rows: Sequence[SentenceReconciliationRecord]) -> LedgerSummary:
    """Counts, plus the ids that block -- a gate must name them, not just count."""

    summary = LedgerSummary()
    for row in rows:
        if row.status == REPRESENTED:
            summary.represented += 1
        elif row.status == EXCLUDED:
            summary.excluded += 1
        else:
            summary.unprocessed += 1
            summary.unprocessed_ids.append(row.sentence_id)
            if row.triage_flags:
                summary.unprocessed_flagged += 1
    return summary
