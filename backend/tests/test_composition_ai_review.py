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
        "proposed_editorial_boundary": "",
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
        "editorial_boundary": "",
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


def _coverage_gap_plan() -> dict:
    """A plan shaped like CP-matthew-16-13-20's 太16:18b decision.

    `action`, `coverage` and `editorial_boundary.required` together are what
    lets a claimless decision through the draft audit, and together they are
    what orders the author to write "there is no material for this".
    """

    return {
        "plan_id": "CP-test",
        "passage": "Matt.16.13-Matt.16.20",
        "decisions": [
            {
                "decision_id": "CD-1",
                "action": "coverage_gap",
                "decision": "直接引用經文並明示現有材料沒有足夠的獨立解釋。",
                "rationale": "教授未講透之處不可由AI填滿。",
                "claim_ids": [],
                "coverage": "missing",
                "editorial_boundary": {
                    "required": True,
                    "label": "編輯說明",
                    "reason": "目前候選知識沒有足以支撐獨立解釋。",
                },
            }
        ],
    }


def test_consensus_can_withdraw_the_editorial_boundary_once_material_arrives() -> None:
    """Regression: the review could promote a coverage gap to a real section
    and route material into it, but not withdraw the note ordering the author
    to declare that no material exists. The author then received two opposite
    instructions and the "no material" sentence survived into the article.
    """

    patch = _empty_patch()
    patch.update(
        {
            "action": "main_section",
            "coverage": "available",
            "add_claim_ids": ["CL-HADES"],
            "editorial_boundary": "withdrawn",
        }
    )
    adjudication = {
        "adjudications": [
            {"decision_id": "CD-1", "decision": "accept", "rationale": "材料已補齊", "patch": patch}
        ]
    }
    candidate, _ = apply_consensus(_coverage_gap_plan(), adjudication, None)
    decision = candidate["decisions"][0]
    assert decision["action"] == "main_section"
    assert decision["coverage"] == "available"
    assert decision["claim_ids"] == ["CL-HADES"]
    # Removed outright, not left as `required: false`: the draft audit reads
    # `editorial_boundary.required`, and a decision with material has nothing
    # left to say about an editorial note.
    assert "editorial_boundary" not in decision


def test_an_empty_boundary_patch_leaves_the_coverage_gap_alone() -> None:
    """The patch schema is strict, so every field is present on every patch.
    "" has to mean "no change" or each accepted patch would silently withdraw
    a boundary nobody asked it to touch.
    """

    adjudication = {
        "adjudications": [
            {
                "decision_id": "CD-1",
                "decision": "accept",
                "rationale": "只改文字",
                "patch": {**_empty_patch(), "rationale": "改寫理由"},
            }
        ]
    }
    candidate, _ = apply_consensus(_coverage_gap_plan(), adjudication, None)
    assert candidate["decisions"][0]["editorial_boundary"]["required"] is True


def test_reviewer_sees_an_unrouted_claim_the_plan_s_passage_covers() -> None:
    """Regression: a claim added to the argument layer after the plan was
    built was invisible to the review that exists to route it.

    The projection only admitted claims a decision already used, or ones
    reaching it through `source_leads`/`occurrences` -- both null on every
    Matthew plan. `unrouted_material` is one of this reviewer's own finding
    types, and it could never see any.
    """

    from backend.pipeline.composition_ai_review_runner import _claim_projection

    knowledge = {
        "claims": [
            {
                "claim_id": "CL-1",
                "statement": "已經被編排使用的主張。",
                "scripture_refs": ["太16:16"],
                "evidence_step_ids": [],
            },
            {
                "claim_id": "CL-HADES",
                "statement": "太16:18原文字面作「陰間的門」，其意指陰間的權柄。",
                "scripture_refs": ["马太福音16:18"],
                "evidence_step_ids": [],
            },
            {
                "claim_id": "CL-ELSEWHERE",
                "statement": "另一段經文的主張。",
                "scripture_refs": ["马太福音17:20"],
                "evidence_step_ids": [],
            },
        ],
        "evidence_steps": [],
        "source_fragments": [],
        "source_documents": [],
    }
    plan = _coverage_gap_plan()
    plan["decisions"][0]["claim_ids"] = ["CL-1"]

    projection = _claim_projection(plan, knowledge)
    by_id = {item["claim_id"]: item for item in projection["available_claims"]}

    assert "CL-HADES" in by_id, "material inside the plan's own passage must reach the reviewer"
    assert "CL-ELSEWHERE" not in by_id, "a claim outside the passage is not this plan's business"
    # The reviewer has to be able to tell routed from unrouted material.
    assert by_id["CL-HADES"]["assigned_decision_ids"] == []
    assert by_id["CL-1"]["assigned_decision_ids"] == ["CD-1"]


