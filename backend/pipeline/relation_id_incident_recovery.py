"""Plan a guarded, atomic recovery from the cross-section ID collision incident.

This is intentionally a recovery planner, not a general rollback command.  It
combines two facts in one desired-state ChangeSet:

* every incident operation can be inverted from its recorded before revision;
* reviewed source packages can be globalized deterministically without asking
  a content model to reconsider any claim or relation.

The resulting plan has at most one operation per repository key.  That avoids
an intermediate state containing restored bare IDs and avoids re-ingesting an
old whole package, which could roll back unrelated source-version changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    ChangeOperation,
    ChangeSetPlan,
    PostgresKnowledgeStore,
    canonical_json,
    normalize_package,
    record_content_sha,
    sha256_json,
)
from backend.pipeline.relation_id_namespace import (
    LEGACY_CLAIM_RELATION_ID,
    LEGACY_EVIDENCE_RELATION_ID,
    migrate_legacy_cross_section_relation_ids,
)


INPUT_SCHEMA_VERSION = "wang_relation_id_incident_recovery_input_v1"
REPORT_SCHEMA_VERSION = "wang_relation_id_incident_recovery_report_v1"
SOURCE_KIND = "wkp353_relation_id_incident_recovery"
RELATION_ID_FIELDS = {
    "knowledge_relations": "relation_id",
    "claim_relations": "claim_relation_id",
}
INCIDENT_OPERATION_TYPES = frozenset({"create", "update", "retire"})
ACTIVE_VERSION_PRODUCERS = frozenset({"create", "update", "revive"})


class RelationIdIncidentRecoveryError(RuntimeError):
    """Raised before a recovery can become a ChangeSet."""


@dataclass(frozen=True)
class RepositoryState:
    collection: str
    object_id: str
    revision: int
    content_sha256: str
    payload: dict[str, Any]
    retired: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expand_path(value: str) -> Path:
    expanded = os.path.expandvars(value)
    if "$" in expanded:
        raise RelationIdIncidentRecoveryError(
            f"recovery path contains an unresolved environment variable: {value}"
        )
    return Path(expanded).expanduser().resolve()


def load_input_manifest(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise RelationIdIncidentRecoveryError(
            f"input manifest schema_version must be {INPUT_SCHEMA_VERSION}"
        )
    change_set_ids = payload.get("incident_change_set_ids") or []
    if not change_set_ids or len(change_set_ids) != len(set(change_set_ids)):
        raise RelationIdIncidentRecoveryError(
            "incident_change_set_ids must be a non-empty unique list"
        )
    packages = payload.get("authoritative_packages") or []
    if not packages:
        raise RelationIdIncidentRecoveryError("authoritative_packages cannot be empty")
    for index, row in enumerate(packages):
        if (
            not isinstance(row, dict)
            or not row.get("path")
            or not row.get("sha256")
            or not row.get("canonical_sha256")
            or not row.get("historical_change_set_id")
        ):
            raise RelationIdIncidentRecoveryError(
                f"authoritative_packages[{index}] requires path, sha256, "
                "canonical_sha256 and historical_change_set_id"
            )
    return {
        **payload,
        "manifest_path": str(path.resolve()),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def load_repository_states(
    store: PostgresKnowledgeStore,
) -> dict[tuple[str, str], RepositoryState]:
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT collection, object_id, revision, content_sha256, payload, retired_at
               FROM wang_knowledge.objects"""
        )
        return {
            (str(collection), str(object_id)): RepositoryState(
                collection=str(collection),
                object_id=str(object_id),
                revision=int(revision),
                content_sha256=str(content_sha256),
                payload=dict(payload),
                retired=retired_at is not None,
            )
            for collection, object_id, revision, content_sha256, payload, retired_at
            in cursor.fetchall()
        }


