from __future__ import annotations

import pytest

from backend.pipeline.corpus_ai_review import (
    AIReviewValidationError,
    apply_risk_routing,
    reviewer_fingerprint,
    validate_review_response,
)
from backend.pipeline.corpus_ai_review_runner import (
    _archive_existing_review,
    _generate_valid_review,
    _normalize_claim_layer,
)


def _survey() -> dict:
    return {
        "candidate_claims": [
            {"claim_id": "C001", "anchors": [{"verbatim_excerpt": "原话"}]},
            {"claim_id": "C002", "anchors": [{"verbatim_excerpt": "另一句"}]},
        ]
    }


def _review() -> dict:
    return {
        "sermon_assessment": {"summary": "", "systemic_risks": []},
        "claim_reviews": [
            {
                "claim_id": "C001",
                "decision": "pass",
                "issues": [],
                "proposed_statement": "",
                "proposed_claim_kind": "",
                "proposed_route_type": "unchanged",
                "rationale": "来源支持",
                "confidence": "high",
                "human_review_reason": "",
            },
            {
                "claim_id": "C002",
                "decision": "changes_suggested",
                "issues": [
                    {
                        "issue_type": "speaker_attribution",
                        "severity": "high",
                        "explanation": "这是听众的话",
                        "affected_anchor_indexes": [0],
                    }
                ],
                "proposed_statement": "",
                "proposed_claim_kind": "question",
                "proposed_route_type": "question_answer",
                "rationale": "需要重新归属",
                "confidence": "high",
                "human_review_reason": "说话者归属风险",
            },
        ],
    }


def test_review_must_cover_every_claim_exactly_once() -> None:
    review = _review()
    review["claim_reviews"].pop()
    with pytest.raises(AIReviewValidationError, match="every claim exactly once"):
        validate_review_response(review, _survey())


def test_pass_cannot_hide_issues_or_proposals() -> None:
    review = _review()
    review["claim_reviews"][0]["proposed_statement"] = "偷偷修改"
    with pytest.raises(AIReviewValidationError, match="pass cannot propose"):
        validate_review_response(review, _survey())


def test_non_pass_requires_an_issue() -> None:
    review = _review()
    review["claim_reviews"][1]["issues"] = []
    with pytest.raises(AIReviewValidationError, match="must explain at least one issue"):
        validate_review_response(review, _survey())


def test_invalid_anchor_index_is_rejected() -> None:
    review = _review()
    review["claim_reviews"][1]["issues"][0]["affected_anchor_indexes"] = [5]
    with pytest.raises(AIReviewValidationError, match="invalid anchor index"):
        validate_review_response(review, _survey())


def test_high_risk_issue_routes_to_openai_before_human() -> None:
    review = _review()
    validate_review_response(review, _survey())
    routed = apply_risk_routing(
        review,
        reviewer_fingerprint_sha256="review-generation",
        spot_check_percent=0,
    )
    decisions = {item["claim_id"]: item for item in routed["claim_reviews"]}
    assert decisions["C001"]["routing_status"] == "ai_reviewed"
    assert decisions["C001"]["approval_status"] == "not_human_approved"
    assert decisions["C002"]["routing_status"] == "awaiting_openai_adjudication"


def test_spot_check_selection_is_deterministic() -> None:
    first = apply_risk_routing(
        _review(), reviewer_fingerprint_sha256="same", spot_check_percent=50
    )
    second = apply_risk_routing(
        _review(), reviewer_fingerprint_sha256="same", spot_check_percent=50
    )
    assert [item["spot_check_selected"] for item in first["claim_reviews"]] == [
        item["spot_check_selected"] for item in second["claim_reviews"]
    ]


def test_spot_check_samples_passed_claims_at_requested_rate() -> None:
    review = {
        "sermon_assessment": {"summary": "", "systemic_risks": []},
        "claim_reviews": [],
    }
    for index in range(19):
        review["claim_reviews"].append(
            {
                "claim_id": f"C{index:03d}",
                "decision": "pass",
                "issues": [],
                "proposed_statement": "",
                "proposed_claim_kind": "",
                "proposed_route_type": "unchanged",
                "rationale": "",
                "confidence": "high",
                "human_review_reason": "",
            }
        )

    routed = apply_risk_routing(
        review,
        reviewer_fingerprint_sha256="review-generation",
        spot_check_percent=10,
    )

    assert routed["routing_summary"]["human_spot_check"] == 2
    assert sum(item["spot_check_selected"] for item in routed["claim_reviews"]) == 2


