from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from backend.config.wang_platform_paths import wang_platform_paths

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = wang_platform_paths().claim_layer_staging
DEFAULT_OUTPUT = DEFAULT_INPUT_DIR / "shared_knowledge_pilot_v1.json"
DEFAULT_ATTRIBUTION_OVERRIDES = DEFAULT_INPUT_DIR / "evidence_attribution_overrides_v1.json"
DEFAULT_CLAIM_OVERRIDES = DEFAULT_INPUT_DIR / "claim_statement_overrides_v1.json"
DEFAULT_RELATION_CONSENSUS = DEFAULT_INPUT_DIR / "claim_relation_consensus_v1.json"
DEFAULT_RELATION_REVIEW = DEFAULT_INPUT_DIR / "claim_relation_review_v1.json"
DEFAULT_QUESTION_OVERRIDES = DEFAULT_INPUT_DIR / "question_answer_state_overrides_v1.json"
DEFAULT_DETAILED_PACKAGES = [
    DEFAULT_INPUT_DIR
    / "detailed-extractions"
    / "011WSR01-f0eac41a4244.reviewed-candidate.json"
]
DEFAULT_TOPIC_TAXONOMY = (
    wang_platform_paths().seed_catalog / "matthew-review-v1/topic_taxonomy.json"
)

OBSERVATION_TYPES = {"經文", "背景"}

ANALYSIS_TOPIC_IDENTITY = {
    "TOPIC-PROMISE-OBEDIENCE": "promise-obedience",
    "TOPIC-ESCHATOLOGY-APOCALYPTIC": "apocalyptic-new-creation",
    "TOPIC-DISPENSATIONALISM": "dispensationalism",
    "TOPIC-RIGHTEOUSNESS-FAITH": "righteousness-faith-relationship",
}

SUPPLEMENTAL_TOPIC_DEFINITIONS = {
    "promise-obedience": {
        "label": "應許、順服與福分的享受",
        "parent_topic_id": "covenant-law-history",
    },
    "righteousness-faith-relationship": {
        "label": "義、信與人神關係",
        "parent_topic_id": "soteriology",
    },
}


