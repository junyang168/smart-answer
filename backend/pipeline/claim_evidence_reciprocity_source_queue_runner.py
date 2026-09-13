"""Consume sealed #364 source work without bypassing canonical ingest guards.

This module deliberately separates four authorities which must not be blurred:

* the sealed reciprocity queue says *which source work is required*;
* the authority validation authenticates an exact historical replay package;
* the existing extraction supersede planner says *what store mutations result*;
* a dedicated store method must recheck source-generation and current-human
  review CAS under its advisory lock before it delegates to ``apply_plan``.

The pure artifacts below make preview, dispatch, result recording, and retry
deterministic.  This module never performs a naked database write.  A store
without ``apply_claim_evidence_source_queue_plan`` is preview-only and fails
closed when ``apply=True``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    CLAIM_EVIDENCE_HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION,
    CLAIM_EVIDENCE_SOURCE_REPLAY_SOURCE_KIND,
    CLAIM_EVIDENCE_SOURCE_RERUN_SOURCE_KIND,
    REVIEW_EVENT_LEDGER_SNAPSHOT_SCHEMA_VERSION,
    ChangeSetPlan,
    PostgresKnowledgeStore,
    operation_fingerprint_rows,
    record_content_sha,
    review_event_fingerprint_rows,
    sha256_json,
    validate_change_set_plan_integrity,
)
from backend.api.canonical_repository.reviewed_candidate_contract import (
    RESEARCH_BATCH_AGGREGATE,
    SOURCE_SCOPED,
    validate_reviewed_candidate_artifact,
    validate_store_package_authorization,
)
from backend.pipeline.claim_evidence_reciprocity_authority import (
    AUTHORITY_VALIDATION_SCHEMA_VERSION,
    QUEUE_EXACT_REPLAY,
    QUEUE_MANUAL,
    QUEUE_SOURCE_RERUN,
    SOURCE_QUEUE_SCHEMA_VERSION,
    UNRESOLVED_SOURCE_TYPE,
    _derive_effective_package,
    _package_claim_evidence_pairs,
    seal_authority_artifact,
    validate_authority_artifact,
    validate_source_work_queue,
)
from backend.pipeline.claim_evidence_reciprocity_authority_bridge import (
    AUTHORITY_BRIDGE_SCHEMA_VERSION,
    build_authority_bound_audit_and_queue,
    validate_authority_bound_audit_and_queue,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    AUDIT_INPUT_SCHEMA_VERSION,
    freeze_claim_evidence_reciprocity_input,
    validate_sealed_artifact,
)
from backend.pipeline.extraction_supersede_runner import (
    no_op_result,
    plan as extraction_supersede_plan,
)
from backend.pipeline.research_batch_runner import artifact_paths
from backend.pipeline.source_keys import document_row_key


EXECUTION_PLAN_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_source_queue_execution_v1"
)
WORK_UNIT_SCHEMA_VERSION = "wang_claim_evidence_reciprocity_source_work_unit_v1"
RERUN_CANDIDATE_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_source_rerun_candidate_v1"
)
HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION = (
    CLAIM_EVIDENCE_HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION
)
HUMAN_IMPACT_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_human_impact_v1"
)
WORK_READBACK_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_source_work_readback_v1"
)
WORK_RECEIPT_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_source_work_receipt_v1"
)
QUEUE_RESULT_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_source_queue_result_v1"
)
SOURCE_QUEUE_APPLY_AUTHORIZATION_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_source_queue_apply_authorization_v1"
)

WKP364_EXACT_REPLAY_SOURCE_KIND = CLAIM_EVIDENCE_SOURCE_REPLAY_SOURCE_KIND
WKP364_SOURCE_RERUN_SOURCE_KIND = CLAIM_EVIDENCE_SOURCE_RERUN_SOURCE_KIND

WORK_PLANNED = "planned"
WORK_APPLIED = "applied"
WORK_UNCHANGED = "unchanged"
WORK_MANUAL = "manual_required"

REASON_MANUAL_QUEUE = "sealed_queue_requires_manual_adjudication"
REASON_HUMAN_IMPACT = "current_human_authority_would_be_changed"
REASON_SEMANTIC_BLOCKER = "supersede_plan_has_semantic_reference_blockers"
REASON_READBACK = "post_apply_reciprocity_not_verified"

PROTECTED_COLLECTIONS = {"claims", "evidence_steps"}
PROTECTED_HUMAN_STATUSES = {"approved", "human_approved"}


class ClaimEvidenceReciprocitySourceQueueError(ValueError):
    """A queue execution artifact or guarded transition is unsafe."""


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _required_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ClaimEvidenceReciprocitySourceQueueError(f"{field} is required")
    return text


def _required_sha256(value: Any, field: str) -> str:
    text = _required_string(value, field)
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ClaimEvidenceReciprocitySourceQueueError(
            f"{field} must be a lowercase SHA-256 digest"
        )
    return text


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    return seal_authority_artifact(payload)


def _validate_seal(
    artifact: Mapping[str, Any], *, schema_version: str
) -> dict[str, Any]:
    try:
        return validate_authority_artifact(artifact, schema_version=schema_version)
    except ValueError as exc:
        raise ClaimEvidenceReciprocitySourceQueueError(str(exc)) from exc


def _source_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _required_string(row.get("source_type"), "source_type"),
        _required_string(row.get("row_key"), "row_key"),
    )


def _canonical_source_identities(rows: Any) -> list[dict[str, str]]:
    if not isinstance(rows, list) or not rows:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source work requires at least one source identity"
        )
    result: dict[tuple[str, str], dict[str, str]] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"source_identities[{index}] must be an object"
            )
        key = _source_key(raw)
        if key in result:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"source identity repeats {key[0]}/{key[1]}"
            )
        result[key] = {"source_type": key[0], "row_key": key[1]}
    return [result[key] for key in sorted(result)]


def _normalize_source_generation(row: Mapping[str, Any]) -> dict[str, Any]:
    source_type, row_key = _source_key(row)
    try:
        revision = int(row.get("expected_revision"))
    except (TypeError, ValueError):
        raise ClaimEvidenceReciprocitySourceQueueError(
            f"source generation {source_type}/{row_key} has invalid revision"
        ) from None
    if revision < 1:
        raise ClaimEvidenceReciprocitySourceQueueError(
            f"source generation {source_type}/{row_key} has invalid revision"
        )
    return {
        "source_type": source_type,
        "row_key": row_key,
        "active_source_document_id": _required_string(
            row.get("active_source_document_id"), "active_source_document_id"
        ),
        "expected_revision": revision,
        "expected_content_sha256": _required_sha256(
            row.get("expected_content_sha256"), "expected_content_sha256"
        ),
        "source_body_sha256": _required_sha256(
            row.get("source_body_sha256"), "source_body_sha256"
        ),
        "extraction_record_namespace": _required_string(
            row.get("extraction_record_namespace"),
            "extraction_record_namespace",
        ),
    }


def _canonical_source_generations(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source work requires exact SourceDocument generations"
        )
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "source generation must be an object"
            )
        normalized = _normalize_source_generation(raw)
        key = _source_key(normalized)
        if key in result:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"source generation repeats {key[0]}/{key[1]}"
            )
        result[key] = normalized
    return [result[key] for key in sorted(result)]


def _active_source_generations(rows: Sequence[Mapping[str, Any]]) -> dict[
    tuple[str, str], dict[str, Any]
]:
    """Normalize frozen/live SourceDocument rows into physical+semantic CAS."""

    result: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"active_source_documents[{index}] must be an object"
            )
        payload_value = raw.get("payload", raw)
        if not isinstance(payload_value, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"active_source_documents[{index}].payload must be an object"
            )
        payload = _json_copy(payload_value)
        source_type = _required_string(payload.get("source_type"), "source_type")
        row_key = _required_string(document_row_key(payload), "source row_key")
        object_id = _required_string(
            raw.get("object_id") or payload.get("source_id"),
            "SourceDocument object_id",
        )
        if object_id != str(payload.get("source_id") or ""):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"active source {source_type}/{row_key} has inconsistent object ID"
            )
        try:
            revision = int(raw.get("revision"))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"active source {source_type}/{row_key} has invalid revision"
            ) from None
        content_sha = _required_sha256(
            raw.get("content_sha256"), "SourceDocument content_sha256"
        )
        if content_sha != record_content_sha(payload):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"active source {source_type}/{row_key} content SHA does not match"
            )
        body_sha = _required_sha256(
            payload.get("source_body_sha256") or payload.get("source_sha256"),
            "SourceDocument source_body_sha256",
        )
        namespace = _required_string(
            payload.get("extraction_record_namespace"),
            "SourceDocument extraction_record_namespace",
        )
        normalized = _normalize_source_generation(
            {
                "source_type": source_type,
                "row_key": row_key,
                "active_source_document_id": object_id,
                "expected_revision": revision,
                "expected_content_sha256": content_sha,
                "source_body_sha256": body_sha,
                "extraction_record_namespace": namespace,
            }
        )
        key = _source_key(normalized)
        if key in result:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"active source identity repeats {source_type}/{row_key}"
            )
        result[key] = normalized
    return result


def _normalize_rerun_binding(
    value: Mapping[str, Any], *, source: Mapping[str, str]
) -> dict[str, Any]:
    batch_path = Path(_required_string(value.get("batch_path"), "batch_path"))
    output_root = Path(_required_string(value.get("output_root"), "output_root"))
    batch_sha = _required_sha256(value.get("batch_sha256"), "batch_sha256")
    transcript_dirs_value = value.get("transcript_dirs") or []
    if not isinstance(transcript_dirs_value, list):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun transcript_dirs must be a list"
        )
    transcript_dirs = [
        _required_string(path, "rerun transcript_dir")
        for path in transcript_dirs_value
    ]
    extraction_backend = str(value.get("extraction_backend") or "api")
    anthropic_backend = str(value.get("anthropic_backend") or "api")
    if extraction_backend not in {"api", "codex-subscription"}:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "unsupported extraction backend"
        )
    if anthropic_backend not in {"api", "claude-subscription"}:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "unsupported independent-review backend"
        )
    reviewed_path = artifact_paths(output_root, source["row_key"])["reviewed"]
    command = [
        sys.executable,
        "-m",
        "backend.pipeline.research_batch_runner",
        "--batch",
        str(batch_path),
        "--output-root",
        str(output_root),
        "--stage",
        "all",
        "--only",
        source["row_key"],
        "--extraction-backend",
        extraction_backend,
        "--anthropic-backend",
        anthropic_backend,
    ]
    for directory in transcript_dirs:
        command.extend(["--transcript-dir", directory])
    # Deliberately no --apply.  The ordinary batch runner may produce and
    # authenticate the reviewed candidate, but only this queue consumer may
    # later submit it through the two-CAS guarded canonical supersede boundary.
    return {
        "status": "ready",
        "batch_path": str(batch_path),
        "batch_sha256": batch_sha,
        "output_root": str(output_root),
        "reviewed_candidate_path": str(reviewed_path),
        "transcript_dirs": transcript_dirs,
        "extraction_backend": extraction_backend,
        "anthropic_backend": anthropic_backend,
        "command": command,
        "ingest_apply": False,
    }


def _work_unit_id(identity: Mapping[str, Any]) -> str:
    return f"CEQW-{sha256_json(identity)[:32]}"


def _derive_source_queue_execution_plan(
    source_queue: Mapping[str, Any],
    authority_validation: Mapping[str, Any],
    *,
    active_source_documents: Sequence[Mapping[str, Any]],
    rerun_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    authority_bridge: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile every sealed queue task into one indivisible work unit."""

    authority = _validate_seal(
        authority_validation, schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION
    )
    queue = validate_source_work_queue(
        source_queue,
        expected_authority_validation_sha256=str(authority["artifact_sha256"]),
    )
    bridge: dict[str, Any] | None = None
    if authority_bridge is not None:
        bridge = validate_authority_bound_audit_and_queue(authority_bridge)
        if (
            bridge.get("source_work_queue") != queue
            or bridge.get("authority_validation") != authority
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "execution plan inputs differ from its authority bridge"
            )
    normalized_source_documents = _json_copy(list(active_source_documents))
    normalized_rerun_bindings = _json_copy(rerun_bindings or {})
    active = _active_source_generations(normalized_source_documents)
    units: dict[str, dict[str, Any]] = {}
    for raw in authority.get("packages") or []:
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "authority validation package must be an object"
            )
        unit_id = _required_string(raw.get("authority_unit_id"), "authority_unit_id")
        if unit_id in units:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"authority validation repeats {unit_id}"
            )
        units[unit_id] = _json_copy(raw)

    bindings = normalized_rerun_bindings
    work_units: list[dict[str, Any]] = []
    replay_unit_uses: Counter[str] = Counter()
    for task in queue.get("source_tasks") or []:
        if not isinstance(task, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "source queue task must be an object"
            )
        queue_id = _required_string(task.get("queue_id"), "queue_id")
        action = _required_string(task.get("action"), f"{queue_id}.action")
        pair_ids_value = task.get("pair_ids")
        if not isinstance(pair_ids_value, list) or not pair_ids_value:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"source task {queue_id} has no pair IDs"
            )
        pair_ids = [_required_string(row, "pair_id") for row in pair_ids_value]
        if pair_ids != sorted(set(pair_ids)):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"source task {queue_id} pair IDs are not canonical"
            )
        sources = _canonical_source_identities(task.get("source_identities"))
        common = {
            "schema_version": WORK_UNIT_SCHEMA_VERSION,
            "queue_id": queue_id,
            "group_key": _required_string(task.get("group_key"), "group_key"),
            "action": action,
            "pair_ids": pair_ids,
            "source_identities": sources,
            "source_queue_sha256": str(queue["artifact_sha256"]),
            "freeze_artifact_sha256": str(queue["freeze_artifact_sha256"]),
            "audit_artifact_sha256": str(queue["audit_artifact_sha256"]),
            "pair_action_manifest_sha256": str(
                queue["pair_action_manifest_sha256"]
            ),
            "authority_validation_sha256": str(authority["artifact_sha256"]),
        }
        source_generations: list[dict[str, Any]] = []
        package_binding: dict[str, Any] | None = None
        dispatch: dict[str, Any] | None = None
        disposition = "ready"
        if action == QUEUE_EXACT_REPLAY:
            authority_ids = task.get("authority_unit_ids")
            if not isinstance(authority_ids, list) or len(authority_ids) != 1:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"exact replay task {queue_id} requires one authority unit"
                )
            authority_unit_id = _required_string(
                authority_ids[0], "authority_unit_id"
            )
            authority_unit = units.get(authority_unit_id)
            if authority_unit is None or authority_unit.get("replay_eligible") is not True:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"exact replay task {queue_id} lacks eligible authority"
                )
            unit_sources = _canonical_source_identities(
                authority_unit.get("source_identities")
            )
            if sources != unit_sources:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"exact replay task {queue_id} splits or changes its authority sources"
                )
            covered_pairs = {
                f"PAIR-{sha256_json([row['claim_id'], row['evidence_step_id']])[:24]}"
                for row in authority_unit.get("claim_evidence_pairs") or []
                if isinstance(row, Mapping)
            }
            if not set(pair_ids).issubset(covered_pairs):
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"exact replay task {queue_id} exceeds package pair coverage"
                )
            source_generations = _canonical_source_generations(
                authority_unit.get("source_generations")
            )
            observed = [active[_source_key(row)] for row in sources if _source_key(row) in active]
            if observed != source_generations:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"exact replay task {queue_id} SourceDocument generation drifted"
                )
            scope_kind = _required_string(
                authority_unit.get("scope_kind"), "authority scope_kind"
            )
            if scope_kind == SOURCE_SCOPED and len(sources) != 1:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    "source-scoped replay does not name exactly one source"
                )
            if scope_kind == RESEARCH_BATCH_AGGREGATE and len(sources) < 2:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    "aggregate replay must retain the complete multi-source unit"
                )
            if scope_kind not in {SOURCE_SCOPED, RESEARCH_BATCH_AGGREGATE}:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"unsupported replay scope {scope_kind!r}"
                )
            replay_unit_uses[authority_unit_id] += 1
            historical = authority_unit.get("historical_change_set") or {}
            package_binding = {
                "authority_unit_id": authority_unit_id,
                "package_id": _required_string(
                    authority_unit.get("package_id"), "package_id"
                ),
                "scope_kind": scope_kind,
                "path": _required_string(authority_unit.get("path"), "package path"),
                "raw_sha256": _required_sha256(
                    authority_unit.get("raw_sha256"), "raw_sha256"
                ),
                "input_canonical_sha256": _required_sha256(
                    authority_unit.get("input_canonical_sha256"),
                    "input_canonical_sha256",
                ),
                "effective_canonical_sha256": _required_sha256(
                    authority_unit.get("effective_canonical_sha256"),
                    "effective_canonical_sha256",
                ),
                "upstream_reviewed_candidate_artifact_sha256": _required_sha256(
                    authority_unit.get(
                        "upstream_reviewed_candidate_artifact_sha256"
                    ),
                    "upstream reviewed artifact SHA",
                ),
                "effective_reviewed_candidate_artifact_sha256": _required_sha256(
                    authority_unit.get(
                        "effective_reviewed_candidate_artifact_sha256"
                    ),
                    "effective reviewed artifact SHA",
                ),
                "relation_id_namespace_migration": _json_copy(
                    authority_unit.get("relation_id_namespace_migration")
                ),
                "claim_evidence_pairs": _json_copy(
                    authority_unit.get("claim_evidence_pairs") or []
                ),
                "claim_evidence_pairs_sha256": _required_sha256(
                    authority_unit.get("claim_evidence_pairs_sha256"),
                    "claim_evidence_pairs_sha256",
                ),
                "historical_change_set_id": _required_string(
                    historical.get("change_set_id"), "historical change_set_id"
                ),
                "source_kind": _required_string(
                    historical.get("source_kind"), "historical source_kind"
                ),
            }
        elif action == QUEUE_SOURCE_RERUN:
            if len(sources) != 1 or sources[0]["source_type"] == UNRESOLVED_SOURCE_TYPE:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"source rerun task {queue_id} lacks one resolved source"
                )
            generation = active.get(_source_key(sources[0]))
            if generation is None:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"source rerun task {queue_id} lacks a current SourceDocument"
                )
            source_generations = [generation]
            binding = bindings.get(queue_id) or bindings.get(sources[0]["row_key"])
            if binding is None:
                disposition = "dispatch_binding_required"
                dispatch = {
                    "status": disposition,
                    "required_runner": "backend.pipeline.research_batch_runner",
                    "required_member": sources[0]["row_key"],
                    "required_stage": "all",
                    "ingest_apply": False,
                }
            else:
                if not isinstance(binding, Mapping):
                    raise ClaimEvidenceReciprocitySourceQueueError(
                        f"rerun binding for {queue_id} must be an object"
                    )
                dispatch = _normalize_rerun_binding(binding, source=sources[0])
        elif action == QUEUE_MANUAL:
            disposition = WORK_MANUAL
        else:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"source task {queue_id} has unsupported action {action!r}"
            )

        identity = {
            **common,
            "source_generations": source_generations,
            "package_binding": package_binding,
            "dispatch": dispatch,
            "disposition": disposition,
        }
        work = _seal({**identity, "work_unit_id": _work_unit_id(identity)})
        work_units.append(work)

    repeated_replays = sorted(
        unit_id for unit_id, count in replay_unit_uses.items() if count != 1
    )
    if repeated_replays:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "aggregate/reviewed authority units cannot be split across work units: "
            + ", ".join(repeated_replays)
        )
    work_units.sort(key=lambda row: str(row["queue_id"]))
    if len({row["work_unit_id"] for row in work_units}) != len(work_units):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source work unit identities are not unique"
        )
    result = {
        "schema_version": EXECUTION_PLAN_SCHEMA_VERSION,
        "derivation_inputs": {
            "source_queue": queue,
            "authority_validation": authority,
            "active_source_documents": normalized_source_documents,
            "rerun_bindings": normalized_rerun_bindings,
            "authority_bridge": bridge,
        },
        "source_queue_sha256": str(queue["artifact_sha256"]),
        "freeze_artifact_sha256": str(queue["freeze_artifact_sha256"]),
        "audit_artifact_sha256": str(queue["audit_artifact_sha256"]),
        "pair_action_manifest_sha256": str(queue["pair_action_manifest_sha256"]),
        "authority_validation_sha256": str(authority["artifact_sha256"]),
        "work_units": work_units,
        "work_unit_manifest_sha256": sha256_json(
            [row["artifact_sha256"] for row in work_units]
        ),
        "counts": {
            "work_units": len(work_units),
            "exact_replay": sum(
                row["action"] == QUEUE_EXACT_REPLAY for row in work_units
            ),
            "source_rerun": sum(
                row["action"] == QUEUE_SOURCE_RERUN for row in work_units
            ),
            "manual": sum(row["action"] == QUEUE_MANUAL for row in work_units),
            "dispatch_binding_required": sum(
                row["disposition"] == "dispatch_binding_required"
                for row in work_units
            ),
        },
    }
    return _seal(result)


