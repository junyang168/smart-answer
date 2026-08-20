from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer
from backend.pipeline.detailed_knowledge_extraction import (
    DetailedExtractionValidationError,
    extraction_identity,
    validate_response,
)
from backend.pipeline.detailed_knowledge_extraction_runner import _validation_feedback
from backend.pipeline.detailed_knowledge_extraction_runner import compile_package
from backend.pipeline.knowledge_consensus_applier import (
    ConsensusApplicationError,
    apply_consensus_overrides,
)


def _transcript() -> dict:
    return {
        "metadata": {"title": "测试讲道", "status": "published"},
        "script": [
            {"index": 10, "start_time": 1.0, "end_time": 8.0, "text": "有人说人子只强调人性。我说不对。"},
            {"index": 11, "start_time": 8.0, "end_time": 16.0, "text": "但以理书所说的那一位人子领受永远的权柄。"},
            {"index": 12, "start_time": 16.0, "end_time": 20.0, "text": "听众：所以这表明神性吗？"},
        ],
    }


def _response() -> dict:
    return {
        "questions": [
            {
                "question_id": "Q001", "text": "这表明神性吗？", "questioner": "audience",
                "question_type": "clarification", "answer_state": "answered", "answer_claim_ids": ["CL001"],
                "anchors": [{"segment_index": "S0003", "start_time": 16.0, "end_time": 20.0, "verbatim_excerpt": "所以这表明神性吗？"}],
            }
        ],
        "positions": [
            {
                "position_id": "POS001", "title": "人子只强调人性", "attribution": "external_view",
                "anchors": [{"segment_index": "S0001", "start_time": 1.0, "end_time": 8.0, "verbatim_excerpt": "有人说人子只强调人性"}],
            }
        ],
        "observations": [
            {
                "observation_id": "OBS001", "statement": "人子领受永远权柄", "observation_type": "scripture_text", "argument_role": "background",
                "scripture_refs": ["但以理书7:13-14"],
                "anchors": [{"segment_index": "S0002", "start_time": 8.0, "end_time": 16.0, "verbatim_excerpt": "那一位人子领受永远的权柄"}],
            }
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "E001", "statement": "教授否定只强调人性的读法", "step_type": "reasoning",
                "speaker": "professor", "stance": "asserted", "discourse_role": "refutation",
                "support_eligibility": "eligible_candidate", "scripture_refs": [], "produced_claim_ids": ["CL001"],
                "anchors": [{"segment_index": "S0001", "start_time": 1.0, "end_time": 8.0, "verbatim_excerpt": "我说不对"}],
            },
            {
                "evidence_step_id": "E002", "statement": "听众追问神性", "step_type": "dialogue_context",
                "speaker": "audience", "stance": "questioned", "discourse_role": "audience_question",
                "support_eligibility": "context_only", "scripture_refs": [], "produced_claim_ids": [],
                "anchors": [{"segment_index": "S0003", "start_time": 16.0, "end_time": 20.0, "verbatim_excerpt": "所以这表明神性吗？"}],
            },
        ],
        "claims": [
            {
                "claim_id": "CL001", "statement": "那一位人子具有神性身份", "claim_kind": "reasoning_conclusion",
                "attribution": "professor", "scripture_refs": ["但以理书7:13-14"], "topic_terms": ["人子", "神性"],
                "evidence_step_ids": ["E001"], "opposed_position_ids": ["POS001"], "review_status": "candidate",
            }
        ],
        "evidence_relations": [
            {"relation_id": "ER001", "from_id": "E001", "to_id": "E002", "relation_type": "contextualizes", "reason": "听众追问承接教授反驳"}
        ],
        "claim_relations": [],
    }


def test_rejects_non_verbatim_anchor() -> None:
    response = _response()
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = "教授说不对"
    with pytest.raises(DetailedExtractionValidationError, match="not verbatim"):
        validate_response(response, _transcript())


