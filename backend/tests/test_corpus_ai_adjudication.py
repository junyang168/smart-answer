from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.pipeline.corpus_ai_adjudication import (
    AIAdjudicationValidationError,
    compile_outcome,
    validate_claude_reconsideration,
    validate_openai_adjudication,
)
from backend.pipeline.corpus_ai_adjudication_runner import _has_matching_generation, _load_context
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


def test_adjudication_context_uses_transcript_id_not_source_node_id(tmp_path, monkeypatch) -> None:
    package_path = tmp_path / "package.json"
    package_path.write_text(
        json.dumps({
            "source_documents": [{
                "source_id": "SRC-content-addressed",
                "transcript_id": "sermon-human-id",
            }],
            "claims": [],
            "claim_relations": [],
        }),
        encoding="utf-8",
    )
    payload = {"script": [{"index": "7", "text": "教授原话"}]}
    monkeypatch.setattr(
        "backend.pipeline.corpus_ai_adjudication_runner.load_knowledge_source_document",
        lambda source, transcript_dirs: (payload, None, None),
    )

    _, _, transcripts, segments = _load_context(package_path, [tmp_path])

    assert transcripts == [("sermon-human-id", payload)]
    assert segments == {"sermon-human-id": {"7": "教授原话"}}


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
    client.system_cache_ttl = "1h"
    client.prefix_cache_ttl = "5m"
    client.client = SimpleNamespace(messages=Messages())
    result = client._post_chat_completion("system", "user", 0.0)
    assert result == "ok"
    assert "temperature" not in calls[0]
    assert "thinking" not in calls[0]


def test_prompt_caching_marks_system_and_stable_prefix() -> None:
    """The system prompt and the caller's stable payload are cached separately."""
    calls: list[dict] = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")], usage=None
            )

    client = Stage1AnthropicClient.__new__(Stage1AnthropicClient)
    client.model = "claude-sonnet-4-6"
    client.max_output_tokens = 100
    client.timeout_seconds = 90
    client.system_cache_ttl = "1h"
    client.prefix_cache_ttl = "5m"
    client.client = SimpleNamespace(messages=Messages())

    client._post_chat_completion("system", "feedback", 0.0, cache_prefix="stable source")

    request = calls[0]
    assert request["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    blocks = request["messages"][0]["content"]
    assert blocks[0]["text"] == "stable source"
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    # The volatile tail must stay outside the cached block, or every retry
    # would invalidate the entry it is supposed to read.
    assert blocks[1] == {"type": "text", "text": "feedback"}


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


def _merge_context() -> tuple[dict[str, dict], list[dict]]:
    claims = _claims()
    claims["CL-2"] = {"claim_id": "CL-2", "statement": "留下的那条", "anchors": [], "relations": []}
    reviews = [{
        "claim_id": "CL-1",
        "decision": "changes_suggested",
        "issues": [{
            "issue_type": "duplicate_claim",
            "duplicate_of_claim_id": "CL-2",
            "affected_anchor_indexes": [],
        }],
    }]
    return claims, reviews


def _merge_response(**patch_updates) -> dict:
    patch = _patch(statement="", claim_kind="", scripture_refs=[], superseded_by_claim_id="CL-2")
    patch.update(patch_updates)
    return {
        "scope_confirmation": "source_fidelity_only_no_theological_critique",
        "adjudications": [{
            "claim_id": "CL-1",
            "decision": "accept",
            "rationale": "两条说的是同一件事",
            "source_anchor_indexes": [],
            "patch": patch,
        }],
    }


def test_merge_is_an_executable_patch_on_its_own() -> None:
    claims, reviews = _merge_context()

    validate_openai_adjudication(
        _merge_response(), reviews=reviews, claims_by_id=claims, transcript_segments={},
    )


def test_openai_cannot_invent_a_merge_claude_did_not_name() -> None:
    claims, reviews = _merge_context()
    reviews[0]["issues"][0]["duplicate_of_claim_id"] = "CL-3"

    with pytest.raises(AIAdjudicationValidationError, match="did not name"):
        validate_openai_adjudication(
            _merge_response(), reviews=reviews, claims_by_id=claims, transcript_segments={},
        )


def test_a_merge_cannot_also_rewrite_the_claim_it_retires() -> None:
    claims, reviews = _merge_context()

    with pytest.raises(AIAdjudicationValidationError, match="cannot also rewrite"):
        validate_openai_adjudication(
            _merge_response(statement="顺手改一句"),
            reviews=reviews, claims_by_id=claims, transcript_segments={},
        )


def test_merge_target_cannot_itself_be_merged_away() -> None:
    """A chain would make the survivor depend on override ordering."""
    claims, reviews = _merge_context()
    claims["CL-3"] = {"claim_id": "CL-3", "statement": "第三条", "anchors": [], "relations": []}
    reviews.append({
        "claim_id": "CL-2",
        "decision": "changes_suggested",
        "issues": [{
            "issue_type": "duplicate_claim",
            "duplicate_of_claim_id": "CL-3",
            "affected_anchor_indexes": [],
        }],
    })
    response = _merge_response()
    response["adjudications"].append({
        "claim_id": "CL-2",
        "decision": "accept",
        "rationale": "也重复",
        "source_anchor_indexes": [],
        "patch": _patch(statement="", claim_kind="", scripture_refs=[], superseded_by_claim_id="CL-3"),
    })

    with pytest.raises(AIAdjudicationValidationError, match="itself being merged"):
        validate_openai_adjudication(
            response, reviews=reviews, claims_by_id=claims, transcript_segments={},
        )
