"""Bind #364 source authority to one frozen audit and source work queue.

This module is the pure orchestration seam between the PostgreSQL freeze in
``claim_evidence_reciprocity_repair`` and the historical-package checks in
``claim_evidence_reciprocity_authority``.  It never reads PostgreSQL, invokes a
model, replays a package, or writes production state.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import (
    record_content_sha,
    sha256_json,
)
from backend.api.canonical_repository.reviewed_candidate_contract import (
    RESEARCH_BATCH_AGGREGATE,
    SOURCE_SCOPED,
)
from backend.pipeline.claim_evidence_reciprocity_authority import (
    AUTHORITY_MANIFEST_SCHEMA_VERSION,
    AUTHORITY_VALIDATION_SCHEMA_VERSION,
    QUEUE_MANUAL,
    UNRESOLVED_SOURCE_TYPE,
    build_source_work_queues,
    validate_authority_artifact,
    validate_authority_manifest,
    validate_source_work_queue,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    AUDIT_INPUT_SCHEMA_VERSION,
    AUDIT_SCHEMA_VERSION,
    AUTHORITY_SEALED_REVIEWED_SOURCE,
    MISMATCH_RECIPROCAL,
    _build_freeze_binding,
    build_reciprocity_audit,
    seal_artifact,
    validate_sealed_artifact,
)
from backend.pipeline.source_keys import document_row_key


AUTHORITY_BRIDGE_SCHEMA_VERSION = (
    "wang_claim_evidence_reciprocity_authority_bridge_v1"
)

AUTHORITY_PROVED = "single_current_reviewed_package_proves_pair"
AUTHORITY_NO_PACKAGE_PAIR = "no_eligible_reviewed_package_proves_pair"
AUTHORITY_PRODUCER_MISMATCH = "reviewed_package_did_not_produce_current_pair_endpoint"
AUTHORITY_SOURCE_MISMATCH = "reviewed_package_source_generation_does_not_cover_pair"
AUTHORITY_CONFLICT = "multiple_current_reviewed_packages_claim_pair_authority"


class ClaimEvidenceReciprocityAuthorityBridgeError(ValueError):
    """A sealed freeze cannot mechanically support its claimed authority."""


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _required_string(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            f"{field} must be non-empty"
        )
    return result


def _source_identity(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_type": _required_string(
            payload.get("source_type"), "SourceDocument source_type"
        ),
        "row_key": _required_string(
            document_row_key(payload), "SourceDocument row_key"
        ),
    }


def _source_key(value: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _required_string(value.get("source_type"), "source identity source_type"),
        _required_string(value.get("row_key"), "source identity row_key"),
    )


def _build_baseline_audit(frozen: Mapping[str, Any]) -> dict[str, Any]:
    authority_records = frozen.get("authority_records") or []
    if not isinstance(authority_records, list) or authority_records:
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority-bound audit requires an empty frozen authority_records list"
        )
    active_records = frozen.get("active_records")
    if not isinstance(active_records, list):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "frozen input active_records must be a list"
        )
    audit = build_reciprocity_audit(
        active_records,
        prerequisites=frozen.get("prerequisites") or [],
        authority_records=[],
        source_lineage_findings=frozen.get("source_lineage_findings") or [],
        review_event_ledger_count=frozen.get("review_event_ledger_count"),
        review_event_ledger_snapshot=frozen.get("review_event_ledger_snapshot"),
        freeze_binding=_build_freeze_binding(
            frozen_input_artifact_sha256=str(frozen["artifact_sha256"]),
            frozen_at=str(frozen["frozen_at"]),
            database_identity=frozen.get("database_identity") or {},
        ),
        source_lineage_identity_snapshot=frozen.get(
            "source_lineage_identity_snapshot"
        ),
    )
    if audit.get("store_snapshot") != frozen.get("active_snapshot"):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "frozen active snapshot does not match its Claim/Evidence records"
        )
    lineage_rows = [
        {
            "collection": row["collection"],
            "object_id": row["object_id"],
            "source_lineage": row["source_lineage"],
        }
        for row in active_records
    ]
    if sha256_json(lineage_rows) != frozen.get("source_lineage_snapshot_sha256"):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "frozen source-lineage snapshot does not match its active endpoints"
        )
    return audit


def _producer_ledger(
    frozen: Mapping[str, Any], audit: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    rows = frozen.get("producer_change_sets")
    if not isinstance(rows, list) or not rows:
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "frozen input requires current producer ChangeSets"
        )
    result: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"producer_change_sets[{index}] must be an object"
            )
        row = _json_copy(raw)
        change_set_id = _required_string(
            row.get("change_set_id"), f"producer_change_sets[{index}].change_set_id"
        )
        if change_set_id in result:
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"frozen producer ledger repeats {change_set_id}"
            )
        if str(row.get("status") or "") != "applied":
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"frozen producer {change_set_id} is not applied"
            )
        for field in ("fingerprint_sha256", "source_kind", "source_sha256"):
            _required_string(row.get(field), f"producer {change_set_id}.{field}")
        result[change_set_id] = row
        ordered_ids.append(change_set_id)
    if ordered_ids != sorted(ordered_ids):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "frozen producer ChangeSets are not in canonical order"
        )

    endpoint_producers: dict[str, dict[str, Any]] = {}
    for endpoint in frozen.get("active_records") or []:
        producer = endpoint.get("producer_change_set")
        if not isinstance(producer, Mapping):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                "audited endpoint lacks a producer ChangeSet"
            )
        change_set_id = _required_string(
            producer.get("change_set_id"), "endpoint producer change_set_id"
        )
        producer_copy = _json_copy(producer)
        prior = endpoint_producers.setdefault(change_set_id, producer_copy)
        if prior != producer_copy:
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"endpoint producer {change_set_id} is internally inconsistent"
            )
    if result != endpoint_producers:
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "frozen producer ledger is not the exact active-endpoint producer set"
        )
    return result


def _active_source_documents(
    frozen: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = frozen.get("active_source_documents")
    if not isinstance(rows, list):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "frozen input active_source_documents must be a list"
        )
    normalized: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    identity_body_shas: dict[tuple[str, str], str] = {}
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"active_source_documents[{index}] must be an object"
            )
        row = _json_copy(raw)
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"active_source_documents[{index}].payload must be an object"
            )
        source_id = _required_string(
            row.get("object_id"), f"active_source_documents[{index}].object_id"
        )
        if source_id in by_id or str(payload.get("source_id") or "") != source_id:
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"active SourceDocument {source_id} has ambiguous identity"
            )
        if row.get("retired") is True or row.get("retired_at") is not None:
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"active SourceDocument {source_id} is marked retired"
            )
        try:
            revision = int(row.get("revision"))
        except (TypeError, ValueError):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"active SourceDocument {source_id} revision is invalid"
            ) from None
        content_sha = _required_string(
            row.get("content_sha256"),
            f"active SourceDocument {source_id}.content_sha256",
        )
        if revision < 1 or content_sha != record_content_sha(payload):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"active SourceDocument {source_id} revision or content SHA is invalid"
            )
        identity = _source_identity(payload)
        identity_key = _source_key(identity)
        body_sha = _required_string(
            payload.get("source_body_sha256") or payload.get("source_sha256"),
            f"active SourceDocument {source_id}.source_body_sha256",
        )
        prior_body_sha = identity_body_shas.get(identity_key)
        if prior_body_sha is not None and prior_body_sha != body_sha:
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                "active source identity names conflicting source bodies: "
                f"{identity_key[0]}/{identity_key[1]}"
            )
        identity_body_shas[identity_key] = body_sha
        row["revision"] = revision
        row["content_sha256"] = content_sha
        row["source_identity"] = identity
        normalized.append(row)
        by_id[source_id] = row
    normalized.sort(key=lambda row: str(row["object_id"]))
    return normalized, by_id


def _unresolved_source(row_key: str) -> dict[str, str]:
    return {
        "source_type": UNRESOLVED_SOURCE_TYPE,
        "row_key": row_key,
    }


def _endpoint_source_identities(
    endpoint: Mapping[str, Any] | None,
    *,
    active_sources: Mapping[str, Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    if endpoint is None:
        return []
    lineage = endpoint.get("source_lineage")
    if not isinstance(lineage, Mapping):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "audited endpoint source_lineage must be an object"
        )
    source_ids: set[str] = set()
    direct_ids = lineage.get("source_document_ids") or []
    if not isinstance(direct_ids, list):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "endpoint source_document_ids must be a list"
        )
    source_ids.update(
        _required_string(value, "endpoint source_document_id")
        for value in direct_ids
    )

    fragments = lineage.get("source_fragments") or []
    if not isinstance(fragments, list):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "endpoint source_fragments must be a list"
        )
    for index, fragment in enumerate(fragments):
        if not isinstance(fragment, Mapping):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"endpoint source_fragments[{index}] must be an object"
            )
        source_id = str(fragment.get("source_document_id") or "").strip()
        if source_id:
            source_ids.add(source_id)

    document_proofs = lineage.get("source_documents") or []
    if not isinstance(document_proofs, list):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "endpoint source_documents must be a list"
        )
    proof_by_id: dict[str, Mapping[str, Any]] = {}
    for index, proof in enumerate(document_proofs):
        if not isinstance(proof, Mapping):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"endpoint source_documents[{index}] must be an object"
            )
        source_id = _required_string(
            proof.get("object_id"), f"endpoint source_documents[{index}].object_id"
        )
        if source_id in proof_by_id:
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                f"endpoint source lineage repeats SourceDocument {source_id}"
            )
        proof_by_id[source_id] = proof
        source_ids.add(source_id)

    identities: dict[tuple[str, str], dict[str, str]] = {}
    for source_id in sorted(source_ids):
        active = active_sources.get(source_id)
        proof = proof_by_id.get(source_id)
        if active is None:
            identity = _unresolved_source(f"missing:{source_id}")
        else:
            if (
                proof is None
                or int(proof.get("revision") or 0) != int(active["revision"])
                or str(proof.get("content_sha256") or "")
                != str(active["content_sha256"])
                or proof.get("retired") is True
            ):
                raise ClaimEvidenceReciprocityAuthorityBridgeError(
                    f"endpoint SourceDocument proof drifted for {source_id}"
                )
            identity = _json_copy(active["source_identity"])
        identities[_source_key(identity)] = identity

    for finding in findings:
        code = _required_string(finding.get("code"), "source lineage finding code")
        referenced_id = _required_string(
            finding.get("referenced_id"), "source lineage finding referenced_id"
        )
        identity = _unresolved_source(f"finding:{code}:{referenced_id}")
        identities[_source_key(identity)] = identity
    return [identities[key] for key in sorted(identities)]


def _pair_sources(
    audit: Mapping[str, Any],
    *,
    active_sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    finding_by_endpoint: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in audit.get("source_lineage_findings") or []:
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                "source_lineage_findings must contain objects"
            )
        key = (
            _required_string(raw.get("collection"), "lineage finding collection"),
            _required_string(raw.get("object_id"), "lineage finding object_id"),
        )
        finding_by_endpoint[key].append(_json_copy(raw))

    result: dict[str, list[dict[str, str]]] = {}
    for pair in audit.get("pairs") or []:
        pair_id = _required_string(pair.get("pair_id"), "audit pair_id")
        identities: dict[tuple[str, str], dict[str, str]] = {}
        endpoint_specs = (
            (
                "claims",
                str(pair.get("claim_id") or ""),
                pair.get("claim_endpoint"),
            ),
            (
                "evidence_steps",
                str(pair.get("evidence_step_id") or ""),
                pair.get("evidence_endpoint"),
            ),
        )
        for collection, object_id, endpoint in endpoint_specs:
            rows = _endpoint_source_identities(
                endpoint if isinstance(endpoint, Mapping) else None,
                active_sources=active_sources,
                findings=finding_by_endpoint.get((collection, object_id), ()),
            )
            for identity in rows:
                identities[_source_key(identity)] = identity
        result[pair_id] = [identities[key] for key in sorted(identities)]
    return dict(sorted(result.items()))


def _unit_source_matches_pair(
    unit: Mapping[str, Any], pair_sources: Sequence[Mapping[str, Any]]
) -> bool:
    observed = {_source_key(row) for row in pair_sources}
    if not observed or any(key[0] == UNRESOLVED_SOURCE_TYPE for key in observed):
        return False
    unit_sources = {
        _source_key(row) for row in unit.get("source_identities") or []
    }
    scope_kind = str(unit.get("scope_kind") or "")
    if scope_kind == SOURCE_SCOPED:
        return observed == unit_sources and len(unit_sources) == 1
    if scope_kind == RESEARCH_BATCH_AGGREGATE:
        return bool(unit_sources) and observed.issubset(unit_sources)
    return False


def _pair_authority(
    baseline_audit: Mapping[str, Any],
    authority_validation: Mapping[str, Any],
    pair_sources: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    units_by_pair: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for unit in authority_validation.get("packages") or []:
        if not isinstance(unit, Mapping) or unit.get("replay_eligible") is not True:
            continue
        for pair in unit.get("claim_evidence_pairs") or []:
            if not isinstance(pair, Mapping):
                raise ClaimEvidenceReciprocityAuthorityBridgeError(
                    "eligible authority unit has malformed pair coverage"
                )
            key = (
                _required_string(pair.get("claim_id"), "authority pair claim_id"),
                _required_string(
                    pair.get("evidence_step_id"), "authority pair evidence_step_id"
                ),
            )
            units_by_pair[key].append(unit)

    authority_records: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for pair in baseline_audit.get("pairs") or []:
        if pair.get("mismatch_type") == MISMATCH_RECIPROCAL:
            continue
        pair_id = _required_string(pair.get("pair_id"), "audit pair_id")
        key = (
            _required_string(pair.get("claim_id"), "audit pair claim_id"),
            _required_string(
                pair.get("evidence_step_id"), "audit pair evidence_step_id"
            ),
        )
        covering = units_by_pair.get(key, [])
        endpoint_producers = {
            str((endpoint.get("producer_change_set") or {}).get("change_set_id") or "")
            for endpoint in (
                pair.get("claim_endpoint"),
                pair.get("evidence_endpoint"),
            )
            if isinstance(endpoint, Mapping)
        }
        source_matched = [
            unit
            for unit in covering
            if _unit_source_matches_pair(unit, pair_sources.get(pair_id, ()))
        ]
        qualified = [
            unit
            for unit in source_matched
            if str((unit.get("historical_change_set") or {}).get("change_set_id") or "")
            in endpoint_producers
        ]
        covering_ids = sorted(str(unit["authority_unit_id"]) for unit in covering)
        qualified_ids = sorted(str(unit["authority_unit_id"]) for unit in qualified)
        if len(qualified) == 1:
            unit = qualified[0]
            historical = unit["historical_change_set"]
            authority_record = {
                "claim_id": key[0],
                "evidence_step_id": key[1],
                "authority_class": AUTHORITY_SEALED_REVIEWED_SOURCE,
                "authority_unit_id": str(unit["authority_unit_id"]),
                "package_id": str(unit["package_id"]),
                "package_sha256": str(unit["effective_canonical_sha256"]),
                "reviewed_artifact_sha256": str(
                    unit["effective_reviewed_candidate_artifact_sha256"]
                ),
                "historical_change_set_id": str(historical["change_set_id"]),
                "historical_change_set_fingerprint_sha256": str(
                    historical["fingerprint_sha256"]
                ),
                "review_completion": str(unit["review_completion"]),
                "scope_kind": str(unit["scope_kind"]),
                "claim_evidence_pairs_sha256": str(
                    unit["claim_evidence_pairs_sha256"]
                ),
                "source_identities": _json_copy(unit["source_identities"]),
                "authority_validation_sha256": str(
                    authority_validation["artifact_sha256"]
                ),
            }
            authority_records.append(authority_record)
            reason_code = AUTHORITY_PROVED
        elif len(qualified) > 1:
            reason_code = AUTHORITY_CONFLICT
        elif not covering:
            reason_code = AUTHORITY_NO_PACKAGE_PAIR
        elif not source_matched:
            reason_code = AUTHORITY_SOURCE_MISMATCH
        else:
            reason_code = AUTHORITY_PRODUCER_MISMATCH
        decisions.append(
            {
                "pair_id": pair_id,
                "claim_id": key[0],
                "evidence_step_id": key[1],
                "reason_code": reason_code,
                "covering_authority_unit_ids": covering_ids,
                "qualified_authority_unit_ids": qualified_ids,
            }
        )
    authority_records.sort(key=lambda row: (row["claim_id"], row["evidence_step_id"]))
    decisions.sort(key=lambda row: row["pair_id"])
    return authority_records, decisions


def _pair_actions(
    audit: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    pair_sources: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    fields = (
        "pair_id",
        "claim_id",
        "evidence_step_id",
        "mismatch_type",
        "authority_class",
        "reason_code",
        "disposition",
        "blocks_apply",
    )
    decision_by_pair = {
        str(row.get("pair_id") or ""): row for row in decisions
    }
    actions: list[dict[str, Any]] = []
    for pair in audit.get("pairs") or []:
        if pair.get("mismatch_type") == MISMATCH_RECIPROCAL:
            continue
        action = {field: _json_copy(pair.get(field)) for field in fields}
        authority_record = pair.get("authority_record")
        if isinstance(authority_record, Mapping):
            authority_unit_id = str(
                authority_record.get("authority_unit_id") or ""
            ).strip()
            if authority_unit_id:
                action["authority_unit_id"] = authority_unit_id
                action["authority_record_sha256"] = sha256_json(authority_record)
        decision = decision_by_pair.get(str(pair.get("pair_id") or "")) or {}
        decision_reason = str(decision.get("reason_code") or "")
        if decision_reason:
            action["authority_decision_reason_code"] = decision_reason
        sources = pair_sources.get(str(pair.get("pair_id") or ""), ())
        source_keys = {_source_key(row) for row in sources}
        source_ambiguous = (
            any(key[0] == UNRESOLVED_SOURCE_TYPE for key in source_keys)
            or (
                len(source_keys) != 1
                and str((authority_record or {}).get("scope_kind") or "")
                != RESEARCH_BATCH_AGGREGATE
            )
        )
        if decision_reason == AUTHORITY_CONFLICT or source_ambiguous:
            action["audit_disposition"] = action["disposition"]
            action["audit_reason_code"] = action["reason_code"]
            action["disposition"] = QUEUE_MANUAL
            action["reason_code"] = (
                AUTHORITY_CONFLICT
                if decision_reason == AUTHORITY_CONFLICT
                else AUTHORITY_SOURCE_MISMATCH
            )
        actions.append(action)
    return sorted(actions, key=lambda row: str(row["pair_id"]))


def _pair_action_sha256(actions: Sequence[Mapping[str, Any]]) -> str:
    return sha256_json(
        sorted(
            (_json_copy(row) for row in actions),
            key=lambda row: str(row.get("pair_id") or ""),
        )
    )


def _derive_authority_bound_audit_and_queue(
    frozen_input: Mapping[str, Any],
    authority_manifest: Mapping[str, Any],
    *,
    read_bytes: Callable[[Path], bytes] | None = None,
) -> dict[str, Any]:
    """Build one cross-sealed authority validation, audit, and source queue."""

    frozen = validate_sealed_artifact(
        frozen_input, expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION
    )
    authenticated_manifest = validate_authority_artifact(
        authority_manifest,
        schema_version=AUTHORITY_MANIFEST_SCHEMA_VERSION,
    )
    baseline_audit = _build_baseline_audit(frozen)
    producer_ledger = _producer_ledger(frozen, baseline_audit)
    active_source_documents, sources_by_id = _active_source_documents(frozen)
    authority_validation = validate_authority_manifest(
        authenticated_manifest,
        historical_change_sets=producer_ledger,
        active_source_documents=active_source_documents,
        read_bytes=read_bytes,
    )
    authority_validation = validate_authority_artifact(
        authority_validation,
        schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION,
    )
    pair_sources = _pair_sources(
        baseline_audit,
        active_sources=sources_by_id,
    )
    authority_records, authority_decisions = _pair_authority(
        baseline_audit,
        authority_validation,
        pair_sources,
    )
    audit = build_reciprocity_audit(
        frozen.get("active_records") or [],
        prerequisites=frozen.get("prerequisites") or [],
        authority_records=authority_records,
        source_lineage_findings=frozen.get("source_lineage_findings") or [],
        review_event_ledger_count=frozen.get("review_event_ledger_count"),
        review_event_ledger_snapshot=frozen.get("review_event_ledger_snapshot"),
        freeze_binding=_build_freeze_binding(
            frozen_input_artifact_sha256=str(frozen["artifact_sha256"]),
            frozen_at=str(frozen["frozen_at"]),
            database_identity=frozen.get("database_identity") or {},
        ),
        source_lineage_identity_snapshot=frozen.get(
            "source_lineage_identity_snapshot"
        ),
    )
    before_pairs = [
        (row["pair_id"], row["claim_id"], row["evidence_step_id"], row["mismatch_type"])
        for row in baseline_audit["pairs"]
    ]
    after_pairs = [
        (row["pair_id"], row["claim_id"], row["evidence_step_id"], row["mismatch_type"])
        for row in audit["pairs"]
    ]
    if before_pairs != after_pairs or audit["store_snapshot"] != frozen["active_snapshot"]:
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority classification changed frozen pair or snapshot identity"
        )
    pair_actions = _pair_actions(audit, authority_decisions, pair_sources)
    pair_action_sha = _pair_action_sha256(pair_actions)
    source_queue = build_source_work_queues(
        pair_actions,
        freeze_artifact_sha256=str(frozen["artifact_sha256"]),
        audit_artifact_sha256=str(audit["artifact_sha256"]),
        authority_validation=authority_validation,
        pair_sources=pair_sources,
    )
    validate_source_work_queue(
        source_queue,
        expected_freeze_artifact_sha256=str(frozen["artifact_sha256"]),
        expected_audit_artifact_sha256=str(audit["artifact_sha256"]),
        expected_pair_action_manifest_sha256=pair_action_sha,
        expected_authority_validation_sha256=str(
            authority_validation["artifact_sha256"]
        ),
    )
    result = seal_artifact(
        {
            "schema_version": AUTHORITY_BRIDGE_SCHEMA_VERSION,
            "derivation_inputs": {
                "frozen_input": frozen,
                "authority_manifest": authenticated_manifest,
            },
            "roots": {
                "frozen_input_sha256": str(frozen["artifact_sha256"]),
                "authority_manifest_sha256": str(
                    authority_validation["authority_manifest_sha256"]
                ),
                "authority_validation_sha256": str(
                    authority_validation["artifact_sha256"]
                ),
                "authority_records_sha256": sha256_json(authority_records),
                "audit_sha256": str(audit["artifact_sha256"]),
                "pair_sources_sha256": sha256_json(pair_sources),
                "pair_actions_sha256": pair_action_sha,
                "source_work_queue_sha256": str(source_queue["artifact_sha256"]),
            },
            "authority_validation": authority_validation,
            "authority_records": authority_records,
            "pair_authority_decisions": authority_decisions,
            "pair_sources": pair_sources,
            "pair_actions": pair_actions,
            "audit": audit,
            "source_work_queue": source_queue,
        }
    )
    return result


def build_authority_bound_audit_and_queue(
    frozen_input: Mapping[str, Any],
    authority_manifest: Mapping[str, Any],
    *,
    read_bytes: Callable[[Path], bytes] | None = None,
) -> dict[str, Any]:
    """Build and independently rederive one authority bridge."""

    result = _derive_authority_bound_audit_and_queue(
        frozen_input,
        authority_manifest,
        read_bytes=read_bytes,
    )
    return validate_authority_bound_audit_and_queue(
        result,
        frozen_input=frozen_input,
        authority_manifest=authority_manifest,
        read_bytes=read_bytes,
        expected_frozen_input_sha256=str(result["roots"]["frozen_input_sha256"]),
        expected_authority_manifest_sha256=str(
            result["roots"]["authority_manifest_sha256"]
        ),
    )


def validate_authority_bound_audit_and_queue(
    artifact: Mapping[str, Any],
    *,
    frozen_input: Mapping[str, Any] | None = None,
    authority_manifest: Mapping[str, Any] | None = None,
    read_bytes: Callable[[Path], bytes] | None = None,
    expected_frozen_input_sha256: str | None = None,
    expected_authority_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Authenticate and deterministically rederive every bridge projection."""

    value = validate_sealed_artifact(
        artifact, expected_schema_version=AUTHORITY_BRIDGE_SCHEMA_VERSION
    )
    roots = value.get("roots")
    if not isinstance(roots, Mapping):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority bridge roots must be an object"
        )
    authority = validate_authority_artifact(
        value.get("authority_validation") or {},
        schema_version=AUTHORITY_VALIDATION_SCHEMA_VERSION,
    )
    audit = validate_sealed_artifact(
        value.get("audit") or {}, expected_schema_version=AUDIT_SCHEMA_VERSION
    )
    actions = value.get("pair_actions")
    pair_sources = value.get("pair_sources")
    authority_records = value.get("authority_records")
    decisions = value.get("pair_authority_decisions")
    if (
        not isinstance(actions, list)
        or not isinstance(pair_sources, Mapping)
        or not isinstance(authority_records, list)
        or not isinstance(decisions, list)
    ):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority bridge lacks actions, sources, decisions, or records"
        )
    expected_actions = _pair_actions(audit, decisions, pair_sources)
    if actions != expected_actions or authority_records != audit.get("authority_records"):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority bridge audit projections do not match"
        )
    action_sha = _pair_action_sha256(actions)
    source_queue = validate_source_work_queue(
        value.get("source_work_queue") or {},
        expected_freeze_artifact_sha256=str(roots.get("frozen_input_sha256") or ""),
        expected_audit_artifact_sha256=str(audit["artifact_sha256"]),
        expected_pair_action_manifest_sha256=action_sha,
        expected_authority_validation_sha256=str(authority["artifact_sha256"]),
    )
    expected_roots = {
        "frozen_input_sha256": str(source_queue["freeze_artifact_sha256"]),
        "authority_manifest_sha256": str(authority["authority_manifest_sha256"]),
        "authority_validation_sha256": str(authority["artifact_sha256"]),
        "authority_records_sha256": sha256_json(authority_records),
        "audit_sha256": str(audit["artifact_sha256"]),
        "pair_sources_sha256": sha256_json(pair_sources),
        "pair_actions_sha256": action_sha,
        "source_work_queue_sha256": str(source_queue["artifact_sha256"]),
    }
    if dict(roots) != expected_roots:
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority bridge cross-artifact roots do not match"
        )
    if (
        expected_frozen_input_sha256 is not None
        and roots.get("frozen_input_sha256") != expected_frozen_input_sha256
    ):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority bridge is bound to another frozen input"
        )
    if (
        expected_authority_manifest_sha256 is not None
        and roots.get("authority_manifest_sha256")
        != expected_authority_manifest_sha256
    ):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority bridge is bound to another authority manifest"
        )
    derivation_inputs = value.get("derivation_inputs")
    if not isinstance(derivation_inputs, Mapping):
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority bridge lacks its deterministic derivation inputs"
        )
    embedded_frozen = validate_sealed_artifact(
        derivation_inputs.get("frozen_input") or {},
        expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION,
    )
    embedded_manifest = validate_authority_artifact(
        derivation_inputs.get("authority_manifest") or {},
        schema_version=AUTHORITY_MANIFEST_SCHEMA_VERSION,
    )
    if frozen_input is not None:
        supplied_frozen = validate_sealed_artifact(
            frozen_input,
            expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION,
        )
        if supplied_frozen != embedded_frozen:
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                "authority bridge derivation uses another frozen input"
            )
    if authority_manifest is not None:
        supplied_manifest = validate_authority_artifact(
            authority_manifest,
            schema_version=AUTHORITY_MANIFEST_SCHEMA_VERSION,
        )
        if supplied_manifest != embedded_manifest:
            raise ClaimEvidenceReciprocityAuthorityBridgeError(
                "authority bridge derivation uses another authority manifest"
            )
    expected = _derive_authority_bound_audit_and_queue(
        embedded_frozen,
        embedded_manifest,
        read_bytes=read_bytes,
    )
    if value != expected:
        raise ClaimEvidenceReciprocityAuthorityBridgeError(
            "authority bridge differs from its deterministic frozen-input derivation"
        )
    return value