def build_source_queue_execution_plan(
    source_queue: Mapping[str, Any],
    authority_validation: Mapping[str, Any],
    *,
    active_source_documents: Sequence[Mapping[str, Any]],
    rerun_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    authority_bridge: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile and independently rederive a sealed source execution plan."""

    result = _derive_source_queue_execution_plan(
        source_queue,
        authority_validation,
        active_source_documents=active_source_documents,
        rerun_bindings=rerun_bindings,
        authority_bridge=authority_bridge,
    )
    return validate_source_queue_execution_plan(
        result,
        source_queue=source_queue,
        authority_validation=authority_validation,
    )


def validate_source_queue_execution_plan(
    artifact: Mapping[str, Any],
    *,
    source_queue: Mapping[str, Any] | None = None,
    authority_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value = _validate_seal(artifact, schema_version=EXECUTION_PLAN_SCHEMA_VERSION)
    queue_sha = _required_sha256(value.get("source_queue_sha256"), "source_queue_sha256")
    authority_sha = _required_sha256(
        value.get("authority_validation_sha256"), "authority_validation_sha256"
    )
    if source_queue is not None:
        queue = validate_source_work_queue(
            source_queue,
            expected_authority_validation_sha256=authority_sha,
        )
        if queue["artifact_sha256"] != queue_sha:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "execution plan belongs to another source queue"
            )
        for field in (
            "freeze_artifact_sha256",
            "audit_artifact_sha256",
            "pair_action_manifest_sha256",
        ):
            if value.get(field) != queue.get(field):
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"execution plan {field} cross-root does not match queue"
                )
    if authority_validation is not None:
        authority = _validate_seal(
            authority_validation, schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION
        )
        if authority["artifact_sha256"] != authority_sha:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "execution plan belongs to another authority validation"
            )
    work_units = value.get("work_units")
    if not isinstance(work_units, list):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan work_units must be a list"
        )
    ids: list[str] = []
    queue_ids: list[str] = []
    counts = Counter()
    binding_required = 0
    for index, raw in enumerate(work_units):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"work_units[{index}] must be an object"
            )
        work = _validate_seal(raw, schema_version=WORK_UNIT_SCHEMA_VERSION)
        work_identity = _json_copy(work)
        work_identity.pop("artifact_sha256", None)
        claimed_work_id = str(work_identity.pop("work_unit_id", ""))
        if claimed_work_id != _work_unit_id(work_identity):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "work-unit identity does not match its sealed content"
            )
        for field in (
            "source_queue_sha256",
            "freeze_artifact_sha256",
            "audit_artifact_sha256",
            "pair_action_manifest_sha256",
            "authority_validation_sha256",
        ):
            expected = value["source_queue_sha256"] if field == "source_queue_sha256" else value[field]
            if work.get(field) != expected:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    f"work unit {work.get('work_unit_id')} has foreign {field}"
                )
        ids.append(_required_string(work.get("work_unit_id"), "work_unit_id"))
        queue_ids.append(_required_string(work.get("queue_id"), "queue_id"))
        action = _required_string(work.get("action"), "work action")
        if action not in {QUEUE_EXACT_REPLAY, QUEUE_SOURCE_RERUN, QUEUE_MANUAL}:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"work unit has unsupported action {action}"
            )
        counts[action] += 1
        binding_required += work.get("disposition") == "dispatch_binding_required"
        _canonical_source_identities(work.get("source_identities"))
        if action != QUEUE_MANUAL:
            _canonical_source_generations(work.get("source_generations"))
        if action == QUEUE_EXACT_REPLAY and not isinstance(
            work.get("package_binding"), Mapping
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "exact replay work lacks a package binding"
            )
        if action == QUEUE_SOURCE_RERUN:
            dispatch = work.get("dispatch")
            if not isinstance(dispatch, Mapping):
                raise ClaimEvidenceReciprocitySourceQueueError(
                    "source rerun work lacks a dispatch contract"
                )
            command = dispatch.get("command") or []
            if not isinstance(command, list) or "--apply" in command:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    "source rerun dispatch may never invoke batch ingest apply"
                )
    if len(ids) != len(set(ids)) or queue_ids != sorted(set(queue_ids)):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan work units are not in unique canonical order"
        )
    if value.get("work_unit_manifest_sha256") != sha256_json(
        [row["artifact_sha256"] for row in work_units]
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan work-unit manifest differs"
        )
    expected_counts = {
        "work_units": len(work_units),
        "exact_replay": counts[QUEUE_EXACT_REPLAY],
        "source_rerun": counts[QUEUE_SOURCE_RERUN],
        "manual": counts[QUEUE_MANUAL],
        "dispatch_binding_required": binding_required,
    }
    if value.get("counts") != expected_counts:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan counts differ from its work units"
        )
    derivation_inputs = value.get("derivation_inputs")
    if not isinstance(derivation_inputs, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan lacks its deterministic derivation inputs"
        )
    embedded_queue = validate_source_work_queue(
        derivation_inputs.get("source_queue") or {},
        expected_authority_validation_sha256=authority_sha,
    )
    embedded_authority = _validate_seal(
        derivation_inputs.get("authority_validation") or {},
        schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION,
    )
    if (
        embedded_queue["artifact_sha256"] != queue_sha
        or embedded_authority["artifact_sha256"] != authority_sha
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan derivation roots differ"
        )
    if source_queue is not None and embedded_queue != queue:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan derivation uses another source queue"
        )
    if authority_validation is not None and embedded_authority != authority:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan derivation uses another authority validation"
        )
    source_documents = derivation_inputs.get("active_source_documents")
    rerun_bindings = derivation_inputs.get("rerun_bindings")
    if not isinstance(source_documents, list) or not isinstance(
        rerun_bindings, Mapping
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan derivation sources or rerun bindings are malformed"
        )
    bridge_value = derivation_inputs.get("authority_bridge")
    if bridge_value is not None and not isinstance(bridge_value, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan authority bridge is malformed"
        )
    expected_plan = _derive_source_queue_execution_plan(
        embedded_queue,
        embedded_authority,
        active_source_documents=source_documents,
        rerun_bindings=rerun_bindings,
        authority_bridge=bridge_value,
    )
    if value != expected_plan:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan differs from its deterministic source queue derivation"
        )
    return value


def _work_from_plan(
    execution_plan: Mapping[str, Any], work_unit_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = validate_source_queue_execution_plan(execution_plan)
    matches = [
        _validate_seal(row, schema_version=WORK_UNIT_SCHEMA_VERSION)
        for row in plan["work_units"]
        if str(row.get("work_unit_id") or "") == work_unit_id
    ]
    if len(matches) != 1:
        raise ClaimEvidenceReciprocitySourceQueueError(
            f"execution plan does not contain exactly one {work_unit_id}"
        )
    return plan, matches[0]


def _package_source_generations(package: Mapping[str, Any]) -> list[dict[str, str]]:
    extraction = package.get("extraction") or {}
    if not isinstance(extraction, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "reviewed package extraction metadata must be an object"
        )
    result: dict[tuple[str, str], dict[str, str]] = {}
    for raw in package.get("source_documents") or []:
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "reviewed package SourceDocument must be an object"
            )
        source_type = _required_string(raw.get("source_type"), "source_type")
        row_key = _required_string(document_row_key(raw), "source row_key")
        row = {
            "source_type": source_type,
            "row_key": row_key,
            "source_body_sha256": _required_sha256(
                raw.get("source_body_sha256") or raw.get("source_sha256"),
                "package source_body_sha256",
            ),
            "extraction_record_namespace": _required_string(
                raw.get("extraction_record_namespace")
                or extraction.get("record_namespace"),
                "package extraction_record_namespace",
            ),
        }
        key = _source_key(row)
        if key in result:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"reviewed package repeats source {source_type}/{row_key}"
            )
        result[key] = row
    return [result[key] for key in sorted(result)]


def _validate_package_against_work(
    package: Mapping[str, Any], work: Mapping[str, Any]
) -> dict[str, Any]:
    validate_reviewed_candidate_artifact(package)
    effective, migration = _derive_effective_package(package)
    validate_store_package_authorization(effective)
    scope_kind = str(
        (effective.get("consensus_application") or {}).get("scope_kind") or ""
    )
    if work.get("action") == QUEUE_SOURCE_RERUN and scope_kind != SOURCE_SCOPED:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "one-source rerun must produce a source-scoped reviewed candidate"
        )
    pairs = _package_claim_evidence_pairs(effective)
    expected_generations = _canonical_source_generations(
        work.get("source_generations")
    )
    expected_semantic = [
        {
            "source_type": row["source_type"],
            "row_key": row["row_key"],
            "source_body_sha256": row["source_body_sha256"],
            "extraction_record_namespace": row["extraction_record_namespace"],
        }
        for row in expected_generations
    ]
    if _package_source_generations(effective) != expected_semantic:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "reviewed package does not name the work unit's exact source generation"
        )
    if _canonical_source_identities(work.get("source_identities")) != [
        {"source_type": row["source_type"], "row_key": row["row_key"]}
        for row in expected_generations
    ]:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "work-unit source identities differ from its generation CAS"
        )
    return {
        "effective": effective,
        "migration": migration,
        "pairs": pairs,
        "input_canonical_sha256": sha256_json(package),
        "effective_canonical_sha256": sha256_json(effective),
        "upstream_reviewed_candidate_artifact_sha256": str(
            (package.get("consensus_application") or {}).get("artifact_sha256")
            or ""
        ),
        "effective_reviewed_candidate_artifact_sha256": str(
            (effective.get("consensus_application") or {}).get("artifact_sha256")
            or ""
        ),
    }


def load_exact_replay_package(
    work_unit: Mapping[str, Any],
    authority_validation: Mapping[str, Any],
    *,
    read_bytes: Callable[[Path], bytes] | None = None,
) -> dict[str, Any]:
    """Re-authenticate exact bytes at the last boundary before planning."""

    work = _validate_seal(work_unit, schema_version=WORK_UNIT_SCHEMA_VERSION)
    if work.get("action") != QUEUE_EXACT_REPLAY:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "only exact replay work can load a historical package"
        )
    authority = _validate_seal(
        authority_validation, schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION
    )
    if authority["artifact_sha256"] != work.get("authority_validation_sha256"):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "work unit belongs to another authority validation"
        )
    binding = work.get("package_binding")
    if not isinstance(binding, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "exact replay work lacks a package binding"
        )
    authority_unit_id = _required_string(
        binding.get("authority_unit_id"), "authority_unit_id"
    )
    unit_matches = [
        row
        for row in authority.get("packages") or []
        if isinstance(row, Mapping)
        and row.get("authority_unit_id") == authority_unit_id
    ]
    if len(unit_matches) != 1 or unit_matches[0].get("replay_eligible") is not True:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "exact replay authority is missing or no longer eligible"
        )
    unit = unit_matches[0]
    path = Path(_required_string(binding.get("path"), "package path"))
    loader = read_bytes or (lambda candidate: candidate.read_bytes())
    raw = loader(path)
    if _sha256_bytes(raw) != binding.get("raw_sha256"):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "exact replay package raw SHA changed"
        )
    try:
        original = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "exact replay package is not canonical JSON"
        ) from exc
    if not isinstance(original, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "exact replay package JSON must be an object"
        )
    checked = _validate_package_against_work(original, work)
    exact_fields = (
        "input_canonical_sha256",
        "effective_canonical_sha256",
        "upstream_reviewed_candidate_artifact_sha256",
        "effective_reviewed_candidate_artifact_sha256",
    )
    if any(checked[field] != binding.get(field) for field in exact_fields):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "exact replay package canonical/review identity changed"
        )
    if (
        checked["migration"] != binding.get("relation_id_namespace_migration")
        or checked["pairs"] != binding.get("claim_evidence_pairs")
        or sha256_json(checked["pairs"])
        != binding.get("claim_evidence_pairs_sha256")
        or str(checked["effective"].get("package_id") or "")
        != binding.get("package_id")
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "exact replay package migration, pair coverage, or package ID changed"
        )
    # Requiring the binding to be byte-for-byte derivable from the sealed
    # validation prevents a caller from retaining its SHA while substituting a
    # different path, scope, or current SourceDocument generation.
    comparable = {
        key: unit.get(key)
        for key in (
            "authority_unit_id",
            "package_id",
            "scope_kind",
            "path",
            "raw_sha256",
            "input_canonical_sha256",
            "effective_canonical_sha256",
            "upstream_reviewed_candidate_artifact_sha256",
            "effective_reviewed_candidate_artifact_sha256",
            "relation_id_namespace_migration",
            "claim_evidence_pairs",
            "claim_evidence_pairs_sha256",
        )
    }
    historical = unit.get("historical_change_set") or {}
    comparable.update(
        {
            "historical_change_set_id": historical.get("change_set_id"),
            "source_kind": historical.get("source_kind"),
        }
    )
    if _json_copy(binding) != _json_copy(comparable):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "exact replay package binding differs from authority validation"
        )
    return checked["effective"]


def record_source_rerun_candidate(
    execution_plan: Mapping[str, Any],
    work_unit_id: str,
    *,
    run_manifest_path: Path,
    read_bytes: Callable[[Path], bytes] | None = None,
) -> dict[str, Any]:
    """Authenticate one governed rerun's reviewed candidate without ingesting it."""

    plan, work = _work_from_plan(execution_plan, work_unit_id)
    if work.get("action") != QUEUE_SOURCE_RERUN:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "only source-rerun work can record a rerun candidate"
        )
    dispatch = work.get("dispatch")
    if not isinstance(dispatch, Mapping) or dispatch.get("status") != "ready":
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source rerun requires a sealed dispatch binding"
        )
    loader = read_bytes or (lambda path: path.read_bytes())
    batch_path = Path(str(dispatch["batch_path"]))
    if _sha256_bytes(loader(batch_path)) != dispatch.get("batch_sha256"):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "research batch bytes changed after queue planning"
        )
    manifest_raw = loader(run_manifest_path)
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "research batch run manifest is invalid JSON"
        ) from exc
    if not isinstance(manifest, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "research batch run manifest must be an object"
        )
    member = work["source_identities"][0]["row_key"]
    expected_manifest_path = Path(str(dispatch["output_root"])) / "run-manifest.json"
    if run_manifest_path != expected_manifest_path:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "research batch run manifest path differs from sealed dispatch"
        )
    members = manifest.get("members")
    matching_members = [
        row
        for row in members or []
        if isinstance(row, Mapping) and str(row.get("source") or "") == member
    ]
    if (
        not isinstance(members, list)
        or len(matching_members) != 1
        or len(members) != 1
        or matching_members[0].get("status") != "completed"
        or manifest.get("status") not in {"completed", "partial_selection"}
        or manifest.get("selected_stage") != "all"
        or manifest.get("ingest_applies") is not False
        or str(manifest.get("output_root") or "") != dispatch.get("output_root")
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "research batch did not complete the exact non-ingesting source dispatch"
        )
    candidate_path = Path(str(dispatch["reviewed_candidate_path"]))
    candidate_raw = loader(candidate_path)
    try:
        package = json.loads(candidate_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun reviewed candidate is invalid JSON"
        ) from exc
    if not isinstance(package, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun reviewed candidate must be an object"
        )
    checked = _validate_package_against_work(package, work)
    result = {
        "schema_version": RERUN_CANDIDATE_SCHEMA_VERSION,
        "execution_plan_sha256": str(plan["artifact_sha256"]),
        "source_queue_sha256": str(plan["source_queue_sha256"]),
        "work_unit_id": str(work["work_unit_id"]),
        "work_unit_sha256": str(work["artifact_sha256"]),
        "run_manifest_path": str(run_manifest_path),
        "run_manifest_raw_sha256": _sha256_bytes(manifest_raw),
        "run_manifest_canonical_sha256": sha256_json(manifest),
        "reviewed_candidate_path": str(candidate_path),
        "reviewed_candidate_raw_sha256": _sha256_bytes(candidate_raw),
        "input_canonical_sha256": checked["input_canonical_sha256"],
        "effective_canonical_sha256": checked["effective_canonical_sha256"],
        "upstream_reviewed_candidate_artifact_sha256": checked[
            "upstream_reviewed_candidate_artifact_sha256"
        ],
        "effective_reviewed_candidate_artifact_sha256": checked[
            "effective_reviewed_candidate_artifact_sha256"
        ],
        "relation_id_namespace_migration": checked["migration"],
        "source_generations_sha256": sha256_json(work["source_generations"]),
        "status": "candidate_ready",
    }
    return _seal(result)


