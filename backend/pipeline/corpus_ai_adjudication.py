from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


ADJUDICATION_VERSION = "wang_corpus_ai_adjudication_v1"
RECONSIDERATION_VERSION = "wang_corpus_claude_reconsideration_v1"
ROUTE_TYPES = [
    "scripture_exposition",
    "topic_research",
    "question_answer",
    "method_research",
    "thought_development",
    "defer",
    "unchanged",
]


PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "statement": {"type": "string"},
        "claim_kind": {"type": "string"},
        "route_type": {"type": "string", "enum": ROUTE_TYPES},
        "scripture_refs": {"type": "array", "items": {"type": "string"}},
        "excluded_anchor_indexes": {"type": "array", "items": {"type": "integer"}},
        "excluded_claim_relation_ids": {"type": "array", "items": {"type": "string"}},
        "anchor_additions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "transcript_id": {"type": "string"},
                    "source_index": {"type": "string"},
                    "verbatim_excerpt": {"type": "string"},
                    "evidence_type": {"type": "string"},
                },
                "required": [
                    "transcript_id",
                    "source_index",
                    "verbatim_excerpt",
                    "evidence_type",
                ],
            },
        },
        # The one executable answer to "these two say the same thing".  The
        # loser is not deleted: it is marked superseded and its anchors move to
        # the survivor, so merging can never cost source coverage.
        "superseded_by_claim_id": {"type": "string"},
        "structural_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "statement",
        "claim_kind",
        "route_type",
        "scripture_refs",
        "excluded_anchor_indexes",
        "excluded_claim_relation_ids",
        "anchor_additions",
        "superseded_by_claim_id",
        "structural_notes",
    ],
}


OPENAI_ADJUDICATION_SCHEMA: dict[str, Any] = {
    "name": ADJUDICATION_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {
                "type": "string",
                "enum": ["source_fidelity_only_no_theological_critique"],
            },
            "adjudications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["accept", "reject"]},
                        "rationale": {"type": "string"},
                        "source_anchor_indexes": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                        "patch": PATCH_SCHEMA,
                    },
                    "required": [
                        "claim_id",
                        "decision",
                        "rationale",
                        "source_anchor_indexes",
                        "patch",
                    ],
                },
            },
        },
        "required": ["scope_confirmation", "adjudications"],
    },
}


CLAUDE_RECONSIDERATION_SCHEMA: dict[str, Any] = {
    "name": RECONSIDERATION_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {
                "type": "string",
                "enum": ["source_fidelity_only_no_theological_critique"],
            },
            "reconsiderations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["withdraw", "maintain"]},
                        "rationale": {"type": "string"},
                        "source_anchor_indexes": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                    "required": [
                        "claim_id",
                        "decision",
                        "rationale",
                        "source_anchor_indexes",
                    ],
                },
            },
        },
        "required": ["scope_confirmation", "reconsiderations"],
    },
}


class AIAdjudicationValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AIAdjudicationValidationError(message)


def actionable_reviews(review_artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in review_artifact.get("claim_reviews", [])
        if item.get("decision") != "pass"
    ]


def _empty_patch(patch: dict[str, Any]) -> bool:
    return not any(
        [
            str(patch.get("statement") or "").strip(),
            str(patch.get("claim_kind") or "").strip(),
            patch.get("route_type") not in {None, "", "unchanged"},
            patch.get("scripture_refs"),
            patch.get("excluded_anchor_indexes"),
            patch.get("excluded_claim_relation_ids"),
            patch.get("anchor_additions"),
            str(patch.get("superseded_by_claim_id") or "").strip(),
        ]
    )


