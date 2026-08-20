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
    for collection, values in records.items():
        normalized[collection] = {}
        for value in values:
            row = value.model_dump(mode="json")
            record_id = _record_id(collection, row)
            if record_id in normalized[collection]:
                raise PostgresKnowledgeStoreError(
                    f"Duplicate record {collection}/{record_id} in package"
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
        "planner_schema": "wang_postgres_changeset_v1",
        "source_kind": source_kind,
        "source_sha256": source_sha,
        "package_id": str(package.get("package_id") or ""),
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
        "planner_schema": "wang_postgres_retirement_v1",
        "source_kind": source_kind,
        "reason": reason,
        "keys": [list(key) for key in sorted({(c, o) for c, o in keys})],
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
        "planner_schema": "wang_postgres_revival_v1",
        "source_kind": source_kind,
        "reason": reason,
        "keys": [list(key) for key in sorted({(c, o) for c, o in keys})],
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

    Arrivals come first. The withdrawal is built to exclude every id the
    package carries, so the two halves never touch the same row.
    """

    fingerprint = sha256_json({
        "planner_schema": "wang_postgres_arrival_with_withdrawal_v1",
        "arrival": arrival.fingerprint_sha256,
        "withdrawal": withdrawal.fingerprint_sha256,
    })
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id=arrival.package_id,
        source_kind=arrival.source_kind,
        source_sha256=arrival.source_sha256,
        operations=arrival.operations + withdrawal.operations,
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

    def plan_package(self, package: Mapping[str, Any], *, source_kind: str = "knowledge_package") -> ChangeSetPlan:
        normalized = normalize_package(package)
        keys = [
            (collection, object_id)
            for collection, rows in normalized.items()
            for object_id in rows
        ]
        with self.connect() as conn:
            existing = self._existing(conn, keys)
        return build_change_set_plan(package, existing, source_kind=source_kind)

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
        return (
            str(payload.get("from_id") or payload.get("source_id") or payload.get("from_claim_id")),
            str(payload.get("to_id") or payload.get("target_id") or payload.get("to_claim_id")),
            str(payload["relation_type"]),
        )

    def apply_plan(self, plan: ChangeSetPlan, *, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT status, summary FROM wang_knowledge.change_sets WHERE fingerprint_sha256=%s",
                    (plan.fingerprint_sha256,),
                )
                prior = cursor.fetchone()
                if prior and prior[0] == "applied":
                    return {"status": "already_applied", "change_set_id": plan.change_set_id, "summary": prior[1]}

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
                changed_claims: list[tuple[str, int, int]] = []
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
                    if operation.operation in {"retire", "revive"}:
                        self._set_retirement(cursor, plan, index, operation)
                        continue
                    payload = dict(operation.payload)
                    payload["revision"] = operation.after_revision
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
                    if operation.collection == "claims" and operation.operation == "update":
                        changed_claims.append(
                            (operation.object_id, operation.before_revision or 0, operation.after_revision)
                        )

                invalidated = self._invalidate_dependencies(cursor, plan, changed_claims, len(plan.operations))
                summary["invalidated_dependencies"] = invalidated
                cursor.execute(
                    """UPDATE wang_knowledge.change_sets
                       SET status='applied', summary=%s::jsonb, applied_at=now()
                       WHERE change_set_id=%s""",
                    (canonical_json(summary), plan.change_set_id),
                )
        return {"status": "applied", "change_set_id": plan.change_set_id, "summary": summary}

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

        payload = dict(operation.payload)
        payload["revision"] = operation.after_revision
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
        changed_claims: list[tuple[str, int, int]],
        operation_offset: int,
    ) -> int:
        count = 0
        for claim_id, from_revision, to_revision in changed_claims:
            cursor.execute(
                """SELECT object_id, revision, payload
                   FROM wang_knowledge.objects
                   WHERE collection='product_dependencies'
                     AND retired_at IS NULL
                     AND payload->>'claim_id'=%s
                     AND COALESCE(payload->>'status','current')='current'""",
                (claim_id,),
            )
            rows = cursor.fetchall()
            affected_ids: list[str] = []
            for dependency_id, revision, payload in rows:
                updated = dict(payload)
                updated["status"] = "invalidated"
                updated.setdefault("invalidation_change_set_ids", []).append(plan.change_set_id)
                next_revision = int(revision) + 1
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
                    (dependency_id, next_revision, content_sha, canonical_json(updated), plan.change_set_id),
                )
                cursor.execute(
                    """INSERT INTO wang_knowledge.change_operations
                       (change_set_id, operation_index, operation, collection, object_id,
                        after_sha256, before_revision, after_revision, details)
                       VALUES (%s,%s,'invalidate','product_dependencies',%s,%s,%s,%s,%s::jsonb)""",
                    (
                        plan.change_set_id, operation_offset + count, dependency_id,
                        content_sha, revision, next_revision,
                        canonical_json({"changed_claim_id": claim_id}),
                    ),
                )
                affected_ids.append(dependency_id)
                count += 1
            if affected_ids:
                event_id = f"IMPACT-{plan.change_set_id}-{claim_id}"
                event = {
                    "impact_event_id": event_id,
                    "changed_record_type": "claims",
                    "changed_record_id": claim_id,
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
        return count

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
                    cursor, plan, [(object_id, int(revision), updated["revision"])], 1
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
