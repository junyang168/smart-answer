import json

import pytest

from backend.pipeline.draft_first_review_runner import (
    DraftReviewContractError,
    merge_blocking_findings,
    validate_alignment,
    validate_review,
)


def _profile():
    return {
        "dimensions": [
            {"id": "source_and_exegesis", "weight": 15, "minimum": 12},
            {"id": "general_reader_readability", "weight": 10, "minimum": 8},
        ],
        "hard_failures": ["conclusion_reader_answer_broken"],
    }


def _review(score_a=13, score_b=9, failed=False, findings=()):
    return {
        "summary": "ok",
        "dimension_scores": [
            {"dimension_id": "source_and_exegesis", "score": score_a, "evidence": "「證據句。」"},
            {"dimension_id": "general_reader_readability", "score": score_b, "evidence": "「證據句。」"},
        ],
        "hard_failure_assessments": [
            {"failure_id": "conclusion_reader_answer_broken", "failed": failed, "evidence": ""}
        ],
        "findings": list(findings),
    }


MANUSCRIPT = "第一段。證據句。經文沒有說明這件事。結尾句。"


def test_alignment_quotes_must_be_verbatim():
    validate_alignment(
        {"findings": [{"quote": "經文沒有說明", "kind": "attribution_swap", "note": "偷換"}]},
        manuscript=MANUSCRIPT,
    )
    with pytest.raises(DraftReviewContractError, match="not verbatim"):
        validate_alignment(
            {"findings": [{"quote": "稿裡沒有這句", "kind": "beyond_source", "note": ""}]},
            manuscript=MANUSCRIPT,
        )


def test_review_verdict_derives_from_minimums_not_totals():
    verdict = validate_review(_review(), manuscript=MANUSCRIPT, profile=_profile())
    assert verdict == {"dimensions_below_minimum": [], "hard_failures_failed": []}

    finding = {
        "anchor": "結尾句。",
        "dimension_id": "general_reader_readability",
        "summary": "s",
        "required_change": "r",
        "blocking": True,
    }
    verdict = validate_review(
        _review(score_b=7, findings=[finding]), manuscript=MANUSCRIPT, profile=_profile()
    )
    assert verdict["dimensions_below_minimum"] == ["general_reader_readability"]


def test_failing_review_without_blocking_finding_is_rejected():
    with pytest.raises(DraftReviewContractError, match="blocking finding"):
        validate_review(_review(score_b=7), manuscript=MANUSCRIPT, profile=_profile())
    with pytest.raises(DraftReviewContractError, match="blocking finding"):
        validate_review(_review(failed=True), manuscript=MANUSCRIPT, profile=_profile())


def test_review_anchor_must_be_verbatim():
    finding = {
        "anchor": "稿裡沒有的錨",
        "dimension_id": "general_reader_readability",
        "summary": "s",
        "required_change": "r",
        "blocking": True,
    }
    with pytest.raises(DraftReviewContractError, match="anchor not verbatim"):
        validate_review(
            _review(findings=[finding]), manuscript=MANUSCRIPT, profile=_profile()
        )


def test_blocking_findings_merge_across_gates():
    blocking = merge_blocking_findings(
        alignment={
            "findings": [
                {"quote": "經文沒有說明這件事", "kind": "attribution_swap", "note": "把講道的沉默說成經文的"}
            ]
        },
        blind_compare={
            "answer_matches_settled_positions": True,
            "modality_preserved": False,
            "mismatches": ["把更可能複述成了就是"],
        },
        review=_review(
            findings=[
                {
                    "anchor": "結尾句。",
                    "dimension_id": "general_reader_readability",
                    "summary": "s",
                    "required_change": "r",
                    "blocking": True,
                },
                {
                    "anchor": "第一段。",
                    "dimension_id": "source_and_exegesis",
                    "summary": "nonblocking",
                    "required_change": "",
                    "blocking": False,
                },
            ]
        ),
        review_verdict={"dimensions_below_minimum": [], "hard_failures_failed": []},
        quote_report={"quotes_failing": ["改動過的引文"]},
    )
    gates = [item["gate"] for item in blocking]
    assert gates == ["alignment", "quote_check", "blind_read", "editorial_review"]
    kinds = {item["kind"] for item in blocking}
    assert "attribution_swap" in kinds and "reader_path_broken" in kinds


def test_clean_gates_produce_no_blocking_findings():
    blocking = merge_blocking_findings(
        alignment={"findings": []},
        blind_compare={
            "answer_matches_settled_positions": True,
            "modality_preserved": True,
            "mismatches": [],
        },
        review=_review(),
        review_verdict={"dimensions_below_minimum": [], "hard_failures_failed": []},
        quote_report={"quotes_failing": []},
    )
    assert blocking == []
