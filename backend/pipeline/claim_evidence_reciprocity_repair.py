"""Deterministic audit and repair planning for Claim--Evidence reciprocity.

This module freezes the PostgreSQL input under the canonical apply lock, builds a
sealed audit, and then builds one sealed preview plan.  Applying that plan is
delegated to the canonical PostgreSQL store, whose transaction lock rechecks the
sealed full Claim/Evidence snapshot.

The planner never asks a model which side of a mismatch is true.  A ruling bound
to the current Claim revision may be projected onto the EvidenceStep reverse
index.  Everything else is explicitly queued for exact source replay, source
rerun, or human adjudication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES,
    CLAIM_EVIDENCE_FREEZE_BINDING_SCHEMA_VERSION,
    CLAIM_EVIDENCE_RECIPROCITY_REPAIR_SOURCE_KIND,
    POSTGRES_APPLY_ADVISORY_LOCK_KEY,
    ChangeOperation,
    ChangeSetPlan,
    PlannedReviewEvent,
    PostgresKnowledgeStoreError,
    build_change_set_plan,
    build_claim_evidence_active_snapshot,
    build_claim_evidence_reciprocity_guard,
    build_product_dependency_active_snapshot,
    build_review_event_ledger_snapshot,
    build_source_lineage_identity_snapshot,
    operation_fingerprint_rows,
    record_content_sha,
    review_event_fingerprint_rows,
    sha256_json,
    validate_change_set_plan_integrity,
)
from backend.pipeline.extraction_supersede_runner import (
    product_impact_keys,
    products_to_rebuild,
)


AUDIT_INPUT_SCHEMA_VERSION = "wang_claim_evidence_reciprocity_audit_input_v1"
AUDIT_SCHEMA_VERSION = "wang_claim_evidence_reciprocity_audit_v1"
PLAN_SCHEMA_VERSION = "wang_claim_evidence_reciprocity_repair_plan_v1"
PREREQUISITES_MANIFEST_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_prerequisites_v1"
)
BACKUP_VERIFICATION_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_backup_verification_v1"
)
RESULT_SCHEMA_VERSION = "wang_claim_evidence_reciprocity_repair_result_v1"
COMMITTED_RECEIPT_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_committed_receipt_v1"
)
AUTHORITY_BINDING_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_plan_authority_binding_v1"
)

AUTHORITY_HUMAN_CURRENT = "human_settled_current_revision"
AUTHORITY_SEALED_REVIEWED_SOURCE = "sealed_reviewed_source_package"
AUTHORITY_UNSEALED_SOURCE = "unsealed_source_package"
AUTHORITY_CANDIDATE = "candidate_or_ai_generation"
AUTHORITY_UNRESOLVED = "unresolved_authority"

MISMATCH_RECIPROCAL = "reciprocal"
MISMATCH_CLAIM_ONLY = "claim_only"
MISMATCH_EVIDENCE_ONLY = "evidence_only"
MISMATCH_DANGLING_CLAIM = "dangling_claim_reference"
MISMATCH_DANGLING_EVIDENCE = "dangling_evidence_reference"
MISMATCH_DUPLICATE_REFERENCE = "duplicate_array_reference"

DISPOSITION_NONE = "none_reciprocal"
DISPOSITION_ADD_REVERSE = "project_human_binding_to_evidence"
DISPOSITION_EXACT_REPLAY = "exact_reviewed_source_replay"
DISPOSITION_SOURCE_RERUN = "authoritative_source_rerun"
DISPOSITION_MANUAL = "manual_adjudication"

REASON_RECIPROCAL = "pair_is_already_reciprocal"
REASON_HUMAN_PRESENT = "current_human_claim_includes_evidence"
REASON_EVIDENCE_ONLY = "evidence_only_requires_explicit_authority"
REASON_HUMAN_EVIDENCE = "current_human_evidence_requires_adjudication"
REASON_REPLAY = "sealed_reviewed_source_requires_exact_replay"
REASON_UNSEALED = "source_authority_is_not_sealed"
REASON_CANDIDATE = "candidate_generation_has_no_repair_authority"
REASON_REVIEW_AMBIGUOUS = "claim_review_authority_is_not_current_and_human"
REASON_DANGLING = "pair_endpoint_is_not_active"
REASON_DUPLICATE = "duplicate_array_reference_requires_manual_adjudication"

DIRECT_DISPOSITIONS = {DISPOSITION_ADD_REVERSE}


class ClaimEvidenceReciprocityRepairError(ValueError):
    """A repair artifact or its claimed authority is not mechanically valid."""


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def seal_artifact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical JSON copy sealed over every field except the seal."""

    result = _json_copy(payload)
    result.pop("artifact_sha256", None)
    result["artifact_sha256"] = sha256_json(result)
    return result


def validate_sealed_artifact(
    artifact: Mapping[str, Any], *, expected_schema_version: str
) -> dict[str, Any]:
    """Authenticate a sealed artifact and return a detached JSON copy."""

    value = _json_copy(artifact)
    if value.get("schema_version") != expected_schema_version:
        raise ClaimEvidenceReciprocityRepairError(
            f"schema_version must be {expected_schema_version}"
        )
    claimed = str(value.pop("artifact_sha256", ""))
    if not claimed or claimed != sha256_json(value):
        raise ClaimEvidenceReciprocityRepairError(
            f"{expected_schema_version} artifact seal does not match"
        )
    value["artifact_sha256"] = claimed
    return value


def _require_nonempty_string(value: Any, *, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ClaimEvidenceReciprocityRepairError(f"{field} must be non-empty")
    return result


def _reference_list(
    payload: Mapping[str, Any],
    field: str,
    owner: str,
    *,
    reject_duplicates: bool = True,
    duplicate_sink: list[dict[str, Any]] | None = None,
) -> list[str]:
    raw = payload.get(field, [])
    if not isinstance(raw, list):
        raise ClaimEvidenceReciprocityRepairError(f"{owner}.{field} must be a list")
    values = [
        _require_nonempty_string(value, field=f"{owner}.{field}[{index}]")
        for index, value in enumerate(raw)
    ]
    counts = Counter(values)
    repeated = sorted(value for value, count in counts.items() if count > 1)
    if duplicate_sink is not None:
        collection, object_id = owner.split("/", 1)
        duplicate_sink.extend(
            {
                "collection": collection,
                "object_id": object_id,
                "field": field,
                "referenced_id": value,
                "occurrences": counts[value],
            }
            for value in repeated
        )
    if repeated and reject_duplicates:
        raise ClaimEvidenceReciprocityRepairError(
            f"{owner}.{field} contains duplicate references: " + ", ".join(repeated)
        )
    return values


def _normalize_current_review_events(
    row: Mapping[str, Any], *, collection: str, object_id: str, revision: int
) -> list[dict[str, Any]]:
    raw_events = row.get("review_events") or []
    if not isinstance(raw_events, list):
        raise ClaimEvidenceReciprocityRepairError(
            f"{collection}/{object_id}.review_events must be a list"
        )
    current: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.review_events[{index}] must be an object"
            )
        event = _json_copy(raw)
        event_id = _require_nonempty_string(
            event.get("review_event_id"),
            field=f"{collection}/{object_id}.review_events[{index}].review_event_id",
        )
        if event_id in seen:
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} repeats review event {event_id}"
            )
        seen.add(event_id)
        event_collection = str(event.get("collection") or collection)
        event_object_id = str(event.get("object_id") or object_id)
        try:
            event_revision = int(event.get("object_revision"))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} review event {event_id} has no revision"
            ) from None
        if event_collection != collection or event_object_id != object_id:
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} review event {event_id} belongs to another object"
            )
        if event_revision == revision:
            current.append(event)
    return sorted(current, key=lambda event: str(event["review_event_id"]))


def _normalize_active_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    normalized: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"active_records[{index}] must be an object"
            )
        collection = _require_nonempty_string(
            raw.get("collection"), field=f"active_records[{index}].collection"
        )
        if collection not in {"claims", "evidence_steps"}:
            raise ClaimEvidenceReciprocityRepairError(
                f"active_records[{index}] has unsupported collection {collection!r}"
            )
        object_id = _require_nonempty_string(
            raw.get("object_id"), field=f"active_records[{index}].object_id"
        )
        key = (collection, object_id)
        if key in by_key:
            raise ClaimEvidenceReciprocityRepairError(
                f"active snapshot repeats {collection}/{object_id}"
            )
        try:
            revision = int(raw.get("revision"))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.revision must be an integer"
            ) from None
        if revision < 1:
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.revision must be positive"
            )
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.payload must be an object"
            )
        payload = _json_copy(payload)
        id_field = "claim_id" if collection == "claims" else "evidence_step_id"
        if str(payload.get(id_field) or "") != object_id:
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} payload identity does not match"
            )
        # The column revision is the CAS authority.  Historical retire/revive
        # operations can advance it without rewriting the semantic payload's
        # compatibility ``revision`` field.
        content_sha256 = _require_nonempty_string(
            raw.get("content_sha256"),
            field=f"{collection}/{object_id}.content_sha256",
        )
        if record_content_sha(payload) != content_sha256:
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} content SHA does not match payload"
            )

        version = raw.get("object_version")
        if not isinstance(version, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.object_version is required"
            )
        version = _json_copy(version)
        if (
            int(version.get("revision") or 0) != revision
            or str(version.get("content_sha256") or "") != content_sha256
        ):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} current ObjectVersion does not match"
            )
        version_payload = version.get("payload")
        version_semantic = dict(version_payload) if isinstance(version_payload, Mapping) else {}
        active_semantic = dict(payload)
        version_semantic.pop("revision", None)
        active_semantic.pop("revision", None)
        if not isinstance(version_payload, Mapping) or version_semantic != active_semantic:
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} current ObjectVersion payload does not match"
            )
        producer_id = _require_nonempty_string(
            version.get("change_set_id"),
            field=f"{collection}/{object_id}.object_version.change_set_id",
        )
        producer = raw.get("producer_change_set")
        if not isinstance(producer, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.producer_change_set is required"
            )
        producer = _json_copy(producer)
        if str(producer.get("change_set_id") or "") != producer_id:
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} producer ChangeSet does not match ObjectVersion"
            )
        if str(producer.get("status") or "") != "applied":
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} producer ChangeSet is not applied"
            )
        for field in ("fingerprint_sha256", "source_kind", "source_sha256"):
            _require_nonempty_string(
                producer.get(field), field=f"{collection}/{object_id}.producer_change_set.{field}"
            )
        producer_operation = raw.get("producer_operation")
        if not isinstance(producer_operation, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.producer_operation is required"
            )
        producer_operation = _json_copy(producer_operation)
        try:
            operation_index = int(producer_operation.get("operation_index"))
            after_revision = int(producer_operation.get("after_revision"))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} producer operation has invalid revisions"
            ) from None
        if (
            operation_index < 0
            or str(producer_operation.get("change_set_id") or "") != producer_id
            or str(producer_operation.get("collection") or "") != collection
            or str(producer_operation.get("object_id") or "") != object_id
            or str(producer_operation.get("operation") or "")
            not in {"create", "update", "revive"}
            or after_revision != revision
            or str(producer_operation.get("after_sha256") or "") != content_sha256
        ):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id} producer operation does not prove current ObjectVersion"
            )

        current_events = _normalize_current_review_events(
            raw, collection=collection, object_id=object_id, revision=revision
        )
        source_lineage = raw.get("source_lineage") or {}
        if not isinstance(source_lineage, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.source_lineage must be an object"
            )
        item = {
            "collection": collection,
            "object_id": object_id,
            "revision": revision,
            "content_sha256": content_sha256,
            "payload": payload,
            "object_version": version,
            "producer_change_set": producer,
            "producer_operation": producer_operation,
            "current_review_events": current_events,
            "source_lineage": _json_copy(source_lineage),
        }
        normalized.append(item)
        by_key[key] = item
    normalized.sort(key=lambda item: (item["collection"], item["object_id"]))
    return normalized, by_key


def _is_current_human_settled(claim: Mapping[str, Any]) -> bool:
    status = str(claim["payload"].get("review_status") or "candidate")
    if status not in {"approved", "human_approved"}:
        return False
    if str(claim["producer_change_set"].get("source_kind") or "") != (
        "review_decision"
    ):
        return False
    producer_change_set_id = str(
        claim["object_version"].get("change_set_id") or ""
    )
    events = claim.get("current_review_events") or []
    matching = [
        event
        for event in events
        if str(event.get("reviewer_kind") or "") == "human"
        and str(event.get("decision") or "") == status
        and isinstance(event.get("artifact"), Mapping)
        and str(event["artifact"].get("change_set_id") or "")
        == producer_change_set_id
    ]
    return len(events) == 1 and len(matching) == 1


