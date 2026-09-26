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
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .knowledge_importer import KnowledgePackageImporter
from .knowledge_models import KNOWLEDGE_COLLECTIONS
from .reviewed_candidate_contract import (
    ConsensusApplicationError,
    validate_store_package_authorization,
)


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
OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS = (
    "composition_decisions",
    "composition_plans",
    "editorial_syntheses",
    "knowledge_routes",
)
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

CLAIM_EVIDENCE_COLLECTIONS = ("claims", "evidence_steps")
CLAIM_EVIDENCE_ACTIVE_SNAPSHOT_SCHEMA_VERSION = (
    "wang_claim_evidence_active_snapshot_v1"
)
CLAIM_EVIDENCE_RECIPROCITY_GUARD_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_store_guard_v1"
)
PRODUCT_DEPENDENCY_ACTIVE_SNAPSHOT_SCHEMA_VERSION = (
    "wang_product_dependency_active_snapshot_v1"
)
REVIEW_EVENT_LEDGER_SNAPSHOT_SCHEMA_VERSION = (
    "wang_review_event_ledger_snapshot_v1"
)
CLAIM_EVIDENCE_FREEZE_BINDING_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_freeze_binding_v1"
)
SOURCE_LINEAGE_IDENTITY_SNAPSHOT_SCHEMA_VERSION = (
    "wang_source_lineage_identity_snapshot_v1"
)
CLAIM_EVIDENCE_RECIPROCITY_REPAIR_SOURCE_KIND = (
    "wkp364_claim_evidence_reciprocity_repair"
)
CLAIM_EVIDENCE_PAIR_ADJUDICATION_SOURCE_KIND = (
    "wkp364_claim_evidence_pair_adjudication"
)
CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS = frozenset(
    {
        CLAIM_EVIDENCE_RECIPROCITY_REPAIR_SOURCE_KIND,
        CLAIM_EVIDENCE_PAIR_ADJUDICATION_SOURCE_KIND,
    }
)
CLAIM_EVIDENCE_RECIPROCITY_REPAIR_METADATA_KEY = (
    "claim_evidence_reciprocity_repair"
)
CLAIM_EVIDENCE_SOURCE_REPLAY_SOURCE_KIND = (
    "wkp364_claim_evidence_source_replay"
)
CLAIM_EVIDENCE_SOURCE_RERUN_SOURCE_KIND = (
    "wkp364_claim_evidence_source_rerun"
)
CLAIM_EVIDENCE_SOURCE_QUEUE_SOURCE_KINDS = frozenset(
    {
        CLAIM_EVIDENCE_SOURCE_REPLAY_SOURCE_KIND,
        CLAIM_EVIDENCE_SOURCE_RERUN_SOURCE_KIND,
    }
)
CLAIM_EVIDENCE_SOURCE_QUEUE_METADATA_KEY = (
    "claim_evidence_reciprocity_source_queue"
)
CLAIM_EVIDENCE_HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_human_authority_snapshot_v1"
)
POSTGRES_APPLY_ADVISORY_LOCK_KEY = "wang_knowledge.apply_plan.v1"
CLAIM_EVIDENCE_BACKUP_VERIFICATION_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_backup_verification_v1"
)
CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES = (
    "change_operations",
    "change_sets",
    "object_versions",
    "objects",
    "review_events",
)
_CLAIM_EVIDENCE_SOURCE_QUEUE_APPLY_TOKEN = object()


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


def _sealed_artifact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical artifact seal used by the #364 queue artifacts."""

    result = json.loads(canonical_json(payload))
    result.pop("artifact_sha256", None)
    result["artifact_sha256"] = sha256_json(result)
    return result


def _required_sha256(value: Any, *, label: str) -> str:
    digest = str(value or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PostgresKnowledgeStoreError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return digest


def _source_document_row_key(payload: Mapping[str, Any]) -> str:
    """Mirror the canonical source-row identity without importing pipeline code."""

    source_type = str(payload.get("source_type") or "").strip()
    transcript_id = str(payload.get("transcript_id") or "").strip()
    source_id = str(payload.get("source_id") or "").strip()
    project_id = str(payload.get("project_id") or "").strip()
    notes_prefix = "notes_manuscript:"
    is_notes = (
        source_type == "notes_manuscript"
        or transcript_id.startswith(notes_prefix)
        or source_id.startswith(notes_prefix)
    )
    if is_notes:
        candidate = project_id or transcript_id or source_id
        return (
            candidate[len(notes_prefix) :].strip()
            if candidate.startswith(notes_prefix)
            else candidate
        )
    return transcript_id or source_id


def _normalize_claim_evidence_source_generations(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source work requires exact SourceDocument generations"
        )
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise PostgresKnowledgeStoreError(
                f"SourceDocument generation {index} must be an object"
            )
        source_type = str(row.get("source_type") or "").strip()
        row_key = str(row.get("row_key") or "").strip()
        object_id = str(row.get("active_source_document_id") or "").strip()
        namespace = str(row.get("extraction_record_namespace") or "").strip()
        try:
            revision = int(row.get("expected_revision"))
        except (TypeError, ValueError) as exc:
            raise PostgresKnowledgeStoreError(
                f"SourceDocument generation {source_type}/{row_key} has invalid revision"
            ) from exc
        normalized = {
            "source_type": source_type,
            "row_key": row_key,
            "active_source_document_id": object_id,
            "expected_revision": revision,
            "expected_content_sha256": _required_sha256(
                row.get("expected_content_sha256"),
                label=f"SourceDocument generation {source_type}/{row_key} content SHA",
            ),
            "source_body_sha256": _required_sha256(
                row.get("source_body_sha256"),
                label=f"SourceDocument generation {source_type}/{row_key} body SHA",
            ),
            "extraction_record_namespace": namespace,
        }
        if not source_type or not row_key or not object_id or not namespace or revision < 1:
            raise PostgresKnowledgeStoreError(
                f"SourceDocument generation {source_type or '<missing>'}/"
                f"{row_key or '<missing>'} is incomplete"
            )
        key = (source_type, row_key)
        if key in result:
            raise PostgresKnowledgeStoreError(
                f"SourceDocument generation repeats {source_type}/{row_key}"
            )
        result[key] = normalized
    return [result[key] for key in sorted(result)]


def _active_claim_evidence_source_generations(
    rows: Sequence[Mapping[str, Any] | Sequence[Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    physical_ids: set[str] = set()
    for raw in rows:
        if isinstance(raw, Mapping):
            object_id = str(raw.get("object_id") or "")
            revision_value = raw.get("revision")
            content_sha = str(raw.get("content_sha256") or "")
            payload = raw.get("payload")
        else:
            try:
                object_id, revision_value, content_sha, payload = raw[:4]
            except (TypeError, ValueError) as exc:
                raise PostgresKnowledgeStoreError(
                    "SourceDocument rows require object_id, revision, content SHA and payload"
                ) from exc
            object_id = str(object_id or "")
            content_sha = str(content_sha or "")
        try:
            revision = int(revision_value)
        except (TypeError, ValueError) as exc:
            raise PostgresKnowledgeStoreError(
                f"SourceDocument {object_id or '<missing>'} has invalid revision"
            ) from exc
        if (
            not object_id
            or object_id in physical_ids
            or revision < 1
            or not isinstance(payload, Mapping)
            or record_content_sha(payload) != content_sha
            or str(payload.get("source_id") or "") != object_id
        ):
            raise PostgresKnowledgeStoreError(
                f"SourceDocument {object_id or '<missing>'} physical identity or SHA is invalid"
            )
        source_type = str(payload.get("source_type") or "").strip()
        row_key = _source_document_row_key(payload)
        source_body_sha = str(
            payload.get("source_body_sha256")
            or payload.get("source_sha256")
            or ""
        )
        namespace = str(payload.get("extraction_record_namespace") or "").strip()
        _required_sha256(
            source_body_sha,
            label=f"SourceDocument {object_id} source body SHA",
        )
        if not source_type or not row_key or not namespace:
            raise PostgresKnowledgeStoreError(
                f"SourceDocument {object_id} semantic generation is incomplete"
            )
        key = (source_type, row_key)
        if key in result:
            raise PostgresKnowledgeStoreError(
                f"Active SourceDocument generation repeats {source_type}/{row_key}"
            )
        physical_ids.add(object_id)
        result[key] = {
            "source_type": source_type,
            "row_key": row_key,
            "active_source_document_id": object_id,
            "expected_revision": revision,
            "expected_content_sha256": content_sha,
            "source_body_sha256": source_body_sha,
            "extraction_record_namespace": namespace,
        }
    return result


def _claim_evidence_row(
    row: Mapping[str, Any] | Sequence[Any],
) -> tuple[str, str, int, str, dict[str, Any]]:
    """Normalize one active Claim/Evidence database row for snapshotting."""

    if isinstance(row, Mapping):
        collection = str(row.get("collection") or "")
        object_id = str(row.get("object_id") or "")
        revision = row.get("revision")
        content_sha256 = str(row.get("content_sha256") or "")
        payload = row.get("payload")
    else:
        try:
            collection, object_id, revision, content_sha256, payload = row
        except (TypeError, ValueError) as exc:
            raise PostgresKnowledgeStoreError(
                "Claim/Evidence snapshot rows require collection, object_id, "
                "revision, content_sha256 and payload"
            ) from exc
        collection = str(collection)
        object_id = str(object_id)
        content_sha256 = str(content_sha256)
    if collection not in CLAIM_EVIDENCE_COLLECTIONS:
        raise PostgresKnowledgeStoreError(
            f"Claim/Evidence snapshot contains unsupported collection {collection!r}"
        )
    if not object_id or not isinstance(payload, Mapping):
        raise PostgresKnowledgeStoreError(
            f"Claim/Evidence snapshot row is incomplete: {collection}/{object_id or '<missing>'}"
        )
    try:
        normalized_revision = int(revision)
    except (TypeError, ValueError) as exc:
        raise PostgresKnowledgeStoreError(
            f"Claim/Evidence snapshot has invalid revision: {collection}/{object_id}"
        ) from exc
    if normalized_revision <= 0:
        raise PostgresKnowledgeStoreError(
            f"Claim/Evidence snapshot has invalid revision: {collection}/{object_id}"
        )
    normalized_payload = dict(payload)
    # The row column is the CAS authority. A retire/revive advances that column
    # while deliberately leaving the semantic payload untouched, so an active
    # revived legacy row can still carry its prior payload revision.
    expected_id_field = "claim_id" if collection == "claims" else "evidence_step_id"
    if str(normalized_payload.get(expected_id_field) or "") != object_id:
        raise PostgresKnowledgeStoreError(
            f"Claim/Evidence snapshot object ID differs from payload: "
            f"{collection}/{object_id}"
        )
    observed_sha = record_content_sha(normalized_payload)
    if content_sha256 != observed_sha:
        raise PostgresKnowledgeStoreError(
            f"Claim/Evidence snapshot content SHA differs from payload: "
            f"{collection}/{object_id}"
        )
    return (
        collection,
        object_id,
        normalized_revision,
        content_sha256,
        normalized_payload,
    )


def build_claim_evidence_active_snapshot(
    rows: Iterable[Mapping[str, Any] | Sequence[Any]],
) -> dict[str, Any]:
    """Seal the complete active Claim/Evidence row and pair state.

    The record root detects an added, removed or revised row even when that row
    is not in a repair plan.  The pair-state root separately binds both stored
    projections, including dangling and repeated references, so a bad legacy
    snapshot can be described exactly before a repair simulates its final state.
    """

    normalized: dict[tuple[str, str], tuple[int, str, dict[str, Any]]] = {}
    for raw_row in rows:
        collection, object_id, revision, content_sha256, payload = (
            _claim_evidence_row(raw_row)
        )
        key = (collection, object_id)
        if key in normalized:
            raise PostgresKnowledgeStoreError(
                f"Claim/Evidence snapshot repeats active row {collection}/{object_id}"
            )
        normalized[key] = (revision, content_sha256, payload)

    claim_ids = {
        object_id for collection, object_id in normalized if collection == "claims"
    }
    evidence_ids = {
        object_id
        for collection, object_id in normalized
        if collection == "evidence_steps"
    }
    claim_pairs: set[tuple[str, str]] = set()
    evidence_pairs: set[tuple[str, str]] = set()
    duplicate_references: list[dict[str, Any]] = []

    def references(
        *, collection: str, object_id: str, payload: Mapping[str, Any], field: str
    ) -> list[str]:
        raw_values = payload.get(field) or []
        if not isinstance(raw_values, (list, tuple)):
            raise PostgresKnowledgeStoreError(
                f"{collection}/{object_id}: {field} must be an array"
            )
        values = [str(value) for value in raw_values]
        if any(not value for value in values):
            raise PostgresKnowledgeStoreError(
                f"{collection}/{object_id}: {field} contains an empty ID"
            )
        counts: dict[str, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        duplicate_references.extend(
            {
                "collection": collection,
                "object_id": object_id,
                "field": field,
                "referenced_id": value,
                "occurrences": count,
            }
            for value, count in sorted(counts.items())
            if count > 1
        )
        return values

    for (collection, object_id), (_revision, _content_sha256, payload) in sorted(
        normalized.items()
    ):
        if collection == "claims":
            claim_pairs.update(
                (object_id, evidence_id)
                for evidence_id in references(
                    collection=collection,
                    object_id=object_id,
                    payload=payload,
                    field="evidence_step_ids",
                )
            )
        else:
            evidence_pairs.update(
                (claim_id, object_id)
                for claim_id in references(
                    collection=collection,
                    object_id=object_id,
                    payload=payload,
                    field="produced_claim_ids",
                )
            )

    dangling_claim_refs = sorted(
        pair for pair in claim_pairs if pair[1] not in evidence_ids
    )
    dangling_evidence_refs = sorted(
        pair for pair in evidence_pairs if pair[0] not in claim_ids
    )
    claim_only = claim_pairs - evidence_pairs
    evidence_only = evidence_pairs - claim_pairs
    pair_state = {
        "claim_evidence_pairs": [list(pair) for pair in sorted(claim_pairs)],
        "evidence_claim_pairs": [list(pair) for pair in sorted(evidence_pairs)],
        "duplicate_references": duplicate_references,
        "dangling_claim_evidence_refs": [
            list(pair) for pair in dangling_claim_refs
        ],
        "dangling_evidence_claim_refs": [
            list(pair) for pair in dangling_evidence_refs
        ],
    }
    record_rows = [
        {
            "collection": collection,
            "object_id": object_id,
            "revision": revision,
            "content_sha256": content_sha256,
        }
        for (collection, object_id), (revision, content_sha256, _payload)
        in sorted(normalized.items())
    ]
    snapshot = {
        "schema_version": CLAIM_EVIDENCE_ACTIVE_SNAPSHOT_SCHEMA_VERSION,
        "records": record_rows,
        "records_sha256": sha256_json(record_rows),
        "pair_state_sha256": sha256_json(pair_state),
        "counts": {
            "active_claims": len(claim_ids),
            "active_evidence_steps": len(evidence_ids),
            "claim_evidence_pairs": len(claim_pairs),
            "evidence_claim_pairs": len(evidence_pairs),
            "reciprocal_pairs": len(claim_pairs & evidence_pairs),
            "claim_only_pairs": len(claim_only),
            "evidence_only_pairs": len(evidence_only),
            "duplicate_array_references": sum(
                int(item["occurrences"]) - 1 for item in duplicate_references
            ),
            "dangling_claim_evidence_refs": len(dangling_claim_refs),
            "dangling_evidence_claim_refs": len(dangling_evidence_refs),
            "dangling_endpoints": len(dangling_claim_refs)
            + len(dangling_evidence_refs),
        },
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    return snapshot


def build_product_dependency_active_snapshot(
    rows: Iterable[Mapping[str, Any] | Sequence[Any]],
) -> dict[str, Any]:
    """Seal every active ProductDependency identity and semantic payload SHA."""

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if isinstance(raw, Mapping):
            object_id = str(raw.get("object_id") or raw.get("dependency_id") or "")
            revision = raw.get("revision")
            content_sha256 = str(raw.get("content_sha256") or "")
            payload = raw.get("payload")
        else:
            try:
                object_id, revision, content_sha256, payload = raw
            except (TypeError, ValueError) as exc:
                raise PostgresKnowledgeStoreError(
                    "ProductDependency snapshot rows require object_id, revision, "
                    "content_sha256 and payload"
                ) from exc
            object_id = str(object_id)
            content_sha256 = str(content_sha256)
        if not object_id or object_id in seen or not isinstance(payload, Mapping):
            raise PostgresKnowledgeStoreError(
                f"ProductDependency snapshot has missing or repeated row {object_id!r}"
            )
        try:
            normalized_revision = int(revision)
        except (TypeError, ValueError) as exc:
            raise PostgresKnowledgeStoreError(
                f"ProductDependency {object_id} has invalid revision"
            ) from exc
        if (
            normalized_revision <= 0
            or record_content_sha(payload) != content_sha256
        ):
            raise PostgresKnowledgeStoreError(
                f"ProductDependency {object_id} revision or content SHA is invalid"
            )
        seen.add(object_id)
        records.append(
            {
                "object_id": object_id,
                "revision": normalized_revision,
                "content_sha256": content_sha256,
            }
        )
    records.sort(key=lambda row: row["object_id"])
    snapshot = {
        "schema_version": PRODUCT_DEPENDENCY_ACTIVE_SNAPSHOT_SCHEMA_VERSION,
        "records": records,
        "records_sha256": sha256_json(records),
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    return snapshot


def _canonical_review_event_rows(
    rows: Iterable[Mapping[str, Any] | Sequence[Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if isinstance(raw, Mapping):
            values = (
                raw.get("review_event_id"),
                raw.get("collection"),
                raw.get("object_id"),
                raw.get("object_revision"),
                raw.get("reviewer_kind"),
                raw.get("reviewer_id"),
                raw.get("decision"),
                raw.get("reason"),
                raw.get("artifact"),
                raw.get("created_at"),
            )
        else:
            try:
                values = tuple(raw[:10])
            except (TypeError, ValueError) as exc:
                raise PostgresKnowledgeStoreError(
                    "Review-event ledger rows require the complete semantic row"
                ) from exc
            if len(values) != 10:
                raise PostgresKnowledgeStoreError(
                    "Review-event ledger rows require the complete semantic row"
                )
        event_id = str(values[0] or "")
        collection = str(values[1] or "")
        object_id = str(values[2] or "")
        try:
            revision = int(values[3])
        except (TypeError, ValueError) as exc:
            raise PostgresKnowledgeStoreError(
                f"Review event {event_id or '<missing>'} has invalid revision"
            ) from exc
        artifact = values[8]
        created_at_value = values[9]
        try:
            if isinstance(created_at_value, datetime):
                created_at = created_at_value
            else:
                created_at = datetime.fromisoformat(
                    str(created_at_value or "").replace("Z", "+00:00")
                )
            if created_at.tzinfo is None:
                raise ValueError("timestamp lacks timezone")
            created_at_text = created_at.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError) as exc:
            raise PostgresKnowledgeStoreError(
                f"Review event {event_id or '<missing>'} has invalid created_at"
            ) from exc
        if (
            not event_id
            or event_id in seen
            or not collection
            or not object_id
            or revision <= 0
            or not str(values[4] or "")
            or not str(values[5] or "")
            or not str(values[6] or "")
            or not isinstance(artifact, Mapping)
        ):
            raise PostgresKnowledgeStoreError(
                f"Review-event ledger has an invalid or repeated row {event_id!r}"
            )
        seen.add(event_id)
        normalized.append(
            {
                "review_event_id": event_id,
                "collection": collection,
                "object_id": object_id,
                "object_revision": revision,
                "reviewer_kind": str(values[4]),
                "reviewer_id": str(values[5]),
                "decision": str(values[6]),
                "reason": str(values[7] or ""),
                "artifact": dict(artifact),
                "created_at": created_at_text,
            }
        )
    normalized.sort(key=lambda row: row["review_event_id"])
    return normalized


def build_review_event_ledger_snapshot(
    rows: Iterable[Mapping[str, Any] | Sequence[Any]],
) -> dict[str, Any]:
    """Seal every review-event ledger row without exporting the row payloads.

    Review events are append-only authority.  Binding only their count permits
    an in-place replacement to preserve the denominator while changing who
    authorized a current Claim, so the repair guard carries this semantic root
    as well as the count.
    """

    normalized = _canonical_review_event_rows(rows)
    snapshot = {
        "schema_version": REVIEW_EVENT_LEDGER_SNAPSHOT_SCHEMA_VERSION,
        "count": len(normalized),
        "rows_sha256": sha256_json(normalized),
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    return snapshot


def _validate_review_event_ledger_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("schema_version") != REVIEW_EVENT_LEDGER_SNAPSHOT_SCHEMA_VERSION:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard has an unsupported review-event ledger snapshot"
        )
    count = snapshot.get("count")
    if (
        not isinstance(count, int)
        or count < 0
        or not re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("rows_sha256") or ""))
    ):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard has an invalid review-event ledger root or count"
        )
    sealed = dict(snapshot)
    observed_seal = str(sealed.pop("snapshot_sha256", ""))
    if observed_seal != sha256_json(sealed):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard review-event ledger snapshot seal is invalid"
        )


def _build_claim_evidence_human_authority_snapshot(
    head_rows: Sequence[Sequence[Any]],
    review_rows: Sequence[Sequence[Any]],
    human_proof_rows: Sequence[Sequence[Any]],
) -> dict[str, Any]:
    """Seal every Claim/Evidence head and every ledger-proven human ruling.

    A bare ``superseded`` status is intentionally not authority: the live
    corpus contains AI-superseded Claims.  Human protection exists only when a
    review event is tied to the exact ObjectVersion and applied review-decision
    ChangeSet that produced the reviewed semantic payload.  That also lets a
    later retire/revive head retain its historical human protection without
    pretending its retirement ChangeSet was a review decision.
    """

    review_events = _canonical_review_event_rows(review_rows)
    review_by_id = {row["review_event_id"]: row for row in review_events}
    events_by_key: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for event in review_events:
        events_by_key.setdefault(
            (
                event["collection"],
                event["object_id"],
                int(event["object_revision"]),
            ),
            [],
        ).append(event)

    proof_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen_proof_events: set[str] = set()
    for raw in human_proof_rows:
        if len(raw) != 16:
            raise PostgresKnowledgeStoreError(
                "Human authority proof rows require the complete event/version ledger"
            )
        (
            event_id,
            collection,
            object_id,
            event_revision,
            reviewer_kind,
            decision,
            artifact,
            version_revision,
            version_sha,
            version_payload,
            producer_change_set_id,
            producer_status,
            producer_source_kind,
            producer_operation,
            producer_after_revision,
            producer_after_sha,
        ) = raw
        event_id = str(event_id or "")
        collection = str(collection or "")
        object_id = str(object_id or "")
        if event_id in seen_proof_events:
            raise PostgresKnowledgeStoreError(
                f"Human authority proof repeats review event {event_id!r}"
            )
        seen_proof_events.add(event_id)
        event = review_by_id.get(event_id)
        try:
            normalized_event_revision = int(event_revision)
            normalized_version_revision = int(version_revision)
            normalized_after_revision = int(producer_after_revision)
        except (TypeError, ValueError):
            normalized_event_revision = normalized_version_revision = 0
            normalized_after_revision = 0
        normalized_artifact = dict(artifact) if isinstance(artifact, Mapping) else {}
        normalized_version_payload = (
            dict(version_payload) if isinstance(version_payload, Mapping) else {}
        )
        proof = {
            "review_event_id": event_id,
            "collection": collection,
            "object_id": object_id,
            "object_revision": normalized_event_revision,
            "reviewer_kind": str(reviewer_kind or ""),
            "decision": str(decision or ""),
            "artifact": normalized_artifact,
            "version_revision": normalized_version_revision,
            "version_content_sha256": str(version_sha or ""),
            "version_payload": normalized_version_payload,
            "producer_change_set_id": str(producer_change_set_id or ""),
            "producer_status": str(producer_status or ""),
            "producer_source_kind": str(producer_source_kind or ""),
            "producer_operation": str(producer_operation or ""),
            "producer_after_revision": normalized_after_revision,
            "producer_after_sha256": str(producer_after_sha or ""),
        }
        if (
            event is None
            or proof["reviewer_kind"] != "human"
            or event["reviewer_kind"] != "human"
            or event["collection"] != collection
            or event["object_id"] != object_id
            or int(event["object_revision"]) != normalized_event_revision
            or event["decision"] != proof["decision"]
            or canonical_json(event["artifact"])
            != canonical_json(normalized_artifact)
        ):
            raise PostgresKnowledgeStoreError(
                f"Human authority review-event proof is inconsistent: {event_id!r}"
            )
        proof_by_key.setdefault((collection, object_id), []).append(proof)

    scanned_heads: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    seen_heads: set[tuple[str, str]] = set()
    for raw in head_rows:
        if len(raw) != 15:
            raise PostgresKnowledgeStoreError(
                "Human authority head rows require current ObjectVersion provenance"
            )
        (
            collection,
            object_id,
            revision_value,
            content_sha,
            payload,
            retired_at,
            head_change_set_id,
            version_revision,
            version_sha,
            version_payload,
            producer_status,
            producer_source_kind,
            producer_operation,
            producer_after_revision,
            producer_after_sha,
        ) = raw
        collection = str(collection or "")
        object_id = str(object_id or "")
        key = (collection, object_id)
        if collection not in CLAIM_EVIDENCE_COLLECTIONS or key in seen_heads:
            raise PostgresKnowledgeStoreError(
                f"Human authority head is invalid or repeated: {collection}/{object_id}"
            )
        seen_heads.add(key)
        normalized = _claim_evidence_row(
            (collection, object_id, revision_value, content_sha, payload)
        )
        revision = normalized[2]
        content_sha = normalized[3]
        payload = normalized[4]
        head_change_set_id = str(head_change_set_id or "")
        try:
            normalized_version_revision = int(version_revision)
            normalized_after_revision = int(producer_after_revision)
        except (TypeError, ValueError) as exc:
            raise PostgresKnowledgeStoreError(
                f"{collection}/{object_id} current producer revision is invalid"
            ) from exc
        if (
            normalized_version_revision != revision
            or str(version_sha or "") != content_sha
            or not isinstance(version_payload, Mapping)
            or record_content_sha(version_payload) != content_sha
            or canonical_json(
                {key: value for key, value in version_payload.items() if key != "revision"}
            )
            != canonical_json(
                {key: value for key, value in payload.items() if key != "revision"}
            )
            or not head_change_set_id
            or str(producer_status or "") != "applied"
            or str(producer_operation or "")
            not in {"create", "update", "retire", "revive"}
            or normalized_after_revision != revision
            or str(producer_after_sha or "") != content_sha
        ):
            raise PostgresKnowledgeStoreError(
                f"{collection}/{object_id} current ObjectVersion producer is incomplete"
            )
        scanned_heads.append(
            {
                "collection": collection,
                "object_id": object_id,
                "revision": revision,
                "content_sha256": content_sha,
                "retired": retired_at is not None,
                "producer_change_set_id": head_change_set_id,
            }
        )

        status = str(payload.get("review_status") or "candidate")
        valid_authorities: list[dict[str, Any]] = []
        for proof in proof_by_key.get(key, []):
            same_semantic_head = (
                proof["decision"] == status
                and proof["version_content_sha256"] == content_sha
                and isinstance(proof["version_payload"], Mapping)
                and record_content_sha(proof["version_payload"]) == content_sha
                and canonical_json(
                    {
                        key: value
                        for key, value in proof["version_payload"].items()
                        if key != "revision"
                    }
                )
                == canonical_json(
                    {key: value for key, value in payload.items() if key != "revision"}
                )
            )
            if not same_semantic_head:
                continue
            producer_valid = (
                proof["object_revision"] == proof["version_revision"]
                and proof["producer_change_set_id"]
                and proof["producer_status"] == "applied"
                and proof["producer_source_kind"] == "review_decision"
                and proof["producer_operation"]
                in {"create", "update", "revive"}
                and proof["producer_after_revision"] == proof["version_revision"]
                and proof["producer_after_sha256"]
                == proof["version_content_sha256"]
                and str(proof["artifact"].get("change_set_id") or "")
                == proof["producer_change_set_id"]
            )
            if not producer_valid:
                raise PostgresKnowledgeStoreError(
                    f"{collection}/{object_id} human event is not bound to its "
                    "review-decision ObjectVersion producer"
                )
            valid_authorities.append(proof)

        requires_human = status in {"approved", "human_approved"}
        if not valid_authorities:
            if requires_human:
                raise PostgresKnowledgeStoreError(
                    f"{collection}/{object_id} approved head lacks ledger-proven human authority"
                )
            continue
        latest_revision = max(
            int(proof["object_revision"]) for proof in valid_authorities
        )
        latest = [
            proof
            for proof in valid_authorities
            if int(proof["object_revision"]) == latest_revision
        ]
        events_at_authority_revision = events_by_key.get(
            (collection, object_id, latest_revision), []
        )
        if len(latest) != 1 or len(events_at_authority_revision) != 1:
            raise PostgresKnowledgeStoreError(
                f"{collection}/{object_id} has ambiguous human authority"
            )
        authority = latest[0]
        event = review_by_id[authority["review_event_id"]]
        projection_field = (
            "evidence_step_ids" if collection == "claims" else "produced_claim_ids"
        )
        projection = payload.get(projection_field) or []
        if not isinstance(projection, list) or any(not str(value) for value in projection):
            raise PostgresKnowledgeStoreError(
                f"{collection}/{object_id}.{projection_field} must be an ID array"
            )
        protected.append(
            {
                "collection": collection,
                "object_id": object_id,
                "revision": revision,
                "content_sha256": content_sha,
                "retired": retired_at is not None,
                "review_status": status,
                "human_decision": authority["decision"],
                "reviewed_revision": latest_revision,
                "producer_change_set_id": authority["producer_change_set_id"],
                "head_producer_change_set_id": head_change_set_id,
                "review_event_id": authority["review_event_id"],
                "review_event_sha256": sha256_json(event),
                "projection_field": projection_field,
                "projection_ids": [str(value) for value in projection],
            }
        )

    scanned_heads.sort(key=lambda row: (row["collection"], row["object_id"]))
    protected.sort(key=lambda row: (row["collection"], row["object_id"]))
    review_snapshot = build_review_event_ledger_snapshot(review_rows)
    result = {
        "schema_version": CLAIM_EVIDENCE_HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION,
        "scan_scope": "all_current_object_heads_claims_and_evidence_steps",
        "scanned_record_count": len(scanned_heads),
        "scanned_heads": scanned_heads,
        "scanned_heads_sha256": sha256_json(scanned_heads),
        "review_event_ledger_count": review_snapshot["count"],
        "review_event_ledger_snapshot": review_snapshot,
        "protected_records": protected,
        "protected_records_sha256": sha256_json(protected),
        "counts": {
            "protected_records": len(protected),
            "claims": sum(row["collection"] == "claims" for row in protected),
            "evidence_steps": sum(
                row["collection"] == "evidence_steps" for row in protected
            ),
        },
    }
    return _sealed_artifact(result)


def _validate_claim_evidence_human_authority_snapshot(
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    value = json.loads(canonical_json(artifact))
    if value.get("schema_version") != (
        CLAIM_EVIDENCE_HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION
    ):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source queue human-authority snapshot schema is invalid"
        )
    claimed_seal = str(value.pop("artifact_sha256", ""))
    if claimed_seal != sha256_json(value):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source queue human-authority snapshot seal is invalid"
        )
    value["artifact_sha256"] = claimed_seal
    ledger = value.get("review_event_ledger_snapshot")
    if not isinstance(ledger, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source queue requires the full review-event ledger root"
        )
    _validate_review_event_ledger_snapshot(ledger)
    if value.get("review_event_ledger_count") != ledger.get("count"):
        raise PostgresKnowledgeStoreError(
            "Human-authority review-event ledger count differs from its root"
        )
    heads = value.get("scanned_heads")
    protected = value.get("protected_records")
    if not isinstance(heads, list) or not isinstance(protected, list):
        raise PostgresKnowledgeStoreError(
            "Human-authority snapshot lacks its full current-head denominator"
        )
    ordered_heads = sorted(
        heads, key=lambda row: (str(row.get("collection")), str(row.get("object_id")))
    )
    ordered_protected = sorted(
        protected,
        key=lambda row: (str(row.get("collection")), str(row.get("object_id"))),
    )
    head_keys = [
        (str(row.get("collection") or ""), str(row.get("object_id") or ""))
        for row in heads
        if isinstance(row, Mapping)
    ]
    protected_keys = [
        (str(row.get("collection") or ""), str(row.get("object_id") or ""))
        for row in protected
        if isinstance(row, Mapping)
    ]
    if (
        heads != ordered_heads
        or protected != ordered_protected
        or len(head_keys) != len(heads)
        or len(head_keys) != len(set(head_keys))
        or len(protected_keys) != len(protected)
        or len(protected_keys) != len(set(protected_keys))
        or not set(protected_keys).issubset(set(head_keys))
        or value.get("scanned_record_count") != len(heads)
        or value.get("scanned_heads_sha256") != sha256_json(heads)
        or value.get("protected_records_sha256") != sha256_json(protected)
    ):
        raise PostgresKnowledgeStoreError(
            "Human-authority snapshot head/protected manifests differ"
        )
    expected_counts = {
        "protected_records": len(protected),
        "claims": sum(row.get("collection") == "claims" for row in protected),
        "evidence_steps": sum(
            row.get("collection") == "evidence_steps" for row in protected
        ),
    }
    if value.get("counts") != expected_counts:
        raise PostgresKnowledgeStoreError(
            "Human-authority snapshot counts differ"
        )
    for label, rows in (("head", heads), ("protected", protected)):
        for row in rows:
            if (
                row.get("collection") not in CLAIM_EVIDENCE_COLLECTIONS
                or not str(row.get("object_id") or "")
                or not isinstance(row.get("revision"), int)
                or int(row["revision"]) < 1
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(row.get("content_sha256") or "")
                )
                or not str(
                    row.get(
                        "head_producer_change_set_id"
                        if label == "protected"
                        else "producer_change_set_id"
                    )
                    or ""
                )
            ):
                raise PostgresKnowledgeStoreError(
                    f"Human-authority {label} manifest row is invalid"
                )
    return value


def _validate_claim_evidence_freeze_binding(binding: Mapping[str, Any]) -> None:
    if binding.get("schema_version") != CLAIM_EVIDENCE_FREEZE_BINDING_SCHEMA_VERSION:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard has an unsupported freeze binding"
        )
    artifact_sha = str(binding.get("frozen_input_artifact_sha256") or "")
    frozen_at = str(binding.get("frozen_at") or "")
    database_identity = binding.get("database_identity")
    try:
        parsed_frozen_at = datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard freeze timestamp is invalid"
        ) from exc
    if (
        not re.fullmatch(r"[0-9a-f]{64}", artifact_sha)
        or parsed_frozen_at.tzinfo is None
        or not isinstance(database_identity, Mapping)
        or not str(database_identity.get("database_name") or "")
    ):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard freeze binding is incomplete"
        )
    sealed = dict(binding)
    observed_seal = str(sealed.pop("binding_sha256", ""))
    if observed_seal != sha256_json(sealed):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard freeze binding seal is invalid"
        )