def load_incident_ledger(
    store: PostgresKnowledgeStore, change_set_ids: Sequence[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Load applied ChangeSets, their operations, and exact before versions."""

    requested = list(change_set_ids)
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT change_set_id, fingerprint_sha256, source_sha256, source_kind,
                      status, applied_at, metadata
               FROM wang_knowledge.change_sets
               WHERE change_set_id = ANY(%s)
               ORDER BY applied_at, change_set_id""",
            (requested,),
        )
        change_sets = [
            {
                "change_set_id": str(row[0]),
                "fingerprint_sha256": str(row[1]),
                "source_sha256": str(row[2]),
                "source_kind": str(row[3]),
                "status": str(row[4]),
                "applied_at": row[5].isoformat() if row[5] else None,
                "metadata": dict(row[6] or {}),
            }
            for row in cursor.fetchall()
        ]
        found = {row["change_set_id"] for row in change_sets}
        if found != set(requested):
            raise RelationIdIncidentRecoveryError(
                "incident ChangeSets missing from the ledger: "
                + ", ".join(sorted(set(requested) - found))
            )
        not_applied = [row["change_set_id"] for row in change_sets if row["status"] != "applied"]
        if not_applied:
            raise RelationIdIncidentRecoveryError(
                "incident ChangeSets are not applied: " + ", ".join(not_applied)
            )

        cursor.execute(
            """SELECT cs.applied_at, co.change_set_id, co.operation_index, co.operation,
                      co.collection, co.object_id, co.before_sha256, co.after_sha256,
                      co.before_revision, co.after_revision
               FROM wang_knowledge.change_operations co
               JOIN wang_knowledge.change_sets cs USING (change_set_id)
               WHERE co.change_set_id = ANY(%s)
               ORDER BY cs.applied_at, co.change_set_id, co.operation_index""",
            (requested,),
        )
        operations = [
            {
                "change_set_id": str(row[1]),
                "operation_index": int(row[2]),
                "operation": str(row[3]),
                "collection": str(row[4]),
                "object_id": str(row[5]),
                "before_sha256": str(row[6]) if row[6] is not None else None,
                "after_sha256": str(row[7]),
                "before_revision": int(row[8]) if row[8] is not None else None,
                "after_revision": int(row[9]),
            }
            for row in cursor.fetchall()
        ]
        unsupported_operations = sorted(
            {row["operation"] for row in operations} - INCIDENT_OPERATION_TYPES
        )
        if unsupported_operations:
            raise RelationIdIncidentRecoveryError(
                "incident ledger contains unsupported operations: "
                + ", ".join(unsupported_operations)
            )
        operation_keys = [(row["collection"], row["object_id"]) for row in operations]
        repeated = sorted(key for key, count in Counter(operation_keys).items() if count > 1)
        if repeated:
            raise RelationIdIncidentRecoveryError(
                "incident ledger repeats repository keys; recovery requires explicit chain logic: "
                + ", ".join(f"{collection}/{object_id}" for collection, object_id in repeated)
            )

        before_needed = [
            row for row in operations if row["before_revision"] is not None
        ]
        cursor.execute(
            """SELECT ov.collection, ov.object_id, ov.revision, ov.content_sha256,
                      ov.payload, ov.change_set_id, producer.operation
               FROM wang_knowledge.object_versions ov
               JOIN wang_knowledge.change_operations co
                 ON co.collection=ov.collection AND co.object_id=ov.object_id
                AND co.before_revision=ov.revision
               LEFT JOIN wang_knowledge.change_operations producer
                 ON producer.change_set_id=ov.change_set_id
                AND producer.collection=ov.collection
                AND producer.object_id=ov.object_id
                AND producer.after_revision=ov.revision
               WHERE co.change_set_id = ANY(%s)""",
            (requested,),
        )
        before_version_rows = cursor.fetchall()
        before_versions = {
            (str(collection), str(object_id)): {
                "revision": int(revision),
                "content_sha256": str(content_sha256),
                "payload": dict(payload),
                "producer_change_set_id": str(producer_change_set_id),
                "producer_operation": str(producer_operation or ""),
            }
            for (
                collection,
                object_id,
                revision,
                content_sha256,
                payload,
                producer_change_set_id,
                producer_operation,
            ) in before_version_rows
        }
    if len(before_version_rows) != len(before_needed) or len(before_versions) != len(
        before_needed
    ):
        raise RelationIdIncidentRecoveryError(
            f"incident before-version coverage is incomplete: expected {len(before_needed)}, "
            f"found {len(before_versions)}"
        )
    for row in before_needed:
        key = (row["collection"], row["object_id"])
        before = before_versions.get(key)
        if not before or before["content_sha256"] != row["before_sha256"]:
            raise RelationIdIncidentRecoveryError(
                f"before-version SHA mismatch for {key[0]}/{key[1]}"
            )
        if before["producer_operation"] not in ACTIVE_VERSION_PRODUCERS:
            status = before["producer_operation"] or "unproven"
            raise RelationIdIncidentRecoveryError(
                f"incident before-state activity is not recoverable for "
                f"{key[0]}/{key[1]}: revision {before['revision']} was {status}"
            )
    return change_sets, operations, before_versions


def assert_incident_still_current(
    operations: Sequence[Mapping[str, Any]],
    current: Mapping[tuple[str, str], RepositoryState],
) -> None:
    """Refuse recovery if any incident-touched key moved afterwards."""

    findings: list[str] = []
    for row in operations:
        key = (str(row["collection"]), str(row["object_id"]))
        state = current.get(key)
        expected_retired = row["operation"] == "retire"
        if state is None:
            findings.append(f"{key[0]}/{key[1]} is missing")
            continue
        if state.revision != row["after_revision"]:
            findings.append(
                f"{key[0]}/{key[1]} revision {state.revision} != {row['after_revision']}"
            )
        if state.content_sha256 != row["after_sha256"]:
            findings.append(f"{key[0]}/{key[1]} content SHA changed")
        if state.retired != expected_retired:
            findings.append(f"{key[0]}/{key[1]} retirement state changed")
    if findings:
        raise RelationIdIncidentRecoveryError(
            "incident state guard failed: " + " | ".join(findings[:20])
        )


def load_authoritative_relations(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Load exact SHA-bound reviewed packages and globalize only relation IDs."""

    relations: dict[tuple[str, str], dict[str, Any]] = {}
    lineage: list[dict[str, Any]] = []
    source_keys: set[str] = set()
    for row in rows:
        path = expand_path(str(row["path"]))
        if not path.is_file():
            raise RelationIdIncidentRecoveryError(f"authoritative package is missing: {path}")
        actual_sha = sha256_file(path)
        expected_sha = str(row["sha256"])
        if actual_sha != expected_sha:
            raise RelationIdIncidentRecoveryError(
                f"authoritative package SHA mismatch for {path}: "
                f"expected {expected_sha}, found {actual_sha}"
            )
        original = json.loads(path.read_text(encoding="utf-8"))
        effective, migration = migrate_legacy_cross_section_relation_ids(original)
        expected_canonical_sha = str(row["canonical_sha256"])
        if migration["input_canonical_sha256"] != expected_canonical_sha:
            raise RelationIdIncidentRecoveryError(
                f"authoritative package canonical SHA mismatch for {path}: expected "
                f"{expected_canonical_sha}, found {migration['input_canonical_sha256']}"
            )
        source_key = migration["source_key"]
        if source_key in source_keys:
            raise RelationIdIncidentRecoveryError(
                f"authoritative source appears more than once: {source_key}"
            )
        source_keys.add(source_key)
        normalized = normalize_package(effective)
        relation_count = 0
        # The authority is only the v2 cross-section rows whose repository IDs
        # were unsafe.  Re-ingesting every relation from an older reviewed
        # package can resurrect base-extraction edges whose endpoints a later,
        # legitimate source revision replaced before the incident.
        for collection, id_field in RELATION_ID_FIELDS.items():
            legacy_pattern = (
                LEGACY_EVIDENCE_RELATION_ID
                if collection == "knowledge_relations"
                else LEGACY_CLAIM_RELATION_ID
            )
            legacy_ids = [
                str(item.get(id_field) or "")
                for item in (original.get(collection) or [])
                if legacy_pattern.fullmatch(str(item.get(id_field) or ""))
            ]
            for legacy_id in legacy_ids:
                object_id = str(migration["id_map"][legacy_id])
                payload = normalized[collection][object_id]
                key = (collection, object_id)
                if key in relations:
                    raise RelationIdIncidentRecoveryError(
                        f"globalized authoritative relation ID is duplicated: "
                        f"{collection}/{object_id}"
                    )
                if str(payload.get(id_field) or "") != object_id:
                    raise RelationIdIncidentRecoveryError(
                        f"relation self-ID mismatch: {collection}/{object_id}"
                    )
                relations[key] = payload
                relation_count += 1
        lineage.append(
            {
                "source_key": source_key,
                "path": str(path),
                "raw_sha256": actual_sha,
                "input_canonical_sha256": migration["input_canonical_sha256"],
                "effective_canonical_sha256": migration["output_canonical_sha256"],
                "historical_change_set_id": str(row["historical_change_set_id"]),
                "relation_count": relation_count,
                "relation_id_namespace_migration": migration,
            }
        )
    return relations, lineage


def validate_historical_package_authority(
    store: PostgresKnowledgeStore,
    lineage: Sequence[Mapping[str, Any]],
    *,
    incident_started_at: str,
) -> dict[str, Any]:
    """Prove every reviewed package is the exact input of a prior applied write."""

    by_id = {str(row["historical_change_set_id"]): row for row in lineage}
    if len(by_id) != len(lineage):
        raise RelationIdIncidentRecoveryError(
            "one historical ChangeSet is claimed as authority for multiple source packages"
        )
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT change_set_id, source_sha256, status, applied_at
               FROM wang_knowledge.change_sets WHERE change_set_id = ANY(%s)""",
            (list(by_id),),
        )
        records = {
            str(change_set_id): {
                "source_sha256": str(source_sha256),
                "status": str(status),
                "applied_at": applied_at.isoformat() if applied_at else None,
            }
            for change_set_id, source_sha256, status, applied_at in cursor.fetchall()
        }
    findings: list[str] = []
    incident_start = datetime.fromisoformat(incident_started_at)
    for change_set_id, row in by_id.items():
        record = records.get(change_set_id)
        if record is None:
            findings.append(f"{change_set_id} is missing")
            continue
        if record["status"] != "applied":
            findings.append(f"{change_set_id} is not applied")
        if record["source_sha256"] != row["input_canonical_sha256"]:
            findings.append(f"{change_set_id} does not bind the reviewed package SHA")
        if datetime.fromisoformat(str(record["applied_at"])) >= incident_start:
            findings.append(f"{change_set_id} is not earlier than the incident")
    if findings:
        raise RelationIdIncidentRecoveryError(
            "historical reviewed-package authority failed: " + " | ".join(findings[:20])
        )
    return {
        "reviewed_packages_checked": len(lineage),
        "historical_applied_change_sets_checked": len(records),
        "sha_mismatches": 0,
        "post_incident_authorities": 0,
    }


def is_bare_relation_key(key: tuple[str, str]) -> bool:
    collection, object_id = key
    return bool(
        (collection == "knowledge_relations" and LEGACY_EVIDENCE_RELATION_ID.fullmatch(object_id))
        or (collection == "claim_relations" and LEGACY_CLAIM_RELATION_ID.fullmatch(object_id))
    )


def desired_recovery_states(
    operations: Sequence[Mapping[str, Any]],
    before_versions: Mapping[tuple[str, str], Mapping[str, Any]],
    current: Mapping[tuple[str, str], RepositoryState],
    authoritative_relations: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any] | None]:
    """Compose pre-incident semantics and global IDs into one final state."""

    desired: dict[tuple[str, str], dict[str, Any] | None] = {}
    for row in operations:
        key = (str(row["collection"]), str(row["object_id"]))
        if row["before_revision"] is None:
            desired[key] = None
        else:
            desired[key] = dict(before_versions[key]["payload"])
    # The inverse can restore a bare relation that the bad rerun retired.  The
    # final state must never expose that unsafe repository key, so apply this
    # rule to both current rows and inverse targets, not only currently-live
    # rows.
    for key in set(current) | set(desired):
        if is_bare_relation_key(key):
            desired[key] = None
    for key, payload in authoritative_relations.items():
        desired[key] = dict(payload)
    return desired


