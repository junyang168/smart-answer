"""Tests for the source-as-denominator ledger."""

from __future__ import annotations

import pytest

from backend.pipeline.base_contract_coverage import BOOK_CODE_TO_CHINESE, ScriptureRef, parse_passage_range
from backend.pipeline.sentence_ledger import (
    AUTO_TERMINAL_REASONS,
    EXACT_SPAN,
    EXCLUDED,
    REPRESENTED,
    UNPROCESSED,
    AnchoredSpan,
    build_inventory,
    is_terminal,
    reconcile,
    sentence_id,
    summarise,
)

SEGMENT = "猶太制度中，君王與祭司的職分是嚴格分開的。耶穌卻同時擁有這三個職分。"


def target() -> ScriptureRef:
    """`overlaps` compares raw book strings, so the OSIS code must be translated.

    `parse_passage_range` yields `book='Matt'` while a manuscript cites 太16:21,
    and comparing them directly returns an empty scope with no error at all.
    """

    raw = parse_passage_range("Matt.16.13-Matt.16.20")
    return ScriptureRef(BOOK_CODE_TO_CHINESE.get(raw.book, raw.book), raw.chapter, raw.start_verse, raw.end_verse)


def test_inventory_addresses_every_sentence_with_its_span():
    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    assert [row.text for row in inventory] == [
        "猶太制度中，君王與祭司的職分是嚴格分開的。",
        "耶穌卻同時擁有這三個職分。",
    ]
    for row in inventory:
        assert SEGMENT[row.char_start : row.char_end] == row.text


def test_editing_a_source_keeps_the_untouched_sentences_addressable():
    """The property an ordinal key cannot have.

    Revise a manuscript and only the sentences whose text changed may lose
    their identity; an ordinal would shift on the insertion and orphan every
    verdict recorded after it.
    """

    before = build_inventory([(1, SEGMENT)], source_id="src")
    inserted = "此處先插入一句新的說明。" + SEGMENT
    after = build_inventory([(1, inserted)], source_id="src")

    assert {row.sentence_id for row in before} <= {row.sentence_id for row in after}
    assert len(after) == len(before) + 1


def test_a_repeated_sentence_gets_distinct_ids():
    repeated = "好。好。"
    inventory = build_inventory([(1, repeated)], source_id="src")
    assert len({row.sentence_id for row in inventory}) == len(inventory) == 2


def test_a_covering_span_makes_a_sentence_represented():
    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    first = inventory[0]
    # An evidence step routinely quotes a clause, not the whole sentence.
    anchored = [AnchoredSpan("E009", 1, first.char_start + 2, first.char_start + 8)]
    rows = reconcile(inventory, anchored)
    assert rows[0].status == REPRESENTED
    assert rows[0].match_kind == EXACT_SPAN
    assert rows[0].represented_by == ["E009"]
    assert rows[1].status == UNPROCESSED


def test_a_span_in_another_segment_does_not_cover_this_one():
    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    anchored = [AnchoredSpan("E009", 2, 0, 999)]
    assert all(row.status == UNPROCESSED for row in reconcile(inventory, anchored))


def test_an_exclusion_is_terminal_and_names_its_record():
    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    rows = reconcile(inventory, [], exclusions_by_sentence={inventory[0].sentence_id: "EX-1"})
    assert rows[0].status == EXCLUDED
    assert rows[0].exclusion_id == "EX-1"
    assert rows[1].status == UNPROCESSED


def test_every_sentence_reaches_exactly_one_verdict():
    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    rows = reconcile(inventory, [])
    assert len(rows) == len(inventory)
    assert {row.status for row in rows} <= {REPRESENTED, EXCLUDED, UNPROCESSED}