def test_reports_all_anchor_errors_in_one_validation_pass() -> None:
    response = _response()
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = "错误证据"
    response["observations"][0]["anchors"][0]["verbatim_excerpt"] = "错误观察"
    with pytest.raises(DetailedExtractionValidationError) as exc_info:
        validate_response(response, _transcript())
    message = str(exc_info.value)
    assert "E001" in message
    assert "OBS001" in message
    assert message.count("not verbatim") == 2


def test_reports_anchor_and_relation_errors_together() -> None:
    response = _response()
    response["evidence_steps"][0]["anchors"][0]["verbatim_excerpt"] = "错误证据"
    response["claim_relations"] = [
        {
            "claim_relation_id": "CR001",
            "from_id": "CL001",
            "to_id": "CL404",
            "relation_type": "supports",
            "reason": "测试",
        }
    ]
    with pytest.raises(DetailedExtractionValidationError) as exc_info:
        validate_response(response, _transcript())
    message = str(exc_info.value)
    assert "not verbatim" in message
    assert "unknown claim endpoint" in message


def test_validation_feedback_includes_exact_referenced_segment() -> None:
    feedback = _validation_feedback(
        DetailedExtractionValidationError("Q003: excerpt is not verbatim in S0002"),
        _transcript(),
    )
    assert "那一位人子领受永远的权柄" in feedback
    assert "上一版" in feedback
    assert "连续逐字复制" in feedback


def test_audience_evidence_cannot_be_eligible() -> None:
    response = _response()
    response["evidence_steps"][1]["support_eligibility"] = "eligible_candidate"
    with pytest.raises(DetailedExtractionValidationError, match="cannot be eligible"):
        validate_response(response, _transcript())


def test_compile_namespaces_ids_and_binds_source_hashes(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = _response()
    validate_response(response, transcript)
    extraction = extraction_identity(
        source_sha256=hashlib.sha256(raw).hexdigest(), prompt="prompt", model_id="gpt-5.6-sol",
        reasoning_effort="medium", max_output_tokens=32000,
    )
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=response, extraction=extraction,
    )
    claim = package["claims"][0]
    assert claim["claim_id"].startswith("DK-")
    assert claim["claim_id"].endswith("-CL001")
    assert claim["evidence_step_ids"][0].endswith("-E001")
    assert claim["opposed_position_ids"][0].endswith("-POS001")
    assert package["questions"][0]["answer_claim_ids"] == [claim["claim_id"]]
    assert package["source_fragments"][0]["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert package["source_fragments"][0]["anchor_state"] == "source_version_bound"


def test_compiled_package_can_feed_existing_claude_reviewer(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=_response(),
        extraction=extraction_identity(
            source_sha256=hashlib.sha256(raw).hexdigest(), prompt="prompt", model_id="gpt-5.6-sol",
            reasoning_effort="medium", max_output_tokens=32000,
        ),
    )
    normalized = _normalize_claim_layer(package)
    assert len(normalized["candidate_claims"]) == 1
    assert normalized["candidate_claims"][0]["anchors"][0]["verbatim_excerpt"] == "我说不对"


def test_consensus_applier_removes_anchor_and_relation_without_approving(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = _response()
    response["claim_relations"] = [
        {
            "claim_relation_id": "CR001", "from_id": "CL001", "to_id": "CL001",
            "relation_type": "contextualizes", "reason": "test",
        }
    ]
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=response,
        extraction=extraction_identity(
            source_sha256=hashlib.sha256(raw).hexdigest(), prompt="prompt", model_id="gpt-5.6-sol",
            reasoning_effort="medium", max_output_tokens=32000,
        ),
    )
    claim = package["claims"][0]
    relation_id = package["claim_relations"][0]["claim_relation_id"]
    anchor = claim["occurrences"][0]["anchors"][0]
    overrides = {
        "adjudication_fingerprint": {"fingerprint_sha256": "fp"},
        "claims": {
            claim["claim_id"]: {
                "status": "ai_consensus_applied", "approval_status": "not_human_approved",
                "excluded_anchors": [{
                    "transcript_id": "011WSR01", "paragraph_key": anchor["paragraph_key"],
                    "evidence_id": anchor["evidence_id"],
                    "verbatim_excerpt": anchor["proposed_highlight"]["text"],
                }],
                "excluded_claim_relation_ids": [relation_id],
                "anchor_additions": [{
                    "transcript_id": "011WSR01", "source_index": "11",
                    "verbatim_excerpt": "那一位人子领受永远的权柄", "evidence_type": "scripture_evidence",
                }],
                "structural_notes": [], "adjudication_fingerprint": "fp",
            }
        },
    }
    result = apply_consensus_overrides(package, overrides, {"011WSR01": transcript})
    updated = result["claims"][0]
    assert relation_id not in {row["claim_relation_id"] for row in result["claim_relations"]}
    assert any(value.startswith("AI-ADJ-") for value in updated["evidence_step_ids"])
    assert result["consensus_application"]["approval_status"] == "not_human_approved"


def test_consensus_applier_accepts_combined_string_fingerprint(tmp_path: Path) -> None:
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=_response(),
        extraction=extraction_identity(
            source_sha256=hashlib.sha256(raw).hexdigest(), prompt="prompt",
            model_id="gpt-5.6-sol", reasoning_effort="medium", max_output_tokens=32000,
        ),
    )
    result = apply_consensus_overrides(
        package,
        {"adjudication_fingerprint": "combined-fp", "claims": {}},
        {"011WSR01": transcript},
    )
    assert result["consensus_application"]["adjudication_fingerprint"] == "combined-fp"


