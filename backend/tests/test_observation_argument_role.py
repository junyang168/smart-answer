"""The rule that an observation the professor reasoned from must record the step."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.pipeline.detailed_knowledge_extraction import (
    ARGUMENT_ROLES,
    DetailedExtractionValidationError,
    validate_response,
)
from backend.pipeline.detailed_knowledge_extraction_runner import compile_package

SEGMENT = (
    "此處原文動詞 φρονέω，意為「關心、重視」。"
    "耶穌責備彼得的，是他在思維與關注的方向上偏向人的意思。"
)


def _transcript():
    return {
        "metadata": {"title": "太16", "status": "published"},
        "script": [{"index": 37, "start_time": 1.0, "end_time": 9.0, "text": SEGMENT}],
    }


def _anchor(excerpt):
    return [{
        "segment_index": "S0001", "start_time": 1.0, "end_time": 9.0,
        "verbatim_excerpt": excerpt,
    }]


def _response(*, argument_role="load_bearing", with_step=True, with_relation=True):
    """The phroneo paragraph: the lexical fact, and the inference from it."""
    evidence_steps = []
    claims = []
    relations = []
    if with_step:
        evidence_steps = [{
            "evidence_step_id": "E001",
            "statement": "彼得的問題在於思維與關注的方向偏向人的意思。",
            "step_type": "original_language", "speaker": "professor", "stance": "asserted",
            "discourse_role": "由原文詞義推出責備的焦點。",
            "support_eligibility": "eligible_candidate", "scripture_refs": ["太16:23"],
            "produced_claim_ids": ["CL001"],
            "anchors": _anchor("耶穌責備彼得的，是他在思維與關注的方向上偏向人的意思。"),
        }]
        claims = [{
            "claim_id": "CL001", "statement": "耶穌責備彼得的是他思維方向偏向人的意思。",
            "claim_kind": "interpretive_judgment", "attribution": "professor",
            "scripture_refs": ["太16:23"], "topic_terms": ["體貼"],
            "evidence_step_ids": ["E001"], "opposed_position_ids": [],
            "review_status": "candidate",
        }]
        if with_relation:
            relations = [{
                "relation_id": "ER001", "from_id": "OBS001", "to_id": "E001",
                "relation_type": "supports", "reason": "詞義是這一步推論的根據。",
            }]
    return {
        "questions": [], "positions": [],
        "observations": [{
            "observation_id": "OBS001",
            "statement": "太16:23的φρονέω意為關心、重視。",
            "observation_type": "original_language",
            "argument_role": argument_role,
            "scripture_refs": ["太16:23"],
            "anchors": _anchor("此處原文動詞 φρονέω，意為「關心、重視」。"),
        }],
        "evidence_steps": evidence_steps,
        "claims": claims,
        "evidence_relations": relations,
        "claim_relations": [],
    }


def test_the_extraction_that_actually_happened_is_now_rejected():
    """What the store holds today: the lexical fact alone, nothing reasoning
    from it, and no claim on the verse.  That extraction validated in v1."""
    with pytest.raises(DetailedExtractionValidationError) as excinfo:
        validate_response(_response(with_step=False), _transcript())
    assert "load_bearing observation has no relation" in str(excinfo.value)
    assert "OBS001" in str(excinfo.value)


def test_producing_the_step_without_the_relation_is_still_rejected():
    """Extracting both halves is not enough; the pairing has to be recorded,
    because nothing downstream can recover it from the text."""
    with pytest.raises(DetailedExtractionValidationError, match="no relation"):
        validate_response(_response(with_relation=False), _transcript())


def test_the_observation_plus_the_step_it_supports_is_accepted():
    validate_response(_response(), _transcript())


def test_a_background_observation_needs_no_step():
    validate_response(_response(argument_role="background", with_step=False), _transcript())


def test_the_failure_message_offers_both_lawful_ways_out():
    """A model that cannot find the step must be told it may mark it background,
    or it will invent a step to satisfy the validator."""
    with pytest.raises(DetailedExtractionValidationError) as excinfo:
        validate_response(_response(with_step=False), _transcript())
    assert "mark it background" in str(excinfo.value)


@pytest.mark.parametrize("role", ["", None, "important", "LOAD_BEARING"])
def test_an_argument_role_outside_the_two_values_is_rejected(role):
    response = _response()
    response["observations"][0]["argument_role"] = role
    with pytest.raises(DetailedExtractionValidationError, match="argument_role must be one of"):
        validate_response(response, _transcript())


def test_argument_role_has_exactly_the_two_values():
    assert ARGUMENT_ROLES == ["load_bearing", "background"]


def test_a_relation_may_not_point_at_an_observation():
    """Observations feed the argument; they do not support each other."""
    response = _response()
    response["evidence_relations"][0]["to_id"] = "OBS001"
    with pytest.raises(DetailedExtractionValidationError, match="unknown evidence endpoint"):
        validate_response(response, _transcript())


def test_a_relation_from_an_unknown_id_is_still_rejected():
    response = _response()
    response["evidence_relations"][0]["from_id"] = "OBS999"
    with pytest.raises(DetailedExtractionValidationError, match="unknown relation source"):
        validate_response(response, _transcript())


def test_an_evidence_to_evidence_relation_still_validates():
    """Relaxing the source endpoint must not lose the original relation kind."""
    response = _response()
    response["evidence_steps"].append({
        "evidence_step_id": "E002", "statement": "第二步。",
        "step_type": "reasoning", "speaker": "professor", "stance": "asserted",
        "discourse_role": "延伸。", "support_eligibility": "eligible_candidate",
        "scripture_refs": [], "produced_claim_ids": [],
        "anchors": _anchor("耶穌責備彼得的"),
    })
    response["evidence_relations"].append({
        "relation_id": "ER002", "from_id": "E002", "to_id": "E001",
        "relation_type": "supports", "reason": "支持前一步。",
    })
    validate_response(response, _transcript())


def test_the_observation_edge_survives_id_namespacing():
    """The runner rewrites short model ids into corpus-unique ones; an
    observation-sourced relation must be remapped with the observation map,
    not the evidence map, or compiling raises KeyError."""
    package = compile_package(
        transcript_id="notes_manuscript:16",
        transcript_path=Path("16.json"),
        transcript=_transcript(),
        raw=b"{}",
        response=_response(),
        extraction={"fingerprint_sha256": "x"},
        source_descriptor={"source_id": "notes_manuscript:16"},
    )
    relation = package["knowledge_relations"][0]
    observation_id = package["observations"][0]["observation_id"]
    evidence_id = package["evidence_steps"][0]["evidence_step_id"]

    assert relation["from_id"] == observation_id
    assert relation["to_id"] == evidence_id
    assert observation_id.startswith("DK-") and observation_id.endswith("-OBS001")