def _load_rerun_candidate(
    work: Mapping[str, Any],
    execution_plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    read_bytes: Callable[[Path], bytes] | None = None,
) -> dict[str, Any]:
    candidate = _validate_seal(
        receipt, schema_version=RERUN_CANDIDATE_SCHEMA_VERSION
    )
    if (
        candidate.get("execution_plan_sha256")
        != execution_plan.get("artifact_sha256")
        or candidate.get("source_queue_sha256")
        != execution_plan.get("source_queue_sha256")
        or candidate.get("work_unit_id") != work.get("work_unit_id")
        or candidate.get("work_unit_sha256") != work.get("artifact_sha256")
        or candidate.get("source_generations_sha256")
        != sha256_json(work.get("source_generations"))
        or candidate.get("status") != "candidate_ready"
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun candidate receipt belongs to another queue/work/source generation"
        )
    path = Path(_required_string(
        candidate.get("reviewed_candidate_path"), "reviewed_candidate_path"
    ))
    dispatch = work.get("dispatch") or {}
    if str(path) != dispatch.get("reviewed_candidate_path"):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun candidate path differs from sealed dispatch"
        )
    loader = read_bytes or (lambda candidate_path: candidate_path.read_bytes())
    raw = loader(path)
    if _sha256_bytes(raw) != candidate.get("reviewed_candidate_raw_sha256"):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun reviewed candidate bytes changed"
        )
    try:
        package = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun reviewed candidate is invalid JSON"
        ) from exc
    if not isinstance(package, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun reviewed candidate must be an object"
        )
    checked = _validate_package_against_work(package, work)
    exact = (
        "input_canonical_sha256",
        "effective_canonical_sha256",
        "upstream_reviewed_candidate_artifact_sha256",
        "effective_reviewed_candidate_artifact_sha256",
    )
    if any(checked[field] != candidate.get(field) for field in exact):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun candidate canonical/review identity changed"
        )
    if checked["migration"] != candidate.get("relation_id_namespace_migration"):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "rerun candidate relation-ID migration changed"
        )
    return checked["effective"]