def _two_claim_package(tmp_path: Path) -> tuple[dict, dict]:
    """A package whose two claims say the same thing from different evidence."""
    transcript = _transcript()
    raw = json.dumps(transcript, ensure_ascii=False).encode("utf-8")
    response = _response()
    response["evidence_steps"].append({
        "evidence_step_id": "E003", "statement": "同一结论的另一处证据", "step_type": "reasoning",
        "speaker": "professor", "stance": "asserted", "discourse_role": "restatement",
        "support_eligibility": "eligible_candidate", "scripture_refs": [], "produced_claim_ids": ["CL002"],
        "anchors": [{
            "segment_index": "S0002", "start_time": 8.0, "end_time": 16.0,
            "verbatim_excerpt": "但以理书所说的那一位人子",
        }],
    })
    response["claims"].append({
        "claim_id": "CL002", "statement": "人子具有神性身份（第二章节重复）",
        "claim_kind": "reasoning_conclusion", "attribution": "professor",
        "scripture_refs": ["但以理书7:13-14"], "topic_terms": ["人子"],
        "evidence_step_ids": ["E003"], "opposed_position_ids": [], "review_status": "candidate",
    })
    response["claim_relations"] = [{
        "claim_relation_id": "CR001", "from_id": "CL002", "to_id": "CL001",
        "relation_type": "supports", "reason": "test",
    }]
    package = compile_package(
        transcript_id="011WSR01", transcript_path=tmp_path / "011WSR01.json",
        transcript=transcript, raw=raw, response=response,
        extraction=extraction_identity(
            source_sha256=hashlib.sha256(raw).hexdigest(), prompt="prompt", model_id="gpt-5.6-sol",
            reasoning_effort="medium", max_output_tokens=32000,
        ),
    )
    survivor_id, retired_id = (row["claim_id"] for row in package["claims"])
    overrides = {
        "adjudication_fingerprint": {"fingerprint_sha256": "fp"},
        "claims": {
            retired_id: {
                "status": "ai_consensus_applied", "approval_status": "not_human_approved",
                "excluded_anchors": [], "excluded_claim_relation_ids": [],
                "anchor_additions": [], "structural_notes": [],
                "superseded_by": survivor_id, "adjudication_fingerprint": "fp",
            }
        },
    }
    return package, overrides


