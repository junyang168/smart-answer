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
from backend.api.canonical_repository.reviewed_candidate_contract import (
    reviewed_candidate_artifact_sha256,
)
from backend.api.canonical_repository.postgres_store import sha256_json


def _knowledge() -> dict:
    return {
        "batch": {"batch_id": "RB-TEST", "semantic_assumption": "none"},
        "source_documents": [
            {"source_id": "S-A", "transcript_id": "讲道甲", "title": "第一讲"},
            {"source_id": "S-B", "transcript_id": "讲道乙", "title": "第二讲"},
            {"source_id": "S-C", "transcript_id": "讲道丙", "title": "第三讲"},
        ],
        "evidence_steps": [
            {"evidence_step_id": "E-A", "statement": "甲证据", "step_type": "exegesis", "source_fragment_ids": ["F-A"], "produced_claim_ids": ["CL-A"]},
            {"evidence_step_id": "E-B", "statement": "乙证据", "step_type": "reasoning", "source_fragment_ids": ["F-B"], "produced_claim_ids": ["CL-B"]},
            {"evidence_step_id": "E-C", "statement": "丙证据", "step_type": "application", "source_fragment_ids": ["F-C"], "produced_claim_ids": ["CL-C"]},
        ],
        "source_fragments": [
            {"fragment_id": "F-A", "source_id": "S-A"},
            {"fragment_id": "F-B", "source_id": "S-B"},
            {"fragment_id": "F-C", "source_id": "S-C"},
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


def _authenticated_knowledge() -> dict:
    knowledge = _knowledge()
    reason = "独立 AI 复审：pass；仲裁：not_required"
    for claim in knowledge["claims"]:
        claim.update(
            {
                "review_status": "ai_consensus_reviewed",
                "reviewed_by": "claude-sonnet-5",
                "reviewed_at": "2026-09-12T00:00:00+00:00",
                "review_note": reason,
            }
        )
    member_lineage = []
    member_by_transcript = {}
    for index, source in enumerate(knowledge["source_documents"], start=1):
        transcript_id = source["transcript_id"]
        member = {
            "transcript_id": transcript_id,
            "reviewed_candidate_artifact_sha256": f"{index:064x}",
            "review_artifact_sha256": f"{index + 10:064x}",
            "review_fingerprint": f"{index + 20:064x}",
            "adjudication_artifact_sha256": f"{index + 30:064x}",
            "adjudication_fingerprint": f"{index + 40:064x}",
            "overrides_artifact_sha256": f"{index + 50:064x}",
            "review_resolution_count": 1,
        }
        member_lineage.append(member)
        member_by_transcript[transcript_id] = member
    resolutions = []
    for claim in knowledge["claims"]:
        transcript_id = claim["occurrences"][0]["transcript_id"]
        member = member_by_transcript[transcript_id]
        resolutions.append(
            {
                "schema_version": "wang_claim_ai_review_provenance_v1",
                "claim_id": claim["claim_id"],
                "independent_review_decision": "pass",
                "adjudication_status": "not_required",
                "target_review_status": "ai_consensus_reviewed",
                "reviewer_id": "claude-sonnet-5",
                "reason": reason,
                "approval_status": "not_human_approved",
                "source_transcript_id": transcript_id,
                "source_reviewed_candidate_artifact_sha256": member[
                    "reviewed_candidate_artifact_sha256"
                ],
                "source_review_artifact_sha256": member[
                    "review_artifact_sha256"
                ],
                "source_review_fingerprint": member["review_fingerprint"],
                "source_adjudication_artifact_sha256": member[
                    "adjudication_artifact_sha256"
                ],
                "source_adjudication_fingerprint": member[
                    "adjudication_fingerprint"
                ],
                "source_overrides_artifact_sha256": member[
                    "overrides_artifact_sha256"
                ],
            }
        )
    knowledge["lineage"] = deepcopy(member_lineage)
    aggregate = lambda field: sha256_json(  # noqa: E731 - compact test fixture
        [(row["transcript_id"], row[field]) for row in member_lineage]
    )
    knowledge["consensus_application"] = {
        "schema_version": "wang_ai_consensus_application_v2",
        "scope_kind": "research_batch_aggregate",
        "review_completion": "complete",
        "review_artifact_sha256": aggregate("review_artifact_sha256"),
        "review_fingerprint": aggregate("review_fingerprint"),
        "adjudication_artifact_sha256": aggregate(
            "adjudication_artifact_sha256"
        ),
        "adjudication_fingerprint": aggregate("adjudication_fingerprint"),
        "overrides_artifact_sha256": aggregate("overrides_artifact_sha256"),
        "applied_claim_ids": [],
        "merged_claim_ids": {},
        "final_review_status_counts": {"ai_consensus_reviewed": 3},
        "review_resolutions": resolutions,
        "member_artifact_lineage": deepcopy(member_lineage),
        "approval_status": "not_human_approved",
    }
    knowledge["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(knowledge)
    )
    return knowledge


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
    knowledge_path.write_text(
        json.dumps(_authenticated_knowledge(), ensure_ascii=False), encoding="utf-8"
    )
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


def test_runner_rejects_tampered_batch_before_any_model_call(tmp_path: Path) -> None:
    knowledge = _authenticated_knowledge()
    knowledge["claims"][0]["title"] = "tampered"
    knowledge_path = tmp_path / "knowledge.json"
    knowledge_path.write_text(json.dumps(knowledge, ensure_ascii=False), encoding="utf-8")
    openai = _FakeClient("gpt-test", [], reasoning_effort="medium")
    claude = _FakeClient("claude-test", [])

    with pytest.raises(CrossSermonRelationValidationError, match="authenticated"):
        run(
            knowledge_path=knowledge_path,
            output_dir=tmp_path / "relations",
            openai_client=openai,
            claude_client=claude,
            discovery_prompt="discover",
            review_prompt="review",
            adjudication_prompt="adjudicate",
            reconsideration_prompt="reconsider",
        )

    assert openai.calls == claude.calls == 0


def test_runner_rejects_resealed_graph_mismatch_before_any_model_call(
    tmp_path: Path,
) -> None:
    knowledge = _authenticated_knowledge()
    knowledge["evidence_steps"][0]["produced_claim_ids"] = []
    knowledge["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(knowledge)
    )
    knowledge_path = tmp_path / "knowledge.json"
    knowledge_path.write_text(json.dumps(knowledge, ensure_ascii=False), encoding="utf-8")
    openai = _FakeClient("gpt-test", [], reasoning_effort="medium")
    claude = _FakeClient("claude-test", [])

    with pytest.raises(CrossSermonRelationValidationError, match="reciprocal"):
        run(
            knowledge_path=knowledge_path,
            output_dir=tmp_path / "relations",
            openai_client=openai,
            claude_client=claude,
            discovery_prompt="discover",
            review_prompt="review",
            adjudication_prompt="adjudicate",
            reconsideration_prompt="reconsider",
        )

    assert openai.calls == claude.calls == 0


def test_runner_rejects_unknown_superseded_survivor_before_any_model_call(
    tmp_path: Path,
) -> None:
    knowledge = _authenticated_knowledge()
    claim = knowledge["claims"][0]
    claim["review_status"] = "superseded"
    claim["superseded_by"] = "CL-404"
    resolution = knowledge["consensus_application"]["review_resolutions"][0]
    resolution.update(
        {
            "independent_review_decision": "changes_suggested",
            "adjudication_status": "auto_applied",
            "target_review_status": "superseded",
        }
    )
    application = knowledge["consensus_application"]
    application["applied_claim_ids"] = [claim["claim_id"]]
    application["merged_claim_ids"] = {claim["claim_id"]: "CL-404"}
    application["final_review_status_counts"] = {
        "ai_consensus_reviewed": 2,
        "superseded": 1,
    }
    application["artifact_sha256"] = reviewed_candidate_artifact_sha256(
        knowledge
    )
    knowledge_path = tmp_path / "knowledge.json"
    knowledge_path.write_text(json.dumps(knowledge, ensure_ascii=False), encoding="utf-8")
    openai = _FakeClient("gpt-test", [], reasoning_effort="medium")
    claude = _FakeClient("claude-test", [])

    with pytest.raises(CrossSermonRelationValidationError, match="unknown superseded"):
        run(
            knowledge_path=knowledge_path,
            output_dir=tmp_path / "relations",
            openai_client=openai,
            claude_client=claude,
            discovery_prompt="discover",
            review_prompt="review",
            adjudication_prompt="adjudicate",
            reconsideration_prompt="reconsider",
        )

    assert openai.calls == claude.calls == 0


def test_projection_exposes_claim_evidence_without_assigning_topics() -> None:
    projection = build_projection(_knowledge())
    assert projection["comparison_policy"]["selection_is_not_classification"] is True
    assert "topic_candidates" not in projection
    assert projection["claims"][0]["evidence"][0]["evidence_step_id"] == "E-A"


def test_projection_excludes_superseded_claims() -> None:
    knowledge = _knowledge()
    knowledge["claims"][0]["superseded_by"] = "CL-B"

    projection = build_projection(knowledge)

    assert [row["claim_id"] for row in projection["claims"]] == ["CL-B", "CL-C"]


def test_runner_rejects_unresolved_live_claim_before_any_model_call(
    tmp_path: Path,
) -> None:
    knowledge = _authenticated_knowledge()
    claim = knowledge["claims"][0]
    claim["review_status"] = "human_review_required"
    resolution = knowledge["consensus_application"]["review_resolutions"][0]
    resolution["adjudication_status"] = "human_spot_check"
    resolution["target_review_status"] = "human_review_required"
    application = knowledge["consensus_application"]
    application["final_review_status_counts"] = {
        "ai_consensus_reviewed": 2,
        "human_review_required": 1,
    }
    application["artifact_sha256"] = reviewed_candidate_artifact_sha256(knowledge)
    knowledge_path = tmp_path / "knowledge.json"
    knowledge_path.write_text(json.dumps(knowledge, ensure_ascii=False), encoding="utf-8")
    openai = _FakeClient("gpt-test", [], reasoning_effort="medium")
    claude = _FakeClient("claude-test", [])

    with pytest.raises(
        CrossSermonRelationValidationError, match="every live claim"
    ):
        run(
            knowledge_path=knowledge_path,
            output_dir=tmp_path / "relations",
            openai_client=openai,
            claude_client=claude,
            discovery_prompt="discover",
            review_prompt="review",
            adjudication_prompt="adjudicate",
            reconsideration_prompt="reconsider",
        )

    assert openai.calls == claude.calls == 0
