from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


REVIEW_VERSION = "wang_composition_independent_review_v1"
ADJUDICATION_VERSION = "wang_composition_ai_adjudication_v2"

ISSUE_TYPES = [
    "claim_misassigned",
    "claim_omitted",
    "duplicate_coverage",
    "wrong_product_axis",
    "over_expansion",
    "under_expansion",
    "insufficient_evidence",
    "missing_claim_relation",
    "unsupported_editorial_bridge",
    "source_maturity_mismatch",
    "passage_order",
    "coverage_gap",
    "other",
]
FINDING_TYPES = [
    "missing_claim",
    "weak_claim",
    "missing_relation",
    "weak_evidence",
    "unrouted_material",
    "conflicting_product_role",
    "none",
]
SEVERITIES = ["low", "medium", "high", "critical"]
DECISIONS = ["pass", "changes_suggested", "human_review_required"]
CONFIDENCE = ["high", "medium", "low"]
ARGUMENT_LAYER_STATUS = ["solid", "usable_with_gaps", "not_solid"]
COMPOSITION_ACTIONS = [
    "",
    "background_appendix",
    "brief_note",
    "coverage_gap",
    "main_section",
    "main_with_topic_link",
    "thought_development_check",
    "topic_link",
    "topic_main_section",
    "topic_section_pending_scope",
]

COMPOSITION_REVIEW_SCHEMA: dict[str, Any] = {
    "name": REVIEW_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {
                "type": "string",
                "enum": ["composition_and_argument_structure_no_theological_critique"],
            },
            "plan_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string"},
                    "argument_layer_status": {
                        "type": "string",
                        "enum": ARGUMENT_LAYER_STATUS,
                    },
                    "argument_layer_findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "finding_type": {"type": "string", "enum": FINDING_TYPES},
                                "severity": {"type": "string", "enum": SEVERITIES},
                                "explanation": {"type": "string"},
                                "claim_ids": {"type": "array", "items": {"type": "string"}},
                                "relation_ids": {"type": "array", "items": {"type": "string"}},
                                "recommended_action": {"type": "string"},
                            },
                            "required": [
                                "finding_type",
                                "severity",
                                "explanation",
                                "claim_ids",
                                "relation_ids",
                                "recommended_action",
                            ],
                        },
                    },
                    "systemic_risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "summary",
                    "argument_layer_status",
                    "argument_layer_findings",
                    "systemic_risks",
                ],
            },
            "decision_reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "decision_id": {"type": "string"},
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
                                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["issue_type", "severity", "explanation", "claim_ids"],
                            },
                        },
                        "proposed_action": {"type": "string"},
                        "proposed_decision_text": {"type": "string"},
                        "proposed_rationale": {"type": "string"},
                        "proposed_add_claim_ids": {"type": "array", "items": {"type": "string"}},
                        "proposed_remove_claim_ids": {"type": "array", "items": {"type": "string"}},
                        "proposed_coverage": {"type": "string"},
                        "proposed_editorial_boundary": {
                            "type": "string",
                            "enum": ["", "required", "withdrawn"],
                        },
                        "rationale": {"type": "string"},
                        "confidence": {"type": "string", "enum": CONFIDENCE},
                        "human_review_reason": {"type": "string"},
                    },
                    "required": [
                        "decision_id",
                        "decision",
                        "issues",
                        "proposed_action",
                        "proposed_decision_text",
                        "proposed_rationale",
                        "proposed_add_claim_ids",
                        "proposed_remove_claim_ids",
                        "proposed_coverage",
                        "proposed_editorial_boundary",
                        "rationale",
                        "confidence",
                        "human_review_reason",
                    ],
                },
            },
        },
        "required": ["scope_confirmation", "plan_assessment", "decision_reviews"],
    },
}

PATCH_PROPERTIES = {
    "action": {"type": "string", "enum": COMPOSITION_ACTIONS},
    "decision_text": {"type": "string"},
    "rationale": {"type": "string"},
    "add_claim_ids": {"type": "array", "items": {"type": "string"}},
    "remove_claim_ids": {"type": "array", "items": {"type": "string"}},
    "coverage": {"type": "string"},
    # `action`, `coverage` and this are one state, not three: the audit's
    # `declared_coverage_gap` only exempts a claimless decision when all three
    # agree. Without this the review could promote a coverage gap to a real
    # section and route material into it, but not withdraw the note ordering
    # the author to declare that no material exists.
    #
    # A string, not a boolean, because the schema is strict and every patch
    # field is required: a boolean has no value meaning "leave this alone",
    # so every accepted patch would restate it. "" is no change, as elsewhere.
    "editorial_boundary": {"type": "string", "enum": ["", "required", "withdrawn"]},
    "topic_plan_ids": {"type": "array", "items": {"type": "string"}},
    "claim_hierarchy": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "paragraph_thesis": {"type": "string"},
            "supporting_claims": {"type": "array", "items": {"type": "string"}},
            "corroborating_claims": {"type": "array", "items": {"type": "string"}},
            "supporting_context": {"type": "array", "items": {"type": "string"}},
            "parallel_context": {"type": "array", "items": {"type": "string"}},
            "methodological_entry": {"type": "string"},
            "theological_structure": {"type": "array", "items": {"type": "string"}},
            "original_language_support": {"type": "string"},
            "evidence_step_scopes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_id": {"type": "string"},
                        "evidence_step_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["claim_id", "evidence_step_ids"],
                },
            },
            "note": {"type": "string"},
        },
        "required": [
            "paragraph_thesis",
            "supporting_claims",
            "corroborating_claims",
            "supporting_context",
            "parallel_context",
            "methodological_entry",
            "theological_structure",
            "original_language_support",
            "evidence_step_scopes",
            "note",
        ],
    },
    "argument_layer_followups": {"type": "array", "items": {"type": "string"}},
}