def build_current_human_authority_snapshot(
    records: Sequence[Mapping[str, Any]],
    *,
    review_event_ledger_count: int,
    review_event_ledger_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal all current-head human Claim/Evidence rulings for apply-time CAS.

    ``records`` must be the full current object head set for Claim and
    EvidenceStep, including retired heads.  An approved-looking payload without
    one current human event bound to its producer ChangeSet is ambiguous and
    blocks snapshot construction instead of being silently treated as AI data.
    """

    try:
        ledger_count = int(review_event_ledger_count)
    except (TypeError, ValueError):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "review_event_ledger_count must be an integer"
        ) from None
    if ledger_count < 0:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "review_event_ledger_count must be non-negative"
        )
    protected: list[dict[str, Any]] = []
    scanned_heads: list[dict[str, Any]] = []
    keys: set[tuple[str, str]] = set()
    scanned = 0
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"human authority records[{index}] must be an object"
            )
        collection = _required_string(raw.get("collection"), "record collection")
        if collection not in PROTECTED_COLLECTIONS:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "human authority snapshot accepts only Claim/Evidence heads"
            )
        object_id = _required_string(raw.get("object_id"), "record object_id")
        key = (collection, object_id)
        if key in keys:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"human authority snapshot repeats {collection}/{object_id}"
            )
        keys.add(key)
        scanned += 1
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"{collection}/{object_id} payload must be an object"
            )
        try:
            revision = int(raw.get("revision"))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"{collection}/{object_id} revision is invalid"
            ) from None
        content_sha = _required_sha256(
            raw.get("content_sha256"), f"{collection}/{object_id} content_sha256"
        )
        if revision < 1 or content_sha != record_content_sha(payload):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"{collection}/{object_id} revision/content SHA is invalid"
            )
        object_version = raw.get("object_version") or {}
        producer_id = _required_string(
            raw.get("producer_change_set_id")
            or (object_version.get("change_set_id") if isinstance(object_version, Mapping) else None),
            f"{collection}/{object_id} producer_change_set_id",
        )
        retired = bool(raw.get("retired") or raw.get("retired_at"))
        scanned_heads.append(
            {
                "collection": collection,
                "object_id": object_id,
                "revision": revision,
                "content_sha256": content_sha,
                "retired": retired,
                "producer_change_set_id": producer_id,
            }
        )
        events_value = raw.get("review_events") or raw.get("current_review_events") or []
        if not isinstance(events_value, list):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"{collection}/{object_id} review_events must be a list"
            )
        current_events = [
            event
            for event in events_value
            if isinstance(event, Mapping)
            and int(event.get("object_revision") or 0) == revision
        ]
        current_human = [
            event
            for event in current_events
            if str(event.get("reviewer_kind") or "") == "human"
        ]
        status = str(payload.get("review_status") or "candidate")
        looks_human = status in PROTECTED_HUMAN_STATUSES or bool(current_human)
        if not looks_human:
            continue
        if len(current_events) != 1 or len(current_human) != 1:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"{collection}/{object_id} has ambiguous current human authority"
            )
        event = current_human[0]
        decision = _required_string(
            event.get("decision"), f"{collection}/{object_id} human decision"
        )
        artifact = event.get("artifact")
        if (
            not isinstance(artifact, Mapping)
            or str(artifact.get("change_set_id") or "") != producer_id
            or (status in PROTECTED_HUMAN_STATUSES and decision != status)
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"{collection}/{object_id} human event is not bound to its current producer"
            )
        projection_field = (
            "evidence_step_ids" if collection == "claims" else "produced_claim_ids"
        )
        projection_value = payload.get(projection_field) or []
        if not isinstance(projection_value, list):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"{collection}/{object_id}.{projection_field} must be a list"
            )
        protected.append(
            {
                "collection": collection,
                "object_id": object_id,
                "revision": revision,
                "content_sha256": content_sha,
                "retired": retired,
                "review_status": status,
                "human_decision": decision,
                "reviewed_revision": revision,
                "producer_change_set_id": producer_id,
                "head_producer_change_set_id": producer_id,
                "review_event_id": _required_string(
                    event.get("review_event_id"), "review_event_id"
                ),
                "review_event_sha256": sha256_json(event),
                "projection_field": projection_field,
                "projection_ids": _json_copy(projection_value),
            }
        )
    protected.sort(key=lambda row: (row["collection"], row["object_id"]))
    scanned_heads.sort(key=lambda row: (row["collection"], row["object_id"]))
    if ledger_count < len(protected):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "review-event ledger count is smaller than protected human records"
        )
    result = {
        "schema_version": HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION,
        "scan_scope": "all_current_object_heads_claims_and_evidence_steps",
        "scanned_record_count": scanned,
        "scanned_heads": scanned_heads,
        "scanned_heads_sha256": sha256_json(scanned_heads),
        "review_event_ledger_count": ledger_count,
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
    if review_event_ledger_snapshot is not None:
        result["review_event_ledger_snapshot"] = _json_copy(
            review_event_ledger_snapshot
        )
    return _seal(result)


def validate_current_human_authority_snapshot(
    artifact: Mapping[str, Any],
    *,
    require_store_roots: bool = False,
) -> dict[str, Any]:
    value = _validate_seal(
        artifact, schema_version=HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION
    )
    records = value.get("protected_records")
    if value.get("scan_scope") != (
        "all_current_object_heads_claims_and_evidence_steps"
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "human authority snapshot scan scope is incomplete"
        )
    ledger_count = value.get("review_event_ledger_count")
    if not isinstance(ledger_count, int) or ledger_count < 0:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "human authority snapshot review-event count is invalid"
        )
    if not isinstance(records, list):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "human authority snapshot protected_records must be a list"
        )
    ordered = sorted(
        (_json_copy(row) for row in records),
        key=lambda row: (str(row.get("collection") or ""), str(row.get("object_id") or "")),
    )
    if records != ordered or value.get("protected_records_sha256") != sha256_json(records):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "human authority snapshot record manifest differs"
        )
    keys = [
        (
            _required_string(row.get("collection"), "protected collection"),
            _required_string(row.get("object_id"), "protected object_id"),
        )
        for row in records
        if isinstance(row, Mapping)
    ]
    if len(keys) != len(records) or len(keys) != len(set(keys)):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "human authority snapshot records are malformed or repeated"
        )
    for row in records:
        if (
            row.get("collection") not in PROTECTED_COLLECTIONS
            or not isinstance(row.get("revision"), int)
            or int(row["revision"]) < 1
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(row.get("content_sha256") or "")
            )
            or not str(row.get("head_producer_change_set_id") or "")
            or not str(row.get("review_event_id") or "")
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "human authority snapshot has an invalid protected record"
            )
    expected_counts = {
        "protected_records": len(records),
        "claims": sum(row["collection"] == "claims" for row in records),
        "evidence_steps": sum(
            row["collection"] == "evidence_steps" for row in records
        ),
    }
    if value.get("counts") != expected_counts:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "human authority snapshot counts differ"
        )
    heads = value.get("scanned_heads")
    if not isinstance(heads, list):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "human authority snapshot lacks its current-head denominator"
        )
    ordered_heads = sorted(
        (_json_copy(row) for row in heads),
        key=lambda row: (
            str(row.get("collection") or ""),
            str(row.get("object_id") or ""),
        ),
    )
    head_keys = [
        (
            _required_string(row.get("collection"), "head collection"),
            _required_string(row.get("object_id"), "head object_id"),
        )
        for row in heads
        if isinstance(row, Mapping)
    ]
    if (
        len(head_keys) != len(heads)
        or len(head_keys) != len(set(head_keys))
        or heads != ordered_heads
        or value.get("scanned_record_count") != len(heads)
        or value.get("scanned_heads_sha256") != sha256_json(heads)
        or not set(keys).issubset(set(head_keys))
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "human authority snapshot current-head manifest differs"
        )
    for row in heads:
        if (
            row.get("collection") not in PROTECTED_COLLECTIONS
            or not isinstance(row.get("revision"), int)
            or int(row["revision"]) < 1
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(row.get("content_sha256") or "")
            )
            or not str(row.get("producer_change_set_id") or "")
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "human authority snapshot has an invalid current head"
            )
    ledger = value.get("review_event_ledger_snapshot")
    if ledger is None and require_store_roots:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "guarded apply requires the full store-owned review-event ledger root"
        )
    if ledger is not None:
        if not isinstance(ledger, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "review-event ledger snapshot must be an object"
            )
        canonical_ledger = _json_copy(ledger)
        if (
            canonical_ledger.get("schema_version")
            != REVIEW_EVENT_LEDGER_SNAPSHOT_SCHEMA_VERSION
            or not isinstance(canonical_ledger.get("count"), int)
            or int(canonical_ledger["count"]) < 0
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(canonical_ledger.get("rows_sha256") or ""),
            )
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "review-event ledger snapshot is invalid"
            )
        ledger_without_seal = dict(canonical_ledger)
        ledger_seal = str(ledger_without_seal.pop("snapshot_sha256", ""))
        if (
            ledger_seal != sha256_json(ledger_without_seal)
            or value.get("review_event_ledger_count")
            != canonical_ledger.get("count")
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "review-event ledger snapshot seal/count differs"
            )
    return value


def _change_set_identity(change_set: ChangeSetPlan) -> dict[str, Any]:
    validate_change_set_plan_integrity(change_set)
    return {
        "change_set_id": change_set.change_set_id,
        "fingerprint_sha256": change_set.fingerprint_sha256,
        "package_id": change_set.package_id,
        "source_kind": change_set.source_kind,
        "source_sha256": change_set.source_sha256,
        "operations_sha256": sha256_json(
            operation_fingerprint_rows(change_set.operations)
        ),
        "review_events_sha256": sha256_json(
            review_event_fingerprint_rows(change_set.review_events)
        ),
    }


def assess_current_human_authority_impact(
    change_set: ChangeSetPlan,
    human_authority_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed on any lower-authority mutation of a current human ruling."""

    snapshot = validate_current_human_authority_snapshot(
        human_authority_snapshot
    )
    identity = _change_set_identity(change_set)
    protected = {
        (row["collection"], row["object_id"]): row
        for row in snapshot["protected_records"]
    }
    creates_by_collection: dict[str, list[str]] = {
        collection: sorted(
            operation.object_id
            for operation in change_set.operations
            if operation.collection == collection and operation.operation == "create"
        )
        for collection in PROTECTED_COLLECTIONS
    }
    blockers: list[dict[str, Any]] = []
    for operation in change_set.operations:
        if operation.collection not in PROTECTED_COLLECTIONS:
            continue
        protected_row = protected.get((operation.collection, operation.object_id))
        if protected_row is None:
            continue
        projection_field = str(protected_row["projection_field"])
        before_projection = _json_copy(protected_row["projection_ids"])
        after_projection = (
            _json_copy(operation.payload.get(projection_field) or [])
            if operation.operation in {"create", "update", "revive"}
            else None
        )
        reason = "human_settled_record_mutation"
        if operation.operation == "retire" and creates_by_collection[operation.collection]:
            reason = "human_settled_record_id_replacement"
        elif (
            operation.collection == "claims"
            and operation.operation == "update"
            and after_projection != before_projection
        ):
            reason = "human_settled_claim_evidence_projection_change"
        blockers.append(
            {
                "reason_code": reason,
                "operation": operation.operation,
                "collection": operation.collection,
                "object_id": operation.object_id,
                "before_revision": operation.before_revision,
                "after_revision": operation.after_revision,
                "review_status": protected_row["review_status"],
                "review_event_id": protected_row["review_event_id"],
                "projection_field": projection_field,
                "before_projection_ids": before_projection,
                "after_projection_ids": after_projection,
                "replacement_create_ids": (
                    creates_by_collection[operation.collection]
                    if operation.operation == "retire"
                    else []
                ),
                "required_action": QUEUE_MANUAL,
            }
        )
    blockers.sort(key=lambda row: (row["collection"], row["object_id"], row["operation"]))
    return _seal(
        {
            "schema_version": HUMAN_IMPACT_SCHEMA_VERSION,
            "status": "blocked" if blockers else "safe",
            "change_set_identity": identity,
            "human_authority_snapshot_sha256": str(snapshot["artifact_sha256"]),
            "blockers": blockers,
            "blockers_sha256": sha256_json(blockers),
            "counts": {"blockers": len(blockers)},
        }
    )


def _frozen_active_claim_evidence_heads(
    frozen_input: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records = frozen_input.get("active_records")
    if not isinstance(records, list):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "frozen input lacks its active Claim/Evidence records"
        )
    heads: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"frozen active_records[{index}] must be an object"
            )
        collection = _required_string(
            raw.get("collection"), "frozen record collection"
        )
        object_id = _required_string(raw.get("object_id"), "frozen record object_id")
        key = (collection, object_id)
        object_version = raw.get("object_version")
        if (
            collection not in PROTECTED_COLLECTIONS
            or key in seen
            or not isinstance(object_version, Mapping)
            or bool(raw.get("retired") or raw.get("retired_at"))
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "frozen input has an invalid active Claim/Evidence head"
            )
        seen.add(key)
        try:
            revision = int(raw.get("revision"))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"frozen head {collection}/{object_id} has invalid revision"
            ) from None
        heads.append(
            {
                "collection": collection,
                "object_id": object_id,
                "revision": revision,
                "content_sha256": _required_sha256(
                    raw.get("content_sha256"),
                    f"frozen head {collection}/{object_id} content_sha256",
                ),
                "retired": False,
                "producer_change_set_id": _required_string(
                    object_version.get("change_set_id"),
                    f"frozen head {collection}/{object_id} producer ChangeSet",
                ),
            }
        )
    heads.sort(key=lambda row: (row["collection"], row["object_id"]))
    return heads


