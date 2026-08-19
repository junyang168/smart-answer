from __future__ import annotations

import pytest

from backend.pipeline.detailed_knowledge_extraction import (
    DetailedExtractionValidationError,
    extraction_identity,
    validate_response,
)
from backend.pipeline.extraction_windows import (
    ExtractionWindow,
    breadcrumb_for,
    merge_window_responses,
    namespace_response,
    plan_windows,
    segment_locator,
    window_plan_identity,
)
from backend.pipeline.sentence_ledger import (
    FRAGMENT,
    HEADING,
    LIST_ITEM,
    PROSE,
    SCRIPTURE_QUOTATION,
    classify_sentence,
)


def _segments(count: int) -> list[str]:
    return [f"第{index}段的正文内容，这里写了足够长的一句话。" for index in range(count)]


def _empty_response() -> dict:
    return {
        "questions": [], "positions": [], "observations": [], "evidence_steps": [],
        "claims": [], "evidence_relations": [], "claim_relations": [],
    }


def _observation(record_id: str, locator: str, excerpt: str, role: str = "background") -> dict:
    return {
        "observation_id": record_id, "statement": f"观察 {record_id}",
        "observation_type": "scripture_text", "argument_role": role, "scripture_refs": [],
        "anchors": [{
            "segment_index": locator, "start_time": None, "end_time": None,
            "verbatim_excerpt": excerpt,
        }],
    }


def _evidence(record_id: str, locator: str, excerpt: str, claims: list[str] | None = None) -> dict:
    return {
        "evidence_step_id": record_id, "statement": f"证据 {record_id}", "step_type": "reasoning",
        "speaker": "professor", "stance": "asserted", "discourse_role": "argument",
        "support_eligibility": "eligible_candidate", "scripture_refs": [],
        "produced_claim_ids": claims or [],
        "anchors": [{
            "segment_index": locator, "start_time": None, "end_time": None,
            "verbatim_excerpt": excerpt,
        }],
    }


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def test_every_segment_has_exactly_one_owner() -> None:
    """The property deduplication rests on: ownership is a partition."""

    windows = plan_windows(_segments(37), fetch=5, context=5, snap=0)
    owned = [position for window in windows for position in range(window.fetch_start, window.fetch_end)]
    assert owned == list(range(37))


def test_pairs_within_twice_the_context_are_always_visible_together() -> None:
    """The frame is fetch + 2*context wide and steps by fetch, so d <= 2*context."""

    segments = _segments(43)
    windows = plan_windows(segments, fetch=5, context=5, snap=0)
    for start in range(len(segments)):
        for distance in range(0, 11):
            end = start + distance
            if end >= len(segments):
                break
            assert any(window.sees(start) and window.sees(end) for window in windows), (
                f"{start}->{end} is not visible in any single window"
            )


def test_narrow_context_leaves_distant_pairs_uncovered() -> None:
    """The complement of the guarantee, so the trade-off cannot silently change."""

    segments = _segments(60)
    windows = plan_windows(segments, fetch=5, context=5, snap=0)
    assert not any(window.sees(24) and window.sees(35) for window in windows)


def test_boundary_snaps_onto_a_nearby_heading() -> None:
    segments = _segments(20)
    segments[6] = "## 二、从马可福音现象回应"
    windows = plan_windows(segments, fetch=5, context=5, snap=2)
    assert [window.fetch_start for window in windows][:3] == [0, 6, 11]


def test_snapping_never_drops_or_duplicates_a_segment() -> None:
    segments = _segments(30)
    for position in (4, 5, 9, 11, 16):
        segments[position] = f"### 标题 {position}"
    windows = plan_windows(segments, fetch=5, context=5, snap=2)
    owned = [position for window in windows for position in range(window.fetch_start, window.fetch_end)]
    assert owned == list(range(30))


def test_breadcrumb_reports_the_enclosing_heading_chain() -> None:
    segments = _segments(8)
    segments[0] = "## 二、从马可福音现象回应"
    segments[2] = "### 释经"
    segments[5] = "### 神学意义"
    assert breadcrumb_for(segments, 4) == "二、从马可福音现象回应 > 释经"
    assert breadcrumb_for(segments, 6) == "二、从马可福音现象回应 > 神学意义"


