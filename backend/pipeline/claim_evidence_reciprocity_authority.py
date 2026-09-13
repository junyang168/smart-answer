"""Authenticate historical source packages and group #364 work by source.

The functions here are deliberately pure with respect to PostgreSQL and model
execution.  Callers supply frozen ChangeSet and SourceDocument rows plus a file
reader.  A package is eligible for exact replay only when all three independent
claims hold:

* the bytes and reviewed-candidate seal authenticate;
* an applied historical ChangeSet binds the exact effective package; and
* that package still names the active source generation and passes today's
  canonical-store contract.

Pair findings remain in the reciprocity audit.  Operational queues are grouped
by source (or by an indivisible aggregate reviewed package), so one bad source
does not become hundreds of duplicate rerun requests.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import (
    record_content_sha,
    sha256_json,
)
from backend.api.canonical_repository.reviewed_candidate_contract import (
    ConsensusApplicationError,
    RESEARCH_BATCH_AGGREGATE,
    SOURCE_SCOPED,
    reseal_after_relation_id_migration,
    validate_reviewed_candidate_artifact,
    validate_store_package_authorization,
)
from backend.pipeline.relation_id_namespace import (
    LEGACY_CLAIM_RELATION_ID,
    LEGACY_EVIDENCE_RELATION_ID,
    RelationIdNamespaceError,
    migrate_legacy_cross_section_relation_ids,
)
from backend.pipeline.source_keys import document_row_key


AUTHORITY_MANIFEST_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_authority_manifest_v1"
)
AUTHORITY_VALIDATION_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_authority_validation_v1"
)
SOURCE_QUEUE_SCHEMA_VERSION = "wang_claim_evidence_reciprocity_source_queue_v1"

REPLAY_ELIGIBLE = "historical_package_authenticated_current_contract"
REASON_RAW_SHA = "historical_package_raw_sha_mismatch"
REASON_JSON = "historical_package_json_invalid"
REASON_CANONICAL_SHA = "historical_package_canonical_sha_mismatch"
REASON_REVIEW_SEAL = "reviewed_candidate_seal_invalid"
REASON_MIGRATION = "relation_id_migration_lineage_invalid"
REASON_CURRENT_CONTRACT = "historical_package_current_contract_failed"
REASON_LEDGER_MISSING = "historical_change_set_missing"
REASON_LEDGER_NOT_APPLIED = "historical_change_set_not_applied"
REASON_LEDGER_MISMATCH = "historical_change_set_identity_mismatch"
REASON_LEDGER_METADATA = "historical_change_set_effective_metadata_mismatch"
REASON_SOURCE_GENERATION = "historical_package_not_current_source_generation"

QUEUE_EXACT_REPLAY = "exact_reviewed_source_replay"
QUEUE_SOURCE_RERUN = "authoritative_source_rerun"
QUEUE_MANUAL = "manual_adjudication"
UNRESOLVED_SOURCE_TYPE = "wkp364_unresolved_source_lineage"

DIRECT_DISPOSITIONS = {"project_human_binding_to_evidence", "none_reciprocal"}
QUEUE_SEVERITY = {
    QUEUE_EXACT_REPLAY: 0,
    QUEUE_SOURCE_RERUN: 1,
    QUEUE_MANUAL: 2,
}


class ClaimEvidenceReciprocityAuthorityError(ValueError):
    """An authority or source-queue artifact is structurally unsafe."""


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def seal_authority_artifact(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _json_copy(payload)
    result.pop("artifact_sha256", None)
    result["artifact_sha256"] = sha256_json(result)
    return result


def validate_authority_artifact(
    artifact: Mapping[str, Any], *, schema_version: str
) -> dict[str, Any]:
    result = _json_copy(artifact)
    if result.get("schema_version") != schema_version:
        raise ClaimEvidenceReciprocityAuthorityError(
            f"schema_version must be {schema_version}"
        )
    claimed = str(result.pop("artifact_sha256", ""))
    if not claimed or claimed != sha256_json(result):
        raise ClaimEvidenceReciprocityAuthorityError(
            f"{schema_version} artifact seal does not match"
        )
    result["artifact_sha256"] = claimed
    return result


def _required_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ClaimEvidenceReciprocityAuthorityError(f"{field} is required")
    return text


def _required_sha256(value: Any, field: str) -> str:
    text = _required_string(value, field)
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ClaimEvidenceReciprocityAuthorityError(
            f"{field} must be a lowercase SHA-256 digest"
        )
    return text


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_identity(document: Mapping[str, Any]) -> dict[str, str]:
    source_type = _required_string(document.get("source_type"), "source_type")
    row_key = _required_string(document_row_key(document), "source row_key")
    return {"source_type": source_type, "row_key": row_key}


def _source_identity_key(identity: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _required_string(identity.get("source_type"), "source_identity.source_type"),
        _required_string(identity.get("row_key"), "source_identity.row_key"),
    )


def _generation_signature(
    document: Mapping[str, Any], *, extraction: Mapping[str, Any] | None = None
) -> dict[str, str]:
    identity = _source_identity(document)
    body_sha = str(
        document.get("source_body_sha256")
        or document.get("source_sha256")
        or ""
    ).strip()
    namespace = str(
        document.get("extraction_record_namespace")
        or (extraction or {}).get("record_namespace")
        or ""
    ).strip()
    if not body_sha or not namespace:
        raise ClaimEvidenceReciprocityAuthorityError(
            f"source {identity['source_type']}/{identity['row_key']} lacks exact "
            "body SHA or extraction namespace"
        )
    return {
        **identity,
        "source_body_sha256": body_sha,
        "extraction_record_namespace": namespace,
    }


def _observed_generation_signature(document: Mapping[str, Any]) -> dict[str, str]:
    """Describe a live generation without upgrading missing legacy provenance."""

    identity = _source_identity(document)
    return {
        **identity,
        "source_body_sha256": str(
            document.get("source_body_sha256")
            or document.get("source_sha256")
            or ""
        ).strip(),
        "extraction_record_namespace": str(
            document.get("extraction_record_namespace") or ""
        ).strip(),
    }


def _normalize_active_source_documents(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"active_source_documents[{index}] must be an object"
            )
        payload_value = raw.get("payload", raw)
        if not isinstance(payload_value, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"active_source_documents[{index}].payload must be an object"
            )
        payload = _json_copy(payload_value)
        collection = str(raw.get("collection") or "source_documents")
        if collection != "source_documents":
            raise ClaimEvidenceReciprocityAuthorityError(
                f"active_source_documents[{index}] is not a SourceDocument"
            )
        object_id = str(raw.get("object_id") or payload.get("source_id") or "").strip()
        if not object_id or str(payload.get("source_id") or "") != object_id:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"active_source_documents[{index}] has inconsistent source ID"
            )
        try:
            revision = int(raw.get("revision", payload.get("revision")))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"active_source_documents[{index}] has invalid revision"
            ) from None
        content_sha = _required_string(
            raw.get("content_sha256"),
            f"active_source_documents[{index}].content_sha256",
        )
        if revision < 1 or content_sha != record_content_sha(payload):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"active source {object_id} has invalid revision or content SHA"
            )
        signature = _observed_generation_signature(payload)
        key = _source_identity_key(signature)
        if key in result:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"active source identity is ambiguous: {key[0]}/{key[1]}"
            )
        result[key] = {
            "object_id": object_id,
            "revision": revision,
            "content_sha256": content_sha,
            "generation": signature,
        }
    return result


def _expected_source_generations(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = spec.get("source_generations")
    if not isinstance(rows, list) or not rows:
        raise ClaimEvidenceReciprocityAuthorityError(
            "authority package requires source_generations"
        )
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source_generations[{index}] must be an object"
            )
        row = _json_copy(raw)
        key = _source_identity_key(row)
        if key in seen:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source_generations repeat {key[0]}/{key[1]}"
            )
        seen.add(key)
        row.update({"source_type": key[0], "row_key": key[1]})
        for field in (
            "active_source_document_id",
            "extraction_record_namespace",
        ):
            _required_string(row.get(field), f"source_generations[{index}].{field}")
        for field in ("expected_content_sha256", "source_body_sha256"):
            _required_sha256(
                row.get(field), f"source_generations[{index}].{field}"
            )
        try:
            row["expected_revision"] = int(row.get("expected_revision"))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source_generations[{index}].expected_revision is invalid"
            ) from None
        normalized.append(row)
    return sorted(normalized, key=lambda row: (row["source_type"], row["row_key"]))


def _legacy_relation_occurrences(value: Any, *, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            findings.extend(
                _legacy_relation_occurrences(child, path=f"{path}.{key}")
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(
                _legacy_relation_occurrences(child, path=f"{path}[{index}]")
            )
    elif isinstance(value, str) and (
        LEGACY_EVIDENCE_RELATION_ID.fullmatch(value)
        or LEGACY_CLAIM_RELATION_ID.fullmatch(value)
    ):
        findings.append(f"{path}={value}")
    return findings


def _derive_effective_package(
    original: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    application = original.get("consensus_application") or {}
    scope_kind = str(application.get("scope_kind") or "")
    if scope_kind == SOURCE_SCOPED:
        migrated, migration = migrate_legacy_cross_section_relation_ids(original)
        effective = reseal_after_relation_id_migration(
            original, migrated, migration
        )
        return effective, migration
    if scope_kind == RESEARCH_BATCH_AGGREGATE:
        legacy_occurrences = _legacy_relation_occurrences(original)
        if legacy_occurrences:
            raise ClaimEvidenceReciprocityAuthorityError(
                "aggregate reviewed candidate contains legacy relation IDs but "
                "source-scoped relation-ID migration cannot authenticate an "
                "aggregate rewrite: "
                + ", ".join(sorted(set(legacy_occurrences)))
            )
        canonical_sha = sha256_json(original)
        return _json_copy(original), {
            "schema_version": "wang_relation_id_namespace_migration_v1",
            "status": "not_required",
            "scope_kind": RESEARCH_BATCH_AGGREGATE,
            "input_canonical_sha256": canonical_sha,
            "output_canonical_sha256": canonical_sha,
            "semantic_change": "none",
            "round_trip_verified": True,
            "legacy_relation_ids_verified_absent": True,
        }
    raise ClaimEvidenceReciprocityAuthorityError(
        f"reviewed candidate has unsupported scope_kind {scope_kind!r}"
    )


def _validate_source_generations(
    *,
    package: Mapping[str, Any],
    expected: Sequence[Mapping[str, Any]],
    active: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    extraction = package.get("extraction")
    if extraction is not None and not isinstance(extraction, Mapping):
        raise ClaimEvidenceReciprocityAuthorityError(
            "package extraction metadata must be an object"
        )
    derived: dict[tuple[str, str], dict[str, str]] = {}
    for document in package.get("source_documents") or []:
        if not isinstance(document, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                "package source_documents contain a malformed row"
            )
        signature = _generation_signature(document, extraction=extraction)
        key = _source_identity_key(signature)
        if key in derived:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"package source identity is ambiguous: {key[0]}/{key[1]}"
            )
        derived[key] = signature
    expected_by_key = {_source_identity_key(row): row for row in expected}
    if set(derived) != set(expected_by_key):
        raise ClaimEvidenceReciprocityAuthorityError(
            "authority manifest source identities do not exactly match the package"
        )
    for key in sorted(expected_by_key):
        expected_row = expected_by_key[key]
        active_row = active.get(key)
        if active_row is None:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"active source generation is missing: {key[0]}/{key[1]}"
            )
        expected_generation = {
            "source_type": key[0],
            "row_key": key[1],
            "source_body_sha256": str(expected_row["source_body_sha256"]),
            "extraction_record_namespace": str(
                expected_row["extraction_record_namespace"]
            ),
        }
        if (
            derived[key] != expected_generation
            or active_row["generation"] != expected_generation
            or active_row["object_id"]
            != str(expected_row["active_source_document_id"])
            or active_row["revision"] != int(expected_row["expected_revision"])
            or active_row["content_sha256"]
            != str(expected_row["expected_content_sha256"])
        ):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source generation drifted: {key[0]}/{key[1]}"
            )
    # Keep the semantic source-generation signature and the physical active-row
    # CAS together.  A later queue consumer must re-read both immediately before
    # applying a supersession plan; body SHA/namespace alone cannot notice an
    # intervening retirement/revival whose column revision changed while the
    # semantic payload stayed byte-for-byte identical.
    return [
        {
            **derived[key],
            "active_source_document_id": str(
                expected_by_key[key]["active_source_document_id"]
            ),
            "expected_revision": int(expected_by_key[key]["expected_revision"]),
            "expected_content_sha256": str(
                expected_by_key[key]["expected_content_sha256"]
            ),
        }
        for key in sorted(derived)
    ]


def _package_claim_evidence_pairs(
    package: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Account for package reciprocity without set-collapsing duplicate refs."""

    claim_pairs: list[tuple[str, str]] = []
    evidence_pairs: list[tuple[str, str]] = []
    for index, claim in enumerate(package.get("claims") or []):
        if not isinstance(claim, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"package claims[{index}] must be an object"
            )
        claim_id = _required_string(claim.get("claim_id"), "package claim_id")
        evidence_ids = claim.get("evidence_step_ids")
        if not isinstance(evidence_ids, list):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"package claim {claim_id} evidence_step_ids must be a list"
            )
        claim_pairs.extend(
            (
                claim_id,
                _required_string(
                    evidence_id,
                    f"package claim {claim_id} evidence_step_ids[{pair_index}]",
                ),
            )
            for pair_index, evidence_id in enumerate(evidence_ids)
        )
    for index, evidence in enumerate(package.get("evidence_steps") or []):
        if not isinstance(evidence, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"package evidence_steps[{index}] must be an object"
            )
        evidence_id = _required_string(
            evidence.get("evidence_step_id"), "package evidence_step_id"
        )
        claim_ids = evidence.get("produced_claim_ids")
        if not isinstance(claim_ids, list):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"package evidence {evidence_id} produced_claim_ids must be a list"
            )
        evidence_pairs.extend(
            (
                _required_string(
                    claim_id,
                    f"package evidence {evidence_id} produced_claim_ids[{pair_index}]",
                ),
                evidence_id,
            )
            for pair_index, claim_id in enumerate(claim_ids)
        )
    repeated_claim = sorted(
        pair for pair, count in Counter(claim_pairs).items() if count > 1
    )
    repeated_evidence = sorted(
        pair for pair, count in Counter(evidence_pairs).items() if count > 1
    )
    if repeated_claim or repeated_evidence or sorted(claim_pairs) != sorted(evidence_pairs):
        raise ClaimEvidenceReciprocityAuthorityError(
            "current-contract package pair accounting is not exactly reciprocal"
        )
    return [
        {"claim_id": claim_id, "evidence_step_id": evidence_id}
        for claim_id, evidence_id in sorted(claim_pairs)
    ]