def _assert_source_queue_authorization_cross_roots(
    *,
    frozen_input: Mapping[str, Any],
    work: Mapping[str, Any],
    expected_source_generations: Sequence[Mapping[str, Any]],
    human_authority_snapshot: Mapping[str, Any],
) -> None:
    frozen_heads = _frozen_active_claim_evidence_heads(frozen_input)
    active_human_heads = [
        _json_copy(row)
        for row in human_authority_snapshot["scanned_heads"]
        if row.get("retired") is False
    ]
    if frozen_heads != active_human_heads:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "frozen active Claim/Evidence heads differ from the human-authority scan"
        )

    frozen_sources = _active_source_generations(
        frozen_input.get("active_source_documents") or []
    )
    expected = _canonical_source_generations(list(expected_source_generations))
    work_generations = _canonical_source_generations(work.get("source_generations"))
    source_identities = _canonical_source_identities(work.get("source_identities"))
    derived: list[dict[str, Any]] = []
    for identity in source_identities:
        generation = frozen_sources.get(_source_key(identity))
        if generation is None:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "source work generation is absent from the frozen SourceDocuments"
            )
        derived.append(generation)
    if expected != work_generations or expected != derived:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source work generations differ from the frozen SourceDocuments"
        )


def _derive_source_queue_apply_authorization(
    execution_plan: Mapping[str, Any],
    work_unit_id: str,
    *,
    frozen_input: Mapping[str, Any],
    authority_bridge: Mapping[str, Any],
    source_queue: Mapping[str, Any],
    authority_validation: Mapping[str, Any],
    effective_package: Mapping[str, Any],
    package_proof: Mapping[str, Any],
    source_candidate_receipt: Mapping[str, Any] | None,
    expected_source_generations: Sequence[Mapping[str, Any]],
    expected_human_authority_snapshot: Mapping[str, Any],
    human_impact: Mapping[str, Any],
    change_set: ChangeSetPlan,
) -> dict[str, Any]:
    validate_change_set_plan_integrity(change_set)
    frozen = validate_sealed_artifact(
        frozen_input,
        expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION,
    )
    bridge = validate_authority_bound_audit_and_queue(
        authority_bridge,
        frozen_input=frozen,
    )
    authority = _validate_seal(
        authority_validation,
        schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION,
    )
    queue = validate_source_work_queue(
        source_queue,
        expected_freeze_artifact_sha256=str(frozen["artifact_sha256"]),
        expected_audit_artifact_sha256=str(bridge["audit"]["artifact_sha256"]),
        expected_pair_action_manifest_sha256=str(
            bridge["roots"]["pair_actions_sha256"]
        ),
        expected_authority_validation_sha256=str(authority["artifact_sha256"]),
    )
    if (
        bridge.get("source_work_queue") != queue
        or bridge.get("authority_validation") != authority
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source apply authorization differs from its authority bridge"
        )
    plan, work = _work_from_plan(execution_plan, work_unit_id)
    derivation_inputs = plan.get("derivation_inputs") or {}
    if (
        not isinstance(derivation_inputs, Mapping)
        or derivation_inputs.get("authority_bridge") != bridge
        or derivation_inputs.get("source_queue") != queue
        or derivation_inputs.get("authority_validation") != authority
        or plan.get("freeze_artifact_sha256") != frozen.get("artifact_sha256")
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source execution plan lacks its exact bridge/freeze provenance"
        )

    expected_generations = _canonical_source_generations(
        list(expected_source_generations)
    )
    human_snapshot = validate_current_human_authority_snapshot(
        expected_human_authority_snapshot,
        require_store_roots=True,
    )
    _assert_source_queue_authorization_cross_roots(
        frozen_input=frozen,
        work=work,
        expected_source_generations=expected_generations,
        human_authority_snapshot=human_snapshot,
    )
    checked_package = _validate_package_against_work(effective_package, work)
    package = _json_copy(effective_package)
    if checked_package["effective"] != package:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source authorization requires the effective reviewed package"
        )
    if (
        change_set.source_sha256 != sha256_json(package)
        or change_set.package_id != str(package.get("package_id") or "")
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source authorization package differs from its ChangeSet"
        )

    candidate: dict[str, Any] | None = None
    if work["action"] == QUEUE_EXACT_REPLAY:
        expected_source_kind = WKP364_EXACT_REPLAY_SOURCE_KIND
        if source_candidate_receipt is not None:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "exact replay authorization may not carry a rerun candidate"
            )
        if load_exact_replay_package(work, authority) != package:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "exact replay authorization package differs from reviewed bytes"
            )
        expected_proof = {
            "kind": "historical_exact_replay",
            "authority_unit_id": work["package_binding"]["authority_unit_id"],
            "effective_canonical_sha256": work["package_binding"][
                "effective_canonical_sha256"
            ],
            "historical_source_kind": work["package_binding"]["source_kind"],
            "guarded_apply_source_kind": expected_source_kind,
        }
    elif work["action"] == QUEUE_SOURCE_RERUN:
        expected_source_kind = WKP364_SOURCE_RERUN_SOURCE_KIND
        if source_candidate_receipt is None:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "source rerun authorization requires its candidate receipt"
            )
        candidate = _validate_seal(
            source_candidate_receipt,
            schema_version=RERUN_CANDIDATE_SCHEMA_VERSION,
        )
        if _load_rerun_candidate(work, plan, candidate) != package:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "source rerun authorization package differs from its candidate"
            )
        expected_proof = {
            "kind": "governed_source_rerun",
            "rerun_candidate_receipt_sha256": candidate["artifact_sha256"],
            "effective_canonical_sha256": sha256_json(package),
            "guarded_apply_source_kind": expected_source_kind,
        }
    else:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "manual source work cannot authorize a canonical apply"
        )
    if change_set.source_kind != expected_source_kind or dict(package_proof) != expected_proof:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source authorization action or package proof differs"
        )

    impact = assess_current_human_authority_impact(change_set, human_snapshot)
    checked_impact = _validate_seal(
        human_impact,
        schema_version=HUMAN_IMPACT_SCHEMA_VERSION,
    )
    if impact != checked_impact or impact.get("status") != "safe":
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source authorization human-impact proof differs or is blocked"
        )
    identity = _change_set_identity(change_set)
    operation_fingerprints = operation_fingerprint_rows(change_set.operations)
    review_event_fingerprints = review_event_fingerprint_rows(
        change_set.review_events
    )
    return _seal(
        {
            "schema_version": SOURCE_QUEUE_APPLY_AUTHORIZATION_SCHEMA_VERSION,
            "frozen_input": frozen,
            "authority_bridge": bridge,
            "source_queue": queue,
            "authority_validation": authority,
            "execution_plan": plan,
            "work_unit_id": str(work["work_unit_id"]),
            "work_unit_sha256": str(work["artifact_sha256"]),
            "effective_package": package,
            "package_proof": _json_copy(expected_proof),
            "source_candidate_receipt": candidate,
            "expected_source_generations": expected_generations,
            "expected_human_authority_snapshot": human_snapshot,
            "human_impact": impact,
            "change_set_identity": identity,
            "operation_fingerprints": operation_fingerprints,
            "review_event_fingerprints": review_event_fingerprints,
        }
    )


