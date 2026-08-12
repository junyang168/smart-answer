from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.composition_ai_review import CompositionReviewValidationError
from backend.pipeline import qa_answer_diagnostic_runner as runner
from backend.pipeline.qa_answer_diagnostic_runner import (
    _final_issue_status,
    _namespace_review_issues,
    _validate_review,
    build_projection,
    run,
    run_fingerprint,
)


def test_projection_keeps_answer_context_and_opposition_separate() -> None:
    qa = {
        "cases": [{
            "case_id": "QA-1",
            "answer_claim_ids": ["CL-A"],
            "context_claim_ids": ["CL-C"],
            "source_question_ids": ["Q-1"],
            "opposed_position_ids": ["POS-1"],
        }]
    }
    knowledge = {
        "claims": [
            {"claim_id": "CL-A", "title": "直接答案", "evidence_step_ids": ["EV-1"]},
            {"claim_id": "CL-C", "title": "背景", "evidence_step_ids": []},
        ],
        "evidence_steps": [{
            "evidence_step_id": "EV-1",
            "statement": "教授的理由",
            "speaker": "professor",
            "stance": "endorsed",
            "support_eligibility": "eligible",
            "source_fragment_id": "FR-1",
        }],
        "source_fragments": [{"fragment_id": "FR-1", "verbatim_excerpt": "教授原话"}],
        "questions": [{"question_id": "Q-1", "text": "问题"}],
        "position_nodes": [{"position_id": "POS-1", "title": "反方"}],
        "claim_relations": [],
    }

    projection = build_projection(qa, knowledge)
    case = projection["cases"][0]
    assert case["answer_claims"][0]["claim_id"] == "CL-A"
    assert case["context_claims"][0]["claim_id"] == "CL-C"
    assert case["opposed_positions"][0]["position_id"] == "POS-1"
    assert case["answer_claims"][0]["eligible_evidence"][0]["source_fragments"][0]["verbatim_excerpt"] == "教授原话"


def test_projection_respects_evidence_gate_and_drops_dangling_relations() -> None:
    qa = {
        "cases": [{
            "case_id": "QA-1",
            "answer_claim_ids": ["CL-A"],
            "context_claim_ids": [],
            "source_question_ids": [],
            "opposed_position_ids": [],
        }]
    }
    knowledge = {
        "claims": [{
            "claim_id": "CL-A",
            "title": "直接答案",
            "evidence_step_ids": ["EV-GOOD", "EV-BAD"],
            "eligible_evidence_step_ids": ["EV-GOOD"],
            "withheld_evidence_step_ids": ["EV-BAD"],
        }],
        "evidence_steps": [
            {"evidence_step_id": "EV-GOOD", "support_eligibility": "eligible"},
            {"evidence_step_id": "EV-BAD", "support_eligibility": "eligible"},
        ],
        "source_fragments": [],
        "questions": [],
        "position_nodes": [],
        "claim_relations": [
            {"claim_relation_id": "CR-DANGLING", "source_id": "CL-A", "target_id": "CL-NOT-IN-PACKAGE"}
        ],
    }

    case = build_projection(qa, knowledge)["cases"][0]
    claim = case["answer_claims"][0]
    assert [item["evidence_step_id"] for item in claim["eligible_evidence"]] == ["EV-GOOD"]
    assert [item["evidence_step_id"] for item in claim["withheld_evidence"]] == ["EV-BAD"]
    # Nothing can be said about an endpoint the package does not contain.
    assert case["claim_relations"] == []


def test_projection_keeps_relations_reaching_outside_the_case() -> None:
    qa = {
        "cases": [{
            "case_id": "QA-1",
            "answer_claim_ids": ["CL-A"],
            "context_claim_ids": [],
            "source_question_ids": [],
            "opposed_position_ids": ["POS-1"],
        }]
    }
    knowledge = {
        "claims": [
            {"claim_id": "CL-A", "title": "教授的主张", "evidence_step_ids": []},
            {"claim_id": "CL-QUALIFIER", "title": "另一处的限定", "evidence_step_ids": []},
        ],
        "evidence_steps": [],
        "source_fragments": [],
        "questions": [],
        "position_nodes": [{"position_id": "POS-1", "title": "反方立场"}],
        "claim_relations": [
            {"claim_relation_id": "CR-1", "source_id": "CL-A", "target_id": "POS-1", "relation_type": "refutes"},
            {"claim_relation_id": "CR-2", "source_id": "CL-A", "target_id": "CL-QUALIFIER", "relation_type": "qualifies"},
        ],
    }

    relations = {item["claim_relation_id"]: item for item in build_projection(qa, knowledge)["cases"][0]["claim_relations"]}
    # The refutes edge is what proves the professor rejected the opposed view;
    # hiding it is exactly what an attribution question turns on.
    assert relations["CR-1"]["target_title"] == "反方立场"
    assert "target_outside_case" not in relations["CR-1"]
    # A qualification pointing at an unlisted claim is the missing qualification
    # the reviewer is asked to detect, so it must be visible and marked.
    assert relations["CR-2"]["target_outside_case"] is True
    assert relations["CR-2"]["target_title"] == "另一处的限定"