COMPOSITION_ADJUDICATION_SCHEMA: dict[str, Any] = {
    "name": ADJUDICATION_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {
                "type": "string",
                "enum": ["composition_and_argument_structure_no_theological_critique"],
            },
            "adjudications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "decision_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["accept", "reject"]},
                        "rationale": {"type": "string"},
                        "patch": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": PATCH_PROPERTIES,
                            "required": list(PATCH_PROPERTIES),
                        },
                    },
                    "required": ["decision_id", "decision", "rationale", "patch"],
                },
            },
        },
        "required": ["scope_confirmation", "adjudications"],
    },
}

COMPOSITION_RECONSIDERATION_SCHEMA: dict[str, Any] = {
    "name": "wang_composition_claude_reconsideration_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {
                "type": "string",
                "enum": ["composition_and_argument_structure_no_theological_critique"],
            },
            "reconsiderations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "decision_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["withdraw", "maintain"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["decision_id", "decision", "rationale"],
                },
            },
        },
        "required": ["scope_confirmation", "reconsiderations"],
    },
}


class CompositionReviewValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CompositionReviewValidationError(message)


def _patch_empty(patch: dict[str, Any]) -> bool:
    for key, value in patch.items():
        if key == "claim_hierarchy":
            if any((value or {}).values()):
                return False
            continue
        if value:
            return False
    return True


def validate_review(response: dict[str, Any], plan: dict[str, Any], claim_ids: set[str]) -> None:
    _require(
        response.get("scope_confirmation")
        == "composition_and_argument_structure_no_theological_critique",
        "reviewer must confirm composition-only scope",
    )
    decisions = {item["decision_id"]: item for item in plan.get("decisions", [])}
    rows = response.get("decision_reviews") or []
    row_ids = [item.get("decision_id") for item in rows]
    _require(len(row_ids) == len(set(row_ids)), "duplicate decision review")
    _require(set(row_ids) == set(decisions), "review must cover every composition decision")
    for row in rows:
        decision_id = row["decision_id"]
        _require(row.get("decision") in DECISIONS, f"{decision_id}: invalid decision")
        issues = row.get("issues") or []
        proposals = [
            row.get("proposed_action"),
            row.get("proposed_decision_text"),
            row.get("proposed_rationale"),
            row.get("proposed_add_claim_ids"),
            row.get("proposed_remove_claim_ids"),
            row.get("proposed_coverage"),
            row.get("proposed_editorial_boundary"),
        ]
        if row["decision"] == "pass":
            _require(not issues, f"{decision_id}: pass cannot contain issues")
            _require(not any(proposals), f"{decision_id}: pass cannot propose changes")
        else:
            _require(bool(issues), f"{decision_id}: non-pass review needs issues")
        if row["decision"] == "human_review_required":
            _require(bool(row.get("human_review_reason", "").strip()), f"{decision_id}: explain human need")
        for claim_id in [
            *row.get("proposed_add_claim_ids", []),
            *row.get("proposed_remove_claim_ids", []),
        ]:
            _require(claim_id in claim_ids, f"{decision_id}: unknown claim {claim_id}")
        for issue in issues:
            _require(issue.get("issue_type") in ISSUE_TYPES, f"{decision_id}: invalid issue type")
            _require(set(issue.get("claim_ids", [])) <= claim_ids, f"{decision_id}: issue cites unknown claim")

    assessment = response.get("plan_assessment") or {}
    _require(assessment.get("argument_layer_status") in ARGUMENT_LAYER_STATUS, "invalid argument layer status")
    for finding in assessment.get("argument_layer_findings", []):
        _require(finding.get("finding_type") in FINDING_TYPES, "invalid argument finding")
        _require(set(finding.get("claim_ids", [])) <= claim_ids, "argument finding cites unknown claim")


