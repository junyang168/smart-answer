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


def test_unresolved_items_reach_every_payload():
    """#291: the structure's unresolved list must travel with the charter."""

    from backend.pipeline.draft_first_author_runner import structure_unresolved_items
    import inspect
    from backend.pipeline import draft_first_review_runner as review

    packet = {
        "structure": {"revision": {"unresolved_items": ["三种正面识别之间的关系未统一"]}}
    }
    assert structure_unresolved_items(packet) == ["三种正面识别之间的关系未统一"]
    assert structure_unresolved_items({}) == []

    gate_source = inspect.getsource(review.run_gates)
    assert gate_source.count('"unresolved_items": unresolved') == 3
    main_source = inspect.getsource(review.main)
    assert '"unresolved_items": structure_unresolved_items(dict(packet))' in main_source


# ---- #293 hardening ----


def test_changed_and_ending_paragraphs():
    from backend.pipeline.draft_first_review_runner import changed_and_ending_paragraphs

    baseline = "# T\n\n甲段。\n\n乙段。\n\n收束段落不变。\n"
    revised = "# T\n\n甲段改了。\n\n乙段。\n\n插入的新段。\n\n收束段落不变。\n"
    changed, ending = changed_and_ending_paragraphs(baseline, revised)
    assert [p["text"] for p in changed] == ["甲段改了。", "插入的新段。"]
    # the unchanged closing paragraph is still re-read every round
    assert [p["text"] for p in ending] == ["插入的新段。", "收束段落不变。"]


def test_gates_carry_routes_fingerprints_and_delta_scope():
    import inspect
    from backend.pipeline import draft_first_review_runner as review

    src = inspect.getsource(review.run_gates)
    assert '"argument_routes": routes' in src
    assert 'fingerprints["alignment"]' in src and 'fingerprints["editorial"]' in src
    assert '"review_scope"' in src and '"baseline_review"' in src
    main_src = inspect.getsource(review.main)
    assert "baseline_manuscript=baseline_manuscript" in main_src


def test_excerpt_overlap_flags_unrelated_spans():
    from backend.pipeline.draft_first_source_binding import (
        excerpt_overlap,
        verify_bindings,
        reader_paragraphs,
    )

    assert excerpt_overlap("教會建立在信仰告白上", "教會建立在信仰告白上") == 1.0
    assert excerpt_overlap("教會建立在信仰告白上", "門徒忘了帶餅彼此議論") < 0.1

    markdown = "# T\n\n教會建立在彼得的信仰告白上。\n"
    paragraphs = reader_paragraphs(markdown)
    packet = {
        "source_originals": {
            "originals": [
                {
                    "source_id": "SRC-1",
                    "content": "門徒渡到那邊去忘了帶餅於是彼此議論起來這件事。教會建立在彼得的信仰告白上這是清楚的教導。",
                }
            ]
        }
    }
    result = verify_bindings(
        {
            "bindings": [
                {
                    "paragraph_index": 0,
                    "spans": [
                        {"source_id": "SRC-1", "excerpt": "教會建立在彼得的信仰告白上這是清楚的教導。"},
                        {"source_id": "SRC-1", "excerpt": "門徒渡到那邊去忘了帶餅於是彼此議論起來這件事。"},
                    ],
                }
            ]
        },
        paragraphs=paragraphs,
        packet=packet,
    )
    spans = result["bindings"][0]["spans"]
    assert len(spans) == 2  # kept, not dropped
    assert [f["code"] for f in result["findings"]] == ["low_overlap"]


def test_projection_audit_floors():
    from backend.api.wang_article_reviews import _draft_first_annotated_markdown
    from backend.pipeline.draft_first_source_binding import reader_paragraphs

    packet = {
        "evidence_packet_sha256": "pk",
        "source_originals": {
            "originals": [
                {"source_id": "SRC-N", "source_type": "notes_manuscript",
                 "title": "母本", "content": "教會建立在信仰告白上的完整教導原文在此。"}
            ]
        },
        "source_documents": [
            {"source_id": "SRC-N", "source_type": "notes_manuscript", "title": "母本",
             "source_url": "/resources/notes/n1"}
        ],
    }
    markdown = "# T\n\n第一段。\n\n第二段。\n\n第三段。\n"
    paragraphs = reader_paragraphs(markdown)
    # only one of three bound -> coverage 33% -> below floor
    bindings_record = {
        "bindings": [
            {"paragraph_index": i, "paragraph_sha256": p["paragraph_sha256"],
             "spans": ([{"source_id": "SRC-N", "excerpt": "教會建立在信仰告白上的完整教導原文在此。"}] if i == 0 else [])}
            for i, p in enumerate(paragraphs)
        ],
        "findings": [{"code": "low_overlap", "paragraph_index": 0, "overlap": 0.05}],
    }
    _, _, projection, _ = _draft_first_annotated_markdown(markdown, bindings_record, packet)
    codes = {f["code"] for f in projection["findings"]}
    assert "binding_coverage_below_floor" in codes
    assert "binding_low_overlap" in codes
    assert projection["passed"] is False