def test_window_plan_enters_the_extraction_fingerprint() -> None:
    """A rerun at a different window size must not be mistaken for the same run."""

    common = {
        "source_sha256": "abc", "prompt": "p", "model_id": "gpt-5.6-sol",
        "reasoning_effort": "medium", "max_output_tokens": 32000,
    }
    narrow = plan_windows(_segments(30), fetch=5, context=5, snap=0)
    wide = plan_windows(_segments(30), fetch=10, context=5, snap=0)
    first = extraction_identity(
        **common, window_plan=window_plan_identity(narrow, fetch=5, context=5, snap=0)
    )
    second = extraction_identity(
        **common, window_plan=window_plan_identity(wide, fetch=10, context=5, snap=0)
    )
    assert first["fingerprint_sha256"] != second["fingerprint_sha256"]


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


def test_namespacing_keeps_two_windows_from_colliding() -> None:
    window = ExtractionWindow(index=3, see_start=0, see_end=10, fetch_start=5, fetch_end=10, breadcrumb="")
    response = _empty_response()
    response["observations"] = [_observation("OBS001", "S0006", "第5段")]
    response["evidence_steps"] = [_evidence("E001", "S0006", "第5段", claims=["CL001"])]
    response["claims"] = [{
        "claim_id": "CL001", "statement": "主张", "claim_kind": "reasoning_conclusion",
        "attribution": "professor", "scripture_refs": [], "topic_terms": [],
        "evidence_step_ids": ["E001"], "opposed_position_ids": [], "review_status": "candidate",
    }]
    response["evidence_relations"] = [{
        "relation_id": "ER001", "from_id": "OBS001", "to_id": "E001",
        "relation_type": "supports", "reason": "r",
    }]
    renamed = namespace_response(response, window)
    assert renamed["observations"][0]["observation_id"] == "W03-OBS001"
    assert renamed["claims"][0]["evidence_step_ids"] == ["W03-E001"]
    assert renamed["evidence_steps"][0]["produced_claim_ids"] == ["W03-CL001"]
    assert renamed["evidence_relations"][0]["from_id"] == "W03-OBS001"


def test_context_zone_duplicate_is_dropped_in_favour_of_its_owner() -> None:
    segments = _segments(20)
    first = ExtractionWindow(index=1, see_start=0, see_end=15, fetch_start=0, fetch_end=5, breadcrumb="")
    second = ExtractionWindow(index=2, see_start=0, see_end=20, fetch_start=5, fetch_end=10, breadcrumb="")
    left, right = _empty_response(), _empty_response()
    # Both windows can see S0006; only the second one owns it.
    left["observations"] = [_observation("OBS001", "S0006", "第5段")]
    right["observations"] = [_observation("OBS001", "S0006", "第5段的正文")]
    merged = merge_window_responses([(first, left), (second, right)], segments)
    assert [row["observation_id"] for row in merged["observations"]] == ["W02-OBS001"]


def test_relation_into_the_context_zone_is_rewritten_onto_the_surviving_record() -> None:
    segments = _segments(20)
    first = ExtractionWindow(index=1, see_start=0, see_end=15, fetch_start=0, fetch_end=5, breadcrumb="")
    second = ExtractionWindow(index=2, see_start=0, see_end=20, fetch_start=5, fetch_end=10, breadcrumb="")
    left, right = _empty_response(), _empty_response()
    left["observations"] = [_observation("OBS001", "S0003", "第2段", role="load_bearing")]
    left["evidence_steps"] = [_evidence("E001", "S0006", "第5段的正文内容")]
    left["evidence_relations"] = [{
        "relation_id": "ER001", "from_id": "OBS001", "to_id": "E001",
        "relation_type": "supports", "reason": "r",
    }]
    right["evidence_steps"] = [_evidence("E001", "S0006", "第5段的正文")]
    merged = merge_window_responses([(first, left), (second, right)], segments)
    assert [row["evidence_step_id"] for row in merged["evidence_steps"]] == ["W02-E001"]
    assert merged["evidence_relations"][0]["from_id"] == "W01-OBS001"
    assert merged["evidence_relations"][0]["to_id"] == "W02-E001"