def _validate_historical_change_set(
    *,
    spec: Mapping[str, Any],
    ledger: Mapping[str, Mapping[str, Any]],
    input_sha: str,
    effective_sha: str,
    upstream_artifact_sha: str,
    effective_artifact_sha: str,
    migration: Mapping[str, Any],
) -> dict[str, Any]:
    expected = spec.get("historical_change_set")
    if not isinstance(expected, Mapping):
        raise ClaimEvidenceReciprocityAuthorityError(
            "authority package requires historical_change_set"
        )
    change_set_id = _required_string(
        expected.get("change_set_id"), "historical_change_set.change_set_id"
    )
    actual = ledger.get(change_set_id)
    if actual is None:
        raise LookupError(REASON_LEDGER_MISSING)
    if str(actual.get("status") or "") != "applied":
        raise PermissionError(REASON_LEDGER_NOT_APPLIED)
    try:
        _required_sha256(
            expected.get("fingerprint_sha256"),
            "historical_change_set.fingerprint_sha256",
        )
        _required_string(
            expected.get("source_kind"), "historical_change_set.source_kind"
        )
    except ClaimEvidenceReciprocityAuthorityError as exc:
        raise RuntimeError(f"{REASON_LEDGER_MISMATCH}: {exc}") from exc
    exact_fields = ("fingerprint_sha256", "source_kind", "status")
    if any(actual.get(field) != expected.get(field) for field in exact_fields):
        raise RuntimeError(REASON_LEDGER_MISMATCH)
    expected_source_sha = str(expected.get("source_sha256") or "")
    actual_source_sha = str(actual.get("source_sha256") or "")
    if expected_source_sha != effective_sha or actual_source_sha != effective_sha:
        raise RuntimeError(
            f"{REASON_LEDGER_MISMATCH}: historical source_sha256 must equal "
            f"effective package {effective_sha}, found "
            f"manifest={expected_source_sha or '<missing>'} "
            f"ledger={actual_source_sha or '<missing>'}; original package {input_sha} "
            "is lineage only and is not accepted at the replay boundary"
        )
    metadata = actual.get("metadata")
    if not isinstance(metadata, Mapping):
        raise RuntimeError(REASON_LEDGER_METADATA)
    if (
        metadata.get("upstream_reviewed_candidate_artifact_sha256")
        != upstream_artifact_sha
        or metadata.get("effective_reviewed_candidate_artifact_sha256")
        != effective_artifact_sha
        or metadata.get("relation_id_namespace_migration") != migration
    ):
        raise RuntimeError(REASON_LEDGER_METADATA)
    return {
        "change_set_id": change_set_id,
        **{field: actual.get(field) for field in exact_fields},
        "source_sha256": actual_source_sha,
        "applied_at": actual.get("applied_at"),
        "effective_metadata_sha256": sha256_json(
            {
                "upstream_reviewed_candidate_artifact_sha256": upstream_artifact_sha,
                "effective_reviewed_candidate_artifact_sha256": effective_artifact_sha,
                "relation_id_namespace_migration": migration,
            }
        ),
    }


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, LookupError):
        return REASON_LEDGER_MISSING
    if isinstance(exc, PermissionError):
        return REASON_LEDGER_NOT_APPLIED
    text = str(exc)
    for reason in (REASON_LEDGER_MISMATCH, REASON_LEDGER_METADATA):
        if reason in text:
            return reason
    if isinstance(exc, json.JSONDecodeError):
        return REASON_JSON
    if isinstance(exc, (ConsensusApplicationError,)):
        if "graph is invalid" in text or "reciprocal" in text:
            return REASON_CURRENT_CONTRACT
        return REASON_REVIEW_SEAL
    if isinstance(exc, RelationIdNamespaceError):
        return REASON_MIGRATION
    if isinstance(exc, ClaimEvidenceReciprocityAuthorityError):
        if "source" in text:
            return REASON_SOURCE_GENERATION
    return REASON_CURRENT_CONTRACT