def build_source_queue_apply_authorization(
    execution_plan: Mapping[str, Any],
    work_unit_id: str,
    *,
    frozen_input: Mapping[str, Any],
    authority_bridge: Mapping[str, Any],
    source_queue: Mapping[str, Any],
    authority_validation: Mapping[str, Any],
    effective_package: Mapping[str, Any],
    package_proof: Mapping[str, Any],
    source_candidate_receipt: Mapping[str, Any] | None,
    expected_source_generations: Sequence[Mapping[str, Any]],
    expected_human_authority_snapshot: Mapping[str, Any],
    human_impact: Mapping[str, Any],
    change_set: ChangeSetPlan,
) -> dict[str, Any]:
    """Seal every derivation needed by the dedicated store authorization gate."""

    result = _derive_source_queue_apply_authorization(
        execution_plan,
        work_unit_id,
        frozen_input=frozen_input,
        authority_bridge=authority_bridge,
        source_queue=source_queue,
        authority_validation=authority_validation,
        effective_package=effective_package,
        package_proof=package_proof,
        source_candidate_receipt=source_candidate_receipt,
        expected_source_generations=expected_source_generations,
        expected_human_authority_snapshot=expected_human_authority_snapshot,
        human_impact=human_impact,
        change_set=change_set,
    )
    return validate_source_queue_apply_authorization(result, plan=change_set)


def validate_source_queue_apply_authorization(
    artifact: Mapping[str, Any],
    *,
    plan: ChangeSetPlan,
) -> dict[str, Any]:
    """Rederive a source apply authorization; its SHA fields are never opaque."""

    value = _validate_seal(
        artifact,
        schema_version=SOURCE_QUEUE_APPLY_AUTHORIZATION_SCHEMA_VERSION,
    )
    execution_plan = value.get("execution_plan")
    frozen_input = value.get("frozen_input")
    authority_bridge = value.get("authority_bridge")
    source_queue = value.get("source_queue")
    authority_validation = value.get("authority_validation")
    effective_package = value.get("effective_package")
    package_proof = value.get("package_proof")
    expected_generations = value.get("expected_source_generations")
    human_snapshot = value.get("expected_human_authority_snapshot")
    human_impact = value.get("human_impact")
    if not all(
        isinstance(item, Mapping)
        for item in (
            execution_plan,
            frozen_input,
            authority_bridge,
            source_queue,
            authority_validation,
            effective_package,
            package_proof,
            human_snapshot,
            human_impact,
        )
    ) or not isinstance(expected_generations, list):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source apply authorization has malformed derivation inputs"
        )
    candidate = value.get("source_candidate_receipt")
    if candidate is not None and not isinstance(candidate, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source apply authorization candidate receipt is malformed"
        )
    expected = _derive_source_queue_apply_authorization(
        execution_plan,
        _required_string(value.get("work_unit_id"), "work_unit_id"),
        frozen_input=frozen_input,
        authority_bridge=authority_bridge,
        source_queue=source_queue,
        authority_validation=authority_validation,
        effective_package=effective_package,
        package_proof=package_proof,
        source_candidate_receipt=candidate,
        expected_source_generations=expected_generations,
        expected_human_authority_snapshot=human_snapshot,
        human_impact=human_impact,
        change_set=plan,
    )
    if value != expected:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source apply authorization differs from its deterministic derivation"
        )
    return value


def _observed_source_generations(
    store: Any, expected: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    reader = getattr(store, "read_claim_evidence_source_generations", None)
    if not callable(reader):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "store lacks read_claim_evidence_source_generations"
        )
    observed = reader(_json_copy(expected))
    if not isinstance(observed, Sequence):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "store source-generation reader returned a malformed value"
        )
    return _canonical_source_generations(observed)


def _receipt(
    plan: Mapping[str, Any],
    work: Mapping[str, Any],
    *,
    status: str,
    completion_verified: bool,
    reason_code: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": WORK_RECEIPT_SCHEMA_VERSION,
        "execution_plan_sha256": str(plan["artifact_sha256"]),
        "source_queue_sha256": str(plan["source_queue_sha256"]),
        "authority_validation_sha256": str(
            plan["authority_validation_sha256"]
        ),
        "work_unit_id": str(work["work_unit_id"]),
        "work_unit_sha256": str(work["artifact_sha256"]),
        "queue_id": str(work["queue_id"]),
        "action": str(work["action"]),
        "pair_ids": _json_copy(work["pair_ids"]),
        "source_generations_sha256": sha256_json(work.get("source_generations") or []),
        "status": status,
        "completion_verified": completion_verified,
        "reason_code": reason_code,
        "details": _json_copy(details or {}),
    }
    return _seal(payload)


def validate_source_work_receipt(
    receipt: Mapping[str, Any],
    *,
    execution_plan: Mapping[str, Any],
) -> dict[str, Any]:
    plan = validate_source_queue_execution_plan(execution_plan)
    value = _validate_seal(receipt, schema_version=WORK_RECEIPT_SCHEMA_VERSION)
    matches = [
        row
        for row in plan["work_units"]
        if row.get("work_unit_id") == value.get("work_unit_id")
    ]
    if len(matches) != 1:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "receipt names a work unit outside its execution plan"
        )
    work = matches[0]
    expected = {
        "execution_plan_sha256": plan["artifact_sha256"],
        "source_queue_sha256": plan["source_queue_sha256"],
        "authority_validation_sha256": plan["authority_validation_sha256"],
        "work_unit_sha256": work["artifact_sha256"],
        "queue_id": work["queue_id"],
        "action": work["action"],
        "pair_ids": work["pair_ids"],
        "source_generations_sha256": sha256_json(
            work.get("source_generations") or []
        ),
    }
    if any(value.get(field) != expected_value for field, expected_value in expected.items()):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "receipt cross-artifact roots do not match its work unit"
        )
    if not isinstance(value.get("completion_verified"), bool):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "receipt completion_verified must be boolean"
        )
    if work["action"] == QUEUE_MANUAL and value.get("completion_verified") is True:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "manual queue work can never be automatically verified"
        )
    if value["completion_verified"]:
        if value.get("status") not in {WORK_APPLIED, WORK_UNCHANGED}:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "only applied/unchanged work with fresh readback may be verified"
            )
        details = value.get("details")
        if not isinstance(details, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "verified receipt lacks execution details"
            )
        human_impact = details.get("human_impact")
        if not isinstance(human_impact, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "verified receipt lacks a human-authority assessment"
            )
        checked_impact = _validate_seal(
            human_impact, schema_version=HUMAN_IMPACT_SCHEMA_VERSION
        )
        if checked_impact.get("status") != "safe" or checked_impact.get("blockers") != []:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "verified receipt has a blocked human-authority assessment"
            )
        readback = details.get("readback")
        if not isinstance(readback, Mapping):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "verified receipt lacks fresh reciprocity readback"
            )
        checked_readback = _validated_readback(
            readback, plan=plan, work=work
        )
        if checked_readback.get("status") != "verified":
            raise ClaimEvidenceReciprocitySourceQueueError(
                "verified receipt readback is not verified"
            )
        apply_result = details.get("apply_result")
        if (
            not isinstance(apply_result, Mapping)
            or details.get("apply_result_sha256") != sha256_json(apply_result)
        ):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "verified receipt apply result is malformed"
            )
    return value


