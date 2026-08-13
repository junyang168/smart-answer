"""Discover reviewable topic families, subtopics, and article sections.

The shared claim graph remains authoritative.  This module creates editorial
proposals from that graph; it never rewrites claims or treats a processing
batch as a topic.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any


SCHEMA_VERSION = "wang_topic_structure_discovery_v1"
SCOPE = "topic_hierarchy_and_composition_no_theological_critique"

SECTION_ROLES = [
    "question_frame",
    "core_thesis",
    "scripture_evidence",
    "reasoning",
    "qualification",
    "application",
    "appendix",
]

SECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "role": {"type": "string", "enum": SECTION_ROLES},
        "purpose": {"type": "string"},
        "claim_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "role", "purpose", "claim_ids"],
}

SUBTOPIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "central_question": {"type": "string"},
        "editorial_rationale": {"type": "string"},
        "sections": {"type": "array", "items": SECTION_SCHEMA},
    },
    "required": ["title", "central_question", "editorial_rationale", "sections"],
}

FAMILY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "organizing_question": {"type": "string"},
        "editorial_rationale": {"type": "string"},
        "subtopics": {"type": "array", "items": SUBTOPIC_SCHEMA},
    },
    "required": ["title", "organizing_question", "editorial_rationale", "subtopics"],
}

DISCOVERY_SCHEMA: dict[str, Any] = {
    "name": SCHEMA_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "topic_families": {"type": "array", "items": FAMILY_SCHEMA},
            "unassigned_claim_ids": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": [
            "scope_confirmation", "topic_families", "unassigned_claim_ids", "summary"
        ],
    },
}

REVIEW_SCHEMA: dict[str, Any] = {
    "name": "wang_topic_family_review_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scope_confirmation": {"type": "string", "const": SCOPE},
            "decision": {"type": "string", "enum": ["approve", "replace"]},
            "reason": {"type": "string"},
            "replacement_families": {"type": "array", "items": FAMILY_SCHEMA},
        },
        "required": ["scope_confirmation", "decision", "reason", "replacement_families"],
    },
}

ADJUDICATION_SCHEMA: dict[str, Any] = {
    "name": "wang_topic_family_adjudication_v1",
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

RECONSIDERATION_SCHEMA: dict[str, Any] = {
    "name": "wang_topic_family_reconsideration_v1",
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


def _relation_ends(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("source_id") or row.get("from_id") or ""),
        str(row.get("target_id") or row.get("to_id") or ""),
    )


def _claim_sources(claim: dict[str, Any]) -> list[str]:
    return sorted({
        str(item.get("transcript_id"))
        for item in claim.get("occurrences") or []
        if item.get("transcript_id")
    })


def graph_profile(knowledge: dict[str, Any]) -> dict[str, Any]:
    """Produce deterministic graph landmarks without deciding the taxonomy."""
    claims = knowledge.get("claims") or []
    claim_ids = {str(row.get("claim_id")) for row in claims}
    degree: Counter[str] = Counter()
    relation_types: Counter[str] = Counter()
    neighbors: dict[str, set[str]] = defaultdict(set)
    for relation in knowledge.get("claim_relations") or []:
        source_id, target_id = _relation_ends(relation)
        if source_id not in claim_ids or target_id not in claim_ids:
            continue
        relation_type = str(relation.get("relation_type") or "unknown")
        relation_types[relation_type] += 1
        degree[source_id] += 1
        degree[target_id] += 1
        neighbors[source_id].add(target_id)
        neighbors[target_id].add(source_id)

    term_claims: dict[str, set[str]] = defaultdict(set)
    for claim in claims:
        for term in claim.get("topic_terms") or []:
            term_claims[str(term).strip()].add(str(claim.get("claim_id")))
    recurring_terms = [
        {"term": term, "claim_count": len(ids), "claim_ids": sorted(ids)}
        for term, ids in term_claims.items()
        if term and len(ids) >= 2
    ]
    recurring_terms.sort(key=lambda row: (-row["claim_count"], row["term"]))

    return {
        "claim_count": len(claims),
        "relation_count": sum(relation_types.values()),
        "relation_type_counts": dict(sorted(relation_types.items())),
        "high_connection_claims": [
            {
                "claim_id": claim_id,
                "degree": count,
                "neighbor_ids": sorted(neighbors[claim_id]),
            }
            for claim_id, count in degree.most_common(30)
        ],
        "recurring_topic_terms": recurring_terms[:60],
    }


def discovery_input(knowledge: dict[str, Any]) -> dict[str, Any]:
    claims = [
        {
            "claim_id": str(row.get("claim_id")),
            "statement": row.get("title") or row.get("statement") or "",
            "claim_type": row.get("claim_type") or "",
            "scripture_refs": row.get("scripture_refs") or [],
            "topic_terms": row.get("topic_terms") or [],
            "source_transcript_ids": _claim_sources(row),
        }
        for row in knowledge.get("claims") or []
    ]
    relations = []
    claim_ids = {row["claim_id"] for row in claims}
    for row in knowledge.get("claim_relations") or []:
        source_id, target_id = _relation_ends(row)
        if source_id not in claim_ids or target_id not in claim_ids:
            continue
        relations.append({
            "relation_id": row.get("claim_relation_id"),
            "source_claim_id": source_id,
            "target_claim_id": target_id,
            "relation_type": row.get("relation_type"),
            "reason": row.get("reason") or "",
            "review_status": row.get("review_status") or "legacy_reviewed",
        })
    return {
        "scope": SCOPE,
        "batch": knowledge.get("batch") or {},
        "policy": {
            "processing_batch_is_not_a_topic": True,
            "derive_structure_from_claims_and_relations_not_sermon_titles": True,
            "editorial_labels_are_not_professor_claims": True,
            "preserve_claims_verbatim_by_id": True,
            "each_claim_has_one_primary_topic_home_or_is_unassigned": True,
            "question_answer_material_may_be_unassigned_for_qa": True,
            "do_not_fact_check_or_theologically_criticize": True,
        },
        "graph_profile": graph_profile(knowledge),
        "claims": claims,
        "claim_relations": relations,
    }


def family_claim_ids(family: dict[str, Any]) -> set[str]:
    return {
        str(claim_id)
        for subtopic in family.get("subtopics") or []
        for section in subtopic.get("sections") or []
        for claim_id in section.get("claim_ids") or []
    }


def validate_discovery(payload: dict[str, Any], source: dict[str, Any]) -> None:
    if payload.get("scope_confirmation") != SCOPE:
        raise ValueError("topic structure scope not confirmed")
    known = {str(row["claim_id"]) for row in source.get("claims") or []}
    families = payload.get("topic_families") or []
    if not families:
        raise ValueError("topic structure produced no families")
    assigned: set[str] = set()
    family_titles: set[str] = set()
    for family_index, family in enumerate(families):
        title = str(family.get("title") or "").strip()
        if not title or title in family_titles:
            raise ValueError(f"family {family_index} has empty or duplicate title")
        family_titles.add(title)
        subtopics = family.get("subtopics") or []
        if not subtopics:
            raise ValueError(f"family {family_index} has no subtopics")
        subtopic_titles: set[str] = set()
        for subtopic_index, subtopic in enumerate(subtopics):
            subtopic_title = str(subtopic.get("title") or "").strip()
            if not subtopic_title or subtopic_title in subtopic_titles:
                raise ValueError(
                    f"family {family_index} subtopic {subtopic_index} has empty or duplicate title"
                )
            subtopic_titles.add(subtopic_title)
            sections = subtopic.get("sections") or []
            if not sections:
                raise ValueError(f"subtopic {subtopic_title} has no sections")
            local: set[str] = set()
            for section in sections:
                ids = list(map(str, section.get("claim_ids") or []))
                if not ids:
                    raise ValueError(f"subtopic {subtopic_title} has an empty section")
                missing = set(ids) - known
                if missing:
                    raise ValueError(f"unknown claims in {subtopic_title}: {sorted(missing)}")
                duplicate = local.intersection(ids)
                if duplicate:
                    raise ValueError(f"claims repeated inside {subtopic_title}: {sorted(duplicate)}")
                local.update(ids)
            cross_duplicate = assigned.intersection(local)
            if cross_duplicate:
                raise ValueError(
                    "a claim must have one primary topic home; repeated claims: "
                    + ", ".join(sorted(cross_duplicate))
                )
            assigned.update(local)
    unassigned = set(map(str, payload.get("unassigned_claim_ids") or []))
    unknown_unassigned = unassigned - known
    if unknown_unassigned:
        raise ValueError(f"unknown unassigned claims: {sorted(unknown_unassigned)}")
    overlap = assigned & unassigned
    if overlap:
        raise ValueError(f"assigned claims also marked unassigned: {sorted(overlap)}")
    omitted = known - assigned - unassigned
    if omitted:
        raise ValueError(f"topic structure omitted claims: {sorted(omitted)}")


def stable_family_key(family: dict[str, Any]) -> str:
    return _digest(str(family.get("title")), *sorted(family_claim_ids(family)))


def _slug(value: str) -> str:
    return _digest(value, size=10)


def build_incremental_package(
    *, batch_id: str, reviewed_payload: dict[str, Any]
) -> dict[str, Any]:
    """Persist hierarchy as candidate TopicNodes, plans, decisions, and routes."""
    topics: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    syntheses: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    prefix = batch_id.removeprefix("RB-")
    for family in reviewed_payload.get("topic_families") or []:
        family_key = stable_family_key(family)
        family_id = f"TOPIC-FAMILY-{prefix}-{family_key}"
        family_claims = sorted(family_claim_ids(family))
        topics.append({
            "topic_id": family_id,
            "label": family["title"],
            "parent_topic_id": None,
            "aliases": [],
            "definition": family.get("organizing_question") or "",
            "topic_level": "family",
            "editorial_rationale": family.get("editorial_rationale") or "",
            "review_status": "candidate",
            "visibility": "internal",
            "revision": 1,
        })
        syntheses.append({
            "synthesis_id": f"SYN-FAMILY-{prefix}-{family_key}",
            "synthesis_type": "topic_family_candidate",
            "title": family["title"],
            "description": family.get("editorial_rationale") or "",
            "claim_ids": family_claims,
            "corpus_scope": batch_id,
            "topic_id": family_id,
            "review_status": "candidate",
            "visibility": "internal",
            "revision": 1,
        })
        for subtopic in family.get("subtopics") or []:
            subtopic_claims = sorted({
                str(claim_id)
                for section in subtopic.get("sections") or []
                for claim_id in section.get("claim_ids") or []
            })
            subtopic_key = _digest(family_key, str(subtopic["title"]), *subtopic_claims)
            topic_id = f"TOPIC-{prefix}-{subtopic_key}"
            plan_id = f"TP-{prefix}-{subtopic_key}"
            topics.append({
                "topic_id": topic_id,
                "label": subtopic["title"],
                "parent_topic_id": family_id,
                "aliases": [],
                "definition": subtopic.get("central_question") or "",
                "topic_level": "subtopic",
                "editorial_rationale": subtopic.get("editorial_rationale") or "",
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 1,
            })
            decisions = []
            for index, section in enumerate(subtopic.get("sections") or [], start=1):
                decision_id = f"SD-{prefix}-{subtopic_key}-{index:02d}"
                claim_ids = list(dict.fromkeys(map(str, section.get("claim_ids") or [])))
                decisions.append({
                    "decision_id": decision_id,
                    "plan_id": plan_id,
                    "decision_type": "topic_section",
                    "decision": section["title"],
                    "reason": section.get("purpose") or "",
                    "section_role": section.get("role"),
                    "claim_ids": claim_ids,
                    "review_status": "candidate",
                    "visibility": "internal",
                    "revision": 1,
                })
                for claim_id in claim_ids:
                    routes.append({
                        "route_id": f"KR-{subtopic_key}-{_slug(claim_id + decision_id)}",
                        "claim_id": claim_id,
                        "route_type": "topic_research",
                        "target_id": plan_id,
                        "decision_ids": [decision_id],
                        "canonical_topic_ids": [family_id, topic_id],
                        "review_status": "candidate",
                        "visibility": "internal",
                        "revision": 1,
                    })
            plans.append({
                "plan_id": plan_id,
                "product_type": "topic_research",
                "axis": "topic",
                "title": subtopic["title"],
                "description": subtopic.get("central_question") or "",
                "topic_family_id": family_id,
                "canonical_topic_id": topic_id,
                "decision_ids": [row["decision_id"] for row in decisions],
                "decisions": decisions,
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 1,
            })
            syntheses.append({
                "synthesis_id": f"SYN-TOPIC-{prefix}-{subtopic_key}",
                "synthesis_type": "topic_candidate",
                "title": subtopic["title"],
                "description": subtopic.get("editorial_rationale") or "",
                "claim_ids": subtopic_claims,
                "corpus_scope": batch_id,
                "topic_id": topic_id,
                "parent_topic_id": family_id,
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 1,
            })
    return {
        "schema_version": "wang_topic_structure_incremental_v1",
        "package_id": f"TOPIC-STRUCTURE-{batch_id}-{_digest(str(reviewed_payload))}",
        "topic_nodes": topics,
        "knowledge_routes": routes,
        "cross_source_syntheses": syntheses,
        "product_plans": plans,
        "candidate_generation": {
            "status": "reviewed_topic_structure_candidates",
            "scope": SCOPE,
            "unassigned_claim_ids": reviewed_payload.get("unassigned_claim_ids") or [],
        },
    }
