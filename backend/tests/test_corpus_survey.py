from __future__ import annotations

import hashlib
import json

import pytest

from backend.pipeline.corpus_survey import SurveyValidationError, validate_survey
from backend.pipeline.corpus_survey_runner import (
    _extraction_metadata,
    _existing_output,
    _archive_superseded_output,
    _load,
    _preserve_exact_anchor_fallbacks,
)


def _fixture() -> tuple[dict, dict, bytes]:
    transcript = {
        "metadata": {"status": "published"},
        "script": [
            {
                "index": 1,
                "text": "教授明确说：这是一个候选主张。",
                "start_time": 10,
                "end_time": 20,
            }
        ],
    }
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    survey = {
        "survey_version": "wang_corpus_first_pass_v1",
        "source": {
            "publication_status": "published",
            "segment_count": 1,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "content_clusters": [
            {
                "cluster_id": "T001",
                "function": "theology",
                "segment_indexes": [1],
            }
        ],
        "candidate_claims": [
            {
                "claim_id": "C001",
                "claim_kind": "explicit_claim",
                "attribution": "explicit",
                "cluster_ids": ["T001"],
                "relations": [],
                "anchors": [
                    {
                        "segment_index": 1,
                        "start_time": 10,
                        "end_time": 20,
                        "verbatim_excerpt": "这是一个候选主张",
                    }
                ],
                "review_status": "candidate",
                "confidence": "high",
            }
        ],
        "survey_summary": {
            "cluster_count": 1,
            "candidate_claim_count": 1,
            "high_confidence_claim_count": 1,
            "medium_confidence_claim_count": 0,
            "editorial_inference_count": 0,
        },
    }
    return survey, transcript, raw


def test_validate_survey_accepts_exact_anchor() -> None:
    survey, transcript, raw = _fixture()
    validate_survey(survey, transcript, raw)


def test_extraction_fingerprint_is_required_for_new_generation() -> None:
    survey, transcript, raw = _fixture()
    extraction = _extraction_metadata(
        source_sha256=survey["source"]["sha256"],
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
    )
    survey["extraction"] = extraction
    survey["candidate_claims"][0]["extraction_fingerprint"] = extraction[
        "fingerprint_sha256"
    ]

    validate_survey(
        survey,
        transcript,
        raw,
        expected_extraction_fingerprint=extraction["fingerprint_sha256"],
    )

    survey["candidate_claims"][0]["extraction_fingerprint"] = "other-generation"
    with pytest.raises(SurveyValidationError, match="extraction fingerprint mismatch"):
        validate_survey(survey, transcript, raw)


def test_extraction_fingerprint_changes_with_prompt_model_or_schema_inputs() -> None:
    _, _, raw = _fixture()
    source_sha = hashlib.sha256(raw).hexdigest()
    base = dict(
        source_sha256=source_sha,
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
    )
    first = _extraction_metadata(**base)["fingerprint_sha256"]
    prompt_changed = _extraction_metadata(
        **{**base, "system_prompt": "prompt-v2"}
    )["fingerprint_sha256"]
    model_changed = _extraction_metadata(
        **{**base, "model_id": "model-b"}
    )["fingerprint_sha256"]

    assert len({first, prompt_changed, model_changed}) == 3


def test_generation_fingerprint_is_shared_across_sources() -> None:
    first = _extraction_metadata(
        source_sha256="source-a",
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
    )
    second = _extraction_metadata(
        source_sha256="source-b",
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
    )

    assert first["fingerprint_sha256"] != second["fingerprint_sha256"]
    assert (
        first["generation_fingerprint_sha256"]
        == second["generation_fingerprint_sha256"]
    )


def test_superseded_survey_is_archived_before_replacement(tmp_path) -> None:
    output = tmp_path / "sermon.first-pass.json"
    output.write_text(
        json.dumps(
            {
                "source": {"sha256": "abc123"},
                "extraction": {"fingerprint_sha256": "old-generation"},
            }
        ),
        encoding="utf-8",
    )

    archived = _archive_superseded_output(output)

    assert archived is not None
    assert archived.exists()
    assert archived.parent == tmp_path / "generations"
    assert json.loads(archived.read_text())["source"]["sha256"] == "abc123"


def test_resume_cache_requires_exact_extraction_generation(tmp_path) -> None:
    survey, _, raw = _fixture()
    transcript_id = "测试讲道"
    extraction = _extraction_metadata(
        source_sha256=hashlib.sha256(raw).hexdigest(),
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
    )
    survey["source"]["transcript_id"] = transcript_id
    survey["extraction"] = extraction
    from backend.pipeline.corpus_survey_runner import _slug

    path = tmp_path / f"{_slug(transcript_id)}.first-pass.json"
    path.write_text(json.dumps(survey, ensure_ascii=False), encoding="utf-8")

    assert _existing_output(
        tmp_path, transcript_id, extraction["fingerprint_sha256"]
    ) == path
    assert _existing_output(tmp_path, transcript_id, "different-generation") is None


def test_validate_survey_rejects_non_verbatim_anchor() -> None:
    survey, transcript, raw = _fixture()
    survey["candidate_claims"][0]["anchors"][0]["verbatim_excerpt"] = "改写过的句子"

    with pytest.raises(SurveyValidationError, match="excerpt is not exact"):
        validate_survey(survey, transcript, raw)


def test_validate_survey_rejects_unknown_relation_target() -> None:
    survey, transcript, raw = _fixture()
    survey["candidate_claims"][0]["relations"] = [
        {"type": "supports", "target_claim_id": "C999"}
    ]

    with pytest.raises(SurveyValidationError, match="unknown relation target"):
        validate_survey(survey, transcript, raw)


def test_anchor_repair_derives_timing_from_source_segment() -> None:
    survey, transcript, _ = _fixture()
    anchor = survey["candidate_claims"][0]["anchors"][0]
    anchor["start_time"] = 999
    anchor["end_time"] = 1000

    _preserve_exact_anchor_fallbacks(survey, transcript)

    assert anchor["start_time"] == 10
    assert anchor["end_time"] == 20
    assert anchor["verbatim_excerpt"] == "这是一个候选主张"


def test_anchor_repair_remaps_unique_exact_excerpt() -> None:
    survey, transcript, _ = _fixture()
    anchor = survey["candidate_claims"][0]["anchors"][0]
    anchor["segment_index"] = 99

    _preserve_exact_anchor_fallbacks(survey, transcript)

    assert anchor["segment_index"] == "S0001"
    assert anchor["source_segment_index"] == 1
    assert anchor["source_segment_ordinal"] == 0
    assert anchor["anchor_resolution"] == "remapped_by_unique_exact_excerpt"


def test_duplicate_source_indexes_are_addressed_by_unique_locator() -> None:
    survey, transcript, raw = _fixture()
    transcript["script"].append(
        {"index": 1, "text": "同编号的另一段。", "start_time": 21, "end_time": 30}
    )
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    survey["source"]["segment_count"] = 2
    survey["source"]["sha256"] = hashlib.sha256(raw).hexdigest()
    survey["content_clusters"][0]["segment_indexes"] = ["S0001"]
    anchor = survey["candidate_claims"][0]["anchors"][0]
    anchor["segment_index"] = "S0001"

    validate_survey(survey, transcript, raw)

    anchor["segment_index"] = 1
    with pytest.raises(SurveyValidationError, match="unknown anchor segment"):
        validate_survey(survey, transcript, raw)


def test_reviewed_array_transcript_preserves_missing_media_times(tmp_path) -> None:
    path = tmp_path / "reviewed-sermon.json"
    source_segments = [{"index": 1, "end_index": 20, "text": "这是人工审阅后的逐字稿。"}]
    path.write_text(json.dumps(source_segments, ensure_ascii=False), encoding="utf-8")

    transcript, raw = _load(path)
    assert transcript["metadata"]["status"] == "reviewed"
    assert transcript["script"][0].get("start_time") is None

    survey, _, _ = _fixture()
    survey["source"]["publication_status"] = "reviewed"
    survey["source"]["sha256"] = hashlib.sha256(raw).hexdigest()
    survey["candidate_claims"][0]["anchors"][0].update(
        {
            "start_time": None,
            "end_time": None,
            "verbatim_excerpt": "人工审阅后的逐字稿",
        }
    )

    validate_survey(survey, transcript, raw)