def build_source_work_readback(
    execution_plan: Mapping[str, Any],
    work_unit_id: str,
    *,
    post_authority_bridge: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove queued pairs disappeared or became reciprocal in a fresh audit."""

    plan, work = _work_from_plan(execution_plan, work_unit_id)
    bridge = validate_authority_bound_audit_and_queue(post_authority_bridge)
    audit_pairs = {
        str(row.get("pair_id") or ""): row
        for row in (bridge.get("audit") or {}).get("pairs") or []
        if isinstance(row, Mapping)
    }
    unresolved: list[str] = []
    resolutions: list[dict[str, str]] = []
    for pair_id in work["pair_ids"]:
        pair = audit_pairs.get(pair_id)
        if pair is None:
            resolutions.append({"pair_id": pair_id, "resolution": "pair_absent"})
        elif pair.get("mismatch_type") == "reciprocal":
            resolutions.append({"pair_id": pair_id, "resolution": "reciprocal"})
        else:
            unresolved.append(pair_id)
    result = {
        "schema_version": WORK_READBACK_SCHEMA_VERSION,
        "execution_plan_sha256": str(plan["artifact_sha256"]),
        "source_queue_sha256": str(plan["source_queue_sha256"]),
        "work_unit_id": str(work["work_unit_id"]),
        "work_unit_sha256": str(work["artifact_sha256"]),
        "post_authority_bridge_schema_version": AUTHORITY_BRIDGE_SCHEMA_VERSION,
        "post_authority_bridge_sha256": str(bridge["artifact_sha256"]),
        "post_frozen_input_sha256": str(bridge["roots"]["frozen_input_sha256"]),
        "post_audit_sha256": str(bridge["roots"]["audit_sha256"]),
        "post_pair_actions_sha256": str(bridge["roots"]["pair_actions_sha256"]),
        "resolutions": resolutions,
        "unresolved_pair_ids": unresolved,
        "status": "verified" if not unresolved else "blocked",
    }
    return _seal(result)


def _validated_readback(
    readback: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    work: Mapping[str, Any],
) -> dict[str, Any]:
    value = _validate_seal(readback, schema_version=WORK_READBACK_SCHEMA_VERSION)
    if (
        value.get("execution_plan_sha256") != plan.get("artifact_sha256")
        or value.get("source_queue_sha256") != plan.get("source_queue_sha256")
        or value.get("work_unit_id") != work.get("work_unit_id")
        or value.get("work_unit_sha256") != work.get("artifact_sha256")
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "readback belongs to another execution plan or work unit"
        )
    resolutions = value.get("resolutions")
    unresolved = value.get("unresolved_pair_ids")
    if not isinstance(resolutions, list) or not isinstance(unresolved, list):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "readback coverage is malformed"
        )
    covered = sorted(
        [str(row.get("pair_id") or "") for row in resolutions if isinstance(row, Mapping)]
        + [str(pair_id) for pair_id in unresolved]
    )
    if covered != sorted(work["pair_ids"]):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "readback does not exactly cover work-unit pairs"
        )
    if (value.get("status") == "verified") != (not unresolved):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "readback status disagrees with unresolved pairs"
        )
    return value


def consume_source_work_unit(
    execution_plan: Mapping[str, Any],
    work_unit_id: str,
    *,
    authority_validation: Mapping[str, Any],
    store: Any,
    human_authority_snapshot: Mapping[str, Any],
    apply: bool = False,
    rerun_candidate_receipt: Mapping[str, Any] | None = None,
    prior_receipts: Sequence[Mapping[str, Any]] = (),
    read_bytes: Callable[[Path], bytes] | None = None,
    supersede_planner: Callable[..., Any] = extraction_supersede_plan,
    completion_readback: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Plan/apply one exact unit, with exact-once and two locked CAS gates."""

    plan, work = _work_from_plan(execution_plan, work_unit_id)
    authority = _validate_seal(
        authority_validation, schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION
    )
    if authority["artifact_sha256"] != plan["authority_validation_sha256"]:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "execution plan belongs to another authority validation"
        )
    prior_for_work: list[dict[str, Any]] = []
    for raw in prior_receipts:
        receipt = validate_source_work_receipt(raw, execution_plan=plan)
        if receipt["work_unit_id"] == work_unit_id:
            prior_for_work.append(receipt)
    if len(prior_for_work) > 1:
        raise ClaimEvidenceReciprocitySourceQueueError(
            f"multiple receipts claim exact-once work {work_unit_id}"
        )
    if work["action"] == QUEUE_MANUAL:
        return _receipt(
            plan,
            work,
            status=WORK_MANUAL,
            completion_verified=False,
            reason_code=REASON_MANUAL_QUEUE,
        )
    if work.get("disposition") == "dispatch_binding_required":
        return _receipt(
            plan,
            work,
            status=WORK_MANUAL,
            completion_verified=False,
            reason_code="source_rerun_dispatch_binding_required",
        )
    human_snapshot = validate_current_human_authority_snapshot(
        human_authority_snapshot,
        require_store_roots=apply,
    )

    if work["action"] == QUEUE_EXACT_REPLAY:
        package = load_exact_replay_package(
            work, authority, read_bytes=read_bytes
        )
        source_kind = WKP364_EXACT_REPLAY_SOURCE_KIND
        package_proof = {
            "kind": "historical_exact_replay",
            "authority_unit_id": work["package_binding"]["authority_unit_id"],
            "effective_canonical_sha256": work["package_binding"][
                "effective_canonical_sha256"
            ],
            "historical_source_kind": work["package_binding"]["source_kind"],
            "guarded_apply_source_kind": source_kind,
        }
    else:
        if rerun_candidate_receipt is None:
            return _receipt(
                plan,
                work,
                status=WORK_PLANNED,
                completion_verified=False,
                reason_code="source_rerun_candidate_required",
                details={"dispatch": work.get("dispatch")},
            )
        package = _load_rerun_candidate(
            work,
            plan,
            rerun_candidate_receipt,
            read_bytes=read_bytes,
        )
        source_kind = WKP364_SOURCE_RERUN_SOURCE_KIND
        package_proof = {
            "kind": "governed_source_rerun",
            "rerun_candidate_receipt_sha256": rerun_candidate_receipt[
                "artifact_sha256"
            ],
            "effective_canonical_sha256": sha256_json(package),
            "guarded_apply_source_kind": source_kind,
        }

    expected_generations = _canonical_source_generations(
        work["source_generations"]
    )
    if _observed_source_generations(store, expected_generations) != expected_generations:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "SourceDocument generation changed before supersede planning"
        )
    planned = supersede_planner(store, package, source_kind=source_kind)
    if not isinstance(planned, tuple) or len(planned) < 4:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "extraction supersede planner returned a malformed result"
        )
    change_set = planned[0]
    withdrawal = planned[1]
    products_to_rebuild = planned[2]
    semantic_blockers = planned[3]
    if not isinstance(change_set, ChangeSetPlan):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "extraction supersede planner did not return a ChangeSetPlan"
        )
    if (
        change_set.source_kind != source_kind
        or change_set.source_sha256 != sha256_json(package)
        or change_set.package_id != str(package.get("package_id") or "")
    ):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "extraction supersede ChangeSet identity differs from the guarded package"
        )
    identity = _change_set_identity(change_set)
    if _observed_source_generations(store, expected_generations) != expected_generations:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "SourceDocument generation changed while supersede was planned"
        )
    human_impact = assess_current_human_authority_impact(
        change_set, human_snapshot
    )
    details = {
        "package_proof": package_proof,
        "change_set_identity": identity,
        "change_set_summary": change_set.as_dict()["summary"],
        "operation_fingerprints": operation_fingerprint_rows(
            change_set.operations
        ),
        "human_impact": human_impact,
        "semantic_blockers": _json_copy(semantic_blockers),
        "supersedes": (
            _json_copy(withdrawal.as_dict())
            if callable(getattr(withdrawal, "as_dict", None))
            else _json_copy(withdrawal)
        ),
        "products_to_rebuild": _json_copy(products_to_rebuild),
        "retirement_audits": _json_copy(list(planned[4:])),
    }
    if semantic_blockers:
        return _receipt(
            plan,
            work,
            status=WORK_MANUAL,
            completion_verified=False,
            reason_code=REASON_SEMANTIC_BLOCKER,
            details=details,
        )
    if human_impact["status"] != "safe":
        return _receipt(
            plan,
            work,
            status=WORK_MANUAL,
            completion_verified=False,
            reason_code=REASON_HUMAN_IMPACT,
            details=details,
        )
    if not apply:
        return _receipt(
            plan,
            work,
            status=WORK_PLANNED,
            completion_verified=False,
            details=details,
        )

    if completion_readback is None:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "apply/unchanged source work requires a fresh sealed audit readback"
        )
    zero_op = no_op_result(change_set)
    guarded_apply = getattr(
        store, "apply_claim_evidence_source_queue_plan", None
    )
    if not callable(guarded_apply):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "store lacks locked apply_claim_evidence_source_queue_plan; "
            "source work remains preview-only"
        )
    derivation_inputs = plan.get("derivation_inputs") or {}
    bridge = (
        derivation_inputs.get("authority_bridge")
        if isinstance(derivation_inputs, Mapping)
        else None
    )
    bridge_inputs = (
        bridge.get("derivation_inputs") if isinstance(bridge, Mapping) else None
    )
    frozen = (
        bridge_inputs.get("frozen_input")
        if isinstance(bridge_inputs, Mapping)
        else None
    )
    source_queue = (
        derivation_inputs.get("source_queue")
        if isinstance(derivation_inputs, Mapping)
        else None
    )
    if not all(isinstance(item, Mapping) for item in (bridge, frozen, source_queue)):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "canonical source apply requires execution derived from an authority bridge"
        )
    authorization = build_source_queue_apply_authorization(
        plan,
        work_unit_id,
        frozen_input=frozen,
        authority_bridge=bridge,
        source_queue=source_queue,
        authority_validation=authority,
        effective_package=package,
        package_proof=package_proof,
        source_candidate_receipt=rerun_candidate_receipt,
        expected_source_generations=expected_generations,
        expected_human_authority_snapshot=human_snapshot,
        human_impact=human_impact,
        change_set=change_set,
    )
    apply_result: Mapping[str, Any] = guarded_apply(
        change_set,
        authorization=authorization,
    )
    if not isinstance(apply_result, Mapping):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "guarded store apply returned a malformed result"
        )
    readback = _validated_readback(
        completion_readback(), plan=plan, work=work
    )
    details["apply_result"] = _json_copy(apply_result)
    details["apply_result_sha256"] = sha256_json(apply_result)
    details["readback"] = readback
    verified = readback["status"] == "verified"
    return _receipt(
        plan,
        work,
        status=(WORK_UNCHANGED if zero_op is not None else WORK_APPLIED),
        completion_verified=verified,
        reason_code=None if verified else REASON_READBACK,
        details=details,
    )


def build_source_queue_result(
    execution_plan: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Seal exact receipt coverage; only ``verified`` may unblock repair apply."""

    plan = validate_source_queue_execution_plan(execution_plan)
    receipt_by_work: dict[str, dict[str, Any]] = {}
    for raw in receipts:
        receipt = validate_source_work_receipt(raw, execution_plan=plan)
        work_id = str(receipt["work_unit_id"])
        if work_id in receipt_by_work:
            raise ClaimEvidenceReciprocitySourceQueueError(
                f"queue result repeats receipt for {work_id}"
            )
        receipt_by_work[work_id] = receipt
    known_ids = {str(row["work_unit_id"]) for row in plan["work_units"]}
    if set(receipt_by_work).difference(known_ids):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "queue result contains a receipt for unknown work"
        )
    blockers: list[dict[str, Any]] = []
    receipt_roots: list[dict[str, str]] = []
    for work in plan["work_units"]:
        work_id = str(work["work_unit_id"])
        receipt = receipt_by_work.get(work_id)
        if receipt is not None:
            receipt_roots.append(
                {
                    "work_unit_id": work_id,
                    "receipt_sha256": str(receipt["artifact_sha256"]),
                }
            )
        if work["action"] == QUEUE_MANUAL:
            blockers.append(
                {
                    "work_unit_id": work_id,
                    "queue_id": work["queue_id"],
                    "reason_code": REASON_MANUAL_QUEUE,
                }
            )
        elif receipt is None:
            blockers.append(
                {
                    "work_unit_id": work_id,
                    "queue_id": work["queue_id"],
                    "reason_code": "source_work_receipt_missing",
                }
            )
        elif receipt["completion_verified"] is not True:
            blockers.append(
                {
                    "work_unit_id": work_id,
                    "queue_id": work["queue_id"],
                    "reason_code": receipt.get("reason_code")
                    or "source_work_not_verified",
                    "receipt_sha256": receipt["artifact_sha256"],
                }
            )
    blockers.sort(key=lambda row: row["work_unit_id"])
    receipt_roots.sort(key=lambda row: row["work_unit_id"])
    result = {
        "schema_version": QUEUE_RESULT_SCHEMA_VERSION,
        "status": "verified" if not blockers else "blocked",
        "source_queue_complete": not blockers,
        # Source replay/rerun changes the active graph and invalidates every
        # repair plan bound to the old freeze.  Even a fully verified queue is
        # only authority to start a new freeze/audit/replan; it can never be
        # attached to the old plan as an apply-unlock token.
        "old_repair_plan_apply_allowed": False,
        "requires_fresh_freeze_audit_and_repair_plan": True,
        "next_required_action": "freeze_audit_and_build_new_repair_plan",
        "execution_plan_sha256": str(plan["artifact_sha256"]),
        "source_queue_sha256": str(plan["source_queue_sha256"]),
        "freeze_artifact_sha256": str(plan["freeze_artifact_sha256"]),
        "audit_artifact_sha256": str(plan["audit_artifact_sha256"]),
        "pair_action_manifest_sha256": str(plan["pair_action_manifest_sha256"]),
        "authority_validation_sha256": str(
            plan["authority_validation_sha256"]
        ),
        "work_unit_manifest_sha256": str(plan["work_unit_manifest_sha256"]),
        "receipt_roots": receipt_roots,
        "receipt_manifest_sha256": sha256_json(receipt_roots),
        "blockers": blockers,
        "blockers_sha256": sha256_json(blockers),
        "counts": {
            "work_units": len(plan["work_units"]),
            "receipts": len(receipt_roots),
            "verified_receipts": sum(
                row["completion_verified"] is True
                for row in receipt_by_work.values()
            ),
            "blockers": len(blockers),
        },
    }
    return _seal(result)


def validate_source_queue_result(
    artifact: Mapping[str, Any],
    *,
    execution_plan: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rebuild a result from its exact receipts and reject a forged unlock."""

    value = _validate_seal(artifact, schema_version=QUEUE_RESULT_SCHEMA_VERSION)
    expected = build_source_queue_result(execution_plan, receipts)
    if value != expected:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source queue result differs from its execution plan and receipts"
        )
    if value.get("old_repair_plan_apply_allowed") is not False:
        raise ClaimEvidenceReciprocitySourceQueueError(
            "source queue result may never unlock an old repair plan"
        )
    return value


