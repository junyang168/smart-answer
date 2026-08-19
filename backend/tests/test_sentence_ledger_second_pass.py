"""Tests for the targeted second pass over unaccounted sentences."""

from __future__ import annotations

import pytest

from backend.pipeline.sentence_ledger import build_inventory, reconcile
from backend.pipeline.sentence_ledger_second_pass import (
    CARRIES_ARGUMENT,
    IS_ASSERTION,
    NO_ARGUMENT,
    SecondPassQuestion,
    SecondPassValidationError,
    build_questions,
    render_request,
    validate_response,
    verdict_counts,
)

SEGMENT = "猶太制度中，君王與祭司的職分是嚴格分開的。耶穌卻同時擁有這三個職分。"


def questions() -> list[SecondPassQuestion]:
    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    rows = reconcile(inventory, [])
    return build_questions(rows, {s.sentence_id: s for s in inventory}, {1: SEGMENT})


def answer(question: SecondPassQuestion, **overrides):
    row = {
        "sentence_id": question.sentence_id,
        "verdict": NO_ARGUMENT,
        "observation": None,
        "evidence_step": None,
        "claim": None,
        "reason_code": "background_only",
        "duplicate_of_record_id": None,
        "rationale": "材料只提到此事，未据以推论。",
    }
    row.update(overrides)
    return row


def test_only_unaccounted_sentences_are_asked_about():
    """Settled sentences are not re-litigated."""

    inventory = build_inventory([(1, SEGMENT)], source_id="src")
    from backend.pipeline.sentence_ledger import AnchoredSpan

    first = inventory[0]
    rows = reconcile(inventory, [AnchoredSpan("E1", 1, first.char_start, first.char_end)])
    asked = build_questions(rows, {s.sentence_id: s for s in inventory}, {1: SEGMENT})
    assert [q.text for q in asked] == ["耶穌卻同時擁有這三個職分。"]


def test_the_paragraph_travels_with_the_sentence():
    """Judging whether the material reasons from a sentence needs its context."""

    rendered = render_request(questions())
    assert SEGMENT in rendered
    assert "S0001" in rendered


def test_a_complete_set_of_answers_is_accepted():
    asked = questions()
    validate_response({"verdicts": [answer(q) for q in asked]}, asked)


def test_a_skipped_sentence_is_refused():
    """The load-bearing check: silence must not be able to drain the queue.

    A model that may quietly omit sentences can make any residue disappear,
    which is the exact failure the ledger was built to end.
    """

    asked = questions()
    with pytest.raises(SecondPassValidationError, match="answered 0 times"):
        validate_response({"verdicts": [answer(asked[0])]}, asked)


def test_answering_the_same_sentence_twice_is_refused():
    asked = questions()
    with pytest.raises(SecondPassValidationError, match="answered 2 times"):
        validate_response({"verdicts": [answer(asked[0]), answer(asked[0]), answer(asked[1])]}, asked)


def test_an_invented_sentence_is_refused():
    asked = questions()
    rows = [answer(q) for q in asked]
    rows.append({**answer(asked[0]), "sentence_id": "src:1:deadbeef"})
    with pytest.raises(SecondPassValidationError, match="not one of the sentences asked"):
        validate_response({"verdicts": rows}, asked)


def test_a_fabricated_quotation_is_refused():
    """Inventing an inference is the cheapest way to look diligent."""

    asked = questions()
    rows = [answer(q) for q in asked]
    rows[0] = answer(
        asked[0],
        verdict=CARRIES_ARGUMENT,
        observation={
            "statement": "捏造的觀察",
            "observation_type": "historical_cultural",
            "supporting_excerpt": "這句話不在段落裡",
        },
        evidence_step={"statement": "捏造的推論", "supporting_excerpt": "君王與祭司"},
        reason_code=None,
        rationale="",
    )
    with pytest.raises(SecondPassValidationError, match="not verbatim"):
        validate_response({"verdicts": rows}, asked)


def test_carries_argument_needs_both_the_fact_and_the_step():
    asked = questions()
    rows = [answer(q) for q in asked]
    rows[0] = answer(
        asked[0],
        verdict=CARRIES_ARGUMENT,
        observation={
            "statement": "猶太制度中君王與祭司分開。",
            "observation_type": "historical_cultural",
            "supporting_excerpt": "君王與祭司的職分是嚴格分開的",
        },
        evidence_step=None,
        reason_code=None,
        rationale="",
    )
    with pytest.raises(SecondPassValidationError, match="needs the step it supports"):
        validate_response({"verdicts": rows}, asked)


def test_duplicate_of_must_name_the_record_that_covers_it():
    asked = questions()
    rows = [answer(q) for q in asked]
    rows[0] = answer(asked[0], reason_code="duplicate_of", duplicate_of_record_id="  ")
    with pytest.raises(SecondPassValidationError, match="must name the record"):
        validate_response({"verdicts": rows}, asked)


def test_an_exclusion_needs_a_rationale():
    asked = questions()
    rows = [answer(q) for q in asked]
    rows[0] = answer(asked[0], rationale="   ")
    with pytest.raises(SecondPassValidationError, match="rationale must not be empty"):
        validate_response({"verdicts": rows}, asked)


def test_an_unknown_reason_code_is_refused():
    asked = questions()
    rows = [answer(q) for q in asked]
    rows[0] = answer(asked[0], reason_code="looks_unimportant")
    with pytest.raises(SecondPassValidationError, match="is not one of"):
        validate_response({"verdicts": rows}, asked)


def test_counts_show_whether_the_queue_was_drained_or_renamed():
    """A pass that answered `no_argument` to everything renamed the residue."""

    asked = questions()
    rows = [answer(q) for q in asked]
    counts = verdict_counts({"verdicts": rows})
    assert counts[NO_ARGUMENT] == 2
    assert counts[CARRIES_ARGUMENT] == 0
    assert counts["reason_codes"] == {"background_only": 2}


def test_is_assertion_needs_a_claim():
    asked = questions()
    rows = [answer(q) for q in asked]
    rows[0] = answer(asked[0], verdict=IS_ASSERTION, claim=None, reason_code=None, rationale="")
    with pytest.raises(SecondPassValidationError, match="needs a claim"):
        validate_response({"verdicts": rows}, asked)


# ---------------------------------------------------------------------------
# batching and recombination
# ---------------------------------------------------------------------------

from backend.pipeline.sentence_ledger_second_pass_runner import batch, combine  # noqa: E402


def test_batches_are_deterministic_and_lose_nothing():
    asked = questions()
    groups = batch(asked, 1)
    assert [q.sentence_id for group in groups for q in group] == [q.sentence_id for q in asked]
    assert batch(asked, 1) == batch(asked, 1)


def test_recombination_is_rechecked_over_the_whole_set():
    """Each batch validated against its own questions does not prove the union
    covers every question exactly once -- and nothing silently going missing is
    the entire point of this stage."""

    asked = questions()
    first, second = batch(asked, 1)
    good = combine([{"verdicts": [answer(first[0])]}, {"verdicts": [answer(second[0])]}], asked)
    assert len(good["verdicts"]) == 2

    with pytest.raises(SecondPassValidationError, match="answered 0 times"):
        combine([{"verdicts": [answer(first[0])]}], asked)