def test_reviewer_fingerprint_changes_with_model_or_prompt() -> None:
    base = reviewer_fingerprint(
        source_extraction_fingerprint="extract-1",
        prompt="review-v1",
        model_id="claude-a",
        max_output_tokens=10000,
    )["fingerprint_sha256"]
    changed = reviewer_fingerprint(
        source_extraction_fingerprint="extract-1",
        prompt="review-v2",
        model_id="claude-a",
        max_output_tokens=10000,
    )["fingerprint_sha256"]
    assert base != changed


def test_subscription_reviewer_fingerprint_is_backend_bound_without_changing_api() -> None:
    kwargs = {
        "source_extraction_fingerprint": "extract-1", "prompt": "review-v1",
        "model_id": "claude-sonnet-5", "max_output_tokens": 64000,
    }
    api = reviewer_fingerprint(**kwargs)
    subscription = reviewer_fingerprint(**kwargs, backend="claude-subscription")
    assert "backend" not in api
    assert subscription["backend"] == "claude-subscription"
    assert subscription["fingerprint_sha256"] != api["fingerprint_sha256"]


def test_invalid_model_review_is_retried_with_validation_feedback() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def generate_json(self, _prompt, user_input, _schema, cache_prefix=None):
            # The stable source now rides in its own cached block; record the
            # rendered prompt so the assertions still see the full text.
            self.calls.append((cache_prefix or "") + user_input)
            review = _review()
            if len(self.calls) == 1:
                review["claim_reviews"].pop()
            return review

    client = FakeClient()
    result, usage = _generate_valid_review(
        client=client,
        prompt="review",
        user_input="source",
        survey=_survey(),
    )

    assert len(result["claim_reviews"]) == 2
    assert len(client.calls) == 2
    assert "未通过程序验证" in client.calls[1]
    # The rejected attempt was billed; a cost that counts only the accepted
    # call is the one number this review was run to produce.
    assert [row["attempt"] for row in usage] == [1, 2]


def test_curated_claim_layer_is_normalized_without_reextracting() -> None:
    package = {
        "claims": [
            {
                "claim_id": "CL-1",
                "title": "主张",
                "claim_type": "解經判斷",
                "scripture_refs": ["太17:1"],
                "review_status": "candidate",
                "occurrences": [
                    {
                        "transcript_id": "lecture-3",
                        "lecture": "第3講",
                        "anchors": [
                            {
                                "paragraph_key": "10",
                                "media_time": 12.5,
                                "evidence_id": "E1",
                                "proposed_highlight": {"text": "教授原话", "status": "proposed"},
                            }
                        ],
                    }
                ],
            }
        ],
        "claim_relations": [
            {
                "source_id": "CL-1",
                "target_id": "CL-2",
                "relation_type": "supports",
            }
        ],
    }

    normalized = _normalize_claim_layer(package)

    assert normalized["candidate_claims"][0]["statement"] == "主张"
    assert normalized["candidate_claims"][0]["relations"] == [
        {"type": "supports", "target_claim_id": "CL-2"}
    ]
    assert normalized["candidate_claims"][0]["anchors"][0]["verbatim_excerpt"] == "教授原话"


def test_existing_ai_review_is_archived_by_reviewer_generation(tmp_path) -> None:
    output = tmp_path / "review.json"
    output.write_text(
        '{"reviewer":{"fingerprint_sha256":"abcdef1234567890"}}',
        encoding="utf-8",
    )

    archive = _archive_existing_review(output)

    assert archive.parent == tmp_path / "review-generations"
    assert archive.name.startswith("review.abcdef123456.")
    assert archive.read_text(encoding="utf-8") == output.read_text(encoding="utf-8")


def test_rerunning_one_reviewer_does_not_overwrite_the_earlier_generation(tmp_path) -> None:
    """Two runs of the same reviewer are the comparison; keep both."""
    output = tmp_path / "review.json"
    output.write_text(
        '{"reviewer":{"fingerprint_sha256":"abcdef1234567890"},"run":1}',
        encoding="utf-8",
    )
    first = _archive_existing_review(output)
    output.write_text(
        '{"reviewer":{"fingerprint_sha256":"abcdef1234567890"},"run":2}',
        encoding="utf-8",
    )

    second = _archive_existing_review(output)

    assert first != second
    assert first.is_file() and second.is_file()