def _load_json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimEvidenceReciprocitySourceQueueError(
            f"could not read {label} JSON at {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ClaimEvidenceReciprocitySourceQueueError(
            f"{label} JSON must be an object"
        )
    return value


def _write_json_file(path: Path | None, value: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    if path is None:
        sys.stdout.buffer.write(encoded)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _human_snapshot_from_store(store: Any) -> dict[str, Any]:
    reader = getattr(store, "read_claim_evidence_current_human_authority", None)
    if not callable(reader):
        raise ClaimEvidenceReciprocitySourceQueueError(
            "store lacks read_claim_evidence_current_human_authority"
        )
    observed = reader()
    if isinstance(observed, Mapping) and observed.get("schema_version") == (
        HUMAN_AUTHORITY_SNAPSHOT_SCHEMA_VERSION
    ):
        return validate_current_human_authority_snapshot(
            observed, require_store_roots=True
        )
    raise ClaimEvidenceReciprocitySourceQueueError(
        "store must return its sealed full human-authority snapshot"
    )


def _fresh_readback_builder(
    *,
    store: Any,
    execution_plan: Mapping[str, Any],
    work_unit_id: str,
    prerequisites_manifest: Mapping[str, Any],
    authority_manifest: Mapping[str, Any],
) -> Callable[[], Mapping[str, Any]]:
    def build() -> Mapping[str, Any]:
        # This deliberately re-freezes after source work.  No result from this
        # runner ever authorizes continuing with the old repair plan.
        frozen = freeze_claim_evidence_reciprocity_input(
            store, prerequisites_manifest=prerequisites_manifest
        )
        bridge = build_authority_bound_audit_and_queue(
            frozen, authority_manifest
        )
        return build_source_work_readback(
            execution_plan,
            work_unit_id,
            post_authority_bridge=bridge,
        )

    return build


def _command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser(
        "plan", help="compile a sealed queue into deterministic source work"
    )
    plan_parser.add_argument(
        "--authority-bridge",
        type=Path,
        help="preferred: sealed bind-authority output; avoids manually splitting roots",
    )
    plan_parser.add_argument("--queue", type=Path)
    plan_parser.add_argument("--authority-validation", type=Path)
    plan_parser.add_argument("--frozen-input", type=Path, required=True)
    plan_parser.add_argument("--rerun-bindings", type=Path)
    plan_parser.add_argument("--output", type=Path)

    dispatch_parser = commands.add_parser(
        "dispatch",
        help="preview or run a governed research rerun (never canonical ingest)",
    )
    dispatch_parser.add_argument("--execution-plan", type=Path, required=True)
    dispatch_parser.add_argument("--work-unit", required=True)
    dispatch_mode = dispatch_parser.add_mutually_exclusive_group()
    dispatch_mode.add_argument("--run", action="store_true")
    dispatch_mode.add_argument("--record-existing", action="store_true")
    dispatch_parser.add_argument("--run-manifest", type=Path)
    dispatch_parser.add_argument("--output", type=Path)

    for command in ("preview", "apply"):
        work_parser = commands.add_parser(
            command,
            help=(
                "plan one supersede under source/human guards"
                if command == "preview"
                else "apply one supersede only through the dedicated locked store guard"
            ),
        )
        work_parser.add_argument("--execution-plan", type=Path, required=True)
        work_parser.add_argument("--authority-validation", type=Path, required=True)
        work_parser.add_argument("--work-unit", required=True)
        work_parser.add_argument("--database-url")
        work_parser.add_argument("--human-snapshot", type=Path)
        work_parser.add_argument("--rerun-candidate", type=Path)
        work_parser.add_argument("--prior-receipt", type=Path, action="append", default=[])
        work_parser.add_argument("--output", type=Path)
        if command == "apply":
            work_parser.add_argument("--authority-manifest", type=Path, required=True)
            work_parser.add_argument(
                "--prerequisites-manifest", type=Path, required=True
            )

    readback_parser = commands.add_parser(
        "readback", help="bind one work unit to a fresh authority/audit bridge"
    )
    readback_parser.add_argument("--execution-plan", type=Path, required=True)
    readback_parser.add_argument("--work-unit", required=True)
    readback_parser.add_argument("--post-bridge", type=Path, required=True)
    readback_parser.add_argument("--output", type=Path)

    result_parser = commands.add_parser(
        "result", help="verify exact receipt coverage and require a fresh replan"
    )
    result_parser.add_argument("--execution-plan", type=Path, required=True)
    result_parser.add_argument("--receipt", type=Path, action="append", default=[])
    result_parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _command_parser().parse_args(argv)
    if args.command == "plan":
        if args.authority_bridge:
            if args.queue or args.authority_validation:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    "--authority-bridge cannot be combined with split queue/validation inputs"
                )
            bridge = validate_authority_bound_audit_and_queue(
                _load_json_file(args.authority_bridge, label="authority bridge")
            )
            queue = bridge["source_work_queue"]
            authority = bridge["authority_validation"]
        else:
            if not args.queue or not args.authority_validation:
                raise ClaimEvidenceReciprocitySourceQueueError(
                    "plan requires --authority-bridge or both --queue and "
                    "--authority-validation"
                )
            queue = _load_json_file(args.queue, label="source queue")
            authority = _load_json_file(
                args.authority_validation, label="authority validation"
            )
        frozen = validate_sealed_artifact(
            _load_json_file(args.frozen_input, label="frozen input"),
            expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION,
        )
        if frozen.get("artifact_sha256") != queue.get("freeze_artifact_sha256"):
            raise ClaimEvidenceReciprocitySourceQueueError(
                "source queue and frozen input cross-roots differ"
            )
        bindings: Mapping[str, Mapping[str, Any]] = {}
        if args.rerun_bindings:
            bindings_value = _load_json_file(
                args.rerun_bindings, label="rerun bindings"
            )
            bindings = bindings_value
        output = build_source_queue_execution_plan(
            queue,
            authority,
            active_source_documents=frozen.get("active_source_documents") or [],
            rerun_bindings=bindings,
            authority_bridge=(bridge if args.authority_bridge else None),
        )
        _write_json_file(args.output, output)
        return 0

    if args.command == "dispatch":
        execution = _load_json_file(args.execution_plan, label="execution plan")
        plan, work = _work_from_plan(execution, args.work_unit)
        if work.get("action") != QUEUE_SOURCE_RERUN:
            raise ClaimEvidenceReciprocitySourceQueueError(
                "dispatch command requires source-rerun work"
            )
        dispatch = work.get("dispatch")
        if not isinstance(dispatch, Mapping) or dispatch.get("status") != "ready":
            raise ClaimEvidenceReciprocitySourceQueueError(
                "source-rerun work lacks a ready dispatch binding"
            )
        if not args.run and not args.record_existing:
            _write_json_file(
                args.output,
                {
                    "status": "preview",
                    "execution_plan_sha256": plan["artifact_sha256"],
                    "work_unit_sha256": work["artifact_sha256"],
                    "command": dispatch["command"],
                    "ingest_apply": False,
                    "reviewed_candidate_path": dispatch[
                        "reviewed_candidate_path"
                    ],
                },
            )
            return 0
        manifest_path = args.run_manifest or (
            Path(str(dispatch["output_root"])) / "run-manifest.json"
        )
        if args.run:
            completed = subprocess.run(
                list(dispatch["command"]),
                cwd=Path(__file__).resolve().parents[2],
                check=False,
            )
            if completed.returncode:
                return int(completed.returncode)
        candidate = record_source_rerun_candidate(
            execution,
            args.work_unit,
            run_manifest_path=manifest_path,
        )
        _write_json_file(args.output, candidate)
        return 0

    if args.command in {"preview", "apply"}:
        execution = _load_json_file(args.execution_plan, label="execution plan")
        authority = _load_json_file(
            args.authority_validation, label="authority validation"
        )
        store = PostgresKnowledgeStore(args.database_url)
        human_snapshot = (
            validate_current_human_authority_snapshot(
                _load_json_file(args.human_snapshot, label="human snapshot")
            )
            if args.human_snapshot
            else _human_snapshot_from_store(store)
        )
        candidate = (
            _load_json_file(args.rerun_candidate, label="rerun candidate receipt")
            if args.rerun_candidate
            else None
        )
        priors = [
            _load_json_file(path, label="prior work receipt")
            for path in args.prior_receipt
        ]
        readback_builder = None
        if args.command == "apply":
            authority_manifest = _load_json_file(
                args.authority_manifest, label="authority manifest"
            )
            prerequisites = _load_json_file(
                args.prerequisites_manifest, label="prerequisites manifest"
            )
            readback_builder = _fresh_readback_builder(
                store=store,
                execution_plan=execution,
                work_unit_id=args.work_unit,
                prerequisites_manifest=prerequisites,
                authority_manifest=authority_manifest,
            )
        receipt = consume_source_work_unit(
            execution,
            args.work_unit,
            authority_validation=authority,
            store=store,
            human_authority_snapshot=human_snapshot,
            apply=args.command == "apply",
            rerun_candidate_receipt=candidate,
            prior_receipts=priors,
            completion_readback=readback_builder,
        )
        _write_json_file(args.output, receipt)
        return 0 if receipt.get("status") != WORK_MANUAL else 2

    if args.command == "readback":
        output = build_source_work_readback(
            _load_json_file(args.execution_plan, label="execution plan"),
            args.work_unit,
            post_authority_bridge=_load_json_file(
                args.post_bridge, label="post-work authority bridge"
            ),
        )
        _write_json_file(args.output, output)
        return 0 if output["status"] == "verified" else 2

    if args.command == "result":
        execution = _load_json_file(args.execution_plan, label="execution plan")
        receipts = [
            _load_json_file(path, label="source work receipt")
            for path in args.receipt
        ]
        output = build_source_queue_result(execution, receipts)
        validate_source_queue_result(
            output, execution_plan=execution, receipts=receipts
        )
        _write_json_file(args.output, output)
        return 0 if output["status"] == "verified" else 2

    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