def _validate_one_package(
    spec: Mapping[str, Any],
    *,
    read_bytes: Callable[[Path], bytes],
    historical_change_sets: Mapping[str, Mapping[str, Any]],
    active_sources: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    authority_unit_id = _required_string(
        spec.get("authority_unit_id"), "authority_unit_id"
    )
    path = Path(_required_string(spec.get("path"), f"{authority_unit_id}.path"))
    expected_generations = _expected_source_generations(spec)
    base = {
        "authority_unit_id": authority_unit_id,
        "path": str(path),
        "source_identities": [
            {"source_type": row["source_type"], "row_key": row["row_key"]}
            for row in expected_generations
        ],
    }
    stage = "raw"
    try:
        raw = read_bytes(path)
        raw_sha = _sha256_bytes(raw)
        if raw_sha != str(spec.get("raw_sha256") or ""):
            raise ClaimEvidenceReciprocityAuthorityError(REASON_RAW_SHA)
        stage = "json"
        original = json.loads(raw)
        if not isinstance(original, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(REASON_JSON)
        input_sha = sha256_json(original)
        if input_sha != str(spec.get("input_canonical_sha256") or ""):
            raise ClaimEvidenceReciprocityAuthorityError(REASON_CANONICAL_SHA)
        stage = "review_seal"
        validate_reviewed_candidate_artifact(original)
        upstream_artifact_sha = str(
            (original.get("consensus_application") or {}).get("artifact_sha256")
            or ""
        )
        if upstream_artifact_sha != str(
            spec.get("upstream_reviewed_candidate_artifact_sha256") or ""
        ):
            raise ClaimEvidenceReciprocityAuthorityError(REASON_REVIEW_SEAL)
        stage = "migration"
        effective, migration = _derive_effective_package(original)
        effective_sha = sha256_json(effective)
        effective_artifact_sha = str(
            (effective.get("consensus_application") or {}).get("artifact_sha256")
            or ""
        )
        if (
            effective_sha != str(spec.get("effective_canonical_sha256") or "")
            or effective_artifact_sha
            != str(spec.get("effective_reviewed_candidate_artifact_sha256") or "")
            or migration != spec.get("relation_id_namespace_migration")
        ):
            raise ClaimEvidenceReciprocityAuthorityError(REASON_MIGRATION)
        stage = "current_contract"
        validate_store_package_authorization(effective)
        claim_evidence_pairs = _package_claim_evidence_pairs(effective)
        stage = "source_generation"
        source_generations = _validate_source_generations(
            package=effective,
            expected=expected_generations,
            active=active_sources,
        )
        stage = "historical_change_set"
        historical = _validate_historical_change_set(
            spec=spec,
            ledger=historical_change_sets,
            input_sha=input_sha,
            effective_sha=effective_sha,
            upstream_artifact_sha=upstream_artifact_sha,
            effective_artifact_sha=effective_artifact_sha,
            migration=migration,
        )
        return {
            **base,
            "status": "eligible",
            "replay_eligible": True,
            "reason_code": REPLAY_ELIGIBLE,
            "raw_sha256": raw_sha,
            "input_canonical_sha256": input_sha,
            "effective_canonical_sha256": effective_sha,
            "upstream_reviewed_candidate_artifact_sha256": upstream_artifact_sha,
            "effective_reviewed_candidate_artifact_sha256": effective_artifact_sha,
            "relation_id_namespace_migration": migration,
            "package_id": _required_string(
                effective.get("package_id"), "effective package_id"
            ),
            "scope_kind": str(
                (effective.get("consensus_application") or {}).get("scope_kind")
                or ""
            ),
            "review_completion": str(
                (effective.get("consensus_application") or {}).get(
                    "review_completion"
                )
                or ""
            ),
            "claim_evidence_pairs": claim_evidence_pairs,
            "claim_evidence_pairs_sha256": sha256_json(claim_evidence_pairs),
            "source_generations": source_generations,
            "historical_change_set": historical,
            "current_contract": "passed",
        }
    except Exception as exc:
        explicit_reason = str(exc) if str(exc) in {
            REASON_RAW_SHA,
            REASON_JSON,
            REASON_CANONICAL_SHA,
            REASON_REVIEW_SEAL,
            REASON_MIGRATION,
            REASON_CURRENT_CONTRACT,
            REASON_LEDGER_MISSING,
            REASON_LEDGER_NOT_APPLIED,
            REASON_LEDGER_MISMATCH,
            REASON_LEDGER_METADATA,
            REASON_SOURCE_GENERATION,
        } else None
        reason = explicit_reason or {
            "raw": REASON_RAW_SHA,
            "json": REASON_JSON,
            "review_seal": REASON_REVIEW_SEAL,
            "migration": REASON_MIGRATION,
            "current_contract": REASON_CURRENT_CONTRACT,
            "source_generation": REASON_SOURCE_GENERATION,
        }.get(stage) or _failure_reason(exc)
        return {
            **base,
            "status": "blocked",
            "replay_eligible": False,
            "reason_code": reason,
            "failed_stage": stage,
            "required_action": QUEUE_SOURCE_RERUN,
            "detail": f"{type(exc).__name__}: {exc}",
        }


def validate_authority_manifest(
    manifest: Mapping[str, Any],
    *,
    historical_change_sets: Mapping[str, Mapping[str, Any]],
    active_source_documents: Sequence[Mapping[str, Any]],
    read_bytes: Callable[[Path], bytes] | None = None,
) -> dict[str, Any]:
    """Return a sealed source-keyed validation of every declared package."""

    authenticated = validate_authority_artifact(
        manifest, schema_version=AUTHORITY_MANIFEST_SCHEMA_VERSION
    )
    package_specs = authenticated.get("packages")
    if not isinstance(package_specs, list):
        raise ClaimEvidenceReciprocityAuthorityError(
            "authority manifest packages must be a list"
        )
    unit_ids: list[str] = []
    change_set_ids: list[str] = []
    for index, spec in enumerate(package_specs):
        if not isinstance(spec, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"authority manifest packages[{index}] must be an object"
            )
        unit_ids.append(_required_string(spec.get("authority_unit_id"), "authority_unit_id"))
        historical = spec.get("historical_change_set") or {}
        change_set_ids.append(
            _required_string(historical.get("change_set_id"), "historical_change_set_id")
        )
    repeated_units = sorted(key for key, count in Counter(unit_ids).items() if count > 1)
    repeated_change_sets = sorted(
        key for key, count in Counter(change_set_ids).items() if count > 1
    )
    if repeated_units or repeated_change_sets:
        raise ClaimEvidenceReciprocityAuthorityError(
            "authority manifest repeats authority units or historical ChangeSets"
        )
    # An explicit empty manifest grants no replay authority, so unrelated
    # legacy SourceDocument aliases cannot affect its zero-authority result.
    active = (
        _normalize_active_source_documents(active_source_documents)
        if package_specs
        else {}
    )
    loader = read_bytes or (lambda path: path.read_bytes())
    packages = [
        _validate_one_package(
            spec,
            read_bytes=loader,
            historical_change_sets=historical_change_sets,
            active_sources=active,
        )
        for spec in sorted(package_specs, key=lambda row: str(row["authority_unit_id"]))
    ]
    result = {
        "schema_version": AUTHORITY_VALIDATION_SCHEMA_VERSION,
        "status": "eligible" if all(row["replay_eligible"] for row in packages) else "blocked",
        "authority_manifest_sha256": authenticated["artifact_sha256"],
        "packages": packages,
        "counts": {
            "packages": len(packages),
            "replay_eligible": sum(row["replay_eligible"] for row in packages),
            "blocked": sum(not row["replay_eligible"] for row in packages),
            "sources": sum(len(row["source_identities"]) for row in packages),
        },
    }
    return seal_authority_artifact(result)


def _normalized_pair_sources(
    pair_id: str,
    pair_sources: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, str]]:
    normalized = {
        _source_identity_key(row): {
            "source_type": _source_identity_key(row)[0],
            "row_key": _source_identity_key(row)[1],
        }
        for row in pair_sources.get(pair_id, ())
    }
    return [normalized[key] for key in sorted(normalized)]


def _normalized_authority_unit_sources(
    authority_unit_id: str,
    rows: Any,
) -> list[dict[str, str]]:
    if not isinstance(rows, list) or not rows:
        raise ClaimEvidenceReciprocityAuthorityError(
            f"authority unit {authority_unit_id} requires source identities"
        )
    normalized: dict[tuple[str, str], dict[str, str]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"authority unit {authority_unit_id} source_identities[{index}] "
                "must be an object"
            )
        key = _source_identity_key(row)
        if key in normalized:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"authority unit {authority_unit_id} repeats source "
                f"{key[0]}/{key[1]}"
            )
        normalized[key] = {"source_type": key[0], "row_key": key[1]}
    return [normalized[key] for key in sorted(normalized)]


