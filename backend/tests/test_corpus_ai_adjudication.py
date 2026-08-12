from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.pipeline.corpus_ai_adjudication import (
    AIAdjudicationValidationError,
    compile_outcome,
    validate_claude_reconsideration,
    validate_openai_adjudication,
)
from backend.pipeline.corpus_ai_adjudication_runner import _has_matching_generation
from backend.pipeline.shared_knowledge_pilot import _apply_claim_overrides
from backend.pipeline.stage1 import Stage1AnthropicClient


def _claims() -> dict[str, dict]:
    return {
        "CL-1": {
            "claim_id": "CL-1",
            "statement": "旧主张",
            "anchors": [
                {
                    "transcript_id": "L3",
                    "paragraph_key": "10",
                    "evidence_id": "E1",
                    "verbatim_excerpt": "教授原话",
                }
            ],
            "relations": [
                {
                    "relation_id": "CR-1",
                    "type": "qualifies",
                    "target_claim_id": "CL-2",
                }
            ],
        }
    }


def _reviews() -> list[dict]:
    return [{"claim_id": "CL-1", "decision": "changes_suggested", "issues": [{}]}]


def _patch(**updates) -> dict:
    value = {
        "statement": "新主张",
        "claim_kind": "神学",
        "route_type": "unchanged",
        "scripture_refs": ["太17:1"],
        "excluded_anchor_indexes": [],
        "excluded_claim_relation_ids": [],
        "anchor_additions": [],
        "structural_notes": [],
    }
    value.update(updates)
    return value


def test_openai_must_cover_every_actionable_claude_review() -> None:
    response = {
        "scope_confirmation": "source_fidelity_only_no_theological_critique",
        "adjudications": [],
    }
    with pytest.raises(AIAdjudicationValidationError, match="every actionable"):
        validate_openai_adjudication(
            response,
            reviews=_reviews(),
            claims_by_id=_claims(),
            transcript_segments={"L3": {"10": "教授原话"}},
        )


def test_accept_requires_executable_patch_and_verbatim_new_anchor() -> None:
    response = {
        "scope_confirmation": "source_fidelity_only_no_theological_critique",
        "adjudications": [
            {
                "claim_id": "CL-1",
                "decision": "accept",
                "rationale": "來源支持",
                "source_anchor_indexes": [0],
                "patch": _patch(
                    anchor_additions=[
                        {
                            "transcript_id": "L3",
                            "source_index": "10",
                            "verbatim_excerpt": "不存在",
                            "evidence_type": "reasoning",
                        }
                    ]
                ),
            }
        ],
    }
    with pytest.raises(AIAdjudicationValidationError, match="not verbatim"):
        validate_openai_adjudication(
            response,
            reviews=_reviews(),
            claims_by_id=_claims(),
            transcript_segments={"L3": {"10": "教授原话"}},
        )


def test_fidelity_adjudication_cannot_silently_change_product_route() -> None:
    response = {
        "scope_confirmation": "source_fidelity_only_no_theological_critique",
        "adjudications": [
            {
                "claim_id": "CL-1",
                "decision": "accept",
                "rationale": "来源支持",
                "source_anchor_indexes": [0],
                "patch": _patch(route_type="topic_research"),
            }
        ],
    }
    with pytest.raises(AIAdjudicationValidationError, match="cannot change product route"):
        validate_openai_adjudication(
            response,
            reviews=_reviews(),
            claims_by_id=_claims(),
            transcript_segments={"L3": {"10": "教授原话"}},
        )


def test_excluded_anchor_must_be_one_claude_identified() -> None:
    claims = _claims()
    claims["CL-1"]["anchors"].append(
        {
            "transcript_id": "L3",
            "paragraph_key": "11",
            "evidence_id": "E2",
            "verbatim_excerpt": "另一句原话",
        }
    )
    reviews = _reviews()
    reviews[0]["issues"] = [{"affected_anchor_indexes": [0]}]
    response = {
        "scope_confirmation": "source_fidelity_only_no_theological_critique",
        "adjudications": [
            {
                "claim_id": "CL-1",
                "decision": "accept",
                "rationale": "来源支持",
                "source_anchor_indexes": [0],
                "patch": _patch(excluded_anchor_indexes=[1]),
            }
        ],
    }
    with pytest.raises(AIAdjudicationValidationError, match="not identified by Claude"):
        validate_openai_adjudication(
            response,
            reviews=reviews,
            claims_by_id=claims,
            transcript_segments={"L3": {"10": "教授原话", "11": "另一句原话"}},
        )


def test_relation_patch_must_reference_outgoing_relation() -> None:
    response = {
        "scope_confirmation": "source_fidelity_only_no_theological_critique",
        "adjudications": [
            {
                "claim_id": "CL-1",
                "decision": "accept",
                "rationale": "关系错误",
                "source_anchor_indexes": [0],
                "patch": _patch(
                    statement="",
                    claim_kind="",
                    scripture_refs=[],
                    excluded_claim_relation_ids=["CR-other"],
                ),
            }
        ],
    }
    with pytest.raises(AIAdjudicationValidationError, match="outside this claim"):
        validate_openai_adjudication(
            response,
            reviews=_reviews(),
            claims_by_id=_claims(),
            transcript_segments={"L3": {"10": "教授原话"}},
        )