def validate_openai_adjudication(
    response: dict[str, Any],
    *,
    reviews: list[dict[str, Any]],
    claims_by_id: dict[str, dict[str, Any]],
    transcript_segments: dict[str, dict[str, str]],
) -> None:
    _require(
        response.get("scope_confirmation")
        == "source_fidelity_only_no_theological_critique",
        "OpenAI must confirm source-fidelity-only scope",
    )
    expected = {item["claim_id"] for item in reviews}
    reviews_by_id = {item["claim_id"]: item for item in reviews}
    rows = response.get("adjudications") or []
    ids = [item.get("claim_id") for item in rows]
    _require(len(ids) == len(set(ids)), "duplicate OpenAI adjudication")
    _require(set(ids) == expected, "OpenAI must adjudicate every actionable Claude review")
    for row in rows:
        claim_id = row["claim_id"]
        claim = claims_by_id[claim_id]
        anchor_count = len(claim.get("anchors", []))
        patch = row.get("patch") or {}
        issue_types = {
            issue.get("issue_type")
            for issue in reviews_by_id[claim_id].get("issues", [])
        }
        affected_anchor_indexes = {
            index
            for issue in reviews_by_id[claim_id].get("issues", [])
            for index in issue.get("affected_anchor_indexes", [])
        }
        _require(
            patch.get("route_type") in {None, "", "unchanged"}
            or "route_error" in issue_types,
            f"{claim_id}: fidelity adjudication cannot change product route without route_error",
        )
        for index in row.get("source_anchor_indexes", []):
            _require(0 <= index < anchor_count, f"{claim_id}: invalid source anchor index {index}")
        for index in patch.get("excluded_anchor_indexes", []):
            _require(0 <= index < anchor_count, f"{claim_id}: invalid excluded anchor index {index}")
            _require(
                index in affected_anchor_indexes,
                f"{claim_id}: excluded anchor index was not identified by Claude",
            )
        for addition in patch.get("anchor_additions", []):
            transcript_id = addition.get("transcript_id")
            source_index = str(addition.get("source_index") or "")
            excerpt = str(addition.get("verbatim_excerpt") or "")
            source_text = transcript_segments.get(str(transcript_id), {}).get(source_index)
            _require(source_text is not None, f"{claim_id}: added anchor source does not exist")
            _require(excerpt and excerpt in source_text, f"{claim_id}: added anchor is not verbatim")
        outgoing_relation_ids = {
            str(item.get("relation_id") or "")
            for item in claim.get("relations", [])
            if item.get("relation_id")
        }
        for relation_id in patch.get("excluded_claim_relation_ids", []):
            _require(
                relation_id in outgoing_relation_ids,
                f"{claim_id}: cannot exclude relation outside this claim: {relation_id}",
            )
        superseded_by = str(patch.get("superseded_by_claim_id") or "").strip()
        if superseded_by:
            duplicate_targets = {
                str(issue.get("duplicate_of_claim_id") or "").strip()
                for issue in reviews_by_id[claim_id].get("issues", [])
                if issue.get("issue_type") == "duplicate_claim"
            }
            _require(
                superseded_by in duplicate_targets,
                f"{claim_id}: cannot merge into a claim Claude did not name as the duplicate",
            )
            _require(superseded_by in claims_by_id, f"{claim_id}: unknown merge target {superseded_by}")
            _require(superseded_by != claim_id, f"{claim_id}: cannot supersede itself")
            # A merge is the whole patch.  Editing a claim and then retiring it
            # leaves an override whose effect nobody can read off the artifact.
            _require(
                not any([
                    str(patch.get("statement") or "").strip(),
                    str(patch.get("claim_kind") or "").strip(),
                    patch.get("route_type") not in {None, "", "unchanged"},
                    patch.get("scripture_refs"),
                    patch.get("excluded_anchor_indexes"),
                    patch.get("anchor_additions"),
                ]),
                f"{claim_id}: a merge patch cannot also rewrite the claim it retires",
            )
        if row.get("decision") == "accept":
            _require(not _empty_patch(patch), f"{claim_id}: accepted review requires executable patch")
        else:
            _require(_empty_patch(patch), f"{claim_id}: rejected review cannot carry patch")
    merged = {
        str(row["claim_id"]): str(row["patch"].get("superseded_by_claim_id") or "").strip()
        for row in rows
        if str((row.get("patch") or {}).get("superseded_by_claim_id") or "").strip()
    }
    for claim_id, target in merged.items():
        # One hop only.  A chain would make the surviving claim depend on the
        # order the overrides happen to be applied in.
        _require(
            target not in merged,
            f"{claim_id}: merge target {target} is itself being merged away",
        )