def test_the_summary_names_what_blocks_rather_than_only_counting():
    """A gate that reports a number cannot be acted on; one that names the
    sentences can. #64 failed with a coverage report that had already listed
    every gap in plain text."""

    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    summary = summarise(reconcile(inventory, []))
    assert summary.blocks
    assert summary.unprocessed == 2
    assert summary.unprocessed_ids == [row.sentence_id for row in inventory]


def test_flags_are_carried_for_triage_but_do_not_change_the_verdict():
    """`load_bearing_flags` ranks a review queue. It must not authorise anything.

    Both sentences the grounding gate deleted in #64 are flag-negative, so a
    tier built on these flags would let the model retire what it missed.
    """

    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    rows = reconcile(inventory, [], target=target())
    assert all(row.status == UNPROCESSED for row in rows)
    # The verdict is identical with and without the flags being computed.
    assert [r.status for r in rows] == [r.status for r in reconcile(inventory, [])]


def test_terminality_follows_the_reason_code_not_the_sentence():
    assert is_terminal("duplicate_of", approved=False) is True
    assert is_terminal("not_exegesis", approved=False) is False
    assert is_terminal("not_exegesis", approved=True) is True
    # The interpretive call that failed in #64 and #53 is never delegated.
    assert is_terminal("background_only", approved=False) is False
    assert is_terminal("deferred", approved=False) is False
    assert AUTO_TERMINAL_REASONS == {"duplicate_of"}


def test_an_unknown_reason_code_is_refused():
    with pytest.raises(ValueError):
        is_terminal("looks_unimportant", approved=False)


def test_the_ledger_records_carry_review_state():
    """#68's law: anything a gate reads must be a record with an owner."""

    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    row = reconcile(inventory, [])[0]
    for record in (inventory[0], row):
        assert record.review_status == "candidate"
        assert record.revision == 1


def test_sentence_id_is_stable_and_content_addressed():
    assert sentence_id("src", 1, "甲。") == sentence_id("src", 1, "甲。")
    assert sentence_id("src", 1, "甲。") != sentence_id("src", 2, "甲。")
    assert sentence_id("src", 1, "甲。") != sentence_id("src", 1, "乙。")


# ---------------------------------------------------------------------------
# placing fragments back on the source
# ---------------------------------------------------------------------------

from backend.pipeline.sentence_ledger_runner import place_fragments  # noqa: E402

SEGMENTS = [(1, "猶太制度中，君王與祭司的職分是嚴格分開的。"), (2, "耶穌卻同時擁有這三個職分。")]


def _package(excerpt: str, *, cited: bool = True) -> dict:
    return {
        "source_fragments": [{"fragment_id": "FR-1", "verbatim_excerpt": excerpt}],
        "evidence_steps": [{"evidence_step_id": "E009", "source_fragment_ids": ["FR-1"] if cited else []}],
    }


def test_a_fragment_is_placed_by_its_text_not_its_claimed_index():
    """The index is what broke, not the text.

    Across the 24 staged packages 100% of excerpts are still verbatim in their
    source while only 20% resolve at the index they claim, so locating by
    content is the only thing that recovers them -- and it is exact, not fuzzy.
    """

    spans, unplaced = place_fragments(_package("君王與祭司"), SEGMENTS)
    assert unplaced == []
    assert len(spans) == 1
    assert spans[0].record_id == "E009"
    assert spans[0].segment_index == 1
    assert SEGMENTS[0][1][spans[0].start : spans[0].end] == "君王與祭司"


def test_an_ambiguous_fragment_is_left_unplaced_rather_than_guessed():
    segments = [(1, "同一句話。"), (2, "同一句話。")]
    spans, unplaced = place_fragments(_package("同一句話"), segments)
    assert spans == []
    assert unplaced == ["FR-1"]


def test_a_fragment_no_record_cites_is_not_treated_as_coverage():
    """A fragment nothing points at did not carry the argument anywhere."""

    spans, unplaced = place_fragments(_package("君王與祭司", cited=False), SEGMENTS)
    assert spans == []
    assert unplaced == []
