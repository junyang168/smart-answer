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
    _survey_artifact_sha256,
    run_one,
)
from backend.pipeline.corpus_ai_review import apply_risk_routing
from backend.pipeline.corpus_ai_review_runner import (
    _matching_review_artifact,
    _review_artifact_sha256,
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


def test_survey_fingerprint_covers_the_exact_user_prompt() -> None:
    base = dict(
        source_sha256="same-body",
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
    )
    before = _extraction_metadata(**base, user_prompt_sha256="title-a")
    after = _extraction_metadata(**base, user_prompt_sha256="title-b")

    assert before["source_sha256"] == after["source_sha256"]
    assert before["fingerprint_sha256"] != after["fingerprint_sha256"]


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


def test_valid_json_wrong_shape_is_not_reused_or_crashed(tmp_path) -> None:
    transcript_id = "wrong-shape"
    from backend.pipeline.corpus_survey_runner import _slug

    path = tmp_path / f"{_slug(transcript_id)}.first-pass.json"
    path.write_text("[]", encoding="utf-8")
    transcript = {
        "metadata": {"status": "reviewed"},
        "script": [{"index": 1, "text": "正文"}],
    }
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")

    assert _existing_output(
        tmp_path,
        transcript_id,
        "f" * 64,
        transcript=transcript,
        raw_source=raw,
    ) is None
    assert _archive_superseded_output(path) is not None


def test_run_one_projects_physical_source_exactly_once_and_resumes(tmp_path) -> None:
    transcript_path = tmp_path / "single-projection.json"
    transcript_path.write_text(
        json.dumps(
            [{"index": 1, "text": "x~~~~a~~~~y", "start_time": None, "end_time": None}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Client:
        def __init__(self) -> None:
            self.calls = 0
            self.prompt = ""

        def generate_json(self, *_args, **_kwargs):
            self.calls += 1
            self.prompt = _kwargs["cache_prefix"]
            return {
                "content_clusters": [
                    {
                        "cluster_id": "T001",
                        "title": "主题",
                        "function": "theology",
                        "summary": "摘要",
                        "segment_indexes": ["S0001"],
                        "scripture_refs": [],
                        "topic_terms": [],
                    }
                ],
                "candidate_claims": [
                    {
                        "claim_id": "C001",
                        "statement": "主张",
                        "claim_kind": "explicit_claim",
                        "attribution": "explicit",
                        "cluster_ids": ["T001"],
                        "scripture_refs": [],
                        "relations": [],
                        "anchors": [
                            {
                                "segment_index": "S0001",
                                "start_time": None,
                                "end_time": None,
                                "verbatim_excerpt": "x~~\n~~y",
                            }
                        ],
                        "review_status": "candidate",
                        "confidence": "high",
                    }
                ],
            }

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    legacy_path = output_dir / "single_projection.first-pass.json"
    legacy_path.write_text(
        json.dumps(
            {
                "source": {"transcript_id": "single-projection", "sha256": "old"},
                "extraction": {"fingerprint_sha256": "old-generation"},
            }
        ),
        encoding="utf-8",
    )
    client = Client()
    status, output = run_one(
        transcript_path,
        output_dir=output_dir,
        client=client,
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
        force=False,
    )

    assert status == "created"
    assert "x~~\n~~y" in client.prompt
    assert "x\ny" not in client.prompt
    survey = json.loads(output.read_text(encoding="utf-8"))
    assert survey["candidate_claims"][0]["anchors"][0]["verbatim_excerpt"] == "x~~\n~~y"
    assert not legacy_path.exists()
    assert len(list((output_dir / "generations").glob("*.json"))) == 1

    status, repeated_output = run_one(
        transcript_path,
        output_dir=output_dir,
        client=client,
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
        force=False,
    )
    assert status == "skipped"
    assert repeated_output == output
    assert client.calls == 1


def test_resume_cache_requires_complete_valid_exact_extraction_generation(tmp_path) -> None:
    survey, transcript, raw = _fixture()
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
    survey["candidate_claims"][0]["extraction_fingerprint"] = extraction[
        "fingerprint_sha256"
    ]
    survey["extraction"]["artifact_sha256"] = _survey_artifact_sha256(survey)
    from backend.pipeline.corpus_survey_runner import _slug

    path = tmp_path / f"{_slug(transcript_id)}.first-pass.json"
    path.write_text(json.dumps(survey, ensure_ascii=False), encoding="utf-8")

    assert _existing_output(
        tmp_path,
        transcript_id,
        extraction["fingerprint_sha256"],
        transcript=transcript,
        raw_source=raw,
    ) == path
    assert _existing_output(
        tmp_path,
        transcript_id,
        "different-generation",
        transcript=transcript,
        raw_source=raw,
    ) is None

    survey["candidate_claims"][0]["anchors"][0]["verbatim_excerpt"] = "篡改"
    path.write_text(json.dumps(survey, ensure_ascii=False), encoding="utf-8")
    assert _existing_output(
        tmp_path,
        transcript_id,
        extraction["fingerprint_sha256"],
        transcript=transcript,
        raw_source=raw,
    ) is None


def test_legacy_name_upgrade_archives_a_stale_canonical_artifact(tmp_path) -> None:
    survey, transcript, raw = _fixture()
    transcript_id = "旧命名讲道"
    extraction = _extraction_metadata(
        source_sha256=hashlib.sha256(raw).hexdigest(),
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
    )
    survey["source"]["transcript_id"] = transcript_id
    survey["extraction"] = extraction
    survey["candidate_claims"][0]["extraction_fingerprint"] = extraction[
        "fingerprint_sha256"
    ]
    survey["extraction"]["artifact_sha256"] = _survey_artifact_sha256(survey)
    from backend.pipeline.corpus_survey_runner import _slug

    canonical = tmp_path / f"{_slug(transcript_id)}.first-pass.json"
    stale = {"source": {"transcript_id": transcript_id}, "stale": True}
    canonical.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    legacy = tmp_path / "legacy.first-pass.json"
    legacy.write_text(json.dumps(survey, ensure_ascii=False), encoding="utf-8")

    found = _existing_output(
        tmp_path,
        transcript_id,
        extraction["fingerprint_sha256"],
        transcript=transcript,
        raw_source=raw,
    )

    assert found == canonical
    assert not legacy.exists()
    assert json.loads(canonical.read_text(encoding="utf-8")) == survey
    archives = list((tmp_path / "generations").glob("*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8")) == stale

def test_resume_cache_rejects_truncated_same_fingerprint_output(tmp_path) -> None:
    _, transcript, raw = _fixture()
    transcript_id = "测试讲道"
    extraction = _extraction_metadata(
        source_sha256=hashlib.sha256(raw).hexdigest(),
        system_prompt="prompt-v1",
        model_id="model-a",
        reasoning_effort="medium",
        max_output_tokens=6000,
    )
    from backend.pipeline.corpus_survey_runner import _slug

    path = tmp_path / f"{_slug(transcript_id)}.first-pass.json"
    path.write_text(
        json.dumps(
            {
                "source": {"transcript_id": transcript_id},
                "extraction": {
                    "fingerprint_sha256": extraction["fingerprint_sha256"]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert _existing_output(
        tmp_path,
        transcript_id,
        extraction["fingerprint_sha256"],
        transcript=transcript,
        raw_source=raw,
    ) is None


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


def test_legacy_mixed_locator_failure_uses_the_survey_validation_contract() -> None:
    survey, transcript, raw = _fixture()
    transcript["script"].insert(
        0, {"index": "subtitle-1", "type": "subtitle", "text": "## 编辑标题"}
    )
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")

    with pytest.raises(SurveyValidationError, match="legacy source has editorial rows"):
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


def test_review_cache_hit_requires_exact_snapshot_and_deterministic_routing() -> None:
    survey = {
        "candidate_claims": [{"claim_id": "CL-1", "anchors": []}],
    }
    response = {
        "sermon_assessment": {"summary": "忠实", "systemic_risks": []},
        "claim_reviews": [{
            "claim_id": "CL-1",
            "decision": "pass",
            "issues": [],
            "proposed_statement": "",
            "proposed_claim_kind": "",
            "proposed_route_type": "unchanged",
            "rationale": "锚点支持",
            "confidence": "high",
            "human_review_reason": "",
        }],
    }
    routed = apply_risk_routing(
        response,
        reviewer_fingerprint_sha256="review-fp",
        spot_check_percent=10,
    )
    artifact = {
        "schema_version": "wang_corpus_independent_review_v1",
        "reviewer": {"fingerprint_sha256": "review-fp"},
        "spot_check_percent": 10,
        "reviewed_claims": survey["candidate_claims"],
        **routed,
    }
    artifact["reviewer"]["artifact_sha256"] = _review_artifact_sha256(artifact)

    assert _matching_review_artifact(
        artifact,
        survey=survey,
        expected_fingerprint="review-fp",
        spot_check_percent=10,
    )
    artifact["claim_reviews"][0]["routing_status"] = "ai_reviewed"
    assert not _matching_review_artifact(
        artifact,
        survey=survey,
        expected_fingerprint="review-fp",
        spot_check_percent=10,
    )
