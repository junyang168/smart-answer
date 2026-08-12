from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from backend.pipeline.cross_sermon_relation import (
    SCOPE,
    CrossSermonRelationValidationError,
    apply_consensus,
    normalize_discovery,
    validate_discovery,
    validate_review,
)
from backend.pipeline.cross_sermon_relation_runner import build_projection, run


def _knowledge() -> dict:
    return {
        "batch": {"batch_id": "RB-TEST", "semantic_assumption": "none"},
        "source_documents": [
            {"transcript_id": "讲道甲", "title": "第一讲"},
            {"transcript_id": "讲道乙", "title": "第二讲"},
            {"transcript_id": "讲道丙", "title": "第三讲"},
        ],
        "evidence_steps": [
            {"evidence_step_id": "E-A", "statement": "甲证据", "step_type": "exegesis"},
            {"evidence_step_id": "E-B", "statement": "乙证据", "step_type": "reasoning"},
            {"evidence_step_id": "E-C", "statement": "丙证据", "step_type": "application"},
        ],
        "claims": [
            {
                "claim_id": "CL-A", "title": "甲主张", "evidence_step_ids": ["E-A"],
                "occurrences": [{"transcript_id": "讲道甲"}],
            },
            {
                "claim_id": "CL-B", "title": "乙主张", "evidence_step_ids": ["E-B"],
                "occurrences": [{"transcript_id": "讲道乙"}],
            },
            {
                "claim_id": "CL-C", "title": "丙主张", "evidence_step_ids": ["E-C"],
                "occurrences": [{"transcript_id": "讲道丙"}],
            },
        ],
    }


def _discovery() -> dict:
    return normalize_discovery(
        {
            "scope_confirmation": SCOPE,
            "relation_candidates": [
                {
                    "candidate_id": "temporary",
                    "source_claim_id": "CL-B",
                    "target_claim_id": "CL-A",
                    "relation_type": "duplicate",
                    "reason": "两讲表达同一命题",
                    "source_evidence_step_ids": ["E-B"],
                    "target_evidence_step_ids": ["E-A"],
                    "confidence": "high",
                }
            ],
            "unassigned_claim_ids": ["CL-C"],
            "comparison_summary": "一组重复，一条暂不归组。",
        }
    )


def _review(discovery: dict, *, decision: str = "pass") -> dict:
    candidate = discovery["relation_candidates"][0]
    proposed = candidate["relation_type"] if decision == "pass" else "extends"
    return {
        "scope_confirmation": SCOPE,
        "relation_reviews": [
            {
                "candidate_id": candidate["candidate_id"],
                "decision": decision,
                "proposed_relation_type": proposed,
                "reverse_direction": False,
                "explanation": "证据足以支持判断。",
                "confidence": "high",
            }
        ],
    }


class _FakeClient:
    def __init__(self, model: str, responses: list[dict], reasoning_effort: str | None = None):
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._responses = list(responses)
        self.calls = 0

    def generate_json(self, *_args, **_kwargs) -> dict:
        self.calls += 1
        return deepcopy(self._responses.pop(0))


def test_discovery_canonicalizes_symmetric_relation_and_preserves_evidence_sides() -> None:
    discovery = _discovery()
    row = discovery["relation_candidates"][0]
    assert row["candidate_id"].startswith("XSR-")
    assert (row["source_claim_id"], row["target_claim_id"]) == ("CL-A", "CL-B")
    assert row["source_evidence_step_ids"] == ["E-A"]
    assert row["target_evidence_step_ids"] == ["E-B"]
    validate_discovery(discovery, _knowledge())


def test_discovery_rejects_wrong_endpoint_evidence() -> None:
    discovery = _discovery()
    discovery["relation_candidates"][0]["source_evidence_step_ids"] = ["E-C"]
    with pytest.raises(CrossSermonRelationValidationError, match="does not belong"):
        validate_discovery(discovery, _knowledge())


def test_discovery_requires_every_claim_to_be_related_or_unassigned() -> None:
    discovery = _discovery()
    discovery["unassigned_claim_ids"] = []
    with pytest.raises(CrossSermonRelationValidationError, match="every claim"):
        validate_discovery(discovery, _knowledge())


def test_review_requires_exact_candidate_coverage() -> None:
    discovery = _discovery()
    review = _review(discovery)
    review["relation_reviews"] = []
    with pytest.raises(CrossSermonRelationValidationError, match="every relation"):
        validate_review(review, discovery)


def test_consensus_applies_claude_change_when_openai_accepts() -> None:
    discovery = _discovery()
    review = _review(discovery, decision="change")
    candidate_id = discovery["relation_candidates"][0]["candidate_id"]
    result = apply_consensus(
        discovery,
        review,
        {
            "scope_confirmation": SCOPE,
            "adjudications": [
                {"candidate_id": candidate_id, "decision": "accept", "reason": "同意。"}
            ],
        },
        None,
    )
    assert result["reviewed_relations"][0]["relation_type"] == "extends"
    assert result["summary"]["human_review_required"] == 0


def test_consensus_only_escalates_persistent_disagreement() -> None:
    discovery = _discovery()
    review = _review(discovery, decision="change")
    candidate_id = discovery["relation_candidates"][0]["candidate_id"]
    result = apply_consensus(
        discovery,
        review,
        {
            "scope_confirmation": SCOPE,
            "adjudications": [
                {"candidate_id": candidate_id, "decision": "reject", "reason": "维持原判。"}
            ],
        },
        {
            "scope_confirmation": SCOPE,
            "reconsiderations": [
                {"candidate_id": candidate_id, "decision": "reaffirm", "reason": "仍不同意。"}
            ],
        },
    )
    assert result["reviewed_relations"] == []
    assert result["outcomes"][0]["status"] == "human_review_required"
    assert result["summary"]["human_review_required"] == 1


def test_runner_is_reproducible_and_skips_matching_generation(tmp_path: Path) -> None:
    knowledge_path = tmp_path / "knowledge.json"
    knowledge_path.write_text(json.dumps(_knowledge(), ensure_ascii=False), encoding="utf-8")
    discovery = _discovery()
    review = _review(discovery)
    openai = _FakeClient("gpt-test", [discovery], reasoning_effort="medium")
    claude = _FakeClient("claude-test", [review])
    kwargs = {
        "knowledge_path": knowledge_path,
        "output_dir": tmp_path / "relations",
        "openai_client": openai,
        "claude_client": claude,
        "discovery_prompt": "discover",
        "review_prompt": "review",
        "adjudication_prompt": "adjudicate",
        "reconsideration_prompt": "reconsider",
    }
    first = run(**kwargs)
    assert first["reviewed_relations"] == 1
    assert openai.calls == 1
    assert claude.calls == 1
    second = run(**kwargs)
    assert second == first
    assert openai.calls == 1
    assert claude.calls == 1
    artifact = json.loads(
        (tmp_path / "relations" / "reviewed-relations.json").read_text(encoding="utf-8")
    )
    assert artifact["generation"]["fingerprint_sha256"]


def test_projection_exposes_claim_evidence_without_assigning_topics() -> None:
    projection = build_projection(_knowledge())
    assert projection["comparison_policy"]["selection_is_not_classification"] is True
    assert "topic_candidates" not in projection
    assert projection["claims"][0]["evidence"][0]["evidence_step_id"] == "E-A"
