from __future__ import annotations

import pytest

from backend.pipeline.observation_type_migration import (
    build_migration_package,
    build_migration_report,
)
from backend.pipeline.observation_type_vocabulary import (
    CERTAIN,
    OBSERVATION_TYPES,
    PROPOSED,
    classify,
    classify_all,
    normalize,
)


def test_the_six_categories_are_the_ones_the_extraction_prompt_names():
    assert OBSERVATION_TYPES == (
        "scripture_text",
        "original_language",
        "literary_form",
        "literary_context",
        "historical_cultural",
        "narrative_structure",
    )


@pytest.mark.parametrize(
    "raw",
    ["原文时态", "grammatical_tense", "希腊文文法观察", "greek_grammar", "原文语法", "grammar"],
)
def test_one_thing_written_six_ways_folds_to_one_category(raw):
    """The 246 values exist because this fold was never available in SQL."""
    assert normalize(raw) == "original_language"


@pytest.mark.parametrize(
    "raw",
    [
        "original_language_structure",
        "original_language_gloss",
        "original_language_lexeme",
        "original_language_form",
        "original_language_comparison",
        "original_language_and_historical_observation",
    ],
)
def test_an_original_language_marker_beats_a_later_category(raw):
    """`..._structure` must not be claimed by `narrative_structure`, and
    `..._and_historical_observation` must not be claimed by `historical_cultural`."""
    assert normalize(raw) == "original_language"


def test_a_translation_note_is_proposed_and_never_settled():
    """Folding these in silently would inflate the number being measured."""
    result = classify("translation_observation")
    assert result.category == "original_language"
    assert result.confidence == PROPOSED
    assert result.needs_review is True
    assert normalize("translation_observation") is None


def test_an_explicit_original_language_marker_outranks_the_translation_rule():
    assert normalize("原文与古译本") == "original_language"
    assert normalize("原文及英译表述") == "original_language"


def test_a_label_naming_the_field_is_never_mapped():
    """All twelve records typed `背景` are genre teaching, the professor's own
    biography, or critiques of dispensationalism -- none is passage background."""
    result = classify("背景")
    assert result.category is None
    assert result.confidence is None
    assert normalize("背景") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("經文", "scripture_text"),
        ("scripture_wording", "scripture_text"),
        ("历史文化背景观察", "historical_cultural"),
        ("上下文观察", "literary_context"),
        ("詩歌平行", "literary_form"),
        ("叙事结构观察", "narrative_structure"),
    ],
)
def test_representative_values_from_each_category(raw, expected):
    assert normalize(raw) == expected


def test_a_value_already_in_the_vocabulary_is_settled_unchanged():
    for category in OBSERVATION_TYPES:
        assert classify(category) == classify(category)
        assert normalize(category) == category


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_a_missing_type_needs_review_rather_than_a_default(raw):
    assert normalize(raw) is None
    assert classify(raw).confidence is None


def test_an_unrecognised_value_is_never_defaulted_into_a_category():
    assert normalize("reported_objection") is None


def test_classify_all_keys_by_the_raw_value_as_given():
    assert classify_all(["grammar", "背景"]).keys() == {"grammar", "背景"}


def _observation(observation_id: str, observation_type: str, statement: str = "s"):
    return {
        "observation_id": observation_id,
        "observation_type": observation_type,
        "statement": statement,
    }


def test_the_report_separates_what_is_settled_from_what_a_human_must_decide():
    report = build_migration_report([
        _observation("OBS-1", "原文时态"),
        _observation("OBS-2", "greek_grammar"),
        _observation("OBS-3", "經文"),
        _observation("OBS-4", "translation", "和合本譯作靈魂。"),
        _observation("OBS-5", "背景"),
    ])

    assert report["totals"] == {
        "observations": 5,
        "distinct_legacy_values": 5,
        "settled_values": 3,
        "settled_records": 3,
        "review_values": 2,
        "review_records": 2,
    }
    assert report["category_records"]["original_language"] == 2
    assert report["category_records"]["scripture_text"] == 1

    queued = {item["raw_value"]: item for item in report["review_queue"]}
    assert queued["translation"]["suggested_category"] == "original_language"
    assert queued["translation"]["confidence"] == PROPOSED
    assert queued["translation"]["observation_ids"] == ["OBS-4"]
    assert queued["translation"]["sample_statements"] == ["和合本譯作靈魂。"]
    assert queued["背景"]["suggested_category"] is None
    assert queued["背景"]["reason"] == "no rule claims this value"


