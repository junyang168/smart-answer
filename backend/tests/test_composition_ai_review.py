from __future__ import annotations

import pytest

from backend.pipeline.composition_ai_review import (
    CompositionReviewValidationError,
    apply_consensus,
    validate_adjudication,
    validate_reconsideration,
    validate_review,
)
from backend.pipeline.composition_ai_review_runner import _normalize_review_response


def _plan() -> dict:
    return {
        "plan_id": "CP-test",
        "decisions": [
            {
                "decision_id": "CD-1",
                "action": "main_section",
                "decision": "原决定",
                "rationale": "原理由",
                "claim_ids": ["CL-1"],
                "coverage": "太1:1",
            },
            {
                "decision_id": "CD-2",
                "action": "topic_link",
                "decision": "第二决定",
                "rationale": "第二理由",
                "claim_ids": ["CL-2"],
                "coverage": "太1:2",
            },
        ],
    }


def _pass_review(decision_id: str) -> dict:
    return {
        "decision_id": decision_id,
        "decision": "pass",
        "issues": [],
        "proposed_action": "",
        "proposed_decision_text": "",
        "proposed_rationale": "",
        "proposed_add_claim_ids": [],
        "proposed_remove_claim_ids": [],
        "proposed_coverage": "",
        "rationale": "编排与现有证据相符",
        "confidence": "high",
        "human_review_reason": "",
    }


def _review() -> dict:
    return {
        "scope_confirmation": "composition_and_argument_structure_no_theological_critique",
        "plan_assessment": {
            "summary": "可用",
            "argument_layer_status": "usable_with_gaps",
            "argument_layer_findings": [
                {
                    "finding_type": "missing_relation",
                    "severity": "medium",
                    "explanation": "两条主张的关系尚未显式记录",
                    "claim_ids": ["CL-1", "CL-2"],
                    "relation_ids": [],
                    "recommended_action": "补关系后重审",
                }
            ],
            "systemic_risks": [],
        },
        "decision_reviews": [_pass_review("CD-1"), _pass_review("CD-2")],
    }


def _empty_patch() -> dict:
    return {
        "action": "",
        "decision_text": "",
        "rationale": "",
        "add_claim_ids": [],
        "remove_claim_ids": [],
        "coverage": "",
        "topic_plan_ids": [],
        "claim_hierarchy": {
            "paragraph_thesis": "",
            "supporting_claims": [],
            "corroborating_claims": [],
            "supporting_context": [],
            "parallel_context": [],
            "methodological_entry": "",
            "theological_structure": [],
            "original_language_support": "",
            "note": "",
        },
        "argument_layer_followups": [],
    }


def test_review_must_cover_every_decision() -> None:
    review = _review()
    review["decision_reviews"].pop()
    with pytest.raises(CompositionReviewValidationError, match="every composition decision"):
        validate_review(review, _plan(), {"CL-1", "CL-2"})


def test_pass_cannot_smuggle_in_composition_changes() -> None:
    review = _review()
    review["decision_reviews"][0]["proposed_decision_text"] = "偷偷改写"
    with pytest.raises(CompositionReviewValidationError, match="pass cannot propose"):
        validate_review(review, _plan(), {"CL-1", "CL-2"})


def test_pass_no_change_prose_is_normalized_before_validation() -> None:
    review = _review()
    review["decision_reviews"][0]["proposed_decision_text"] = "无需修改"
    normalized = _normalize_review_response(review)
    validate_review(normalized, _plan(), {"CL-1", "CL-2"})
    assert normalized["decision_reviews"][0]["proposed_decision_text"] == ""
    # Blanking is still discarding model output, so the artifact keeps the record.
    assert normalized["decision_reviews"][0]["normalized_away"] == {
        "proposed_decision_text": "无需修改"
    }


def test_normalization_leaves_no_record_when_nothing_was_dropped() -> None:
    normalized = _normalize_review_response(_review())
    assert "normalized_away" not in normalized["decision_reviews"][0]
    # Rows must not share the same mutable proposal lists after normalization.
    rows = normalized["decision_reviews"]
    if len(rows) > 1:
        assert rows[0]["proposed_add_claim_ids"] is not rows[1]["proposed_add_claim_ids"]


def test_adjudicator_must_cover_every_actionable_review() -> None:
    actionable = _pass_review("CD-1")
    actionable["decision"] = "changes_suggested"
    actionable["issues"] = [
        {"issue_type": "claim_omitted", "severity": "medium", "explanation": "遗漏", "claim_ids": ["CL-2"]}
    ]
    response = {
        "scope_confirmation": "composition_and_argument_structure_no_theological_critique",
        "adjudications": [],
    }
    with pytest.raises(CompositionReviewValidationError, match="every actionable"):
        validate_adjudication(response, [actionable], {"CL-1", "CL-2"})


def test_adjudicator_patch_action_must_be_a_real_action_value() -> None:
    actionable = _pass_review("CD-1")
    actionable["decision"] = "changes_suggested"
    actionable["issues"] = [
        {"issue_type": "wrong_product_axis", "severity": "medium", "explanation": "动作错误", "claim_ids": ["CL-1"]}
    ]
    patch = _empty_patch()
    patch["action"] = "把 action 改成 main_section"
    response = {
        "scope_confirmation": "composition_and_argument_structure_no_theological_critique",
        "adjudications": [
            {"decision_id": "CD-1", "decision": "accept", "rationale": "同意", "patch": patch}
        ],
    }
    with pytest.raises(CompositionReviewValidationError, match="invalid action"):
        validate_adjudication(response, [actionable], {"CL-1", "CL-2"})


def test_consensus_applies_composition_patch_and_records_argument_followup() -> None:
    patch = _empty_patch()
    patch.update(
        {
            "decision_text": "修订决定",
            "add_claim_ids": ["CL-2"],
            "argument_layer_followups": ["补充 CL-1 supports CL-2 的关系"],
        }
    )
    adjudication = {
        "adjudications": [
            {"decision_id": "CD-1", "decision": "accept", "rationale": "同意", "patch": patch}
        ]
    }
    candidate, outcome = apply_consensus(_plan(), adjudication, None)
    first = candidate["decisions"][0]
    assert first["decision"] == "修订决定"
    assert first["claim_ids"] == ["CL-1", "CL-2"]
    assert first["argument_layer_followups"] == ["补充 CL-1 supports CL-2 的关系"]
    assert candidate["ai_composition_consensus"]["approval_status"] == "not_human_approved"
    assert outcome["outcomes"] == [{"decision_id": "CD-1", "status": "auto_applied"}]


def test_rejected_review_requires_human_only_when_claude_maintains() -> None:
    adjudication = {
        "adjudications": [
            {"decision_id": "CD-1", "decision": "reject", "rationale": "不同意", "patch": _empty_patch()}
        ]
    }
    withdrawn = {
        "scope_confirmation": "composition_and_argument_structure_no_theological_critique",
        "reconsiderations": [{"decision_id": "CD-1", "decision": "withdraw", "rationale": "接受"}],
    }
    validate_reconsideration(withdrawn, {"CD-1"})
    _, outcome = apply_consensus(_plan(), adjudication, withdrawn)
    assert outcome["outcomes"][0]["status"] == "withdrawn"

    maintained = {
        **withdrawn,
        "reconsiderations": [{"decision_id": "CD-1", "decision": "maintain", "rationale": "仍不同意"}],
    }
    _, outcome = apply_consensus(_plan(), adjudication, maintained)
    assert outcome["outcomes"][0]["status"] == "human_disagreement_required"