def build_source_lineage_identity_snapshot(
    rows: Iterable[Mapping[str, Any] | Sequence[Any]],
) -> dict[str, Any]:
    """Seal referenced SourceFragment/SourceDocument row identities."""

    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in rows:
        if isinstance(raw, Mapping):
            collection = str(raw.get("collection") or "")
            object_id = str(raw.get("object_id") or "")
            revision = raw.get("revision")
            content_sha256 = str(raw.get("content_sha256") or "")
            retired = raw.get("retired")
        else:
            try:
                collection, object_id, revision, content_sha256, _payload, retired_at = raw
            except (TypeError, ValueError) as exc:
                raise PostgresKnowledgeStoreError(
                    "Source-lineage snapshot rows require collection, object_id, "
                    "revision, content SHA, payload and retired_at"
                ) from exc
            collection = str(collection)
            object_id = str(object_id)
            content_sha256 = str(content_sha256)
            retired = retired_at is not None
        key = (collection, object_id)
        try:
            normalized_revision = int(revision)
        except (TypeError, ValueError) as exc:
            raise PostgresKnowledgeStoreError(
                f"Source-lineage row {collection}/{object_id} has invalid revision"
            ) from exc
        if (
            collection not in {"source_fragments", "source_documents"}
            or not object_id
            or key in seen
            or normalized_revision <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", content_sha256)
            or not isinstance(retired, bool)
        ):
            raise PostgresKnowledgeStoreError(
                f"Source-lineage snapshot has invalid row {collection}/{object_id}"
            )
        seen.add(key)
        records.append(
            {
                "collection": collection,
                "object_id": object_id,
                "revision": normalized_revision,
                "content_sha256": content_sha256,
                "retired": retired,
            }
        )
    records.sort(key=lambda row: (row["collection"], row["object_id"]))
    snapshot = {
        "schema_version": SOURCE_LINEAGE_IDENTITY_SNAPSHOT_SCHEMA_VERSION,
        "records": records,
        "records_sha256": sha256_json(records),
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    return snapshot


def _validate_source_lineage_identity_snapshot(
    snapshot: Mapping[str, Any],
) -> None:
    if snapshot.get("schema_version") != SOURCE_LINEAGE_IDENTITY_SNAPSHOT_SCHEMA_VERSION:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard has an unsupported source-lineage snapshot"
        )
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard lacks source-lineage row identities"
        )
    try:
        rebuilt = build_source_lineage_identity_snapshot(records)
    except PostgresKnowledgeStoreError:
        raise
    if rebuilt != snapshot:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard source-lineage snapshot seal is invalid"
        )


def _validate_product_dependency_active_snapshot(
    snapshot: Mapping[str, Any],
) -> None:
    if snapshot.get("schema_version") != PRODUCT_DEPENDENCY_ACTIVE_SNAPSHOT_SCHEMA_VERSION:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard has an unsupported ProductDependency snapshot"
        )
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard lacks full ProductDependency identities"
        )
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping):
            raise PostgresKnowledgeStoreError(
                "Claim/Evidence guard has malformed ProductDependency identity"
            )
        object_id = str(row.get("object_id") or "")
        if (
            not object_id
            or object_id in seen
            or not isinstance(row.get("revision"), int)
            or int(row["revision"]) <= 0
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(row.get("content_sha256") or "")
            )
        ):
            raise PostgresKnowledgeStoreError(
                "Claim/Evidence guard has invalid ProductDependency identity"
            )
        seen.add(object_id)
    if snapshot.get("records_sha256") != sha256_json(records):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard ProductDependency record root is invalid"
        )
    sealed = dict(snapshot)
    observed_seal = str(sealed.pop("snapshot_sha256", ""))
    if observed_seal != sha256_json(sealed):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard ProductDependency snapshot seal is invalid"
        )


def _validate_claim_evidence_active_snapshot(
    snapshot: Mapping[str, Any],
) -> None:
    if snapshot.get("schema_version") != CLAIM_EVIDENCE_ACTIVE_SNAPSHOT_SCHEMA_VERSION:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard has an unsupported active snapshot schema"
        )
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard active snapshot lacks full record identities"
        )
    record_keys: set[tuple[str, str]] = set()
    for row in records:
        if not isinstance(row, Mapping):
            raise PostgresKnowledgeStoreError(
                "Claim/Evidence guard active snapshot has a malformed record"
            )
        collection = str(row.get("collection") or "")
        object_id = str(row.get("object_id") or "")
        if (
            collection not in CLAIM_EVIDENCE_COLLECTIONS
            or not object_id
            or not isinstance(row.get("revision"), int)
            or int(row["revision"]) <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("content_sha256") or ""))
        ):
            raise PostgresKnowledgeStoreError(
                "Claim/Evidence guard active snapshot has an invalid record identity"
            )
        key = (collection, object_id)
        if key in record_keys:
            raise PostgresKnowledgeStoreError(
                f"Claim/Evidence guard active snapshot repeats {collection}/{object_id}"
            )
        record_keys.add(key)
    if snapshot.get("records_sha256") != sha256_json(records):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard active snapshot record root is invalid"
        )
    if not isinstance(snapshot.get("counts"), Mapping) or not re.fullmatch(
        r"[0-9a-f]{64}", str(snapshot.get("pair_state_sha256") or "")
    ):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard active snapshot lacks pair-state counts or root"
        )
    sealed = dict(snapshot)
    observed_seal = str(sealed.pop("snapshot_sha256", ""))
    if observed_seal != sha256_json(sealed):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence guard active snapshot seal is invalid"
        )


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
    incoming: Mapping[str, Any],
    existing: Optional[Mapping[str, Any]],
    *,
    existing_reviewer_kind: str | None = None,
) -> dict[str, Any]:
    result = dict(incoming)
    # Package ingest cannot make an owner ruling; that authority belongs to
    # record_review, which writes the record revision and human event together.
    # Here we only carry forward a ruling already bound to current store state.
    if not existing:
        return result
    existing_status = existing.get("review_status", "candidate")
    human_settled = _is_human_settled(
        existing_status, existing_reviewer_kind=existing_reviewer_kind
    )
    # ``superseded`` is not an authority level: both the AI consensus applier
    # and a human reviewer can produce it.  Preserve it only when the event
    # ledger proves that the current ruling was human; otherwise a later exact
    # review generation must be allowed to revive or reclassify the claim.
    if not human_settled:
        return result
    for field in REVIEW_FIELDS:
        if field in existing:
            result[field] = existing[field]
    return result


def _is_human_settled(
    review_status: Any, *, existing_reviewer_kind: str | None
) -> bool:
    status = str(review_status or "candidate")
    return status in {"approved", "human_approved"} or (
        status == "superseded" and existing_reviewer_kind == "human"
    )