def _queue_action(disposition: str) -> str:
    if disposition == QUEUE_EXACT_REPLAY:
        return QUEUE_EXACT_REPLAY
    if disposition == QUEUE_SOURCE_RERUN:
        return QUEUE_SOURCE_RERUN
    return QUEUE_MANUAL


def build_source_work_queues(
    pair_actions: Sequence[Mapping[str, Any]],
    *,
    freeze_artifact_sha256: str,
    audit_artifact_sha256: str,
    authority_validation: Mapping[str, Any],
    pair_sources: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Group every blocking pair exactly once by safe operational source."""

    authority = validate_authority_artifact(
        authority_validation, schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION
    )
    freeze_sha = _required_sha256(
        freeze_artifact_sha256, "freeze_artifact_sha256"
    )
    audit_sha = _required_sha256(
        audit_artifact_sha256, "audit_artifact_sha256"
    )
    packages = authority.get("packages")
    if not isinstance(packages, list):
        raise ClaimEvidenceReciprocityAuthorityError(
            "authority validation packages must be a list"
        )
    units: dict[str, dict[str, Any]] = {}
    for index, raw_unit in enumerate(packages):
        if not isinstance(raw_unit, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"authority validation packages[{index}] must be an object"
            )
        authority_unit_id = _required_string(
            raw_unit.get("authority_unit_id"),
            f"authority validation packages[{index}].authority_unit_id",
        )
        if authority_unit_id in units:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"authority validation repeats unit {authority_unit_id}"
            )
        unit = _json_copy(raw_unit)
        unit["source_identities"] = _normalized_authority_unit_sources(
            authority_unit_id, unit.get("source_identities")
        )
        units[authority_unit_id] = unit
    seen_pairs: set[str] = set()
    groups: dict[str, dict[str, Any]] = {}
    blocking_pair_ids: set[str] = set()
    for index, raw in enumerate(pair_actions):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"pair_actions[{index}] must be an object"
            )
        pair = _json_copy(raw)
        pair_id = _required_string(pair.get("pair_id"), f"pair_actions[{index}].pair_id")
        if pair_id in seen_pairs:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"pair_actions repeat {pair_id}"
            )
        seen_pairs.add(pair_id)
        disposition = str(pair.get("disposition") or "")
        blocks_apply = pair.get("blocks_apply")
        if not isinstance(blocks_apply, bool):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"pair_actions[{index}].blocks_apply must be boolean"
            )
        if disposition in DIRECT_DISPOSITIONS:
            if blocks_apply:
                raise ClaimEvidenceReciprocityAuthorityError(
                    f"direct pair action {pair_id} cannot block apply"
                )
            continue
        if not blocks_apply:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"non-direct pair action {pair_id} must block apply"
            )
        blocking_pair_ids.add(pair_id)
        requested_action = _queue_action(disposition)
        authority_unit_id = str(pair.get("authority_unit_id") or "").strip()
        unit = units.get(authority_unit_id) if authority_unit_id else None
        sources = _normalized_pair_sources(pair_id, pair_sources)
        if any(
            row["source_type"] == UNRESOLVED_SOURCE_TYPE for row in sources
        ):
            requested_action = QUEUE_MANUAL
        group_key: str | None = None

        if requested_action == QUEUE_EXACT_REPLAY:
            if unit is None or not unit.get("replay_eligible"):
                requested_action = QUEUE_SOURCE_RERUN
            elif len(unit.get("source_identities") or []) > 1:
                unit_sources = _json_copy(unit["source_identities"])
                source_keys = {_source_identity_key(row) for row in sources}
                unit_source_keys = {
                    _source_identity_key(row) for row in unit_sources
                }
                if not source_keys or not source_keys.issubset(unit_source_keys):
                    requested_action = QUEUE_MANUAL
                else:
                    # An aggregate reviewed candidate is indivisible: a pair may
                    # be traced to one member source, but replay authorization is
                    # bound to the package's complete, exact source generation.
                    group_key = f"aggregate:{authority_unit_id}"
                    sources = unit_sources
            else:
                unit_sources = _json_copy(unit.get("source_identities") or [])
                if len(unit_sources) != 1 or sources != unit_sources:
                    requested_action = QUEUE_MANUAL
                else:
                    sources = unit_sources

        if group_key is None:
            if len(sources) == 1:
                source_key = _source_identity_key(sources[0])
                group_key = f"source:{sha256_json(list(source_key))}"
            else:
                group_key = f"ambiguous:{sha256_json(sources)[:24]}:{pair_id}"
                requested_action = QUEUE_MANUAL

        group = groups.setdefault(
            group_key,
            {
                "group_key": group_key,
                "source_identities": sources,
                "actions": [],
                "pair_rows": [],
                "authority_unit_ids": set(),
            },
        )
        if group["source_identities"] != sources:
            requested_action = QUEUE_MANUAL
        group["actions"].append(requested_action)
        group["pair_rows"].append(pair)
        if authority_unit_id:
            group["authority_unit_ids"].add(authority_unit_id)

    tasks: list[dict[str, Any]] = []
    pair_assignments: list[dict[str, str]] = []
    for group_key, group in sorted(groups.items()):
        action = max(group["actions"], key=lambda value: QUEUE_SEVERITY[value])
        authority_ids = sorted(group["authority_unit_ids"])
        if action == QUEUE_EXACT_REPLAY and len(authority_ids) != 1:
            action = QUEUE_MANUAL
        if len(authority_ids) > 1:
            # Two reviewed generations for one source are not an invitation to
            # pick the newest timestamp.  The registered supersession must say.
            action = QUEUE_MANUAL
        pair_rows = sorted(group["pair_rows"], key=lambda row: row["pair_id"])
        pair_ids = [str(row["pair_id"]) for row in pair_rows]
        queue_id = f"SRCQ-{sha256_json(group_key)[:24]}"
        task = {
            "queue_id": queue_id,
            "group_key": group_key,
            "source_identities": group["source_identities"],
            "action": action,
            "authority_unit_ids": authority_ids,
            "pair_ids": pair_ids,
            "mismatch_counts": dict(
                sorted(
                    Counter(
                        str(row.get("mismatch_type") or "unknown")
                        for row in pair_rows
                    ).items()
                )
            ),
            "reason_codes": sorted(
                {str(row.get("reason_code") or "unknown") for row in pair_rows}
            ),
        }
        tasks.append(task)
        pair_assignments.extend(
            {"pair_id": pair_id, "queue_id": queue_id} for pair_id in pair_ids
        )

    assigned = [row["pair_id"] for row in pair_assignments]
    if len(assigned) != len(set(assigned)) or set(assigned) != blocking_pair_ids:
        raise ClaimEvidenceReciprocityAuthorityError(
            "source queues do not cover every blocking pair exactly once"
        )
    result = {
        "schema_version": SOURCE_QUEUE_SCHEMA_VERSION,
        "freeze_artifact_sha256": freeze_sha,
        "audit_artifact_sha256": audit_sha,
        "pair_action_manifest_sha256": sha256_json(
            sorted(
                (_json_copy(row) for row in pair_actions),
                key=lambda row: str(row.get("pair_id") or ""),
            )
        ),
        "authority_validation_sha256": authority["artifact_sha256"],
        "source_tasks": tasks,
        "pair_assignments": sorted(pair_assignments, key=lambda row: row["pair_id"]),
        "counts": {
            "blocking_pairs": len(blocking_pair_ids),
            "source_tasks": len(tasks),
            "exact_replay": sum(row["action"] == QUEUE_EXACT_REPLAY for row in tasks),
            "source_rerun": sum(row["action"] == QUEUE_SOURCE_RERUN for row in tasks),
            "manual": sum(row["action"] == QUEUE_MANUAL for row in tasks),
        },
    }
    return seal_authority_artifact(result)


def validate_source_work_queue(
    artifact: Mapping[str, Any],
    *,
    expected_freeze_artifact_sha256: str | None = None,
    expected_audit_artifact_sha256: str | None = None,
    expected_pair_action_manifest_sha256: str | None = None,
    expected_authority_validation_sha256: str | None = None,
) -> dict[str, Any]:
    """Authenticate a queue and recheck its pair assignment coverage."""

    value = validate_authority_artifact(
        artifact, schema_version=SOURCE_QUEUE_SCHEMA_VERSION
    )
    freeze_sha = _required_sha256(
        value.get("freeze_artifact_sha256"), "freeze_artifact_sha256"
    )
    audit_sha = _required_sha256(
        value.get("audit_artifact_sha256"), "audit_artifact_sha256"
    )
    action_manifest_sha = _required_sha256(
        value.get("pair_action_manifest_sha256"),
        "pair_action_manifest_sha256",
    )
    authority_sha = _required_sha256(
        value.get("authority_validation_sha256"),
        "authority_validation_sha256",
    )
    if (
        expected_freeze_artifact_sha256 is not None
        and freeze_sha != expected_freeze_artifact_sha256
    ):
        raise ClaimEvidenceReciprocityAuthorityError(
            "source queue is bound to another frozen input"
        )
    if (
        expected_audit_artifact_sha256 is not None
        and audit_sha != expected_audit_artifact_sha256
    ):
        raise ClaimEvidenceReciprocityAuthorityError(
            "source queue is bound to another reciprocity audit"
        )
    if (
        expected_pair_action_manifest_sha256 is not None
        and action_manifest_sha != expected_pair_action_manifest_sha256
    ):
        raise ClaimEvidenceReciprocityAuthorityError(
            "source queue is bound to another pair-action manifest"
        )
    if (
        expected_authority_validation_sha256 is not None
        and authority_sha != expected_authority_validation_sha256
    ):
        raise ClaimEvidenceReciprocityAuthorityError(
            "source queue is bound to another authority validation"
        )
    tasks = value.get("source_tasks")
    assignments = value.get("pair_assignments")
    if not isinstance(tasks, list) or not isinstance(assignments, list):
        raise ClaimEvidenceReciprocityAuthorityError(
            "source queue lacks tasks or pair assignments"
        )
    task_pairs: set[tuple[str, str]] = set()
    task_pair_count = 0
    group_keys: list[str] = []
    action_counts = Counter()
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source_tasks[{index}] must be an object"
            )
        queue_id = _required_string(task.get("queue_id"), "source task queue_id")
        group_key = _required_string(task.get("group_key"), "source task group_key")
        if queue_id != f"SRCQ-{sha256_json(group_key)[:24]}":
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source task {queue_id} does not match its group key"
            )
        action = str(task.get("action") or "")
        if action not in QUEUE_SEVERITY:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source task {queue_id} has unsupported action"
            )
        pair_ids = task.get("pair_ids")
        if not isinstance(pair_ids, list) or not pair_ids:
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source queue coverage task {queue_id} has no pairs"
            )
        normalized_pair_ids = [
            _required_string(pair_id, f"source task {queue_id} pair_id")
            for pair_id in pair_ids
        ]
        if normalized_pair_ids != sorted(set(normalized_pair_ids)):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"source task {queue_id} pair IDs are not canonical"
            )
        group_keys.append(group_key)
        action_counts[action] += 1
        task_pair_count += len(normalized_pair_ids)
        task_pairs.update((pair_id, queue_id) for pair_id in normalized_pair_ids)

    assignment_rows: list[tuple[str, str]] = []
    for index, row in enumerate(assignments):
        if not isinstance(row, Mapping):
            raise ClaimEvidenceReciprocityAuthorityError(
                f"pair_assignments[{index}] must be an object"
            )
        assignment_rows.append(
            (
                _required_string(row.get("pair_id"), "assignment pair_id"),
                _required_string(row.get("queue_id"), "assignment queue_id"),
            )
        )
    assignment_pairs = set(assignment_rows)
    if (
        group_keys != sorted(set(group_keys))
        or len(task_pairs) != task_pair_count
        or assignment_rows
        != sorted(set(assignment_rows), key=lambda row: (row[0], row[1]))
        or task_pairs != assignment_pairs
    ):
        raise ClaimEvidenceReciprocityAuthorityError(
            "source queue pair coverage is not exact"
        )
    expected_counts = {
        "blocking_pairs": len(assignment_rows),
        "source_tasks": len(tasks),
        "exact_replay": action_counts[QUEUE_EXACT_REPLAY],
        "source_rerun": action_counts[QUEUE_SOURCE_RERUN],
        "manual": action_counts[QUEUE_MANUAL],
    }
    if value.get("counts") != expected_counts:
        raise ClaimEvidenceReciprocityAuthorityError(
            "source queue counts do not match its exact coverage"
        )
    return value
