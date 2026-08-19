"""Tests for the source-as-denominator ledger."""

from __future__ import annotations

import pytest

from backend.pipeline.base_contract_coverage import BOOK_CODE_TO_CHINESE, ScriptureRef, parse_passage_range
from backend.pipeline.sentence_ledger import (
    FRAGMENT,
    HEADING,
    LIST_ITEM,
    PROSE,
    SCRIPTURE_QUOTATION,
    classify_sentence,
    summarise_by_category,
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


# --------------------------------------------------------------------------
# Sentence categories (#88): the total is not the score
# --------------------------------------------------------------------------


def test_categories_separate_prose_from_the_structure_around_it():
    """Headings are 51 of 208 sentences and represented 0% by design.

    Averaged into one number they hide every change in the denominator that
    matters, which is why #88's acceptance is written against prose alone.
    """

    assert classify_sentence("## 一、彌賽亞秘密", "## 一、彌賽亞秘密") == HEADING
    assert classify_sentence("> 當下、耶穌囑咐門徒。", "當下、耶穌囑咐門徒。") == SCRIPTURE_QUOTATION
    assert classify_sentence("- Logos Bible Software", "- Logos Bible Software") == LIST_ITEM
    assert classify_sentence("太 16:21 記載：", "太 16:21 記載：") == FRAGMENT
    assert classify_sentence(
        "這節經文記載耶穌在彼得宣認祂是基督之後，立即命令門徒對外保密。",
        "這節經文記載耶穌在彼得宣認祂是基督之後，立即命令門徒對外保密。",
    ) == PROSE


def test_a_quotation_split_across_sentences_stays_a_quotation():
    """The block decides, not the sentence: half a quotation is still quoted."""

    block = "> 從此耶穌才指示門徒。他必須上耶路撒冷去。"
    assert classify_sentence(block, "他必須上耶路撒冷去。") == SCRIPTURE_QUOTATION


def test_category_counts_split_the_verdicts_they_came_from():
    segments = [(1, "## 標題"), (2, "彼得宣認耶穌是基督，這一認信本身是正確的。")]
    inventory = build_inventory(segments, source_id="SRC")
    heading, prose = inventory[0], inventory[-1]
    rows = reconcile(
        inventory,
        [AnchoredSpan("E001", prose.segment_index, prose.char_start, prose.char_end)],
    )
    summaries = summarise_by_category(inventory, rows, dict(segments))
    assert summaries[PROSE].represented == 1
    assert summaries[PROSE].unprocessed == 0
    assert summaries[HEADING].represented == 0
    assert summaries[HEADING].unprocessed == 1
    assert heading.sentence_id in summaries[HEADING].unprocessed_ids


# --------------------------------------------------------------------------
# Placing a fragment whose phrase the source repeats
# --------------------------------------------------------------------------


def _repeated_package(sha: str | None, paragraph_key: str = "S0002"):
    package = _package("同一句話")
    fragment = package["source_fragments"][0]
    fragment["paragraph_key"] = paragraph_key
    if sha is not None:
        fragment["source_sha256"] = sha
    return package


def test_a_repeated_phrase_resolves_by_the_key_it_was_validated_against():
    """The 太16 母本 states the same geography under 釋經 and again under 附錄.

    Extraction checked the excerpt was verbatim in the segment it named, so the
    key is evidence rather than a guess -- while the source it was checked
    against is still the source in hand.
    """

    segments = [(1, "同一句話。"), (2, "同一句話。")]
    spans, unplaced = place_fragments(_repeated_package("SHA-NOW"), segments, "SHA-NOW")
    assert unplaced == []
    assert [span.segment_index for span in spans] == [2]


def test_a_repeated_phrase_stays_unplaced_when_the_source_has_moved_on():
    """Only 20% of claimed indices in the staged packages still resolve.

    A key validated against a source that has since been re-segmented is not
    evidence about this source, so ambiguity wins and nothing is guessed.
    """

    segments = [(1, "同一句話。"), (2, "同一句話。")]
    spans, unplaced = place_fragments(_repeated_package("SHA-OLD"), segments, "SHA-NOW")
    assert spans == []
    assert unplaced == ["FR-1"]


def test_a_repeated_phrase_stays_unplaced_when_the_fragment_records_no_source():
    segments = [(1, "同一句話。"), (2, "同一句話。")]
    spans, unplaced = place_fragments(_repeated_package(None), segments, "SHA-NOW")
    assert spans == []
    assert unplaced == ["FR-1"]


def test_an_unambiguous_fragment_never_consults_the_key():
    """A key that disagrees with the only place the text occurs changes nothing."""

    spans, _ = place_fragments(
        _repeated_package("SHA-NOW", paragraph_key="S0099"),
        [(1, "同一句話。")],
        "SHA-NOW",
    )
    assert [span.segment_index for span in spans] == [1]