def _substantive_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The revision content that an old review decision actually covered."""

    return {
        key: value
        for key, value in payload.items()
        if key not in REVIEW_FIELDS
    }


def _contains_exact_value(value: Any, target: str) -> bool:
    """Find an object ID as a JSON value, not as a substring of prose or another ID."""

    if isinstance(value, str):
        return value == target
    if isinstance(value, Mapping):
        return any(_contains_exact_value(child, target) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_exact_value(child, target) for child in value)
    return False


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


@dataclass(frozen=True)
class PlannedReviewEvent:
    review_event_id: str
    collection: str
    object_id: str
    object_revision: int
    reviewer_kind: str
    reviewer_id: str
    decision: str
    reason: str
    artifact: dict[str, Any]


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


def review_event_fingerprint_rows(
    events: Sequence[PlannedReviewEvent],
) -> list[dict[str, Any]]:
    return [asdict(row) for row in events]


def planned_ai_review_events(
    package: Mapping[str, Any],
    operations: Sequence[ChangeOperation],
    *,
    source_sha256: str,
) -> tuple[PlannedReviewEvent, ...]:
    """Compile exact-once review events from a sealed candidate manifest."""

    application = package.get("consensus_application")
    if not isinstance(application, Mapping) or application.get("review_completion") != "complete":
        return ()
    rows = application.get("review_resolutions")
    if not isinstance(rows, list):
        raise PostgresKnowledgeStoreError(
            "complete consensus application lacks review_resolutions"
        )
    resolutions = {
        str(row.get("claim_id") or ""): row
        for row in rows
        if isinstance(row, Mapping)
    }
    if len(resolutions) != len(rows) or "" in resolutions:
        raise PostgresKnowledgeStoreError(
            "review_resolutions contain a missing or duplicate claim id"
        )
    events: list[PlannedReviewEvent] = []
    for operation in operations:
        if operation.collection != "claims" or operation.operation not in {"create", "update"}:
            continue
        resolution = resolutions.get(operation.object_id)
        if resolution is None:
            raise PostgresKnowledgeStoreError(
                f"claims/{operation.object_id}: complete review manifest has no resolution"
            )
        target = str(resolution.get("target_review_status") or "")
        actual = str(operation.payload.get("review_status") or "candidate")
        # An existing human ruling outranks this AI run. preserve_human_review
        # has already put it back into operation.payload, so no lower-authority
        # event may contradict it.
        if actual in {"approved", "human_approved", "superseded"} and actual != target:
            continue
        if actual != target or target not in {
            "ai_consensus_reviewed",
            "human_review_required",
            "superseded",
        }:
            raise PostgresKnowledgeStoreError(
                f"claims/{operation.object_id}: review resolution disagrees with final payload"
            )
        superseded_by = str(operation.payload.get("superseded_by") or "")
        if (target == "superseded") != bool(superseded_by):
            raise PostgresKnowledgeStoreError(
                f"claims/{operation.object_id}: superseded target and survivor link disagree"
            )
        reviewer_id = str(resolution.get("reviewer_id") or "").strip()
        reason = str(resolution.get("reason") or "").strip()
        if not reviewer_id or not reason:
            raise PostgresKnowledgeStoreError(
                f"claims/{operation.object_id}: review resolution lacks reviewer or reason"
            )
        artifact = {
            "reviewed_candidate_sha256": source_sha256,
            "reviewed_candidate_artifact_sha256": application.get("artifact_sha256"),
            "review_artifact_sha256": application.get("review_artifact_sha256"),
            "review_fingerprint": application.get("review_fingerprint"),
            "adjudication_artifact_sha256": application.get("adjudication_artifact_sha256"),
            "adjudication_fingerprint": application.get("adjudication_fingerprint"),
            "overrides_artifact_sha256": application.get("overrides_artifact_sha256"),
            "resolution": dict(resolution),
        }
        identity = {
            "collection": "claims",
            "object_id": operation.object_id,
            "object_revision": operation.after_revision,
            "after_sha256": operation.after_sha256,
            "artifact": artifact,
        }
        events.append(
            PlannedReviewEvent(
                review_event_id=f"REV-AI-{sha256_json(identity)[:32]}",
                collection="claims",
                object_id=operation.object_id,
                object_revision=operation.after_revision,
                reviewer_kind="ai",
                reviewer_id=reviewer_id,
                decision=target,
                reason=reason,
                artifact=artifact,
            )
        )
    return tuple(events)


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
    review_events: tuple[PlannedReviewEvent, ...] = ()

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
        if self.review_events:
            value["summary"]["review_events"] = len(self.review_events)
        return value


@dataclass(frozen=True)
class _ClaimEvidenceSourceQueueApplyContext:
    token: object
    expected_source_generations: tuple[dict[str, Any], ...]
    expected_human_authority_snapshot: dict[str, Any]


def _claim_evidence_plan_identity(plan: ChangeSetPlan) -> dict[str, Any]:
    return {
        "change_set_id": plan.change_set_id,
        "fingerprint_sha256": plan.fingerprint_sha256,
        "package_id": plan.package_id,
        "source_kind": plan.source_kind,
        "source_sha256": plan.source_sha256,
        "operations_sha256": sha256_json(operation_fingerprint_rows(plan.operations)),
        "review_events_sha256": sha256_json(
            review_event_fingerprint_rows(plan.review_events)
        ),
    }


def _validate_claim_evidence_source_queue_apply(
    plan: ChangeSetPlan,
    metadata: Optional[Mapping[str, Any]],
    expected_source_generations: Sequence[Mapping[str, Any]],
    expected_human_authority_snapshot: Mapping[str, Any],
) -> _ClaimEvidenceSourceQueueApplyContext:
    if plan.source_kind not in CLAIM_EVIDENCE_SOURCE_QUEUE_SOURCE_KINDS:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source-queue apply requires its dedicated source kind"
        )
    if not plan.operations:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source-queue apply requires a non-empty ChangeSet"
        )
    if not isinstance(metadata, Mapping) or set(metadata) != {
        CLAIM_EVIDENCE_SOURCE_QUEUE_METADATA_KEY
    }:
        raise PostgresKnowledgeStoreError(
            "Dedicated Claim/Evidence source work requires only its sealed queue metadata"
        )
    queue = metadata.get(CLAIM_EVIDENCE_SOURCE_QUEUE_METADATA_KEY)
    if not isinstance(queue, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source-queue metadata must be an object"
        )
    required = {
        "execution_plan_sha256",
        "work_unit_sha256",
        "source_queue_sha256",
        "authority_validation_sha256",
        "package_proof",
        "expected_source_generations_sha256",
        "expected_human_authority_snapshot_sha256",
        "human_impact_sha256",
    }
    if set(queue) != required:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source-queue metadata fields are incomplete or unexpected"
        )
    for field in required - {"package_proof"}:
        _required_sha256(queue.get(field), label=f"source-queue metadata {field}")

    generations = _normalize_claim_evidence_source_generations(
        expected_source_generations
    )
    human_snapshot = _validate_claim_evidence_human_authority_snapshot(
        expected_human_authority_snapshot
    )
    if (
        queue["expected_source_generations_sha256"] != sha256_json(generations)
        or queue["expected_human_authority_snapshot_sha256"]
        != human_snapshot["artifact_sha256"]
    ):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source-queue metadata is bound to another CAS snapshot"
        )

    proof = queue.get("package_proof")
    if not isinstance(proof, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source-queue package proof must be an object"
        )
    shared_fields = {"kind", "effective_canonical_sha256", "guarded_apply_source_kind"}
    if plan.source_kind == CLAIM_EVIDENCE_SOURCE_REPLAY_SOURCE_KIND:
        expected_proof_fields = shared_fields | {
            "authority_unit_id",
            "historical_source_kind",
        }
        proof_valid = (
            proof.get("kind") == "historical_exact_replay"
            and bool(str(proof.get("authority_unit_id") or "").strip())
            and bool(str(proof.get("historical_source_kind") or "").strip())
            and proof.get("historical_source_kind")
            not in CLAIM_EVIDENCE_SOURCE_QUEUE_SOURCE_KINDS
        )
    else:
        expected_proof_fields = shared_fields | {
            "rerun_candidate_receipt_sha256"
        }
        proof_valid = proof.get("kind") == "governed_source_rerun"
        _required_sha256(
            proof.get("rerun_candidate_receipt_sha256"),
            label="source-queue rerun candidate receipt",
        )
    effective_sha = _required_sha256(
        proof.get("effective_canonical_sha256"),
        label="source-queue effective package",
    )
    if (
        set(proof) != expected_proof_fields
        or not proof_valid
        or proof.get("guarded_apply_source_kind") != plan.source_kind
        or effective_sha != plan.source_sha256
    ):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence source-queue package proof differs from its ChangeSet"
        )

    frozen_ids = {
        row["active_source_document_id"] for row in generations
    }
    frozen_keys = {(row["source_type"], row["row_key"]) for row in generations}
    for operation in plan.operations:
        if operation.collection != "source_documents":
            continue
        planned_payload = stored_operation_payload(operation)
        planned_key = (
            str(planned_payload.get("source_type") or "").strip(),
            _source_document_row_key(planned_payload),
        )
        if operation.object_id in frozen_ids or planned_key in frozen_keys:
            raise PostgresKnowledgeStoreError(
                "Source-queue ChangeSet may not mutate its frozen SourceDocument generation"
            )
    return _ClaimEvidenceSourceQueueApplyContext(
        token=_CLAIM_EVIDENCE_SOURCE_QUEUE_APPLY_TOKEN,
        expected_source_generations=tuple(
            json.loads(canonical_json(row)) for row in generations
        ),
        expected_human_authority_snapshot=human_snapshot,
    )


def build_claim_evidence_reciprocity_guard(
    plan: ChangeSetPlan,
    expected_active_snapshot: Mapping[str, Any],
    expected_product_dependency_snapshot: Mapping[str, Any] | None = None,
    expected_review_event_ledger_snapshot: Mapping[str, Any] | None = None,
    expected_freeze_binding: Mapping[str, Any] | None = None,
    expected_source_lineage_snapshot: Mapping[str, Any] | None = None,
    expected_pair_adjudication_authorization: Mapping[str, Any] | None = None,
    *,
    expected_final_source_lineage_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind one sealed full active snapshot to exactly one ChangeSet plan."""

    _validate_claim_evidence_active_snapshot(expected_active_snapshot)
    if expected_product_dependency_snapshot is None:
        if plan.source_kind in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS:
            raise PostgresKnowledgeStoreError(
                "Dedicated Claim/Evidence repair requires a full ProductDependency snapshot"
            )
        expected_product_dependency_snapshot = (
            build_product_dependency_active_snapshot(())
        )
    _validate_product_dependency_active_snapshot(
        expected_product_dependency_snapshot
    )
    if expected_review_event_ledger_snapshot is None:
        if plan.source_kind in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS:
            raise PostgresKnowledgeStoreError(
                "Dedicated Claim/Evidence repair requires the full review-event "
                "ledger root and count"
            )
        expected_review_event_ledger_snapshot = build_review_event_ledger_snapshot(())
    _validate_review_event_ledger_snapshot(expected_review_event_ledger_snapshot)
    if expected_freeze_binding is not None:
        _validate_claim_evidence_freeze_binding(expected_freeze_binding)
    if expected_source_lineage_snapshot is None:
        expected_source_lineage_snapshot = build_source_lineage_identity_snapshot(())
    _validate_source_lineage_identity_snapshot(expected_source_lineage_snapshot)
    if expected_final_source_lineage_snapshot is not None:
        _validate_source_lineage_identity_snapshot(
            expected_final_source_lineage_snapshot
        )
    snapshot = json.loads(canonical_json(expected_active_snapshot))
    guard = {
        "schema_version": CLAIM_EVIDENCE_RECIPROCITY_GUARD_SCHEMA_VERSION,
        "plan_identity": _claim_evidence_plan_identity(plan),
        "expected_active_snapshot": snapshot,
        "expected_product_dependency_snapshot": json.loads(
            canonical_json(expected_product_dependency_snapshot)
        ),
        "expected_review_event_ledger_snapshot": json.loads(
            canonical_json(expected_review_event_ledger_snapshot)
        ),
        "expected_freeze_binding": (
            json.loads(canonical_json(expected_freeze_binding))
            if expected_freeze_binding is not None
            else None
        ),
        "expected_source_lineage_snapshot": json.loads(
            canonical_json(expected_source_lineage_snapshot)
        ),
        "expected_pair_adjudication_authorization": (
            json.loads(canonical_json(expected_pair_adjudication_authorization))
            if expected_pair_adjudication_authorization is not None
            else None
        ),
    }
    # Dedicated pair repairs do not change source lineage, so their established
    # guard remains byte-for-byte compatible. A source extraction can create a
    # new SourceDocument/SourceFragment denominator in the same ChangeSet; bind
    # that post-state separately from the current production denominator.
    if expected_final_source_lineage_snapshot is not None:
        guard["expected_final_source_lineage_snapshot"] = json.loads(
            canonical_json(expected_final_source_lineage_snapshot)
        )
    guard["guard_sha256"] = sha256_json(guard)
    return guard


def _validate_claim_evidence_reciprocity_guard(
    plan: ChangeSetPlan,
    guard: Mapping[str, Any],
) -> dict[str, Any]:
    if guard.get("schema_version") != CLAIM_EVIDENCE_RECIPROCITY_GUARD_SCHEMA_VERSION:
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair has an unsupported store guard schema"
        )
    sealed = dict(guard)
    observed_seal = str(sealed.pop("guard_sha256", ""))
    if observed_seal != sha256_json(sealed):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair store guard seal is invalid"
        )
    if guard.get("plan_identity") != _claim_evidence_plan_identity(plan):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair store guard is bound to another ChangeSet plan"
        )
    snapshot = guard.get("expected_active_snapshot")
    if not isinstance(snapshot, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair store guard lacks its expected active snapshot"
        )
    _validate_claim_evidence_active_snapshot(snapshot)
    dependency_snapshot = guard.get("expected_product_dependency_snapshot")
    if not isinstance(dependency_snapshot, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair guard lacks ProductDependency snapshot"
        )
    _validate_product_dependency_active_snapshot(dependency_snapshot)
    review_snapshot = guard.get("expected_review_event_ledger_snapshot")
    if not isinstance(review_snapshot, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair guard lacks review-event ledger snapshot"
        )
    _validate_review_event_ledger_snapshot(review_snapshot)
    freeze_binding = guard.get("expected_freeze_binding")
    if freeze_binding is not None:
        if not isinstance(freeze_binding, Mapping):
            raise PostgresKnowledgeStoreError(
                "Claim/Evidence repair guard has malformed freeze binding"
            )
        _validate_claim_evidence_freeze_binding(freeze_binding)
    source_lineage_snapshot = guard.get("expected_source_lineage_snapshot")
    if not isinstance(source_lineage_snapshot, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair guard lacks source-lineage snapshot"
        )
    _validate_source_lineage_identity_snapshot(source_lineage_snapshot)
    final_source_lineage_snapshot = guard.get(
        "expected_final_source_lineage_snapshot"
    )
    if final_source_lineage_snapshot is not None:
        if not isinstance(final_source_lineage_snapshot, Mapping):
            raise PostgresKnowledgeStoreError(
                "Claim/Evidence repair guard has malformed final source-lineage snapshot"
            )
        _validate_source_lineage_identity_snapshot(final_source_lineage_snapshot)
    pair_authorization = guard.get("expected_pair_adjudication_authorization")
    if plan.source_kind == CLAIM_EVIDENCE_PAIR_ADJUDICATION_SOURCE_KIND:
        if not isinstance(pair_authorization, Mapping):
            raise PostgresKnowledgeStoreError(
                "Pair-adjudication repair guard lacks its sealed authorization"
            )
    elif pair_authorization is not None:
        raise PostgresKnowledgeStoreError(
            "Pair-adjudication authorization cannot govern another source kind"
        )
    return json.loads(canonical_json(guard))


def _resolve_claim_evidence_reciprocity_guard(
    plan: ChangeSetPlan,
    metadata: Optional[Mapping[str, Any]],
    explicit_guard: Optional[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    repair_metadata = (metadata or {}).get(
        CLAIM_EVIDENCE_RECIPROCITY_REPAIR_METADATA_KEY
    )
    if repair_metadata is not None and not isinstance(repair_metadata, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair metadata must be an object"
        )
    metadata_guard = (
        repair_metadata.get("store_guard")
        if isinstance(repair_metadata, Mapping)
        else None
    )
    if metadata_guard is not None and not isinstance(metadata_guard, Mapping):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence repair metadata store_guard must be an object"
        )
    if (
        explicit_guard is not None
        and metadata_guard is not None
        and canonical_json(explicit_guard) != canonical_json(metadata_guard)
    ):
        raise PostgresKnowledgeStoreError(
            "Explicit and metadata Claim/Evidence repair guards disagree"
        )
    selected = explicit_guard or metadata_guard
    if (
        plan.source_kind in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS
        and selected is None
    ):
        raise PostgresKnowledgeStoreError(
            "Claim/Evidence reciprocity repair requires a sealed store guard"
        )
    if selected is None:
        return None
    return _validate_claim_evidence_reciprocity_guard(plan, selected)


def _validate_claim_evidence_repair_apply_metadata(
    plan: ChangeSetPlan,
    metadata: Optional[Mapping[str, Any]],
    guard: Mapping[str, Any],
) -> None:
    """Require the operational artifact and verified backup at the write boundary."""

    if not plan.operations:
        return
    repair = (metadata or {}).get(CLAIM_EVIDENCE_RECIPROCITY_REPAIR_METADATA_KEY)
    if not isinstance(repair, Mapping):
        raise PostgresKnowledgeStoreError(
            "Dedicated Claim/Evidence repair requires operational apply metadata"
        )
    if canonical_json(repair.get("store_guard")) != canonical_json(guard):
        raise PostgresKnowledgeStoreError(
            "Dedicated Claim/Evidence repair metadata does not bind its store guard"
        )
    required_sha_fields = (
        "audit_artifact_sha256",
        "plan_artifact_sha256",
        "action_manifest_sha256",
        "operation_manifest_sha256",
    )
    for field in required_sha_fields:
        value = str(repair.get(field) or "")
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise PostgresKnowledgeStoreError(
                f"Dedicated Claim/Evidence repair metadata lacks {field}"
            )
    if repair["operation_manifest_sha256"] != sha256_json(
        operation_fingerprint_rows(plan.operations)
    ):
        raise PostgresKnowledgeStoreError(
            "Dedicated Claim/Evidence repair operation manifest does not match plan"
        )
    backup = repair.get("backup")
    if not isinstance(backup, Mapping):
        raise PostgresKnowledgeStoreError(
            "Dedicated Claim/Evidence repair requires verified backup metadata"
        )
    freeze_binding = guard.get("expected_freeze_binding")
    if not isinstance(freeze_binding, Mapping):
        raise PostgresKnowledgeStoreError(
            "Dedicated Claim/Evidence repair requires a frozen-input binding"
        )
    _validate_claim_evidence_freeze_binding(freeze_binding)
    sealed_backup = dict(backup)
    backup_seal = str(sealed_backup.pop("artifact_sha256", ""))
    try:
        size_bytes = int(backup.get("size_bytes") or 0)
        entry_count = int(backup.get("pg_restore_entry_count") or 0)
    except (TypeError, ValueError):
        size_bytes = 0
        entry_count = 0
    try:
        archive_created_at = datetime.fromisoformat(
            str(backup.get("archive_created_at") or "").replace("Z", "+00:00")
        )
        frozen_at = datetime.fromisoformat(
            str(freeze_binding["frozen_at"]).replace("Z", "+00:00")
        )
        timestamp_is_bound = (
            archive_created_at.tzinfo is not None
            and frozen_at.tzinfo is not None
            and archive_created_at
            >= frozen_at.replace(microsecond=0)
        )
    except (TypeError, ValueError):
        timestamp_is_bound = False
    coverage = backup.get("required_table_coverage")
    expected_coverage = [
        {
            "table_name": table_name,
            "has_table_definition": True,
            "has_table_data": True,
        }
        for table_name in CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES
    ]
    coverage_is_complete = (
        coverage == expected_coverage
        and backup.get("required_table_coverage_sha256")
        == sha256_json(expected_coverage)
    )
    if (
        backup.get("schema_version")
        != CLAIM_EVIDENCE_BACKUP_VERIFICATION_SCHEMA_VERSION
        or backup_seal != sha256_json(sealed_backup)
        or not re.fullmatch(r"[0-9a-f]{64}", str(backup.get("sha256") or ""))
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(backup.get("pg_restore_list_sha256") or "")
        )
        or size_bytes <= 0
        or entry_count <= 0
        or not str(backup.get("path") or "")
        or backup.get("contains_wang_knowledge_schema") is not True
        or backup.get("contains_required_table_data") is not True
        or not coverage_is_complete
        or not timestamp_is_bound
        or str(backup.get("frozen_input_artifact_sha256") or "")
        != str(freeze_binding["frozen_input_artifact_sha256"])
        or str(backup.get("frozen_at") or "") != str(freeze_binding["frozen_at"])
        or canonical_json(backup.get("database_identity") or {})
        != canonical_json(freeze_binding["database_identity"])
        or str(backup.get("archive_database_name") or "")
        != str(freeze_binding["database_identity"]["database_name"])
    ):
        raise PostgresKnowledgeStoreError(
            "Dedicated Claim/Evidence repair backup verification is invalid"
        )


def validate_change_set_plan_integrity(plan: ChangeSetPlan) -> None:
    """Reject mutation of nested plan payloads before opening PostgreSQL."""

    operation_index: dict[tuple[str, str, int], ChangeOperation] = {}
    for operation in plan.operations:
        if operation.operation not in {"create", "update"}:
            continue
        actual_sha = record_content_sha(stored_operation_payload(operation))
        if actual_sha != operation.after_sha256:
            raise PostgresKnowledgeStoreError(
                f"planned payload changed after fingerprinting: "
                f"{operation.collection}/{operation.object_id}"
            )
        key = (
            operation.collection,
            operation.object_id,
            operation.after_revision,
        )
        if key in operation_index:
            raise PostgresKnowledgeStoreError(
                f"change set repeats one target revision: {key}"
            )
        operation_index[key] = operation

    event_ids: set[str] = set()
    for event in plan.review_events:
        operation = operation_index.get(
            (event.collection, event.object_id, event.object_revision)
        )
        resolution = event.artifact.get("resolution")
        if (
            operation is None
            or not isinstance(resolution, Mapping)
            or event.reviewer_kind != "ai"
            or event.decision != resolution.get("target_review_status")
            or event.reviewer_id != resolution.get("reviewer_id")
            or event.reason != resolution.get("reason")
        ):
            raise PostgresKnowledgeStoreError(
                f"planned review event no longer matches its claim operation: "
                f"{event.review_event_id}"
            )
        identity = {
            "collection": event.collection,
            "object_id": event.object_id,
            "object_revision": event.object_revision,
            "after_sha256": operation.after_sha256,
            "artifact": event.artifact,
        }
        expected_id = f"REV-AI-{sha256_json(identity)[:32]}"
        if event.review_event_id != expected_id or expected_id in event_ids:
            raise PostgresKnowledgeStoreError(
                f"planned review event identity changed or repeats: "
                f"{event.review_event_id}"
            )
        event_ids.add(expected_id)


