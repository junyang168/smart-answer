"""Discover reviewable topic families, subtopics, and article sections.

The shared claim graph remains authoritative.  This module creates editorial
proposals from that graph; it never rewrites claims or treats a processing
batch as a topic.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter, defaultdict
from typing import Any, Mapping

from backend.pipeline.knowledge_package import live_claims


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
        for row in live_claims(knowledge)
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


EDITORIAL_TITLE_PREFIXES = ("候选母题：", "候选专题：", "候選母題：", "候選專題：")
_TITLE_NOISE = str.maketrans({character: None for character in " \t　：:，,。.、；;（）()「」《》—-_"})


def normalize_topic_title(title: str) -> str:
    """Reduce an editorial label to the identity-bearing part of the name.

    Dropping the ``候选`` prefix and punctuation means approving a candidate, or
    tidying its punctuation, does not mint a different topic.
    """
    value = str(title or "").strip()
    for prefix in EDITORIAL_TITLE_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return value.translate(_TITLE_NOISE).casefold()


def topic_slug(*parts: str) -> str:
    """Create a deterministic *candidate* key, never a canonical topic id."""
    return _digest(*(normalize_topic_title(part) for part in parts))


def stable_family_key(family: dict[str, Any]) -> str:
    """Identify one discovered family inside a single run's report."""
    return topic_slug(str(family.get("title")))


def _slug(value: str) -> str:
    return _digest(value, size=10)


def _topic_level(row: Mapping[str, Any]) -> str:
    """A node without a parent is a family; that is how the store records roots."""
    level = row.get("topic_level")
    if level:
        return str(level)
    return "subtopic" if row.get("parent_topic_id") else "family"


