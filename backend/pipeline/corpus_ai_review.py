from __future__ import annotations

import hashlib
import json
import math
from typing import Any


AI_REVIEW_VERSION = "wang_corpus_independent_review_v1"

ISSUE_TYPES = [
    "speaker_attribution",
    "opponent_as_professor",
    "audience_as_evidence",
    "unsupported_claim",
    "insufficient_anchor",
    "missing_qualification",
    "missing_scripture_evidence",
    "claim_too_broad",
    "claim_should_split",
    "duplicate_claim",
    "relation_error",
    "route_error",
    "editorial_inference",
    "unresolved_tension",
    "other",
]
DECISIONS = ["pass", "changes_suggested", "human_review_required"]
SEVERITIES = ["low", "medium", "high", "critical"]
CONFIDENCE = ["high", "medium", "low"]
ROUTE_TYPES = [
    "scripture_exposition",
    "topic_research",
    "question_answer",
    "method_research",
    "thought_development",
    "defer",
    "unchanged",
]

AI_REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "wang_corpus_independent_review_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sermon_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string"},
                    "systemic_risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "systemic_risks"],
            },
            "claim_reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_id": {"type": "string"},
                        "decision": {"type": "string", "enum": DECISIONS},
                        "issues": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "issue_type": {"type": "string", "enum": ISSUE_TYPES},
                                    "severity": {"type": "string", "enum": SEVERITIES},
                                    "explanation": {"type": "string"},
                                    "affected_anchor_indexes": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                    },
                                },
                                "required": [
                                    "issue_type",
                                    "severity",
                                    "explanation",
                                    "affected_anchor_indexes",
                                ],
                            },
                        },
                        "proposed_statement": {"type": "string"},
                        "proposed_claim_kind": {"type": "string"},
                        "proposed_route_type": {"type": "string", "enum": ROUTE_TYPES},
                        "rationale": {"type": "string"},
                        "confidence": {"type": "string", "enum": CONFIDENCE},
                        "human_review_reason": {"type": "string"},
                    },
                    "required": [
                        "claim_id",
                        "decision",
                        "issues",
                        "proposed_statement",
                        "proposed_claim_kind",
                        "proposed_route_type",
                        "rationale",
                        "confidence",
                        "human_review_reason",
                    ],
                },
            },
        },
        "required": ["sermon_assessment", "claim_reviews"],
    },
}


class AIReviewValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AIReviewValidationError(message)


def reviewer_fingerprint(
    *,
    source_extraction_fingerprint: str,
    prompt: str,
    model_id: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    identity = {
        "source_extraction_fingerprint": source_extraction_fingerprint,
        "review_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "review_model_id": model_id,
        "max_output_tokens": max_output_tokens,
        "review_schema_version": AI_REVIEW_VERSION,
        "response_schema_sha256": hashlib.sha256(
            json.dumps(
                AI_REVIEW_RESPONSE_SCHEMA,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    }
    return {
        **identity,
        "fingerprint_sha256": hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def validate_review_response(
    response: dict[str, Any],
    survey: dict[str, Any],
) -> None:
    claims = {item["claim_id"]: item for item in survey.get("candidate_claims", [])}
    reviews = response.get("claim_reviews")
    _require(isinstance(reviews, list), "claim_reviews must be a list")
    review_ids = [item.get("claim_id") for item in reviews]
    _require(len(review_ids) == len(set(review_ids)), "duplicate claim review")
    _require(set(review_ids) == set(claims), "review must cover every claim exactly once")
    for review in reviews:
        claim_id = review["claim_id"]
        decision = review.get("decision")
        _require(decision in DECISIONS, f"{claim_id}: invalid decision")
        issues = review.get("issues") or []
        if decision == "pass":
            _require(not issues, f"{claim_id}: pass cannot contain issues")
            _require(not review.get("proposed_statement"), f"{claim_id}: pass cannot propose statement")
            _require(not review.get("proposed_claim_kind"), f"{claim_id}: pass cannot propose claim kind")
            _require(
                review.get("proposed_route_type") == "unchanged",
                f"{claim_id}: pass route must be unchanged",
            )
        else:
            _require(issues, f"{claim_id}: non-pass decision must explain at least one issue")
        if decision == "human_review_required":
            _require(
                bool(str(review.get("human_review_reason") or "").strip()),
                f"{claim_id}: human review decision must explain why",
            )
        anchor_count = len(claims[claim_id].get("anchors", []))
        for issue in issues:
            _require(issue.get("issue_type") in ISSUE_TYPES, f"{claim_id}: invalid issue type")
            _require(issue.get("severity") in SEVERITIES, f"{claim_id}: invalid severity")
            for index in issue.get("affected_anchor_indexes", []):
                _require(
                    isinstance(index, int) and 0 <= index < anchor_count,
                    f"{claim_id}: invalid anchor index {index}",
                )


def apply_risk_routing(
    response: dict[str, Any],
    *,
    reviewer_fingerprint_sha256: str,
    spot_check_percent: int = 10,
) -> dict[str, Any]:
    routed = json.loads(json.dumps(response, ensure_ascii=False))
    counts = {
        "ai_reviewed": 0,
        "awaiting_openai_adjudication": 0,
        "human_spot_check": 0,
    }
    passed_claim_ids = [
        review["claim_id"]
        for review in routed["claim_reviews"]
        if review.get("decision") == "pass"
    ]
    sample_size = (
        min(
            len(passed_claim_ids),
            math.ceil(len(passed_claim_ids) * spot_check_percent / 100),
        )
        if spot_check_percent > 0
        else 0
    )
    spot_check_claim_ids = set(
        sorted(
            passed_claim_ids,
            key=lambda claim_id: hashlib.sha256(
                f"{reviewer_fingerprint_sha256}:{claim_id}".encode("utf-8")
            ).hexdigest(),
        )[:sample_size]
    )
    for review in routed["claim_reviews"]:
        spot_check = review["claim_id"] in spot_check_claim_ids
        if review["decision"] != "pass":
            # Claude never sends an item directly to a person.  All non-pass
            # findings, including high-risk attribution findings, first go to
            # OpenAI adjudication and then one Claude reconsideration.
            routing = "awaiting_openai_adjudication"
        elif spot_check:
            routing = "human_spot_check"
        else:
            routing = "ai_reviewed"
        review["reviewer_fingerprint"] = reviewer_fingerprint_sha256
        review["spot_check_selected"] = spot_check
        review["routing_status"] = routing
        review["approval_status"] = "not_human_approved"
        counts[routing] += 1
    routed["routing_summary"] = counts
    return routed
