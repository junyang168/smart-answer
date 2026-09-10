"""Transactional PostgreSQL authoring store for the shared knowledge model.

PostgreSQL is the canonical *authoring* authority.  JSON knowledge packages
remain versioned exchange inputs and compiled read snapshots for existing UI,
search, and QA consumers.  A ResearchBatch never becomes a separate semantic
store: every accepted package is applied as an idempotent ChangeSet here.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .knowledge_importer import KnowledgePackageImporter
from .knowledge_models import KNOWLEDGE_COLLECTIONS


MIGRATIONS_DIR = Path(__file__).with_name("migrations")
EDGE_COLLECTIONS = {
    "knowledge_relations",
    "claim_relations",
    "claim_relation_constraints",
    "viewpoint_claim_links",
    "viewpoint_proposition_unit_links",
    "viewpoint_relations",
}
EDGE_ENDPOINT_COLLECTIONS = {
    "knowledge_relations": ({"evidence_steps", "observations"}, {"evidence_steps"}),
    "claim_relations": ({"claims"}, {"claims"}),
    "claim_relation_constraints": ({"claims"}, {"claims"}),
    "viewpoint_claim_links": ({"canonical_viewpoints"}, {"claims"}),
    "viewpoint_proposition_unit_links": (
        {"canonical_viewpoints"}, {"viewpoint_proposition_units"},
    ),
    "viewpoint_relations": ({"canonical_viewpoints"}, {"canonical_viewpoints"}),
}
VIEWPOINT_VALIDATION_COLLECTIONS = {
    "source_documents",
    "source_fragments",
    "claims",
    "evidence_steps",
    "claim_relations",
    "viewpoint_coverage_snapshots",
    "canonical_viewpoints",
    "viewpoint_revisions",
    "viewpoint_claim_links",
    "viewpoint_proposition_units",
    "viewpoint_proposition_unit_links",
    "argument_routes",
    "argument_route_revisions",
    "argument_route_attestations",
    "viewpoint_relations",
    "viewpoint_identity_candidates",
    "viewpoint_identity_decisions",
    "viewpoint_resolution_ledgers",
    "viewpoint_quality_reports",
    "viewpoint_atomic_coverage_snapshots",
    "viewpoint_atomic_resolution_ledgers",
    "viewpoint_atomic_quality_reports",
    "viewpoint_automated_promotion_decisions",
}
EXTRACTION_RECORD_COLLECTIONS = {
    "source_fragments",
    "questions",
    "position_nodes",
    "observations",
    "evidence_steps",
    "claims",
    "knowledge_relations",
    "claim_relations",
}
# Fields that mean a *current semantic dependency* on an extraction record.
# Searching every string value caused transcript metadata and explicit lineage
# (for example ``previous_claim_id``) to masquerade as live references. Nested
# objects are still traversed, but only values under these authoritative field
# names can block a retirement.
# Retired CompositionPlan/CompositionDecision rows are historical artifacts,
# not live authoring authority; draft-first products are protected through
# ProductDependency invalidation instead.
SEMANTIC_EXTRACTION_REFERENCE_FIELDS = {
    "topic_identity_reconciliations": {"claim_ids"},
    "claim_relation_constraints": {"source_id", "target_id"},
    "knowledge_routes": {"claim_id"},
    "editorial_syntheses": {"claim_ids"},
    "viewpoint_coverage_snapshots": {"source_id"},
    "viewpoint_structure_revisions": {"basis_claim_ids"},
    "viewpoint_claim_links": {
        "claim_id", "supporting_relation_ids", "evidence_step_id", "source_fragment_id",
    },
    "viewpoint_proposition_units": {
        "parent_claim_id", "source_id", "evidence_step_id", "source_fragment_id",
    },
    "viewpoint_atomic_coverage_snapshots": {"claim_ids", "source_ids"},
    "viewpoint_atomic_resolution_ledgers": {"parent_claim_id"},
    "argument_route_revisions": {"evidence_step_ids", "source_fragment_ids"},
    "argument_route_attestations": {
        "source_id", "claim_ids", "evidence_step_ids", "source_fragment_ids",
    },
    "viewpoint_relations": {
        "supporting_claim_relation_ids", "supporting_claim_ids",
        "correction_evidence_claim_ids",
    },
    "viewpoint_identity_candidates": {"candidate_claim_ids", "seed_relation_ids"},
    "viewpoint_resolution_ledgers": {"claim_id"},
}
SEMANTIC_REFERENCE_COLLECTIONS = set(SEMANTIC_EXTRACTION_REFERENCE_FIELDS)
PACKAGE_REFERENCE_FIELDS = {
    "source_fragments": {"source_id"},
    "questions": {"source_fragment_id", "source_fragment_ids", "answer_claim_ids"},
    "position_nodes": {"source_fragment_ids"},
    "observations": {"source_fragment_id", "source_fragment_ids"},
    "evidence_steps": {
        "source_fragment_id", "source_fragment_ids", "produced_claim_ids",
    },
    "claims": {
        "evidence_step_ids", "opposed_position_ids", "superseded_by",
        "source_id", "evidence_id",
    },
}
# Every registered collection is deliberately classified as extraction,
# current semantic master data, or non-live/history. The test over this set
# forces a new collection to choose before it can silently escape the guard.
NON_LIVE_EXTRACTION_REFERENCE_COLLECTIONS = {
    "argument_routes",
    "canonical_viewpoints",
    "composition_decisions",
    "composition_plans",
    "editorial_checks",
    "impact_events",
    "product_dependencies",
    "tensions",
    "topic_nodes",
    "viewpoint_atomic_quality_reports",
    "viewpoint_automated_promotion_decisions",
    "viewpoint_identity_decisions",
    "viewpoint_proposition_unit_links",
    "viewpoint_quality_reports",
    "viewpoint_revisions",
    "viewpoint_structures",
}
# These fields preserve history or source lookup identity; they do not assert
# that the referenced extraction object remains live. Every other previously
# unclassified exact object-id occurrence fails closed during retirement so a
# new schema field cannot silently bypass the allowlist above.
NON_LIVE_EXTRACTION_ID_FIELDS = {
    "previous_claim_id",
    "transcript_id",
}
REVIEW_FIELDS = {
    "review_status",
    "reviewed_at",
    "reviewed_by",
    "review_note",
    "revision",
    "visibility",
}
SOURCE_KEYS = {
    "source_documents": "source_documents",
    "source_fragments": "source_fragments",
    "questions": "questions",
    "observations": "observations",
    "claims": "claims",
    "topic_nodes": "topic_nodes",
    "topic_identity_reconciliations": "topic_identity_reconciliations",
    "evidence_steps": "evidence_steps",
    "knowledge_relations": "knowledge_relations",
    "claim_relations": "claim_relations",
    "claim_relation_constraints": "claim_relation_constraints",
    "position_nodes": "position_nodes",
    "knowledge_routes": "knowledge_routes",
    "product_dependencies": "product_dependencies",
    "impact_events": "impact_events",
    "editorial_syntheses": "cross_source_syntheses",
    "editorial_checks": "editorial_checks",
    "tensions": "tensions",
    "viewpoint_coverage_snapshots": "viewpoint_coverage_snapshots",
    "canonical_viewpoints": "canonical_viewpoints",
    "viewpoint_revisions": "viewpoint_revisions",
    "viewpoint_claim_links": "viewpoint_claim_links",
    "viewpoint_proposition_units": "viewpoint_proposition_units",
    "viewpoint_proposition_unit_links": "viewpoint_proposition_unit_links",
    "viewpoint_atomic_coverage_snapshots": "viewpoint_atomic_coverage_snapshots",
    "viewpoint_atomic_resolution_ledgers": "viewpoint_atomic_resolution_ledgers",
    "viewpoint_atomic_quality_reports": "viewpoint_atomic_quality_reports",
    "viewpoint_automated_promotion_decisions": "viewpoint_automated_promotion_decisions",
    "argument_routes": "argument_routes",
    "argument_route_revisions": "argument_route_revisions",
    "argument_route_attestations": "argument_route_attestations",
    "viewpoint_relations": "viewpoint_relations",
    "viewpoint_identity_candidates": "viewpoint_identity_candidates",
    "viewpoint_identity_decisions": "viewpoint_identity_decisions",
    "viewpoint_resolution_ledgers": "viewpoint_resolution_ledgers",
    "viewpoint_quality_reports": "viewpoint_quality_reports",
}


class PostgresKnowledgeStoreError(RuntimeError):
    pass


class ChangeSetConflict(PostgresKnowledgeStoreError):
    pass


class ActiveSnapshotBlocked(PostgresKnowledgeStoreError):
    """The authoring store contains no safely publishable active projection."""

    def __init__(self, findings: list[dict[str, Any]]):
        self.findings = findings
        super().__init__("Active Snapshot publication was blocked by validation findings")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def record_content_sha(payload: Mapping[str, Any]) -> str:
    """Hash semantic record content while keeping revision as store metadata."""
    semantic = dict(payload)
    semantic.pop("revision", None)
    return sha256_json(semantic)


ACTIVE_ANCHOR_STATES = {
    "source_version_bound",
    "canonical_citation_bound",
    "verified",
    "valid",
}
ACTIVE_EVIDENCE_STATES = {"eligible", "eligible_with_label"}


def build_active_snapshot(
    package: Mapping[str, Any], *, build_id: Optional[str] = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Create the strict, approved-only read projection from an authoring package.

    Human approval applies to semantic assertions. Source documents, fragments,
    and evidence are included transitively as dependencies, but only after their
    mechanical attribution and anchor gates pass.
    """
    findings: list[dict[str, Any]] = []
    claims = {
        str(item["claim_id"]): dict(item)
        for item in package.get("claims", [])
        if item.get("review_status") == "approved"
    }
    evidence = {
        str(item["evidence_step_id"]): dict(item)
        for item in package.get("evidence_steps", [])
    }
    fragments = {
        str(item["fragment_id"]): dict(item)
        for item in package.get("source_fragments", [])
    }
    sources = {
        str(item["source_id"]): dict(item)
        for item in package.get("source_documents", [])
    }

    active_evidence: dict[str, dict[str, Any]] = {}
    active_fragments: dict[str, dict[str, Any]] = {}
    active_sources: dict[str, dict[str, Any]] = {}
    valid_claim_ids: set[str] = set()
    for claim_id, claim in claims.items():
        usable = 0
        for evidence_id in claim.get("evidence_step_ids", []):
            step = evidence.get(str(evidence_id))
            if not step or step.get("support_eligibility") not in ACTIVE_EVIDENCE_STATES:
                continue
            fragment_id = str(step.get("source_fragment_id") or "")
            fragment = fragments.get(fragment_id)
            if not fragment or fragment.get("anchor_state") not in ACTIVE_ANCHOR_STATES:
                findings.append(
                    {
                        "severity": "error",
                        "code": "approved_claim_has_unbound_evidence",
                        "claim_id": claim_id,
                        "evidence_step_id": evidence_id,
                        "source_fragment_id": fragment_id or None,
                    }
                )
                continue
            source_id = str(fragment.get("source_id") or "")
            source = sources.get(source_id)
            if not source:
                findings.append(
                    {
                        "severity": "error",
                        "code": "approved_claim_source_missing",
                        "claim_id": claim_id,
                        "source_fragment_id": fragment_id,
                        "source_id": source_id or None,
                    }
                )
                continue
            active_evidence[str(evidence_id)] = step
            active_fragments[fragment_id] = fragment
            active_sources[source_id] = source
            usable += 1
        if usable:
            valid_claim_ids.add(claim_id)
        else:
            findings.append(
                {
                    "severity": "error",
                    "code": "approved_claim_without_publishable_evidence",
                    "claim_id": claim_id,
                }
            )

    active_claims = [claims[item] for item in sorted(valid_claim_ids)]
    position_nodes = {
        str(item["position_id"]): dict(item)
        for item in package.get("position_nodes", [])
        if item.get("review_status") == "approved"
    }
    active_node_ids = valid_claim_ids | set(position_nodes)

    def approved_edges(key: str, id_key: str) -> list[dict[str, Any]]:
        rows = []
        for item in package.get(key, []):
            if item.get("review_status") != "approved":
                continue
            left = str(item.get("from_id") or item.get("source_id") or "")
            right = str(item.get("to_id") or item.get("target_id") or "")
            if left in active_node_ids and right in active_node_ids:
                rows.append(dict(item))
            else:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "approved_relation_endpoint_not_active",
                        "relation_id": item.get(id_key),
                        "from_id": left,
                        "to_id": right,
                    }
                )
        return rows

    active_topics = [
        dict(item)
        for item in package.get("topic_nodes", [])
        if item.get("review_status") == "approved"
    ]
    active_topic_ids = {str(item["topic_id"]) for item in active_topics}
    active_routes = [
        dict(item)
        for item in package.get("knowledge_routes", [])
        if item.get("review_status") == "approved"
        and str(item.get("claim_id")) in valid_claim_ids
        and all(str(topic_id) in active_topic_ids for topic_id in item.get("canonical_topic_ids", []))
    ]
    active_questions = [
        dict(item)
        for item in package.get("questions", [])
        if item.get("review_status") == "approved"
        and set(map(str, item.get("answer_claim_ids", []))).issubset(valid_claim_ids)
    ]

    active_plans = []
    for plan in package.get("product_plans", []):
        if plan.get("review_status") != "approved":
            continue
        row = dict(plan)
        row["decisions"] = [
            dict(item)
            for item in plan.get("decisions", [])
            if item.get("review_status") == "approved"
            and set(map(str, item.get("claim_ids", []))).issubset(valid_claim_ids)
        ]
        active_plans.append(row)

    active_dependencies = [
        dict(item)
        for item in package.get("product_dependencies", [])
        if item.get("status", "current") == "current"
        and str(item.get("claim_id")) in valid_claim_ids
    ]
    unresolved_dependency_ids = [
        item.get("dependency_id")
        for item in package.get("product_dependencies", [])
        if item.get("status", "current") != "current"
        and str(item.get("claim_id")) in valid_claim_ids
    ]
    if unresolved_dependency_ids:
        findings.append(
            {
                "severity": "error",
                "code": "approved_claim_has_invalidated_product_dependency",
                "dependency_ids": unresolved_dependency_ids,
            }
        )

    now = datetime.now(timezone.utc)
    snapshot: dict[str, Any] = {
        "schema_version": "wang_active_knowledge_snapshot_v1",
        "build_id": build_id or f"ACTIVE-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        "generated_at": now.isoformat(),
        "authority": "postgresql_authoring_store",
        "publication_policy": {
            "semantic_records": "approved_only",
            "source_dependencies": "transitively_included_after_anchor_and_attribution_gates",
            "invalidated_dependencies": "blocking",
        },
        "source_documents": [active_sources[item] for item in sorted(active_sources)],
        "source_fragments": [active_fragments[item] for item in sorted(active_fragments)],
        "questions": active_questions,
        "observations": [],
        "claims": active_claims,
        "topic_nodes": active_topics,
        "evidence_steps": [active_evidence[item] for item in sorted(active_evidence)],
        "knowledge_relations": approved_edges("knowledge_relations", "relation_id"),
        "claim_relations": approved_edges("claim_relations", "claim_relation_id"),
        "claim_relation_constraints": [
            dict(item)
            for item in package.get("claim_relation_constraints", [])
            if item.get("review_status") == "approved"
            and str(item.get("source_id")) in valid_claim_ids
            and str(item.get("target_id")) in valid_claim_ids
        ],
        "position_nodes": [position_nodes[item] for item in sorted(position_nodes)],
        "knowledge_routes": active_routes,
        "product_dependencies": active_dependencies,
        "impact_events": [],
        "cross_source_syntheses": [
            dict(item)
            for item in package.get("cross_source_syntheses", [])
            if item.get("review_status") == "approved"
            and set(map(str, item.get("claim_ids", []))).issubset(valid_claim_ids)
        ],
        "product_plans": active_plans,
        "editorial_checks": [],
        "tensions": [],
    }
    snapshot["summary"] = {
        "counts": {
            key: len(value)
            for key, value in snapshot.items()
            if isinstance(value, list)
        },
        "findings": findings,
    }
    return snapshot, findings