def reconcile_topic_identity(
    proposed: list[dict[str, Any]],
    existing_topics: Mapping[str, Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Create persistent reconciliation work; never mint canonical identities."""
    existing = {
        topic_id: {
            "label": row.get("label"),
            "level": _topic_level(row),
            "parent_topic_id": row.get("parent_topic_id"),
            "claim_ids": {str(claim_id) for claim_id in (row.get("claim_ids") or [])},
        }
        for topic_id, row in (existing_topics or {}).items()
    }
    records: list[dict[str, Any]] = []
    exact_parent_mapping: dict[str, str] = {}
    for topic in proposed:
        topic_id = str(topic["topic_id"])
        level = _topic_level(topic)
        parent_id = str(topic.get("parent_topic_id") or "")
        claim_ids = {str(claim_id) for claim_id in topic.get("claim_ids") or []}
        expected_parent = exact_parent_mapping.get(parent_id) if parent_id else None
        exact = [
            other_id for other_id, other in existing.items()
            if other["level"] == level
            and (
                level == "family"
                or (
                    expected_parent is not None
                    and str(other.get("parent_topic_id") or "") == expected_parent
                )
            )
            and normalize_topic_title(str(other.get("label") or ""))
            == normalize_topic_title(str(topic.get("label") or ""))
        ]
        if len(exact) == 1:
            exact_parent_mapping[topic_id] = exact[0]
            records.append({
                "reconciliation_id": f"TIR-{_digest(topic_id, exact[0])}",
                "candidate_topic_id": topic_id,
                "label": topic["label"],
                "topic_level": level,
                "parent_candidate_topic_id": topic.get("parent_topic_id"),
                "claim_ids": sorted(claim_ids),
                "status": "matched_existing",
                "resolved_topic_id": exact[0],
                "resolution_action": "exact_normalized_label",
                "origin_batch_id": topic.get("origin_batch_id"),
                "review_status": "ai_consensus",
            })
            continue
        overlaps = []
        for other_id, other in existing.items():
            # A subtopic shares its parent's claims by construction, and a
            # subtopic is never a merge candidate for a family.
            if other_id == parent_id or other["level"] != level:
                continue
            shared = claim_ids & other["claim_ids"]
            if not shared:
                continue
            union = claim_ids | other["claim_ids"]
            overlaps.append({
                "existing_topic_id": other_id,
                "existing_label": other["label"],
                "shared_claim_count": len(shared),
                "jaccard": round(len(shared) / len(union), 4) if union else 0.0,
                "shared_claim_ids": sorted(shared),
            })
        overlaps.sort(key=lambda row: (-row["jaccard"], row["existing_topic_id"]))
        records.append({
            "reconciliation_id": f"TIR-{_digest(topic_id)}",
            "candidate_topic_id": topic_id,
            "label": topic["label"],
            "topic_level": level,
            "parent_candidate_topic_id": topic.get("parent_topic_id"),
            "claim_ids": sorted(claim_ids),
            "status": "pending_match" if overlaps else "pending_new",
            "candidate_matches": overlaps[:5],
            "origin_batch_id": topic.get("origin_batch_id"),
            "review_status": "candidate",
        })
    return records


_TOPIC_ID_NAMESPACE = uuid.UUID("8df74531-4d41-48a8-a4f7-785da9712077")


def _new_canonical_topic_id(reconciliation_id: str) -> str:
    """Allocate an opaque, repeatable id when a candidate is first approved."""
    return f"TOPIC-{uuid.uuid5(_TOPIC_ID_NAMESPACE, reconciliation_id).hex}"


def pending_topic_identity_ids(package: Mapping[str, Any]) -> list[str]:
    return [
        str(row["candidate_topic_id"])
        for row in package.get("topic_identity_reconciliations") or []
        if row.get("status") in {"pending_new", "pending_match"}
    ]


def resolve_topic_identity_package(
    candidate_package: Mapping[str, Any],
    resolutions: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rewrite candidate references to immutable canonical topic identities.

    ``resolutions`` is keyed by candidate topic id.  Each value uses either
    ``match_existing`` with ``canonical_topic_id`` or ``create_new``.  Exact
    matches recorded by discovery require no extra resolution.
    """
    resolutions = resolutions or {}
    reconciliations = [dict(row) for row in candidate_package.get("topic_identity_reconciliations") or []]
    mapping: dict[str, str] = {}
    for row in reconciliations:
        candidate_id = str(row["candidate_topic_id"])
        if row.get("status") == "matched_existing" and row.get("resolved_topic_id"):
            mapping[candidate_id] = str(row["resolved_topic_id"])
            continue
        decision = dict(resolutions.get(candidate_id) or {})
        action = str(decision.get("action") or "")
        if action == "match_existing":
            canonical_id = str(decision.get("canonical_topic_id") or "")
            if not canonical_id:
                raise ValueError(f"{candidate_id}: match_existing requires canonical_topic_id")
        elif action == "create_new":
            canonical_id = str(decision.get("canonical_topic_id") or _new_canonical_topic_id(str(row["reconciliation_id"])))
        else:
            raise ValueError(f"unresolved topic identity: {candidate_id}")
        mapping[candidate_id] = canonical_id
        row.update({
            "status": "resolved",
            "resolved_topic_id": canonical_id,
            "resolution_action": action,
            "review_status": "approved",
            "reviewed_by": decision.get("reviewed_by") or "topic_identity_resolution",
            "review_note": decision.get("review_note") or "",
        })

    candidate_topics = [dict(row) for row in candidate_package.get("candidate_topic_nodes") or []]
    created_ids = {
        mapping[str(row["candidate_topic_id"])]
        for row in reconciliations
        if row.get("resolution_action") == "create_new"
    }
    topics: list[dict[str, Any]] = []
    for row in candidate_topics:
        candidate_id = str(row.pop("topic_id"))
        canonical_id = mapping[candidate_id]
        if canonical_id not in created_ids:
            continue
        parent = row.get("parent_topic_id")
        row["topic_id"] = canonical_id
        row["parent_topic_id"] = mapping[str(parent)] if parent else None
        row["review_status"] = "candidate"
        topics.append(row)

    plan_id_map: dict[str, str] = {}
    decision_id_map: dict[str, str] = {}
    plans: list[dict[str, Any]] = []
    for raw_plan in candidate_package.get("candidate_product_plans") or []:
        plan = dict(raw_plan)
        old_plan_id = str(plan["plan_id"])
        canonical_topic_id = mapping[str(plan["canonical_topic_id"])]
        new_plan_id = f"TP-{_digest(canonical_topic_id)}"
        plan_id_map[old_plan_id] = new_plan_id
        plan["plan_id"] = new_plan_id
        plan["canonical_topic_id"] = canonical_topic_id
        plan["topic_family_id"] = mapping[str(plan["topic_family_id"])]
        decisions = []
        for index, raw_decision in enumerate(plan.get("decisions") or [], start=1):
            decision = dict(raw_decision)
            old_decision_id = str(decision["decision_id"])
            new_decision_id = f"SD-{_digest(new_plan_id, str(index))}"
            decision_id_map[old_decision_id] = new_decision_id
            decision["decision_id"] = new_decision_id
            decision["plan_id"] = new_plan_id
            decisions.append(decision)
        plan["decisions"] = decisions
        plan["decision_ids"] = [row["decision_id"] for row in decisions]
        plans.append(plan)

    routes: list[dict[str, Any]] = []
    for raw_route in candidate_package.get("candidate_knowledge_routes") or []:
        route = dict(raw_route)
        route["target_id"] = plan_id_map[str(route["target_id"])]
        route["decision_ids"] = [decision_id_map[str(item)] for item in route.get("decision_ids") or []]
        route["canonical_topic_ids"] = [mapping[str(item)] for item in route.get("canonical_topic_ids") or []]
        route["route_id"] = f"KR-{_digest(route['claim_id'], route['target_id'], *route['decision_ids'])}"
        routes.append(route)

    syntheses: list[dict[str, Any]] = []
    for raw in candidate_package.get("candidate_cross_source_syntheses") or []:
        row = dict(raw)
        topic_id = mapping[str(row["topic_id"])]
        row["topic_id"] = topic_id
        if row.get("parent_topic_id"):
            row["parent_topic_id"] = mapping[str(row["parent_topic_id"])]
        row["synthesis_id"] = f"SYN-{_digest(topic_id)}"
        syntheses.append(row)

    return {
        "schema_version": "wang_topic_structure_canonical_write_v1",
        "package_id": f"{candidate_package['package_id']}-RESOLVED",
        "topic_nodes": topics,
        "knowledge_routes": routes,
        "cross_source_syntheses": syntheses,
        "product_plans": plans,
        "topic_identity_reconciliations": reconciliations,
        "candidate_generation": {
            **dict(candidate_package.get("candidate_generation") or {}),
            "status": "identity_resolved_canonical_write",
            "pending_identity_candidates": [],
            "canonical_write_ready": True,
        },
    }


def build_incremental_package(
    *,
    batch_id: str,
    reviewed_payload: dict[str, Any],
    existing_topics: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist hierarchy as candidate TopicNodes, plans, decisions, and routes.

    This function creates a review artifact, not canonical TopicNodes.  Candidate
    ids may change between discovery runs; only the resolution step allocates or
    reuses an immutable canonical identity.
    """
    topics: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    syntheses: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    proposed_identities: list[dict[str, Any]] = []
    for family in reviewed_payload.get("topic_families") or []:
        family_key = stable_family_key(family)
        family_id = f"TCAND-FAMILY-{_digest(batch_id, family_key)}"
        family_claims = sorted(family_claim_ids(family))
        topics.append({
            "topic_id": family_id,
            "label": family["title"],
            "parent_topic_id": None,
            "aliases": [],
            "definition": family.get("organizing_question") or "",
            "topic_level": "family",
            "editorial_rationale": family.get("editorial_rationale") or "",
            "origin_batch_ids": [batch_id],
            "review_status": "candidate",
            "visibility": "internal",
            "revision": 1,
        })
        proposed_identities.append({
            "topic_id": family_id, "label": family["title"], "claim_ids": family_claims,
            "topic_level": "family", "parent_topic_id": None,
            "origin_batch_id": batch_id,
        })
        syntheses.append({
            "synthesis_id": f"SYN-FAMILY-{family_key}",
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
            subtopic_key = topic_slug(str(family.get("title")), str(subtopic["title"]))
            topic_id = f"TCAND-{_digest(batch_id, subtopic_key)}"
            plan_id = f"TCP-{_digest(batch_id, subtopic_key)}"
            topics.append({
                "topic_id": topic_id,
                "label": subtopic["title"],
                "parent_topic_id": family_id,
                "aliases": [],
                "definition": subtopic.get("central_question") or "",
                "topic_level": "subtopic",
                "editorial_rationale": subtopic.get("editorial_rationale") or "",
                "origin_batch_ids": [batch_id],
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 1,
            })
            proposed_identities.append({
                "topic_id": topic_id, "label": subtopic["title"], "claim_ids": subtopic_claims,
                "topic_level": "subtopic", "parent_topic_id": family_id,
                "origin_batch_id": batch_id,
            })
            decisions = []
            for index, section in enumerate(subtopic.get("sections") or [], start=1):
                decision_id = f"SD-{subtopic_key}-{index:02d}"
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
                "synthesis_id": f"SYN-TOPIC-{subtopic_key}",
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
    reconciliations = reconcile_topic_identity(proposed_identities, existing_topics)
    return {
        "schema_version": "wang_topic_structure_incremental_v3",
        "package_id": f"TOPIC-STRUCTURE-{batch_id}-{_digest(str(reviewed_payload))}",
        "topic_nodes": [],
        "knowledge_routes": [],
        "cross_source_syntheses": [],
        "product_plans": [],
        "candidate_topic_nodes": topics,
        "candidate_knowledge_routes": routes,
        "candidate_cross_source_syntheses": syntheses,
        "candidate_product_plans": plans,
        "topic_identity_reconciliations": reconciliations,
        "candidate_generation": {
            "status": "reviewed_topic_structure_candidates",
            "scope": SCOPE,
            "origin_batch_id": batch_id,
            "unassigned_claim_ids": reviewed_payload.get("unassigned_claim_ids") or [],
            "identity_policy": "candidate_ids_are_not_canonical; canonical_ids_are_reused_or_allocated_once_at_resolution",
            "pending_identity_candidates": pending_topic_identity_ids({"topic_identity_reconciliations": reconciliations}),
            "canonical_write_ready": not pending_topic_identity_ids({"topic_identity_reconciliations": reconciliations}),
        },
    }