def validate_adjudication(
    response: dict[str, Any],
    actionable_reviews: list[dict[str, Any]],
    claim_ids: set[str],
) -> None:
    _require(
        response.get("scope_confirmation")
        == "composition_and_argument_structure_no_theological_critique",
        "adjudicator must confirm composition-only scope",
    )
    expected = {item["decision_id"] for item in actionable_reviews}
    rows = response.get("adjudications") or []
    ids = [item.get("decision_id") for item in rows]
    _require(len(ids) == len(set(ids)), "duplicate adjudication")
    _require(set(ids) == expected, "adjudication must cover every actionable review")
    for row in rows:
        patch = row.get("patch") or {}
        _require(patch.get("action", "") in COMPOSITION_ACTIONS, f"{row['decision_id']}: invalid action")
        hierarchy = patch.get("claim_hierarchy") or {}
        hierarchy_claim_ids = [
            hierarchy.get("paragraph_thesis"),
            hierarchy.get("methodological_entry"),
            hierarchy.get("original_language_support"),
            *hierarchy.get("supporting_claims", []),
            *hierarchy.get("corroborating_claims", []),
            *hierarchy.get("supporting_context", []),
            *hierarchy.get("parallel_context", []),
            *hierarchy.get("theological_structure", []),
            *(
                scope.get("claim_id")
                for scope in hierarchy.get("evidence_step_scopes", [])
            ),
        ]
        for claim_id in [
            *patch.get("add_claim_ids", []),
            *patch.get("remove_claim_ids", []),
            *(item for item in hierarchy_claim_ids if item),
        ]:
            _require(claim_id in claim_ids, f"{row['decision_id']}: patch cites unknown claim")
        if row.get("decision") == "accept":
            _require(not _patch_empty(patch), f"{row['decision_id']}: accepted review needs patch")
        else:
            _require(_patch_empty(patch), f"{row['decision_id']}: rejected review cannot patch")


def validate_reconsideration(response: dict[str, Any], rejected_ids: set[str]) -> None:
    _require(
        response.get("scope_confirmation")
        == "composition_and_argument_structure_no_theological_critique",
        "reconsideration must confirm composition-only scope",
    )
    rows = response.get("reconsiderations") or []
    ids = [item.get("decision_id") for item in rows]
    _require(len(ids) == len(set(ids)), "duplicate reconsideration")
    _require(set(ids) == rejected_ids, "reconsideration must cover every rejection")


def review_fingerprint(*, plan_bytes: bytes, knowledge_bytes: bytes, prompt: str, model: str) -> dict[str, str]:
    identity = {
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "knowledge_sha256": hashlib.sha256(knowledge_bytes).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "model": model,
        "schema_version": REVIEW_VERSION,
    }
    identity["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return identity


def apply_consensus(
    plan: dict[str, Any],
    adjudication: dict[str, Any],
    reconsideration: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = deepcopy(plan)
    decisions = {item["decision_id"]: item for item in result.get("decisions", [])}
    reconsidered = {
        item["decision_id"]: item
        for item in (reconsideration or {}).get("reconsiderations", [])
    }
    outcomes = []
    for row in adjudication.get("adjudications", []):
        decision_id = row["decision_id"]
        if row["decision"] == "accept":
            patch = row["patch"]
            target = decisions[decision_id]
            if patch.get("action"):
                target["action"] = patch["action"]
            if patch.get("decision_text"):
                target["decision"] = patch["decision_text"]
            if patch.get("rationale"):
                target["rationale"] = patch["rationale"]
            if patch.get("coverage"):
                target["coverage"] = patch["coverage"]
            if patch.get("editorial_boundary") == "withdrawn":
                # Drop the whole boundary rather than leaving `required: false`
                # behind: the audit reads `editorial_boundary.required`, and a
                # decision that no longer needs an editorial note has nothing
                # left to say about one.
                target.pop("editorial_boundary", None)
            elif patch.get("editorial_boundary") == "required":
                target.setdefault("editorial_boundary", {})["required"] = True
            if patch.get("topic_plan_ids"):
                target["topic_plan_ids"] = patch["topic_plan_ids"]
            if patch.get("claim_hierarchy") and any(patch["claim_hierarchy"].values()):
                target["claim_hierarchy"] = {
                    key: value
                    for key, value in patch["claim_hierarchy"].items()
                    if value
                }
            claim_ids = [
                item for item in target.get("claim_ids", [])
                if item not in set(patch.get("remove_claim_ids", []))
            ]
            for claim_id in patch.get("add_claim_ids", []):
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)
            target["claim_ids"] = claim_ids
            if patch.get("argument_layer_followups"):
                followups = target.setdefault("argument_layer_followups", [])
                followups.extend(
                    item for item in patch["argument_layer_followups"]
                    if item not in followups
                )
            outcomes.append({"decision_id": decision_id, "status": "auto_applied"})
            continue
        reconsidered_row = reconsidered.get(decision_id)
        status = "withdrawn" if reconsidered_row and reconsidered_row["decision"] == "withdraw" else "human_disagreement_required"
        outcomes.append({"decision_id": decision_id, "status": status})
    result["ai_composition_consensus"] = {
        "approval_status": "not_human_approved",
        "outcomes": outcomes,
    }
    return result, {"outcomes": outcomes}
