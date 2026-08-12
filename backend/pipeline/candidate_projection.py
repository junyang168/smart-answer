"""Build reviewable scripture/topic product candidates from shared claims.

The research batch is only a processing cohort.  Candidate plans are derived
after independent extraction and cross-sermon comparison; they never mutate or
merge the source claims.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from backend.api.sermon_search.bible_refs import normalize_ref


SCHEMA_VERSION = "wang_candidate_projection_v1"
SCOPE = "product_candidate_structure_no_theological_critique"


SECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "section_title": {"type": "string"},
        "arrangement": {
            "type": "string",
            "enum": [
                "main_section", "brief_note", "background", "application",
                "appendix", "coverage_gap",
            ],
        },
        "reason": {"type": "string"},
        "claim_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["section_title", "arrangement", "reason", "claim_ids"],
}

CANDIDATE_SCHEMA: dict[str, Any] = {
    "name": "wang_candidate_projection_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "candidate_plans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "axis": {"type": "string", "enum": ["scripture", "topic"]},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "canonical_topic_id": {"type": ["string", "null"]},
                        "scripture_target_id": {"type": ["string", "null"]},
                        "sections": {"type": "array", "items": SECTION_SCHEMA},
                    },
                    "required": [
                        "axis", "title", "description", "canonical_topic_id",
                        "scripture_target_id", "sections",
                    ],
                },
            },
            "unassigned_claim_ids": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": [
            "scope_confirmation", "candidate_plans", "unassigned_claim_ids", "summary",
        ],
    },
}

PLAN_REVIEW_SCHEMA: dict[str, Any] = {
    "name": "wang_candidate_plan_review_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "decision": {"type": "string", "enum": ["approve", "replace"]},
            "reason": {"type": "string"},
            "replacement_plans": {
                "type": "array",
                "items": CANDIDATE_SCHEMA["schema"]["properties"]["candidate_plans"]["items"],
            },
        },
        "required": [
            "scope_confirmation", "decision", "reason", "replacement_plans",
        ],
    },
}


PLAN_ADJUDICATION_SCHEMA: dict[str, Any] = {
    "name": "wang_candidate_plan_adjudication_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "decision": {"type": "string", "enum": ["accept_claude", "keep_openai"]},
            "reason": {"type": "string"},
        },
        "required": ["scope_confirmation", "decision", "reason"],
    },
}


PLAN_RECONSIDERATION_SCHEMA: dict[str, Any] = {
    "name": "wang_candidate_plan_reconsideration_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "decision": {"type": "string", "enum": ["accept_openai", "reaffirm"]},
            "reason": {"type": "string"},
        },
        "required": ["scope_confirmation", "decision", "reason"],
    },
}


def _digest(*values: str, size: int = 12) -> str:
    return hashlib.sha256(":".join(values).encode("utf-8")).hexdigest()[:size]


def scripture_targets(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Offer chapter-level targets; the model must still judge primary relevance."""
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        for raw in claim.get("scripture_refs") or []:
            ref = normalize_ref(str(raw))
            if not ref:
                continue
            key = (ref.book, ref.chapter_start)
            row = grouped.setdefault(
                key,
                {
                    "target_id": f"SCRIPTURE-{ref.book}-{ref.chapter_start}",
                    "label": f"{ref.book_zh}第{ref.chapter_start}章",
                    "book": ref.book,
                    "book_zh": ref.book_zh,
                    "chapter": ref.chapter_start,
                    "claim_ids": [],
                },
            )
            if claim_id and claim_id not in row["claim_ids"]:
                row["claim_ids"].append(claim_id)
    return sorted(grouped.values(), key=lambda item: (item["book"], item["chapter"]))


