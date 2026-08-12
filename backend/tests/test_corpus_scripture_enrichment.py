from __future__ import annotations

import json

import pytest

from backend.pipeline.corpus_scripture_enrichment import (
    ScriptureEnrichmentValidationError,
    build_reference_inventory,
    make_enrichment,
    validate_enrichment,
)


def _survey() -> dict:
    return {
        "source": {"transcript_id": "sample", "sha256": "source-hash"},
        "content_clusters": [
            {
                "cluster_id": "T001",
                "title": "登山变像",
                "function": "exegesis",
                "summary": "比较马太与路加的记载。",
                "scripture_refs": ["太 17:1–8；路 9:28–36"],
            }
        ],
        "candidate_claims": [
            {
                "claim_id": "C001",
                "statement": "两卷福音书采用不同计日方法。",
                "claim_kind": "reasoning_conclusion",
                "attribution": "close_paraphrase",
                "scripture_refs": ["太 17:1", "路 9:28"],
                "anchors": [{"verbatim_excerpt": "马太说过了六天，路加说约有八天。"}],
            }
        ],
    }


def test_inventory_splits_multiple_books_and_normalizes_osis() -> None:
    inventory = build_reference_inventory(_survey())
    assert [item["osis"] for item in inventory] == [
        "Matt.17.1-Matt.17.8",
        "Luke.9.28-Luke.9.36",
        "Matt.17.1",
        "Luke.9.28",
    ]
    assert len({item["ref_key"] for item in inventory}) == 4


def test_inventory_preserves_unresolved_reference() -> None:
    survey = _survey()
    survey["content_clusters"][0]["scripture_refs"] = ["某处经文"]
    inventory = build_reference_inventory(survey)
    assert inventory[0]["osis"] is None
    assert inventory[0]["normalization_status"] == "unresolved"


def test_validation_rejects_invented_or_missing_reference(tmp_path) -> None:
    survey = _survey()
    survey_path = tmp_path / "survey.json"
    survey_path.write_text(json.dumps(survey, ensure_ascii=False), encoding="utf-8")
    inventory = build_reference_inventory(survey)
    response = {
        "classifications": [
            {"ref_key": item["ref_key"], "role": "primary_passage", "role_reason": "正文持续解释。", "confidence": "high"}
            for item in inventory
        ]
    }
    enrichment = make_enrichment(
        survey, survey_path, inventory, response,
        model="gpt-5.6-terra", reasoning_effort="medium",
    )
    validate_enrichment(enrichment, survey, survey_path)

    enrichment["references"][0]["raw_text"] = "约 1:1"
    with pytest.raises(ScriptureEnrichmentValidationError, match="derived field changed"):
        validate_enrichment(enrichment, survey, survey_path)


def test_validation_rejects_duplicate_occurrence(tmp_path) -> None:
    survey = _survey()
    survey_path = tmp_path / "survey.json"
    survey_path.write_text(json.dumps(survey, ensure_ascii=False), encoding="utf-8")
    inventory = build_reference_inventory(survey)
    response = {
        "classifications": [
            {"ref_key": item["ref_key"], "role": "theological_support", "role_reason": "支持结论。", "confidence": "medium"}
            for item in inventory
        ]
    }
    enrichment = make_enrichment(
        survey, survey_path, inventory, response,
        model="gpt-5.6-terra", reasoning_effort="medium",
    )
    enrichment["references"].append(dict(enrichment["references"][0]))
    with pytest.raises(ScriptureEnrichmentValidationError, match="duplicate ref_key"):
        validate_enrichment(enrichment, survey, survey_path)