def test_duplicate_finding_must_name_another_claim_in_this_input() -> None:
    """The merge downstream reads the field, so prose alone is not a finding."""
    review = _review()
    review["claim_reviews"][1]["issues"] = [
        {
            "issue_type": "duplicate_claim",
            "severity": "medium",
            "explanation": "与另一条重复",
            "affected_anchor_indexes": [],
            "duplicate_of_claim_id": "",
        }
    ]

    with pytest.raises(AIReviewValidationError):
        validate_review_response(review, _survey())


def test_a_claim_cannot_duplicate_itself() -> None:
    review = _review()
    review["claim_reviews"][1]["issues"] = [
        {
            "issue_type": "duplicate_claim",
            "severity": "medium",
            "explanation": "与自己重复",
            "affected_anchor_indexes": [],
            "duplicate_of_claim_id": "C002",
        }
    ]

    with pytest.raises(AIReviewValidationError):
        validate_review_response(review, _survey())


def test_duplicate_target_is_accepted_when_it_names_a_sibling() -> None:
    review = _review()
    review["claim_reviews"][1]["issues"] = [
        {
            "issue_type": "duplicate_claim",
            "severity": "medium",
            "explanation": "与 C001 说的是同一件事",
            "affected_anchor_indexes": [],
            "duplicate_of_claim_id": "C001",
        }
    ]

    validate_review_response(review, _survey())


def test_only_a_duplicate_issue_may_carry_a_duplicate_target() -> None:
    review = _review()
    review["claim_reviews"][1]["issues"][0]["duplicate_of_claim_id"] = "C001"

    with pytest.raises(AIReviewValidationError):
        validate_review_response(review, _survey())


def test_a_duplicate_may_name_a_claim_another_batch_is_reviewing() -> None:
    """Which batch the twin fell into is a partition decision, not evidence."""
    survey = _survey()
    survey["other_batch_claims"] = [{"claim_id": "C099", "statement": "另一批次的同一结论"}]
    review = _review()
    review["claim_reviews"][1]["issues"] = [
        {
            "issue_type": "duplicate_claim",
            "severity": "medium",
            "explanation": "与 C099 说的是同一件事",
            "affected_anchor_indexes": [],
            "duplicate_of_claim_id": "C099",
        }
    ]

    validate_review_response(review, survey)


def test_a_duplicate_target_outside_the_package_is_still_refused() -> None:
    """Widening what may be pointed at must not accept an invented id."""
    survey = _survey()
    survey["other_batch_claims"] = [{"claim_id": "C099", "statement": "另一批次的同一结论"}]
    review = _review()
    review["claim_reviews"][1]["issues"] = [
        {
            "issue_type": "duplicate_claim",
            "severity": "medium",
            "explanation": "与某条重复",
            "affected_anchor_indexes": [],
            "duplicate_of_claim_id": "C404",
        }
    ]

    with pytest.raises(AIReviewValidationError):
        validate_review_response(review, survey)


def test_the_reviewer_is_not_shown_a_claim_a_merge_retired() -> None:
    """Re-reviewing a merged package must not re-open the resolved duplicate."""
    package = {
        "claim_relations": [],
        "claims": [
            {"claim_id": "CL-1", "title": "留下的那条", "occurrences": []},
            {"claim_id": "CL-2", "title": "被合并的那条", "occurrences": [], "superseded_by": "CL-1"},
        ],
    }

    normalized = _normalize_claim_layer(package)

    assert [row["claim_id"] for row in normalized["candidate_claims"]] == ["CL-1"]


def test_the_other_batches_claims_reach_the_reviewer_input() -> None:
    package = {
        "claim_relations": [],
        "claims": [{"claim_id": "CL-1", "title": "本批次", "occurrences": []}],
        "review_batch": {
            "other_batch_claims": [{"claim_id": "CL-9", "statement": "另一批次"}],
        },
    }

    normalized = _normalize_claim_layer(package)

    assert normalized["other_batch_claims"] == [
        {"claim_id": "CL-9", "statement": "另一批次"}
    ]