def test_projection_carries_the_claim_text_not_only_an_absent_title() -> None:
    """No claim in the store has `title`; every one states itself in
    `statement`. Sending only the title handed the reviewer a nameless claim.
    """

    from backend.pipeline.composition_ai_review_runner import _claim_projection

    knowledge = {
        "claims": [
            {
                "claim_id": "CL-HADES",
                "statement": "太16:18原文字面作「陰間的門」。",
                "scripture_refs": ["太16:18"],
                "evidence_step_ids": [],
            }
        ],
        "evidence_steps": [],
        "source_fragments": [],
        "source_documents": [],
    }
    projection = _claim_projection(_coverage_gap_plan(), knowledge)
    assert projection["available_claims"][0]["statement"] == "太16:18原文字面作「陰間的門」。"


def test_a_plan_without_a_passage_still_projects() -> None:
    from backend.pipeline.composition_ai_review_runner import _claim_projection

    plan = _plan()  # no `passage` key at all
    projection = _claim_projection(
        plan,
        {"claims": [], "evidence_steps": [], "source_fragments": [], "source_documents": []},
    )
    assert projection["available_claims"] == []


def test_projection_drops_what_the_review_is_never_asked_about() -> None:
    """Two fifths of this payload was material no prompt mentions: the reader
    player's timeline on each decision, and the authoring contract carried on
    the plan. The reviewer judges which claims belong in which decision.

    Safe because `apply_consensus` patches the full plan, not this projection,
    so nothing trimmed here is missing from the reviewed candidate.
    """

    from backend.pipeline.composition_ai_review_runner import _claim_projection

    plan = _coverage_gap_plan()
    plan["global_rules"] = ["寫作規則"]
    plan["authoring_sections"] = [{"section_id": "reader-sec-01"}]
    plan["base_source"] = {"source_id": "SRC-1"}
    plan["decisions"][0]["source_presentations"] = [
        {"presentation_id": "SP-1", "start_seconds": 2507, "end_seconds": 3202}
    ]
    plan["decisions"][0]["source_presentation_summary"] = {"mode": "segment_group"}

    projected = _claim_projection(
        plan,
        {"claims": [], "evidence_steps": [], "source_fragments": [], "source_documents": []},
    )["plan"]

    for dropped in ("global_rules", "authoring_sections", "base_source"):
        assert dropped not in projected
    decision = projected["decisions"][0]
    assert "source_presentations" not in decision
    assert "source_presentation_summary" not in decision
    # Everything the review actually reasons about survives.
    assert decision["decision_id"] == "CD-1"
    assert decision["claim_ids"] == []
    assert decision["coverage"] == "missing"
    assert decision["editorial_boundary"]["required"] is True


def test_an_unrouted_claim_still_says_so_after_compaction() -> None:
    """Empty values are dropped from evidence rows, but not from the claim:
    `assigned_decision_ids: []` is how the reviewer sees unrouted material,
    and an absent key would make that invisible again.
    """

    from backend.pipeline.composition_ai_review_runner import _claim_projection

    knowledge = {
        "claims": [
            {
                "claim_id": "CL-HADES",
                "statement": "太16:18原文字面作「陰間的門」。",
                "scripture_refs": ["太16:18"],
                "evidence_step_ids": ["E-1"],
            }
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "E-1",
                "statement": "教授說明原文字面。",
                "support_eligibility": "eligible_candidate",
                "function": None,
                "source_fragment_id": "FR-1",
            }
        ],
        "source_fragments": [
            {"fragment_id": "FR-1", "verbatim_excerpt": "陰間的門", "media_time": None}
        ],
        "source_documents": [],
    }
    claim = _claim_projection(_coverage_gap_plan(), knowledge)["available_claims"][0]
    assert claim["assigned_decision_ids"] == []
    step = claim["evidence"][0]
    assert "function" not in step, "a null function is 115 rows of noise"
    assert step["support_eligibility"] == "eligible_candidate"
    assert "media_time" not in step["source_fragments"][0]
    assert step["source_fragments"][0]["verbatim_excerpt"] == "陰間的門"


