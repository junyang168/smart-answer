"""Stage reviewed cross-sermon relations for the shared knowledge store.

This module is deliberately deterministic.  AI review happens upstream; this
step only checks referential integrity, preserves unresolved disagreements,
and produces an auditable incremental package plus a merged candidate view.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.api.canonical_repository.postgres_store import (
    reviewed_relations_package,
    sha256_json,
)


ACCEPTED_REVIEW_STATUSES = {"ai_consensus", "approved"}
RELATION_COLLECTIONS = {
    "claim_relations": "claim_relation_id",
    "claim_relation_constraints": "constraint_id",
}


def _result(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    value = artifact.get("result") or artifact
    return value if isinstance(value, Mapping) else {}


def _accepted_rows(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = _result(artifact)
    return [
        dict(row)
        for row in [
            *result.get("reviewed_relations", []),
            *result.get("negative_comparisons", []),
        ]
        if row.get("review_status") in ACCEPTED_REVIEW_STATUSES
    ]


def _human_queue(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _result(artifact).get("outcomes", [])
        if row.get("status") == "human_review_required"
    ]


def validate_reviewed_relations(
    artifact: Mapping[str, Any], base_knowledge: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Validate accepted edges against the knowledge package they extend."""
    claim_ids = {
        str(row.get("claim_id"))
        for row in base_knowledge.get("claims", [])
        if row.get("claim_id")
    }
    evidence_ids = {
        str(row.get("evidence_step_id"))
        for row in base_knowledge.get("evidence_steps", [])
        if row.get("evidence_step_id")
    }
    findings: list[dict[str, Any]] = []
    seen: dict[str, tuple[str, str, str]] = {}

    for row in _accepted_rows(artifact):
        candidate_id = str(row.get("candidate_id") or "")
        source_id = str(row.get("source_claim_id") or "")
        target_id = str(row.get("target_claim_id") or "")
        relation_type = str(row.get("relation_type") or "")
        identity = (source_id, target_id, relation_type)
        if not candidate_id:
            findings.append({"severity": "error", "code": "missing_candidate_id"})
            continue
        if candidate_id in seen and seen[candidate_id] != identity:
            findings.append(
                {
                    "severity": "error",
                    "code": "candidate_id_collision",
                    "candidate_id": candidate_id,
                    "first": seen[candidate_id],
                    "second": identity,
                }
            )
        seen[candidate_id] = identity
        for role, claim_id in (("source", source_id), ("target", target_id)):
            if claim_id not in claim_ids:
                findings.append(
                    {
                        "severity": "error",
                        "code": "relation_endpoint_missing",
                        "candidate_id": candidate_id,
                        "endpoint_role": role,
                        "claim_id": claim_id or None,
                    }
                )
        if source_id and source_id == target_id:
            findings.append(
                {
                    "severity": "error",
                    "code": "self_relation_not_allowed",
                    "candidate_id": candidate_id,
                    "claim_id": source_id,
                }
            )
        for field in ("source_evidence_step_ids", "target_evidence_step_ids"):
            for evidence_id in row.get(field, []):
                if str(evidence_id) not in evidence_ids:
                    findings.append(
                        {
                            "severity": "error",
                            "code": "relation_evidence_missing",
                            "candidate_id": candidate_id,
                            "field": field,
                            "evidence_step_id": str(evidence_id),
                        }
                    )
    return findings


def merge_relation_increment(
    base_knowledge: Mapping[str, Any], increment: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge relation-only records without mutating the supplied base package."""
    candidate = copy.deepcopy(dict(base_knowledge))
    findings: list[dict[str, Any]] = []
    for collection, id_field in RELATION_COLLECTIONS.items():
        existing = {
            str(row[id_field]): dict(row)
            for row in candidate.get(collection, [])
            if row.get(id_field)
        }
        for incoming in increment.get(collection, []):
            object_id = str(incoming[id_field])
            prior = existing.get(object_id)
            if prior:
                prior_shape = (
                    prior.get("source_id") or prior.get("from_id"),
                    prior.get("target_id") or prior.get("to_id"),
                    prior.get("relation_type") or tuple(prior.get("forbidden_relation_types", [])),
                )
                incoming_shape = (
                    incoming.get("source_id") or incoming.get("from_id"),
                    incoming.get("target_id") or incoming.get("to_id"),
                    incoming.get("relation_type")
                    or tuple(incoming.get("forbidden_relation_types", [])),
                )
                if prior_shape != incoming_shape:
                    findings.append(
                        {
                            "severity": "error",
                            "code": "relation_id_collision",
                            "collection": collection,
                            "object_id": object_id,
                        }
                    )
                    continue
            existing[object_id] = dict(incoming)
        candidate[collection] = [existing[key] for key in sorted(existing)]

    digest = sha256_json(increment)
    candidate["package_id"] = (
        f"{base_knowledge.get('package_id') or 'KNOWLEDGE'}-XSR-{digest[:12]}"
    )
    candidate["candidate_generation"] = {
        "kind": "reviewed_cross_sermon_relation_integration",
        "increment_package_id": increment.get("package_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "publication_state": "candidate_not_active",
    }
    return candidate, findings


def build_reviewed_relation_integration(
    artifact: Mapping[str, Any], base_knowledge: Mapping[str, Any]
) -> dict[str, Any]:
    """Build all auditable products for one reviewed-relation integration."""
    increment = reviewed_relations_package(artifact)
    validation_findings = validate_reviewed_relations(artifact, base_knowledge)
    candidate, merge_findings = merge_relation_increment(base_knowledge, increment)
    findings = [*validation_findings, *merge_findings]
    human_queue = _human_queue(artifact)
    errors = [row for row in findings if row.get("severity") == "error"]
    accepted_count = sum(
        len(increment.get(collection, [])) for collection in RELATION_COLLECTIONS
    )
    status = "blocked" if errors else (
        "ready_with_human_queue" if human_queue else "ready"
    )
    return {
        "schema_version": "wang_reviewed_relation_integration_v1",
        "status": status,
        "source_artifact_sha256": sha256_json(artifact),
        "base_package_sha256": sha256_json(base_knowledge),
        "summary": {
            "accepted_relation_records": accepted_count,
            "human_review_items": len(human_queue),
            "errors": len(errors),
            "warnings": sum(row.get("severity") == "warning" for row in findings),
        },
        "findings": findings,
        "human_review_queue": human_queue,
        "incremental_package": increment,
        "candidate_snapshot": candidate,
    }