def validate_claude_reconsideration(
    response: dict[str, Any],
    *,
    rejected_claim_ids: set[str],
    claims_by_id: dict[str, dict[str, Any]],
) -> None:
    _require(
        response.get("scope_confirmation")
        == "source_fidelity_only_no_theological_critique",
        "Claude must confirm source-fidelity-only scope",
    )
    rows = response.get("reconsiderations") or []
    ids = [item.get("claim_id") for item in rows]
    _require(len(ids) == len(set(ids)), "duplicate Claude reconsideration")
    _require(set(ids) == rejected_claim_ids, "Claude must reconsider every OpenAI rejection")
    for row in rows:
        claim_id = row["claim_id"]
        anchor_count = len(claims_by_id[claim_id].get("anchors", []))
        for index in row.get("source_anchor_indexes", []):
            _require(0 <= index < anchor_count, f"{claim_id}: invalid reconsideration anchor index")


def adjudication_fingerprint(
    *,
    review_fingerprint: str,
    openai_prompt: str,
    openai_model: str,
    openai_reasoning_effort: str,
    claude_prompt: str,
    claude_model: str,
) -> dict[str, str]:
    identity = {
        "review_fingerprint": review_fingerprint,
        "openai_prompt_sha256": hashlib.sha256(openai_prompt.encode()).hexdigest(),
        "openai_model": openai_model,
        "openai_reasoning_effort": openai_reasoning_effort,
        "claude_reconsideration_prompt_sha256": hashlib.sha256(claude_prompt.encode()).hexdigest(),
        "claude_model": claude_model,
        "schema_version": ADJUDICATION_VERSION,
    }
    identity["fingerprint_sha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return identity


def compile_outcome(
    openai_response: dict[str, Any],
    claude_reconsideration: dict[str, Any] | None,
    reviews: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reconsidered = {
        item["claim_id"]: item
        for item in (claude_reconsideration or {}).get("reconsiderations", [])
    }
    reviews_by_id = {item["claim_id"]: item for item in (reviews or [])}
    results: list[dict[str, Any]] = []
    overrides: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    counts = {
        "auto_applied": 0,
        "withdrawn": 0,
        "human_confirmation_required": 0,
        "human_disagreement_required": 0,
    }
    for row in openai_response["adjudications"]:
        claim_id = row["claim_id"]
        review = reviews_by_id.get(claim_id, {})
        if row["decision"] == "accept":
            if review.get("decision") == "human_review_required":
                # Claude judged that the source itself cannot settle this claim.
                # Two models agreeing on the repair does not remove the reason a
                # person was asked for, so the patch waits instead of applying.
                status = "human_confirmation_required"
                pending[claim_id] = deepcopy(row["patch"])
            else:
                status = "auto_applied"
                overrides[claim_id] = deepcopy(row["patch"])
        else:
            reconsideration = reconsidered[claim_id]
            if reconsideration["decision"] == "withdraw":
                status = "withdrawn"
            else:
                status = "human_disagreement_required"
        counts[status] += 1
        results.append(
            {
                "claim_id": claim_id,
                "status": status,
                "claude_decision": review.get("decision"),
                "human_review_reason": review.get("human_review_reason", ""),
                "openai": row,
                "claude_reconsideration": reconsidered.get(claim_id),
                "approval_status": "not_human_approved",
            }
        )
    return {
        "results": results,
        "claim_overrides": overrides,
        "pending_patches": pending,
        "summary": counts,
    }