def test_review_requires_exact_case_coverage_and_issue_consistency() -> None:
    valid = {
        "scope_confirmation": "answer_fidelity_and_system_diagnosis_no_theological_critique",
        "case_reviews": [{
            "case_id": "QA-1",
            "decision": "pass",
            "answer_state_assessment": "supported",
            "issues": [],
            "rationale": "来源支持",
            "confidence": "high",
        }],
    }
    _validate_review(valid, {"QA-1"})

    invalid = {**valid, "case_reviews": []}
    with pytest.raises(CompositionReviewValidationError):
        _validate_review(invalid, {"QA-1"})

    invalid = {
        **valid,
        "case_reviews": [{**valid["case_reviews"][0], "decision": "changes_required"}],
    }
    with pytest.raises(CompositionReviewValidationError):
        _validate_review(invalid, {"QA-1"})


def test_per_case_issue_ids_are_namespaced_before_combining() -> None:
    first = _namespace_review_issues("QA-A", {"issues": [{"issue_id": "ISS-1"}]})
    second = _namespace_review_issues("QA-B", {"issues": [{"issue_id": "ISS-1"}]})
    assert first["issues"][0]["issue_id"] == "QA-A::ISS-1"
    assert second["issues"][0]["issue_id"] == "QA-B::ISS-1"


def _diagnostic_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    qa_path = tmp_path / "qa.json"
    knowledge_path = tmp_path / "knowledge.json"
    output_path = tmp_path / "diagnostics.json"
    qa_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    knowledge_path.write_text(json.dumps({"claims": []}), encoding="utf-8")
    return qa_path, knowledge_path, output_path


def test_unchanged_inputs_reuse_diagnostics_instead_of_calling_models(
    tmp_path: Path, monkeypatch
) -> None:
    qa_path, knowledge_path, output_path = _diagnostic_inputs(tmp_path)
    fingerprint = run_fingerprint(
        qa_sha256=runner._sha256(qa_path.read_bytes()),
        knowledge_sha256=runner._sha256(knowledge_path.read_bytes()),
        claude_model="claude-sonnet-5",
        openai_model="gpt-5.6-sol",
    )
    output_path.write_text(
        json.dumps({"fingerprint": fingerprint, "summary": {"cases": 0}}), encoding="utf-8"
    )

    def explode(*args, **kwargs):
        raise AssertionError("model client must not be constructed for unchanged inputs")

    monkeypatch.setattr(runner, "Stage1AnthropicClient", explode)
    monkeypatch.setattr(runner, "Stage1OpenAIClient", explode)

    assert run(
        qa_path=qa_path,
        knowledge_path=knowledge_path,
        output_path=output_path,
        claude_model="claude-sonnet-5",
        openai_model="gpt-5.6-sol",
    )["summary"] == {"cases": 0}


def test_changed_question_text_invalidates_the_fingerprint(tmp_path: Path) -> None:
    qa_path, knowledge_path, _ = _diagnostic_inputs(tmp_path)
    first = run_fingerprint(
        qa_sha256=runner._sha256(qa_path.read_bytes()),
        knowledge_sha256=runner._sha256(knowledge_path.read_bytes()),
        claude_model="claude-sonnet-5",
        openai_model="gpt-5.6-sol",
    )
    qa_path.write_text(json.dumps({"cases": [{"case_id": "QA-1"}]}), encoding="utf-8")
    second = run_fingerprint(
        qa_sha256=runner._sha256(qa_path.read_bytes()),
        knowledge_sha256=runner._sha256(knowledge_path.read_bytes()),
        claude_model="claude-sonnet-5",
        openai_model="gpt-5.6-sol",
    )
    assert first["run_sha256"] != second["run_sha256"]


def test_superseded_diagnostics_are_archived(tmp_path: Path) -> None:
    output_path = tmp_path / "diagnostics.json"
    output_path.write_text(json.dumps({"fingerprint": {"run_sha256": "abc123def456789"}}), encoding="utf-8")

    archived = runner._archive(output_path)
    assert archived is not None and archived.exists()
    assert archived.name == "diagnostics.abc123def456.json"


def test_retracted_claude_issue_is_withdrawn_not_consensus() -> None:
    assert _final_issue_status({"decision": "accept"}, None) == "ai_consensus_issue"
    assert _final_issue_status(
        {"decision": "reject"}, {"decision": "withdraw"}
    ) == "withdrawn"
    assert _final_issue_status(
        {"decision": "reject"}, {"decision": "reaffirm"}
    ) == "human_diagnostic_required"
