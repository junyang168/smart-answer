"""Validated models for discovering and reviewing cross-sermon claim relations.

The input batch is a processing cohort, not a topic.  This module therefore
stores pairwise semantic comparisons without merging source claims or assigning
canonical topics.  Original claims remain the authoritative provenance units.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "wang_cross_sermon_relation_v1"
SCOPE = "cross_sermon_structure_no_theological_critique"
RELATION_TYPES = [
    "duplicate",
    "supports",
    "extends",
    "qualifies",
    "contrasts",
    "supersedes",
    "unrelated",
]
SYMMETRIC_RELATION_TYPES = {"duplicate", "contrasts", "unrelated"}
REVIEW_DECISIONS = ["pass", "change", "remove"]


DISCOVERY_SCHEMA: dict[str, Any] = {
    "name": "wang_cross_sermon_relation_discovery_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "relation_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "source_claim_id": {"type": "string"},
                        "target_claim_id": {"type": "string"},
                        "relation_type": {"type": "string", "enum": RELATION_TYPES},
                        "reason": {"type": "string"},
                        "source_evidence_step_ids": {
                            "type": "array", "items": {"type": "string"}
                        },
                        "target_evidence_step_ids": {
                            "type": "array", "items": {"type": "string"}
                        },
                        "confidence": {
                            "type": "string", "enum": ["high", "medium", "low"]
                        },
                    },
                    "required": [
                        "candidate_id", "source_claim_id", "target_claim_id",
                        "relation_type", "reason", "source_evidence_step_ids",
                        "target_evidence_step_ids", "confidence",
                    ],
                },
            },
            "unassigned_claim_ids": {"type": "array", "items": {"type": "string"}},
            "comparison_summary": {"type": "string"},
        },
        "required": [
            "scope_confirmation", "relation_candidates", "unassigned_claim_ids",
            "comparison_summary",
        ],
    },
}


REVIEW_SCHEMA: dict[str, Any] = {
    "name": "wang_cross_sermon_relation_review_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "relation_reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "decision": {"type": "string", "enum": REVIEW_DECISIONS},
                        "proposed_relation_type": {
                            "type": "string", "enum": [*RELATION_TYPES, "none"]
                        },
                        "reverse_direction": {"type": "boolean"},
                        "explanation": {"type": "string"},
                        "confidence": {
                            "type": "string", "enum": ["high", "medium", "low"]
                        },
                    },
                    "required": [
                        "candidate_id", "decision", "proposed_relation_type",
                        "reverse_direction", "explanation", "confidence",
                    ],
                },
            },
        },
        "required": ["scope_confirmation", "relation_reviews"],
    },
}


ADJUDICATION_SCHEMA: dict[str, Any] = {
    "name": "wang_cross_sermon_relation_adjudication_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "adjudications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["accept", "reject"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["candidate_id", "decision", "reason"],
                },
            },
        },
        "required": ["scope_confirmation", "adjudications"],
    },
}


RECONSIDERATION_SCHEMA: dict[str, Any] = {
    "name": "wang_cross_sermon_relation_reconsideration_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "reconsiderations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "decision": {
                            "type": "string", "enum": ["accept_openai", "reaffirm"]
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["candidate_id", "decision", "reason"],
                },
            },
        },
        "required": ["scope_confirmation", "reconsiderations"],
    },
}


class CrossSermonRelationValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CrossSermonRelationValidationError(message)


def _claim_sources(knowledge: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for claim in knowledge.get("claims", []):
        result[str(claim["claim_id"])] = {
            str(row.get("transcript_id") or "")
            for row in claim.get("occurrences", [])
            if row.get("transcript_id")
        }
    return result


def _canonical_pair(source_id: str, target_id: str, relation_type: str) -> tuple[str, str]:
    if relation_type in SYMMETRIC_RELATION_TYPES:
        return tuple(sorted((source_id, target_id)))  # type: ignore[return-value]
    return source_id, target_id


def _stable_candidate_id(source_id: str, target_id: str, relation_type: str) -> str:
    source_id, target_id = _canonical_pair(source_id, target_id, relation_type)
    digest = hashlib.sha256(
        f"{SCHEMA_VERSION}:{source_id}:{relation_type}:{target_id}".encode("utf-8")
    ).hexdigest()[:16]
    return f"XSR-{digest}"


def normalize_discovery(response: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize symmetric directions and replace model-local IDs."""
    normalized = deepcopy(response)
    for row in normalized.get("relation_candidates", []):
        source_id, target_id = _canonical_pair(
            str(row.get("source_claim_id") or ""),
            str(row.get("target_claim_id") or ""),
            str(row.get("relation_type") or ""),
        )
        if source_id != row.get("source_claim_id"):
            row["source_claim_id"], row["target_claim_id"] = source_id, target_id
            row["source_evidence_step_ids"], row["target_evidence_step_ids"] = (
                row.get("target_evidence_step_ids", []),
                row.get("source_evidence_step_ids", []),
            )
        row["candidate_id"] = _stable_candidate_id(
            source_id, target_id, str(row.get("relation_type") or "")
        )
    normalized["unassigned_claim_ids"] = sorted(
        set(normalized.get("unassigned_claim_ids") or [])
    )
    return normalized