def test_reject_then_claude_withdraw_needs_no_human() -> None:
    openai = {
        "adjudications": [
            {
                "claim_id": "CL-1",
                "decision": "reject",
                "rationale": "误解来源",
                "source_anchor_indexes": [0],
                "patch": _patch(
                    statement="",
                    claim_kind="",
                    route_type="unchanged",
                    scripture_refs=[],
                ),
            }
        ]
    }
    claude = {
        "reconsiderations": [
            {
                "claim_id": "CL-1",
                "decision": "withdraw",
                "rationale": "接受反驳",
                "source_anchor_indexes": [0],
            }
        ]
    }
    outcome = compile_outcome(openai, claude)
    assert outcome["summary"] == {
        "auto_applied": 0,
        "withdrawn": 1,
        "human_confirmation_required": 0,
        "human_disagreement_required": 0,
    }


def test_claude_escalation_survives_openai_agreement() -> None:
    openai = {
        "adjudications": [
            {
                "claim_id": "CL-1",
                "decision": "accept",
                "rationale": "意见成立",
                "source_anchor_indexes": [0],
                "patch": _patch(statement="修正后的主张"),
            }
        ]
    }
    reviews = [
        {
            "claim_id": "CL-1",
            "decision": "human_review_required",
            "human_review_reason": "无法从来源判断是教授还是反方立场",
        }
    ]
    outcome = compile_outcome(openai, None, reviews=reviews)
    assert outcome["summary"]["human_confirmation_required"] == 1
    assert outcome["summary"]["auto_applied"] == 0
    # Agreement between models produces a patch, but it waits for the person
    # Claude asked for instead of silently editing the candidate layer.
    assert outcome["claim_overrides"] == {}
    assert outcome["pending_patches"]["CL-1"]["statement"] == "修正后的主张"
    assert outcome["results"][0]["human_review_reason"] == "无法从来源判断是教授还是反方立场"


def test_persistent_model_disagreement_is_the_only_human_route() -> None:
    response = {
        "scope_confirmation": "source_fidelity_only_no_theological_critique",
        "reconsiderations": [
            {
                "claim_id": "CL-1",
                "decision": "maintain",
                "rationale": "原话仍支持",
                "source_anchor_indexes": [0],
            }
        ],
    }
    validate_claude_reconsideration(
        response,
        rejected_claim_ids={"CL-1"},
        claims_by_id=_claims(),
    )
    openai = {
        "adjudications": [
            {
                "claim_id": "CL-1",
                "decision": "reject",
                "rationale": "不同意",
                "source_anchor_indexes": [0],
                "patch": _patch(statement="", claim_kind="", route_type="unchanged", scripture_refs=[]),
            }
        ]
    }
    assert compile_outcome(openai, response)["summary"]["human_disagreement_required"] == 1


def test_consensus_override_changes_candidate_without_human_approval() -> None:
    source = {
        "claims": [
            {
                "claim_id": "CL-1",
                "title": "旧主张",
                "claim_type": "解经",
                "scripture_refs": [],
                "occurrences": [
                    {
                        "transcript_id": "L3",
                        "anchors": [
                            {
                                "paragraph_key": "10",
                                "evidence_id": "E1",
                                "proposed_highlight": {"text": "教授原话"},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    overrides = {
        "claims": {
            "CL-1": {
                "status": "ai_consensus_applied",
                "approval_status": "not_human_approved",
                "title": "新主张",
                "claim_type": "神学",
                "route_type": "topic_research",
                "scripture_refs": ["太17:1"],
                "excluded_anchors": [
                    {
                        "transcript_id": "L3",
                        "paragraph_key": "10",
                        "evidence_id": "E1",
                        "verbatim_excerpt": "教授原话",
                    }
                ],
                "anchor_additions": [],
                "structural_notes": [],
                "adjudication_fingerprint": "fp",
            }
        }
    }
    result = _apply_claim_overrides(source, overrides)["claims"][0]
    assert result["title"] == "新主张"
    assert result["occurrences"][0]["anchors"] == []
    assert result["ai_adjudication"]["approval_status"] == "not_human_approved"


def test_sonnet_5_omits_legacy_sampling_and_thinking_fields() -> None:
    calls: list[dict] = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    client = Stage1AnthropicClient.__new__(Stage1AnthropicClient)
    client.model = "claude-sonnet-5"
    client.max_output_tokens = 100
    client.timeout_seconds = 90
    client.client = SimpleNamespace(messages=Messages())
    result = client._post_chat_completion("system", "user", 0.0)
    assert result == "ok"
    assert "temperature" not in calls[0]
    assert "thinking" not in calls[0]


def test_matching_adjudication_generation_requires_output_and_overrides(tmp_path) -> None:
    output_path = tmp_path / "adjudication.json"
    overrides_path = tmp_path / "overrides.json"
    output_path.write_text(
        '{"adjudicator":{"fingerprint_sha256":"same"}}', encoding="utf-8"
    )
    assert not _has_matching_generation(
        output_path=output_path,
        overrides_path=overrides_path,
        expected_fingerprint="same",
    )

    overrides_path.write_text(
        '{"adjudication_fingerprint":{"fingerprint_sha256":"same"}}', encoding="utf-8"
    )
    assert _has_matching_generation(
        output_path=output_path,
        overrides_path=overrides_path,
        expected_fingerprint="same",
    )

    overrides_path.write_text(
        '{"adjudication_fingerprint":{"fingerprint_sha256":"old"}}', encoding="utf-8"
    )
    assert not _has_matching_generation(
        output_path=output_path,
        overrides_path=overrides_path,
        expected_fingerprint="same",
    )