def _evidence_metadata(node: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    excerpt = (node.get("q") or "").strip()
    speaker = "audience" if excerpt.startswith(("(聽眾", "（聽眾")) else "professor"
    missing_anchor = not excerpt or node.get("qt") is None
    metadata: dict[str, Any] = {
        "speaker": speaker,
        "stance": "neutral" if node.get("ty") == "問題" else "endorsed",
        "discourse_role": "question_context" if node.get("ty") == "問題" else "own_reasoning",
        "anchor_quality": "missing" if missing_anchor else "verified_candidate",
        "support_eligibility": (
            "withheld_missing_anchor"
            if missing_anchor
            else "contextual_only"
            if speaker == "audience" or node.get("ty") == "問題"
            else "eligible"
        ),
        "review_note": "",
        "rejected_highlights": [],
    }
    if override:
        metadata.update(override)
    return metadata


CLAIM_RELATIONS = [
    {"claim_relation_id": "CR-0001", "source_id": "CL-0013", "target_id": "CL-0021", "relation_type": "supports"},
    {"claim_relation_id": "CR-0002", "source_id": "CL-0023", "target_id": "CL-0007", "relation_type": "supports"},
    {"claim_relation_id": "CR-0003", "source_id": "CL-0007", "target_id": "CL-0029", "relation_type": "supports"},
    {"claim_relation_id": "CR-0004", "source_id": "CL-0009", "target_id": "CL-0022", "relation_type": "explains"},
    {"claim_relation_id": "CR-0005", "source_id": "CL-0022", "target_id": "CL-0012", "relation_type": "explains"},
    {"claim_relation_id": "CR-0006", "source_id": "CL-0009", "target_id": "CL-0012", "relation_type": "contextualizes"},
    {"claim_relation_id": "CR-0007", "source_id": "CL-0001", "target_id": "POS-M17-SECOND-COMING-FAILED", "relation_type": "refutes"},
]

ALLOWED_CLAIM_RELATION_TYPES = {
    "supports",
    "answers",
    "qualifies",
    "applies",
    "refutes",
    "contextualizes",
    "explains",
    "corroborates",
}


def _merge_claim_relation_consensus(
    claim_relations: list[dict[str, Any]],
    consensus_payload: dict[str, Any] | None,
    valid_endpoint_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge reviewed cross-claim edges and enforce explicit non-edge constraints."""
    payload = consensus_payload or {}
    merged = [dict(item) for item in claim_relations]
    relation_ids = {str(item.get("claim_relation_id")) for item in merged}

    for relation in payload.get("relations", []):
        item = dict(relation)
        relation_id = str(item.get("claim_relation_id") or "")
        source_id = str(item.get("source_id") or item.get("from_id") or "")
        target_id = str(item.get("target_id") or item.get("to_id") or "")
        relation_type = str(item.get("relation_type") or "")
        if not relation_id or relation_id in relation_ids:
            raise ValueError(f"Duplicate or missing consensus claim relation id: {relation_id!r}")
        if source_id not in valid_endpoint_ids or target_id not in valid_endpoint_ids:
            raise ValueError(
                f"Consensus claim relation {relation_id} has unresolved endpoint(s): "
                f"{source_id} -> {target_id}"
            )
        if relation_type not in ALLOWED_CLAIM_RELATION_TYPES:
            raise ValueError(
                f"Consensus claim relation {relation_id} has unsupported type: {relation_type}"
            )
        item.update(
            {
                "source_id": source_id,
                "target_id": target_id,
                "review_status": item.get("review_status", "ai_consensus_candidate"),
            }
        )
        merged.append(item)
        relation_ids.add(relation_id)

    constraints: list[dict[str, Any]] = []
    for constraint in payload.get("constraints", []):
        item = dict(constraint)
        constraint_id = str(item.get("constraint_id") or "")
        source_id = str(item.get("source_id") or "")
        target_id = str(item.get("target_id") or "")
        if not constraint_id:
            raise ValueError("Claim relation constraint is missing constraint_id")
        if source_id not in valid_endpoint_ids or target_id not in valid_endpoint_ids:
            raise ValueError(
                f"Claim relation constraint {constraint_id} has unresolved endpoint(s): "
                f"{source_id} -> {target_id}"
            )
        forbidden = set(item.get("forbidden_relation_types") or [])
        if not forbidden:
            raise ValueError(
                f"Claim relation constraint {constraint_id} has no forbidden relation types"
            )
        bidirectional = bool(item.get("bidirectional", False))
        for relation in merged:
            relation_pair = (relation.get("source_id"), relation.get("target_id"))
            constrained_pair = (source_id, target_id)
            reverse_pair = (target_id, source_id)
            if relation.get("relation_type") in forbidden and (
                relation_pair == constrained_pair
                or (bidirectional and relation_pair == reverse_pair)
            ):
                raise ValueError(
                    f"Claim relation {relation.get('claim_relation_id')} violates "
                    f"constraint {constraint_id}: {relation_pair[0]} "
                    f"{relation.get('relation_type')} {relation_pair[1]}"
                )
        item["review_status"] = item.get("review_status", "ai_consensus_candidate")
        constraints.append(item)

    return merged, constraints


def _apply_claim_relation_review(
    claim_relations: list[dict[str, Any]],
    review_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    outcomes = {
        str(item.get("claim_relation_id") or ""): item
        for item in (review_payload or {}).get("outcomes", [])
    }
    result: list[dict[str, Any]] = []
    for relation in claim_relations:
        item = dict(relation)
        outcome = outcomes.get(str(item.get("claim_relation_id") or ""))
        if not outcome:
            result.append(item)
            continue
        if outcome.get("status") != "ai_consensus_reviewed":
            item["review_status"] = "human_review_required"
            item["relation_review"] = outcome
            result.append(item)
            continue
        final_relation_type = str(outcome.get("final_relation_type") or "")
        if outcome.get("claude_decision") == "remove" or not final_relation_type:
            continue
        # The adjudicated type is authoritative even when Claude labelled its
        # own first-pass decision ``pass``.  Otherwise an accepted OpenAI
        # refinement can be recorded in the review artifact but silently
        # discarded by the shared graph builder.
        item["relation_type"] = final_relation_type
        item["review_status"] = "ai_consensus_reviewed"
        item["relation_review"] = outcome
        result.append(item)
    return result


def _knowledge_routes(
    claims: list[dict[str, Any]],
    product_plans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plan_routes_by_claim: dict[str, list[dict[str, Any]]] = {}
    for plan in product_plans:
        route_type = plan.get("product_type", "scripture_exposition")
        target_id = plan.get("product_target_id") or plan.get("plan_id")
        canonical_topic_ids = plan.get("canonical_topic_ids", [])
        for decision in plan.get("decisions", []):
            for claim_id in decision.get("claim_ids", []):
                routes = plan_routes_by_claim.setdefault(claim_id, [])
                existing = next(
                    (item for item in routes if item["target_id"] == target_id),
                    None,
                )
                if existing:
                    existing["decision_ids"].append(decision["decision_id"])
                else:
                    routes.append(
                        {
                            "route_type": route_type,
                            "target_id": target_id,
                            "decision_ids": [decision["decision_id"]],
                            "canonical_topic_ids": canonical_topic_ids,
                        }
                    )

    topic_targets = {
        "CL-0002": "TOPIC-PROMISE-OBEDIENCE",
        "CL-0004": "TOPIC-ESCHATOLOGY-APOCALYPTIC",
        "CL-0006": "TOPIC-DISPENSATIONALISM",
        "CL-0021": "TOPIC-ESCHATOLOGY-APOCALYPTIC",
        "CL-0024": "TOPIC-ESCHATOLOGY-APOCALYPTIC",
        "CL-0025": "TOPIC-RIGHTEOUSNESS-FAITH",
    }
    routes = []
    for claim in claims:
        claim_id = claim["claim_id"]
        plan_routes = plan_routes_by_claim.get(claim_id, [])
        if plan_routes:
            for position, plan_route in enumerate(plan_routes, start=1):
                routes.append(
                    {
                        "route_id": f"ROUTE-{claim_id}-{position}",
                        "claim_id": claim_id,
                        **plan_route,
                        "review_status": "candidate",
                    }
                )
            continue
        if claim.get("corpus_scope") == "detailed_single_sermon_extension":
            # A detailed extraction belongs in the shared graph immediately,
            # but it must not silently create a new canonical topic identity.
            # Until an editor routes it into a real product/topic plan, keep an
            # explicit candidate-routing record so the claim is neither lost
            # nor misrepresented as an approved topical classification.
            routes.append(
                {
                    "route_id": f"ROUTE-{claim_id}",
                    "claim_id": claim_id,
                    "route_type": "candidate_routing",
                    "target_id": f"UNROUTED-{claim_id}",
                    "decision_ids": [],
                    "canonical_topic_ids": [],
                    "review_status": "candidate",
                }
            )
            continue
        if claim.get("ai_route_override"):
            route_type = claim["ai_route_override"]
            target_id = topic_targets.get(claim_id, f"TOPIC-{claim.get('group_id', claim_id)}")
        elif claim.get("group_id", "").startswith("CG-METHOD-"):
            route_type = "method_research"
            target_id = "METHOD-EXEGESIS"
        elif claim_id == "CL-0026":
            route_type = "thought_development"
            target_id = "TIMELINE-INTERPRETIVE-DEVELOPMENT"
        else:
            route_type = "topic_research"
            target_id = topic_targets.get(claim_id, f"TOPIC-{claim.get('group_id', claim_id)}")
        routes.append(
            {
                "route_id": f"ROUTE-{claim_id}",
                "claim_id": claim_id,
                "route_type": route_type,
                "target_id": target_id,
                "decision_ids": [],
                "canonical_topic_ids": (
                    [ANALYSIS_TOPIC_IDENTITY[target_id]]
                    if route_type == "topic_research" and target_id in ANALYSIS_TOPIC_IDENTITY
                    else []
                ),
                "review_status": "candidate",
            }
        )
    return routes


def _validate_product_plan_evidence_scopes(
    product_plans: list[dict[str, Any]],
    claim_index: dict[str, dict[str, Any]],
) -> None:
    """Ensure composition evidence slices cannot cite foreign or missing steps."""
    for plan in product_plans:
        for decision in plan.get("decisions", []):
            decision_claim_ids = set(decision.get("claim_ids", []))
            scopes = (decision.get("claim_hierarchy") or {}).get(
                "evidence_step_scopes", []
            )
            seen_claim_ids: set[str] = set()
            for scope in scopes:
                claim_id = str(scope.get("claim_id") or "")
                if claim_id in seen_claim_ids:
                    raise ValueError(
                        f"{decision.get('decision_id')}: duplicate evidence scope for {claim_id}"
                    )
                seen_claim_ids.add(claim_id)
                if claim_id not in decision_claim_ids:
                    raise ValueError(
                        f"{decision.get('decision_id')}: evidence scope cites an unrouted claim {claim_id}"
                    )
                claim = claim_index.get(claim_id)
                if not claim:
                    # A partial package may load the plan before its detailed
                    # extraction package. The routed claim ID is still valid
                    # plan data; defer step ownership validation until that
                    # claim is present in the compiled package.
                    continue
                owned_steps = set(claim.get("evidence_step_ids", []))
                scoped_steps = set(scope.get("evidence_step_ids", []))
                if not scoped_steps:
                    raise ValueError(
                        f"{decision.get('decision_id')}: evidence scope for {claim_id} is empty"
                    )
                foreign_steps = scoped_steps - owned_steps
                if foreign_steps:
                    raise ValueError(
                        f"{decision.get('decision_id')}: evidence scope for {claim_id} cites foreign steps {sorted(foreign_steps)}"
                    )


def _topic_nodes(taxonomy_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Export the same authoritative identities consumed by the repository."""
    aliases_by_topic: dict[str, list[str]] = {}
    for legacy_id, topic_id in ANALYSIS_TOPIC_IDENTITY.items():
        aliases_by_topic.setdefault(topic_id, []).append(legacy_id)
    nodes: list[dict[str, Any]] = []
    for parent in taxonomy_payload.get("topics", []):
        nodes.append(
            {
                "topic_id": str(parent["id"]),
                "label": str(parent["label"]),
                "aliases": aliases_by_topic.get(str(parent["id"]), []),
                "legacy_ids": aliases_by_topic.get(str(parent["id"]), []),
                "review_status": "candidate",
            }
        )
        for child in parent.get("children", []):
            nodes.append(
                {
                    "topic_id": str(child["id"]),
                    "label": str(child["label"]),
                    "parent_topic_id": str(parent["id"]),
                    "aliases": aliases_by_topic.get(str(child["id"]), []),
                    "legacy_ids": aliases_by_topic.get(str(child["id"]), []),
                    "review_status": "candidate",
                }
            )
    existing_ids = {item["topic_id"] for item in nodes}
    for topic_id, definition in SUPPLEMENTAL_TOPIC_DEFINITIONS.items():
        if topic_id in existing_ids:
            continue
        nodes.append(
            {
                "topic_id": topic_id,
                "label": definition["label"],
                "parent_topic_id": definition["parent_topic_id"],
                "aliases": aliases_by_topic.get(topic_id, []),
                "legacy_ids": aliases_by_topic.get(topic_id, []),
                "review_status": "candidate",
            }
        )
    return nodes


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_reviewed_composition_candidate(input_dir: Path, filename: str) -> dict[str, Any] | None:
    original = input_dir / filename
    if not original.exists():
        return None
    plan = _read_json(original)
    reviewed = input_dir / "composition-reviews" / f"{plan['plan_id']}.reviewed-candidate.json"
    return _read_json(reviewed) if reviewed.exists() else plan


def _apply_claim_overrides(
    claims_payload: dict[str, Any],
    override_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply model-consensus corrections without mutating extraction history."""
    if not override_payload:
        return claims_payload
    result = json.loads(json.dumps(claims_payload, ensure_ascii=False))
    overrides = override_payload.get("claims", {})
    for claim in result.get("claims", []):
        override = overrides.get(claim.get("claim_id"))
        if not override or override.get("status") != "ai_consensus_applied":
            continue
        if override.get("superseded_by"):
            # This path applies field-level corrections; a merge also has to
            # move anchors and retarget relations, which only
            # `knowledge_consensus_applier` does. Ignoring it silently would
            # leave the duplicate live in the shared package with nothing to
            # show a merge was ever accepted.
            raise ValueError(
                f"{claim.get('claim_id')}: override merges into "
                f"{override['superseded_by']}; build this package with "
                "knowledge_consensus_applier, which can execute a merge"
            )
        if override.get("title"):
            claim["title"] = override["title"]
        if override.get("claim_type"):
            claim["claim_type"] = override["claim_type"]
        if override.get("scripture_refs"):
            claim["scripture_refs"] = override["scripture_refs"]
        if override.get("route_type"):
            claim["ai_route_override"] = override["route_type"]

        excluded = override.get("excluded_anchors", [])
        removed_evidence_ids: set[str] = set()
        retained_evidence_ids: set[str] = set()
        for occurrence in claim.get("occurrences", []):
            transcript_id = occurrence.get("transcript_id")
            lecture_number = "".join(
                character for character in occurrence.get("lecture", "") if character.isdigit()
            )
            retained = []
            for anchor in occurrence.get("anchors", []):
                excerpt = (anchor.get("proposed_highlight") or {}).get("text")
                should_exclude = any(
                    item.get("transcript_id") == transcript_id
                    and str(item.get("paragraph_key")) == str(anchor.get("paragraph_key"))
                    and (
                        not item.get("evidence_id")
                        or item.get("evidence_id") == anchor.get("evidence_id")
                    )
                    and (
                        not item.get("verbatim_excerpt")
                        or item.get("verbatim_excerpt") == excerpt
                    )
                    for item in excluded
                )
                if should_exclude:
                    local_evidence_id = anchor.get("evidence_id")
                    if local_evidence_id:
                        removed_evidence_ids.add(
                            f"L{lecture_number}-{local_evidence_id}"
                            if lecture_number and not str(local_evidence_id).startswith("L")
                            else str(local_evidence_id)
                        )
                else:
                    retained.append(anchor)
                    local_evidence_id = anchor.get("evidence_id")
                    if local_evidence_id:
                        retained_evidence_ids.add(
                            f"L{lecture_number}-{local_evidence_id}"
                            if lecture_number and not str(local_evidence_id).startswith("L")
                            else str(local_evidence_id)
                        )
            occurrence["anchors"] = retained

        for position, addition in enumerate(override.get("anchor_additions", []), start=1):
            transcript_id = addition.get("transcript_id")
            occurrence = next(
                (
                    item
                    for item in claim.get("occurrences", [])
                    if item.get("transcript_id") == transcript_id
                ),
                None,
            )
            if occurrence is None:
                occurrence = {
                    "transcript_id": transcript_id,
                    "lecture": "",
                    "local_source_evidence_ids": [],
                    "anchors": [],
                }
                claim.setdefault("occurrences", []).append(occurrence)
            excerpt = addition.get("verbatim_excerpt", "")
            occurrence.setdefault("anchors", []).append(
                {
                    "paragraph_key": addition.get("source_index"),
                    "evidence_id": f"AI-ADJ-{claim['claim_id']}-{position}",
                    "evidence_type": addition.get("evidence_type") or "reasoning",
                    "assertive": True,
                    "speaker": "professor",
                    "stance": "endorsed",
                    "discourse_role": "own_reasoning",
                    "proposed_highlight": {
                        "text": excerpt,
                        "status": "ai_consensus_candidate",
                    },
                }
            )
        claim["ai_adjudication"] = {
            "status": override.get("status"),
            "approval_status": override.get("approval_status"),
            "fingerprint": override.get("adjudication_fingerprint"),
            "structural_notes": override.get("structural_notes", []),
            # One extracted evidence step can have multiple candidate anchors.
            # Rejecting a bad anchor must not suppress the same evidence step
            # when another source-verified anchor remains.
            "excluded_evidence_step_ids": sorted(removed_evidence_ids - retained_evidence_ids),
        }
    return result


def _source_id(lecture: str) -> str:
    digits = "".join(character for character in lecture if character.isdigit())
    return f"SRC-L{digits or lecture}"


def _source_url(transcript_id: str | None, media_time: float | int | None = None) -> str | None:
    if not transcript_id:
        return None
    suffix = f"?t={int(media_time)}" if media_time is not None else ""
    return f"/resources/sermons/{quote(transcript_id, safe='')}{suffix}"


def _merge_detailed_package(
    package: dict[str, Any],
    *,
    source_documents: list[dict[str, Any]],
    fragments: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    evidence_steps: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    position_nodes: list[dict[str, Any]],
    claim_relations: list[dict[str, Any]],
) -> None:
    """Project a reviewed single-sermon package into the shared pilot model."""
    source_title_by_id: dict[str, str] = {}
    transcript_by_source_id: dict[str, str] = {}
    for source in package.get("source_documents", []):
        item = dict(source)
        transcript_id = str(item.get("transcript_id") or "")
        item["source_url"] = _source_url(transcript_id)
        item["lecture"] = item.get("title") or transcript_id
        source_documents.append(item)
        source_title_by_id[str(item["source_id"])] = str(item["lecture"])
        transcript_by_source_id[str(item["source_id"])] = transcript_id

    for fragment in package.get("source_fragments", []):
        item = dict(fragment)
        source_id = str(item.get("source_id") or "")
        transcript_id = transcript_by_source_id.get(source_id)
        item["lecture"] = source_title_by_id.get(source_id, transcript_id or "")
        item["source_url"] = _source_url(transcript_id, item.get("media_time"))
        fragments.append(item)

    eligibility_by_evidence: dict[str, str] = {}
    for step in package.get("evidence_steps", []):
        item = dict(step)
        fragment_ids = list(item.get("source_fragment_ids") or [])
        if not fragment_ids and item.get("source_fragment_id"):
            fragment_ids = [item["source_fragment_id"]]
        support = str(item.get("support_eligibility") or "eligible_candidate")
        normalized_support = "eligible" if support == "eligible_candidate" else support
        item.update(
            {
                "source_fragment_ids": fragment_ids,
                "source_fragment_id": fragment_ids[0] if fragment_ids else None,
                "function": item.get("function") or item.get("step_type"),
                "argument_lane": item.get("argument_lane") or item.get("discourse_role"),
                "anchor_quality": item.get("anchor_quality") or "source_version_bound",
                "support_eligibility": normalized_support,
            }
        )
        evidence_steps.append(item)
        eligibility_by_evidence[str(item["evidence_step_id"])] = normalized_support

    for question in package.get("questions", []):
        item = dict(question)
        fragment_ids = list(item.get("source_fragment_ids") or [])
        item["source_fragment_id"] = fragment_ids[0] if fragment_ids else None
        questions.append(item)
    for observation in package.get("observations", []):
        item = dict(observation)
        fragment_ids = list(item.get("source_fragment_ids") or [])
        item["source_fragment_id"] = fragment_ids[0] if fragment_ids else None
        observations.append(item)
    position_nodes.extend(dict(item) for item in package.get("position_nodes", []))

    for claim in package.get("claims", []):
        item = dict(claim)
        evidence_ids = list(item.get("evidence_step_ids") or [])
        item["eligible_evidence_step_ids"] = [
            evidence_id
            for evidence_id in evidence_ids
            if eligibility_by_evidence.get(evidence_id, "eligible") in {"eligible", "eligible_with_label"}
        ]
        item["context_evidence_step_ids"] = [
            evidence_id
            for evidence_id in evidence_ids
            if eligibility_by_evidence.get(evidence_id) == "contextual_only"
        ]
        item["withheld_evidence_step_ids"] = [
            evidence_id
            for evidence_id in evidence_ids
            if str(eligibility_by_evidence.get(evidence_id, "")).startswith("withheld")
        ]
        item["attribution"] = "professor"
        item["corpus_scope"] = "detailed_single_sermon_extension"
        item["lectures"] = [
            str(occurrence.get("lecture") or occurrence.get("transcript_id") or "")
            for occurrence in item.get("occurrences", [])
        ]
        item["recurrence"] = len(item.get("occurrences", []))
        item["maturity"] = "ai_consensus_candidate"
        claims.append(item)

    for relation in package.get("knowledge_relations", []):
        relations.append(
            {
                **relation,
                "source_id": relation.get("source_id") or relation.get("from_id"),
                "target_id": relation.get("target_id") or relation.get("to_id"),
                "review_status": relation.get("review_status", "candidate"),
            }
        )
    for relation in package.get("claim_relations", []):
        claim_relations.append(
            {
                **relation,
                "source_id": relation.get("source_id") or relation.get("from_id"),
                "target_id": relation.get("target_id") or relation.get("to_id"),
                "review_status": relation.get("review_status", "candidate"),
            }
        )


def build_shared_knowledge_package(
    claims_payload: dict[str, Any],
    graph_payload: dict[str, Any],
    composition_payload: dict[str, Any],
    attribution_overrides: dict[str, Any] | None = None,
    topic_taxonomy: dict[str, Any] | None = None,
    claim_overrides: dict[str, Any] | None = None,
    additional_composition_payloads: list[dict[str, Any]] | None = None,
    detailed_packages: list[dict[str, Any]] | None = None,
    claim_relation_consensus: dict[str, Any] | None = None,
    claim_relation_review: dict[str, Any] | None = None,
    question_answer_state_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claims_payload = _apply_claim_overrides(claims_payload, claim_overrides)
    overrides = (attribution_overrides or {}).get("evidence", {})
    source_projects = claims_payload.get("source_projects", [])
    transcript_by_lecture = {
        f"第{index + 3}講": source.get("transcript_id")
        for index, source in enumerate(source_projects)
    }
    source_documents = []
    for index, source in enumerate(source_projects):
        lecture = f"第{index + 3}講"
        transcript_id = source.get("transcript_id")
        source_documents.append(
            {
                "source_id": _source_id(lecture),
                "source_type": "sermon_transcript",
                "project_id": source.get("project_id"),
                "transcript_id": transcript_id,
                "lecture": lecture,
                "evidence_count": source.get("evidence_count", 0),
                "source_url": _source_url(transcript_id),
            }
        )

    answered_question_ids = {
        relation.get("source_evidence_id")
        for relation in graph_payload.get("relations", [])
        if relation.get("relation_type") == "answers"
    }
    question_overrides = (question_answer_state_overrides or {}).get("questions", {})

    fragments = []
    evidence_steps = []
    questions = []
    observations = []
    for node in graph_payload.get("evidence_nodes", []):
        lecture = node.get("lec", "")
        transcript_id = transcript_by_lecture.get(lecture)
        fragment_id = f"FR-{node['id']}"
        evidence_override = overrides.get(node["id"]) or {}
        fragment_keys = {"paragraph_key", "media_time", "verbatim_excerpt"}
        metadata = _evidence_metadata(
            node,
            {key: value for key, value in evidence_override.items() if key not in fragment_keys},
        )
        fragment = {
            "fragment_id": fragment_id,
            "source_id": _source_id(lecture),
            "local_source_evidence_id": node.get("e"),
            "lecture": lecture,
            "paragraph_key": evidence_override.get("paragraph_key", node.get("qp")),
            "media_time": evidence_override.get("media_time", node.get("qt")),
            "verbatim_excerpt": evidence_override.get("verbatim_excerpt", node.get("q", "")),
            "source_url": _source_url(
                transcript_id,
                evidence_override.get("media_time", node.get("qt")),
            ),
        }
        fragments.append(fragment)
        evidence_steps.append(
            {
                "evidence_step_id": node["id"],
                "source_fragment_id": fragment_id,
                "function": node.get("ty"),
                "argument_lane": node.get("lane"),
                "statement": node.get("full", ""),
                "scripture_refs": node.get("scr", []),
                "claim_group_id": node.get("topic"),
                "claim_group_ids": node.get("claim_group_ids", [node.get("topic")] if node.get("topic") else []),
                "claim_group_label": node.get("topicName"),
                **metadata,
                "review_status": "candidate",
            }
        )
        if node.get("ty") == "問題":
            # A question is only "linked" when the graph actually carries an
            # answers edge out of it.  Labelling every extracted question as
            # linked hid unanswered ones from the open-question list, and the
            # link itself never means a person confirmed the answer is complete.
            answered_in_graph = node["id"] in answered_question_ids
            question_id = f"Q-{node['id']}"
            question_override = question_overrides.get(question_id) or {}
            questions.append(
                {
                    "question_id": question_id,
                    "question": node.get("full", ""),
                    "source_fragment_id": fragment_id,
                    # A graph edge proves that some response was linked; it
                    # does not prove that every part of a compound question was
                    # answered. Keep linkage and completeness separate.
                    "argument_link_state": (
                        "linked_in_argument_graph" if answered_in_graph else "unlinked"
                    ),
                    "answer_state": question_override.get(
                        "answer_state", "answered" if answered_in_graph else "unanswered"
                    ),
                    "answer_state_origin": question_override.get(
                        "answer_state_origin", "derived_from_argument_graph"
                    ),
                    "answered_subquestions": question_override.get("answered_subquestions", []),
                    "unanswered_subquestions": question_override.get("unanswered_subquestions", []),
                    "answer_state_note": question_override.get("note", ""),
                    "answer_verified_by_human": False,
                    "review_status": "candidate",
                }
            )
        if node.get("ty") in OBSERVATION_TYPES:
            observations.append(
                {
                    "observation_id": f"OBS-{node['id']}",
                    "observation_type": node.get("ty"),
                    "statement": node.get("full", ""),
                    "scripture_refs": node.get("scr", []),
                    "source_fragment_id": fragment_id,
                    "review_status": "candidate",
                }
            )

    evidence_ids = {item["evidence_step_id"] for item in evidence_steps}
    claims = []
    for claim in claims_payload.get("claims", []):
        # Consensus-added anchors are source-verified during adjudication.  Turn
        # them into first-class fragments/evidence so downstream review screens
        # do not keep showing the superseded weak evidence only.
        for occurrence in claim.get("occurrences", []):
            for anchor in occurrence.get("anchors", []):
                evidence_id = str(anchor.get("evidence_id") or "")
                if not evidence_id.startswith("AI-ADJ-") or evidence_id in evidence_ids:
                    continue
                lecture = occurrence.get("lecture", "")
                transcript_id = occurrence.get("transcript_id")
                excerpt = (anchor.get("proposed_highlight") or {}).get("text", "")
                fragment_id = f"FR-{evidence_id}"
                fragments.append(
                    {
                        "fragment_id": fragment_id,
                        "source_id": _source_id(lecture),
                        "local_source_evidence_id": evidence_id,
                        "lecture": lecture,
                        "paragraph_key": anchor.get("paragraph_key"),
                        "media_time": anchor.get("media_time"),
                        "verbatim_excerpt": excerpt,
                        "source_url": _source_url(transcript_id, anchor.get("media_time")),
                        "anchor_origin": "ai_consensus_adjudication",
                    }
                )
                function = {
                    "exegesis": "解經",
                    "reasoning": "推理",
                    "methodology": "推理",
                    "theology": "神學",
                    "scripture": "經文",
                }.get(anchor.get("evidence_type"), anchor.get("evidence_type") or "推理")
                evidence_steps.append(
                    {
                        "evidence_step_id": evidence_id,
                        "source_fragment_id": fragment_id,
                        "function": function,
                        "argument_lane": "ai_consensus_repair",
                        "statement": excerpt,
                        "scripture_refs": claim.get("scripture_refs", []),
                        "claim_group_id": claim.get("group_id"),
                        "claim_group_ids": [claim.get("group_id")],
                        "claim_group_label": claim.get("title"),
                        "speaker": "professor",
                        "stance": "endorsed",
                        "discourse_role": "own_reasoning",
                        "anchor_quality": "verified_candidate",
                        "support_eligibility": "eligible",
                        "review_note": "OpenAI/Claude fidelity consensus source repair.",
                        "rejected_highlights": [],
                        "review_status": "candidate",
                    }
                )
                evidence_ids.add(evidence_id)
        claim_evidence_ids = [
            step["evidence_step_id"]
            for step in evidence_steps
            if claim.get("group_id") in step.get("claim_group_ids", [])
        ]
        eligible = []
        contextual = []
        withheld = []
        adjudication_exclusions = set(
            (claim.get("ai_adjudication") or {}).get("excluded_evidence_step_ids", [])
        )
        evidence_by_id = {item["evidence_step_id"]: item for item in evidence_steps}
        for evidence_id in claim_evidence_ids:
            if evidence_id in adjudication_exclusions:
                withheld.append(evidence_id)
                continue
            eligibility = evidence_by_id[evidence_id]["support_eligibility"]
            if eligibility.startswith("withheld"):
                withheld.append(evidence_id)
            elif eligibility == "contextual_only":
                contextual.append(evidence_id)
            else:
                eligible.append(evidence_id)
        normalized_occurrences = []
        for occurrence in claim.get("occurrences", []):
            local_ids = occurrence.get("local_source_evidence_ids", occurrence.get("source_evidence_ids", []))
            lecture_number = "".join(character for character in occurrence.get("lecture", "") if character.isdigit())
            normalized_occurrence = {
                key: value
                for key, value in occurrence.items()
                if key not in {"source_evidence_ids", "local_source_evidence_ids"}
            }
            normalized_occurrence["local_source_evidence_ids"] = local_ids
            normalized_occurrence["canonical_evidence_step_ids"] = [
                f"L{lecture_number}-{local_id}" for local_id in local_ids
            ] if lecture_number else []
            normalized_occurrences.append(normalized_occurrence)
        normalized_claim = {
            key: value for key, value in claim.items() if key != "occurrences"
        }
        normalized_claim["occurrences"] = normalized_occurrences
        claims.append(
            {
                **normalized_claim,
                "evidence_step_ids": claim_evidence_ids,
                "eligible_evidence_step_ids": eligible,
                "context_evidence_step_ids": contextual,
                "withheld_evidence_step_ids": withheld,
                "attribution": "professor",
                "corpus_scope": "pilot_two_lectures",
                "maturity": "candidate",
            }
        )

    relations = []
    for relation in graph_payload.get("relations", []):
        source_id = relation.get("source_evidence_id")
        target_id = relation.get("target_evidence_id")
        if source_id not in evidence_ids or target_id not in evidence_ids:
            raise ValueError(f"Relation endpoint is missing: {source_id} -> {target_id}")
        relations.append(
            {
                "relation_id": relation.get("relation_id"),
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation.get("relation_type"),
                "review_status": relation.get("review_status", "candidate"),
            }
        )

    for open_question in claims_payload.get("open_questions", []):
        questions.append(
            {
                "question_id": open_question["open_question_id"],
                "question": open_question["question"],
                "answer_state": open_question.get("status"),
                "note": open_question.get("note"),
                "occurrences": open_question.get("occurrences", []),
                "review_status": open_question.get("review_status", "candidate"),
            }
        )

    detailed_position_nodes: list[dict[str, Any]] = []
    detailed_claim_relations: list[dict[str, Any]] = []
    for detailed_package in detailed_packages or []:
        _merge_detailed_package(
            detailed_package,
            source_documents=source_documents,
            fragments=fragments,
            questions=questions,
            observations=observations,
            evidence_steps=evidence_steps,
            claims=claims,
            relations=relations,
            position_nodes=detailed_position_nodes,
            claim_relations=detailed_claim_relations,
        )

    claim_index = {claim["claim_id"]: claim for claim in claims}
    position_nodes = [
        {
            "position_id": "POS-M17-SECOND-COMING-FAILED",
            "title": "太16:28直接指第二次再臨，因此預言落空或不可靠",
            "attribution": "external_opponent_position",
            "corpus_scope": "pilot_two_lectures",
            "review_status": "candidate",
        }
    ] + detailed_position_nodes
    valid_claim_or_position_ids = set(claim_index) | {item["position_id"] for item in position_nodes}
    claim_relations = [
        {**relation, "review_status": "candidate"}
        for relation in CLAIM_RELATIONS
        if relation["source_id"] in valid_claim_or_position_ids and relation["target_id"] in valid_claim_or_position_ids
    ] + [
        relation
        for relation in detailed_claim_relations
        if relation["source_id"] in valid_claim_or_position_ids
        and relation["target_id"] in valid_claim_or_position_ids
    ]
    claim_relations, claim_relation_constraints = _merge_claim_relation_consensus(
        claim_relations,
        claim_relation_consensus,
        valid_claim_or_position_ids,
    )
    claim_relations = _apply_claim_relation_review(
        claim_relations,
        claim_relation_review,
    )
    product_plans = [composition_payload, *(additional_composition_payloads or [])]
    _validate_product_plan_evidence_scopes(product_plans, claim_index)
    knowledge_routes = _knowledge_routes(claims, product_plans)
    topic_nodes = _topic_nodes(
        topic_taxonomy
        if topic_taxonomy is not None
        else _read_json(DEFAULT_TOPIC_TAXONOMY)
    )
    relation_degree = Counter()
    for relation in claim_relations:
        if relation["source_id"] in claim_index:
            relation_degree[relation["source_id"]] += 1
        if relation["target_id"] in claim_index:
            relation_degree[relation["target_id"]] += 1
    decision_actions_by_claim: dict[str, list[dict[str, Any]]] = {}
    for plan in product_plans:
        for decision in plan.get("decisions", []):
            for claim_id in decision.get("claim_ids", []):
                decision_actions_by_claim.setdefault(claim_id, []).append(
                    {
                        "plan_id": plan.get("plan_id"),
                        "decision_id": decision.get("decision_id"),
                        "action": decision.get("action"),
                    }
                )
    for claim in claims:
        product_actions = decision_actions_by_claim.get(claim["claim_id"], [])
        claim["metrics"] = {
            "frequency_count": claim.get("recurrence", 0),
            "frequency_note": "只表示在本次材料中出現的頻率，不等於重要性。",
            "product_relevance": (
                product_actions[0]["action"]
                if product_actions
                else "routed_outside_current_products"
            ),
            "product_relevance_actions": product_actions,
            "thought_centrality_candidate": relation_degree[claim["claim_id"]],
        }
    cross_source_claim_ids = [
        claim["claim_id"] for claim in claims if len(claim.get("lectures", [])) > 1
    ]
    cross_source_syntheses = [
        {
            "synthesis_id": "SYN-CROSS-LECTURE",
            "synthesis_type": "cross_source_claims",
            "title": "兩講中重複、延伸或互相補充的主張",
            "description": "這些主張在第三講和第四講都出現；歸併關係需要人工確認。",
            "claim_ids": cross_source_claim_ids,
            "corpus_scope": "pilot_two_lectures",
            "validation_only": True,
            "review_status": "candidate",
        },
        {
            "synthesis_id": "SYN-TOPIC-SON-OF-MAN",
            "synthesis_type": "topic_composition_candidate",
            "title": "「人子」與耶穌身份：雙軸專題驗證",
            "description": "以205篇普查线索界定语料范围，以第三、第四讲和011WSR01的详细论证资料建立专题候选子图；其余普查线索在完成来源核实前不作为已验证结论。",
            "claim_ids": sorted(
                {
                    claim_id
                    for plan in product_plans
                    if plan.get("plan_id") == "CP-topic-son-of-man"
                    for decision in plan.get("decisions", [])
                    for claim_id in decision.get("claim_ids", [])
                    if claim_id in claim_index
                }
            ),
            "source_leads": next(
                (
                    plan.get("source_leads", [])
                    for plan in product_plans
                    if plan.get("plan_id") == "CP-topic-son-of-man"
                ),
                [],
            ),
            "corpus_scope": "full_corpus_survey_with_three_detailed_sources",
            "validation_only": True,
            "review_status": "candidate",
        },
        {
            "synthesis_id": "SYN-METHOD",
            "synthesis_type": "method_pattern_lead",
            "title": "教授釋經方法：跨講模式驗證",
            "description": "檢驗原文、文體、上下文、歷史文化與清楚經文互證等方法能否從多篇材料累積出來。",
            "claim_ids": [
                claim["claim_id"]
                for claim in claims
                if claim.get("group_id", "").startswith("CG-METHOD-")
            ],
            "corpus_scope": "pilot_two_lectures",
            "validation_only": True,
            "review_status": "candidate",
        },
    ]

    validation_experiments = [
        {
            "experiment_id": "VAL-EXEGESIS-M17",
            "product_type": "scripture_exposition",
            "title": "马太福音第17章释经",
            "question": "共享知识能否支持一篇忠实、连贯、不过度岔题的逐段释经？",
            "acceptance_criteria": ["重要论断可回到原始录音", "材料缺口明确显示", "专题岔题只作链接或短注"],
            "status": "ready_for_human_review",
            "product_plan_id": composition_payload.get("plan_id"),
        },
        {
            "experiment_id": "VAL-TOPIC-SON-OF-MAN",
            "product_type": "topic_retrieval_dossier",
            "title": "“人子”专题检索档案",
            "question": "系统能否从不同讲道找出相关主张、差异与来源，而不把两讲误称为完整专题？",
            "acceptance_criteria": ["只称检索线索，不称最终结论", "保留不同场合的证据", "可继续吸收其余讲道"],
            "status": "ready_for_human_review",
            "product_plan_id": next(
                (
                    plan.get("plan_id")
                    for plan in product_plans
                    if plan.get("product_type") == "topic_research"
                ),
                None,
            ),
        },
        {
            "experiment_id": "VAL-QA",
            "product_type": "question_answer_collection",
            "title": "问答链复原",
            "question": "系统能否保存听众问题、教授回答及未回答边界？",
            "acceptance_criteria": ["问题与回答不被文章结构吞掉", "部分回答与未回答准确标示", "回答证据可复用"],
            "status": "ready_for_human_review",
        },
        {
            "experiment_id": "VAL-SEARCH-QA",
            "product_type": "search_and_ai_qa",
            "title": "检索与智能问答",
            "question": "面对具体问题，系统能否返回主张、论证链和原始定位，而不是只返回文章？",
            "acceptance_criteria": ["答案引用教授原话", "区分教授主张与编辑归纳", "显示当前语料范围"],
            "status": "ready_for_human_review",
        },
        {
            "experiment_id": "VAL-METHOD",
            "product_type": "method_research",
            "title": "教授释经方法研究",
            "question": "系统能否跨篇累计教授反复采用的释经动作，而不是只收集结论？",
            "acceptance_criteria": ["每个方法模式有多处实例", "频率与来源可核对", "允许随全库分析修订"],
            "status": "ready_for_human_review",
        },
    ]

    return {
        "schema_version": "wang_shared_knowledge_v1.2",
        "package_id": "SKP-MATTHEW-LECTURES-3-4-SON-OF-MAN-011",
        "title": "马太福音释经（五）第三、第四讲与专题扩展来源共享知识验证包",
        "corpus_scope": {
            "scope_type": "pilot_subset",
            "source_count": len(source_documents),
            "description": "包含第三讲、第四讲及011WSR01逐句详细知识，用于验证同一知识模型的多种用途。",
            "completeness": "not_corpus_complete",
            "warning": "这里仍是三篇详细来源加全库普查线索，不代表教授全部205篇讲道的最终思想结构。",
        },
        "source_documents": source_documents,
        "source_fragments": fragments,
        "questions": questions,
        "observations": observations,
        "claims": claims,
        "position_nodes": position_nodes,
        "evidence_steps": evidence_steps,
        "knowledge_relations": relations,
        "claim_relations": claim_relations,
        "claim_relation_constraints": claim_relation_constraints,
        "tensions": [
            {
                "tension_id": "TENSION-LITTLE-FAITH-MUSTARD",
                "claim_ids": ["CL-0012"],
                "question": "若「信心小」不是信心數量不足，太17:20為何仍使用芥菜種的大小意象？",
                "attribution": "editorial_open_question",
                "status": "open_question",
                "review_status": "candidate",
            }
        ],
        "editorial_checks": [
            {
                "check_id": "CHECK-M17-21-TEXTUAL-VARIANT",
                "title": "太17:21經文異文需獨立核查",
                "note": "教授按和合本引用此節；最早抄本與NA28是否收錄，應在出版階段另作文本批判核查，不可改寫為教授自己的主張。",
                "status": "pending_fact_check",
            },
            {
                "check_id": "CHECK-CL0010-CL0011-WORDING",
                "title": "摩西、以利亞的代表功能與僕人身分須分層表述",
                "note": "「律法與先知」說明敘事中的代表功能；「先知、僕人」說明兩人在基督之下的身分，兩者並不互相排斥。",
                "status": "pending_editorial_review",
            },
        ],
        "knowledge_routes": knowledge_routes,
        "topic_nodes": topic_nodes,
        "framework_candidate": graph_payload.get("framework_candidate"),
        "cross_source_syntheses": cross_source_syntheses,
        "product_plans": [
            {
                **plan,
                "product_type": plan.get("product_type", "scripture_exposition"),
                "validation_only": True,
                "editorial_attribution": "editor",
            }
            for plan in product_plans
        ],
        "validation_experiments": validation_experiments,
        "summary": {
            "counts": {
                "sources": len(source_documents),
                "fragments": len(fragments),
                "questions": len(questions),
                "observations": len(observations),
                "claims": len(claims),
                "evidence_steps": len(evidence_steps),
                "relations": len(relations),
                "claim_relations": len(claim_relations),
                "claim_relation_constraints": len(claim_relation_constraints),
                "position_nodes": len(position_nodes),
                "knowledge_routes": len(knowledge_routes),
                "topic_nodes": len(topic_nodes),
                "cross_source_syntheses": len(cross_source_syntheses),
                "validation_experiments": len(validation_experiments),
            },
            "evidence_function_counts": dict(Counter(step["function"] for step in evidence_steps)),
            "evidence_eligibility_counts": dict(Counter(step["support_eligibility"] for step in evidence_steps)),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the shared knowledge multi-product validation package.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    package = build_shared_knowledge_package(
        _read_json(args.input_dir / "claims.json"),
        _read_json(args.input_dir / "argument_graph.json"),
        _read_json(args.input_dir / "composition_plan_matthew_17.json"),
        _read_json(args.input_dir / "evidence_attribution_overrides_v1.json")
        if (args.input_dir / "evidence_attribution_overrides_v1.json").exists()
        else None,
        claim_overrides=(
            _read_json(args.input_dir / "claim_statement_overrides_v1.json")
            if (args.input_dir / "claim_statement_overrides_v1.json").exists()
            else None
        ),
        additional_composition_payloads=[
            plan
            for plan in (
                _read_reviewed_composition_candidate(
                    args.input_dir, "composition_plan_matthew_26_1_30_011.json"
                ),
                _read_reviewed_composition_candidate(
                    args.input_dir, "composition_plan_son_of_man.json"
                ),
            )
            if plan is not None
        ],
        detailed_packages=[
            _read_json(path) for path in DEFAULT_DETAILED_PACKAGES if path.exists()
        ],
        claim_relation_consensus=(
            _read_json(args.input_dir / "claim_relation_consensus_v1.json")
            if (args.input_dir / "claim_relation_consensus_v1.json").exists()
            else None
        ),
        claim_relation_review=(
            _read_json(args.input_dir / "claim_relation_review_v1.json")
            if (args.input_dir / "claim_relation_review_v1.json").exists()
            else None
        ),
        question_answer_state_overrides=(
            _read_json(args.input_dir / "question_answer_state_overrides_v1.json")
            if (args.input_dir / "question_answer_state_overrides_v1.json").exists()
            else None
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(package["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