def validate_discovery(response: dict[str, Any], knowledge: dict[str, Any]) -> None:
    _require(response.get("scope_confirmation") == SCOPE, "discovery scope not confirmed")
    claims = {str(row["claim_id"]): row for row in knowledge.get("claims", [])}
    evidence = {
        str(row["evidence_step_id"]): row for row in knowledge.get("evidence_steps", [])
    }
    sources = _claim_sources(knowledge)
    rows = response.get("relation_candidates") or []
    candidate_ids = [str(row.get("candidate_id") or "") for row in rows]
    _require(len(candidate_ids) == len(set(candidate_ids)), "duplicate candidate_id")
    pairs: set[tuple[str, str]] = set()
    endpoint_ids: set[str] = set()
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "")
        source_id = str(row.get("source_claim_id") or "")
        target_id = str(row.get("target_claim_id") or "")
        relation_type = str(row.get("relation_type") or "")
        _require(candidate_id.startswith("XSR-"), f"{candidate_id}: unstable candidate id")
        _require(source_id in claims and target_id in claims, f"{candidate_id}: unknown claim")
        _require(source_id != target_id, f"{candidate_id}: self relation")
        _require(relation_type in RELATION_TYPES, f"{candidate_id}: invalid relation type")
        _require(
            sources.get(source_id) and sources.get(target_id)
            and sources[source_id].isdisjoint(sources[target_id]),
            f"{candidate_id}: endpoints must come from different sermons",
        )
        pair = tuple(sorted((source_id, target_id)))
        _require(pair not in pairs, f"{candidate_id}: multiple judgments for one claim pair")
        pairs.add(pair)
        _require(bool(str(row.get("reason") or "").strip()), f"{candidate_id}: missing reason")
        for field, claim_id in (
            ("source_evidence_step_ids", source_id),
            ("target_evidence_step_ids", target_id),
        ):
            ids = row.get(field) or []
            _require(ids, f"{candidate_id}: {field} cannot be empty")
            _require(len(ids) == len(set(ids)), f"{candidate_id}: duplicate evidence id")
            allowed = set(claims[claim_id].get("evidence_step_ids") or [])
            _require(set(ids) <= allowed, f"{candidate_id}: evidence does not belong to endpoint")
            _require(set(ids) <= set(evidence), f"{candidate_id}: unknown evidence step")
        endpoint_ids.update((source_id, target_id))
    unassigned = response.get("unassigned_claim_ids") or []
    _require(len(unassigned) == len(set(unassigned)), "duplicate unassigned claim")
    _require(set(unassigned) <= set(claims), "unknown unassigned claim")
    _require(not (set(unassigned) & endpoint_ids), "claim cannot be related and unassigned")
    _require(
        endpoint_ids | set(unassigned) == set(claims),
        "every claim must be compared or explicitly unassigned",
    )


def validate_review(response: dict[str, Any], discovery: dict[str, Any]) -> None:
    _require(response.get("scope_confirmation") == SCOPE, "review scope not confirmed")
    candidates = {
        str(row["candidate_id"]): row for row in discovery.get("relation_candidates", [])
    }
    rows = response.get("relation_reviews") or []
    ids = [str(row.get("candidate_id") or "") for row in rows]
    _require(len(ids) == len(set(ids)) and set(ids) == set(candidates),
             "review must cover every relation candidate exactly once")
    for row in rows:
        candidate = candidates[str(row["candidate_id"])]
        decision = row.get("decision")
        proposed = row.get("proposed_relation_type")
        reverse = bool(row.get("reverse_direction"))
        _require(bool(str(row.get("explanation") or "").strip()), "review explanation required")
        if decision == "pass":
            _require(proposed == candidate["relation_type"], "pass must preserve relation type")
            _require(not reverse, "pass cannot reverse direction")
        elif decision == "change":
            _require(proposed in RELATION_TYPES, "change requires a relation type")
            _require(
                proposed != candidate["relation_type"] or reverse,
                "change must alter type or direction",
            )
            if candidate["relation_type"] in SYMMETRIC_RELATION_TYPES:
                _require(not reverse, "symmetric relation cannot be reversed")
        elif decision == "remove":
            _require(proposed == "none" and not reverse, "remove must propose none")
        else:
            raise CrossSermonRelationValidationError("invalid review decision")