def test_unmatched_relation_endpoint_is_promoted_rather_than_severed() -> None:
    """A near-duplicate record is cheap; an orphaned load_bearing observation is not."""

    segments = _segments(20)
    first = ExtractionWindow(index=1, see_start=0, see_end=15, fetch_start=0, fetch_end=5, breadcrumb="")
    second = ExtractionWindow(index=2, see_start=0, see_end=20, fetch_start=5, fetch_end=10, breadcrumb="")
    left, right = _empty_response(), _empty_response()
    left["observations"] = [_observation("OBS001", "S0003", "第2段", role="load_bearing")]
    left["evidence_steps"] = [_evidence("E001", "S0006", "这里写了足够长的一句话")]
    left["evidence_relations"] = [{
        "relation_id": "ER001", "from_id": "OBS001", "to_id": "E001",
        "relation_type": "supports", "reason": "r",
    }]
    # The owner of S0006 quoted a disjoint span, so no overlap can be matched.
    right["evidence_steps"] = [_evidence("E002", "S0006", "第5段")]
    merged = merge_window_responses([(first, left), (second, right)], segments)
    surviving = {row["evidence_step_id"] for row in merged["evidence_steps"]}
    assert surviving == {"W01-E001", "W02-E002"}
    assert merged["evidence_relations"][0]["to_id"] == "W01-E001"


def test_merged_package_satisfies_the_full_contract_each_window_was_excused_from() -> None:
    segments = _segments(12)
    transcript = {
        "metadata": {"title": "t"},
        "script": [{"index": i + 1, "start_time": None, "end_time": None, "text": text}
                   for i, text in enumerate(segments)],
    }
    first = ExtractionWindow(index=1, see_start=0, see_end=12, fetch_start=0, fetch_end=5, breadcrumb="")
    second = ExtractionWindow(index=2, see_start=0, see_end=12, fetch_start=5, fetch_end=10, breadcrumb="")
    left, right = _empty_response(), _empty_response()
    left["observations"] = [_observation("OBS001", "S0003", "第2段", role="load_bearing")]
    left["evidence_steps"] = [_evidence("E001", "S0006", "第5段的正文内容")]
    left["evidence_relations"] = [{
        "relation_id": "ER001", "from_id": "OBS001", "to_id": "E001",
        "relation_type": "supports", "reason": "r",
    }]
    right["evidence_steps"] = [_evidence("E001", "S0006", "第5段的正文内容，这里")]
    right["claims"] = [{
        "claim_id": "CL001", "statement": "主张", "claim_kind": "reasoning_conclusion",
        "attribution": "professor", "scripture_refs": [], "topic_terms": [],
        "evidence_step_ids": ["E001"], "opposed_position_ids": [], "review_status": "candidate",
    }]
    right["evidence_steps"][0]["produced_claim_ids"] = ["CL001"]
    merged = merge_window_responses([(first, left), (second, right)], segments)
    validate_response(merged, transcript)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _one_segment_transcript() -> dict:
    return {
        "metadata": {"title": "t"},
        "script": [
            {"index": 1, "start_time": None, "end_time": None, "text": "第一段的正文。"},
            {"index": 2, "start_time": None, "end_time": None, "text": "第二段的正文。"},
        ],
    }


def test_window_validation_rejects_an_anchor_outside_the_frame() -> None:
    response = _empty_response()
    response["observations"] = [_observation("OBS001", "S0002", "第二段")]
    with pytest.raises(DetailedExtractionValidationError, match="outside this window"):
        validate_response(response, _one_segment_transcript(), visible_locators={"S0001"})


def test_window_validation_excuses_the_load_bearing_rule_but_the_package_does_not() -> None:
    """Per window the rule is unanswerable; enforcing it there buys relabelling."""

    response = _empty_response()
    response["observations"] = [_observation("OBS001", "S0001", "第一段", role="load_bearing")]
    validate_response(response, _one_segment_transcript(), require_load_bearing_relations=False)
    with pytest.raises(DetailedExtractionValidationError, match="load_bearing"):
        validate_response(response, _one_segment_transcript())


# --------------------------------------------------------------------------
# Ledger categories
# --------------------------------------------------------------------------


def test_sentence_categories_separate_prose_from_the_structure_around_it() -> None:
    assert classify_sentence("## 一、彌賽亞秘密", "## 一、彌賽亞秘密") == HEADING
    assert classify_sentence("> 當下、耶穌囑咐門徒。", "當下、耶穌囑咐門徒。") == SCRIPTURE_QUOTATION
    assert classify_sentence("- Logos Bible Software", "- Logos Bible Software") == LIST_ITEM
    assert classify_sentence("太 16:21 記載：", "太 16:21 記載：") == FRAGMENT
    assert classify_sentence(
        "這節經文記載耶穌在彼得宣認祂是基督之後，立即命令門徒對外保密。",
        "這節經文記載耶穌在彼得宣認祂是基督之後，立即命令門徒對外保密。",
    ) == PROSE


def test_locator_is_global_not_window_local() -> None:
    assert segment_locator(0) == "S0001"
    assert segment_locator(115) == "S0116"