def projection_input(
    knowledge: dict[str, Any],
    reviewed_relations: dict[str, Any],
    topic_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    claims = [
        {
            "claim_id": row["claim_id"],
            "title": row.get("title") or row.get("statement"),
            "claim_type": row.get("claim_type"),
            "scripture_refs": row.get("scripture_refs") or [],
            "topic_terms": row.get("topic_terms") or [],
            "source_transcript_ids": sorted(
                {
                    str(item.get("transcript_id"))
                    for item in row.get("occurrences") or []
                    if item.get("transcript_id")
                }
            ),
        }
        for row in knowledge.get("claims") or []
    ]
    result = reviewed_relations.get("result") or reviewed_relations
    return {
        "scope": SCOPE,
        "batch": knowledge.get("batch") or {},
        "policy": {
            "selection_is_not_classification": True,
            "do_not_assume_one_batch_is_one_topic": True,
            "preserve_original_claims": True,
            "allow_unassigned_claims": True,
            "allow_multiple_topic_candidates": True,
            "scripture_reference_is_not_automatically_primary_exegesis": True,
            "questions_belong_to_qa_not_topic_exposition": True,
        },
        "canonical_topics": [
            {
                "topic_id": row.get("topic_id"),
                "label": row.get("label"),
                "parent_topic_id": row.get("parent_topic_id"),
                "definition": row.get("definition") or "",
            }
            for row in topic_nodes
        ],
        "scripture_targets": scripture_targets(claims),
        "claims": claims,
        "reviewed_cross_sermon_relations": result.get("reviewed_relations") or [],
        "unassigned_by_relation_review": result.get("unassigned_claim_ids") or [],
    }


def validate_candidates(payload: dict[str, Any], source: dict[str, Any]) -> None:
    if payload.get("scope_confirmation") != SCOPE:
        raise ValueError("candidate projection scope not confirmed")
    claim_ids = {str(row["claim_id"]) for row in source["claims"]}
    topic_ids = {str(row["topic_id"]) for row in source["canonical_topics"]}
    scripture_ids = {str(row["target_id"]) for row in source["scripture_targets"]}
    plans = payload.get("candidate_plans") or []
    if not plans:
        raise ValueError("candidate projection produced no plans")
    assigned_globally: set[str] = set()
    for index, plan in enumerate(plans):
        axis = plan.get("axis")
        if axis not in {"scripture", "topic"}:
            raise ValueError(f"candidate plan {index} has invalid axis")
        if not str(plan.get("title") or "").strip():
            raise ValueError(f"candidate plan {index} has no title")
        topic_id = str(plan.get("canonical_topic_id") or "")
        scripture_id = str(plan.get("scripture_target_id") or "")
        if axis == "topic" and topic_id and topic_id not in topic_ids:
            raise ValueError(f"candidate plan {index} has unknown topic {topic_id}")
        if axis == "scripture" and scripture_id not in scripture_ids:
            raise ValueError(f"candidate plan {index} has unknown scripture target {scripture_id}")
        sections = plan.get("sections") or []
        if not sections:
            raise ValueError(f"candidate plan {index} has no sections")
        used: set[str] = set()
        for section in sections:
            ids = [str(value) for value in section.get("claim_ids") or []]
            missing = sorted(set(ids) - claim_ids)
            if missing:
                raise ValueError(f"candidate plan {index} references unknown claims {missing}")
            duplicates = used.intersection(ids)
            if duplicates:
                raise ValueError(f"candidate plan {index} repeats claims {sorted(duplicates)}")
            used.update(ids)
            assigned_globally.update(ids)
    missing_unassigned = sorted(
        set(map(str, payload.get("unassigned_claim_ids") or [])) - claim_ids
    )
    if missing_unassigned:
        raise ValueError(f"unknown unassigned claims {missing_unassigned}")
    unassigned = set(map(str, payload.get("unassigned_claim_ids") or []))
    overlap = assigned_globally & unassigned
    if overlap:
        raise ValueError(f"claims cannot be both assigned and unassigned: {sorted(overlap)}")
    missing_coverage = claim_ids - assigned_globally - unassigned
    if missing_coverage:
        raise ValueError(f"candidate projection omitted claims: {sorted(missing_coverage)}")


def stable_plan_key(plan: dict[str, Any]) -> str:
    claims = sorted(
        str(claim_id)
        for section in plan.get("sections") or []
        for claim_id in section.get("claim_ids") or []
    )
    target = str(plan.get("canonical_topic_id") or plan.get("scripture_target_id") or plan.get("title"))
    return _digest(str(plan.get("axis")), target, *claims)


def _topic_slug(title: str) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return ascii_slug[:32] or f"candidate-{_digest(title)}"


def build_incremental_package(
    *,
    batch_id: str,
    reviewed_payload: dict[str, Any],
    canonical_topics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert reviewed candidate plans into idempotent authoring records."""
    topic_by_id = {str(row["topic_id"]): row for row in canonical_topics}
    topic_nodes: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    syntheses: list[dict[str, Any]] = []
    product_plans: list[dict[str, Any]] = []
    for plan in reviewed_payload.get("candidate_plans") or []:
        key = stable_plan_key(plan)
        axis = str(plan["axis"])
        prefix = "T" if axis == "topic" else "S"
        plan_id = f"CP-{batch_id.removeprefix('RB-')}-{prefix}-{key}"
        canonical_topic_id = str(plan.get("canonical_topic_id") or "")
        if axis == "topic" and not canonical_topic_id:
            canonical_topic_id = f"candidate-{_topic_slug(str(plan['title']))}-{key[:6]}"
            topic_nodes.append(
                {
                    "topic_id": canonical_topic_id,
                    "label": plan["title"],
                    "parent_topic_id": None,
                    "aliases": [],
                    "definition": plan.get("description") or "",
                    "legacy_ids": [],
                    "review_status": "candidate",
                    "visibility": "internal",
                    "revision": 1,
                }
            )
        elif canonical_topic_id in topic_by_id:
            # The route may reference the existing authoritative TopicNode.
            pass
        decisions = []
        all_claim_ids: list[str] = []
        for number, section in enumerate(plan.get("sections") or [], start=1):
            decision_id = f"CD-{batch_id.removeprefix('RB-')}-{prefix}-{key}-{number:02d}"
            claim_ids = list(dict.fromkeys(map(str, section.get("claim_ids") or [])))
            all_claim_ids.extend(claim_ids)
            decisions.append(
                {
                    "decision_id": decision_id,
                    "plan_id": plan_id,
                    "decision_type": section.get("arrangement") or "main_section",
                    "decision": section.get("section_title") or plan["title"],
                    "reason": section.get("reason") or "",
                    "claim_ids": claim_ids,
                    "review_status": "candidate",
                    "visibility": "internal",
                    "revision": 1,
                }
            )
            for claim_id in claim_ids:
                route_type = "topic_research" if axis == "topic" else "scripture_exposition"
                routes.append(
                    {
                        "route_id": f"KR-{key}-{_digest(claim_id, decision_id)}",
                        "claim_id": claim_id,
                        "route_type": route_type,
                        "target_id": plan_id,
                        "decision_ids": [decision_id],
                        "canonical_topic_ids": [canonical_topic_id] if canonical_topic_id else [],
                        "review_status": "candidate",
                        "visibility": "internal",
                        "revision": 1,
                    }
                )
        all_claim_ids = list(dict.fromkeys(all_claim_ids))
        product_plans.append(
            {
                "plan_id": plan_id,
                "product_type": "topic_research" if axis == "topic" else "scripture_exposition",
                "axis": axis,
                "title": plan["title"],
                "description": plan.get("description") or "",
                "decision_ids": [row["decision_id"] for row in decisions],
                "decisions": decisions,
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 1,
            }
        )
        syntheses.append(
            {
                "synthesis_id": f"SYN-{batch_id.removeprefix('RB-')}-{prefix}-{key}",
                "synthesis_type": "topic_candidate" if axis == "topic" else "scripture_candidate",
                "title": plan["title"],
                "description": plan.get("description") or "",
                "claim_ids": all_claim_ids,
                "corpus_scope": batch_id,
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 1,
            }
        )
    return {
        "schema_version": "wang_shared_knowledge_incremental_v1",
        "package_id": f"CANDIDATES-{batch_id}-{_digest(str(reviewed_payload))}",
        "topic_nodes": topic_nodes,
        "knowledge_routes": routes,
        "cross_source_syntheses": syntheses,
        "product_plans": product_plans,
        "candidate_generation": {
            "status": "reviewed_candidates",
            "scope": SCOPE,
            "unassigned_claim_ids": reviewed_payload.get("unassigned_claim_ids") or [],
        },
    }