def test_the_report_never_reports_a_settled_value_as_reviewable():
    report = build_migration_report([_observation("OBS-1", "希腊文词义观察")])
    assert report["review_queue"] == []
    assert report["settled_map"] == {"希腊文词义观察": "original_language"}
    assert all(
        classify(raw).confidence == CERTAIN for raw in report["settled_map"]
    )


def test_the_extraction_validator_rejects_a_type_outside_the_vocabulary():
    """The API enforces the enum on its own output; this is the check that
    still applies when a response reaches the validator by any other route."""
    from backend.pipeline.detailed_knowledge_extraction import (
        DetailedExtractionValidationError,
        validate_response,
    )

    transcript = {"script": [{"index": 1, "start_time": 0.0, "end_time": 1.0, "text": "原文是 φρονέω。"}]}
    anchors = [{"segment_index": "S0001", "start_time": 0.0, "end_time": 1.0, "verbatim_excerpt": "原文是 φρονέω。"}]
    response = {
        "questions": [], "positions": [], "evidence_steps": [], "claims": [],
        "evidence_relations": [], "claim_relations": [],
        "observations": [{
            "observation_id": "OBS001", "statement": "φρονέω 意為關心。",
            "observation_type": "希腊文词义观察", "argument_role": "background",
            "scripture_refs": [], "anchors": anchors,
        }],
    }

    with pytest.raises(DetailedExtractionValidationError, match="outside the vocabulary"):
        validate_response(response, transcript)

    response["observations"][0]["observation_type"] = "original_language"
    validate_response(response, transcript)


def test_the_migration_rewrites_only_what_a_rule_settled():
    package = build_migration_package([
        _observation("OBS-1", "希腊文文法观察"),
        _observation("OBS-2", "translation"),
        _observation("OBS-3", "背景"),
    ])
    assert [row["observation_id"] for row in package["observations"]] == ["OBS-1"]
    assert package["observations"][0]["observation_type"] == "original_language"


def test_the_migration_keeps_the_original_label():
    """The fold must stay auditable; nothing about the record is destroyed."""
    package = build_migration_package([_observation("OBS-1", "原文时态")])
    assert package["observations"][0]["observation_type_original"] == "原文时态"


def test_a_record_already_at_its_target_is_not_rewritten():
    """Rewriting it would bump the revision and dirty the history for nothing."""
    assert build_migration_package([_observation("OBS-1", "original_language")])["observations"] == []


def test_a_reviewer_decision_settles_a_value_the_rules_would_not():
    package = build_migration_package(
        [_observation("OBS-1", "translation")],
        decisions={"translation": "original_language"},
    )
    assert package["observations"][0]["observation_type"] == "original_language"
    assert package["observations"][0]["observation_type_original"] == "translation"


def test_a_decision_outside_the_vocabulary_is_refused():
    with pytest.raises(ValueError, match="not in the vocabulary"):
        build_migration_package(
            [_observation("OBS-1", "translation")],
            decisions={"translation": "翻译"},
        )


def test_fields_other_than_the_type_are_carried_through_untouched():
    row = _observation("OBS-1", "希腊文词义观察")
    row["source_fragment_ids"] = ["FR-1"]
    row["scripture_refs"] = ["太16:23"]
    rewritten = build_migration_package([row])["observations"][0]
    assert rewritten["source_fragment_ids"] == ["FR-1"]
    assert rewritten["scripture_refs"] == ["太16:23"]
    assert rewritten["statement"] == row["statement"]