def recovery_accounting(
    *,
    operations: Sequence[Mapping[str, Any]],
    before_versions: Mapping[tuple[str, str], Mapping[str, Any]],
    current: Mapping[tuple[str, str], RepositoryState],
    authoritative_relations: Mapping[tuple[str, str], Mapping[str, Any]],
    desired: Mapping[tuple[str, str], Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Prove every desired state has exactly one declared reason."""

    findings: list[str] = []
    counts: Counter[str] = Counter()
    for row in operations:
        key = (str(row["collection"]), str(row["object_id"]))
        target = desired[key]
        if key in authoritative_relations:
            reason = "authoritative_cross_section_relation"
            expected = record_content_sha(authoritative_relations[key])
            if target is None or record_content_sha(target) != expected:
                findings.append(f"{key[0]}/{key[1]} lost authoritative relation state")
        elif is_bare_relation_key(key):
            reason = "legacy_bare_relation_inactive"
            if target is not None:
                findings.append(f"{key[0]}/{key[1]} bare relation remains active")
        elif row["before_revision"] is None:
            reason = "incident_created_record_inactive"
            if target is not None:
                findings.append(f"{key[0]}/{key[1]} incident create was not withdrawn")
        else:
            reason = "incident_before_state_restored"
            before = before_versions[key]
            if target is None or record_content_sha(target) != before["content_sha256"]:
                findings.append(f"{key[0]}/{key[1]} before state was not restored")
        counts[reason] += 1
        counts[f"incident_collection:{key[0]}"] += 1

    for key, payload in authoritative_relations.items():
        target = desired.get(key)
        if target is None or record_content_sha(target) != record_content_sha(payload):
            findings.append(f"{key[0]}/{key[1]} authoritative target is not active")
    bare_keys = {key for key in set(current) | set(desired) if is_bare_relation_key(key)}
    for key in bare_keys:
        if desired.get(key) is not None:
            findings.append(f"{key[0]}/{key[1]} bare key was not made inactive")
    if findings:
        raise RelationIdIncidentRecoveryError(
            "recovery accounting failed: " + " | ".join(findings[:20])
        )
    return {
        "incident_keys_accounted_for": len(operations),
        "incident_created_records_made_inactive": counts[
            "incident_created_record_inactive"
        ],
        "incident_before_states_restored": counts["incident_before_state_restored"],
        "incident_keys_replaced_by_authoritative_relations": counts[
            "authoritative_cross_section_relation"
        ],
        "incident_bare_relation_keys_made_inactive": counts[
            "legacy_bare_relation_inactive"
        ],
        "authoritative_relation_targets_verified": len(authoritative_relations),
        "all_bare_relation_keys_made_inactive": len(bare_keys),
        "incident_collection_counts": {
            key.split(":", 1)[1]: value
            for key, value in sorted(counts.items())
            if key.startswith("incident_collection:")
        },
        "unaccounted_keys": 0,
    }


def build_recovery_plan(
    *,
    desired: Mapping[tuple[str, str], Mapping[str, Any] | None],
    current: Mapping[tuple[str, str], RepositoryState],
    authority: Mapping[str, Any],
) -> ChangeSetPlan:
    operations: list[ChangeOperation] = []
    unchanged = 0
    for collection, object_id in sorted(desired):
        target = desired[(collection, object_id)]
        state = current.get((collection, object_id))
        if target is None:
            if state is None or state.retired:
                unchanged += 1
                continue
            operations.append(
                ChangeOperation(
                    operation="retire",
                    collection=collection,
                    object_id=object_id,
                    before_sha256=state.content_sha256,
                    after_sha256=state.content_sha256,
                    before_revision=state.revision,
                    after_revision=state.revision + 1,
                    payload=dict(state.payload),
                )
            )
            continue

        payload = dict(target)
        after_sha = record_content_sha(payload)
        if state is None:
            operation = "create"
            before_sha = None
            before_revision = None
        elif state.retired and state.content_sha256 == after_sha:
            operation = "revive"
            before_sha = state.content_sha256
            before_revision = state.revision
        elif not state.retired and state.content_sha256 == after_sha:
            unchanged += 1
            continue
        else:
            # An update also revives a retired row through the store's upsert.
            # The recovery manifest names that combined state transition.
            operation = "update"
            before_sha = state.content_sha256
            before_revision = state.revision
        operations.append(
            ChangeOperation(
                operation=operation,
                collection=collection,
                object_id=object_id,
                before_sha256=before_sha,
                after_sha256=after_sha,
                before_revision=before_revision,
                after_revision=(before_revision or 0) + 1,
                payload=payload,
            )
        )

    keys = [(row.collection, row.object_id) for row in operations]
    if len(keys) != len(set(keys)):
        raise RelationIdIncidentRecoveryError("recovery plan contains repeated repository keys")
    source_sha = sha256_json(authority)
    fingerprint = sha256_json(
        {
            "planner_schema": REPORT_SCHEMA_VERSION,
            "source_kind": SOURCE_KIND,
            "source_sha256": source_sha,
            "operations": [
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
            ],
        }
    )
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id="WKP353-RELATION-ID-INCIDENT-RECOVERY",
        source_kind=SOURCE_KIND,
        source_sha256=source_sha,
        operations=tuple(operations),
        unchanged=unchanged,
        ignored_keys=(),
    )


def simulate_active_states(
    current: Mapping[tuple[str, str], RepositoryState],
    desired: Mapping[tuple[str, str], Mapping[str, Any] | None],
) -> dict[tuple[str, str], dict[str, Any]]:
    active = {
        key: dict(state.payload) for key, state in current.items() if not state.retired
    }
    for key, target in desired.items():
        if target is None:
            active.pop(key, None)
        else:
            active[key] = dict(target)
    return active


def validate_simulated_graph(
    active: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    bare = sorted(
        f"{collection}/{object_id}"
        for collection, object_id in active
        if is_bare_relation_key((collection, object_id))
    )
    if bare:
        raise RelationIdIncidentRecoveryError(
            "simulated active graph still contains bare relation IDs: " + ", ".join(bare)
        )
    object_ids = {
        object_id
        for (collection, object_id) in active
        if collection not in RELATION_ID_FIELDS
    }
    unresolved: list[str] = []
    relation_count = 0
    for (collection, object_id), payload in active.items():
        if collection not in RELATION_ID_FIELDS:
            continue
        relation_count += 1
        for endpoint in ("from_id", "to_id"):
            endpoint_id = str(payload.get(endpoint) or "")
            if endpoint_id not in object_ids:
                unresolved.append(f"{collection}/{object_id}.{endpoint}={endpoint_id}")
    if unresolved:
        raise RelationIdIncidentRecoveryError(
            "simulated relation endpoints do not resolve: " + " | ".join(unresolved[:20])
        )
    return {
        "active_object_count": len(active),
        "active_relation_count": relation_count,
        "active_bare_relation_id_count": 0,
        "unresolved_relation_endpoint_count": 0,
    }


def active_dependency_references(
    store: PostgresKnowledgeStore, keys: Iterable[tuple[str, str]]
) -> list[str]:
    """Name current products that a recovery would invalidate."""

    wanted = set(keys)
    if not wanted:
        return []
    findings: list[str] = []
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT object_id, payload FROM wang_knowledge.objects
               WHERE collection='product_dependencies' AND retired_at IS NULL
                 AND COALESCE(payload->>'status','current')='current'"""
        )
        for dependency_id, payload in cursor.fetchall():
            references = {
                (str(row.get("collection") or ""), str(row.get("record_id") or ""))
                for row in (payload.get("dependency_manifest") or [])
            }
            claim_id = str(payload.get("claim_id") or "")
            if claim_id:
                references.add(("claims", claim_id))
            if references & wanted:
                findings.append(str(dependency_id))
    return sorted(findings)


def validate_edge_mirror(
    store: PostgresKnowledgeStore,
    current: Mapping[tuple[str, str], RepositoryState],
    keys: Iterable[tuple[str, str]],
) -> dict[str, Any]:
    """Verify relation objects and their traversal rows describe one state."""

    relation_keys = {
        key for key in keys if key[0] in RELATION_ID_FIELDS and key in current
    }
    if not relation_keys:
        return {"relation_objects_checked": 0, "edge_mismatches": 0}
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT edge_collection, edge_id, revision, payload, retired_at
               FROM wang_knowledge.edges"""
        )
        edges = {
            (str(collection), str(object_id)): {
                "revision": int(revision),
                "payload": dict(payload),
                "retired": retired_at is not None,
            }
            for collection, object_id, revision, payload, retired_at in cursor.fetchall()
        }
    findings: list[str] = []
    for key in sorted(relation_keys):
        state = current[key]
        edge = edges.get(key)
        if edge is None:
            findings.append(f"{key[0]}/{key[1]} has no edge row")
            continue
        if (
            edge["revision"] != state.revision
            or edge["payload"] != state.payload
            or edge["retired"] != state.retired
        ):
            findings.append(f"{key[0]}/{key[1]} edge/object state differs")
    if findings:
        raise RelationIdIncidentRecoveryError(
            "relation edge mirror validation failed: " + " | ".join(findings[:20])
        )
    return {"relation_objects_checked": len(relation_keys), "edge_mismatches": 0}


def validate_desired_current(
    current: Mapping[tuple[str, str], RepositoryState],
    desired: Mapping[tuple[str, str], Mapping[str, Any] | None],
) -> dict[str, Any]:
    findings: list[str] = []
    active = 0
    inactive = 0
    for key, target in desired.items():
        state = current.get(key)
        if target is None:
            inactive += 1
            if state is not None and not state.retired:
                findings.append(f"{key[0]}/{key[1]} should be inactive")
            continue
        active += 1
        expected_sha = record_content_sha(target)
        if state is None or state.retired:
            findings.append(f"{key[0]}/{key[1]} should be active")
        elif state.content_sha256 != expected_sha:
            findings.append(f"{key[0]}/{key[1]} desired content SHA differs")
    if findings:
        raise RelationIdIncidentRecoveryError(
            "desired-state verification failed: " + " | ".join(findings[:20])
        )
    return {
        "desired_active_states_verified": active,
        "desired_inactive_states_verified": inactive,
        "desired_state_mismatches": 0,
    }


def snapshot_roots(values: Sequence[str]) -> dict[str, str]:
    snapshots: dict[str, str] = {}
    for value in values:
        root = expand_path(value)
        if not root.exists():
            raise RelationIdIncidentRecoveryError(f"protected root is missing: {root}")
        paths = (
            [root]
            if root.is_file()
            else sorted(path for path in root.rglob("*") if path.is_file())
        )
        for path in paths:
            snapshots[str(path)] = sha256_file(path)
    return snapshots


def validate_backup_dump(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RelationIdIncidentRecoveryError(f"registry backup is missing or empty: {path}")
    try:
        completed = subprocess.run(
            ["pg_restore", "--list", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RelationIdIncidentRecoveryError(
            f"registry backup failed pg_restore validation: {path}"
        ) from exc
    if not completed.stdout.strip():
        raise RelationIdIncidentRecoveryError(f"registry backup listing is empty: {path}")
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "pg_restore_list_verified": True,
    }


def plan_from_manifest(
    store: PostgresKnowledgeStore, manifest: Mapping[str, Any]
) -> tuple[
    ChangeSetPlan,
    dict[str, Any],
    dict[str, str],
    dict[tuple[str, str], dict[str, Any] | None],
]:
    current = load_repository_states(store)
    change_sets, incident_operations, before_versions = load_incident_ledger(
        store, manifest["incident_change_set_ids"]
    )
    assert_incident_still_current(incident_operations, current)
    authoritative_relations, package_lineage = load_authoritative_relations(
        manifest["authoritative_packages"]
    )
    authority_validation = validate_historical_package_authority(
        store,
        package_lineage,
        incident_started_at=str(change_sets[0]["applied_at"]),
    )
    desired = desired_recovery_states(
        incident_operations, before_versions, current, authoritative_relations
    )
    accounting = recovery_accounting(
        operations=incident_operations,
        before_versions=before_versions,
        current=current,
        authoritative_relations=authoritative_relations,
        desired=desired,
    )
    simulated = simulate_active_states(current, desired)
    graph_validation = validate_simulated_graph(simulated)
    authority = {
        "input_manifest_sha256": manifest["manifest_sha256"],
        "incident_change_sets": change_sets,
        "authoritative_packages": package_lineage,
        "desired_state_sha256": sha256_json(
            {
                f"{collection}/{object_id}": payload
                for (collection, object_id), payload in sorted(desired.items())
            }
        ),
    }
    plan = build_recovery_plan(desired=desired, current=current, authority=authority)
    dependencies = active_dependency_references(
        store, ((row.collection, row.object_id) for row in plan.operations)
    )
    if dependencies:
        raise RelationIdIncidentRecoveryError(
            "recovery would invalidate active product dependencies: " + ", ".join(dependencies)
        )
    edge_validation = validate_edge_mirror(store, current, desired)
    protected = snapshot_roots(manifest.get("protected_roots") or [])
    actual_counts = {
        "incident_change_sets": len(change_sets),
        "incident_operations": len(incident_operations),
        "authoritative_sources": len(package_lineage),
        "authoritative_relations": len(authoritative_relations),
    }
    expected_counts = {
        str(key): int(value)
        for key, value in (manifest.get("expected_counts") or {}).items()
    }
    if expected_counts and expected_counts != actual_counts:
        raise RelationIdIncidentRecoveryError(
            f"recovery count contract differs: expected {expected_counts}, found {actual_counts}"
        )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "planned",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_manifest_path": manifest["manifest_path"],
        "input_manifest_sha256": manifest["manifest_sha256"],
        "incident_change_set_count": len(change_sets),
        "incident_operation_count": len(incident_operations),
        "incident_operation_counts": dict(
            sorted(Counter(row["operation"] for row in incident_operations).items())
        ),
        "incident_current_state_guards_verified": len(incident_operations),
        "incident_before_active_states_verified": len(before_versions),
        "incident_before_state_producer_counts": dict(
            sorted(
                Counter(
                    str(row["producer_operation"])
                    for row in before_versions.values()
                ).items()
            )
        ),
        "authoritative_source_count": len(package_lineage),
        "authoritative_relation_count": len(authoritative_relations),
        "desired_state_key_count": len(desired),
        "change_set": plan.as_dict(),
        "planned_operation_counts": dict(
            sorted(Counter(row.operation for row in plan.operations).items())
        ),
        "graph_validation": graph_validation,
        "edge_validation": edge_validation,
        "historical_package_authority_validation": authority_validation,
        "recovery_accounting": accounting,
        "count_contract": {
            "expected": expected_counts,
            "actual": actual_counts,
            "matches": not expected_counts or expected_counts == actual_counts,
        },
        "active_product_dependency_references": [],
        "protected_file_snapshot": protected,
        "authority": authority,
    }
    return plan, report, protected, desired


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--database-url")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dump", type=Path)
    args = parser.parse_args(argv)

    manifest = load_input_manifest(args.input_manifest)
    store = PostgresKnowledgeStore(args.database_url)
    plan, report, protected_before, desired = plan_from_manifest(store, manifest)
    if not args.apply:
        write_report(args.report, report)
        print(json.dumps({
            "status": "planned",
            "change_set_id": plan.change_set_id,
            "summary": plan.as_dict()["summary"],
            "report": str(args.report),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.backup_dump is None:
        raise RelationIdIncidentRecoveryError("--apply requires --backup-dump")
    backup = validate_backup_dump(args.backup_dump.resolve())
    report = {**report, "backup": backup, "status": "applying"}
    write_report(args.report, report)
    result = store.apply_plan(
        plan,
        metadata={
            "recovery_report": str(args.report.resolve()),
            "recovery_input_manifest_sha256": manifest["manifest_sha256"],
            "backup": backup,
            "authority": report["authority"],
        },
    )
    protected_after = snapshot_roots(manifest.get("protected_roots") or [])
    if protected_after != protected_before:
        raise RelationIdIncidentRecoveryError(
            "protected repository/compiled files changed during database recovery"
        )
    current_after = load_repository_states(store)
    desired_validation = validate_desired_current(current_after, desired)
    graph_after = validate_simulated_graph(
        {
            key: dict(state.payload)
            for key, state in current_after.items()
            if not state.retired
        }
    )
    edge_after = validate_edge_mirror(store, current_after, desired)
    if int((result.get("summary") or {}).get("invalidated_dependencies") or 0) != 0:
        raise RelationIdIncidentRecoveryError(
            "recovery unexpectedly invalidated product dependencies"
        )
    final = {
        **report,
        "status": "applied" if result.get("status") == "applied" else result.get("status"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "protected_file_snapshot_after": protected_after,
        "protected_files_unchanged": True,
        "desired_state_validation_after": desired_validation,
        "graph_validation_after": graph_after,
        "edge_validation_after": edge_after,
    }
    write_report(args.report, final)
    print(json.dumps({
        "status": final["status"],
        "change_set_id": plan.change_set_id,
        "summary": result.get("summary"),
        "report": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