def database_url_from_env(explicit: Optional[str] = None) -> str:
    value = explicit or os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not value:
        raise PostgresKnowledgeStoreError(
            "Set KNOWLEDGE_DATABASE_URL (preferred) or DATABASE_URL."
        )
    return value


def _load_psycopg() -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise PostgresKnowledgeStoreError(
            "PostgreSQL support requires psycopg>=3.1. Install backend requirements."
        ) from exc
    return psycopg


def _record_id(collection: str, payload: Mapping[str, Any]) -> str:
    _, id_field = KNOWLEDGE_COLLECTIONS[collection]
    return str(payload[id_field])


def _normalize_records(
    payload: Mapping[str, Any],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[tuple[str, str], frozenset[str]]]:
    """Flatten a package, keeping which fields each record actually stated.

    The dumped row cannot answer that on its own: every declared field appears
    in it, so a package that never mentioned `project_id` is byte for byte a
    package that set it to null. Pydantic keeps the difference in
    `model_fields_set` -- extras included, aliases resolved to the field name
    the dump uses -- and it is the only place the difference survives, so it is
    read here and carried alongside the row.
    """

    records = KnowledgePackageImporter._model_records(dict(payload))
    normalized: dict[str, dict[str, dict[str, Any]]] = {}
    stated: dict[tuple[str, str], frozenset[str]] = {}
    id_owners: dict[str, str] = {}
    source_identity_owners: dict[tuple[str, str], str] = {}
    for collection, values in records.items():
        normalized[collection] = {}
        for value in values:
            row = value.model_dump(mode="json")
            record_id = _record_id(collection, row)
            if record_id in normalized[collection]:
                raise PostgresKnowledgeStoreError(
                    f"Duplicate record {collection}/{record_id} in package"
                )
            prior_collection = id_owners.setdefault(record_id, collection)
            if prior_collection != collection:
                raise PostgresKnowledgeStoreError(
                    "Record IDs are globally unique; "
                    f"{record_id!r} appears in both {prior_collection} and {collection}"
                )
            if collection == "source_documents":
                identity = (
                    str(row.get("source_type") or "").strip(),
                    str(row.get("transcript_id") or record_id).strip(),
                )
                prior_source = source_identity_owners.setdefault(identity, record_id)
                if prior_source != record_id:
                    raise PostgresKnowledgeStoreError(
                        "Current SourceDocument identity is unique; "
                        f"{identity!r} is declared by both {prior_source!r} "
                        f"and {record_id!r}"
                    )
            normalized[collection][record_id] = row
            stated[(collection, record_id)] = frozenset(value.model_fields_set)
    return normalized, stated


