import hashlib
import json

import pytest

from backend.pipeline.corpus_synthesis_runner import (
    _batch_theme_catalog,
    _expand_system_evidence,
    _load_current_surveys,
    _normalize_claim_refs,
    _validate_batch_theme_refs,
    _validate_refs,
)
from backend.pipeline.corpus_survey_runner import _extraction_metadata


def test_normalize_claim_refs_repairs_only_unique_local_id_variation() -> None:
    valid = {
        "sermon A::C001",
        "sermon A::C002",
        "sermon B::c1",
    }
    payload = {
        "theme_candidates": [
            {"claim_refs": ["sermon A::c1", "sermon A::C2", "sermon B::C01"]}
        ]
    }

    _normalize_claim_refs(payload, valid, ["theme_candidates"])
    _validate_refs(payload, valid, ["theme_candidates"])

    assert payload["theme_candidates"][0]["claim_refs"] == [
        "sermon A::C001",
        "sermon A::C002",
        "sermon B::c1",
    ]


def test_normalize_claim_refs_repairs_unique_transcript_separator_variation() -> None:
    payload = {"theme_candidates": [{"claim_refs": ["S 210405::c2"]}]}

    _normalize_claim_refs(payload, {"S_210405::C002"}, ["theme_candidates"])

    assert payload["theme_candidates"][0]["claim_refs"] == ["S_210405::C002"]


def test_normalize_claim_refs_does_not_guess_ambiguous_transcript_alias() -> None:
    payload = {"theme_candidates": [{"claim_refs": ["S 210405::c2"]}]}

    _normalize_claim_refs(
        payload,
        {"S_210405::C002", "S-210405::C002"},
        ["theme_candidates"],
    )

    assert payload["theme_candidates"][0]["claim_refs"] == ["S 210405::c2"]


def test_system_coverage_is_expanded_from_batch_themes() -> None:
    batches = [
        {
            "analysis": {"batch_number": 1},
            "theme_candidates": [
                {"theme_id": "T1", "claim_refs": ["A::c1", "B::c2"]},
                {"theme_id": "T2", "claim_refs": ["B::c2", "C::c3"]},
            ],
        }
    ]
    _, catalog = _batch_theme_catalog(batches)
    final = {
        "candidate_systems": [
            {
                "system_id": "S1",
                "batch_theme_refs": ["B01::T1", "B01::T2"],
                "claim_refs": ["A::c1"],
            }
        ]
    }

    _validate_batch_theme_refs(final, catalog)
    _expand_system_evidence(final, catalog)

    system = final["candidate_systems"][0]
    assert system["representative_claim_refs"] == ["A::c1"]
    assert system["claim_refs"] == ["A::c1", "B::c2", "C::c3"]