def test_merging_a_duplicate_keeps_its_grip_on_the_source(tmp_path: Path) -> None:
    package, overrides = _two_claim_package(tmp_path)

    result = apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})

    survivor, retired = result["claims"]
    assert retired["superseded_by"] == survivor["claim_id"]
    assert retired["review_status"] == "superseded"
    # The retired claim is still in the file, but its evidence now hangs off
    # the claim that stays -- filtering superseded claims must not cost anchors.
    retired_evidence = set(retired["evidence_step_ids"])
    assert retired_evidence <= set(survivor["evidence_step_ids"])
    assert retired_evidence <= {
        str(anchor["evidence_id"])
        for occurrence in survivor["occurrences"] for anchor in occurrence["anchors"]
    }
    assert result["summary"]["active_claim_count"] == 1
    assert result["summary"]["superseded_claim_count"] == 1
    assert result["consensus_application"]["merged_claim_ids"] == {
        retired["claim_id"]: survivor["claim_id"]
    }


def test_merging_drops_the_relation_between_the_two_merged_claims(tmp_path: Path) -> None:
    package, overrides = _two_claim_package(tmp_path)

    result = apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})

    # CL002 supports CL001; retargeting would otherwise leave CL001 supporting itself.
    assert result["claim_relations"] == []


def test_merge_into_a_claim_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    package, overrides = _two_claim_package(tmp_path)
    retired_id = next(iter(overrides["claims"]))
    overrides["claims"][retired_id]["superseded_by"] = "CL999"

    with pytest.raises(ConsensusApplicationError, match="merge target does not exist"):
        apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})


def test_a_merge_may_also_exclude_the_relation_it_dissolves(tmp_path: Path) -> None:
    """One review finds both the duplicate and the wrong edge between the pair.

    The merge's own dedupe removes CR001 as a self-loop, so by the time the
    exclusion list is checked the id is gone from `claim_relations`.  "Already
    removed" has to satisfy "remove this" -- treating it as an unknown relation
    failed the entire application over a request that had been carried out.
    """
    package, overrides = _two_claim_package(tmp_path)
    retired_id = next(iter(overrides["claims"]))
    relation_id = package["claim_relations"][0]["claim_relation_id"]
    overrides["claims"][retired_id]["excluded_claim_relation_ids"] = [relation_id]

    result = apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})

    assert result["claim_relations"] == []
    assert result["consensus_application"]["dissolved_claim_relation_ids"] == [relation_id]
    assert result["consensus_application"]["removed_claim_relation_ids"] == [relation_id]


def test_an_exclusion_naming_no_relation_at_all_is_still_refused(tmp_path: Path) -> None:
    """Tolerating what the merge dissolved must not tolerate a typo."""
    package, overrides = _two_claim_package(tmp_path)
    retired_id = next(iter(overrides["claims"]))
    overrides["claims"][retired_id]["excluded_claim_relation_ids"] = ["CR-NOPE"]

    with pytest.raises(ConsensusApplicationError, match="unknown relations"):
        apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})


def test_merging_into_an_already_retired_claim_is_refused(tmp_path: Path) -> None:
    """Round two must not name round one's loser as the survivor.

    Both claims leave the live set at once, and the coverage guard then reports
    lost evidence -- true, but it names the symptom instead of the override.
    """
    package, overrides = _two_claim_package(tmp_path)
    merged = apply_consensus_overrides(package, overrides, {"011WSR01": _transcript()})
    survivor_id, retired_id = (row["claim_id"] for row in merged["claims"])
    second_round = {
        "adjudication_fingerprint": {"fingerprint_sha256": "fp2"},
        "claims": {
            survivor_id: {
                "status": "ai_consensus_applied", "approval_status": "not_human_approved",
                "excluded_anchors": [], "excluded_claim_relation_ids": [],
                "anchor_additions": [], "structural_notes": [],
                "superseded_by": retired_id, "adjudication_fingerprint": "fp2",
            }
        },
    }

    with pytest.raises(ConsensusApplicationError, match="already superseded"):
        apply_consensus_overrides(merged, second_round, {"011WSR01": _transcript()})