def _normalize_authority_records(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    allowed = {
        AUTHORITY_SEALED_REVIEWED_SOURCE,
        AUTHORITY_UNSEALED_SOURCE,
        AUTHORITY_CANDIDATE,
        AUTHORITY_UNRESOLVED,
    }
    normalized: list[dict[str, Any]] = []
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"authority_records[{index}] must be an object"
            )
        claim_id = _require_nonempty_string(
            raw.get("claim_id"), field=f"authority_records[{index}].claim_id"
        )
        evidence_id = _require_nonempty_string(
            raw.get("evidence_step_id"),
            field=f"authority_records[{index}].evidence_step_id",
        )
        authority_class = _require_nonempty_string(
            raw.get("authority_class"),
            field=f"authority_records[{index}].authority_class",
        )
        if authority_class not in allowed:
            raise ClaimEvidenceReciprocityRepairError(
                f"authority_records[{index}] has unsupported authority_class"
            )
        key = (claim_id, evidence_id)
        if key in by_pair:
            raise ClaimEvidenceReciprocityRepairError(
                f"authority_records repeat pair {claim_id}/{evidence_id}"
            )
        item = _json_copy(raw)
        item["claim_id"] = claim_id
        item["evidence_step_id"] = evidence_id
        item["authority_class"] = authority_class
        if authority_class == AUTHORITY_SEALED_REVIEWED_SOURCE:
            for field in (
                "package_id",
                "package_sha256",
                "reviewed_artifact_sha256",
                "historical_change_set_id",
            ):
                _require_nonempty_string(
                    item.get(field), field=f"authority_records[{index}].{field}"
                )
            if item.get("review_completion") != "complete":
                raise ClaimEvidenceReciprocityRepairError(
                    f"authority_records[{index}] reviewed source is not complete"
                )
        normalized.append(item)
        by_pair[key] = item
    normalized.sort(key=lambda item: (item["claim_id"], item["evidence_step_id"]))
    return normalized, by_pair


def _normalize_prerequisites(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    findings: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"prerequisites[{index}] must be an object"
            )
        row = _json_copy(raw)
        change_set_id = _require_nonempty_string(
            row.get("change_set_id"), field=f"prerequisites[{index}].change_set_id"
        )
        if change_set_id in seen:
            raise ClaimEvidenceReciprocityRepairError(
                f"prerequisites repeat ChangeSet {change_set_id}"
            )
        seen.add(change_set_id)
        _require_nonempty_string(
            row.get("fingerprint_sha256"),
            field=f"prerequisites[{index}].fingerprint_sha256",
        )
        if str(row.get("status") or "") != "applied":
            findings.append(f"{change_set_id}:not_applied")
        normalized.append(row)
    normalized.sort(key=lambda row: row["change_set_id"])
    return normalized, sorted(findings)