def test_load_current_surveys_combines_published_and_reviewed_without_duplicates(tmp_path) -> None:
    published_dir = tmp_path / "published"
    reviewed_dir = tmp_path / "reviewed"
    survey_dir = tmp_path / "surveys"
    published_dir.mkdir()
    reviewed_dir.mkdir()
    survey_dir.mkdir()

    published = {
        "metadata": {"status": "published"},
        "script": [{"index": 1, "text": "已发布内容", "start_time": 1, "end_time": 2}],
    }
    published_raw = json.dumps(published, ensure_ascii=False).encode()
    (published_dir / "shared.json").write_bytes(published_raw)
    (reviewed_dir / "shared.json").write_text(
        json.dumps([{"index": 1, "text": "较旧的审阅内容"}], ensure_ascii=False),
        encoding="utf-8",
    )
    reviewed_raw = json.dumps([{"index": 1, "text": "审阅内容"}], ensure_ascii=False).encode()
    (reviewed_dir / "review-only.json").write_bytes(reviewed_raw)

    def survey(transcript_id, raw, stage, text, start, end, prompt="prompt-v1"):
        extraction = _extraction_metadata(
            source_sha256=hashlib.sha256(raw).hexdigest(),
            system_prompt=prompt,
            model_id="model-a",
            reasoning_effort="medium",
            max_output_tokens=6000,
        )
        return {
            "survey_version": "wang_corpus_first_pass_v1",
            "source": {
                "transcript_id": transcript_id,
                "publication_status": stage,
                "segment_count": 1,
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "extraction": extraction,
            "content_clusters": [],
            "candidate_claims": [],
            "survey_summary": {
                "cluster_count": 0,
                "candidate_claim_count": 0,
                "high_confidence_claim_count": 0,
                "medium_confidence_claim_count": 0,
                "editorial_inference_count": 0,
            },
        }

    for name, payload in [
        ("shared", survey("shared", published_raw, "published", "已发布内容", 1, 2)),
        ("review-only", survey("review-only", reviewed_raw, "reviewed", "审阅内容", None, None)),
    ]:
        (survey_dir / f"{name}.first-pass.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    loaded = _load_current_surveys([published_dir, reviewed_dir], survey_dir)

    assert [item["source"]["transcript_id"] for item in loaded] == ["shared", "review-only"]


def test_load_current_surveys_rejects_legacy_generation(tmp_path) -> None:
    transcript_dir = tmp_path / "published"
    survey_dir = tmp_path / "surveys"
    transcript_dir.mkdir()
    survey_dir.mkdir()
    transcript = {
        "metadata": {"status": "published"},
        "script": [{"index": 1, "text": "内容", "start_time": 1, "end_time": 2}],
    }
    raw = json.dumps(transcript, ensure_ascii=False).encode()
    (transcript_dir / "legacy.json").write_bytes(raw)
    (survey_dir / "legacy.first-pass.json").write_text(
        json.dumps(
            {
                "survey_version": "wang_corpus_first_pass_v1",
                "source": {
                    "transcript_id": "legacy",
                    "publication_status": "published",
                    "segment_count": 1,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
                "content_clusters": [],
                "candidate_claims": [],
                "survey_summary": {
                    "cluster_count": 0,
                    "candidate_claim_count": 0,
                    "high_confidence_claim_count": 0,
                    "medium_confidence_claim_count": 0,
                    "editorial_inference_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="legacy survey without extraction generation"):
        _load_current_surveys([transcript_dir], survey_dir)


def test_load_current_surveys_rejects_mixed_generations(tmp_path) -> None:
    transcript_dir = tmp_path / "published"
    survey_dir = tmp_path / "surveys"
    transcript_dir.mkdir()
    survey_dir.mkdir()

    for number, prompt in [(1, "prompt-v1"), (2, "prompt-v2")]:
        transcript_id = f"sermon-{number}"
        transcript = {
            "metadata": {"status": "published"},
            "script": [
                {"index": 1, "text": f"内容{number}", "start_time": 1, "end_time": 2}
            ],
        }
        raw = json.dumps(transcript, ensure_ascii=False).encode()
        (transcript_dir / f"{transcript_id}.json").write_bytes(raw)
        extraction = _extraction_metadata(
            source_sha256=hashlib.sha256(raw).hexdigest(),
            system_prompt=prompt,
            model_id="model-a",
            reasoning_effort="medium",
            max_output_tokens=6000,
        )
        survey = {
            "survey_version": "wang_corpus_first_pass_v1",
            "source": {
                "transcript_id": transcript_id,
                "publication_status": "published",
                "segment_count": 1,
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "extraction": extraction,
            "content_clusters": [],
            "candidate_claims": [],
            "survey_summary": {
                "cluster_count": 0,
                "candidate_claim_count": 0,
                "high_confidence_claim_count": 0,
                "medium_confidence_claim_count": 0,
                "editorial_inference_count": 0,
            },
        }
        (survey_dir / f"{transcript_id}.first-pass.json").write_text(
            json.dumps(survey), encoding="utf-8"
        )

    with pytest.raises(RuntimeError, match="mixed extraction generations"):
        _load_current_surveys([transcript_dir], survey_dir)