def normalize_package(payload: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate record shapes and flatten a package into collection/id records."""
    return _normalize_records(payload)[0]


def preserve_human_review(
    incoming: Mapping[str, Any], existing: Optional[Mapping[str, Any]]
) -> dict[str, Any]:
    result = dict(incoming)
    # An explicit owner ruling is allowed to promote a record that was
    # previously system-approved.  The old blanket preservation rule kept the
    # system status and made the ruling provenance contradict the status that
    # answers "who approved this?".  Lower-authority reimports still cannot
    # erase an existing reviewed decision below.
    if result.get("review_status") == "human_approved":
        return result
    if not existing or existing.get("review_status", "candidate") == "candidate":
        return result
    for field in REVIEW_FIELDS:
        if field in existing:
            result[field] = existing[field]
    return result


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def merge_over_existing(
    incoming: Mapping[str, Any],
    existing: Optional[Mapping[str, Any]],
    stated: frozenset[str],
) -> dict[str, Any]:
    """Apply the fields a package stated; leave every field it did not alone.

    An update used to be a replacement, so any field the incoming package
    omitted was overwritten to whatever the schema says when nobody speaks --
    null, or the declared default. Nobody ever asked for that: across the whole
    of `object_versions` it took out 511 field values on 194 objects, and
    the only two change sets that ever reversed one are named
    `AUTHORING-CONTRACT-MIGRATION-RESTORE` and `RESTORE-NOTES-PROVENANCE` --
    repairs, applied 98 seconds and 15 minutes after the damage. There is no
    change set anywhere in the store whose purpose was to clear a field by
    leaving it out, so nothing depends on omission meaning erasure.

    It could not have been asked for, either: what an extraction omits is
    decided by what extraction knows, not by what should stop being true. One
    re-extraction erased `source_id` and `target_id` from nine claim relations
    -- both endpoints of the edge -- and `source_fragment_id` from thirty-nine
    evidence steps, because a re-extraction of one lecture has no opinion about
    a cross-lecture edge and never claimed to.

    So omission means "unchanged" and deletion has to be written down: state
    the field as null. That is still a removal, and `fields_removed` names it.
    """

    if not existing:
        return dict(incoming)
    merged = dict(existing)
    merged.update({field: incoming[field] for field in stated if field in incoming})
    return merged


def fields_removed(
    existing: Optional[Mapping[str, Any]], final: Mapping[str, Any]
) -> tuple[str, ...]:
    """Name the fields this update takes away, once the payload is settled.

    Read after review preservation rather than before it: a package that
    blanks `review_note` on an approved record has it put back, and reporting
    a removal that did not happen teaches whoever reads these to ignore them.
    """

    if not existing:
        return ()
    return tuple(
        sorted(
            field
            for field, value in existing.items()
            if not _is_empty(value) and _is_empty(final.get(field))
        )
    )


@dataclass(frozen=True)
class ChangeOperation:
    operation: str
    collection: str
    object_id: str
    before_sha256: Optional[str]
    after_sha256: str
    before_revision: Optional[int]
    after_revision: int
    payload: dict[str, Any]
    # Which fields this update takes away. `before_sha256`/`after_sha256` say
    # that something changed and nothing more, which is why 511 field values
    # went out of the store unnoticed until a manuscript vanished from every
    # scripture-grouped view and someone spotted it by eye.
    removed_fields: tuple[str, ...] = ()


def stored_operation_payload(operation: ChangeOperation) -> dict[str, Any]:
    """Return the exact payload persisted for one ChangeSet operation.

    A package and a ChangeOperation deliberately omit the store-assigned
    revision from semantic hashing.  The write boundary adds it.  Keeping that
    transformation in one helper lets strict-model tests exercise the same
    bytes the apply path writes instead of approximating the round trip.
    """

    payload = dict(operation.payload)
    payload["revision"] = operation.after_revision
    return payload


def operation_fingerprint_rows(
    operations: Sequence[ChangeOperation],
) -> list[dict[str, Any]]:
    """Bind ChangeSet identity to the exact store snapshot it intends to change."""

    return [
        {
            "operation": row.operation,
            "collection": row.collection,
            "object_id": row.object_id,
            "before_sha256": row.before_sha256,
            "after_sha256": row.after_sha256,
            "before_revision": row.before_revision,
            "after_revision": row.after_revision,
        }
        for row in operations
    ]


@dataclass(frozen=True)
class ChangeSetPlan:
    change_set_id: str
    fingerprint_sha256: str
    package_id: str
    source_kind: str
    source_sha256: str
    operations: tuple[ChangeOperation, ...]
    unchanged: int
    ignored_keys: tuple[str, ...]

    @property
    def removals(self) -> tuple[dict[str, Any], ...]:
        """Every field this change set takes away, named, before it is applied.

        A caller that plans without applying can read this and refuse; the same
        list goes into the change set summary and into each operation's
        `details`, so the answer to "what did that ingest remove" exists in the
        store afterwards instead of only in whatever scrollback is still open.
        """

        return tuple(
            {
                "collection": item.collection,
                "object_id": item.object_id,
                "fields": list(item.removed_fields),
            }
            for item in self.operations
            if item.removed_fields
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        removals = self.removals
        value["summary"] = {
            "created": sum(item.operation == "create" for item in self.operations),
            "updated": sum(item.operation == "update" for item in self.operations),
            "retired": sum(item.operation == "retire" for item in self.operations),
            "revived": sum(item.operation == "revive" for item in self.operations),
            "unchanged": self.unchanged,
            "operations": len(self.operations),
            "fields_removed": sum(len(item["fields"]) for item in removals),
            "removals": [dict(item) for item in removals],
        }
        return value


def build_change_set_plan(
    package: Mapping[str, Any],
    existing: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    source_kind: str = "knowledge_package",
) -> ChangeSetPlan:
    normalized, stated = _normalize_records(package)
    # The generic JSONB tables deliberately accept new collections without a
    # DDL migration, but viewpoint master data has cross-record invariants the
    # shape validator cannot see.  Refuse the ChangeSet before it receives an
    # id or touches PostgreSQL.
    from .viewpoint_foundation import validate_foundation_change_set

    validate_foundation_change_set(normalized, existing)
    operations: list[ChangeOperation] = []
    unchanged = 0
    for collection in sorted(normalized):
        for object_id in sorted(normalized[collection]):
            current = existing.get((collection, object_id))
            current_payload = (current or {}).get("payload")
            merged = merge_over_existing(
                normalized[collection][object_id],
                current_payload,
                stated[(collection, object_id)],
            )
            if collection == "source_documents" and current_payload:
                incoming_identity = (
                    str(merged.get("source_type") or "").strip(),
                    str(merged.get("transcript_id") or object_id).strip(),
                )
                current_identity = (
                    str(current_payload.get("source_type") or "").strip(),
                    str(current_payload.get("transcript_id") or object_id).strip(),
                )
                completing_legacy_type = (
                    not current_identity[0]
                    and bool(incoming_identity[0])
                    and incoming_identity[1] == current_identity[1]
                )
                if incoming_identity != current_identity and not completing_legacy_type:
                    raise PostgresKnowledgeStoreError(
                        f"SourceDocument id {object_id!r} cannot change identity "
                        f"from {current_identity!r} to {incoming_identity!r}"
                    )
            incoming = preserve_human_review(merged, current_payload)
            removed_fields = fields_removed(current_payload, incoming)
            after_sha = record_content_sha(incoming)
            before_sha = str((current or {}).get("content_sha256") or "") or None
            if before_sha == after_sha:
                unchanged += 1
                continue
            before_revision = int(current["revision"]) if current else None
            operations.append(
                ChangeOperation(
                    operation="update" if current else "create",
                    collection=collection,
                    object_id=object_id,
                    before_sha256=before_sha,
                    after_sha256=after_sha,
                    before_revision=before_revision,
                    after_revision=(before_revision or 0) + 1,
                    payload=incoming,
                    removed_fields=removed_fields,
                )
            )

    source_sha = sha256_json(package)
    fingerprint_payload = {
        "planner_schema": "wang_postgres_changeset_v2",
        "source_kind": source_kind,
        "source_sha256": source_sha,
        "package_id": str(package.get("package_id") or ""),
        "operations": operation_fingerprint_rows(operations),
    }
    fingerprint = sha256_json(fingerprint_payload)
    recognized = set(KnowledgePackageImporter.SOURCE_COLLECTION_KEYS) | {
        "product_plans", "schema_version", "package_id", "title", "corpus_scope",
        "framework_candidate", "validation_experiments", "summary", "batch",
        "candidate_generation", "lineage", "approval_status",
    }
    ignored = tuple(sorted(set(package) - recognized))
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id=str(package.get("package_id") or f"PACKAGE-{source_sha[:12]}"),
        source_kind=source_kind,
        source_sha256=source_sha,
        operations=tuple(operations),
        unchanged=unchanged,
        ignored_keys=ignored,
    )


def build_retirement_plan(
    keys: Sequence[tuple[str, str]],
    existing: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    reason: str,
    package_id: str,
    source_kind: str = "retirement",
) -> ChangeSetPlan:
    """Plan the withdrawal of records that should no longer stand.

    A retirement leaves the payload byte for byte as it was. What is being
    withdrawn is the store's assertion that this record is current, not the
    record of what was once extracted -- rewriting the payload to say
    "retired" would edit the evidence to record a decision about it. So
    `after_sha256` equals `before_sha256` here, deliberately, and the reason
    lives on the change set where the rest of the provenance already lives.

    Keys already absent or already retired are skipped rather than refused:
    running a retirement twice must not be an error, or nobody will dare run
    it once.
    """

    operations: list[ChangeOperation] = []
    skipped = 0
    for collection, object_id in keys:
        current = existing.get((collection, object_id))
        if not current or current.get("retired_at") is not None:
            skipped += 1
            continue
        before_sha = str(current.get("content_sha256") or "")
        before_revision = int(current["revision"])
        operations.append(
            ChangeOperation(
                operation="retire",
                collection=collection,
                object_id=object_id,
                before_sha256=before_sha,
                after_sha256=before_sha,
                before_revision=before_revision,
                after_revision=before_revision + 1,
                payload=dict(current.get("payload") or {}),
            )
        )
    fingerprint = sha256_json({
        "planner_schema": "wang_postgres_retirement_v2",
        "source_kind": source_kind,
        "reason": reason,
        "keys": [list(key) for key in sorted({(c, o) for c, o in keys})],
        "operations": operation_fingerprint_rows(operations),
    })
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id=package_id,
        source_kind=source_kind,
        source_sha256=fingerprint,
        operations=tuple(operations),
        unchanged=skipped,
        ignored_keys=(),
    )


def build_revival_plan(
    keys: Sequence[tuple[str, str]],
    retired: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    reason: str,
    package_id: str,
    source_kind: str = "revival",
) -> ChangeSetPlan:
    """Put back records that were withdrawn on evidence that did not hold.

    Retirement is a judgement and judgements are sometimes wrong: one fragment
    was retired because a short excerpt happened to occur inside a deleted span
    somewhere in its source, while the occurrence its anchor meant survived.
    Without a way back the only remedies are editing rows behind the history
    tables' back, or leaving a true record withdrawn -- so there is a way back,
    and it is recorded like everything else.

    Like a retirement it leaves the payload untouched; what changes is the
    store's assertion about the record, not the record.
    """

    operations: list[ChangeOperation] = []
    skipped = 0
    for collection, object_id in keys:
        current = retired.get((collection, object_id))
        if not current:
            skipped += 1
            continue
        before_sha = str(current.get("content_sha256") or "")
        before_revision = int(current["revision"])
        operations.append(
            ChangeOperation(
                operation="revive",
                collection=collection,
                object_id=object_id,
                before_sha256=before_sha,
                after_sha256=before_sha,
                before_revision=before_revision,
                after_revision=before_revision + 1,
                payload=dict(current.get("payload") or {}),
            )
        )
    fingerprint = sha256_json({
        "planner_schema": "wang_postgres_revival_v2",
        "source_kind": source_kind,
        "reason": reason,
        "keys": [list(key) for key in sorted({(c, o) for c, o in keys})],
        "operations": operation_fingerprint_rows(operations),
    })
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id=package_id,
        source_kind=source_kind,
        source_sha256=fingerprint,
        operations=tuple(operations),
        unchanged=skipped,
        ignored_keys=(),
    )


def combined_plan(arrival: ChangeSetPlan, withdrawal: ChangeSetPlan) -> ChangeSetPlan:
    """One change set that lands a package and retires what it replaces.

    Two change sets would leave a window in which the store holds both
    extractions, or neither, and nothing to say which state it is in. The
    fingerprint covers both halves, so re-running the same arrival against the
    same predecessor plans the same change set and applies once.

    Withdrawals come first inside the transaction. The withdrawal is built to
    exclude every id the package carries, so the halves never touch the same
    row; retiring an old SourceDocument alias first also lets the database's
    current-transcript uniqueness constraint admit its replacement without an
    observable gap outside the transaction.
    """

    fingerprint = sha256_json({
        "planner_schema": "wang_postgres_arrival_with_withdrawal_v2",
        "arrival": arrival.fingerprint_sha256,
        "withdrawal": withdrawal.fingerprint_sha256,
        "operations": operation_fingerprint_rows(
            withdrawal.operations + arrival.operations
        ),
    })
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id=arrival.package_id,
        source_kind=arrival.source_kind,
        source_sha256=arrival.source_sha256,
        operations=withdrawal.operations + arrival.operations,
        unchanged=arrival.unchanged + withdrawal.unchanged,
        ignored_keys=arrival.ignored_keys,
    )


def conflict_for(
    collection: str, object_id: str, *, expected: Optional[str], found: Optional[str],
    retired_at: Optional[datetime],
) -> ChangeSetConflict:
    """Name the reason the store refused this write.

    A retired object arrives here looking exactly like a concurrent one: the
    planner reads only live rows, so it plans a `create` with no
    `before_sha256`, while the row is still there with a hash. Reporting that
    as "concurrent change" sends whoever hit it looking for another writer
    instead of for the retirement, which is the answer.
    """

    if retired_at is not None:
        return ChangeSetConflict(
            f"{collection}/{object_id} was retired at {retired_at:%Y-%m-%d %H:%M:%S%z}; "
            "re-ingesting the package that produced it would bring it back. Withdraw the "
            "retirement deliberately, or take this record out of the package."
        )
    return ChangeSetConflict(
        f"Concurrent change for {collection}/{object_id}: expected {expected}, found {found}"
    )


def reviewed_relations_package(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Convert accepted cross-sermon judgments to an incremental package.

    Positive relations become ClaimRelation records. `unrelated` judgments are
    retained as negative constraints so a later model cannot silently recreate
    a rejected merge/support edge.
    """
    result = artifact.get("result") or artifact
    relations: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    reviewed_rows = [
        *result.get("reviewed_relations", []),
        *result.get("negative_comparisons", []),
    ]
    for row in reviewed_rows:
        if row.get("review_status") not in {"ai_consensus", "approved"}:
            continue
        review_status = str(row["review_status"])
        candidate_id = str(row["candidate_id"])
        relation_type = str(row["relation_type"])
        if relation_type == "unrelated":
            constraints.append(
                {
                    "constraint_id": f"CRC-{candidate_id}",
                    "source_id": row["source_claim_id"],
                    "target_id": row["target_claim_id"],
                    "forbidden_relation_types": [
                        "duplicate", "supports", "extends", "qualifies", "supersedes"
                    ],
                    "bidirectional": True,
                    "reason": row.get("reason", ""),
                    "review_status": review_status,
                    "review_artifact_id": candidate_id,
                }
            )
        else:
            relations.append(
                {
                    "claim_relation_id": f"CR-{candidate_id}",
                    "source_id": row["source_claim_id"],
                    "target_id": row["target_claim_id"],
                    "relation_type": relation_type,
                    "reason": row.get("reason", ""),
                    "review_status": review_status,
                    "confidence": row.get("confidence"),
                    "source_evidence_step_ids": row.get("source_evidence_step_ids", []),
                    "target_evidence_step_ids": row.get("target_evidence_step_ids", []),
                    "review_artifact_id": candidate_id,
                }
            )
    digest = sha256_json(artifact)
    return {
        "schema_version": "wang_shared_knowledge_increment_v1",
        "package_id": f"XSR-CONSENSUS-{digest[:16]}",
        "claim_relations": relations,
        "claim_relation_constraints": constraints,
    }


class PostgresKnowledgeStore:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url_from_env(database_url)
        self.psycopg = _load_psycopg()

    def connect(self) -> Any:
        return self.psycopg.connect(self.database_url)

    def get_record(self, collection: str, object_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT payload FROM wang_knowledge.objects
                   WHERE collection=%s AND object_id=%s AND retired_at IS NULL""",
                (collection, object_id),
            )
            row = cursor.fetchone()
        return dict(row[0]) if row else None

    def list_records(self, collection: str) -> list[dict[str, Any]]:
        """Read the active collection in stable object-id order."""

        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT payload FROM wang_knowledge.objects
                   WHERE collection=%s AND retired_at IS NULL ORDER BY object_id""",
                (collection,),
            )
            rows = cursor.fetchall()
        return [dict(row[0]) for row in rows]

    def list_change_set_object_ids(
        self, change_set_ids: Sequence[str], collection: str
    ) -> list[str]:
        """Return the exact object denominator written by explicit ChangeSets."""

        if not change_set_ids:
            return []
        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT DISTINCT object_id FROM wang_knowledge.change_operations
                   WHERE change_set_id = ANY(%s) AND collection=%s
                     AND operation IN ('create', 'update', 'revive')
                   ORDER BY object_id""",
                (list(change_set_ids), collection),
            )
            rows = cursor.fetchall()
        return [str(row[0]) for row in rows]

    def list_change_set_states(
        self, change_set_ids: Sequence[str]
    ) -> list[dict[str, str]]:
        """Return immutable identity and terminal state for an explicit cohort."""

        if not change_set_ids:
            return []
        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT change_set_id, fingerprint_sha256, status
                   FROM wang_knowledge.change_sets
                   WHERE change_set_id = ANY(%s)
                   ORDER BY change_set_id""",
                (list(change_set_ids),),
            )
            rows = cursor.fetchall()
        return [
            {
                "change_set_id": str(row[0]),
                "fingerprint_sha256": str(row[1]),
                "status": str(row[2]),
            }
            for row in rows
        ]

    def get_plan_document(self, plan_id: str) -> Optional[dict[str, Any]]:
        """A CompositionPlan with its decisions inlined.

        The store keeps a plan and its decisions as separate objects, but every
        consumer -- the authoring packet builder, the composition review, an
        exported file -- wants them as one document. Assembling it here keeps
        one definition of what "the plan" is.
        """

        plan = self.get_record("composition_plans", plan_id)
        if plan is None:
            return None
        decisions = []
        for decision_id in plan.get("decision_ids") or []:
            decision = self.get_record("composition_decisions", decision_id)
            if decision is None:
                raise KeyError(
                    f"decision {decision_id} referenced by {plan_id} is not in the store"
                )
            decisions.append(decision)
        return {**plan, "decisions": decisions}

    def migrate(self) -> list[str]:
        applied: list[str] = []
        with self.connect() as conn:
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                with conn.cursor() as cursor:
                    cursor.execute(path.read_text(encoding="utf-8"))
                applied.append(path.name)
        return applied

    def _existing(self, conn: Any, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        with conn.cursor() as cursor:
            for collection, object_id in keys:
                cursor.execute(
                    """SELECT revision, content_sha256, payload
                       FROM wang_knowledge.objects
                       WHERE collection=%s AND object_id=%s AND retired_at IS NULL""",
                    (collection, object_id),
                )
                row = cursor.fetchone()
                if row:
                    result[(collection, object_id)] = {
                        "revision": row[0], "content_sha256": row[1], "payload": row[2]
                    }
        return result

    def _existing_collections(
        self, conn: Any, collections: Iterable[str]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        selected = sorted(set(collections))
        if not selected:
            return {}
        with conn.cursor() as cursor:
            cursor.execute(
                """SELECT collection, object_id, revision, content_sha256, payload
                   FROM wang_knowledge.objects
                   WHERE collection = ANY(%s) AND retired_at IS NULL""",
                (selected,),
            )
            rows = cursor.fetchall()
        return {
            (str(row[0]), str(row[1])): {
                "revision": row[2],
                "content_sha256": row[3],
                "payload": row[4],
            }
            for row in rows
        }

    def plan_package(
        self,
        package: Mapping[str, Any],
        *,
        source_kind: str = "knowledge_package",
        retiring_keys: Sequence[tuple[str, str]] = (),
    ) -> ChangeSetPlan:
        """Plan one package, optionally withdrawing records it makes obsolete.

        `retiring_keys` is not a convenience for issuing two change sets. The
        cross-record validator runs inside this call against package plus store,
        so a record the package strands -- an attestation whose route revision
        the package supersedes -- has to be gone from that picture, or the
        package is refused for a state neither half intends to leave behind.
        Retiring afterwards is too late: nothing was ever planned.
        """

        normalized = normalize_package(package)
        keys = [
            (collection, object_id)
            for collection, rows in normalized.items()
            for object_id in rows
        ]
        with self.connect() as conn:
            # The schema's global unique index is the final concurrency guard;
            # this read gives a useful fail-closed error before a plan is made
            # and also protects installations until migration 005 is applied.
            incoming_owner = {object_id: collection for collection, object_id in keys}
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT collection, object_id FROM wang_knowledge.objects
                       WHERE object_id = ANY(%s)""",
                    (sorted(incoming_owner),),
                )
                for existing_collection, object_id in cursor.fetchall():
                    expected_collection = incoming_owner[str(object_id)]
                    if str(existing_collection) != expected_collection:
                        raise PostgresKnowledgeStoreError(
                            "Record IDs are globally unique; "
                            f"{object_id!r} already belongs to {existing_collection}, "
                            f"not {expected_collection}"
                        )
            existing = self._existing(conn, keys)
            if any(
                collection in VIEWPOINT_VALIDATION_COLLECTIONS - {
                    "source_documents", "source_fragments", "claims",
                    "evidence_steps", "claim_relations",
                }
                for collection in normalized
            ):
                existing.update(
                    self._existing_collections(conn, VIEWPOINT_VALIDATION_COLLECTIONS)
                )
            withdrawal = (
                build_retirement_plan(
                    list(retiring_keys),
                    self._existing(conn, list(retiring_keys)),
                    reason="superseded by the package planned in the same change set",
                    package_id=str(package.get("package_id") or ""),
                    source_kind=source_kind,
                )
                if retiring_keys
                else None
            )
        arrival = build_change_set_plan(
            package,
            {key: row for key, row in existing.items() if key not in set(retiring_keys)},
            source_kind=source_kind,
        )
        return combined_plan(arrival, withdrawal) if withdrawal else arrival

    def plan_retirement(
        self, keys: Sequence[tuple[str, str]], *, reason: str, package_id: str,
        source_kind: str = "retirement",
    ) -> ChangeSetPlan:
        with self.connect() as conn:
            existing = self._existing(conn, keys)
        return build_retirement_plan(
            keys, existing, reason=reason, package_id=package_id, source_kind=source_kind
        )

    def retire_objects(
        self, keys: Sequence[tuple[str, str]], *, reason: str, package_id: str,
        source_kind: str = "retirement", apply: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        plan = self.plan_retirement(
            keys, reason=reason, package_id=package_id, source_kind=source_kind
        )
        if not apply:
            return {"status": "planned", **plan.as_dict()}
        return self.apply_plan(plan, metadata={**(metadata or {}), "reason": reason})

    @staticmethod
    def _edge_values(collection: str, payload: Mapping[str, Any]) -> tuple[str, str, str]:
        if collection == "claim_relation_constraints":
            return str(payload["source_id"]), str(payload["target_id"]), "forbids"
        if collection == "viewpoint_claim_links":
            return (
                str(payload["viewpoint_id"]),
                str(payload["claim_id"]),
                str(payload["link_type"]),
            )
        if collection == "viewpoint_proposition_unit_links":
            return (
                str(payload["viewpoint_id"]),
                str(payload["proposition_unit_id"]),
                str(payload["link_type"]),
            )
        if collection == "viewpoint_relations":
            return (
                str(payload["source_viewpoint_id"]),
                str(payload["target_viewpoint_id"]),
                str(payload["relation_type"]),
            )
        return (
            str(
                payload.get("from_id")
                or payload.get("source_id")
                or payload.get("from_claim_id")
                or ""
            ),
            str(
                payload.get("to_id")
                or payload.get("target_id")
                or payload.get("to_claim_id")
                or ""
            ),
            str(payload["relation_type"]),
        )

    @staticmethod
    def _assert_current_viewpoint_revisions(
        cursor: Any, expected: Mapping[str, str]
    ) -> None:
        """Lock conclusions and perform the Route apply compare-and-swap."""

        for viewpoint_id, revision_id in sorted(expected.items()):
            cursor.execute(
                """SELECT payload->>'current_revision_id'
                   FROM wang_knowledge.objects
                   WHERE collection='canonical_viewpoints' AND object_id=%s
                     AND retired_at IS NULL FOR UPDATE""",
                (viewpoint_id,),
            )
            row = cursor.fetchone()
            observed = str(row[0]) if row and row[0] is not None else None
            if observed != revision_id:
                raise ChangeSetConflict(
                    f"Route conclusion {viewpoint_id} expected current revision "
                    f"{revision_id}, found {observed or 'missing'}"
                )

    def apply_plan(
        self,
        plan: ChangeSetPlan,
        *,
        metadata: Optional[dict[str, Any]] = None,
        expected_current_viewpoint_revisions: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        if not plan.operations:
            return {
                "status": "unchanged",
                "change_set_id": None,
                "summary": plan.as_dict()["summary"],
            }
        with self.connect() as conn:
            with conn.cursor() as cursor:
                # ChangeSets share cross-record invariants that row locks alone
                # cannot protect (most importantly a new CVR link racing a
                # source re-extraction). Serialize the short apply transaction;
                # model work and planning happen before this lock is taken.
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("wang_knowledge.apply_plan.v1",),
                )
                cursor.execute(
                    "SELECT status, summary FROM wang_knowledge.change_sets WHERE fingerprint_sha256=%s",
                    (plan.fingerprint_sha256,),
                )
                prior = cursor.fetchone()
                if prior and prior[0] == "applied":
                    return {"status": "already_applied", "change_set_id": plan.change_set_id, "summary": prior[1]}

                self._assert_global_id_uniqueness(cursor, plan)
                self._assert_source_identity_uniqueness(cursor, plan)
                self._assert_edge_integrity(cursor, plan)
                self._assert_no_dangling_package_references(cursor, plan)
                self._assert_no_uncoordinated_semantic_references(cursor, plan)

                self._assert_current_viewpoint_revisions(
                    cursor, expected_current_viewpoint_revisions or {}
                )

                summary = plan.as_dict()["summary"]
                cursor.execute(
                    """INSERT INTO wang_knowledge.change_sets
                       (change_set_id, fingerprint_sha256, package_id, source_kind,
                        source_sha256, status, summary, metadata)
                       VALUES (%s,%s,%s,%s,%s,'planned',%s::jsonb,%s::jsonb)""",
                    (
                        plan.change_set_id, plan.fingerprint_sha256, plan.package_id,
                        plan.source_kind, plan.source_sha256, canonical_json(summary),
                        canonical_json(metadata or {}),
                    ),
                )
                changed_records: list[tuple[str, str, int, int]] = []
                for index, operation in enumerate(plan.operations):
                    cursor.execute(
                        """SELECT revision, content_sha256, retired_at FROM wang_knowledge.objects
                           WHERE collection=%s AND object_id=%s FOR UPDATE""",
                        (operation.collection, operation.object_id),
                    )
                    locked = cursor.fetchone()
                    actual_sha = locked[1] if locked else None
                    if actual_sha != operation.before_sha256:
                        raise conflict_for(
                            operation.collection, operation.object_id,
                            expected=operation.before_sha256, found=actual_sha,
                            retired_at=locked[2] if locked else None,
                        )
                    actual_revision = int(locked[0]) if locked else None
                    if actual_revision != operation.before_revision:
                        raise ChangeSetConflict(
                            f"Concurrent revision change for {operation.collection}/"
                            f"{operation.object_id}: expected revision "
                            f"{operation.before_revision}, found {actual_revision}"
                        )
                    if operation.operation in {"retire", "revive"}:
                        self._set_retirement(cursor, plan, index, operation)
                        if operation.before_revision is not None:
                            changed_records.append((
                                operation.collection, operation.object_id,
                                operation.before_revision, operation.after_revision,
                            ))
                        continue
                    payload = stored_operation_payload(operation)
                    content_sha = record_content_sha(payload)
                    review_status = str(payload.get("review_status", "candidate"))
                    visibility = str(payload.get("visibility", "internal"))
                    source_fingerprint = next(
                        iter(payload.get("extraction_fingerprints") or []),
                        payload.get("extraction_fingerprint"),
                    )
                    cursor.execute(
                        """INSERT INTO wang_knowledge.objects
                           (collection, object_id, revision, review_status, visibility,
                            content_sha256, source_fingerprint, payload)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                           ON CONFLICT (collection, object_id) DO UPDATE SET
                             revision=EXCLUDED.revision,
                             review_status=EXCLUDED.review_status,
                             visibility=EXCLUDED.visibility,
                             content_sha256=EXCLUDED.content_sha256,
                             source_fingerprint=EXCLUDED.source_fingerprint,
                             payload=EXCLUDED.payload,
                             updated_at=now(), retired_at=NULL""",
                        (
                            operation.collection, operation.object_id, operation.after_revision,
                            review_status, visibility, content_sha, source_fingerprint,
                            canonical_json(payload),
                        ),
                    )
                    cursor.execute(
                        """INSERT INTO wang_knowledge.object_versions
                           (collection, object_id, revision, content_sha256, payload, change_set_id)
                           VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
                        (
                            operation.collection, operation.object_id, operation.after_revision,
                            content_sha, canonical_json(payload), plan.change_set_id,
                        ),
                    )
                    cursor.execute(
                        """INSERT INTO wang_knowledge.change_operations
                           (change_set_id, operation_index, operation, collection, object_id,
                            before_sha256, after_sha256, before_revision, after_revision,
                            details)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                        (
                            plan.change_set_id, index, operation.operation, operation.collection,
                            operation.object_id, operation.before_sha256, content_sha,
                            operation.before_revision, operation.after_revision,
                            canonical_json(
                                {"removed_fields": list(operation.removed_fields)}
                                if operation.removed_fields
                                else {}
                            ),
                        ),
                    )
                    if operation.collection in EDGE_COLLECTIONS:
                        from_id, to_id, relation_type = self._edge_values(operation.collection, payload)
                        cursor.execute(
                            """INSERT INTO wang_knowledge.edges
                               (edge_collection, edge_id, from_id, to_id, relation_type,
                                review_status, revision, payload)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                               ON CONFLICT (edge_collection, edge_id) DO UPDATE SET
                                 from_id=EXCLUDED.from_id, to_id=EXCLUDED.to_id,
                                 relation_type=EXCLUDED.relation_type,
                                 review_status=EXCLUDED.review_status,
                                 revision=EXCLUDED.revision, payload=EXCLUDED.payload,
                                 updated_at=now(), retired_at=NULL""",
                            (
                                operation.collection, operation.object_id, from_id, to_id,
                                relation_type, review_status, operation.after_revision,
                                canonical_json(payload),
                            ),
                        )
                    if operation.operation == "update":
                        changed_records.append(
                            (operation.collection, operation.object_id,
                             operation.before_revision or 0, operation.after_revision)
                        )

                invalidated = self._invalidate_dependencies(cursor, plan, changed_records, len(plan.operations))
                summary["invalidated_dependencies"] = invalidated
                cursor.execute(
                    """UPDATE wang_knowledge.change_sets
                       SET status='applied', summary=%s::jsonb, applied_at=now()
                       WHERE change_set_id=%s""",
                    (canonical_json(summary), plan.change_set_id),
                )
        return {"status": "applied", "change_set_id": plan.change_set_id, "summary": summary}

    @staticmethod
    def _assert_global_id_uniqueness(cursor: Any, plan: ChangeSetPlan) -> None:
        """Repeat the planning check under the global apply lock.

        Migration 005 is the permanent database backstop, but an installation
        with pre-existing source-identity duplicates cannot install it until
        those rows are repaired. Two plans made before either apply must still
        not create the same object id in different collections meanwhile.
        """

        incoming: dict[str, str] = {}
        for operation in plan.operations:
            if operation.operation == "retire":
                continue
            prior = incoming.setdefault(operation.object_id, operation.collection)
            if prior != operation.collection:
                raise ChangeSetConflict(
                    "Record IDs are globally unique; "
                    f"{operation.object_id!r} arrives in both {prior} "
                    f"and {operation.collection}"
                )
        if not incoming:
            return
        cursor.execute(
            """SELECT collection, object_id FROM wang_knowledge.objects
               WHERE object_id = ANY(%s) FOR UPDATE""",
            (sorted(incoming),),
        )
        conflicts = [
            (str(object_id), str(collection), incoming[str(object_id)])
            for collection, object_id in cursor.fetchall()
            if str(collection) != incoming[str(object_id)]
        ]
        if conflicts:
            object_id, existing, arriving = sorted(conflicts)[0]
            raise ChangeSetConflict(
                "Record IDs are globally unique; "
                f"{object_id!r} already belongs to {existing}, not {arriving}"
            )

    @staticmethod
    def _assert_source_identity_uniqueness(cursor: Any, plan: ChangeSetPlan) -> None:
        """Lock and reject a second current source for one transcript identity."""

        retiring = {
            operation.object_id
            for operation in plan.operations
            if operation.collection == "source_documents"
            and operation.operation == "retire"
        }
        incoming_identities: dict[tuple[str, str], str] = {}
        for operation in plan.operations:
            if operation.collection != "source_documents" or operation.operation == "retire":
                continue
            payload = stored_operation_payload(operation)
            source_type = str(payload.get("source_type") or "").strip()
            transcript_id = str(
                payload.get("transcript_id") or operation.object_id
            ).strip()
            if not source_type:
                raise ChangeSetConflict(
                    f"SourceDocument {operation.object_id} has no stable source_type identity"
                )
            identity = (source_type, transcript_id)
            prior_source = incoming_identities.setdefault(identity, operation.object_id)
            if prior_source != operation.object_id:
                raise ChangeSetConflict(
                    "Multiple incoming SourceDocuments would name "
                    f"{identity}: {prior_source}, {operation.object_id}"
                )
            cursor.execute(
                """SELECT object_id,
                          btrim(COALESCE(payload->>'source_type','')),
                          btrim(COALESCE(NULLIF(payload->>'transcript_id',''), object_id))
                   FROM wang_knowledge.objects
                   WHERE collection='source_documents' AND retired_at IS NULL
                     AND object_id<>%s
                     AND (
                       (
                         btrim(COALESCE(payload->>'source_type',''))=''
                         AND btrim(COALESCE(NULLIF(payload->>'transcript_id',''), object_id))=%s
                       )
                       OR (
                         btrim(payload->>'source_type')=%s
                         AND btrim(COALESCE(NULLIF(payload->>'transcript_id',''), object_id))=%s
                       )
                     )
                   FOR UPDATE""",
                (operation.object_id, transcript_id, source_type, transcript_id),
            )
            conflicts: set[str] = set()
            for object_id, existing_type, _existing_transcript in cursor.fetchall():
                if str(object_id) in retiring:
                    continue
                if not str(existing_type or "").strip():
                    raise ChangeSetConflict(
                        "Cannot prove transcript identity uniqueness while current "
                        f"SourceDocument {object_id!r} has no source_type"
                    )
                conflicts.add(str(object_id))
            if conflicts:
                raise ChangeSetConflict(
                    "Multiple current SourceDocuments would name "
                    f"({source_type}, {transcript_id}): " + ", ".join(sorted(conflicts))
                )

    @classmethod
    def _assert_edge_integrity(cls, cursor: Any, plan: ChangeSetPlan) -> None:
        """Validate every arriving edge against the transaction's final graph.

        Model validators are useful early feedback, but the database write is
        the chokepoint shared by extraction, consensus, CVR and imported
        increments. Recheck here under the global apply lock so no path can
        create a self-edge, dangling endpoint, or the same semantic edge under
        a second identifier.
        """

        retiring_keys = {
            (operation.collection, operation.object_id)
            for operation in plan.operations
            if operation.operation == "retire"
        }
        retiring_ids = {object_id for _, object_id in retiring_keys}
        planned_edge_operations = {
            (operation.collection, operation.object_id): operation
            for operation in plan.operations
            if operation.collection in EDGE_COLLECTIONS
        }
        arriving_owners = {
            operation.object_id: operation.collection
            for operation in plan.operations
            if operation.operation != "retire"
        }
        incoming_edges: list[tuple[str, str, str, str, str]] = []
        incoming_signatures: dict[tuple[str, str, str, str], str] = {}
        endpoint_ids: set[str] = set()
        for operation in plan.operations:
            if operation.collection not in EDGE_COLLECTIONS or operation.operation == "retire":
                continue
            payload = stored_operation_payload(operation)
            from_id, to_id, relation_type = cls._edge_values(
                operation.collection, payload
            )
            if not from_id or not to_id:
                raise ChangeSetConflict(
                    f"Edge {operation.collection}/{operation.object_id} has an empty endpoint"
                )
            if not relation_type:
                raise ChangeSetConflict(
                    f"Edge {operation.collection}/{operation.object_id} has no relation type"
                )
            if from_id == to_id:
                raise ChangeSetConflict(
                    f"Edge {operation.collection}/{operation.object_id} points to itself"
                )
            signature = (operation.collection, from_id, to_id, relation_type)
            prior = incoming_signatures.setdefault(signature, operation.object_id)
            if prior != operation.object_id:
                raise ChangeSetConflict(
                    "Duplicate semantic edge in one ChangeSet: "
                    f"{operation.collection}/{prior} and {operation.object_id} "
                    f"both name {from_id}->{to_id} ({relation_type})"
                )
            incoming_edges.append(
                (operation.collection, operation.object_id, from_id, to_id, relation_type)
            )
            endpoint_ids.update((from_id, to_id))
        if retiring_ids:
            cursor.execute(
                """SELECT edge_collection, edge_id, from_id, to_id
                   FROM wang_knowledge.edges
                   WHERE retired_at IS NULL
                     AND (from_id = ANY(%s) OR to_id = ANY(%s))
                   FOR UPDATE""",
                (sorted(retiring_ids), sorted(retiring_ids)),
            )
            surviving_references = []
            for collection, edge_id, from_id, to_id in cursor.fetchall():
                key = (str(collection), str(edge_id))
                planned = planned_edge_operations.get(key)
                if planned is not None:
                    if planned.operation == "retire":
                        continue
                    from_id, to_id, _ = cls._edge_values(
                        key[0], stored_operation_payload(planned)
                    )
                if str(from_id) in retiring_ids or str(to_id) in retiring_ids:
                    surviving_references.append(
                        (key[0], key[1], str(from_id), str(to_id))
                    )
            if surviving_references:
                collection, edge_id, from_id, to_id = sorted(
                    surviving_references
                )[0]
                raise ChangeSetConflict(
                    f"Retirement would leave current edge {collection}/{edge_id} "
                    f"with non-current endpoint {from_id}->{to_id}"
                )
        if not incoming_edges:
            return

        cursor.execute(
            """SELECT collection, object_id FROM wang_knowledge.objects
               WHERE object_id = ANY(%s) AND retired_at IS NULL FOR UPDATE""",
            (sorted(endpoint_ids),),
        )
        current_owner_sets: dict[str, set[str]] = {}
        for collection, object_id in cursor.fetchall():
            object_id = str(object_id)
            if object_id in retiring_ids:
                continue
            current_owner_sets.setdefault(object_id, set()).add(str(collection))
        ambiguous = {
            object_id: owners
            for object_id, owners in current_owner_sets.items()
            if len(owners) > 1
        }
        if ambiguous:
            object_id = sorted(ambiguous)[0]
            raise ChangeSetConflict(
                f"Edge endpoint {object_id} has ambiguous global ownership: "
                + ", ".join(sorted(ambiguous[object_id]))
            )
        current_owners = {
            object_id: next(iter(owners))
            for object_id, owners in current_owner_sets.items()
        }
        final_owners = {**current_owners, **arriving_owners}
        for collection, edge_id, from_id, to_id, _ in incoming_edges:
            missing = {from_id, to_id} - set(final_owners)
            if missing:
                raise ChangeSetConflict(
                    f"Edge {collection}/{edge_id} has non-current endpoints: "
                    + ", ".join(sorted(missing))
                )
            allowed_from, allowed_to = EDGE_ENDPOINT_COLLECTIONS[collection]
            if final_owners[from_id] not in allowed_from:
                raise ChangeSetConflict(
                    f"Edge {collection}/{edge_id} from endpoint {from_id} belongs to "
                    f"{final_owners[from_id]}, expected {sorted(allowed_from)}"
                )
            if final_owners[to_id] not in allowed_to:
                raise ChangeSetConflict(
                    f"Edge {collection}/{edge_id} to endpoint {to_id} belongs to "
                    f"{final_owners[to_id]}, expected {sorted(allowed_to)}"
                )

        cursor.execute(
            """SELECT edge_collection, edge_id, from_id, to_id, relation_type
               FROM wang_knowledge.edges WHERE retired_at IS NULL FOR UPDATE"""
        )
        existing: dict[tuple[str, str, str, str], set[str]] = {}
        for collection, edge_id, from_id, to_id, relation_type in cursor.fetchall():
            key = (str(collection), str(edge_id))
            planned = planned_edge_operations.get(key)
            if planned is not None:
                if planned.operation == "retire":
                    continue
                from_id, to_id, relation_type = cls._edge_values(
                    key[0], stored_operation_payload(planned)
                )
            signature = (
                key[0], str(from_id), str(to_id), str(relation_type)
            )
            existing.setdefault(signature, set()).add(key[1])
        for collection, edge_id, from_id, to_id, relation_type in incoming_edges:
            other_owners = existing.get(
                (collection, from_id, to_id, relation_type), set()
            ) - {edge_id}
            if other_owners:
                owner = sorted(other_owners)[0]
                raise ChangeSetConflict(
                    "Semantic edge already has another current ID: "
                    f"{collection}/{owner}, not {edge_id}, names "
                    f"{from_id}->{to_id} ({relation_type})"
                )

    @staticmethod
    def _assert_no_uncoordinated_semantic_references(
        cursor: Any, plan: ChangeSetPlan
    ) -> None:
        """Do not retire extraction identity under current CVR records.

        Model-local ordinals are not semantic identity. Re-extraction therefore
        creates a disjoint generation and retires its predecessor. If current
        viewpoint/route master data still cites that predecessor, a coordinated
        CVR ChangeSet must update or retire those records in the same plan;
        silently leaving them attached to retired content is forbidden. An
        ``update`` preserves the same object id and is therefore deliberately
        not included: references to an identity-preserving update remain valid.
        """

        retired_ids = {
            operation.object_id
            for operation in plan.operations
            if (
                operation.collection in EXTRACTION_RECORD_COLLECTIONS
                or operation.collection == "source_documents"
            )
            and operation.operation == "retire"
        }
        if not retired_ids:
            return
        planned_operations = {
            (operation.collection, operation.object_id): operation
            for operation in plan.operations
        }
        cursor.execute(
            """SELECT collection, object_id, payload
               FROM wang_knowledge.objects
               WHERE collection = ANY(%s) AND retired_at IS NULL
               FOR UPDATE""",
            (sorted(SEMANTIC_REFERENCE_COLLECTIONS),),
        )

        def strings(value: Any) -> set[str]:
            if isinstance(value, Mapping):
                found: set[str] = set()
                for key, child in value.items():
                    if isinstance(key, str):
                        found.add(key)
                    found.update(strings(child))
                return found
            if isinstance(value, (list, tuple, set)):
                found: set[str] = set()
                for child in value:
                    found.update(strings(child))
                return found
            return {value} if isinstance(value, str) else set()

        def exact_references(collection: str, value: Any) -> set[str]:
            fields = SEMANTIC_EXTRACTION_REFERENCE_FIELDS.get(collection, set())
            if not fields or not isinstance(value, (Mapping, list, tuple, set)):
                return set()
            if isinstance(value, Mapping):
                found: set[str] = set()
                for key, child in value.items():
                    if str(key) in fields:
                        found.update(strings(child) & retired_ids)
                    else:
                        found.update(exact_references(collection, child))
                return found
            found: set[str] = set()
            for child in value:
                found.update(exact_references(collection, child))
            return found

        def unclassified_references(
            collection: str, value: Any, *, path: tuple[str, ...] = ()
        ) -> set[str]:
            fields = SEMANTIC_EXTRACTION_REFERENCE_FIELDS.get(collection, set())
            found: set[str] = set()
            if isinstance(value, Mapping):
                for key, child in value.items():
                    field = str(key)
                    if field in retired_ids:
                        found.add(f"{'.'.join(path) or '<root>'}.<key>={field}")
                    if field in fields or field in NON_LIVE_EXTRACTION_ID_FIELDS:
                        continue
                    found.update(
                        unclassified_references(
                            collection, child, path=(*path, field)
                        )
                    )
                return found
            if isinstance(value, (list, tuple, set)):
                for child in value:
                    found.update(
                        unclassified_references(collection, child, path=path)
                    )
                return found
            if isinstance(value, str) and value in retired_ids:
                found.add(f"{'.'.join(path) or '<root>'}={value}")
            return found

        blockers: list[str] = []
        for collection, object_id, payload in cursor.fetchall():
            key = (str(collection), str(object_id))
            planned = planned_operations.get(key)
            if planned is not None:
                if planned.operation != "retire":
                    planned_payload = stored_operation_payload(planned)
                    references = exact_references(key[0], planned_payload)
                    if references:
                        blockers.append(
                            f"planned {key[0]}/{key[1]} still -> "
                                f"{','.join(sorted(references))}"
                            )
                    unknown = unclassified_references(key[0], planned_payload)
                    if unknown:
                        blockers.append(
                            f"planned {key[0]}/{key[1]} has unclassified id field "
                            f"{','.join(sorted(unknown))}"
                        )
                continue
            references = exact_references(key[0], payload)
            if references:
                blockers.append(
                    f"{key[0]}/{key[1]} -> {','.join(sorted(references))}"
                )
            unknown = unclassified_references(key[0], payload)
            if unknown:
                blockers.append(
                    f"{key[0]}/{key[1]} has unclassified id field "
                    f"{','.join(sorted(unknown))}"
                )
        # A newly created semantic row is absent from the locked query above.
        for key, planned in planned_operations.items():
            if key[0] not in SEMANTIC_REFERENCE_COLLECTIONS:
                continue
            if planned.operation == "retire":
                continue
            references = exact_references(
                key[0], stored_operation_payload(planned)
            )
            if references and not any(
                item.startswith(f"planned {key[0]}/{key[1]} ")
                for item in blockers
            ):
                blockers.append(
                    f"planned {key[0]}/{key[1]} still -> "
                    f"{','.join(sorted(references))}"
                )
            unknown = unclassified_references(
                key[0], stored_operation_payload(planned)
            )
            if unknown and not any(
                item.startswith(f"planned {key[0]}/{key[1]} has unclassified ")
                for item in blockers
            ):
                blockers.append(
                    f"planned {key[0]}/{key[1]} has unclassified id field "
                    f"{','.join(sorted(unknown))}"
                )
        if blockers:
            raise ChangeSetConflict(
                "re-extraction requires a coordinated CVR update; current semantic "
                "master data still references the predecessor: "
                + " | ".join(sorted(blockers)[:20])
            )

    @staticmethod
    def _assert_no_dangling_package_references(
        cursor: Any, plan: ChangeSetPlan
    ) -> None:
        """Do not retire a package object while a current package row cites it.

        Supersession retires a whole extraction generation.  Its closure must
        include every current owner of a retiring id, or update that owner in
        the same ChangeSet so the retired id is removed.  Otherwise a retry can
        appear successful while leaving a package that cannot be traversed.
        """

        retired_ids = {
            operation.object_id
            for operation in plan.operations
            if (
                operation.collection in EXTRACTION_RECORD_COLLECTIONS
                or operation.collection == "source_documents"
            )
            and operation.operation == "retire"
        }
        if not retired_ids:
            return

        planned_operations = {
            (operation.collection, operation.object_id): operation
            for operation in plan.operations
        }

        def strings(value: Any) -> set[str]:
            if isinstance(value, Mapping):
                found: set[str] = set()
                for child in value.values():
                    found.update(strings(child))
                return found
            if isinstance(value, (list, tuple, set)):
                found: set[str] = set()
                for child in value:
                    found.update(strings(child))
                return found
            return {value} if isinstance(value, str) else set()

        def references(collection: str, value: Any) -> set[str]:
            fields = PACKAGE_REFERENCE_FIELDS.get(collection, set())
            if not fields or not isinstance(value, (Mapping, list, tuple, set)):
                return set()
            if isinstance(value, Mapping):
                found: set[str] = set()
                for key, child in value.items():
                    if str(key) in fields:
                        found.update(strings(child) & retired_ids)
                    else:
                        found.update(references(collection, child))
                return found
            found: set[str] = set()
            for child in value:
                found.update(references(collection, child))
            return found

        cursor.execute(
            """SELECT collection, object_id, payload
               FROM wang_knowledge.objects
               WHERE collection = ANY(%s) AND retired_at IS NULL
               FOR UPDATE""",
            (sorted(PACKAGE_REFERENCE_FIELDS),),
        )
        blockers: list[str] = []
        seen_planned: set[tuple[str, str]] = set()
        for collection, object_id, payload in cursor.fetchall():
            key = (str(collection), str(object_id))
            planned = planned_operations.get(key)
            if planned is not None:
                seen_planned.add(key)
                if planned.operation == "retire":
                    continue
                payload = stored_operation_payload(planned)
            found = references(key[0], payload)
            if found:
                prefix = "planned " if planned is not None else ""
                blockers.append(
                    f"{prefix}{key[0]}/{key[1]} -> {','.join(sorted(found))}"
                )

        # Creates and restores are absent from the current-row query. Updates
        # are normally seen there, but including unseen ones is fail-closed for
        # malformed plans and test doubles.
        for key, planned in planned_operations.items():
            if key in seen_planned or key[0] not in PACKAGE_REFERENCE_FIELDS:
                continue
            if planned.operation == "retire":
                continue
            found = references(key[0], stored_operation_payload(planned))
            if found:
                blockers.append(
                    f"planned {key[0]}/{key[1]} -> {','.join(sorted(found))}"
                )

        if blockers:
            raise ChangeSetConflict(
                "retirement would leave current extraction records pointing to "
                "non-current objects: " + " | ".join(sorted(blockers)[:20])
            )

    def _set_retirement(
        self, cursor: Any, plan: ChangeSetPlan, index: int, operation: ChangeOperation
    ) -> None:
        """Withdraw one record or put it back, leaving what it says untouched.

        The row keeps its payload and gains `retired_at`; the new revision is
        written to `object_versions` so the withdrawal is a point in the
        record's history rather than an absence, and `change_operations` gets
        a `retire` row like every other change. Nothing is deleted: three
        tables exist to say what happened to this store, and a row removed
        behind their back makes all three lie.
        """

        payload = stored_operation_payload(operation)
        retiring = operation.operation == "retire"
        cursor.execute(
            """UPDATE wang_knowledge.objects
               SET revision=%s, updated_at=now(),
                   retired_at = CASE WHEN %s THEN now() ELSE NULL END
               WHERE collection=%s AND object_id=%s""",
            (operation.after_revision, retiring, operation.collection, operation.object_id),
        )
        cursor.execute(
            """INSERT INTO wang_knowledge.object_versions
               (collection, object_id, revision, content_sha256, payload, change_set_id)
               VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
            (
                operation.collection, operation.object_id, operation.after_revision,
                operation.after_sha256, canonical_json(payload), plan.change_set_id,
            ),
        )
        cursor.execute(
            """INSERT INTO wang_knowledge.change_operations
               (change_set_id, operation_index, operation, collection, object_id,
                before_sha256, after_sha256, before_revision, after_revision)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                plan.change_set_id, index, operation.operation,
                operation.collection, operation.object_id,
                operation.before_sha256, operation.after_sha256,
                operation.before_revision, operation.after_revision,
            ),
        )
        if operation.collection in EDGE_COLLECTIONS:
            # An edge outlives its object otherwise: `edges` is a separate
            # table with its own `retired_at`, and every traversal reads it
            # rather than `objects`.
            cursor.execute(
                """UPDATE wang_knowledge.edges
                   SET revision=%s, updated_at=now(),
                       retired_at = CASE WHEN %s THEN now() ELSE NULL END
                   WHERE edge_collection=%s AND edge_id=%s""",
                (operation.after_revision, retiring, operation.collection, operation.object_id),
            )

    def _invalidate_dependencies(
        self,
        cursor: Any,
        plan: ChangeSetPlan,
        changed_records: list[tuple[str, str, int, int]],
        operation_offset: int,
    ) -> int:
        matched_by_change: dict[tuple[str, str, int, int], list[str]] = {}
        dependencies: dict[str, tuple[int, dict[str, Any]]] = {}
        for changed_collection, changed_id, from_revision, to_revision in changed_records:
            manifest_ref = canonical_json([{
                "collection": changed_collection,
                "record_id": changed_id,
            }])
            cursor.execute(
                """SELECT object_id, revision, payload
                   FROM wang_knowledge.objects
                   WHERE collection='product_dependencies'
                     AND retired_at IS NULL
                     AND (
                       (%s='claims' AND payload->>'claim_id'=%s)
                       OR COALESCE(payload->'dependency_manifest','[]'::jsonb) @> %s::jsonb
                     )
                     AND COALESCE(payload->>'status','current')='current'""",
                (changed_collection, changed_id, manifest_ref),
            )
            rows = cursor.fetchall()
            for dependency_id, revision, payload in rows:
                dependency_id = str(dependency_id)
                dependencies.setdefault(dependency_id, (int(revision), dict(payload)))
                matched_by_change.setdefault(
                    (changed_collection, changed_id, from_revision, to_revision), []
                ).append(dependency_id)

        # Scan all change reasons before mutating status. Otherwise the first
        # matched record flips a dependency to invalidated and later records in
        # the same ChangeSet can no longer see it, leaving the impact ledger
        # with only one of several true causes.
        for count, dependency_id in enumerate(sorted(dependencies)):
            revision, payload = dependencies[dependency_id]
            reasons = [
                change
                for change, affected in matched_by_change.items()
                if dependency_id in affected
            ]
            event_ids = [
                f"IMPACT-{plan.change_set_id}-{collection}-{record_id}"
                for collection, record_id, _, _ in reasons
            ]
            updated = dict(payload)
            updated["status"] = "invalidated"
            updated["invalidation_change_set_ids"] = sorted({
                *updated.get("invalidation_change_set_ids", []),
                plan.change_set_id,
            })
            updated["invalidation_event_ids"] = sorted({
                *updated.get("invalidation_event_ids", []),
                *event_ids,
            })
            next_revision = revision + 1
            updated["revision"] = next_revision
            content_sha = record_content_sha(updated)
            cursor.execute(
                """UPDATE wang_knowledge.objects SET revision=%s, review_status=%s,
                   visibility=%s, content_sha256=%s, payload=%s::jsonb, updated_at=now()
                   WHERE collection='product_dependencies' AND object_id=%s""",
                (
                    next_revision, updated.get("review_status", "candidate"),
                    updated.get("visibility", "internal"), content_sha,
                    canonical_json(updated), dependency_id,
                ),
            )
            cursor.execute(
                """INSERT INTO wang_knowledge.object_versions
                   (collection, object_id, revision, content_sha256, payload, change_set_id)
                   VALUES ('product_dependencies',%s,%s,%s,%s::jsonb,%s)""",
                (
                    dependency_id, next_revision, content_sha,
                    canonical_json(updated), plan.change_set_id,
                ),
            )
            cursor.execute(
                """INSERT INTO wang_knowledge.change_operations
                   (change_set_id, operation_index, operation, collection, object_id,
                    after_sha256, before_revision, after_revision, details)
                   VALUES (%s,%s,'invalidate','product_dependencies',%s,%s,%s,%s,%s::jsonb)""",
                (
                    plan.change_set_id, operation_offset + count, dependency_id,
                    content_sha, revision, next_revision,
                    canonical_json({
                        "changed_records": [
                            {"collection": collection, "record_id": record_id}
                            for collection, record_id, _, _ in reasons
                        ],
                    }),
                ),
            )

        for change, affected_ids in matched_by_change.items():
            changed_collection, changed_id, from_revision, to_revision = change
            affected_ids = sorted(set(affected_ids))
            if affected_ids:
                event_id = f"IMPACT-{plan.change_set_id}-{changed_collection}-{changed_id}"
                # ``impact_events`` are derived inside the apply transaction,
                # after the incoming package's global-ID preflight has run.
                # They still live in the same global object-id namespace.  Do
                # not let this internal insertion become a bypass while an
                # installation is waiting to apply migration 005.
                cursor.execute(
                    """SELECT collection FROM wang_knowledge.objects
                       WHERE object_id=%s FOR UPDATE""",
                    (event_id,),
                )
                owners = {str(row[0]) for row in cursor.fetchall()}
                foreign_owners = owners - {"impact_events"}
                if foreign_owners:
                    raise ChangeSetConflict(
                        "Record IDs are globally unique; generated impact event "
                        f"{event_id!r} already belongs to "
                        + ", ".join(sorted(foreign_owners))
                    )
                event = {
                    "impact_event_id": event_id,
                    "changed_record_type": changed_collection,
                    "changed_record_id": changed_id,
                    "from_revision": from_revision,
                    "to_revision": to_revision,
                    "affected_dependency_ids": affected_ids,
                    "required_actions": [
                        "review_affected_products", "withdraw_or_rebuild_published_consumers",
                        "invalidate_qa_and_search_cache",
                    ],
                    "status": "open",
                    "review_status": "system_generated",
                    "visibility": "internal",
                    "revision": 1,
                }
                content_sha = record_content_sha(event)
                cursor.execute(
                    """INSERT INTO wang_knowledge.objects
                       (collection, object_id, revision, review_status, visibility,
                        content_sha256, payload)
                       VALUES ('impact_events',%s,1,'system_generated','internal',%s,%s::jsonb)
                       ON CONFLICT (collection, object_id) DO NOTHING""",
                    (event_id, content_sha, canonical_json(event)),
                )
                cursor.execute(
                    """INSERT INTO wang_knowledge.object_versions
                       (collection, object_id, revision, content_sha256, payload, change_set_id)
                       VALUES ('impact_events',%s,1,%s,%s::jsonb,%s)
                       ON CONFLICT DO NOTHING""",
                    (event_id, content_sha, canonical_json(event), plan.change_set_id),
                )
        return len(dependencies)

    def ingest_package(
        self,
        package: Mapping[str, Any],
        *,
        source_kind: str = "knowledge_package",
        apply: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        plan = self.plan_package(package, source_kind=source_kind)
        if not apply:
            return {"status": "planned", **plan.as_dict()}
        return self.apply_plan(plan, metadata=metadata)

    def compile_package(self, *, package_id: Optional[str] = None) -> dict[str, Any]:
        by_collection: dict[str, list[dict[str, Any]]] = {
            collection: [] for collection in KNOWLEDGE_COLLECTIONS
        }
        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT collection, payload FROM wang_knowledge.objects
                   WHERE retired_at IS NULL ORDER BY collection, object_id"""
            )
            for collection, payload in cursor.fetchall():
                if collection in by_collection:
                    by_collection[collection].append(payload)

        result: dict[str, Any] = {
            "schema_version": "wang_shared_knowledge_v1.3",
            "package_id": package_id or f"PG-SNAPSHOT-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "compiled_at": datetime.now(timezone.utc).isoformat(),
            "authority": "postgresql_authoring_store",
        }
        for collection, source_key in SOURCE_KEYS.items():
            if collection in {"product_dependencies", "impact_events"}:
                result[collection] = by_collection[collection]
            else:
                result[source_key] = by_collection[collection]

        decisions_by_plan: dict[str, list[dict[str, Any]]] = {}
        for decision in by_collection["composition_decisions"]:
            decisions_by_plan.setdefault(str(decision["plan_id"]), []).append(decision)
        result["product_plans"] = []
        for plan in by_collection["composition_plans"]:
            row = dict(plan)
            row["decisions"] = decisions_by_plan.get(str(plan["plan_id"]), [])
            result["product_plans"].append(row)
        result["summary"] = {
            "counts": {key: len(value) for key, value in by_collection.items()}
        }
        return result

    def record_review(
        self,
        collection: str,
        object_id: str,
        *,
        decision: str,
        reason: str = "",
        reviewer_id: str = "同工",
        reviewer_kind: str = "human",
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        """Persist a review as an auditable revision and invalidate consumers."""
        if reviewer_kind not in {"human", "ai", "system"}:
            raise PostgresKnowledgeStoreError(f"Unsupported reviewer_kind: {reviewer_kind}")
        now = datetime.now(timezone.utc)
        event_id = f"REV-{uuid.uuid4().hex}"
        fingerprint = sha256_json(
            {"review_event_id": event_id, "collection": collection, "object_id": object_id}
        )
        change_set_id = f"KCS-REVIEW-{uuid.uuid4().hex[:20]}"
        with self.connect() as conn, conn.cursor() as cursor:
            # Review decisions can invalidate ProductDependency rows and create
            # ImpactEvent objects. They therefore participate in the same
            # cross-record invariants as ``apply_plan`` and must serialize on
            # the same transaction lock.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("wang_knowledge.apply_plan.v1",),
            )
            cursor.execute(
                """SELECT revision, content_sha256, payload
                   FROM wang_knowledge.objects
                   WHERE collection=%s AND object_id=%s AND retired_at IS NULL
                   FOR UPDATE""",
                (collection, object_id),
            )
            row = cursor.fetchone()
            if not row:
                raise PostgresKnowledgeStoreError(f"Unknown record {collection}/{object_id}")
            revision, before_sha, payload = row
            if expected_revision is not None and int(revision) != expected_revision:
                raise ChangeSetConflict(
                    f"Expected revision {expected_revision}, found {revision} for {collection}/{object_id}"
                )
            updated = dict(payload)
            updated.update(
                {
                    "review_status": decision,
                    "review_note": reason.strip(),
                    "reviewed_by": reviewer_id.strip() or "同工",
                    "reviewed_at": now.isoformat(),
                    "revision": int(revision) + 1,
                }
            )
            after_sha = record_content_sha(updated)
            summary = {"created": 0, "updated": 1, "unchanged": 0, "operations": 1}
            cursor.execute(
                """INSERT INTO wang_knowledge.change_sets
                   (change_set_id, fingerprint_sha256, package_id, source_kind,
                    source_sha256, status, summary, metadata, applied_at)
                   VALUES (%s,%s,%s,'review_decision',%s,'applied',%s::jsonb,%s::jsonb,now())""",
                (
                    change_set_id,
                    fingerprint,
                    f"REVIEW-{collection}-{object_id}",
                    before_sha,
                    canonical_json(summary),
                    canonical_json({"review_event_id": event_id}),
                ),
            )
            cursor.execute(
                """UPDATE wang_knowledge.objects
                   SET revision=%s, review_status=%s, content_sha256=%s,
                       payload=%s::jsonb, updated_at=now()
                   WHERE collection=%s AND object_id=%s""",
                (
                    updated["revision"], decision, after_sha, canonical_json(updated),
                    collection, object_id,
                ),
            )
            cursor.execute(
                """INSERT INTO wang_knowledge.object_versions
                   (collection, object_id, revision, content_sha256, payload, change_set_id)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    collection, object_id, updated["revision"], after_sha,
                    canonical_json(updated), change_set_id,
                ),
            )
            cursor.execute(
                """INSERT INTO wang_knowledge.change_operations
                   (change_set_id, operation_index, operation, collection, object_id,
                    before_sha256, after_sha256, before_revision, after_revision)
                   VALUES (%s,0,'update',%s,%s,%s,%s,%s,%s)""",
                (
                    change_set_id, collection, object_id, before_sha, after_sha,
                    revision, updated["revision"],
                ),
            )
            if collection in EDGE_COLLECTIONS:
                cursor.execute(
                    """UPDATE wang_knowledge.edges
                       SET review_status=%s, revision=%s, payload=%s::jsonb, updated_at=now()
                       WHERE edge_collection=%s AND edge_id=%s""",
                    (decision, updated["revision"], canonical_json(updated), collection, object_id),
                )
            cursor.execute(
                """INSERT INTO wang_knowledge.review_events
                   (review_event_id, collection, object_id, object_revision,
                    reviewer_kind, reviewer_id, decision, reason, artifact)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    event_id, collection, object_id, updated["revision"], reviewer_kind,
                    reviewer_id.strip() or "同工", decision, reason.strip(),
                    canonical_json({"change_set_id": change_set_id}),
                ),
            )
            invalidated = 0
            if collection == "claims" and int(revision) > 0:
                plan = ChangeSetPlan(
                    change_set_id=change_set_id,
                    fingerprint_sha256=fingerprint,
                    package_id=f"REVIEW-{object_id}",
                    source_kind="review_decision",
                    source_sha256=before_sha,
                    operations=(),
                    unchanged=0,
                    ignored_keys=(),
                )
                invalidated = self._invalidate_dependencies(
                    cursor, plan, [("claims", object_id, int(revision), updated["revision"])], 1
                )
                if invalidated:
                    summary["invalidated_dependencies"] = invalidated
                    cursor.execute(
                        "UPDATE wang_knowledge.change_sets SET summary=%s::jsonb WHERE change_set_id=%s",
                        (canonical_json(summary), change_set_id),
                    )
        return {
            "status": decision,
            "note": reason.strip(),
            "reviewer": reviewer_id.strip() or "同工",
            "reviewed_at": now.isoformat(),
            "revision": updated["revision"],
            "review_event_id": event_id,
        }

    def publish_active_snapshot(self, output_root: Path) -> dict[str, Any]:
        """Atomically build and activate the approved read snapshot."""
        package = self.compile_package()
        snapshot, findings = build_active_snapshot(package)
        errors = [item for item in findings if item.get("severity") == "error"]
        if not snapshot["claims"]:
            errors.append({"severity": "error", "code": "active_snapshot_has_no_approved_claims"})
        if errors:
            raise ActiveSnapshotBlocked(errors)

        output_root = Path(output_root)
        builds_root = output_root / "builds"
        builds_root.mkdir(parents=True, exist_ok=True)
        build_dir = builds_root / snapshot["build_id"]
        temporary_dir = Path(tempfile.mkdtemp(prefix="active-build-", dir=builds_root))
        try:
            snapshot_path = temporary_dir / "shared_knowledge.json"
            snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            snapshot_path.write_bytes(snapshot_bytes)
            digest = sha256_bytes(snapshot_bytes)
            manifest = {
                "schema_version": "wang_active_snapshot_manifest_v1",
                "build_id": snapshot["build_id"],
                "generated_at": snapshot["generated_at"],
                "snapshot_sha256": digest,
                "snapshot_file": "shared_knowledge.json",
                "counts": snapshot["summary"]["counts"],
            }
            (temporary_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(temporary_dir, build_dir)
            pointer = {
                **manifest,
                "snapshot_path": str(build_dir / "shared_knowledge.json"),
            }
            fd, pointer_temp = tempfile.mkstemp(prefix="active-", suffix=".json", dir=output_root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(pointer, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                os.replace(pointer_temp, output_root / "active.json")
            finally:
                if os.path.exists(pointer_temp):
                    os.unlink(pointer_temp)
        finally:
            if temporary_dir.exists():
                temporary_dir.rmdir()
        return pointer

    def status(self) -> dict[str, Any]:
        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT collection, count(*) FROM wang_knowledge.objects
                   WHERE retired_at IS NULL GROUP BY collection ORDER BY collection"""
            )
            counts = dict(cursor.fetchall())
            cursor.execute(
                "SELECT status, count(*) FROM wang_knowledge.change_sets GROUP BY status ORDER BY status"
            )
            change_sets = dict(cursor.fetchall())
            cursor.execute(
                """SELECT collection, review_status, count(*)
                   FROM wang_knowledge.objects WHERE retired_at IS NULL
                   GROUP BY collection, review_status ORDER BY collection, review_status"""
            )
            review_counts: dict[str, dict[str, int]] = {}
            for collection, review_status, count in cursor.fetchall():
                review_counts.setdefault(collection, {})[review_status] = count
            cursor.execute(
                """SELECT change_set_id, source_kind, applied_at
                   FROM wang_knowledge.change_sets WHERE status='applied'
                   ORDER BY applied_at DESC NULLS LAST LIMIT 1"""
            )
            latest = cursor.fetchone()
        return {
            "objects": counts,
            "review_counts": review_counts,
            "change_sets": change_sets,
            "latest_change_set": (
                {"change_set_id": latest[0], "source_kind": latest[1], "applied_at": latest[2].isoformat()}
                if latest else None
            ),
        }