def test_a_pass_row_restating_the_existing_boundary_is_normalized_away() -> None:
    """Regression: the review schema is strict, so a reviewer with nothing to
    change still has to emit `proposed_editorial_boundary`. Restating the
    boundary a decision already has read to validation as a proposal on a pass
    row and ended the whole review.
    """

    row = _pass_review("CD-1")
    row["proposed_editorial_boundary"] = "required"
    normalized = _normalize_review_response(
        {
            "scope_confirmation": "composition_and_argument_structure_no_theological_critique",
            "decision_reviews": [row],
        }
    )
    normalized_row = normalized["decision_reviews"][0]
    assert normalized_row["proposed_editorial_boundary"] == ""
    # Blanking is still discarding model output; the artifact records it.
    assert normalized_row["normalized_away"]["proposed_editorial_boundary"] == "required"


def test_promoting_an_action_updates_the_name_the_store_reads_first() -> None:
    """Regression: the reviewed candidate carried `action: main_section` beside
    the untouched `decision_type: coverage_gap` it came out of the store with.
    `CompositionDecisionRecord` accepts either name but reads `decision_type`
    first, so ingesting the result restored exactly the value the review had
    just changed -- the promotion looked applied and silently evaporated.
    """

    plan = _coverage_gap_plan()
    plan["decisions"][0]["decision_type"] = "coverage_gap"
    patch = _empty_patch()
    patch.update({"action": "main_section", "add_claim_ids": ["CL-HADES"]})
    candidate, _ = apply_consensus(
        plan,
        {"adjudications": [
            {"decision_id": "CD-1", "decision": "accept", "rationale": "材料已補齊", "patch": patch}
        ]},
        None,
    )
    decision = candidate["decisions"][0]
    assert decision["action"] == "main_section"
    assert decision["decision_type"] == "main_section"


def test_a_patched_rationale_replaces_the_reason_the_store_reads_first() -> None:
    """The same alias trap one field over: `reason` is read before
    `rationale`, so a promoted decision kept the justification for the
    coverage gap it had just stopped being.
    """

    plan = _coverage_gap_plan()
    plan["decisions"][0]["reason"] = "教授未講透之處也不可由AI填滿。"
    patch = _empty_patch()
    patch["rationale"] = "材料已足以解釋本段，coverage_gap 前提不再成立。"
    candidate, _ = apply_consensus(
        plan,
        {"adjudications": [
            {"decision_id": "CD-1", "decision": "accept", "rationale": "同意", "patch": patch}
        ]},
        None,
    )
    decision = candidate["decisions"][0]
    assert decision["rationale"].startswith("材料已足以")
    assert decision["reason"] == decision["rationale"]


def test_a_decision_cannot_be_routed_to_its_own_plan() -> None:
    """`topic_plan_ids` routes out to a *topic* plan. The adjudicator named the
    scripture plan the decision already belongs to, which passed the "must be a
    real plan_id" rule because the plan's own id is real.
    """

    actionable = {"decision_id": "CD-1", "decision": "changes_suggested", "issues": [{"issue_type": "claim_omitted"}]}
    patch = _empty_patch()
    patch["topic_plan_ids"] = ["CP-test"]
    response = {
        "scope_confirmation": "composition_and_argument_structure_no_theological_critique",
        "adjudications": [
            {"decision_id": "CD-1", "decision": "accept", "rationale": "同意", "patch": patch}
        ],
    }
    with pytest.raises(CompositionReviewValidationError, match="own plan"):
        validate_adjudication(response, [actionable], {"CL-1"}, "CP-test")
    # A real topic plan is still fine, and the check is skipped when no plan id
    # is supplied so existing callers keep working.
    patch["topic_plan_ids"] = ["CP-topic-son-of-man"]
    validate_adjudication(response, [actionable], {"CL-1"}, "CP-test")
    validate_adjudication(response, [actionable], {"CL-1"})