def uncoordinated_semantic_reference_blockers(
    plan: ChangeSetPlan,
    rows: Sequence[tuple[str, str, Mapping[str, Any]]],
) -> list[str]:
    """Describe live semantic rows a re-extraction ChangeSet would strand.

    This is deliberately a pure function shared by preview and the locked
    apply guard. A preview that only reports draft-product dependencies can
    otherwise say a replacement is clear even though apply later discovers
    current CVR/topic master data pinned to the retiring extraction generation.
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
        return []
    planned_operations = {
        (operation.collection, operation.object_id): operation
        for operation in plan.operations
    }

    def strings(value: Any) -> set[str]:
        if isinstance(value, Mapping):
            found: set[str] = set()
            for key, child in value.items():
                if isinstance(key, str):
                    found.add(key)
                found.update(strings(child))
            return found
        if isinstance(value, (list, tuple, set)):
            found = set()
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
        found = set()
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
    for collection, object_id, payload in rows:
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
    # Newly created semantic rows are absent from the live-row input.
    for key, planned in planned_operations.items():
        if key[0] not in SEMANTIC_REFERENCE_COLLECTIONS:
            continue
        if planned.operation == "retire":
            continue
        planned_payload = stored_operation_payload(planned)
        references = exact_references(key[0], planned_payload)
        if references and not any(
            item.startswith(f"planned {key[0]}/{key[1]} ") for item in blockers
        ):
            blockers.append(
                f"planned {key[0]}/{key[1]} still -> "
                f"{','.join(sorted(references))}"
            )
        unknown = unclassified_references(key[0], planned_payload)
        if unknown and not any(
            item.startswith(f"planned {key[0]}/{key[1]} has unclassified ")
            for item in blockers
        ):
            blockers.append(
                f"planned {key[0]}/{key[1]} has unclassified id field "
                f"{','.join(sorted(unknown))}"
            )
    return sorted(blockers)


def build_change_set_plan(
    package: Mapping[str, Any],
    existing: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    source_kind: str = "knowledge_package",
    existing_review_authorities: Mapping[tuple[str, str], str] | None = None,
) -> ChangeSetPlan:
    try:
        validate_store_package_authorization(package)
    except ConsensusApplicationError as exc:
        raise PostgresKnowledgeStoreError(
            f"reviewed candidate cannot enter the canonical store: {exc}"
        ) from exc
    normalized, stated = _normalize_records(package)
    review_targets = {
        str(row.get("claim_id") or ""): str(
            row.get("target_review_status") or ""
        )
        for row in (
            (package.get("consensus_application") or {}).get(
                "review_resolutions"
            )
            or []
        )
        if isinstance(row, Mapping)
    }
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
            declared_status = str(
                normalized[collection][object_id].get("review_status")
                or "candidate"
            )
            current_status = str(
                (current_payload or {}).get("review_status") or "candidate"
            )
            if (
                collection == "claims"
                and declared_status in {"approved", "human_approved"}
                and declared_status != current_status
            ):
                raise PostgresKnowledgeStoreError(
                    f"{collection}/{object_id}: package ingest cannot create or "
                    "change a human approval; use record_review"
                )
            merged = merge_over_existing(
                normalized[collection][object_id],
                current_payload,
                stated[(collection, object_id)],
            )
            existing_reviewer_kind = (existing_review_authorities or {}).get(
                (collection, object_id)
            )
            if (
                collection == "claims"
                and current_payload
                and current_payload.get("review_status") == "superseded"
                and review_targets.get(object_id) not in {None, "", "superseded"}
            ):
                # `superseded_by` is substantive and normally survives an
                # omitted field.  Here the sealed next-generation resolution
                # explicitly revives the claim, so retaining the old AI merge
                # target would contradict that exact reviewed candidate.
                if existing_reviewer_kind == "ai":
                    merged.pop("superseded_by", None)
                elif existing_reviewer_kind != "human":
                    raise PostgresKnowledgeStoreError(
                        f"claims/{object_id}: cannot replace superseded status "
                        "without a review event bound to the current revision"
                    )
            if (
                collection == "claims"
                and current_payload
                and current_payload.get("review_status") == "superseded"
                and existing_reviewer_kind not in {"ai", "human"}
                and _substantive_payload(merged)
                != _substantive_payload(current_payload)
            ):
                raise PostgresKnowledgeStoreError(
                    f"claims/{object_id}: cannot change a superseded claim "
                    "whose review authority is unknown"
                )
            if (
                current_payload
                and _is_human_settled(
                    current_payload.get("review_status"),
                    existing_reviewer_kind=existing_reviewer_kind,
                )
                and _substantive_payload(merged)
                != _substantive_payload(current_payload)
            ):
                raise PostgresKnowledgeStoreError(
                    f"{collection}/{object_id}: incoming package changes content "
                    "covered by an existing human review; a new human ruling is required"
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
            incoming = preserve_human_review(
                merged,
                current_payload,
                existing_reviewer_kind=existing_reviewer_kind,
            )
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
    review_events = planned_ai_review_events(
        package, operations, source_sha256=source_sha
    )
    fingerprint_payload = {
        "planner_schema": "wang_postgres_changeset_v2",
        "source_kind": source_kind,
        "source_sha256": source_sha,
        "package_id": str(package.get("package_id") or ""),
        "operations": operation_fingerprint_rows(operations),
        "review_events": review_event_fingerprint_rows(review_events),
    }
    fingerprint = sha256_json(fingerprint_payload)
    recognized = set(KnowledgePackageImporter.SOURCE_COLLECTION_KEYS) | {
        "product_plans", "schema_version", "package_id", "title", "corpus_scope",
        "framework_candidate", "validation_experiments", "summary", "batch",
        "candidate_generation", "lineage", "approval_status",
        "consensus_application",
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
        review_events=review_events,
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
        "review_events": review_event_fingerprint_rows(arrival.review_events),
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
        review_events=arrival.review_events,
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

    def read_claim_evidence_reciprocity_guard(
        self,
        plan: ChangeSetPlan,
        *,
        preserve_current_bindings: bool = True,
    ) -> dict[str, Any]:
        """Bind a source ChangeSet to the current repaired pair graph.

        A source runner may resume from source-SHA-bound model artifacts, but
        those artifacts are not a snapshot of the canonical store.  Read the
        current Claim/Evidence heads under the same global writer lock used by
        apply, reject an old package that would replace an existing endpoint's
        repaired binding arrays, and seal the complete denominator for the
        transaction-time final-graph check.
        """

        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (POSTGRES_APPLY_ADVISORY_LOCK_KEY,),
            )
            cursor.execute(
                """SELECT collection, object_id, revision, content_sha256, payload
                   FROM wang_knowledge.objects
                   WHERE collection = ANY(%s) AND retired_at IS NULL
                   ORDER BY collection, object_id FOR SHARE""",
                (list(CLAIM_EVIDENCE_COLLECTIONS),),
            )
            endpoint_rows = list(cursor.fetchall())
            active_snapshot = build_claim_evidence_active_snapshot(endpoint_rows)
            active_rows = {
                (row[0], row[1]): row
                for row in (_claim_evidence_row(raw) for raw in endpoint_rows)
            }

            if preserve_current_bindings:
                binding_fields = {
                    "claims": "evidence_step_ids",
                    "evidence_steps": "produced_claim_ids",
                }
                for operation in plan.operations:
                    field = binding_fields.get(operation.collection)
                    if field is None or operation.operation != "update":
                        continue
                    current = active_rows.get(
                        (operation.collection, operation.object_id)
                    )
                    if current is None:
                        continue
                    before = list(current[4].get(field) or [])
                    after = list(stored_operation_payload(operation).get(field) or [])
                    if before != after:
                        raise PostgresKnowledgeStoreError(
                            f"{operation.collection}/{operation.object_id}: "
                            f"source resume would replace current {field}; rebuild "
                            "from the current production baseline or use a newly "
                            "authorized Claim/Evidence adjudication"
                        )

            cursor.execute(
                """SELECT object_id, revision, content_sha256, payload
                   FROM wang_knowledge.objects
                   WHERE collection='product_dependencies' AND retired_at IS NULL
                   ORDER BY object_id FOR SHARE"""
            )
            dependency_snapshot = build_product_dependency_active_snapshot(
                cursor.fetchall()
            )
            review_snapshot = self._review_event_ledger_snapshot(
                cursor, lock_rows=True
            )

            fragment_ids: set[str] = set()
            document_ids: set[str] = set()

            def add_many(
                payload: Mapping[str, Any],
                plural: str,
                singular: str,
                sink: set[str],
            ) -> None:
                raw = payload.get(plural) or []
                if not isinstance(raw, (list, tuple)):
                    raise PostgresKnowledgeStoreError(
                        f"Claim/Evidence source lineage field {plural} is not an array"
                    )
                sink.update(str(value) for value in raw if str(value))
                if payload.get(singular):
                    sink.add(str(payload[singular]))

            for row in active_rows.values():
                payload = row[4]
                add_many(
                    payload,
                    "source_fragment_ids",
                    "source_fragment_id",
                    fragment_ids,
                )
                add_many(
                    payload,
                    "source_document_ids",
                    "source_document_id",
                    document_ids,
                )
                if payload.get("source_id"):
                    document_ids.add(str(payload["source_id"]))

            fragment_rows: list[Sequence[Any]] = []
            if fragment_ids:
                cursor.execute(
                    """SELECT collection, object_id, revision, content_sha256,
                              payload, retired_at
                       FROM wang_knowledge.objects
                       WHERE collection='source_fragments' AND object_id = ANY(%s)
                       ORDER BY object_id FOR SHARE""",
                    (sorted(fragment_ids),),
                )
                fragment_rows = list(cursor.fetchall())
                for row in fragment_rows:
                    payload = row[4]
                    if isinstance(payload, Mapping) and payload.get("source_id"):
                        document_ids.add(str(payload["source_id"]))

            document_rows: list[Sequence[Any]] = []
            if document_ids:
                cursor.execute(
                    """SELECT collection, object_id, revision, content_sha256,
                              payload, retired_at
                       FROM wang_knowledge.objects
                       WHERE collection='source_documents' AND object_id = ANY(%s)
                       ORDER BY object_id FOR SHARE""",
                    (sorted(document_ids),),
                )
                document_rows = list(cursor.fetchall())
            source_snapshot = build_source_lineage_identity_snapshot(
                [*fragment_rows, *document_rows]
            )

            # A fresh extraction creates its source lineage and endpoints in one
            # ChangeSet. Simulate that exact post-state here: apply first proves
            # the current production denominator, then proves this final one.
            final_active_rows = dict(active_rows)
            for operation in plan.operations:
                if operation.collection not in CLAIM_EVIDENCE_COLLECTIONS:
                    continue
                key = (operation.collection, operation.object_id)
                if operation.operation == "retire":
                    final_active_rows.pop(key, None)
                else:
                    final_active_rows[key] = (
                        operation.collection,
                        operation.object_id,
                        operation.after_revision,
                        operation.after_sha256,
                        stored_operation_payload(operation),
                    )

            final_fragment_ids: set[str] = set()
            final_document_ids: set[str] = set()
            for row in final_active_rows.values():
                payload = row[4]
                add_many(
                    payload,
                    "source_fragment_ids",
                    "source_fragment_id",
                    final_fragment_ids,
                )
                add_many(
                    payload,
                    "source_document_ids",
                    "source_document_id",
                    final_document_ids,
                )
                if payload.get("source_id"):
                    final_document_ids.add(str(payload["source_id"]))

            final_fragments = {str(row[1]): row for row in fragment_rows}
            extra_fragment_ids = final_fragment_ids - set(final_fragments)
            if extra_fragment_ids:
                cursor.execute(
                    """SELECT collection, object_id, revision, content_sha256,
                              payload, retired_at
                       FROM wang_knowledge.objects
                       WHERE collection='source_fragments' AND object_id = ANY(%s)
                       ORDER BY object_id FOR SHARE""",
                    (sorted(extra_fragment_ids),),
                )
                final_fragments.update(
                    (str(row[1]), row) for row in cursor.fetchall()
                )
            for operation in plan.operations:
                if operation.collection != "source_fragments":
                    continue
                if operation.operation == "retire":
                    current = final_fragments.get(operation.object_id)
                    if current is not None:
                        final_fragments[operation.object_id] = (*current[:5], True)
                else:
                    final_fragments[operation.object_id] = (
                        "source_fragments",
                        operation.object_id,
                        operation.after_revision,
                        operation.after_sha256,
                        stored_operation_payload(operation),
                        None,
                    )

            missing_fragments = final_fragment_ids - set(final_fragments)
            if missing_fragments:
                raise PostgresKnowledgeStoreError(
                    "Claim/Evidence final graph references missing SourceFragments: "
                    + ", ".join(sorted(missing_fragments)[:10])
                )
            for fragment_id in final_fragment_ids:
                row = final_fragments[fragment_id]
                payload = row[4]
                if (
                    not isinstance(payload, Mapping)
                    or record_content_sha(payload) != str(row[3])
                ):
                    raise PostgresKnowledgeStoreError(
                        f"SourceFragment {fragment_id} content SHA differs from payload"
                    )
                source_id = str(payload.get("source_id") or "")
                if not source_id:
                    raise PostgresKnowledgeStoreError(
                        f"SourceFragment {fragment_id} lacks its SourceDocument lineage"
                    )
                final_document_ids.add(source_id)

            final_documents = {str(row[1]): row for row in document_rows}
            extra_document_ids = final_document_ids - set(final_documents)
            if extra_document_ids:
                cursor.execute(
                    """SELECT collection, object_id, revision, content_sha256,
                              payload, retired_at
                       FROM wang_knowledge.objects
                       WHERE collection='source_documents' AND object_id = ANY(%s)
                       ORDER BY object_id FOR SHARE""",
                    (sorted(extra_document_ids),),
                )
                final_documents.update(
                    (str(row[1]), row) for row in cursor.fetchall()
                )
            for operation in plan.operations:
                if operation.collection != "source_documents":
                    continue
                if operation.operation == "retire":
                    current = final_documents.get(operation.object_id)
                    if current is not None:
                        final_documents[operation.object_id] = (*current[:5], True)
                else:
                    final_documents[operation.object_id] = (
                        "source_documents",
                        operation.object_id,
                        operation.after_revision,
                        operation.after_sha256,
                        stored_operation_payload(operation),
                        None,
                    )

            missing_documents = final_document_ids - set(final_documents)
            if missing_documents:
                raise PostgresKnowledgeStoreError(
                    "Claim/Evidence final graph references missing SourceDocuments: "
                    + ", ".join(sorted(missing_documents)[:10])
                )
            for document_id in final_document_ids:
                row = final_documents[document_id]
                if (
                    not isinstance(row[4], Mapping)
                    or record_content_sha(row[4]) != str(row[3])
                ):
                    raise PostgresKnowledgeStoreError(
                        f"SourceDocument {document_id} content SHA differs from payload"
                    )

            final_source_snapshot = build_source_lineage_identity_snapshot(
                [
                    *(final_fragments[object_id] for object_id in final_fragment_ids),
                    *(final_documents[object_id] for object_id in final_document_ids),
                ]
            )
            guard = build_claim_evidence_reciprocity_guard(
                plan,
                active_snapshot,
                dependency_snapshot,
                review_snapshot,
                expected_source_lineage_snapshot=source_snapshot,
                expected_final_source_lineage_snapshot=final_source_snapshot,
            )
            # Reuse the apply-time validator here so preview proves the exact
            # final graph before returning a plan to an operator.
            self._assert_claim_evidence_reciprocity_guard(cursor, plan, guard)
            return guard

    @staticmethod
    def _claim_evidence_source_generations_from_cursor(
        cursor: Any,
        expected: Sequence[Mapping[str, Any]],
        *,
        lock_rows: bool,
    ) -> list[dict[str, Any]]:
        canonical_expected = _normalize_claim_evidence_source_generations(expected)
        cursor.execute(
            """SELECT object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection='source_documents' AND retired_at IS NULL
               ORDER BY object_id"""
            + (" FOR UPDATE" if lock_rows else " FOR SHARE")
        )
        active = _active_claim_evidence_source_generations(list(cursor.fetchall()))
        return [
            active[key]
            for key in (
                (row["source_type"], row["row_key"])
                for row in canonical_expected
            )
            if key in active
        ]

    def read_claim_evidence_source_generations(
        self, expected_rows: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Read queue SourceDocument generations under the global writer lock."""

        expected = _normalize_claim_evidence_source_generations(expected_rows)
        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (POSTGRES_APPLY_ADVISORY_LOCK_KEY,),
            )
            return self._claim_evidence_source_generations_from_cursor(
                cursor, expected, lock_rows=False
            )

    @staticmethod
    def _claim_evidence_human_authority_from_cursor(
        cursor: Any, *, lock_rows: bool
    ) -> dict[str, Any]:
        cursor.execute(
            """SELECT o.collection, o.object_id, o.revision, o.content_sha256,
                      o.payload, o.retired_at,
                      ov.change_set_id, ov.revision, ov.content_sha256, ov.payload,
                      cs.status, cs.source_kind,
                      co.operation, co.after_revision, co.after_sha256
               FROM wang_knowledge.objects o
               LEFT JOIN wang_knowledge.object_versions ov
                 ON ov.collection=o.collection
                AND ov.object_id=o.object_id
                AND ov.revision=o.revision
               LEFT JOIN wang_knowledge.change_sets cs
                 ON cs.change_set_id=ov.change_set_id
               LEFT JOIN wang_knowledge.change_operations co
                 ON co.change_set_id=ov.change_set_id
                AND co.collection=ov.collection
                AND co.object_id=ov.object_id
                AND co.after_revision=ov.revision
                AND co.after_sha256=ov.content_sha256
               WHERE o.collection = ANY(%s)
               ORDER BY o.collection, o.object_id"""
            + (" FOR UPDATE OF o" if lock_rows else " FOR SHARE OF o"),
            (list(CLAIM_EVIDENCE_COLLECTIONS),),
        )
        heads = list(cursor.fetchall())
        cursor.execute(
            """SELECT review_event_id, collection, object_id, object_revision,
                      reviewer_kind, reviewer_id, decision, reason, artifact,
                      created_at
               FROM wang_knowledge.review_events
               ORDER BY review_event_id FOR SHARE"""
        )
        review_rows = list(cursor.fetchall())
        cursor.execute(
            """SELECT re.review_event_id, re.collection, re.object_id,
                      re.object_revision, re.reviewer_kind, re.decision,
                      re.artifact,
                      ov.revision, ov.content_sha256, ov.payload, ov.change_set_id,
                      cs.status, cs.source_kind,
                      co.operation, co.after_revision, co.after_sha256
               FROM wang_knowledge.review_events re
               LEFT JOIN wang_knowledge.object_versions ov
                 ON ov.collection=re.collection
                AND ov.object_id=re.object_id
                AND ov.revision=re.object_revision
               LEFT JOIN wang_knowledge.change_sets cs
                 ON cs.change_set_id=ov.change_set_id
               LEFT JOIN wang_knowledge.change_operations co
                 ON co.change_set_id=ov.change_set_id
                AND co.collection=ov.collection
                AND co.object_id=ov.object_id
                AND co.after_revision=ov.revision
                AND co.after_sha256=ov.content_sha256
               WHERE re.reviewer_kind='human'
                 AND re.collection = ANY(%s)
               ORDER BY re.collection, re.object_id, re.object_revision,
                        re.review_event_id""",
            (list(CLAIM_EVIDENCE_COLLECTIONS),),
        )
        human_proofs = list(cursor.fetchall())
        return _build_claim_evidence_human_authority_snapshot(
            heads, review_rows, human_proofs
        )

    def read_claim_evidence_current_human_authority(self) -> dict[str, Any]:
        """Freeze all current heads and ledger-proven human Claim/Evidence authority."""

        with self.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (POSTGRES_APPLY_ADVISORY_LOCK_KEY,),
            )
            return self._claim_evidence_human_authority_from_cursor(
                cursor, lock_rows=False
            )

    def apply_claim_evidence_source_queue_plan(
        self,
        plan: ChangeSetPlan,
        *,
        metadata: Mapping[str, Any],
        expected_source_generations: Sequence[Mapping[str, Any]],
        expected_human_authority_snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply one #364 source unit through its mandatory locked CAS gates."""

        context = _validate_claim_evidence_source_queue_apply(
            plan,
            metadata,
            expected_source_generations,
            expected_human_authority_snapshot,
        )
        return self.apply_plan(
            plan,
            metadata=dict(metadata),
            _claim_evidence_source_queue_context=context,
        )

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

        try:
            validate_store_package_authorization(package)
        except ConsensusApplicationError as exc:
            raise PostgresKnowledgeStoreError(
                f"reviewed candidate cannot enter the canonical store: {exc}"
            ) from exc

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
                cursor.execute(
                    """SELECT DISTINCT ON (collection, object_id)
                              collection, object_id, reviewer_kind, decision,
                              object_revision
                       FROM wang_knowledge.review_events
                       WHERE object_id = ANY(%s)
                       ORDER BY collection, object_id,
                                object_revision DESC, created_at DESC,
                                review_event_id DESC""",
                    (sorted(incoming_owner),),
                )
                existing_review_events = {
                    (str(collection), str(object_id)): (
                        str(reviewer_kind), str(decision), int(object_revision)
                    )
                    for collection, object_id, reviewer_kind, decision,
                    object_revision
                    in cursor.fetchall()
                    if incoming_owner.get(str(object_id)) == str(collection)
                }
            existing = self._existing(conn, keys)
            existing_review_authorities = {
                key: reviewer_kind
                for key, (reviewer_kind, decision, object_revision)
                in existing_review_events.items()
                if str(((existing.get(key) or {}).get("payload") or {}).get(
                    "review_status"
                ) or "") == decision
                and int((existing.get(key) or {}).get("revision") or 0)
                == object_revision
            }
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
            existing_review_authorities=existing_review_authorities,
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
    def _assert_claim_evidence_source_lineage_snapshot(
        cursor: Any,
        active_rows: Mapping[
            tuple[str, str], tuple[str, str, int, str, dict[str, Any]]
        ],
        expected_snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Lock and compare every source row referenced by active endpoints."""

        _validate_source_lineage_identity_snapshot(expected_snapshot)
        expected_records = {
            (str(row["collection"]), str(row["object_id"])): dict(row)
            for row in expected_snapshot["records"]
        }
        fragment_ids: set[str] = set()
        document_ids: set[str] = set()

        def add_many(payload: Mapping[str, Any], plural: str, singular: str, sink: set[str]) -> None:
            raw = payload.get(plural) or []
            if not isinstance(raw, (list, tuple)):
                raise ChangeSetConflict(
                    f"Claim/Evidence source lineage field {plural} is not an array"
                )
            sink.update(str(value) for value in raw if str(value))
            if payload.get(singular):
                sink.add(str(payload[singular]))

        for _key, row in active_rows.items():
            payload = row[4]
            add_many(payload, "source_fragment_ids", "source_fragment_id", fragment_ids)
            add_many(payload, "source_document_ids", "source_document_id", document_ids)
            if payload.get("source_id"):
                document_ids.add(str(payload["source_id"]))

        expected_fragment_ids = {
            object_id
            for (collection, object_id) in expected_records
            if collection == "source_fragments"
        }
        if fragment_ids != expected_fragment_ids:
            raise ChangeSetConflict(
                "Claim/Evidence repair source-fragment denominator differs from freeze"
            )
        fragment_rows: list[Sequence[Any]] = []
        if fragment_ids:
            cursor.execute(
                """SELECT collection, object_id, revision, content_sha256,
                          payload, retired_at
                   FROM wang_knowledge.objects
                   WHERE collection='source_fragments' AND object_id = ANY(%s)
                   ORDER BY object_id
                   FOR UPDATE""",
                (sorted(fragment_ids),),
            )
            fragment_rows = list(cursor.fetchall())
        for row in fragment_rows:
            payload = row[4]
            if not isinstance(payload, Mapping) or record_content_sha(payload) != str(row[3]):
                raise ChangeSetConflict(
                    f"SourceFragment {row[1]} content SHA differs from payload"
                )
            source_id = str(payload.get("source_id") or "")
            if not source_id:
                raise ChangeSetConflict(
                    f"SourceFragment {row[1]} lacks its SourceDocument lineage"
                )
            document_ids.add(source_id)

        expected_document_ids = {
            object_id
            for (collection, object_id) in expected_records
            if collection == "source_documents"
        }
        if document_ids != expected_document_ids:
            raise ChangeSetConflict(
                "Claim/Evidence repair source-document denominator differs from freeze"
            )
        document_rows: list[Sequence[Any]] = []
        if document_ids:
            cursor.execute(
                """SELECT collection, object_id, revision, content_sha256,
                          payload, retired_at
                   FROM wang_knowledge.objects
                   WHERE collection='source_documents' AND object_id = ANY(%s)
                   ORDER BY object_id
                   FOR UPDATE""",
                (sorted(document_ids),),
            )
            document_rows = list(cursor.fetchall())
        for row in document_rows:
            if not isinstance(row[4], Mapping) or record_content_sha(row[4]) != str(row[3]):
                raise ChangeSetConflict(
                    f"SourceDocument {row[1]} content SHA differs from payload"
                )

        actual = build_source_lineage_identity_snapshot(
            [*fragment_rows, *document_rows]
        )
        if actual != expected_snapshot:
            raise ChangeSetConflict(
                "Referenced source-lineage snapshot drifted after repair freeze"
            )
        return actual

    @staticmethod
    def _assert_repair_targets_use_active_source_lineage(
        plan: ChangeSetPlan,
        active_rows: Mapping[
            tuple[str, str], tuple[str, str, int, str, Mapping[str, Any]]
        ],
        source_snapshot: Mapping[str, Any],
    ) -> None:
        """Ignore unrelated legacy lineage, but never repair through retired evidence."""

        touched_evidence_ids = {
            operation.object_id
            for operation in plan.operations
            if operation.collection == "evidence_steps"
        }
        for operation in plan.operations:
            if operation.collection != "claims" or operation.operation != "update":
                continue
            current = active_rows.get(("claims", operation.object_id))
            if current is None:
                continue
            before = {
                str(value) for value in current[4].get("evidence_step_ids") or []
            }
            after = {
                str(value)
                for value in stored_operation_payload(operation).get(
                    "evidence_step_ids"
                )
                or []
            }
            touched_evidence_ids.update(after - before)

        retired = {
            (str(row["collection"]), str(row["object_id"]))
            for row in source_snapshot.get("records") or []
            if bool(row.get("retired"))
        }
        for evidence_id in sorted(touched_evidence_ids):
            evidence = active_rows.get(("evidence_steps", evidence_id))
            if evidence is None:
                continue
            payload = evidence[4]
            fragment_ids = {
                str(value)
                for value in (
                    payload.get("source_fragment_ids")
                    or ([payload["source_fragment_id"]]
                        if payload.get("source_fragment_id")
                        else [])
                )
            }
            source_ids = {
                str(value)
                for value in (
                    payload.get("source_document_ids")
                    or ([payload["source_id"]] if payload.get("source_id") else [])
                )
            }
            retired_refs = sorted(
                {
                    object_id
                    for collection, object_id in retired
                    if (
                        collection == "source_fragments"
                        and object_id in fragment_ids
                    )
                    or (
                        collection == "source_documents" and object_id in source_ids
                    )
                }
            )
            if retired_refs:
                raise ChangeSetConflict(
                    "Claim/Evidence repair target uses retired source lineage: "
                    f"evidence_steps/{evidence_id} -> {','.join(retired_refs)}"
                )

    @staticmethod
    def _assert_claim_evidence_reciprocity_guard(
        cursor: Any,
        plan: ChangeSetPlan,
        guard: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Lock the full denominator and validate the repair's final graph."""

        validated_guard = _validate_claim_evidence_reciprocity_guard(plan, guard)
        expected = validated_guard["expected_active_snapshot"]
        cursor.execute(
            """SELECT collection, object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection = ANY(%s) AND retired_at IS NULL
               ORDER BY collection, object_id
               FOR UPDATE""",
            (list(CLAIM_EVIDENCE_COLLECTIONS),),
        )
        rows = list(cursor.fetchall())
        actual = build_claim_evidence_active_snapshot(rows)

        def record_index(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
            return {
                (str(row["collection"]), str(row["object_id"])): dict(row)
                for row in snapshot["records"]
            }

        expected_records = record_index(expected)
        actual_records = record_index(actual)
        missing = sorted(set(expected_records) - set(actual_records))
        unexpected = sorted(set(actual_records) - set(expected_records))
        if missing or unexpected:
            details: list[str] = []
            if missing:
                details.append(
                    "missing="
                    + ",".join(
                        f"{collection}/{object_id}"
                        for collection, object_id in missing[:10]
                    )
                )
            if unexpected:
                details.append(
                    "unexpected="
                    + ",".join(
                        f"{collection}/{object_id}"
                        for collection, object_id in unexpected[:10]
                    )
                )
            raise ChangeSetConflict(
                "Active Claim/Evidence snapshot ID drift after preview: "
                + " | ".join(details)
            )
        revision_drift = [
            (key, expected_records[key]["revision"], actual_records[key]["revision"])
            for key in sorted(expected_records)
            if expected_records[key]["revision"] != actual_records[key]["revision"]
        ]
        if revision_drift:
            key, expected_revision, actual_revision = revision_drift[0]
            raise ChangeSetConflict(
                "Active Claim/Evidence snapshot revision drift after preview: "
                f"{key[0]}/{key[1]} expected {expected_revision}, "
                f"found {actual_revision}"
            )
        sha_drift = [
            key
            for key in sorted(expected_records)
            if expected_records[key]["content_sha256"]
            != actual_records[key]["content_sha256"]
        ]
        if sha_drift:
            key = sha_drift[0]
            raise ChangeSetConflict(
                "Active Claim/Evidence snapshot content SHA drift after preview: "
                f"{key[0]}/{key[1]}"
            )
        if (
            expected.get("records_sha256") != actual.get("records_sha256")
            or expected.get("pair_state_sha256") != actual.get("pair_state_sha256")
            or expected.get("counts") != actual.get("counts")
            or expected.get("snapshot_sha256") != actual.get("snapshot_sha256")
        ):
            raise ChangeSetConflict(
                "Active Claim/Evidence pair-state drift after preview"
            )

        expected_dependencies = validated_guard[
            "expected_product_dependency_snapshot"
        ]
        cursor.execute(
            """SELECT object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection='product_dependencies' AND retired_at IS NULL
               ORDER BY object_id
               FOR UPDATE"""
        )
        actual_dependencies = build_product_dependency_active_snapshot(
            cursor.fetchall()
        )
        if actual_dependencies != expected_dependencies:
            raise ChangeSetConflict(
                "Active ProductDependency snapshot drift after repair preview"
            )

        active_rows: dict[
            tuple[str, str], tuple[str, str, int, str, dict[str, Any]]
        ] = {}
        for row in rows:
            normalized = _claim_evidence_row(row)
            active_rows[(normalized[0], normalized[1])] = normalized
        PostgresKnowledgeStore._assert_claim_evidence_source_lineage_snapshot(
            cursor,
            active_rows,
            validated_guard["expected_source_lineage_snapshot"],
        )
        PostgresKnowledgeStore._assert_repair_targets_use_active_source_lineage(
            plan,
            active_rows,
            validated_guard["expected_source_lineage_snapshot"],
        )
        if plan.source_kind in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS:
            PostgresKnowledgeStore._assert_claim_evidence_repair_authority(
                cursor,
                plan,
                active_rows,
                pair_adjudication_authorization=validated_guard.get(
                    "expected_pair_adjudication_authorization"
                ),
            )
        planned_keys: set[tuple[str, str]] = set()
        for operation in plan.operations:
            if operation.collection not in CLAIM_EVIDENCE_COLLECTIONS:
                continue
            key = (operation.collection, operation.object_id)
            if key in planned_keys:
                raise ChangeSetConflict(
                    "Claim/Evidence repair plan repeats object "
                    f"{operation.collection}/{operation.object_id}"
                )
            planned_keys.add(key)
            current = active_rows.get(key)
            if operation.operation in {"update", "retire"}:
                if current is None:
                    raise ChangeSetConflict(
                        f"Claim/Evidence repair {operation.operation} target is not active: "
                        f"{operation.collection}/{operation.object_id}"
                    )
                if (
                    operation.before_revision != current[2]
                    or operation.before_sha256 != current[3]
                ):
                    raise ChangeSetConflict(
                        "Claim/Evidence repair operation does not match its locked "
                        f"before state: {operation.collection}/{operation.object_id}"
                    )
            elif operation.operation in {"create", "revive"}:
                if current is not None:
                    raise ChangeSetConflict(
                        f"Claim/Evidence repair {operation.operation} target is already active: "
                        f"{operation.collection}/{operation.object_id}"
                    )
            else:
                raise ChangeSetConflict(
                    "Claim/Evidence repair uses an unsupported operation for "
                    f"{operation.collection}/{operation.object_id}: {operation.operation}"
                )

            if operation.operation == "retire":
                active_rows.pop(key)
                continue
            payload = stored_operation_payload(operation)
            active_rows[key] = (
                operation.collection,
                operation.object_id,
                operation.after_revision,
                operation.after_sha256,
                payload,
            )

        final_snapshot = build_claim_evidence_active_snapshot(active_rows.values())
        counts = final_snapshot["counts"]
        if counts["duplicate_array_references"]:
            raise ChangeSetConflict(
                "Claim/Evidence repair final graph contains duplicate array references: "
                f"{counts['duplicate_array_references']}"
            )
        if counts["dangling_endpoints"]:
            raise ChangeSetConflict(
                "Claim/Evidence repair final graph contains dangling endpoints: "
                f"{counts['dangling_endpoints']}"
            )
        if counts["claim_only_pairs"] or counts["evidence_only_pairs"]:
            raise ChangeSetConflict(
                "Claim/Evidence repair final graph is not reciprocal: "
                f"claim_only={counts['claim_only_pairs']}, "
                f"evidence_only={counts['evidence_only_pairs']}"
            )
        review_snapshot = PostgresKnowledgeStore._review_event_ledger_snapshot(
            cursor, lock_rows=True
        )
        if (
            review_snapshot
            != validated_guard["expected_review_event_ledger_snapshot"]
        ):
            raise ChangeSetConflict(
                "Review-event ledger drifted after Claim/Evidence repair preview"
            )
        return final_snapshot

    @staticmethod
    def _assert_claim_evidence_reciprocity_applied_state(
        cursor: Any,
        plan: ChangeSetPlan,
        guard: Mapping[str, Any],
    ) -> dict[str, Any]:
        """On idempotent retry, prove the exact guarded final state still exists."""

        validated_guard = _validate_claim_evidence_reciprocity_guard(plan, guard)
        expected_records = {
            (str(row["collection"]), str(row["object_id"])): dict(row)
            for row in validated_guard["expected_active_snapshot"]["records"]
        }
        for operation in plan.operations:
            if operation.collection not in CLAIM_EVIDENCE_COLLECTIONS:
                continue
            key = (operation.collection, operation.object_id)
            before = expected_records.get(key)
            if (
                before is None
                or before["revision"] != operation.before_revision
                or before["content_sha256"] != operation.before_sha256
            ):
                raise ChangeSetConflict(
                    "Applied Claim/Evidence repair guard cannot derive its final row: "
                    f"{operation.collection}/{operation.object_id}"
                )
            if operation.operation == "retire":
                expected_records.pop(key)
            else:
                expected_records[key] = {
                    "collection": operation.collection,
                    "object_id": operation.object_id,
                    "revision": operation.after_revision,
                    "content_sha256": operation.after_sha256,
                }

        cursor.execute(
            """SELECT collection, object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection = ANY(%s) AND retired_at IS NULL
               ORDER BY collection, object_id
               FOR UPDATE""",
            (list(CLAIM_EVIDENCE_COLLECTIONS),),
        )
        locked_rows = list(cursor.fetchall())
        actual = build_claim_evidence_active_snapshot(locked_rows)
        if actual["records"] != [
            expected_records[key] for key in sorted(expected_records)
        ]:
            raise ChangeSetConflict(
                "Applied Claim/Evidence repair final snapshot drifted after apply"
            )
        counts = actual["counts"]
        if (
            counts["duplicate_array_references"]
            or counts["dangling_endpoints"]
            or counts["claim_only_pairs"]
            or counts["evidence_only_pairs"]
        ):
            raise ChangeSetConflict(
                "Applied Claim/Evidence repair final graph is no longer clean"
            )
        cursor.execute(
            """SELECT object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection='product_dependencies' AND retired_at IS NULL
               ORDER BY object_id
               FOR UPDATE"""
        )
        dependencies = build_product_dependency_active_snapshot(cursor.fetchall())
        if dependencies != validated_guard["expected_product_dependency_snapshot"]:
            raise ChangeSetConflict(
                "Applied Claim/Evidence repair ProductDependency snapshot drifted"
            )
        active_rows = {
            (normalized[0], normalized[1]): normalized
            for normalized in (_claim_evidence_row(row) for row in locked_rows)
        }
        PostgresKnowledgeStore._assert_claim_evidence_source_lineage_snapshot(
            cursor,
            active_rows,
            validated_guard.get("expected_final_source_lineage_snapshot")
            or validated_guard["expected_source_lineage_snapshot"],
        )
        authority_rows = dict(active_rows)
        for operation in plan.operations:
            if operation.collection not in CLAIM_EVIDENCE_COLLECTIONS:
                continue
            cursor.execute(
                """SELECT payload
                   FROM wang_knowledge.object_versions
                   WHERE collection=%s AND object_id=%s
                     AND revision=%s AND content_sha256=%s""",
                (
                    operation.collection,
                    operation.object_id,
                    operation.before_revision,
                    operation.before_sha256,
                ),
            )
            prior_version = cursor.fetchone()
            if not prior_version or not isinstance(prior_version[0], Mapping):
                raise ChangeSetConflict(
                    "Applied Claim/Evidence repair lacks its exact before ObjectVersion: "
                    f"{operation.collection}/{operation.object_id}"
                )
            before_payload = dict(prior_version[0])
            if record_content_sha(before_payload) != operation.before_sha256:
                raise ChangeSetConflict(
                    "Applied Claim/Evidence repair before ObjectVersion SHA is invalid: "
                    f"{operation.object_id}"
                )
            authority_rows[(operation.collection, operation.object_id)] = (
                operation.collection,
                operation.object_id,
                int(operation.before_revision or 0),
                str(operation.before_sha256 or ""),
                before_payload,
            )
        PostgresKnowledgeStore._assert_repair_targets_use_active_source_lineage(
            plan,
            authority_rows,
            validated_guard["expected_source_lineage_snapshot"],
        )
        PostgresKnowledgeStore._assert_claim_evidence_repair_authority(
            cursor,
            plan,
            authority_rows,
            pair_adjudication_authorization=validated_guard.get(
                "expected_pair_adjudication_authorization"
            ),
        )
        review_snapshot = PostgresKnowledgeStore._review_event_ledger_snapshot(
            cursor, lock_rows=True
        )
        if (
            review_snapshot
            != validated_guard["expected_review_event_ledger_snapshot"]
        ):
            raise ChangeSetConflict(
                "Applied Claim/Evidence repair review-event ledger drifted"
            )
        return actual

    @staticmethod
    def _assert_change_set_write_ledger(
        cursor: Any,
        plan: ChangeSetPlan,
        metadata: Mapping[str, Any],
        summary: Mapping[str, Any],
    ) -> None:
        """Verify the exact KCS, ChangeOperation and ObjectVersion ledger."""

        cursor.execute(
            """SELECT fingerprint_sha256, package_id, source_kind, source_sha256,
                      status, summary, metadata, created_at, applied_at
               FROM wang_knowledge.change_sets
               WHERE change_set_id=%s""",
            (plan.change_set_id,),
        )
        change_set_row = cursor.fetchone()
        timestamps_valid = False
        if change_set_row:
            try:
                created_at = (
                    change_set_row[7]
                    if isinstance(change_set_row[7], datetime)
                    else datetime.fromisoformat(
                        str(change_set_row[7] or "").replace("Z", "+00:00")
                    )
                )
                applied_at = (
                    change_set_row[8]
                    if isinstance(change_set_row[8], datetime)
                    else datetime.fromisoformat(
                        str(change_set_row[8] or "").replace("Z", "+00:00")
                    )
                )
                timestamps_valid = (
                    created_at.tzinfo is not None
                    and applied_at.tzinfo is not None
                    and applied_at >= created_at
                )
            except (TypeError, ValueError):
                timestamps_valid = False
        if not change_set_row or (
            str(change_set_row[0]) != plan.fingerprint_sha256
            or str(change_set_row[1]) != plan.package_id
            or str(change_set_row[2]) != plan.source_kind
            or str(change_set_row[3]) != plan.source_sha256
            or str(change_set_row[4]) != "applied"
            or canonical_json(change_set_row[5] or {}) != canonical_json(summary)
            or canonical_json(change_set_row[6] or {}) != canonical_json(metadata)
            or not timestamps_valid
        ):
            raise ChangeSetConflict(
                "Dedicated ChangeSet ledger write is incomplete"
            )

        cursor.execute(
            """SELECT operation_index, operation, collection, object_id,
                      before_sha256, after_sha256, before_revision, after_revision,
                      details
               FROM wang_knowledge.change_operations
               WHERE change_set_id=%s
               ORDER BY operation_index""",
            (plan.change_set_id,),
        )
        observed_operations = list(cursor.fetchall())
        expected_operations = [
            (
                index,
                operation.operation,
                operation.collection,
                operation.object_id,
                operation.before_sha256,
                operation.after_sha256,
                operation.before_revision,
                operation.after_revision,
                (
                    {"removed_fields": list(operation.removed_fields)}
                    if operation.removed_fields
                    else {}
                ),
            )
            for index, operation in enumerate(plan.operations)
        ]
        normalized_operations = [
            (
                int(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]) if row[4] is not None else None,
                str(row[5]) if row[5] is not None else None,
                int(row[6]) if row[6] is not None else None,
                int(row[7]) if row[7] is not None else None,
                dict(row[8] or {}),
            )
            for row in observed_operations
        ]
        if normalized_operations != expected_operations:
            raise ChangeSetConflict(
                "Dedicated ChangeOperation ledger differs "
                "from its sealed plan"
            )

        cursor.execute(
            """SELECT collection, object_id, revision, content_sha256,
                      payload, change_set_id
               FROM wang_knowledge.object_versions
               WHERE change_set_id=%s
               ORDER BY collection, object_id, revision""",
            (plan.change_set_id,),
        )
        observed_versions = [
            (
                str(row[0]),
                str(row[1]),
                int(row[2]),
                str(row[3]),
                dict(row[4]),
                str(row[5]),
            )
            for row in cursor.fetchall()
        ]
        expected_versions = sorted(
            (
                operation.collection,
                operation.object_id,
                operation.after_revision,
                operation.after_sha256,
                stored_operation_payload(operation),
                plan.change_set_id,
            )
            for operation in plan.operations
        )
        if observed_versions != expected_versions:
            raise ChangeSetConflict(
                "Dedicated ObjectVersion ledger differs "
                "from its sealed plan"
            )

    @staticmethod
    def _assert_claim_evidence_repair_write_ledger(
        cursor: Any,
        plan: ChangeSetPlan,
        metadata: Mapping[str, Any],
        summary: Mapping[str, Any],
        review_events_before: Mapping[str, Any],
    ) -> None:
        """Verify every dedicated repair write before the transaction may commit."""

        PostgresKnowledgeStore._assert_change_set_write_ledger(
            cursor, plan, metadata, summary
        )

        review_events_after = PostgresKnowledgeStore._review_event_ledger_snapshot(
            cursor
        )
        if review_events_after != review_events_before:
            raise ChangeSetConflict(
                "Dedicated Claim/Evidence repair unexpectedly changed review events"
            )

    @staticmethod
    def _review_event_ledger_snapshot(
        cursor: Any, *, lock_rows: bool = False
    ) -> dict[str, Any]:
        cursor.execute(
            """SELECT review_event_id, collection, object_id, object_revision,
                      reviewer_kind, reviewer_id, decision, reason, artifact,
                      created_at
               FROM wang_knowledge.review_events
               ORDER BY review_event_id"""
            + (" FOR SHARE" if lock_rows else "")
        )
        return build_review_event_ledger_snapshot(cursor.fetchall())

    @staticmethod
    def _assert_claim_evidence_repair_authority(
        cursor: Any,
        plan: ChangeSetPlan,
        active_rows: Mapping[
            tuple[str, str], tuple[str, str, int, str, Mapping[str, Any]]
        ],
        *,
        pair_adjudication_authorization: Mapping[str, Any] | None = None,
    ) -> None:
        """Permit only human-approved Claim bindings to fill reverse indexes."""

        if plan.review_events:
            raise ChangeSetConflict(
                "Dedicated Claim/Evidence repair may not create review events"
            )

        if plan.source_kind == CLAIM_EVIDENCE_PAIR_ADJUDICATION_SOURCE_KIND:
            from backend.pipeline.claim_evidence_pair_adjudication import (
                validate_pair_repair_authorization,
            )

            try:
                validate_pair_repair_authorization(
                    pair_adjudication_authorization or {},
                    plan=plan,
                    active_rows=active_rows,
                )
            except ValueError as exc:
                raise ChangeSetConflict(
                    f"Pair-adjudication repair authority failed: {exc}"
                ) from exc

            target_claim_ids = sorted(
                operation.object_id
                for operation in plan.operations
                if operation.collection == "claims"
            )
            target_evidence_ids = sorted(
                operation.object_id
                for operation in plan.operations
                if operation.collection == "evidence_steps"
            )
            cursor.execute(
                """SELECT re.collection, re.object_id, re.object_revision,
                          re.reviewer_kind, re.decision
                   FROM wang_knowledge.review_events re
                   WHERE (re.collection='claims' AND re.object_id = ANY(%s))
                      OR (re.collection='evidence_steps' AND re.object_id = ANY(%s))
                   ORDER BY re.collection, re.object_id, re.object_revision,
                            re.review_event_id""",
                (target_claim_ids, target_evidence_ids),
            )
            for collection, object_id, revision, reviewer_kind, decision in cursor.fetchall():
                current = active_rows.get((str(collection), str(object_id)))
                if (
                    current is not None
                    and int(revision) == current[2]
                    and str(reviewer_kind) == "human"
                    and str(decision)
                    == str(current[4].get("review_status") or "candidate")
                ):
                    raise ChangeSetConflict(
                        "Pair-adjudication repair cannot alter a current human-settled "
                        f"object: {collection}/{object_id}"
                    )
            return

        if pair_adjudication_authorization is not None:
            raise ChangeSetConflict(
                "Pair-adjudication authority cannot govern a projection-only repair"
            )

        additions_by_evidence: dict[str, tuple[str, ...]] = {}
        added_claim_ids: set[str] = set()
        for operation in plan.operations:
            if operation.collection != "evidence_steps" or operation.operation != "update":
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair permits only active "
                    "EvidenceStep updates; rejected "
                    f"{operation.collection}/{operation.object_id} "
                    f"{operation.operation}"
                )
            key = (operation.collection, operation.object_id)
            current = active_rows.get(key)
            if current is None:
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair target is not an active "
                    f"EvidenceStep: {operation.object_id}"
                )
            if (
                operation.before_revision != current[2]
                or operation.before_sha256 != current[3]
                or operation.after_revision != current[2] + 1
            ):
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair operation does not match its "
                    "locked adjacent revision: "
                    f"evidence_steps/{operation.object_id}"
                )
            current_payload = dict(current[4])
            planned_payload = stored_operation_payload(operation)
            protected_before = {
                key: value
                for key, value in current_payload.items()
                if key not in {"produced_claim_ids", "revision"}
            }
            protected_after = {
                key: value
                for key, value in planned_payload.items()
                if key not in {"produced_claim_ids", "revision"}
            }
            if canonical_json(protected_before) != canonical_json(protected_after):
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair changed EvidenceStep content "
                    f"outside produced_claim_ids: {operation.object_id}"
                )
            before = current_payload.get("produced_claim_ids") or []
            after = planned_payload.get("produced_claim_ids") or []
            if not isinstance(before, list) or not isinstance(after, list):
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair requires produced_claim_ids arrays: "
                    f"{operation.object_id}"
                )
            before_ids = [str(value) for value in before]
            after_ids = [str(value) for value in after]
            if (
                any(not value for value in before_ids + after_ids)
                or len(before_ids) != len(set(before_ids))
                or len(after_ids) != len(set(after_ids))
            ):
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair found empty or duplicate "
                    f"produced_claim_ids: {operation.object_id}"
                )
            if after_ids[: len(before_ids)] != before_ids:
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair may not remove or reorder "
                    f"produced_claim_ids: {operation.object_id}"
                )
            additions = tuple(after_ids[len(before_ids) :])
            if not additions:
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair update must append a proven "
                    f"Claim binding: {operation.object_id}"
                )
            if operation.object_id in additions_by_evidence:
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair repeats EvidenceStep operation: "
                    f"{operation.object_id}"
                )
            additions_by_evidence[operation.object_id] = additions
            added_claim_ids.update(additions)

        if not additions_by_evidence:
            return

        evidence_ids = sorted(additions_by_evidence)
        cursor.execute(
            """SELECT review_event_id, collection, object_id, object_revision,
                      reviewer_kind, decision, artifact
               FROM wang_knowledge.review_events
               WHERE (collection='claims' AND object_id = ANY(%s))
                  OR (collection='evidence_steps' AND object_id = ANY(%s))
               ORDER BY collection, object_id, object_revision, review_event_id""",
            (sorted(added_claim_ids), evidence_ids),
        )
        events: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        for (
            event_id,
            collection,
            object_id,
            revision,
            reviewer_kind,
            decision,
            artifact,
        ) in cursor.fetchall():
            events.setdefault(
                (str(collection), str(object_id), int(revision)), []
            ).append(
                {
                    "review_event_id": str(event_id),
                    "reviewer_kind": str(reviewer_kind),
                    "decision": str(decision),
                    "artifact": dict(artifact or {}),
                }
            )

        for evidence_id in evidence_ids:
            current = active_rows[("evidence_steps", evidence_id)]
            status = str(current[4].get("review_status") or "candidate")
            matching_human = [
                event
                for event in events.get(("evidence_steps", evidence_id, current[2]), [])
                if event["reviewer_kind"] == "human" and event["decision"] == status
            ]
            if matching_human:
                raise ChangeSetConflict(
                    "Dedicated Claim/Evidence repair cannot change a current "
                    f"human-settled EvidenceStep: {evidence_id}"
                )

        cursor.execute(
            """SELECT ov.object_id, ov.revision, ov.content_sha256,
                      ov.change_set_id, co.operation, co.after_revision,
                      co.after_sha256, cs.status, cs.source_kind
               FROM wang_knowledge.object_versions ov
               JOIN wang_knowledge.objects current
                 ON current.collection=ov.collection
                AND current.object_id=ov.object_id
                AND current.revision=ov.revision
                AND current.content_sha256=ov.content_sha256
                AND current.retired_at IS NULL
               LEFT JOIN wang_knowledge.change_operations co
                 ON co.change_set_id=ov.change_set_id
                AND co.collection=ov.collection
                AND co.object_id=ov.object_id
                AND co.after_revision=ov.revision
                AND co.after_sha256=ov.content_sha256
               JOIN wang_knowledge.change_sets cs
                 ON cs.change_set_id=ov.change_set_id
               WHERE ov.collection='claims' AND ov.object_id = ANY(%s)
               ORDER BY ov.object_id, co.operation""",
            (sorted(added_claim_ids),),
        )
        producers: dict[str, list[dict[str, Any]]] = {}
        for (
            object_id,
            revision,
            content_sha256,
            change_set_id,
            operation,
            after_revision,
            after_sha256,
            change_set_status,
            source_kind,
        ) in cursor.fetchall():
            producers.setdefault(str(object_id), []).append(
                {
                    "revision": int(revision),
                    "content_sha256": str(content_sha256),
                    "change_set_id": str(change_set_id),
                    "operation": str(operation or ""),
                    "after_revision": (
                        int(after_revision) if after_revision is not None else None
                    ),
                    "after_sha256": str(after_sha256 or ""),
                    "change_set_status": str(change_set_status or ""),
                    "source_kind": str(source_kind or ""),
                }
            )

        for evidence_id, claim_ids in additions_by_evidence.items():
            for claim_id in claim_ids:
                claim = active_rows.get(("claims", claim_id))
                if claim is None:
                    raise ChangeSetConflict(
                        "Dedicated Claim/Evidence repair references a non-current "
                        f"Claim: {claim_id}"
                    )
                claim_payload = claim[4]
                if evidence_id not in {
                    str(value) for value in claim_payload.get("evidence_step_ids") or []
                }:
                    raise ChangeSetConflict(
                        "Dedicated Claim/Evidence repair cannot invent a binding absent "
                        f"from the locked Claim: {claim_id}/{evidence_id}"
                    )
                status = str(claim_payload.get("review_status") or "candidate")
                current_events = events.get(("claims", claim_id, claim[2]), [])
                matching_events = [
                    event
                    for event in current_events
                    if event["reviewer_kind"] == "human"
                    and event["decision"] == status
                ]
                producer_rows = producers.get(claim_id, [])
                if (
                    status not in {"approved", "human_approved"}
                    or len(current_events) != 1
                    or len(matching_events) != 1
                    or len(producer_rows) != 1
                ):
                    raise ChangeSetConflict(
                        "Dedicated Claim/Evidence repair lacks one current human "
                        f"Claim authority: {claim_id}"
                    )
                producer = producer_rows[0]
                if (
                    producer["revision"] != claim[2]
                    or producer["content_sha256"] != claim[3]
                    or producer["operation"] not in {"create", "update", "revive"}
                    or producer["after_revision"] != claim[2]
                    or producer["after_sha256"] != claim[3]
                    or producer["change_set_status"] != "applied"
                    or producer["source_kind"] != "review_decision"
                    or str(matching_events[0]["artifact"].get("change_set_id") or "")
                    != producer["change_set_id"]
                ):
                    raise ChangeSetConflict(
                        "Dedicated Claim/Evidence repair human event is not bound to "
                        f"the current ObjectVersion producer: {claim_id}"
                    )

    @staticmethod
    def _source_queue_final_heads(
        plan: ChangeSetPlan, expected: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        heads = {
            (str(row["collection"]), str(row["object_id"])): dict(row)
            for row in expected["scanned_heads"]
        }
        for operation in plan.operations:
            if operation.collection not in CLAIM_EVIDENCE_COLLECTIONS:
                continue
            key = (operation.collection, operation.object_id)
            if operation.operation == "create":
                if key in heads:
                    raise ChangeSetConflict(
                        f"Source-queue plan creates existing head {key[0]}/{key[1]}"
                    )
            elif key not in heads:
                raise ChangeSetConflict(
                    f"Source-queue plan changes absent head {key[0]}/{key[1]}"
                )
            heads[key] = {
                "collection": operation.collection,
                "object_id": operation.object_id,
                "revision": operation.after_revision,
                "content_sha256": operation.after_sha256,
                "retired": operation.operation == "retire",
                "producer_change_set_id": plan.change_set_id,
            }
        return [heads[key] for key in sorted(heads)]

    @staticmethod
    def _assert_claim_evidence_source_queue_plan_authority(
        plan: ChangeSetPlan, expected: Mapping[str, Any]
    ) -> None:
        protected = {
            (str(row["collection"]), str(row["object_id"]))
            for row in expected["protected_records"]
        }
        for operation in plan.operations:
            key = (operation.collection, operation.object_id)
            if key in protected:
                raise ChangeSetConflict(
                    "Claim/Evidence source work cannot mutate human-settled "
                    f"{operation.collection}/{operation.object_id}"
                )
            if operation.collection in CLAIM_EVIDENCE_COLLECTIONS:
                status = str(operation.payload.get("review_status") or "candidate")
                if status in {"approved", "human_approved"}:
                    raise ChangeSetConflict(
                        "Claim/Evidence source work cannot create an unproven human "
                        f"status on {operation.collection}/{operation.object_id}"
                    )
        if any(event.reviewer_kind != "ai" for event in plan.review_events):
            raise ChangeSetConflict(
                "Claim/Evidence source work permits only sealed planned AI review events"
            )

    @staticmethod
    def _assert_claim_evidence_source_queue_review_ledger(
        cursor: Any,
        plan: ChangeSetPlan,
        expected_before: Mapping[str, Any],
    ) -> dict[str, Any]:
        cursor.execute(
            """SELECT review_event_id, collection, object_id, object_revision,
                      reviewer_kind, reviewer_id, decision, reason, artifact,
                      created_at
               FROM wang_knowledge.review_events
               ORDER BY review_event_id FOR SHARE"""
        )
        actual_rows = _canonical_review_event_rows(list(cursor.fetchall()))
        planned = {
            event.review_event_id: event for event in plan.review_events
        }
        actual_by_id = {row["review_event_id"]: row for row in actual_rows}
        if not set(planned).issubset(actual_by_id):
            raise ChangeSetConflict(
                "Claim/Evidence source work did not persist every planned review event"
            )
        for event_id, event in planned.items():
            observed = actual_by_id[event_id]
            expected_fields = {
                "review_event_id": event.review_event_id,
                "collection": event.collection,
                "object_id": event.object_id,
                "object_revision": event.object_revision,
                "reviewer_kind": event.reviewer_kind,
                "reviewer_id": event.reviewer_id,
                "decision": event.decision,
                "reason": event.reason,
                "artifact": event.artifact,
            }
            if any(
                canonical_json(observed[field]) != canonical_json(value)
                for field, value in expected_fields.items()
            ):
                raise ChangeSetConflict(
                    f"Planned review event {event_id} differs from its sealed plan"
                )
        preexisting = [
            row for row in actual_rows if row["review_event_id"] not in planned
        ]
        if build_review_event_ledger_snapshot(preexisting) != expected_before:
            raise ChangeSetConflict(
                "Claim/Evidence source work review-event baseline drifted"
            )
        if len(actual_rows) != int(expected_before["count"]) + len(planned):
            raise ChangeSetConflict(
                "Claim/Evidence source work review-event accounting differs"
            )
        return build_review_event_ledger_snapshot(actual_rows)

    def _assert_claim_evidence_source_queue_pre_state(
        self,
        cursor: Any,
        plan: ChangeSetPlan,
        context: _ClaimEvidenceSourceQueueApplyContext,
    ) -> None:
        actual_sources = self._claim_evidence_source_generations_from_cursor(
            cursor, context.expected_source_generations, lock_rows=True
        )
        if actual_sources != list(context.expected_source_generations):
            raise ChangeSetConflict(
                "Claim/Evidence source work SourceDocument generation drifted"
            )
        actual_human = self._claim_evidence_human_authority_from_cursor(
            cursor, lock_rows=True
        )
        if actual_human != context.expected_human_authority_snapshot:
            raise ChangeSetConflict(
                "Claim/Evidence source work human-authority snapshot drifted"
            )
        self._assert_claim_evidence_source_queue_plan_authority(
            plan, context.expected_human_authority_snapshot
        )

    def _assert_claim_evidence_source_queue_post_state(
        self,
        cursor: Any,
        plan: ChangeSetPlan,
        context: _ClaimEvidenceSourceQueueApplyContext,
    ) -> None:
        actual_sources = self._claim_evidence_source_generations_from_cursor(
            cursor, context.expected_source_generations, lock_rows=True
        )
        if actual_sources != list(context.expected_source_generations):
            raise ChangeSetConflict(
                "Claim/Evidence source work changed its frozen SourceDocument generation"
            )
        expected = context.expected_human_authority_snapshot
        actual = self._claim_evidence_human_authority_from_cursor(
            cursor, lock_rows=True
        )
        if actual["scanned_heads"] != self._source_queue_final_heads(plan, expected):
            raise ChangeSetConflict(
                "Claim/Evidence source work current-head readback differs from its plan"
            )
        if actual["protected_records"] != expected["protected_records"]:
            raise ChangeSetConflict(
                "Claim/Evidence source work changed human-settled authority"
            )
        expected_ledger_after = self._assert_claim_evidence_source_queue_review_ledger(
            cursor,
            plan,
            expected["review_event_ledger_snapshot"],
        )
        if actual["review_event_ledger_snapshot"] != expected_ledger_after:
            raise ChangeSetConflict(
                "Claim/Evidence source work review-event ledger readback drifted"
            )

    @staticmethod
    def _assert_change_set_object_readback(
        cursor: Any, plan: ChangeSetPlan
    ) -> None:
        for operation in plan.operations:
            cursor.execute(
                """SELECT revision, content_sha256, payload, retired_at
                   FROM wang_knowledge.objects
                   WHERE collection=%s AND object_id=%s""",
                (operation.collection, operation.object_id),
            )
            row = cursor.fetchone()
            if not row or not isinstance(row[2], Mapping):
                raise ChangeSetConflict(
                    f"Dedicated ChangeSet object readback is missing: "
                    f"{operation.collection}/{operation.object_id}"
                )
            expected_retired = operation.operation == "retire"
            expected_payload = stored_operation_payload(operation)
            payload_matches = (
                canonical_json(row[2]) == canonical_json(expected_payload)
                if operation.operation in {"create", "update"}
                else canonical_json(
                    {
                        key: value
                        for key, value in row[2].items()
                        if key != "revision"
                    }
                )
                == canonical_json(
                    {
                        key: value
                        for key, value in expected_payload.items()
                        if key != "revision"
                    }
                )
            )
            if (
                int(row[0]) != operation.after_revision
                or str(row[1]) != operation.after_sha256
                or record_content_sha(row[2]) != operation.after_sha256
                or (row[3] is not None) != expected_retired
                or not payload_matches
            ):
                raise ChangeSetConflict(
                    f"Dedicated ChangeSet object readback differs: "
                    f"{operation.collection}/{operation.object_id}"
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
        expected_current_source_records: Optional[Mapping[str, tuple[int, str]]] = None,
        expected_current_claim_related_records: Optional[
            Mapping[str, Sequence[tuple[str, str, int, str]]]
        ] = None,
        expected_claim_evidence_guard: Optional[Mapping[str, Any]] = None,
        _claim_evidence_source_queue_context: Optional[
            _ClaimEvidenceSourceQueueApplyContext
        ] = None,
    ) -> dict[str, Any]:
        source_queue_context = _claim_evidence_source_queue_context
        if plan.source_kind in CLAIM_EVIDENCE_SOURCE_QUEUE_SOURCE_KINDS:
            if (
                source_queue_context is None
                or source_queue_context.token
                is not _CLAIM_EVIDENCE_SOURCE_QUEUE_APPLY_TOKEN
            ):
                raise PostgresKnowledgeStoreError(
                    "Dedicated Claim/Evidence source work must use "
                    "apply_claim_evidence_source_queue_plan"
                )
        elif source_queue_context is not None:
            raise PostgresKnowledgeStoreError(
                "Claim/Evidence source-queue guard cannot authorize another source kind"
            )
        claim_evidence_guard = _resolve_claim_evidence_reciprocity_guard(
            plan, metadata, expected_claim_evidence_guard
        )
        validate_change_set_plan_integrity(plan)
        if plan.source_kind in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS:
            assert claim_evidence_guard is not None
            _validate_claim_evidence_repair_apply_metadata(
                plan, metadata, claim_evidence_guard
            )
        if (
            not plan.operations
            and plan.source_kind not in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS
        ):
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
                    (POSTGRES_APPLY_ADVISORY_LOCK_KEY,),
                )
                for source_id, (expected_revision, expected_sha) in sorted(
                    (expected_current_source_records or {}).items()
                ):
                    cursor.execute(
                        """SELECT revision, content_sha256
                           FROM wang_knowledge.objects
                           WHERE collection='source_documents' AND object_id=%s
                             AND retired_at IS NULL FOR SHARE""",
                        (source_id,),
                    )
                    source_row = cursor.fetchone()
                    if source_row != (expected_revision, expected_sha):
                        raise ChangeSetConflict(
                            f"Source generation changed before apply: {source_id}"
                        )
                if expected_current_claim_related_records is not None:
                    if plan.source_kind not in {
                        "legacy_adjudicated_withdrawn_review_reconciliation_v1",
                        "legacy_adjudicated_auto_applied_review_reconciliation_v1",
                        "legacy_relation_id_only_review_reconciliation_v1",
                    }:
                        raise PostgresKnowledgeStoreError(
                            "Claim-related graph guard is restricted to adjudicated legacy review reconciliation"
                        )
                    claim_ids = sorted(expected_current_claim_related_records)
                    if not claim_ids or set(claim_ids) != {
                        operation.object_id for operation in plan.operations
                    }:
                        raise PostgresKnowledgeStoreError(
                            "Claim-related graph guard must cover every planned Claim"
                        )
                    expected_keys = {
                        claim_id: {
                            (str(item[0]), str(item[1]))
                            for item in expected_current_claim_related_records[claim_id]
                        }
                        for claim_id in claim_ids
                    }
                    related_evidence_ids = {
                        claim_id: {
                            object_id for collection, object_id in expected_keys[claim_id]
                            if collection == "evidence_steps"
                        }
                        for claim_id in claim_ids
                    }
                    all_related_ids = sorted({
                        object_id for keys in expected_keys.values()
                        for _, object_id in keys
                    })
                    lookup_values = claim_ids + sorted({
                        evidence_id for ids in related_evidence_ids.values()
                        for evidence_id in ids
                    })
                    cursor.execute(
                        """SELECT collection, object_id, revision, content_sha256, payload
                           FROM wang_knowledge.objects
                           WHERE retired_at IS NULL AND collection <> 'claims'
                             AND (payload::text LIKE ANY(%s) OR object_id = ANY(%s))""",
                        ([f"%{value}%" for value in lookup_values], all_related_ids),
                    )
                    observed: dict[str, list[tuple[str, str, int, str]]] = {
                        claim_id: [] for claim_id in claim_ids
                    }
                    for collection, object_id, revision, content_sha, payload in cursor.fetchall():
                        for claim_id in claim_ids:
                            if (
                                _contains_exact_value(payload, claim_id)
                                or (str(collection), str(object_id)) in expected_keys[claim_id]
                                or (
                                    collection == "knowledge_relations"
                                    and any(
                                        _contains_exact_value(payload, evidence_id)
                                        for evidence_id in related_evidence_ids[claim_id]
                                    )
                                )
                            ):
                                observed[claim_id].append(
                                    (str(collection), str(object_id), int(revision), str(content_sha))
                                )
                    for claim_id in claim_ids:
                        expected = sorted(
                            tuple(item) for item in expected_current_claim_related_records[claim_id]
                        )
                        if sorted(observed[claim_id]) != expected:
                            raise ChangeSetConflict(
                                f"Claim-related graph changed before apply: {claim_id}"
                            )
                elif plan.source_kind in {
                    "legacy_adjudicated_withdrawn_review_reconciliation_v1",
                    "legacy_adjudicated_auto_applied_review_reconciliation_v1",
                    "legacy_relation_id_only_review_reconciliation_v1",
                }:
                    raise PostgresKnowledgeStoreError(
                        "Adjudicated legacy review reconciliation requires a graph guard"
                    )
                cursor.execute(
                    """SELECT change_set_id, status, summary, metadata
                       FROM wang_knowledge.change_sets
                       WHERE fingerprint_sha256=%s""",
                    (plan.fingerprint_sha256,),
                )
                prior = cursor.fetchone()
                if prior and prior[1] == "applied" and plan.operations:
                    if source_queue_context is not None:
                        if (
                            str(prior[0]) != plan.change_set_id
                            or canonical_json(prior[3] or {})
                            != canonical_json(metadata or {})
                        ):
                            raise ChangeSetConflict(
                                "Applied Claim/Evidence source work metadata differs "
                                "from this retry"
                            )
                        expected_retry_summary = plan.as_dict()["summary"]
                        expected_retry_summary["invalidated_dependencies"] = 0
                        self._assert_change_set_write_ledger(
                            cursor,
                            plan,
                            metadata or {},
                            expected_retry_summary,
                        )
                        self._assert_change_set_object_readback(cursor, plan)
                        self._assert_claim_evidence_source_queue_post_state(
                            cursor, plan, source_queue_context
                        )
                    if (
                        plan.source_kind in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS
                        and claim_evidence_guard is not None
                    ):
                        if (
                            str(prior[0]) != plan.change_set_id
                            or canonical_json(prior[3] or {})
                            != canonical_json(metadata or {})
                        ):
                            raise ChangeSetConflict(
                                "Applied Claim/Evidence repair metadata or backup "
                                "differs from this retry"
                            )
                        retry_review_events = self._review_event_ledger_snapshot(
                            cursor
                        )
                        self._assert_claim_evidence_reciprocity_applied_state(
                            cursor, plan, claim_evidence_guard
                        )
                        expected_retry_summary = plan.as_dict()["summary"]
                        expected_retry_summary["invalidated_dependencies"] = 0
                        self._assert_claim_evidence_repair_write_ledger(
                            cursor,
                            plan,
                            metadata or {},
                            expected_retry_summary,
                            retry_review_events,
                        )
                    return {
                        "status": "already_applied",
                        "change_set_id": plan.change_set_id,
                        "summary": prior[2],
                    }

                guarded_final_snapshot: Optional[dict[str, Any]] = None
                if source_queue_context is not None:
                    self._assert_claim_evidence_source_queue_pre_state(
                        cursor, plan, source_queue_context
                    )
                if claim_evidence_guard is not None:
                    guarded_final_snapshot = self._assert_claim_evidence_reciprocity_guard(
                        cursor, plan, claim_evidence_guard
                    )
                if not plan.operations:
                    return {
                        "status": "unchanged",
                        "change_set_id": None,
                        "summary": plan.as_dict()["summary"],
                    }
                repair_review_events_before: Optional[dict[str, Any]] = None
                if plan.source_kind in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS:
                    repair_review_events_before = self._review_event_ledger_snapshot(
                        cursor
                    )

                self._assert_obsolete_candidate_retirement(
                    cursor,
                    plan,
                    (metadata or {}).get("obsolete_candidate_batch_retirement"),
                )
                self._assert_stale_pending_topic_identity_retirement(
                    cursor,
                    plan,
                    (metadata or {}).get(
                        "stale_pending_topic_identity_retirement"
                    ),
                )
                self._assert_stale_candidate_projection_retirement(
                    cursor,
                    plan,
                    (metadata or {}).get(
                        "stale_candidate_projection_retirement"
                    ),
                    (metadata or {}).get(
                        "obsolete_candidate_batch_retirement"
                    ),
                )
                self._assert_stale_ai_cross_sermon_constraint_retirement(
                    cursor,
                    plan,
                    (metadata or {}).get(
                        "stale_ai_cross_sermon_constraint_retirement"
                    ),
                )
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

                for review_event in plan.review_events:
                    cursor.execute(
                        """INSERT INTO wang_knowledge.review_events
                           (review_event_id, collection, object_id,
                            object_revision, reviewer_kind, reviewer_id,
                            decision, reason, artifact)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                        (
                            review_event.review_event_id,
                            review_event.collection,
                            review_event.object_id,
                            review_event.object_revision,
                            review_event.reviewer_kind,
                            review_event.reviewer_id,
                            review_event.decision,
                            review_event.reason,
                            canonical_json(review_event.artifact),
                        ),
                    )

                invalidated = self._invalidate_dependencies(cursor, plan, changed_records, len(plan.operations))
                if (
                    plan.source_kind
                    in (
                        CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS
                        | CLAIM_EVIDENCE_SOURCE_QUEUE_SOURCE_KINDS
                    )
                    and invalidated
                ):
                    raise ChangeSetConflict(
                        "Dedicated Claim/Evidence work encountered an uncoordinated "
                        f"ProductDependency invalidation: {invalidated}"
                    )
                summary["invalidated_dependencies"] = invalidated
                if guarded_final_snapshot is not None:
                    cursor.execute(
                        """SELECT collection, object_id, revision, content_sha256, payload
                           FROM wang_knowledge.objects
                           WHERE collection = ANY(%s) AND retired_at IS NULL
                           ORDER BY collection, object_id""",
                        (list(CLAIM_EVIDENCE_COLLECTIONS),),
                    )
                    written_rows = list(cursor.fetchall())
                    written_snapshot = build_claim_evidence_active_snapshot(
                        written_rows
                    )
                    if written_snapshot != guarded_final_snapshot:
                        raise ChangeSetConflict(
                            "Claim/Evidence repair database state differs from its "
                            "locked final simulation"
                        )
                    written_active_rows = {
                        (normalized[0], normalized[1]): normalized
                        for normalized in (
                            _claim_evidence_row(row) for row in written_rows
                        )
                    }
                    assert claim_evidence_guard is not None
                    PostgresKnowledgeStore._assert_claim_evidence_source_lineage_snapshot(
                        cursor,
                        written_active_rows,
                        claim_evidence_guard.get(
                            "expected_final_source_lineage_snapshot"
                        )
                        or claim_evidence_guard["expected_source_lineage_snapshot"],
                    )
                cursor.execute(
                    """UPDATE wang_knowledge.change_sets
                       SET status='applied', summary=%s::jsonb, applied_at=now()
                       WHERE change_set_id=%s""",
                    (canonical_json(summary), plan.change_set_id),
                )
                if plan.source_kind in CLAIM_EVIDENCE_REPAIR_SOURCE_KINDS:
                    assert repair_review_events_before is not None
                    self._assert_claim_evidence_repair_write_ledger(
                        cursor,
                        plan,
                        metadata or {},
                        summary,
                        repair_review_events_before,
                    )
                if source_queue_context is not None:
                    self._assert_change_set_write_ledger(
                        cursor, plan, metadata or {}, summary
                    )
                    self._assert_change_set_object_readback(cursor, plan)
                    self._assert_claim_evidence_source_queue_post_state(
                        cursor, plan, source_queue_context
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

        if not any(
            (
                operation.collection in EXTRACTION_RECORD_COLLECTIONS
                or operation.collection == "source_documents"
            )
            and operation.operation == "retire"
            for operation in plan.operations
        ):
            return
        cursor.execute(
            """SELECT collection, object_id, payload
               FROM wang_knowledge.objects
               WHERE collection = ANY(%s) AND retired_at IS NULL
               FOR UPDATE""",
            (sorted(SEMANTIC_REFERENCE_COLLECTIONS),),
        )
        blockers = uncoordinated_semantic_reference_blockers(
            plan, cursor.fetchall()
        )
        if blockers:
            raise ChangeSetConflict(
                "re-extraction requires a coordinated CVR update; current semantic "
                "master data still references the predecessor: "
                + " | ".join(sorted(blockers)[:20])
            )

    @staticmethod
    def _assert_obsolete_candidate_retirement(
        cursor: Any,
        plan: ChangeSetPlan,
        audit: Mapping[str, Any] | None,
    ) -> None:
        """Recheck an opt-in legacy candidate cleanup under the apply lock."""

        if audit is None:
            return
        if (
            audit.get("schema_version")
            != "wang_obsolete_candidate_batch_retirement_v1"
            or audit.get("reason_code")
            != "composition_plan_candidate_retired_by_draft_first"
        ):
            raise ChangeSetConflict(
                "obsolete candidate retirement audit is missing its governed identity"
            )
        canonical_audit = dict(audit)
        stored_scope_sha256 = str(canonical_audit.pop("scope_sha256", ""))
        if stored_scope_sha256 != sha256_json(canonical_audit):
            raise ChangeSetConflict(
                "obsolete candidate retirement audit scope SHA does not match"
            )
        batch_id = str(audit.get("batch_id") or "")
        if not batch_id.startswith("RB-") or len(batch_id) <= 3:
            raise ChangeSetConflict(
                "obsolete candidate retirement audit has an invalid batch id"
            )
        batch_key = re.escape(batch_id[3:])
        plan_pattern = re.compile(rf"^CP-{batch_key}-[ST]-[0-9a-f]{{12}}$")
        synthesis_pattern = re.compile(
            rf"^SYN-{batch_key}-[ST]-[0-9a-f]{{12}}$"
        )
        known_plan_ids = {
            str(object_id)
            for object_id in audit.get("known_plan_ids") or []
            if plan_pattern.fullmatch(str(object_id))
        }

        def belongs_to_obsolete_batch(
            collection: str, object_id: str, payload: Mapping[str, Any]
        ) -> bool:
            if collection == "composition_plans":
                return bool(plan_pattern.fullmatch(object_id))
            if collection == "composition_decisions":
                plan_id = str(payload.get("plan_id") or "")
                return plan_id in known_plan_ids or bool(plan_pattern.fullmatch(plan_id))
            if collection == "knowledge_routes":
                plan_id = str(payload.get("target_id") or "")
                return plan_id in known_plan_ids or bool(plan_pattern.fullmatch(plan_id))
            if collection == "editorial_syntheses":
                return bool(synthesis_pattern.fullmatch(object_id))
            return False

        status = audit.get("status")
        if status == "already_retired":
            if audit.get("records") not in ([], None) or any(
                operation.operation == "retire"
                and operation.collection
                in OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS
                for operation in plan.operations
            ):
                raise ChangeSetConflict(
                    "already-retired candidate audit conflicts with planned retires"
                )
            cursor.execute(
                """SELECT collection, object_id, payload
                   FROM wang_knowledge.objects
                   WHERE retired_at IS NULL FOR UPDATE"""
            )
            resurrected = sorted(
                f"{collection}/{object_id}"
                for collection, object_id, payload in cursor.fetchall()
                if belongs_to_obsolete_batch(
                    str(collection), str(object_id), payload
                )
            )
            if resurrected:
                raise ChangeSetConflict(
                    "already-retired candidate batch has live rows again: "
                    + ", ".join(resurrected[:20])
                )
            return
        if status != "planned":
            raise ChangeSetConflict(
                "obsolete candidate retirement audit has an invalid status"
            )
        records = audit.get("records")
        if not isinstance(records, list) or not records:
            raise ChangeSetConflict(
                "obsolete candidate retirement audit has no records"
            )
        audited: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in records:
            if not isinstance(row, Mapping):
                raise ChangeSetConflict(
                    "obsolete candidate retirement audit contains a malformed record"
                )
            key = (
                str(row.get("collection") or ""),
                str(row.get("object_id") or ""),
            )
            if (
                key in audited
                or key[0] not in OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS
                or not key[1]
            ):
                raise ChangeSetConflict(
                    "obsolete candidate retirement audit has a duplicate or invalid key"
                )
            audited[key] = row

        planned = {
            (operation.collection, operation.object_id): operation
            for operation in plan.operations
        }
        for key, row in audited.items():
            operation = planned.get(key)
            if operation is None or operation.operation != "retire":
                raise ChangeSetConflict(
                    f"obsolete candidate retirement omits planned retire {key[0]}/{key[1]}"
                )
            if (
                operation.before_revision != row.get("expected_revision")
                or operation.before_sha256 != row.get("expected_content_sha256")
            ):
                raise ChangeSetConflict(
                    f"obsolete candidate retirement snapshot drifted for {key[0]}/{key[1]}"
                )
        unexpected = sorted(
            key
            for key, operation in planned.items()
            if operation.operation == "retire"
            and key[0] in OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS
            and key not in audited
        )
        if unexpected:
            raise ChangeSetConflict(
                "obsolete candidate retirement audit omits candidate-workflow retires: "
                + ", ".join(f"{collection}/{object_id}" for collection, object_id in unexpected)
            )

        def strings(value: Any) -> set[str]:
            if isinstance(value, Mapping):
                found = {str(key) for key in value if isinstance(key, str)}
                for child in value.values():
                    found.update(strings(child))
                return found
            if isinstance(value, (list, tuple, set)):
                found: set[str] = set()
                for child in value:
                    found.update(strings(child))
                return found
            return {value} if isinstance(value, str) else set()

        cursor.execute(
            """SELECT collection, object_id, payload
               FROM wang_knowledge.objects
               WHERE retired_at IS NULL FOR UPDATE"""
        )
        audited_ids = {object_id for _collection, object_id in audited}
        blockers: list[str] = []
        seen_current: set[tuple[str, str]] = set()
        seen_rows: set[tuple[str, str]] = set()
        for collection, object_id, payload in cursor.fetchall():
            key = (str(collection), str(object_id))
            seen_rows.add(key)
            operation = planned.get(key)
            if key in audited:
                seen_current.add(key)
                if (
                    not belongs_to_obsolete_batch(key[0], key[1], payload)
                    or str(payload.get("review_status") or "") != "candidate"
                    or str(payload.get("visibility") or "") != "internal"
                ):
                    blockers.append(f"{key[0]}/{key[1]} is no longer pending candidate data")
                continue
            if operation is not None:
                if operation.operation == "retire":
                    continue
                payload = stored_operation_payload(operation)
            if belongs_to_obsolete_batch(key[0], key[1], payload):
                prefix = "planned " if operation is not None else ""
                blockers.append(
                    f"{prefix}{key[0]}/{key[1]} is an omitted obsolete candidate"
                )
            references = strings(payload) & audited_ids
            if references:
                prefix = "planned " if operation is not None else ""
                blockers.append(
                    f"{prefix}{key[0]}/{key[1]} -> {','.join(sorted(references))}"
                )
        missing = sorted(set(audited) - seen_current)
        blockers.extend(
            f"{collection}/{object_id} is no longer current"
            for collection, object_id in missing
        )
        # Creates are absent from the locked current-row query. Check every
        # non-retire arrival so one ChangeSet cannot introduce the dangling
        # reference it claims to have ruled out.
        for key, operation in planned.items():
            if key in seen_rows or operation.operation == "retire":
                continue
            payload = stored_operation_payload(operation)
            if belongs_to_obsolete_batch(key[0], key[1], payload):
                blockers.append(
                    f"planned {key[0]}/{key[1]} is an omitted obsolete candidate"
                )
            references = strings(payload) & audited_ids
            if references:
                blockers.append(
                    f"planned {key[0]}/{key[1]} -> {','.join(sorted(references))}"
                )
        if blockers:
            raise ChangeSetConflict(
                "obsolete candidate retirement changed after preview or has current "
                "external references: " + " | ".join(sorted(set(blockers))[:20])
            )

    @staticmethod
    def _assert_stale_pending_topic_identity_retirement(
        cursor: Any,
        plan: ChangeSetPlan,
        audit: Mapping[str, Any] | None,
    ) -> None:
        """Recheck pending topic identities invalidated by re-extraction."""

        if audit is None:
            unaudited = sorted(
                (operation.collection, operation.object_id)
                for operation in plan.operations
                if operation.operation == "retire"
                and operation.collection == "topic_identity_reconciliations"
            )
            has_extraction_retirement = any(
                operation.operation == "retire"
                and (
                    operation.collection in EXTRACTION_RECORD_COLLECTIONS
                    or operation.collection == "source_documents"
                )
                for operation in plan.operations
            )
            if unaudited and has_extraction_retirement:
                raise ChangeSetConflict(
                    "stale topic identity retirement is missing its audit: "
                    + ", ".join(
                        f"{collection}/{object_id}"
                        for collection, object_id in unaudited[:20]
                    )
                )
            return
        if (
            audit.get("schema_version")
            != "wang_stale_pending_topic_identity_retirement_v1"
            or audit.get("reason_code")
            != "pending_topic_identity_invalidated_by_extraction_supersession"
        ):
            raise ChangeSetConflict(
                "stale topic identity retirement audit is missing its governed identity"
            )
        canonical_audit = dict(audit)
        stored_scope_sha256 = str(canonical_audit.pop("scope_sha256", ""))
        if stored_scope_sha256 != sha256_json(canonical_audit):
            raise ChangeSetConflict(
                "stale topic identity retirement audit scope SHA does not match"
            )
        batch_id = str(audit.get("batch_id") or "")
        if not batch_id.startswith("RB-") or len(batch_id) <= 3:
            raise ChangeSetConflict(
                "stale topic identity retirement audit has an invalid batch id"
            )
        retired_ids = {
            operation.object_id
            for operation in plan.operations
            if operation.operation == "retire"
            and (
                operation.collection in EXTRACTION_RECORD_COLLECTIONS
                or operation.collection == "source_documents"
            )
        }
        if audit.get("retired_extraction_ids_sha256") != sha256_json(
            sorted(retired_ids)
        ):
            raise ChangeSetConflict(
                "stale topic identity audit does not match retiring extraction ids"
            )
        status = str(audit.get("status") or "")
        records = audit.get("records")
        if status == "not_needed":
            if records not in ([], None):
                raise ChangeSetConflict(
                    "not-needed topic identity audit contains retirement records"
                )
            records = []
        elif status == "planned":
            if not isinstance(records, list) or not records:
                raise ChangeSetConflict(
                    "stale topic identity retirement audit has no records"
                )
        else:
            raise ChangeSetConflict(
                "stale topic identity retirement audit has an invalid status"
            )

        audited: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in records:
            if not isinstance(row, Mapping):
                raise ChangeSetConflict(
                    "stale topic identity retirement audit contains a malformed record"
                )
            key = (
                str(row.get("collection") or ""),
                str(row.get("object_id") or ""),
            )
            if (
                key in audited
                or key[0] != "topic_identity_reconciliations"
                or not key[1]
            ):
                raise ChangeSetConflict(
                    "stale topic identity retirement audit has a duplicate or invalid key"
                )
            audited[key] = row

        planned = {
            (operation.collection, operation.object_id): operation
            for operation in plan.operations
        }
        unexpected_retires = sorted(
            key
            for key, operation in planned.items()
            if operation.operation == "retire"
            and key[0] == "topic_identity_reconciliations"
            and key not in audited
        )
        if unexpected_retires:
            raise ChangeSetConflict(
                "stale topic identity audit does not exactly cover planned retires: "
                + ", ".join(
                    f"{collection}/{object_id}"
                    for collection, object_id in unexpected_retires[:20]
                )
            )
        for key, row in audited.items():
            operation = planned.get(key)
            if operation is None or operation.operation != "retire":
                raise ChangeSetConflict(
                    f"stale topic identity retirement omits {key[0]}/{key[1]}"
                )
            if (
                operation.before_revision != row.get("expected_revision")
                or operation.before_sha256 != row.get("expected_content_sha256")
            ):
                raise ChangeSetConflict(
                    f"stale topic identity retirement snapshot drifted for {key[0]}/{key[1]}"
                )

        def strings(value: Any) -> set[str]:
            if isinstance(value, Mapping):
                found = {str(key) for key in value if isinstance(key, str)}
                for child in value.values():
                    found.update(strings(child))
                return found
            if isinstance(value, (list, tuple, set)):
                found: set[str] = set()
                for child in value:
                    found.update(strings(child))
                return found
            return {value} if isinstance(value, str) else set()

        cursor.execute(
            """SELECT collection, object_id, payload
               FROM wang_knowledge.objects
               WHERE retired_at IS NULL FOR UPDATE"""
        )
        rows = [
            (str(collection), str(object_id), payload)
            for collection, object_id, payload in cursor.fetchall()
        ]
        audited_ids = {object_id for _collection, object_id in audited}
        seen: set[tuple[str, str]] = set()
        blockers: list[str] = []

        def inspect(
            key: tuple[str, str], payload: Mapping[str, Any], *, planned_row: bool
        ) -> None:
            stale_refs = sorted(
                set(map(str, payload.get("claim_ids") or [])) & retired_ids
            )
            is_scoped_identity = (
                key[0] == "topic_identity_reconciliations"
                and str(payload.get("origin_batch_id") or "") == batch_id
                and bool(stale_refs)
            )
            if key in audited:
                seen.add(key)
                expected_refs = sorted(
                    map(str, audited[key].get("stale_claim_ids") or [])
                )
                if (
                    not is_scoped_identity
                    or stale_refs != expected_refs
                    or str(payload.get("review_status") or "") != "candidate"
                    or str(payload.get("visibility") or "") != "internal"
                    or str(payload.get("status") or "")
                    not in {"pending_match", "pending_new"}
                ):
                    blockers.append(
                        f"{key[0]}/{key[1]} is no longer the audited pending candidate"
                    )
                return
            if is_scoped_identity:
                prefix = "planned " if planned_row else ""
                blockers.append(
                    f"{prefix}{key[0]}/{key[1]} is an omitted stale identity"
                )
            references = strings(payload) & audited_ids
            if references:
                prefix = "planned " if planned_row else ""
                blockers.append(
                    f"{prefix}{key[0]}/{key[1]} -> {','.join(sorted(references))}"
                )

        seen_rows: set[tuple[str, str]] = set()
        for collection, object_id, payload in rows:
            key = (collection, object_id)
            seen_rows.add(key)
            operation = planned.get(key)
            if operation is not None:
                if operation.operation == "retire":
                    if key in audited:
                        inspect(key, payload, planned_row=False)
                    continue
                payload = stored_operation_payload(operation)
                inspect(key, payload, planned_row=True)
                continue
            inspect(key, payload, planned_row=False)
        for key, operation in planned.items():
            if key in seen_rows or operation.operation == "retire":
                continue
            inspect(key, stored_operation_payload(operation), planned_row=True)
        blockers.extend(
            f"{collection}/{object_id} is no longer current"
            for collection, object_id in sorted(set(audited) - seen)
        )
        if blockers:
            raise ChangeSetConflict(
                "stale pending topic identity retirement changed after preview, "
                "omitted an eligible row, or has current external references: "
                + " | ".join(sorted(set(blockers))[:20])
            )

    @staticmethod
    def _assert_stale_candidate_projection_retirement(
        cursor: Any,
        plan: ChangeSetPlan,
        audit: Mapping[str, Any] | None,
        whole_batch_audit: Mapping[str, Any] | None = None,
    ) -> None:
        """Recheck the narrow old-workflow projection retirement under lock."""

        if audit is None:
            whole_batch_keys = {
                (str(row.get("collection") or ""), str(row.get("object_id") or ""))
                for row in (whole_batch_audit or {}).get("records") or []
                if isinstance(row, Mapping)
            }
            unaudited = sorted(
                (operation.collection, operation.object_id)
                for operation in plan.operations
                if operation.operation == "retire"
                and operation.collection in OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS
                and (operation.collection, operation.object_id) not in whole_batch_keys
            )
            has_extraction_retirement = any(
                operation.operation == "retire"
                and (
                    operation.collection in EXTRACTION_RECORD_COLLECTIONS
                    or operation.collection == "source_documents"
                )
                for operation in plan.operations
            )
            if unaudited and has_extraction_retirement:
                raise ChangeSetConflict(
                    "stale candidate projection retirement is missing its audit: "
                    + ", ".join(
                        f"{collection}/{object_id}"
                        for collection, object_id in unaudited[:20]
                    )
                )
            return
        if (
            audit.get("schema_version")
            != "wang_stale_candidate_projection_retirement_v1"
            or audit.get("reason_code")
            != "retired_composition_projection_invalidated_by_extraction_supersession"
        ):
            raise ChangeSetConflict(
                "stale candidate projection audit is missing its governed identity"
            )
        canonical_audit = dict(audit)
        stored_scope_sha256 = str(canonical_audit.pop("scope_sha256", ""))
        if stored_scope_sha256 != sha256_json(canonical_audit):
            raise ChangeSetConflict(
                "stale candidate projection audit scope SHA does not match"
            )
        batch_ids = audit.get("batch_ids")
        if (
            not isinstance(batch_ids, list)
            or not batch_ids
            or batch_ids != sorted(set(map(str, batch_ids)))
            or any(
                not str(batch_id).startswith("RB-") or len(str(batch_id)) <= 3
                for batch_id in batch_ids
            )
        ):
            raise ChangeSetConflict(
                "stale candidate projection audit has invalid batch ids"
            )
        retired_ids = {
            operation.object_id
            for operation in plan.operations
            if operation.operation == "retire"
            and (
                operation.collection in EXTRACTION_RECORD_COLLECTIONS
                or operation.collection == "source_documents"
            )
        }
        if audit.get("retired_extraction_ids_sha256") != sha256_json(
            sorted(retired_ids)
        ):
            raise ChangeSetConflict(
                "stale candidate projection audit does not match retiring extraction ids"
            )
        status = str(audit.get("status") or "")
        records = audit.get("records")
        if status == "not_needed":
            if records not in ([], None):
                raise ChangeSetConflict(
                    "not-needed candidate projection audit contains retirement records"
                )
            records = []
        elif status == "planned":
            if not isinstance(records, list) or not records:
                raise ChangeSetConflict(
                    "stale candidate projection audit has no records"
                )
        else:
            raise ChangeSetConflict(
                "stale candidate projection audit has an invalid status"
            )

        audited: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in records:
            if not isinstance(row, Mapping):
                raise ChangeSetConflict(
                    "stale candidate projection audit contains a malformed record"
                )
            key = (
                str(row.get("collection") or ""),
                str(row.get("object_id") or ""),
            )
            if (
                key in audited
                or key[0] not in {"knowledge_routes", "editorial_syntheses"}
                or not key[1]
                or str(row.get("batch_id") or "") not in batch_ids
            ):
                raise ChangeSetConflict(
                    "stale candidate projection audit has a duplicate or invalid key"
                )
            audited[key] = row
        expected_summary = {
            "knowledge_routes": sum(
                key[0] == "knowledge_routes" for key in audited
            ),
            "editorial_syntheses": sum(
                key[0] == "editorial_syntheses" for key in audited
            ),
            "total": len(audited),
        }
        if audit.get("summary") != expected_summary:
            raise ChangeSetConflict(
                "stale candidate projection audit summary does not match its records"
            )

        planned = {
            (operation.collection, operation.object_id): operation
            for operation in plan.operations
        }
        whole_batch_keys = {
            (str(row.get("collection") or ""), str(row.get("object_id") or ""))
            for row in (whole_batch_audit or {}).get("records") or []
            if isinstance(row, Mapping)
        }
        unexpected_retires = sorted(
            key
            for key, operation in planned.items()
            if operation.operation == "retire"
            and key[0] in OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS
            and key not in audited
            and key not in whole_batch_keys
        )
        if unexpected_retires:
            raise ChangeSetConflict(
                "stale candidate projection audit does not exactly cover planned retires: "
                + ", ".join(
                    f"{collection}/{object_id}"
                    for collection, object_id in unexpected_retires[:20]
                )
            )
        for key, row in audited.items():
            operation = planned.get(key)
            if operation is None or operation.operation != "retire":
                raise ChangeSetConflict(
                    f"stale candidate projection omits {key[0]}/{key[1]}"
                )
            if (
                operation.before_revision != row.get("expected_revision")
                or operation.before_sha256 != row.get("expected_content_sha256")
            ):
                raise ChangeSetConflict(
                    f"stale candidate projection snapshot drifted for {key[0]}/{key[1]}"
                )

        plan_patterns = {
            batch_id: re.compile(
                rf"^CP-{re.escape(batch_id[3:])}-[ST]-[0-9a-f]{{12}}$"
            )
            for batch_id in batch_ids
        }
        synthesis_patterns = {
            batch_id: re.compile(
                rf"^SYN-{re.escape(batch_id[3:])}-[ST]-[0-9a-f]{{12}}$"
            )
            for batch_id in batch_ids
        }

        def projection_owner(
            collection: str, object_id: str, payload: Mapping[str, Any]
        ) -> tuple[str, str, list[str]] | None:
            if collection == "knowledge_routes":
                claim_id = str(payload.get("claim_id") or "")
                stale_claim_ids = [claim_id] if claim_id in retired_ids else []
                owner_plan_id = str(payload.get("target_id") or "")
                matches = [
                    batch_id
                    for batch_id, pattern in plan_patterns.items()
                    if pattern.fullmatch(owner_plan_id)
                ]
            elif collection == "editorial_syntheses":
                stale_claim_ids = sorted(
                    set(map(str, payload.get("claim_ids") or [])) & retired_ids
                )
                matches = [
                    batch_id
                    for batch_id, pattern in synthesis_patterns.items()
                    if pattern.fullmatch(object_id)
                    and str(payload.get("corpus_scope") or "") == batch_id
                ]
                owner_plan_id = (
                    f"CP{object_id[3:]}" if object_id.startswith("SYN-") else ""
                )
            else:
                return None
            if not stale_claim_ids:
                return None
            if len(matches) != 1:
                raise ChangeSetConflict(
                    "stale candidate projection has ambiguous or mismatched ownership: "
                    f"{collection}/{object_id}"
                )
            return matches[0], owner_plan_id, stale_claim_ids

        def strings(value: Any) -> set[str]:
            if isinstance(value, Mapping):
                found = {str(key) for key in value if isinstance(key, str)}
                for child in value.values():
                    found.update(strings(child))
                return found
            if isinstance(value, (list, tuple, set)):
                found: set[str] = set()
                for child in value:
                    found.update(strings(child))
                return found
            return {value} if isinstance(value, str) else set()

        cursor.execute(
            """SELECT collection, object_id, payload
               FROM wang_knowledge.objects
               WHERE retired_at IS NULL FOR UPDATE"""
        )
        current_rows = [
            (str(collection), str(object_id), payload)
            for collection, object_id, payload in cursor.fetchall()
        ]
        current_by_key = {
            (collection, object_id): payload
            for collection, object_id, payload in current_rows
        }
        cursor.execute(
            """SELECT object_id, payload FROM wang_knowledge.objects
               WHERE collection='composition_plans' FOR UPDATE"""
        )
        historical_plans = {
            str(object_id): payload for object_id, payload in cursor.fetchall()
        }
        seen_current: set[tuple[str, str]] = set()
        blockers: list[str] = []
        effective_rows: dict[tuple[str, str], Mapping[str, Any]] = {}
        for collection, object_id, current_payload in current_rows:
            key = (collection, object_id)
            operation = planned.get(key)
            owned = projection_owner(collection, object_id, current_payload)
            if key in audited:
                seen_current.add(key)
                expected = audited[key]
                if (
                    owned is None
                    or owned[0] != str(expected.get("batch_id") or "")
                    or owned[1] != str(expected.get("owner_plan_id") or "")
                    or owned[2]
                    != sorted(map(str, expected.get("stale_claim_ids") or []))
                    or str(current_payload.get("review_status") or "") != "candidate"
                    or str(current_payload.get("visibility") or "") != "internal"
                ):
                    blockers.append(
                        f"{collection}/{object_id} is no longer the audited candidate projection"
                    )
            elif owned is not None and (
                operation is None or operation.operation != "retire"
            ):
                prefix = "planned " if operation is not None else ""
                blockers.append(
                    f"{prefix}{collection}/{object_id} is an omitted stale projection"
                )
            elif owned is not None and operation is not None:
                blockers.append(
                    f"{collection}/{object_id} is retired without projection audit"
                )
            if operation is None:
                effective_rows[key] = current_payload
            elif operation.operation != "retire":
                effective_rows[key] = stored_operation_payload(operation)
        for key, operation in planned.items():
            if key in current_by_key or operation.operation == "retire":
                continue
            payload = stored_operation_payload(operation)
            effective_rows[key] = payload
            if projection_owner(key[0], key[1], payload) is not None:
                blockers.append(
                    f"planned {key[0]}/{key[1]} is an omitted stale projection"
                )
        blockers.extend(
            f"{collection}/{object_id} is no longer current"
            for collection, object_id in sorted(set(audited) - seen_current)
        )

        final_plans = {
            object_id: payload
            for (collection, object_id), payload in effective_rows.items()
            if collection == "composition_plans"
        }
        final_decisions: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        for (collection, object_id), payload in effective_rows.items():
            if collection == "composition_decisions":
                final_decisions.setdefault(
                    str(payload.get("plan_id") or ""), []
                ).append((object_id, payload))
        for row in audited.values():
            owner_plan_id = str(row.get("owner_plan_id") or "")
            historical_plan = historical_plans.get(owner_plan_id)
            if (
                historical_plan is None
                or str(historical_plan.get("plan_id") or "") != owner_plan_id
            ):
                blockers.append(
                    f"composition_plans/{owner_plan_id} lacks historical ownership proof"
                )
            current_plan = final_plans.get(owner_plan_id)
            if current_plan is not None and (
                str(current_plan.get("review_status") or "") != "candidate"
                or str(current_plan.get("visibility") or "") != "internal"
            ):
                blockers.append(
                    f"composition_plans/{owner_plan_id} is current plan authority"
                )
            blockers.extend(
                f"composition_decisions/{decision_id} is current decision authority"
                for decision_id, decision in final_decisions.get(owner_plan_id, [])
                if str(decision.get("review_status") or "") != "candidate"
                or str(decision.get("visibility") or "") != "internal"
            )

        audited_ids = {object_id for _collection, object_id in audited}
        for key, payload in effective_rows.items():
            referenced = strings(payload) & audited_ids
            if referenced:
                blockers.append(
                    f"{key[0]}/{key[1]} -> {','.join(sorted(referenced))}"
                )
        if blockers:
            raise ChangeSetConflict(
                "stale candidate projection retirement changed after preview, "
                "omitted an eligible row, reached current authority, or has current "
                "external references: "
                + " | ".join(sorted(set(blockers))[:20])
            )

    @staticmethod
    def _assert_stale_ai_cross_sermon_constraint_retirement(
        cursor: Any,
        plan: ChangeSetPlan,
        audit: Mapping[str, Any] | None,
    ) -> None:
        """Recheck exact stale AI constraints without retargeting judgments."""

        if audit is None:
            unaudited = sorted(
                (operation.collection, operation.object_id)
                for operation in plan.operations
                if operation.operation == "retire"
                and operation.collection == "claim_relation_constraints"
            )
            has_extraction_retirement = any(
                operation.operation == "retire"
                and (
                    operation.collection in EXTRACTION_RECORD_COLLECTIONS
                    or operation.collection == "source_documents"
                )
                for operation in plan.operations
            )
            if unaudited and has_extraction_retirement:
                raise ChangeSetConflict(
                    "stale cross-sermon constraint retirement is missing its audit: "
                    + ", ".join(
                        f"{collection}/{object_id}"
                        for collection, object_id in unaudited[:20]
                    )
                )
            return
        if (
            audit.get("schema_version")
            != "wang_stale_ai_cross_sermon_constraint_retirement_v1"
            or audit.get("reason_code")
            != "cross_sermon_judgment_invalidated_by_claim_supersession"
        ):
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit is missing its governed identity"
            )
        canonical_audit = dict(audit)
        stored_scope_sha256 = str(canonical_audit.pop("scope_sha256", ""))
        if stored_scope_sha256 != sha256_json(canonical_audit):
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit scope SHA does not match"
            )
        constraint_ids = audit.get("constraint_ids")
        if (
            not isinstance(constraint_ids, list)
            or not constraint_ids
            or constraint_ids != sorted(set(map(str, constraint_ids)))
            or any(
                re.fullmatch(r"CRC-XSR-[0-9a-f]{16}", str(value)) is None
                for value in constraint_ids
            )
        ):
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit has invalid constraint ids"
            )
        retired_ids = {
            operation.object_id
            for operation in plan.operations
            if operation.operation == "retire"
            and (
                operation.collection in EXTRACTION_RECORD_COLLECTIONS
                or operation.collection == "source_documents"
            )
        }
        if audit.get("retired_extraction_ids_sha256") != sha256_json(
            sorted(retired_ids)
        ):
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit does not match retiring extraction ids"
            )
        records = audit.get("records")
        if not isinstance(records, list):
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit records are malformed"
            )
        already_retired_ids = audit.get("already_retired_ids")
        if (
            not isinstance(already_retired_ids, list)
            or already_retired_ids != sorted(set(map(str, already_retired_ids)))
        ):
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit has invalid retired ids"
            )
        if set(already_retired_ids) | {
            str(row.get("object_id") or "")
            for row in records
            if isinstance(row, Mapping)
        } != set(constraint_ids):
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit does not cover its exact ids"
            )
        expected_status = "planned" if records else "already_retired"
        if audit.get("status") != expected_status:
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit has an invalid status"
            )

        audited: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in records:
            if not isinstance(row, Mapping):
                raise ChangeSetConflict(
                    "stale cross-sermon constraint audit contains a malformed record"
                )
            key = (
                str(row.get("collection") or ""),
                str(row.get("object_id") or ""),
            )
            if (
                key in audited
                or key[0] != "claim_relation_constraints"
                or key[1] not in constraint_ids
            ):
                raise ChangeSetConflict(
                    "stale cross-sermon constraint audit has a duplicate or invalid key"
                )
            audited[key] = row
        expected_summary = {
            "claim_relation_constraints": len(audited),
            "total": len(audited),
        }
        if audit.get("summary") != expected_summary:
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit summary does not match records"
            )

        planned = {
            (operation.collection, operation.object_id): operation
            for operation in plan.operations
        }
        unexpected_retires = sorted(
            key
            for key, operation in planned.items()
            if operation.operation == "retire"
            and key[0] == "claim_relation_constraints"
            and key not in audited
        )
        if unexpected_retires:
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit does not exactly cover planned retires: "
                + ", ".join(
                    f"{collection}/{object_id}"
                    for collection, object_id in unexpected_retires[:20]
                )
            )
        governed_ids = set(map(str, constraint_ids))
        governed_non_retires = sorted(
            key
            for key, operation in planned.items()
            if key[0] == "claim_relation_constraints"
            and key[1] in governed_ids
            and operation.operation != "retire"
        )
        if governed_non_retires:
            raise ChangeSetConflict(
                "stale cross-sermon constraint audit cannot create, update, or revive "
                "a governed id: "
                + ", ".join(
                    f"{collection}/{object_id}"
                    for collection, object_id in governed_non_retires[:20]
                )
            )
        for key, row in audited.items():
            operation = planned.get(key)
            if operation is None or operation.operation != "retire":
                raise ChangeSetConflict(
                    f"stale cross-sermon constraint omits {key[0]}/{key[1]}"
                )
            if (
                operation.before_revision != row.get("expected_revision")
                or operation.before_sha256 != row.get("expected_content_sha256")
            ):
                raise ChangeSetConflict(
                    f"stale cross-sermon constraint snapshot drifted for {key[0]}/{key[1]}"
                )

        def strings(value: Any) -> set[str]:
            if isinstance(value, Mapping):
                found = {str(key) for key in value if isinstance(key, str)}
                for child in value.values():
                    found.update(strings(child))
                return found
            if isinstance(value, (list, tuple, set)):
                found: set[str] = set()
                for child in value:
                    found.update(strings(child))
                return found
            return {value} if isinstance(value, str) else set()

        cursor.execute(
            """SELECT collection, object_id, payload
               FROM wang_knowledge.objects
               WHERE retired_at IS NULL FOR UPDATE"""
        )
        current_rows = [
            (str(collection), str(object_id), payload)
            for collection, object_id, payload in cursor.fetchall()
        ]
        current_by_key = {
            (collection, object_id): payload
            for collection, object_id, payload in current_rows
        }
        blockers: list[str] = []
        seen: set[tuple[str, str]] = set()
        effective_rows: dict[tuple[str, str], Mapping[str, Any]] = {}
        for collection, object_id, current_payload in current_rows:
            key = (collection, object_id)
            operation = planned.get(key)
            if object_id in constraint_ids and collection == "claim_relation_constraints":
                if object_id in already_retired_ids:
                    blockers.append(
                        f"claim_relation_constraints/{object_id} is current again"
                    )
                row = audited.get(key)
                if row is None:
                    blockers.append(
                        f"claim_relation_constraints/{object_id} is omitted from audit"
                    )
                else:
                    seen.add(key)
                    stale_claim_ids = sorted(
                        {
                            str(current_payload.get("source_id") or ""),
                            str(current_payload.get("target_id") or ""),
                        }
                        & retired_ids
                    )
                    if (
                        not stale_claim_ids
                        or stale_claim_ids
                        != sorted(map(str, row.get("stale_claim_ids") or []))
                        or str(current_payload.get("constraint_id") or "") != object_id
                        or str(current_payload.get("review_artifact_id") or "")
                        != object_id.removeprefix("CRC-")
                        or str(row.get("review_artifact_id") or "")
                        != object_id.removeprefix("CRC-")
                        or str(current_payload.get("source_id") or "")
                        != str(row.get("source_id") or "")
                        or str(current_payload.get("target_id") or "")
                        != str(row.get("target_id") or "")
                        or str(current_payload.get("reason") or "")
                        != str(row.get("reason") or "")
                        or str(current_payload.get("review_status") or "")
                        != "ai_consensus"
                        or str(current_payload.get("visibility") or "") != "internal"
                    ):
                        blockers.append(
                            f"claim_relation_constraints/{object_id} is no longer the audited AI judgment"
                        )
            if operation is None:
                effective_rows[key] = current_payload
            elif operation.operation != "retire":
                effective_rows[key] = stored_operation_payload(operation)
        for key, operation in planned.items():
            if key in current_by_key or operation.operation == "retire":
                continue
            effective_rows[key] = stored_operation_payload(operation)
        blockers.extend(
            f"{collection}/{object_id} is no longer current"
            for collection, object_id in sorted(set(audited) - seen)
        )
        audited_ids = governed_ids
        for key, payload in effective_rows.items():
            referenced = strings(payload) & audited_ids
            if referenced:
                blockers.append(
                    f"{key[0]}/{key[1]} -> {','.join(sorted(referenced))}"
                )
        if blockers:
            raise ChangeSetConflict(
                "stale cross-sermon constraint retirement changed after preview, "
                "lost exact authority, or has current external references: "
                + " | ".join(sorted(set(blockers))[:20])
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
                (POSTGRES_APPLY_ADVISORY_LOCK_KEY,),
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