def actionable_reviews(review: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in review.get("relation_reviews", []) if row.get("decision") != "pass"]


def validate_adjudication(response: dict[str, Any], review: dict[str, Any]) -> None:
    _require(response.get("scope_confirmation") == SCOPE, "adjudication scope not confirmed")
    expected = {str(row["candidate_id"]) for row in actionable_reviews(review)}
    rows = response.get("adjudications") or []
    ids = [str(row.get("candidate_id") or "") for row in rows]
    _require(len(ids) == len(set(ids)) and set(ids) == expected,
             "adjudication must cover every actionable review exactly once")


def validate_reconsideration(
    response: dict[str, Any], *, rejected_candidate_ids: set[str]
) -> None:
    _require(response.get("scope_confirmation") == SCOPE, "reconsideration scope not confirmed")
    rows = response.get("reconsiderations") or []
    ids = [str(row.get("candidate_id") or "") for row in rows]
    _require(len(ids) == len(set(ids)) and set(ids) == rejected_candidate_ids,
             "reconsideration must cover every rejected review exactly once")


def apply_consensus(
    discovery: dict[str, Any],
    review: dict[str, Any],
    adjudication: dict[str, Any],
    reconsideration: dict[str, Any] | None,
) -> dict[str, Any]:
    reviews = {str(row["candidate_id"]): row for row in review["relation_reviews"]}
    adjudications = {
        str(row["candidate_id"]): row for row in adjudication.get("adjudications", [])
    }
    reconsiderations = {
        str(row["candidate_id"]): row
        for row in (reconsideration or {}).get("reconsiderations", [])
    }
    reviewed_relations: list[dict[str, Any]] = []
    negative_comparisons: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    human_required = 0
    for candidate in discovery.get("relation_candidates", []):
        candidate_id = str(candidate["candidate_id"])
        first = reviews[candidate_id]
        final_type = str(candidate["relation_type"])
        reverse = False
        status = "ai_consensus"
        disposition = "keep"
        if first["decision"] != "pass":
            second = adjudications[candidate_id]
            if second["decision"] == "accept":
                final_type = str(first["proposed_relation_type"])
                reverse = bool(first["reverse_direction"])
                disposition = "remove" if first["decision"] == "remove" else "keep"
            else:
                third = reconsiderations[candidate_id]
                if third["decision"] == "accept_openai":
                    final_type = str(candidate["relation_type"])
                else:
                    status = "human_review_required"
                    disposition = "pending"
                    human_required += 1
        source_id = str(candidate["source_claim_id"])
        target_id = str(candidate["target_claim_id"])
        source_evidence = list(candidate["source_evidence_step_ids"])
        target_evidence = list(candidate["target_evidence_step_ids"])
        if reverse:
            source_id, target_id = target_id, source_id
            source_evidence, target_evidence = target_evidence, source_evidence
        final_row = {
            **candidate,
            "source_claim_id": source_id,
            "target_claim_id": target_id,
            "source_evidence_step_ids": source_evidence,
            "target_evidence_step_ids": target_evidence,
            "relation_type": final_type,
            "review_status": status,
        }
        if status == "ai_consensus" and disposition != "remove":
            if final_type == "unrelated":
                negative_comparisons.append(final_row)
            else:
                reviewed_relations.append(final_row)
        outcomes.append(
            {
                "candidate_id": candidate_id,
                "claude_decision": first["decision"],
                "openai_decision": (adjudications.get(candidate_id) or {}).get("decision"),
                "claude_reconsideration": (
                    reconsiderations.get(candidate_id) or {}
                ).get("decision"),
                "disposition": disposition,
                "final_relation_type": final_type if disposition != "remove" else "none",
                "status": status,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "reviewed_relations": reviewed_relations,
        "negative_comparisons": negative_comparisons,
        "unassigned_claim_ids": discovery.get("unassigned_claim_ids", []),
        "outcomes": outcomes,
        "summary": {
            "candidate_relations": len(discovery.get("relation_candidates", [])),
            "reviewed_relations": len(reviewed_relations),
            "negative_comparisons": len(negative_comparisons),
            "removed_candidates": sum(row["disposition"] == "remove" for row in outcomes),
            "human_review_required": human_required,
            "unassigned_claims": len(discovery.get("unassigned_claim_ids", [])),
        },
    }


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