def _timestamp_text(value: Any, *, field: str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    result = str(value or "").strip()
    if not result:
        raise ClaimEvidenceReciprocityRepairError(f"{field} must be non-empty")
    return result


def _normalize_review_event_ledger_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    value = _json_copy(snapshot)
    expected_empty = build_review_event_ledger_snapshot(())
    if value.get("schema_version") != expected_empty["schema_version"]:
        raise ClaimEvidenceReciprocityRepairError(
            "review-event ledger snapshot schema is unsupported"
        )
    count = value.get("count")
    if (
        not isinstance(count, int)
        or count < 0
        or len(str(value.get("rows_sha256") or "")) != 64
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "review-event ledger snapshot lacks its exact root and count"
        )
    sealed = dict(value)
    observed = str(sealed.pop("snapshot_sha256", ""))
    if observed != sha256_json(sealed):
        raise ClaimEvidenceReciprocityRepairError(
            "review-event ledger snapshot seal does not match"
        )
    return value


def _normalize_source_lineage_identity_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    value = _json_copy(snapshot)
    records = value.get("records")
    if not isinstance(records, list):
        raise ClaimEvidenceReciprocityRepairError(
            "source-lineage snapshot lacks row identities"
        )
    try:
        rebuilt = build_source_lineage_identity_snapshot(records)
    except PostgresKnowledgeStoreError as exc:
        raise ClaimEvidenceReciprocityRepairError(str(exc)) from exc
    if rebuilt != value:
        raise ClaimEvidenceReciprocityRepairError(
            "source-lineage snapshot seal does not match"
        )
    return value


def _source_lineage_snapshot_from_active_records(
    active_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    for record in active_records:
        lineage = record.get("source_lineage") or {}
        if not isinstance(lineage, Mapping):
            continue
        for field, collection in (
            ("source_fragments", "source_fragments"),
            ("source_documents", "source_documents"),
        ):
            rows = lineage.get(field) or []
            if not isinstance(rows, list):
                raise ClaimEvidenceReciprocityRepairError(
                    f"source_lineage.{field} must be a list"
                )
            for raw in rows:
                if not isinstance(raw, Mapping):
                    raise ClaimEvidenceReciprocityRepairError(
                        f"source_lineage.{field} contains a malformed row"
                    )
                item = {
                    "collection": collection,
                    "object_id": str(raw.get("object_id") or ""),
                    "revision": raw.get("revision"),
                    "content_sha256": str(raw.get("content_sha256") or ""),
                    "retired": bool(raw.get("retired")),
                }
                key = (collection, item["object_id"])
                if key in identities and identities[key] != item:
                    raise ClaimEvidenceReciprocityRepairError(
                        f"source lineage repeats inconsistent row {collection}/{item['object_id']}"
                    )
                identities[key] = item
    try:
        return build_source_lineage_identity_snapshot(identities.values())
    except PostgresKnowledgeStoreError as exc:
        raise ClaimEvidenceReciprocityRepairError(str(exc)) from exc


def _review_event_snapshot_from_active_records(
    active_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Provide a deterministic test/offline denominator when no freeze is supplied.

    A real apply is still fail-closed on a database freeze binding.  This
    fallback keeps pure audit/planner use useful and asserts that the global
    ledger contains exactly the event rows supplied by that offline input.
    """

    rows: list[dict[str, Any]] = []
    for record in active_records:
        raw_events = record.get("review_events") or []
        if not isinstance(raw_events, list):
            raise ClaimEvidenceReciprocityRepairError(
                "active record review_events must be a list"
            )
        for event in raw_events:
            if not isinstance(event, Mapping):
                raise ClaimEvidenceReciprocityRepairError(
                    "active record review_events contains a malformed row"
                )
            copied = _json_copy(event)
            # Offline fixtures historically omitted the immutable ledger
            # timestamp.  The sentinel cannot match a real PostgreSQL freeze,
            # so it remains fail-closed if such an artifact reaches apply.
            copied.setdefault("created_at", "1970-01-01T00:00:00+00:00")
            rows.append(copied)
    try:
        return build_review_event_ledger_snapshot(rows)
    except PostgresKnowledgeStoreError as exc:
        raise ClaimEvidenceReciprocityRepairError(str(exc)) from exc


def _build_freeze_binding(
    *,
    frozen_input_artifact_sha256: str,
    frozen_at: str,
    database_identity: Mapping[str, Any],
) -> dict[str, Any]:
    binding = {
        "schema_version": CLAIM_EVIDENCE_FREEZE_BINDING_SCHEMA_VERSION,
        "frozen_input_artifact_sha256": _require_nonempty_string(
            frozen_input_artifact_sha256,
            field="frozen_input_artifact_sha256",
        ),
        "frozen_at": _timestamp_text(frozen_at, field="frozen_at"),
        "database_identity": _json_copy(database_identity),
    }
    binding["binding_sha256"] = sha256_json(binding)
    return _normalize_freeze_binding(binding)


def _normalize_freeze_binding(
    binding: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if binding is None:
        return None
    value = _json_copy(binding)
    if value.get("schema_version") != CLAIM_EVIDENCE_FREEZE_BINDING_SCHEMA_VERSION:
        raise ClaimEvidenceReciprocityRepairError(
            "freeze binding schema is unsupported"
        )
    artifact_sha = str(value.get("frozen_input_artifact_sha256") or "")
    try:
        frozen_at = datetime.fromisoformat(
            str(value.get("frozen_at") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ClaimEvidenceReciprocityRepairError(
            "freeze binding timestamp is invalid"
        ) from exc
    identity = value.get("database_identity")
    if (
        len(artifact_sha) != 64
        or frozen_at.tzinfo is None
        or not isinstance(identity, Mapping)
        or not str(identity.get("database_name") or "")
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "freeze binding is incomplete"
        )
    sealed = dict(value)
    observed = str(sealed.pop("binding_sha256", ""))
    if observed != sha256_json(sealed):
        raise ClaimEvidenceReciprocityRepairError(
            "freeze binding seal does not match"
        )
    return value


@contextmanager
def _locked_repeatable_read_cursor(store: Any):
    """Acquire the apply lock before PostgreSQL establishes the read snapshot.

    A transaction-level advisory lock SELECT is itself the first statement and
    therefore fixes a REPEATABLE READ snapshot *before* it finishes waiting.
    A session lock survives the small READ COMMITTED transaction used to
    acquire it; after that transaction commits, the first statement of the next
    transaction can safely set REPEATABLE READ/READ ONLY before any snapshot is
    taken.
    """

    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
            (POSTGRES_APPLY_ADVISORY_LOCK_KEY,),
        )
        connection.commit()
        try:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                (POSTGRES_APPLY_ADVISORY_LOCK_KEY,),
            )
            connection.commit()


def _normalize_prerequisites_manifest(
    manifest: Mapping[str, Any],
) -> list[dict[str, str]]:
    value = validate_sealed_artifact(
        manifest, expected_schema_version=PREREQUISITES_MANIFEST_SCHEMA_VERSION
    )
    rows = value.get("change_sets")
    if not isinstance(rows, list) or not rows:
        raise ClaimEvidenceReciprocityRepairError(
            "prerequisites manifest must explicitly name at least one ChangeSet"
        )
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"prerequisites manifest change_sets[{index}] must be an object"
            )
        change_set_id = _require_nonempty_string(
            raw.get("change_set_id"),
            field=f"prerequisites manifest change_sets[{index}].change_set_id",
        )
        fingerprint = _require_nonempty_string(
            raw.get("fingerprint_sha256"),
            field=f"prerequisites manifest change_sets[{index}].fingerprint_sha256",
        )
        if len(fingerprint) != 64:
            raise ClaimEvidenceReciprocityRepairError(
                f"prerequisite {change_set_id} fingerprint_sha256 must be 64 characters"
            )
        if change_set_id in seen:
            raise ClaimEvidenceReciprocityRepairError(
                f"prerequisites manifest repeats ChangeSet {change_set_id}"
            )
        seen.add(change_set_id)
        result.append(
            {"change_set_id": change_set_id, "fingerprint_sha256": fingerprint}
        )
    return sorted(result, key=lambda row: row["change_set_id"])


def _source_lineage(
    payload: Mapping[str, Any], producer: Mapping[str, Any]
) -> dict[str, Any]:
    source_document_ids = {
        str(value)
        for value in payload.get("source_document_ids") or []
        if str(value)
    }
    for field in ("source_document_id", "source_id"):
        if payload.get(field):
            source_document_ids.add(str(payload[field]))
    extraction_fingerprints = {
        str(value)
        for value in payload.get("extraction_fingerprints") or []
        if str(value)
    }
    if payload.get("extraction_fingerprint"):
        extraction_fingerprints.add(str(payload["extraction_fingerprint"]))
    source_fragment_ids = {
        str(value)
        for value in payload.get("source_fragment_ids") or []
        if str(value)
    }
    if payload.get("source_fragment_id"):
        source_fragment_ids.add(str(payload["source_fragment_id"]))
    return {
        "producer_change_set_id": str(producer["change_set_id"]),
        "producer_package_id": str(producer["package_id"]),
        "producer_source_kind": str(producer["source_kind"]),
        "producer_source_sha256": str(producer["source_sha256"]),
        "source_document_ids": sorted(source_document_ids),
        "source_fragment_ids": sorted(source_fragment_ids),
        "extraction_fingerprints": sorted(extraction_fingerprints),
        "producer_metadata": _json_copy(producer.get("metadata") or {}),
    }


def freeze_claim_evidence_reciprocity_input(
    store: Any,
    *,
    prerequisites_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze a lineage-complete audit input under the canonical apply lock."""

    expected_prerequisites = _normalize_prerequisites_manifest(
        prerequisites_manifest
    )
    expected_by_id = {
        row["change_set_id"]: row for row in expected_prerequisites
    }
    with _locked_repeatable_read_cursor(store) as cursor:
        cursor.execute("SELECT transaction_timestamp()")
        timestamp_row = cursor.fetchone()
        if not timestamp_row:
            raise ClaimEvidenceReciprocityRepairError(
                "PostgreSQL freeze did not return a transaction timestamp"
            )
        frozen_at = _timestamp_text(timestamp_row[0], field="frozen_at")
        cursor.execute(
            """SELECT current_database(), current_setting('server_version_num'),
                      inet_server_addr()::text, inet_server_port(),
                      current_setting('cluster_name', true),
                      CASE
                        WHEN has_function_privilege(
                               current_user, 'pg_control_system()', 'EXECUTE'
                             )
                        THEN (SELECT system_identifier::text FROM pg_control_system())
                        ELSE NULL
                      END"""
        )
        database_row = cursor.fetchone()
        if not database_row or not str(database_row[0] or ""):
            raise ClaimEvidenceReciprocityRepairError(
                "PostgreSQL freeze did not return its database identity"
            )
        database_identity = {
            "database_name": str(database_row[0]),
            "server_version_num": str(database_row[1] or ""),
        }
        optional_database_identity = {
            "server_address": database_row[2] if len(database_row) > 2 else None,
            "server_port": database_row[3] if len(database_row) > 3 else None,
            "cluster_name": database_row[4] if len(database_row) > 4 else None,
            "system_identifier": database_row[5] if len(database_row) > 5 else None,
        }
        database_identity.update(
            {
                key: str(value)
                for key, value in optional_database_identity.items()
                if value not in {None, ""}
            }
        )

        cursor.execute(
            """SELECT change_set_id, fingerprint_sha256, package_id, source_kind,
                      source_sha256, status, summary, metadata, created_at, applied_at
               FROM wang_knowledge.change_sets
               WHERE change_set_id = ANY(%s)
               ORDER BY change_set_id""",
            (sorted(expected_by_id),),
        )
        prerequisites: list[dict[str, Any]] = []
        observed_prerequisite_ids: set[str] = set()
        for row in cursor.fetchall():
            change_set_id = str(row[0])
            if change_set_id in observed_prerequisite_ids:
                raise ClaimEvidenceReciprocityRepairError(
                    f"PostgreSQL repeats prerequisite ChangeSet {change_set_id}"
                )
            observed_prerequisite_ids.add(change_set_id)
            expected = expected_by_id.get(change_set_id)
            if (
                expected is None
                or str(row[1]) != expected["fingerprint_sha256"]
                or str(row[5]) != "applied"
                or row[9] is None
            ):
                raise ClaimEvidenceReciprocityRepairError(
                    f"prerequisite ChangeSet {change_set_id} is not the exact applied KCS"
                )
            prerequisites.append(
                {
                    "change_set_id": change_set_id,
                    "fingerprint_sha256": str(row[1]),
                    "package_id": str(row[2]),
                    "source_kind": str(row[3]),
                    "source_sha256": str(row[4]),
                    "status": str(row[5]),
                    "summary": _json_copy(row[6] or {}),
                    "metadata": _json_copy(row[7] or {}),
                    "created_at": _timestamp_text(
                        row[8], field=f"prerequisite {change_set_id}.created_at"
                    ),
                    "applied_at": _timestamp_text(
                        row[9], field=f"prerequisite {change_set_id}.applied_at"
                    ),
                }
            )
        missing_prerequisites = sorted(
            set(expected_by_id) - observed_prerequisite_ids
        )
        if missing_prerequisites:
            raise ClaimEvidenceReciprocityRepairError(
                "PostgreSQL lacks prerequisite applied ChangeSets: "
                + ", ".join(missing_prerequisites)
            )

        cursor.execute(
            """SELECT o.collection, o.object_id, o.revision, o.content_sha256,
                      o.payload, ov.revision, ov.content_sha256, ov.payload,
                      ov.change_set_id, ov.recorded_at,
                      cs.fingerprint_sha256, cs.package_id, cs.source_kind,
                      cs.source_sha256, cs.status, cs.summary, cs.metadata,
                      cs.created_at, cs.applied_at,
                      co.operation_index, co.operation, co.collection, co.object_id,
                      co.before_sha256, co.after_sha256,
                      co.before_revision, co.after_revision, co.details
               FROM wang_knowledge.objects o
               JOIN wang_knowledge.object_versions ov
                 ON ov.collection=o.collection AND ov.object_id=o.object_id
                AND ov.revision=o.revision AND ov.content_sha256=o.content_sha256
               JOIN wang_knowledge.change_sets cs
                 ON cs.change_set_id=ov.change_set_id
               LEFT JOIN wang_knowledge.change_operations co
                 ON co.change_set_id=ov.change_set_id
                AND co.collection=ov.collection AND co.object_id=ov.object_id
                AND co.after_revision=ov.revision
                AND co.after_sha256=ov.content_sha256
               WHERE o.collection = ANY(%s) AND o.retired_at IS NULL
               ORDER BY o.collection, o.object_id, co.operation_index""",
            (["claims", "evidence_steps"],),
        )
        active_records: list[dict[str, Any]] = []
        active_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        producer_change_sets: dict[str, dict[str, Any]] = {}
        for row in cursor.fetchall():
            collection = str(row[0])
            object_id = str(row[1])
            key = (collection, object_id)
            if key in active_by_key:
                raise ClaimEvidenceReciprocityRepairError(
                    "PostgreSQL has ambiguous current producer operation for "
                    f"{collection}/{object_id}"
                )
            producer = {
                "change_set_id": str(row[8]),
                "fingerprint_sha256": str(row[10]),
                "package_id": str(row[11]),
                "source_kind": str(row[12]),
                "source_sha256": str(row[13]),
                "status": str(row[14]),
                "summary": _json_copy(row[15] or {}),
                "metadata": _json_copy(row[16] or {}),
                "created_at": _timestamp_text(
                    row[17], field=f"{collection}/{object_id}.producer.created_at"
                ),
                "applied_at": _timestamp_text(
                    row[18], field=f"{collection}/{object_id}.producer.applied_at"
                ),
            }
            operation = {
                "change_set_id": str(row[8]),
                "operation_index": row[19],
                "operation": str(row[20] or ""),
                "collection": str(row[21] or ""),
                "object_id": str(row[22] or ""),
                "before_sha256": str(row[23]) if row[23] is not None else None,
                "after_sha256": str(row[24] or ""),
                "before_revision": int(row[25]) if row[25] is not None else None,
                "after_revision": int(row[26]) if row[26] is not None else None,
                "details": _json_copy(row[27] or {}),
            }
            if row[19] is None:
                raise ClaimEvidenceReciprocityRepairError(
                    "PostgreSQL current ObjectVersion lacks producer operation: "
                    f"{collection}/{object_id}"
                )
            payload = _json_copy(row[4])
            item = {
                "collection": collection,
                "object_id": object_id,
                "revision": int(row[2]),
                "content_sha256": str(row[3]),
                "payload": payload,
                "object_version": {
                    "revision": int(row[5]),
                    "content_sha256": str(row[6]),
                    "payload": _json_copy(row[7]),
                    "change_set_id": str(row[8]),
                    "recorded_at": _timestamp_text(
                        row[9], field=f"{collection}/{object_id}.recorded_at"
                    ),
                },
                "producer_change_set": producer,
                "producer_operation": operation,
                "review_events": [],
                "source_lineage": _source_lineage(payload, producer),
            }
            active_records.append(item)
            active_by_key[key] = item
            prior_producer = producer_change_sets.setdefault(str(row[8]), producer)
            if prior_producer != producer:
                raise ClaimEvidenceReciprocityRepairError(
                    f"PostgreSQL returned inconsistent ChangeSet {row[8]}"
                )

        cursor.execute(
            """SELECT r.review_event_id, r.collection, r.object_id,
                      r.object_revision, r.reviewer_kind, r.reviewer_id,
                      r.decision, r.reason, r.artifact, r.created_at
               FROM wang_knowledge.review_events r
               JOIN wang_knowledge.objects o
                 ON o.collection=r.collection AND o.object_id=r.object_id
                AND o.revision=r.object_revision AND o.retired_at IS NULL
               WHERE r.collection = ANY(%s)
               ORDER BY r.collection, r.object_id, r.review_event_id""",
            (["claims", "evidence_steps"],),
        )
        for row in cursor.fetchall():
            key = (str(row[1]), str(row[2]))
            target = active_by_key.get(key)
            if target is None:
                raise ClaimEvidenceReciprocityRepairError(
                    f"PostgreSQL current review event has no active object: {key[0]}/{key[1]}"
                )
            target["review_events"].append(
                {
                    "review_event_id": str(row[0]),
                    "collection": key[0],
                    "object_id": key[1],
                    "object_revision": int(row[3]),
                    "reviewer_kind": str(row[4]),
                    "reviewer_id": str(row[5]),
                    "decision": str(row[6]),
                    "reason": str(row[7] or ""),
                    "artifact": _json_copy(row[8] or {}),
                    "created_at": _timestamp_text(
                        row[9], field=f"review event {row[0]}.created_at"
                    ),
                }
            )

        cursor.execute(
            """SELECT r.review_event_id, r.collection, r.object_id,
                      r.object_revision, r.reviewer_kind, r.reviewer_id,
                      r.decision, r.reason, r.artifact, r.created_at
               FROM wang_knowledge.review_events r
               ORDER BY r.review_event_id"""
        )
        try:
            review_event_ledger_snapshot = build_review_event_ledger_snapshot(
                cursor.fetchall()
            )
        except PostgresKnowledgeStoreError as exc:
            raise ClaimEvidenceReciprocityRepairError(str(exc)) from exc
        review_event_ledger_count = int(review_event_ledger_snapshot["count"])

        cursor.execute(
            """SELECT object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection='product_dependencies' AND retired_at IS NULL
               ORDER BY object_id"""
        )
        product_dependency_records: list[dict[str, Any]] = []
        product_dependencies: dict[str, dict[str, Any]] = {}
        for object_id, revision, content_sha256, payload_value in cursor.fetchall():
            dependency_id = str(object_id)
            payload = _json_copy(payload_value)
            if record_content_sha(payload) != str(content_sha256):
                raise ClaimEvidenceReciprocityRepairError(
                    f"ProductDependency {dependency_id} content SHA does not match"
                )
            product_dependency_records.append(
                {
                    "object_id": dependency_id,
                    "revision": int(revision),
                    "content_sha256": str(content_sha256),
                    "payload": payload,
                }
            )
            product_dependencies[dependency_id] = payload

        referenced_fragment_ids = sorted(
            {
                fragment_id
                for record in active_records
                for fragment_id in record["source_lineage"]["source_fragment_ids"]
            }
        )
        cursor.execute(
            """SELECT object_id, revision, content_sha256, payload, retired_at
               FROM wang_knowledge.objects
               WHERE collection='source_fragments' AND object_id = ANY(%s)
               ORDER BY object_id""",
            (referenced_fragment_ids,),
        )
        fragments_by_id: dict[str, dict[str, Any]] = {}
        for object_id, revision, content_sha256, payload_value, retired_at in cursor.fetchall():
            fragment_id = str(object_id)
            payload = _json_copy(payload_value)
            if (
                fragment_id in fragments_by_id
                or str(payload.get("fragment_id") or "") != fragment_id
                or record_content_sha(payload) != str(content_sha256)
            ):
                raise ClaimEvidenceReciprocityRepairError(
                    f"SourceFragment {fragment_id} has ambiguous identity or content SHA"
                )
            fragments_by_id[fragment_id] = {
                "object_id": fragment_id,
                "revision": int(revision),
                "content_sha256": str(content_sha256),
                "source_document_id": str(payload.get("source_id") or ""),
                "retired": retired_at is not None,
                "retired_at": (
                    _timestamp_text(
                        retired_at, field=f"SourceFragment {fragment_id}.retired_at"
                    )
                    if retired_at is not None
                    else None
                ),
            }

        referenced_document_ids = {
            source_id
            for record in active_records
            for source_id in record["source_lineage"]["source_document_ids"]
        }
        referenced_document_ids.update(
            str(fragment["source_document_id"])
            for fragment in fragments_by_id.values()
            if fragment["source_document_id"]
        )
        cursor.execute(
            """SELECT object_id, revision, content_sha256, payload, retired_at
               FROM wang_knowledge.objects
               WHERE collection='source_documents' AND object_id = ANY(%s)
               ORDER BY object_id""",
            (sorted(referenced_document_ids),),
        )
        documents_by_id: dict[str, dict[str, Any]] = {}
        active_source_documents: list[dict[str, Any]] = []
        for object_id, revision, content_sha256, payload_value, retired_at in cursor.fetchall():
            source_id = str(object_id)
            payload = _json_copy(payload_value)
            if (
                source_id in documents_by_id
                or str(payload.get("source_id") or "") != source_id
                or record_content_sha(payload) != str(content_sha256)
            ):
                raise ClaimEvidenceReciprocityRepairError(
                    f"SourceDocument {source_id} has ambiguous identity or content SHA"
                )
            record = {
                "object_id": source_id,
                "revision": int(revision),
                "content_sha256": str(content_sha256),
                "payload": payload,
                "retired": retired_at is not None,
                "retired_at": (
                    _timestamp_text(
                        retired_at, field=f"SourceDocument {source_id}.retired_at"
                    )
                    if retired_at is not None
                    else None
                ),
            }
            documents_by_id[source_id] = record
            if retired_at is None:
                active_source_documents.append(record)

        source_lineage_findings: list[dict[str, str]] = []
        for record in active_records:
            collection = str(record["collection"])
            object_id = str(record["object_id"])
            lineage = record["source_lineage"]
            fragments: list[dict[str, Any]] = []
            document_ids = set(lineage["source_document_ids"])
            for fragment_id in lineage["source_fragment_ids"]:
                fragment = fragments_by_id.get(fragment_id)
                if fragment is None:
                    source_lineage_findings.append(
                        {
                            "code": "source_fragment_missing",
                            "collection": collection,
                            "object_id": object_id,
                            "referenced_id": fragment_id,
                        }
                    )
                    continue
                fragments.append(_json_copy(fragment))
                if fragment["source_document_id"]:
                    document_ids.add(str(fragment["source_document_id"]))
                if fragment["retired"]:
                    source_lineage_findings.append(
                        {
                            "code": "source_fragment_retired",
                            "collection": collection,
                            "object_id": object_id,
                            "referenced_id": fragment_id,
                        }
                    )
            documents: list[dict[str, Any]] = []
            for source_id in sorted(document_ids):
                document = documents_by_id.get(source_id)
                if document is None:
                    source_lineage_findings.append(
                        {
                            "code": "source_document_missing",
                            "collection": collection,
                            "object_id": object_id,
                            "referenced_id": source_id,
                        }
                    )
                    continue
                documents.append(
                    {
                        key: _json_copy(value)
                        for key, value in document.items()
                        if key != "payload"
                    }
                )
                if document["retired"]:
                    source_lineage_findings.append(
                        {
                            "code": "source_document_retired",
                            "collection": collection,
                            "object_id": object_id,
                            "referenced_id": source_id,
                        }
                    )
            lineage["source_fragments"] = fragments
            lineage["source_documents"] = documents
            lineage["chain_sha256"] = sha256_json(
                {"source_fragments": fragments, "source_documents": documents}
            )
        source_lineage_findings.sort(
            key=lambda row: (
                row["collection"],
                row["object_id"],
                row["code"],
                row["referenced_id"],
            )
        )

    normalized_records, _ = _normalize_active_records(active_records)
    active_records.sort(key=lambda row: (row["collection"], row["object_id"]))
    snapshot = build_claim_evidence_active_snapshot(
        [
            (
                row["collection"],
                row["object_id"],
                row["revision"],
                row["content_sha256"],
                row["payload"],
            )
            for row in normalized_records
        ]
    )
    dependency_identities = [
        {
            "object_id": row["object_id"],
            "revision": row["revision"],
            "content_sha256": row["content_sha256"],
        }
        for row in product_dependency_records
    ]
    source_lineage_identity_snapshot = build_source_lineage_identity_snapshot(
        [
            {
                "collection": collection,
                "object_id": row["object_id"],
                "revision": row["revision"],
                "content_sha256": row["content_sha256"],
                "retired": row["retired"],
            }
            for collection, source_rows in (
                ("source_fragments", fragments_by_id.values()),
                ("source_documents", documents_by_id.values()),
            )
            for row in source_rows
        ]
    )
    return seal_artifact(
        {
            "schema_version": AUDIT_INPUT_SCHEMA_VERSION,
            "frozen_at": frozen_at,
            "database_identity": database_identity,
            "freeze_transaction": {
                "isolation": "repeatable_read",
                "read_only": True,
                "advisory_lock_key": POSTGRES_APPLY_ADVISORY_LOCK_KEY,
            },
            "prerequisites_manifest_sha256": prerequisites_manifest[
                "artifact_sha256"
            ],
            "prerequisites": prerequisites,
            "active_records": active_records,
            "active_snapshot": snapshot,
            "producer_change_sets": [
                producer_change_sets[key] for key in sorted(producer_change_sets)
            ],
            # Source-package authority is bound only by the dedicated bridge;
            # a database freeze never authenticates caller-supplied authority.
            "authority_records": [],
            "product_dependencies": product_dependencies,
            "product_dependency_records": product_dependency_records,
            "product_dependency_snapshot_sha256": sha256_json(
                dependency_identities
            ),
            "active_source_documents": active_source_documents,
            "source_lineage_findings": source_lineage_findings,
            "source_lineage_snapshot_sha256": sha256_json(
                [
                    {
                        "collection": row["collection"],
                        "object_id": row["object_id"],
                        "source_lineage": row["source_lineage"],
                    }
                    for row in active_records
                ]
            ),
            "source_lineage_identity_snapshot": source_lineage_identity_snapshot,
            "review_event_ledger_count": review_event_ledger_count,
            "review_event_ledger_snapshot": review_event_ledger_snapshot,
        }
    )


def verify_postgres_backup_dump(
    backup_dump: Path,
    *,
    freeze_binding: Mapping[str, Any] | None = None,
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Authenticate a PostgreSQL archive by parsing its pg_restore TOC."""

    path = backup_dump.resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ClaimEvidenceReciprocityRepairError(
            "apply requires a non-empty existing backup dump"
        )
    normalized_binding = _normalize_freeze_binding(freeze_binding)
    try:
        completed = run(
            ["pg_restore", "--list", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaimEvidenceReciprocityRepairError(
            f"pg_restore could not inspect backup dump: {exc}"
        ) from exc
    listing = str(getattr(completed, "stdout", "") or "")
    if int(getattr(completed, "returncode", 1)) != 0:
        error = str(getattr(completed, "stderr", "") or "").strip()
        raise ClaimEvidenceReciprocityRepairError(
            "pg_restore --list rejected backup dump"
            + (f": {error}" if error else "")
        )
    toc_rows = [
        line.strip()
        for line in listing.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]
    if not toc_rows:
        raise ClaimEvidenceReciprocityRepairError(
            "pg_restore --list returned no archive entries"
        )
    contains_wang_knowledge_schema = any(
        re.search(r"\bSCHEMA\s+-\s+wang_knowledge(?:\s|$)", row)
        for row in toc_rows
    )
    table_coverage = {
        table_name: {"table": False, "table_data": False}
        for table_name in CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES
    }
    toc_object = re.compile(
        r"^\d+;\s+\d+\s+\d+\s+(TABLE DATA|TABLE)\s+"
        r"wang_knowledge\s+(\S+)(?:\s|$)"
    )
    for row in toc_rows:
        match = toc_object.match(row)
        if not match:
            continue
        kind, table_name = match.groups()
        if table_name in table_coverage:
            table_coverage[table_name][
                "table_data" if kind == "TABLE DATA" else "table"
            ] = True
    required_table_coverage = [
        {
            "table_name": table_name,
            "has_table_definition": table_coverage[table_name]["table"],
            "has_table_data": table_coverage[table_name]["table_data"],
        }
        for table_name in CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES
    ]
    contains_required_table_data = all(
        row["has_table_definition"] and row["has_table_data"]
        for row in required_table_coverage
    )
    archive_timestamp_match = re.search(
        r"^;\s*Archive created at\s+(.+?)\s*$", listing, flags=re.MULTILINE
    )
    archive_database_match = re.search(
        r"^;\s*dbname:\s+(.+?)\s*$", listing, flags=re.MULTILINE
    )
    archive_created_at: str | None = None
    archive_database_name: str | None = None
    if archive_timestamp_match:
        raw_timestamp = archive_timestamp_match.group(1).strip()
        timezone_offsets = {
            "UTC": "+00:00",
            "GMT": "+00:00",
            "EST": "-05:00",
            "EDT": "-04:00",
            "CST": "-06:00",
            "CDT": "-05:00",
            "MST": "-07:00",
            "MDT": "-06:00",
            "PST": "-08:00",
            "PDT": "-07:00",
        }
        timestamp_parts = raw_timestamp.rsplit(" ", 1)
        normalized_timestamp = raw_timestamp
        if len(timestamp_parts) == 2 and timestamp_parts[1] in timezone_offsets:
            normalized_timestamp = (
                timestamp_parts[0] + timezone_offsets[timestamp_parts[1]]
            )
        try:
            archive_timestamp = datetime.fromisoformat(normalized_timestamp)
        except ValueError:
            archive_timestamp = None
        if archive_timestamp is not None and archive_timestamp.tzinfo is not None:
            archive_created_at = archive_timestamp.astimezone(timezone.utc).isoformat()
    if archive_database_match:
        archive_database_name = archive_database_match.group(1).strip()
    if normalized_binding is not None:
        frozen_at = datetime.fromisoformat(
            str(normalized_binding["frozen_at"]).replace("Z", "+00:00")
        )
        if archive_created_at is None:
            raise ClaimEvidenceReciprocityRepairError(
                "pg_restore TOC lacks a timezone-bound archive timestamp"
            )
        archive_timestamp = datetime.fromisoformat(archive_created_at)
        if archive_timestamp < frozen_at.replace(microsecond=0):
            raise ClaimEvidenceReciprocityRepairError(
                "backup archive predates the frozen PostgreSQL snapshot"
            )
        expected_database = str(
            normalized_binding["database_identity"]["database_name"]
        )
        if archive_database_name != expected_database:
            raise ClaimEvidenceReciprocityRepairError(
                "backup archive database does not match the frozen PostgreSQL database"
            )
        if not contains_wang_knowledge_schema:
            raise ClaimEvidenceReciprocityRepairError(
                "backup archive does not contain the wang_knowledge schema"
            )
    if not contains_required_table_data:
        missing = [
            row["table_name"]
            for row in required_table_coverage
            if not row["has_table_definition"] or not row["has_table_data"]
        ]
        raise ClaimEvidenceReciprocityRepairError(
            "backup archive lacks required wang_knowledge table definitions or "
            "TABLE DATA entries: " + ", ".join(missing)
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    verification = {
            "schema_version": BACKUP_VERIFICATION_SCHEMA_VERSION,
            "path": str(path),
            "sha256": digest.hexdigest(),
            "size_bytes": path.stat().st_size,
            "pg_restore_list_sha256": hashlib.sha256(
                listing.encode("utf-8")
            ).hexdigest(),
            "pg_restore_entry_count": len(toc_rows),
            "archive_created_at": archive_created_at,
            "archive_database_name": archive_database_name,
            "contains_wang_knowledge_schema": contains_wang_knowledge_schema,
            "contains_required_table_data": contains_required_table_data,
            "required_table_coverage": required_table_coverage,
            "required_table_coverage_sha256": sha256_json(
                required_table_coverage
            ),
            "timestamp_precision": "second",
            "frozen_input_artifact_sha256": (
                normalized_binding["frozen_input_artifact_sha256"]
                if normalized_binding is not None
                else None
            ),
            "frozen_at": (
                normalized_binding["frozen_at"]
                if normalized_binding is not None
                else None
            ),
            "database_identity": (
                normalized_binding["database_identity"]
                if normalized_binding is not None
                else None
            ),
        }
    return seal_artifact(verification)


def _classify_pair(
    *,
    mismatch_type: str,
    claim: Mapping[str, Any] | None,
    evidence: Mapping[str, Any] | None,
    authority: Mapping[str, Any] | None,
) -> tuple[str, str, str, bool]:
    if mismatch_type == MISMATCH_RECIPROCAL:
        return AUTHORITY_UNRESOLVED, REASON_RECIPROCAL, DISPOSITION_NONE, False
    if mismatch_type == MISMATCH_DUPLICATE_REFERENCE:
        return AUTHORITY_UNRESOLVED, REASON_DUPLICATE, DISPOSITION_MANUAL, True
    if mismatch_type in {MISMATCH_DANGLING_CLAIM, MISMATCH_DANGLING_EVIDENCE}:
        return AUTHORITY_UNRESOLVED, REASON_DANGLING, DISPOSITION_MANUAL, True
    if evidence is not None and _is_current_human_settled(evidence):
        return (
            AUTHORITY_HUMAN_CURRENT,
            REASON_HUMAN_EVIDENCE,
            DISPOSITION_MANUAL,
            True,
        )
    if claim is not None and _is_current_human_settled(claim):
        if mismatch_type == MISMATCH_CLAIM_ONLY:
            return AUTHORITY_HUMAN_CURRENT, REASON_HUMAN_PRESENT, DISPOSITION_ADD_REVERSE, False
        reason = (
            REASON_HUMAN_EVIDENCE
            if evidence is not None and _is_current_human_settled(evidence)
            else REASON_EVIDENCE_ONLY
        )
        return AUTHORITY_HUMAN_CURRENT, reason, DISPOSITION_MANUAL, True
    authority_class = str((authority or {}).get("authority_class") or "")
    if authority_class == AUTHORITY_SEALED_REVIEWED_SOURCE:
        return authority_class, REASON_REPLAY, DISPOSITION_EXACT_REPLAY, True
    if authority_class == AUTHORITY_UNSEALED_SOURCE:
        return authority_class, REASON_UNSEALED, DISPOSITION_SOURCE_RERUN, True
    claim_status = (
        str(claim["payload"].get("review_status") or "candidate")
        if claim is not None
        else ""
    )
    if claim_status == "human_review_required":
        return AUTHORITY_UNRESOLVED, REASON_REVIEW_AMBIGUOUS, DISPOSITION_MANUAL, True
    if authority_class == AUTHORITY_CANDIDATE or (
        claim is not None
        and claim_status not in {"approved", "human_approved", "superseded"}
    ):
        return AUTHORITY_CANDIDATE, REASON_CANDIDATE, DISPOSITION_SOURCE_RERUN, True
    return AUTHORITY_UNRESOLVED, REASON_REVIEW_AMBIGUOUS, DISPOSITION_MANUAL, True


def build_reciprocity_audit(
    active_records: Sequence[Mapping[str, Any]],
    *,
    prerequisites: Sequence[Mapping[str, Any]] = (),
    authority_records: Sequence[Mapping[str, Any]] = (),
    source_lineage_findings: Sequence[Mapping[str, Any]] = (),
    review_event_ledger_count: int | None = None,
    review_event_ledger_snapshot: Mapping[str, Any] | None = None,
    freeze_binding: Mapping[str, Any] | None = None,
    source_lineage_identity_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a sealed audit with full-set roots and detailed mismatch rows."""

    records, by_key = _normalize_active_records(active_records)
    authorities, authority_by_pair = _normalize_authority_records(authority_records)
    prerequisites_normalized, prerequisite_findings = _normalize_prerequisites(prerequisites)
    lineage_findings: list[dict[str, Any]] = []
    for index, raw in enumerate(source_lineage_findings):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"source_lineage_findings[{index}] must be an object"
            )
        finding = _json_copy(raw)
        for field in ("code", "collection", "object_id", "referenced_id"):
            _require_nonempty_string(
                finding.get(field),
                field=f"source_lineage_findings[{index}].{field}",
            )
        lineage_findings.append(finding)
    lineage_findings.sort(
        key=lambda row: (
            row["collection"], row["object_id"], row["code"], row["referenced_id"]
        )
    )
    if review_event_ledger_snapshot is None:
        normalized_review_snapshot = _review_event_snapshot_from_active_records(
            active_records
        )
    else:
        normalized_review_snapshot = _normalize_review_event_ledger_snapshot(
            review_event_ledger_snapshot
        )
    if review_event_ledger_count is not None and review_event_ledger_count < 0:
        raise ClaimEvidenceReciprocityRepairError(
            "review_event_ledger_count must be non-negative"
        )
    if (
        review_event_ledger_count is not None
        and review_event_ledger_count != normalized_review_snapshot["count"]
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "review-event ledger count disagrees with its sealed row root"
        )
    review_event_ledger_count = int(normalized_review_snapshot["count"])
    normalized_freeze_binding = _normalize_freeze_binding(freeze_binding)
    normalized_source_lineage_snapshot = (
        _normalize_source_lineage_identity_snapshot(
            source_lineage_identity_snapshot
        )
        if source_lineage_identity_snapshot is not None
        else _source_lineage_snapshot_from_active_records(active_records)
    )
    claim_ids = {
        object_id for collection, object_id in by_key if collection == "claims"
    }
    evidence_ids = {
        object_id for collection, object_id in by_key if collection == "evidence_steps"
    }
    claim_pairs: list[tuple[str, str]] = []
    evidence_pairs: list[tuple[str, str]] = []
    dangling_claim: list[tuple[str, str]] = []
    dangling_evidence: list[tuple[str, str]] = []
    duplicate_references: list[dict[str, Any]] = []
    for claim_id in sorted(claim_ids):
        payload = by_key[("claims", claim_id)]["payload"]
        for evidence_id in _reference_list(
            payload,
            "evidence_step_ids",
            f"claims/{claim_id}",
            reject_duplicates=False,
            duplicate_sink=duplicate_references,
        ):
            pair = (claim_id, evidence_id)
            if evidence_id in evidence_ids:
                claim_pairs.append(pair)
            else:
                dangling_claim.append(pair)
    for evidence_id in sorted(evidence_ids):
        payload = by_key[("evidence_steps", evidence_id)]["payload"]
        for claim_id in _reference_list(
            payload,
            "produced_claim_ids",
            f"evidence_steps/{evidence_id}",
            reject_duplicates=False,
            duplicate_sink=duplicate_references,
        ):
            pair = (claim_id, evidence_id)
            if claim_id in claim_ids:
                evidence_pairs.append(pair)
            else:
                dangling_evidence.append(pair)

    # Duplicates were rejected above, before these sets are allowed to collapse
    # anything.  The set algebra now describes only semantic pair membership.
    claim_set = set(claim_pairs)
    evidence_set = set(evidence_pairs)
    reciprocal = sorted(claim_set & evidence_set)
    claim_only = sorted(claim_set - evidence_set)
    evidence_only = sorted(evidence_set - claim_set)
    pair_types = {
        **{pair: MISMATCH_RECIPROCAL for pair in reciprocal},
        **{pair: MISMATCH_CLAIM_ONLY for pair in claim_only},
        **{pair: MISMATCH_EVIDENCE_ONLY for pair in evidence_only},
        **{pair: MISMATCH_DANGLING_CLAIM for pair in dangling_claim},
        **{pair: MISMATCH_DANGLING_EVIDENCE for pair in dangling_evidence},
    }
    duplicate_pairs = {
        (
            row["object_id"],
            row["referenced_id"],
        )
        if row["collection"] == "claims"
        else (
            row["referenced_id"],
            row["object_id"],
        )
        for row in duplicate_references
    }
    for pair in duplicate_pairs:
        pair_types[pair] = MISMATCH_DUPLICATE_REFERENCE
    pairs: list[dict[str, Any]] = []
    for (claim_id, evidence_id), mismatch_type in sorted(pair_types.items()):
        # Reciprocal pairs are already sealed by the full pair-set roots and counts.
        # Expanding their endpoint ledgers here duplicates hundreds of megabytes
        # without adding repair evidence; only exceptional pairs need row detail.
        if mismatch_type == MISMATCH_RECIPROCAL:
            continue
        claim = by_key.get(("claims", claim_id))
        evidence = by_key.get(("evidence_steps", evidence_id))
        authority = authority_by_pair.get((claim_id, evidence_id))
        authority_class, reason, disposition, blocks_apply = _classify_pair(
            mismatch_type=mismatch_type,
            claim=claim,
            evidence=evidence,
            authority=authority,
        )
        pairs.append(
            {
                "pair_id": f"PAIR-{sha256_json([claim_id, evidence_id])[:24]}",
                "claim_id": claim_id,
                "evidence_step_id": evidence_id,
                "mismatch_type": mismatch_type,
                "claim_endpoint": (
                    {
                        key: deepcopy(claim[key])
                        for key in (
                            "revision",
                            "content_sha256",
                            "object_version",
                            "producer_change_set",
                            "producer_operation",
                            "current_review_events",
                            "source_lineage",
                        )
                    }
                    if claim
                    else None
                ),
                "evidence_endpoint": (
                    {
                        key: deepcopy(evidence[key])
                        for key in (
                            "revision",
                            "content_sha256",
                            "object_version",
                            "producer_change_set",
                            "producer_operation",
                            "current_review_events",
                            "source_lineage",
                        )
                    }
                    if evidence
                    else None
                ),
                "authority_class": authority_class,
                "authority_record": deepcopy(authority) if authority else None,
                "reason_code": reason,
                "disposition": disposition,
                "blocks_apply": blocks_apply,
            }
        )

    store_snapshot = build_claim_evidence_active_snapshot(
        [
            (
                row["collection"],
                row["object_id"],
                row["revision"],
                row["content_sha256"],
                row["payload"],
            )
            for row in records
        ]
    )
    mismatch_rows = pairs
    blockers = [row["pair_id"] for row in pairs if row["blocks_apply"]]
    affected_endpoint_keys = {
        endpoint_key
        for pair in pairs
        for endpoint_key in (
            ("claims", pair["claim_id"]),
            ("evidence_steps", pair["evidence_step_id"]),
        )
    }
    blocking_lineage_findings = [
        row
        for row in lineage_findings
        if (row["collection"], row["object_id"]) in affected_endpoint_keys
    ]
    counts = {
        "active_claims": len(claim_ids),
        "active_evidence_steps": len(evidence_ids),
        "claim_references": len(claim_pairs) + len(dangling_claim),
        "evidence_references": len(evidence_pairs) + len(dangling_evidence),
        "union_pairs": len(pair_types),
        "reciprocal_pairs": len(reciprocal),
        "claim_only": len(claim_only),
        "evidence_only": len(evidence_only),
        "dangling_claim_references": len(dangling_claim),
        "dangling_evidence_references": len(dangling_evidence),
        "mismatches": len(mismatch_rows),
        "blocking_mismatches": len(blockers),
        "duplicate_reference_rows": len(duplicate_references),
        "duplicate_array_references": sum(
            int(row["occurrences"]) - 1 for row in duplicate_references
        ),
    }
    audit = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "status": (
            "blocked"
            if prerequisite_findings or blocking_lineage_findings or blockers
            else "repair_required"
            if mismatch_rows
            else "clean"
        ),
        "store_snapshot": store_snapshot,
        "active_snapshot_sha256": str(store_snapshot["snapshot_sha256"]),
        "pair_detail_scope": "active_mismatches_only",
        "counts": counts,
        "pair_set_sha256": {
            "claim_references": sha256_json([list(pair) for pair in sorted(claim_pairs)]),
            "evidence_references": sha256_json([list(pair) for pair in sorted(evidence_pairs)]),
            "reciprocal": sha256_json([list(pair) for pair in reciprocal]),
            "claim_only": sha256_json([list(pair) for pair in claim_only]),
            "evidence_only": sha256_json([list(pair) for pair in evidence_only]),
            "dangling_claim": sha256_json([list(pair) for pair in sorted(dangling_claim)]),
            "dangling_evidence": sha256_json([list(pair) for pair in sorted(dangling_evidence)]),
        },
        "endpoints": [
            {
                key: deepcopy(row[key])
                for key in (
                    "collection",
                    "object_id",
                    "revision",
                    "content_sha256",
                    "object_version",
                    "producer_change_set",
                    "producer_operation",
                    "current_review_events",
                    "source_lineage",
                )
            }
            for row in records
            if (row["collection"], row["object_id"]) in affected_endpoint_keys
        ],
        "pairs": pairs,
        "prerequisites": prerequisites_normalized,
        "prerequisite_findings": prerequisite_findings,
        "source_lineage_findings": lineage_findings,
        "blocking_source_lineage_findings": blocking_lineage_findings,
        "review_event_ledger_count": review_event_ledger_count,
        "review_event_ledger_snapshot": normalized_review_snapshot,
        "freeze_binding": normalized_freeze_binding,
        "source_lineage_identity_snapshot": normalized_source_lineage_snapshot,
        "authority_records": authorities,
        "authority_sha256": sha256_json(authorities),
        "duplicate_references": duplicate_references,
    }
    return seal_artifact(audit)


def _change_set_from_dict(value: Mapping[str, Any]) -> ChangeSetPlan:
    try:
        operations = tuple(
            ChangeOperation(
                operation=str(row["operation"]),
                collection=str(row["collection"]),
                object_id=str(row["object_id"]),
                before_sha256=(
                    str(row["before_sha256"])
                    if row.get("before_sha256") is not None
                    else None
                ),
                after_sha256=str(row["after_sha256"]),
                before_revision=(
                    int(row["before_revision"])
                    if row.get("before_revision") is not None
                    else None
                ),
                after_revision=int(row["after_revision"]),
                payload=_json_copy(row["payload"]),
                removed_fields=tuple(map(str, row.get("removed_fields") or [])),
            )
            for row in value.get("operations") or []
        )
        events = tuple(
            PlannedReviewEvent(
                review_event_id=str(row["review_event_id"]),
                collection=str(row["collection"]),
                object_id=str(row["object_id"]),
                object_revision=int(row["object_revision"]),
                reviewer_kind=str(row["reviewer_kind"]),
                reviewer_id=str(row["reviewer_id"]),
                decision=str(row["decision"]),
                reason=str(row["reason"]),
                artifact=_json_copy(row["artifact"]),
            )
            for row in value.get("review_events") or []
        )
        return ChangeSetPlan(
            change_set_id=str(value["change_set_id"]),
            fingerprint_sha256=str(value["fingerprint_sha256"]),
            package_id=str(value["package_id"]),
            source_kind=str(value["source_kind"]),
            source_sha256=str(value["source_sha256"]),
            operations=operations,
            unchanged=int(value.get("unchanged") or 0),
            ignored_keys=tuple(map(str, value.get("ignored_keys") or [])),
            review_events=events,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ClaimEvidenceReciprocityRepairError(
            f"repair plan contains a malformed ChangeSet: {exc}"
        ) from exc


def _validate_change_set_identity(plan: ChangeSetPlan, package: Mapping[str, Any]) -> None:
    try:
        validate_change_set_plan_integrity(plan)
    except PostgresKnowledgeStoreError as exc:
        raise ClaimEvidenceReciprocityRepairError(
            f"repair ChangeSet payload integrity failed: {exc}"
        ) from exc
    source_sha = sha256_json(package)
    fingerprint = sha256_json(
        {
            "planner_schema": "wang_postgres_changeset_v2",
            "source_kind": plan.source_kind,
            "source_sha256": source_sha,
            "package_id": str(package.get("package_id") or ""),
            "operations": operation_fingerprint_rows(plan.operations),
            "review_events": review_event_fingerprint_rows(plan.review_events),
        }
    )
    if (
        plan.source_kind != CLAIM_EVIDENCE_RECIPROCITY_REPAIR_SOURCE_KIND
        or plan.source_sha256 != source_sha
        or plan.package_id != str(package.get("package_id") or "")
        or plan.fingerprint_sha256 != fingerprint
        or plan.change_set_id != f"KCS-{fingerprint[:20]}"
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair ChangeSet identity does not match its exact package and operations"
        )


def _impact_preview(
    change_set: ChangeSetPlan,
    product_dependencies: Mapping[str, Mapping[str, Any]],
    hook: Callable[[ChangeSetPlan], Sequence[Mapping[str, Any]]] | None,
) -> list[dict[str, Any]]:
    if hook is not None:
        return [_json_copy(row) for row in hook(change_set)]
    return products_to_rebuild(
        product_impact_keys(change_set), dependencies=product_dependencies
    )


def _action_manifest(audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for pair in audit.get("pairs") or []:
        if pair.get("mismatch_type") == MISMATCH_RECIPROCAL:
            continue
        action = {
            key: deepcopy(pair[key])
            for key in (
                "pair_id",
                "claim_id",
                "evidence_step_id",
                "mismatch_type",
                "authority_class",
                "reason_code",
                "disposition",
                "blocks_apply",
            )
        }
        authority_record = pair.get("authority_record")
        if isinstance(authority_record, Mapping):
            authority_unit_id = str(
                authority_record.get("authority_unit_id") or ""
            ).strip()
            if authority_unit_id:
                action["authority_unit_id"] = authority_unit_id
                action["authority_record_sha256"] = sha256_json(authority_record)
        actions.append(action)
    return actions


def _queue_manifest(actions: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    queues: dict[str, list[dict[str, Any]]] = {
        "exact_source_replay": [],
        "authoritative_source_rerun": [],
        "manual_adjudication": [],
    }
    queue_by_disposition = {
        DISPOSITION_EXACT_REPLAY: "exact_source_replay",
        DISPOSITION_SOURCE_RERUN: "authoritative_source_rerun",
        DISPOSITION_MANUAL: "manual_adjudication",
    }
    for action in actions:
        disposition = str(action.get("disposition") or "")
        if disposition in DIRECT_DISPOSITIONS:
            continue
        queue = queue_by_disposition.get(disposition)
        if queue is None:
            raise ClaimEvidenceReciprocityRepairError(
                f"unsupported mismatch disposition {disposition!r}"
            )
        queues[queue].append(_json_copy(action))
    return queues


def _authority_plan_binding(
    authority_bridge: Mapping[str, Any] | None,
    *,
    audit: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[dict[str, Any]],
]:
    """Reduce a fully validated authority bridge to plan-bound roots and queue."""

    if authority_bridge is None:
        return None, None, [_json_copy(row) for row in actions]
    # Imported lazily because the bridge consumes the audit functions in this
    # module; importing it at module load time would create a cycle.
    from backend.pipeline.claim_evidence_reciprocity_authority import (
        validate_source_work_queue,
    )
    from backend.pipeline.claim_evidence_reciprocity_authority_bridge import (
        validate_authority_bound_audit_and_queue,
    )

    bridge = validate_authority_bound_audit_and_queue(authority_bridge)
    bridge_actions = bridge.get("pair_actions")
    if bridge.get("audit") != audit or not isinstance(bridge_actions, list):
        raise ClaimEvidenceReciprocityRepairError(
            "authority bridge does not describe this exact audit and action manifest"
        )
    if len(bridge_actions) != len(actions):
        raise ClaimEvidenceReciprocityRepairError(
            "authority bridge action denominator differs from the audit"
        )
    for expected, observed in zip(actions, bridge_actions, strict=True):
        stable_fields = set(expected) - {"disposition", "reason_code"}
        if any(observed.get(key) != expected[key] for key in stable_fields):
            raise ClaimEvidenceReciprocityRepairError(
                "authority bridge changed an audited pair identity or authority"
            )
        if (
            observed.get("disposition") != expected.get("disposition")
            or observed.get("reason_code") != expected.get("reason_code")
        ) and (
            observed.get("audit_disposition") != expected.get("disposition")
            or observed.get("audit_reason_code") != expected.get("reason_code")
            or observed.get("disposition") != DISPOSITION_MANUAL
        ):
            raise ClaimEvidenceReciprocityRepairError(
                "authority bridge changed a pair action without preserving its audit"
            )
    roots = bridge["roots"]
    queue = validate_source_work_queue(
        bridge["source_work_queue"],
        expected_freeze_artifact_sha256=str(roots["frozen_input_sha256"]),
        expected_audit_artifact_sha256=str(audit["artifact_sha256"]),
        expected_pair_action_manifest_sha256=sha256_json(bridge_actions),
        expected_authority_validation_sha256=str(
            roots["authority_validation_sha256"]
        ),
    )
    binding = seal_artifact(
        {
            "schema_version": AUTHORITY_BINDING_SCHEMA_VERSION,
            "authority_bridge_artifact_sha256": str(bridge["artifact_sha256"]),
            "frozen_input_artifact_sha256": str(roots["frozen_input_sha256"]),
            "authority_manifest_sha256": str(roots["authority_manifest_sha256"]),
            "authority_validation_sha256": str(
                roots["authority_validation_sha256"]
            ),
            "audit_artifact_sha256": str(audit["artifact_sha256"]),
            "pair_action_manifest_sha256": sha256_json(bridge_actions),
            "source_work_queue_sha256": str(queue["artifact_sha256"]),
        }
    )
    return binding, _json_copy(queue), _json_copy(bridge_actions)


def _validate_plan_authority_binding(
    binding_value: Any,
    queue_value: Any,
    *,
    audit_artifact_sha256: str,
    action_manifest_sha256: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if binding_value is None and queue_value is None:
        return None, None
    if not isinstance(binding_value, Mapping) or not isinstance(
        queue_value, Mapping
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair plan must bind authority and source queue together"
        )
    from backend.pipeline.claim_evidence_reciprocity_authority import (
        validate_source_work_queue,
    )

    binding = validate_sealed_artifact(
        binding_value,
        expected_schema_version=AUTHORITY_BINDING_SCHEMA_VERSION,
    )
    if (
        binding.get("audit_artifact_sha256") != audit_artifact_sha256
        or binding.get("pair_action_manifest_sha256")
        != action_manifest_sha256
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair authority binding names another audit or action manifest"
        )
    queue = validate_source_work_queue(
        queue_value,
        expected_freeze_artifact_sha256=str(
            binding["frozen_input_artifact_sha256"]
        ),
        expected_audit_artifact_sha256=audit_artifact_sha256,
        expected_pair_action_manifest_sha256=action_manifest_sha256,
        expected_authority_validation_sha256=str(
            binding["authority_validation_sha256"]
        ),
    )
    if binding.get("source_work_queue_sha256") != queue["artifact_sha256"]:
        raise ClaimEvidenceReciprocityRepairError(
            "repair authority binding names another source work queue"
        )
    return binding, queue


def build_repair_plan(
    audit: Mapping[str, Any],
    active_records: Sequence[Mapping[str, Any]],
    *,
    product_dependencies: Mapping[str, Mapping[str, Any]] | None = None,
    product_dependency_records: Sequence[Mapping[str, Any]] | None = None,
    authority_bridge: Mapping[str, Any] | None = None,
    impact_hook: Callable[[ChangeSetPlan], Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Compile direct EvidenceStep projections into one sealed ChangeSet preview."""

    authenticated_audit = validate_sealed_artifact(
        audit, expected_schema_version=AUDIT_SCHEMA_VERSION
    )
    records, by_key = _normalize_active_records(active_records)
    snapshot = build_claim_evidence_active_snapshot(
        [
            (
                row["collection"], row["object_id"], row["revision"],
                row["content_sha256"], row["payload"],
            )
            for row in records
        ]
    )
    if snapshot != authenticated_audit.get("store_snapshot"):
        raise ClaimEvidenceReciprocityRepairError(
            "active Claim/Evidence snapshot changed after the sealed audit"
        )
    recomputed_audit = build_reciprocity_audit(
        active_records,
        prerequisites=authenticated_audit.get("prerequisites") or [],
        authority_records=authenticated_audit.get("authority_records") or [],
        source_lineage_findings=(
            authenticated_audit.get("source_lineage_findings") or []
        ),
        review_event_ledger_count=authenticated_audit.get(
            "review_event_ledger_count"
        ),
        review_event_ledger_snapshot=authenticated_audit.get(
            "review_event_ledger_snapshot"
        ),
        freeze_binding=authenticated_audit.get("freeze_binding"),
        source_lineage_identity_snapshot=authenticated_audit.get(
            "source_lineage_identity_snapshot"
        ),
    )
    if recomputed_audit != authenticated_audit:
        raise ClaimEvidenceReciprocityRepairError(
            "sealed audit does not match its deterministic pair classification"
        )

    additions: dict[str, set[str]] = defaultdict(set)
    audited_actions = _action_manifest(authenticated_audit)
    authority_binding, source_work_queue, actions = _authority_plan_binding(
        authority_bridge,
        audit=authenticated_audit,
        actions=audited_actions,
    )
    queues = _queue_manifest(actions)
    for action in actions:
        disposition = str(action["disposition"])
        if disposition == DISPOSITION_ADD_REVERSE:
            additions[str(action["evidence_step_id"])].add(str(action["claim_id"]))

    evidence_updates: list[dict[str, Any]] = []
    before_after: list[dict[str, Any]] = []
    for evidence_id in sorted(additions):
        evidence = by_key.get(("evidence_steps", evidence_id))
        if evidence is None:
            raise ClaimEvidenceReciprocityRepairError(
                f"direct repair targets missing EvidenceStep {evidence_id}"
            )
        before = _reference_list(
            evidence["payload"],
            "produced_claim_ids",
            f"evidence_steps/{evidence_id}",
            reject_duplicates=False,
        )
        add = additions[evidence_id]
        after = list(before)
        after.extend(sorted(add - set(after)))
        if after == before:
            raise ClaimEvidenceReciprocityRepairError(
                f"direct repair for {evidence_id} has no semantic effect"
            )
        evidence_updates.append(
            {"evidence_step_id": evidence_id, "produced_claim_ids": after}
        )
        before_after.append(
            {
                "evidence_step_id": evidence_id,
                "expected_revision": evidence["revision"],
                "expected_content_sha256": evidence["content_sha256"],
                "before_produced_claim_ids": before,
                "after_produced_claim_ids": after,
            }
        )

    action_sha = sha256_json(actions)
    repair_context = {
        "audit_artifact_sha256": authenticated_audit["artifact_sha256"],
        "active_snapshot_sha256": authenticated_audit["active_snapshot_sha256"],
        "authority_sha256": authenticated_audit["authority_sha256"],
        "action_manifest_sha256": action_sha,
    }
    if authority_binding is not None:
        repair_context["authority_binding_sha256"] = authority_binding[
            "artifact_sha256"
        ]
        repair_context["source_work_queue_sha256"] = source_work_queue[
            "artifact_sha256"
        ]
    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": (
            "CLAIM-EVIDENCE-RECIPROCITY-REPAIR-"
            + authenticated_audit["artifact_sha256"][:20]
        ),
        "evidence_steps": evidence_updates,
        "claim_evidence_reciprocity_repair": repair_context,
    }
    existing = {
        key: {
            "revision": row["revision"],
            "content_sha256": row["content_sha256"],
            "payload": row["payload"],
        }
        for key, row in by_key.items()
        if key[0] == "evidence_steps" and key[1] in additions
    }
    change_set = build_change_set_plan(
        package,
        existing,
        source_kind=CLAIM_EVIDENCE_RECIPROCITY_REPAIR_SOURCE_KIND,
    )
    if any(operation.collection == "claims" for operation in change_set.operations):
        raise ClaimEvidenceReciprocityRepairError(
            "reciprocity repair must never mutate a Claim"
        )
    if len(change_set.operations) != len(evidence_updates):
        raise ClaimEvidenceReciprocityRepairError(
            "each changed EvidenceStep must compile to exactly one operation"
        )
    operation_rows = operation_fingerprint_rows(change_set.operations)
    dependencies = {
        str(key): _json_copy(value)
        for key, value in sorted((product_dependencies or {}).items())
    }
    dependency_records = [
        _json_copy(row) for row in (product_dependency_records or [])
    ]
    dependency_snapshot = build_product_dependency_active_snapshot(
        dependency_records
    )
    records_by_id = {
        str(row.get("object_id") or ""): row for row in dependency_records
    }
    if len(records_by_id) != len(dependency_records):
        raise ClaimEvidenceReciprocityRepairError(
            "ProductDependency snapshot repeats an object ID"
        )
    if set(records_by_id) != set(dependencies):
        raise ClaimEvidenceReciprocityRepairError(
            "ProductDependency payloads and full identity snapshot disagree"
        )
    for dependency_id, payload in dependencies.items():
        if records_by_id[dependency_id].get("payload") != payload:
            raise ClaimEvidenceReciprocityRepairError(
                f"ProductDependency {dependency_id} payload differs from frozen identity"
            )
    impact = _impact_preview(change_set, dependencies, impact_hook)
    queues["dependency_coordination"] = _json_copy(impact)
    has_queues = any(queues.values())
    prerequisite_findings = authenticated_audit.get("prerequisite_findings") or []
    lineage_findings = authenticated_audit.get("source_lineage_findings") or []
    apply_allowed = not has_queues and not prerequisite_findings and not lineage_findings
    guard = build_claim_evidence_reciprocity_guard(
        change_set,
        snapshot,
        dependency_snapshot,
        authenticated_audit["review_event_ledger_snapshot"],
        authenticated_audit.get("freeze_binding"),
        authenticated_audit["source_lineage_identity_snapshot"],
    )
    plan_artifact = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": "planned" if apply_allowed else "blocked",
        "apply_allowed": apply_allowed,
        "audit": authenticated_audit,
        "audit_artifact_sha256": authenticated_audit["artifact_sha256"],
        "active_snapshot_sha256": authenticated_audit["active_snapshot_sha256"],
        "authority_sha256": authenticated_audit["authority_sha256"],
        "authority_binding": authority_binding,
        "authority_binding_sha256": (
            authority_binding["artifact_sha256"]
            if authority_binding is not None
            else None
        ),
        "source_work_queue": source_work_queue,
        "source_work_queue_sha256": (
            source_work_queue["artifact_sha256"]
            if source_work_queue is not None
            else None
        ),
        "action_manifest": actions,
        "action_manifest_sha256": action_sha,
        "direct_projection_states": before_after,
        "human_claim_snapshot": [
            {
                "object_id": row["object_id"],
                "revision": row["revision"],
                "content_sha256": row["content_sha256"],
            }
            for row in records
            if row["collection"] == "claims" and _is_current_human_settled(row)
        ],
        "review_event_ledger_count": authenticated_audit.get(
            "review_event_ledger_count"
        ),
        "review_event_ledger_snapshot": authenticated_audit[
            "review_event_ledger_snapshot"
        ],
        "freeze_binding": authenticated_audit.get("freeze_binding"),
        "source_lineage_identity_snapshot": authenticated_audit[
            "source_lineage_identity_snapshot"
        ],
        "queues": queues,
        "repair_package": package,
        "repair_package_sha256": sha256_json(package),
        "change_set": change_set.as_dict(),
        "change_set_fingerprint_sha256": change_set.fingerprint_sha256,
        "operation_manifest_sha256": sha256_json(operation_rows),
        "store_guard": guard,
        "product_dependency_snapshot": dependency_snapshot,
        "product_dependency_snapshot_sha256": dependency_snapshot[
            "snapshot_sha256"
        ],
        "products_to_rebuild": impact,
        "impact_preview_sha256": sha256_json(impact),
    }
    return seal_artifact(plan_artifact)


def deserialize_repair_plan(
    artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], ChangeSetPlan]:
    """Authenticate a preview and reconstruct exactly the planned ChangeSet."""

    value = validate_sealed_artifact(artifact, expected_schema_version=PLAN_SCHEMA_VERSION)
    audit = validate_sealed_artifact(
        value.get("audit") or {}, expected_schema_version=AUDIT_SCHEMA_VERSION
    )
    if (
        value.get("audit_artifact_sha256") != audit["artifact_sha256"]
        or value.get("active_snapshot_sha256") != audit["active_snapshot_sha256"]
        or value.get("authority_sha256") != audit["authority_sha256"]
        or value.get("action_manifest_sha256")
        != sha256_json(value.get("action_manifest") or [])
        or value.get("repair_package_sha256")
        != sha256_json(value.get("repair_package") or {})
        or value.get("product_dependency_snapshot_sha256")
        != (value.get("product_dependency_snapshot") or {}).get("snapshot_sha256")
        or value.get("impact_preview_sha256")
        != sha256_json(value.get("products_to_rebuild") or [])
        or value.get("review_event_ledger_snapshot")
        != audit.get("review_event_ledger_snapshot")
        or value.get("freeze_binding") != audit.get("freeze_binding")
        or value.get("source_lineage_identity_snapshot")
        != audit.get("source_lineage_identity_snapshot")
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair plan cross-artifact fingerprints do not match"
        )
    authority_binding, source_work_queue = _validate_plan_authority_binding(
        value.get("authority_binding"),
        value.get("source_work_queue"),
        audit_artifact_sha256=str(audit["artifact_sha256"]),
        action_manifest_sha256=str(value["action_manifest_sha256"]),
    )
    if (
        value.get("authority_binding_sha256")
        != (
            authority_binding["artifact_sha256"]
            if authority_binding is not None
            else None
        )
        or value.get("source_work_queue_sha256")
        != (
            source_work_queue["artifact_sha256"]
            if source_work_queue is not None
            else None
        )
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair plan authority or source-queue root does not match"
        )
    change_set = _change_set_from_dict(value.get("change_set") or {})
    _validate_change_set_identity(change_set, value["repair_package"])
    repair_context = value["repair_package"].get(
        "claim_evidence_reciprocity_repair"
    )
    if not isinstance(repair_context, Mapping) or (
        repair_context.get("authority_binding_sha256")
        != value.get("authority_binding_sha256")
        or repair_context.get("source_work_queue_sha256")
        != value.get("source_work_queue_sha256")
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair package authority roots do not match its sealed plan"
        )
    if (
        value.get("change_set_fingerprint_sha256") != change_set.fingerprint_sha256
        or value.get("operation_manifest_sha256")
        != sha256_json(operation_fingerprint_rows(change_set.operations))
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair plan operation fingerprint does not match"
        )
    expected_guard = build_claim_evidence_reciprocity_guard(
        change_set,
        audit["store_snapshot"],
        value.get("product_dependency_snapshot") or {},
        audit["review_event_ledger_snapshot"],
        audit.get("freeze_binding"),
        audit["source_lineage_identity_snapshot"],
    )
    if value.get("store_guard") != expected_guard:
        raise ClaimEvidenceReciprocityRepairError(
            "repair plan store guard does not match its ChangeSet and snapshot"
        )
    if any(operation.collection == "claims" for operation in change_set.operations):
        raise ClaimEvidenceReciprocityRepairError(
            "reciprocity repair plan contains a Claim mutation"
        )
    return value, change_set


def _read_post_apply_ledger(
    store: Any, change_set: ChangeSetPlan
) -> dict[str, Any]:
    """Read the applied ledger and current guarded rows in one locked snapshot."""

    with _locked_repeatable_read_cursor(store) as cursor:
        cursor.execute(
            """SELECT collection, object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection = ANY(%s) AND retired_at IS NULL
               ORDER BY collection, object_id""",
            (["claims", "evidence_steps"],),
        )
        active_snapshot = build_claim_evidence_active_snapshot(cursor.fetchall())
        cursor.execute(
            """SELECT object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection='product_dependencies' AND retired_at IS NULL
               ORDER BY object_id"""
        )
        dependency_snapshot = build_product_dependency_active_snapshot(
            cursor.fetchall()
        )
        cursor.execute(
            """SELECT review_event_id, collection, object_id, object_revision,
                      reviewer_kind, reviewer_id, decision, reason, artifact,
                      created_at
               FROM wang_knowledge.review_events
               ORDER BY review_event_id"""
        )
        try:
            review_event_snapshot = build_review_event_ledger_snapshot(
                cursor.fetchall()
            )
        except PostgresKnowledgeStoreError as exc:
            raise ClaimEvidenceReciprocityRepairError(str(exc)) from exc
        review_event_count = int(review_event_snapshot["count"])

        cursor.execute(
            """SELECT change_set_id, fingerprint_sha256, package_id, source_kind,
                      source_sha256, status, summary, metadata, created_at, applied_at
               FROM wang_knowledge.change_sets
               WHERE change_set_id=%s""",
            (change_set.change_set_id,),
        )
        change_set_row = cursor.fetchone()
        cursor.execute(
            """SELECT co.operation_index, co.operation, co.collection, co.object_id,
                      co.before_sha256, co.after_sha256,
                      co.before_revision, co.after_revision, co.details,
                      ov.revision, ov.content_sha256, ov.payload, ov.change_set_id
               FROM wang_knowledge.change_operations co
               LEFT JOIN wang_knowledge.object_versions ov
                 ON ov.collection=co.collection AND ov.object_id=co.object_id
                AND ov.revision=co.after_revision
                AND ov.content_sha256=co.after_sha256
                AND ov.change_set_id=co.change_set_id
               WHERE co.change_set_id=%s
               ORDER BY co.operation_index""",
            (change_set.change_set_id,),
        )
        operation_rows = list(cursor.fetchall())

    serialized_change_set = None
    if change_set_row is not None:
        serialized_change_set = {
            "change_set_id": str(change_set_row[0]),
            "fingerprint_sha256": str(change_set_row[1]),
            "package_id": str(change_set_row[2]),
            "source_kind": str(change_set_row[3]),
            "source_sha256": str(change_set_row[4]),
            "status": str(change_set_row[5]),
            "summary": _json_copy(change_set_row[6] or {}),
            "metadata": _json_copy(change_set_row[7] or {}),
            "created_at": _timestamp_text(
                change_set_row[8], field="repair ChangeSet.created_at"
            ),
            "applied_at": _timestamp_text(
                change_set_row[9], field="repair ChangeSet.applied_at"
            ),
        }
    operations = [
        {
            "operation_index": int(row[0]),
            "operation": str(row[1]),
            "collection": str(row[2]),
            "object_id": str(row[3]),
            "before_sha256": str(row[4]) if row[4] is not None else None,
            "after_sha256": str(row[5]) if row[5] is not None else None,
            "before_revision": int(row[6]) if row[6] is not None else None,
            "after_revision": int(row[7]) if row[7] is not None else None,
            "details": _json_copy(row[8] or {}),
            "object_version": (
                {
                    "revision": int(row[9]),
                    "content_sha256": str(row[10]),
                    "payload": _json_copy(row[11]),
                    "change_set_id": str(row[12]),
                }
                if row[9] is not None
                else None
            ),
        }
        for row in operation_rows
    ]
    return {
        "active_snapshot": active_snapshot,
        "product_dependency_snapshot": dependency_snapshot,
        "review_event_ledger_count": review_event_count,
        "review_event_ledger_snapshot": review_event_snapshot,
        "change_set": serialized_change_set,
        "operations": operations,
    }


def build_post_apply_result(
    *,
    store: Any,
    plan_artifact: Mapping[str, Any],
    change_set: ChangeSetPlan,
    apply_result: Mapping[str, Any],
    backup: Mapping[str, Any],
    committed_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a sealed result only after DB readback and a zero-op fresh replan."""

    audit = validate_sealed_artifact(
        plan_artifact.get("audit") or {}, expected_schema_version=AUDIT_SCHEMA_VERSION
    )
    prerequisite_manifest = seal_artifact(
        {
            "schema_version": PREREQUISITES_MANIFEST_SCHEMA_VERSION,
            "change_sets": [
                {
                    "change_set_id": row["change_set_id"],
                    "fingerprint_sha256": row["fingerprint_sha256"],
                }
                for row in audit.get("prerequisites") or []
            ],
        }
    )
    fresh = freeze_claim_evidence_reciprocity_input(
        store,
        prerequisites_manifest=prerequisite_manifest,
    )
    fresh = validate_sealed_artifact(
        fresh, expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION
    )
    ledger = _read_post_apply_ledger(store, change_set)
    if ledger["active_snapshot"] != fresh["active_snapshot"]:
        raise ClaimEvidenceReciprocityRepairError(
            "post-apply Claim/Evidence snapshots disagree across locked readback"
        )
    if (
        ledger["product_dependency_snapshot"]
        != plan_artifact.get("product_dependency_snapshot")
        or ledger["product_dependency_snapshot"]
        != build_product_dependency_active_snapshot(
            fresh.get("product_dependency_records") or []
        )
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "post-apply ProductDependency snapshot differs from preview"
        )
    counts = ledger["active_snapshot"]["counts"]
    if any(
        int(counts[key])
        for key in (
            "claim_only_pairs",
            "evidence_only_pairs",
            "dangling_endpoints",
            "duplicate_array_references",
        )
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "post-apply full Claim/Evidence graph is not clean"
        )
    baseline_review_count = plan_artifact.get("review_event_ledger_count")
    if not isinstance(baseline_review_count, int):
        raise ClaimEvidenceReciprocityRepairError(
            "repair plan lacks frozen review-event ledger count"
        )
    if (
        ledger["review_event_ledger_count"] != baseline_review_count
        or fresh["review_event_ledger_count"] != baseline_review_count
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair unexpectedly changed the review-event ledger"
        )
    baseline_review_snapshot = _normalize_review_event_ledger_snapshot(
        plan_artifact.get("review_event_ledger_snapshot") or {}
    )
    if (
        ledger["review_event_ledger_snapshot"] != baseline_review_snapshot
        or fresh.get("review_event_ledger_snapshot") != baseline_review_snapshot
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "repair unexpectedly changed the review-event ledger root"
        )

    fresh_by_key = {
        (row["collection"], row["object_id"]): row
        for row in fresh["active_records"]
    }
    for expected in plan_artifact.get("human_claim_snapshot") or []:
        actual = fresh_by_key.get(("claims", str(expected["object_id"])))
        if actual is None or (
            actual["revision"] != expected["revision"]
            or actual["content_sha256"] != expected["content_sha256"]
        ):
            raise ClaimEvidenceReciprocityRepairError(
                "repair changed a human-settled Claim: "
                f"{expected['object_id']}"
            )

    expected_operations = operation_fingerprint_rows(change_set.operations)
    observed_operations = [
        {
            key: row[key]
            for key in (
                "operation",
                "collection",
                "object_id",
                "before_sha256",
                "after_sha256",
                "before_revision",
                "after_revision",
            )
        }
        for row in ledger["operations"]
    ]
    if observed_operations != expected_operations:
        raise ClaimEvidenceReciprocityRepairError(
            "applied ChangeSet operations differ from sealed preview"
        )
    if change_set.operations:
        observed_change_set = ledger["change_set"]
        if not isinstance(observed_change_set, Mapping) or (
            observed_change_set["change_set_id"] != change_set.change_set_id
            or observed_change_set["fingerprint_sha256"]
            != change_set.fingerprint_sha256
            or observed_change_set["package_id"] != change_set.package_id
            or observed_change_set["source_kind"] != change_set.source_kind
            or observed_change_set["source_sha256"] != change_set.source_sha256
            or observed_change_set["status"] != "applied"
        ):
            raise ClaimEvidenceReciprocityRepairError(
                "applied ChangeSet ledger identity differs from sealed preview"
            )
        for expected, observed in zip(
            change_set.operations, ledger["operations"], strict=True
        ):
            version = observed["object_version"]
            if not isinstance(version, Mapping) or (
                version["revision"] != expected.after_revision
                or version["content_sha256"] != expected.after_sha256
                or version["change_set_id"] != change_set.change_set_id
                or version["payload"] != {
                    **expected.payload,
                    "revision": expected.after_revision,
                }
            ):
                raise ClaimEvidenceReciprocityRepairError(
                    "applied ObjectVersion differs from sealed operation: "
                    f"{expected.collection}/{expected.object_id}"
                )
    elif ledger["change_set"] is not None or ledger["operations"]:
        raise ClaimEvidenceReciprocityRepairError(
            "zero-operation repair unexpectedly created a ChangeSet ledger"
        )

    fresh_audit = build_reciprocity_audit(
        fresh["active_records"],
        prerequisites=fresh["prerequisites"],
        authority_records=fresh["authority_records"],
        source_lineage_findings=fresh["source_lineage_findings"],
        review_event_ledger_count=fresh["review_event_ledger_count"],
        review_event_ledger_snapshot=fresh["review_event_ledger_snapshot"],
        freeze_binding=_build_freeze_binding(
            frozen_input_artifact_sha256=str(fresh["artifact_sha256"]),
            frozen_at=str(fresh["frozen_at"]),
            database_identity=fresh.get("database_identity") or {},
        ),
        source_lineage_identity_snapshot=fresh[
            "source_lineage_identity_snapshot"
        ],
    )
    fresh_plan = build_repair_plan(
        fresh_audit,
        fresh["active_records"],
        product_dependencies=fresh["product_dependencies"],
        product_dependency_records=fresh["product_dependency_records"],
    )
    _, fresh_change_set = deserialize_repair_plan(fresh_plan)
    if fresh_change_set.operations or not fresh_plan["apply_allowed"]:
        raise ClaimEvidenceReciprocityRepairError(
            "fresh post-apply replan is not an allowed zero-operation plan"
        )
    return seal_artifact(
        {
            "schema_version": RESULT_SCHEMA_VERSION,
            "plan_artifact_sha256": plan_artifact["artifact_sha256"],
            "change_set_id": (
                change_set.change_set_id if change_set.operations else None
            ),
            "apply_result": _json_copy(apply_result),
            "backup": _json_copy(backup),
            "committed_receipt_artifact_sha256": (
                str(committed_receipt.get("artifact_sha256"))
                if committed_receipt is not None
                else None
            ),
            "post_apply_freeze_artifact_sha256": fresh["artifact_sha256"],
            "post_apply_active_snapshot": ledger["active_snapshot"],
            "post_apply_product_dependency_snapshot": ledger[
                "product_dependency_snapshot"
            ],
            "post_apply_review_event_ledger_count": ledger[
                "review_event_ledger_count"
            ],
            "post_apply_review_event_ledger_snapshot": ledger[
                "review_event_ledger_snapshot"
            ],
            "initial_freeze_binding": _json_copy(
                plan_artifact.get("freeze_binding")
            ),
            "applied_change_set": ledger["change_set"],
            "applied_operations": ledger["operations"],
            "human_claim_snapshot_sha256": sha256_json(
                plan_artifact.get("human_claim_snapshot") or []
            ),
            "fresh_audit_artifact_sha256": fresh_audit["artifact_sha256"],
            "fresh_plan_artifact_sha256": fresh_plan["artifact_sha256"],
            "fresh_plan_operations": 0,
        }
    )


def _build_committed_receipt(
    *,
    plan_artifact: Mapping[str, Any],
    change_set: ChangeSetPlan,
    apply_result: Mapping[str, Any],
    backup: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal the raw store return before post-commit verification can fail."""

    return seal_artifact(
        {
            "schema_version": COMMITTED_RECEIPT_SCHEMA_VERSION,
            "status": "store_commit_returned_result_verification_pending",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "plan_artifact_sha256": str(plan_artifact["artifact_sha256"]),
            "audit_artifact_sha256": str(plan_artifact["audit_artifact_sha256"]),
            "freeze_binding": _json_copy(plan_artifact.get("freeze_binding")),
            "change_set_id": (
                change_set.change_set_id if change_set.operations else None
            ),
            "change_set_fingerprint_sha256": change_set.fingerprint_sha256,
            "apply_result": _json_copy(apply_result),
            "backup": _json_copy(backup),
        }
    )


def apply_sealed_plan(
    artifact: Mapping[str, Any],
    *,
    store: Any,
    backup_dump: Path,
    backup_verifier: Callable[..., Mapping[str, Any]] = verify_postgres_backup_dump,
    result_builder: Callable[..., Mapping[str, Any]] = build_post_apply_result,
    committed_receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Thin apply seam; all writes and readback guards remain store-owned."""

    value, change_set = deserialize_repair_plan(artifact)
    if not value["apply_allowed"]:
        raise ClaimEvidenceReciprocityRepairError(
            "blocked repair plan cannot be applied; resolve every explicit queue first"
        )
    freeze_binding = _normalize_freeze_binding(value.get("freeze_binding"))
    if freeze_binding is None:
        raise ClaimEvidenceReciprocityRepairError(
            "repair apply requires a PostgreSQL frozen-input binding"
        )
    backup = validate_sealed_artifact(
        backup_verifier(backup_dump, freeze_binding=freeze_binding),
        expected_schema_version=BACKUP_VERIFICATION_SCHEMA_VERSION,
    )
    expected_table_coverage = [
        {
            "table_name": table_name,
            "has_table_definition": True,
            "has_table_data": True,
        }
        for table_name in CLAIM_EVIDENCE_BACKUP_REQUIRED_TABLES
    ]
    if (
        backup.get("frozen_input_artifact_sha256")
        != freeze_binding["frozen_input_artifact_sha256"]
        or backup.get("frozen_at") != freeze_binding["frozen_at"]
        or backup.get("database_identity") != freeze_binding["database_identity"]
        or backup.get("contains_wang_knowledge_schema") is not True
        or backup.get("contains_required_table_data") is not True
        or backup.get("required_table_coverage") != expected_table_coverage
        or backup.get("required_table_coverage_sha256")
        != sha256_json(expected_table_coverage)
        or backup.get("archive_database_name")
        != freeze_binding["database_identity"]["database_name"]
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "verified backup is not bound to this repair freeze"
        )
    guard = value["store_guard"]
    repair_metadata = {
        "audit_artifact_sha256": value["audit_artifact_sha256"],
        "plan_artifact_sha256": value["artifact_sha256"],
        "action_manifest_sha256": value["action_manifest_sha256"],
        "operation_manifest_sha256": value["operation_manifest_sha256"],
        "store_guard": guard,
        "backup": backup,
    }
    if value.get("authority_binding_sha256") is not None:
        repair_metadata.update(
            {
                "authority_binding_sha256": value[
                    "authority_binding_sha256"
                ],
                "source_work_queue_sha256": value[
                    "source_work_queue_sha256"
                ],
            }
        )
    apply_result = store.apply_plan(
        change_set,
        metadata={
            "claim_evidence_reciprocity_repair": repair_metadata
        },
        expected_claim_evidence_guard=guard,
    )
    committed_receipt = _build_committed_receipt(
        plan_artifact=value,
        change_set=change_set,
        apply_result=apply_result,
        backup=backup,
    )
    if committed_receipt_path is not None:
        _write_json(committed_receipt_path, committed_receipt)
    return dict(
        result_builder(
            store=store,
            plan_artifact=value,
            change_set=change_set,
            apply_result=apply_result,
            backup=backup,
            committed_receipt=committed_receipt,
        )
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimEvidenceReciprocityRepairError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ClaimEvidenceReciprocityRepairError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze_parser = commands.add_parser("freeze")
    freeze_parser.add_argument("--prerequisites", required=True, type=Path)
    freeze_parser.add_argument("--output", required=True, type=Path)
    freeze_parser.add_argument("--database-url")
    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--input", required=True, type=Path)
    audit_parser.add_argument("--output", required=True, type=Path)
    authority_parser = commands.add_parser("bind-authority")
    authority_parser.add_argument("--input", required=True, type=Path)
    authority_parser.add_argument("--manifest", required=True, type=Path)
    authority_parser.add_argument("--output", required=True, type=Path)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--input", required=True, type=Path)
    plan_authority = plan_parser.add_mutually_exclusive_group(required=True)
    plan_authority.add_argument("--audit", type=Path)
    plan_authority.add_argument("--authority-bridge", type=Path)
    plan_parser.add_argument("--output", required=True, type=Path)
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--plan", required=True, type=Path)
    apply_parser.add_argument("--backup-dump", required=True, type=Path)
    apply_parser.add_argument("--output", required=True, type=Path)
    apply_parser.add_argument("--committed-receipt", type=Path)
    apply_parser.add_argument("--database-url")
    args = parser.parse_args(list(argv) if argv is not None else None)
    load_dotenv()

    if args.command == "freeze":
        from backend.api.canonical_repository.postgres_store import (
            PostgresKnowledgeStore,
        )

        artifact = freeze_claim_evidence_reciprocity_input(
            PostgresKnowledgeStore(args.database_url),
            prerequisites_manifest=_load_json(args.prerequisites),
        )
        _write_json(args.output, artifact)
        print(
            json.dumps(
                {
                    "status": "frozen",
                    "artifact": str(args.output),
                    "active_records": len(artifact["active_records"]),
                }
            )
        )
        return 0
    if args.command == "audit":
        value = validate_sealed_artifact(
            _load_json(args.input),
            expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION,
        )
        artifact = build_reciprocity_audit(
            value.get("active_records") or [],
            prerequisites=value.get("prerequisites") or [],
            authority_records=value.get("authority_records") or [],
            source_lineage_findings=value.get("source_lineage_findings") or [],
            review_event_ledger_count=value.get("review_event_ledger_count"),
            review_event_ledger_snapshot=value.get(
                "review_event_ledger_snapshot"
            ),
            freeze_binding=_build_freeze_binding(
                frozen_input_artifact_sha256=str(value["artifact_sha256"]),
                frozen_at=str(value["frozen_at"]),
                database_identity=value.get("database_identity") or {},
            ),
            source_lineage_identity_snapshot=value.get(
                "source_lineage_identity_snapshot"
            ),
        )
        _write_json(args.output, artifact)
        print(json.dumps({"status": artifact["status"], "artifact": str(args.output)}))
        return 0
    if args.command == "bind-authority":
        from backend.pipeline.claim_evidence_reciprocity_authority_bridge import (
            build_authority_bound_audit_and_queue,
        )

        artifact = build_authority_bound_audit_and_queue(
            _load_json(args.input),
            _load_json(args.manifest),
        )
        _write_json(args.output, artifact)
        queue_counts = artifact["source_work_queue"]["counts"]
        print(
            json.dumps(
                {
                    "status": "authority_bound",
                    "artifact": str(args.output),
                    "source_tasks": queue_counts["source_tasks"],
                    "blocking_pairs": queue_counts["blocking_pairs"],
                }
            )
        )
        return 0
    if args.command == "plan":
        value = validate_sealed_artifact(
            _load_json(args.input),
            expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION,
        )
        authority_bridge: Mapping[str, Any] | None = None
        if args.authority_bridge:
            from backend.pipeline.claim_evidence_reciprocity_authority_bridge import (
                validate_authority_bound_audit_and_queue,
            )

            authority_bridge = validate_authority_bound_audit_and_queue(
                _load_json(args.authority_bridge),
                expected_frozen_input_sha256=str(value["artifact_sha256"]),
            )
            audit_value = authority_bridge["audit"]
        else:
            audit_value = _load_json(args.audit)
        artifact = build_repair_plan(
            audit_value,
            value.get("active_records") or [],
            product_dependencies=value.get("product_dependencies") or {},
            product_dependency_records=(
                value.get("product_dependency_records") or []
            ),
            authority_bridge=authority_bridge,
        )
        _write_json(args.output, artifact)
        print(json.dumps({"status": artifact["status"], "artifact": str(args.output)}))
        return 0 if artifact["apply_allowed"] else 2

    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    committed_receipt_path = args.committed_receipt or Path(
        f"{args.output}.committed-receipt.json"
    )
    result = apply_sealed_plan(
        _load_json(args.plan),
        store=PostgresKnowledgeStore(args.database_url),
        backup_dump=args.backup_dump,
        committed_receipt_path=committed_receipt_path,
    )
    _write_json(args.output, result)
    print(
        json.dumps(
            {
                "status": "verified",
                "artifact": str(args.output),
                "committed_receipt": str(committed_receipt_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
