"""Independent structural and freshness checks for CVP partition authorization."""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
from typing import Any, Mapping

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import (
    CORPUS_COLLECTIONS,
    CVP_FREEZE_VERSION,
    CvpProductionBlocked,
    collection_fingerprint,
)
from backend.pipeline.viewpoint_partition_manifest import (
    GROUPING_PROMPT,
    PARTITION_MANIFEST_VERSION,
    grouping_claim,
    grouping_request_bytes,
)
from backend.pipeline import viewpoint_partition_manifest as planner_module
from backend.pipeline.passage_scope_attestation import validate_passage_scope_attestation
from backend.pipeline.passage_knowledge_slice import Passage


def validate_partition_manifest(
    manifest: Mapping[str, Any],
    *,
    claim_manifest: Mapping[str, Any],
    freeze: Mapping[str, Any],
    cvp_policy_sha256: str,
    store: Any | None = None,
    partition_id: str | None = None,
    scope_packet: Mapping[str, Any] | None = None,
    partition_freeze: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reject altered, stale, incomplete or oversized plans before model use."""

    findings: list[str] = []
    body = {key: value for key, value in manifest.items() if key != "artifact_sha256"}
    if manifest.get("artifact_sha256") != sha256_json(body):
        findings.append("partition artifact SHA mismatch")
    if manifest.get("schema_version") != PARTITION_MANIFEST_VERSION:
        findings.append("unsupported partition schema")
    freeze_body = {key: value for key, value in freeze.items() if key != "artifact_sha256"}
    if freeze.get("artifact_sha256") != sha256_json(freeze_body):
        findings.append("global freeze SHA mismatch")
    if manifest.get("mode") == "final" and (
        freeze.get("schema_version") != CVP_FREEZE_VERSION
        or freeze.get("status") != "frozen"
    ):
        findings.append("final manifest lacks production corpus freeze")
    claim_manifest_body = {
        key: value for key, value in claim_manifest.items() if key != "manifest_sha256"
    }
    claim_manifest_sha = sha256_json(claim_manifest_body)
    if claim_manifest.get("manifest_sha256") != claim_manifest_sha:
        findings.append("Claim manifest SHA mismatch")
    if manifest.get("global_freeze_sha256") != freeze.get("artifact_sha256"):
        findings.append("global freeze binding mismatch")
    if manifest.get("claim_manifest_sha256") != claim_manifest_sha:
        findings.append("partition Claim manifest binding mismatch")
    if freeze.get("claim_manifest_sha256") != claim_manifest_sha:
        findings.append("freeze Claim manifest binding mismatch")
    if manifest.get("cvp_policy_sha256") != cvp_policy_sha256:
        findings.append("CVP policy drift")
    if freeze.get("cvp_policy_sha256") != cvp_policy_sha256:
        findings.append("freeze CVP policy drift")
    if manifest.get("corpus_fingerprint_sha256") != freeze.get("corpus_fingerprint_sha256"):
        findings.append("corpus fingerprint binding mismatch")
    prerequisites = freeze.get("prerequisites") or {}
    for field, role in (
        ("source_reconciliation_sha256", "source_state_reconciliation"),
        ("independent_audit_sha256", "independent_audit"),
    ):
        expected = (prerequisites.get(role) or {}).get("sha256")
        if manifest.get(field) != expected or (
            manifest.get("mode") == "final" and not expected
        ):
            findings.append(f"{field} binding mismatch")
    policy = manifest.get("partition_policy") or {}
    if manifest.get("partition_policy_sha256") != sha256_json(policy):
        findings.append("partition policy SHA mismatch")
    target = int(policy.get("target_request_bytes") or 0)
    ceiling = int(policy.get("max_request_bytes") or 0)
    max_claims = int(policy.get("max_claims_per_partition") or 0)
    if not 0 < target < ceiling or max_claims < 1:
        findings.append("invalid partition policy limits")
    if manifest.get("grouping_prompt_sha256") != sha256_json(
        GROUPING_PROMPT.read_text(encoding="utf-8")
    ):
        findings.append("grouping prompt drift")
    if store is not None and manifest.get("corpus_fingerprint_sha256") != collection_fingerprint(
        store, CORPUS_COLLECTIONS
    ):
        findings.append("current corpus fingerprint drift")
    if not str(manifest.get("runner_commit") or ""):
        findings.append("missing runner commit")
    if manifest.get("planner_code_sha256") != hashlib.sha256(
        Path(planner_module.__file__).read_bytes()
    ).hexdigest():
        findings.append("partition planner code drift")

    expected_index: dict[str, dict[str, Any]] = {}
    for row in claim_manifest.get("claims") or []:
        claim_id = str(row.get("claim_id") or "")
        if not claim_id or claim_id in expected_index:
            findings.append(f"duplicate or empty Claim manifest ID: {claim_id}")
        expected_index[claim_id] = dict(row)
    if list(manifest.get("denominator") or []) != [
        expected_index[key] for key in sorted(expected_index)
    ]:
        findings.append("partition denominator differs from Claim manifest")
    source_exclusions = list(manifest.get("source_exclusions") or [])
    if source_exclusions != sorted(source_exclusions, key=lambda row: row.get("source_id", "")):
        findings.append("source exclusions are not canonical")
    source_exclusion_ids = [str(row.get("source_id") or "") for row in source_exclusions]
    if len(source_exclusion_ids) != len(set(source_exclusion_ids)) or any(
        not source_id for source_id in source_exclusion_ids
    ):
        findings.append("source exclusions contain duplicate or empty IDs")
    if any(row.get("reason_code") != "source_repair_deferred_by_owner" for row in source_exclusions):
        findings.append("source exclusion lacks stable reason code")

    owners: Counter[str] = Counter()
    rows_by_id: dict[str, Mapping[str, Any]] = {}
    owner_by_id: dict[str, str] = {}
    partitions = list(manifest.get("partitions") or [])
    partition_ids = [str(row.get("partition_id") or "") for row in partitions]
    if len(set(partition_ids)) != len(partition_ids):
        findings.append("duplicate partition ID")
    for ordinal, partition in enumerate(partitions, 1):
        name = str(partition.get("partition_id") or "")
        if name != f"p{ordinal:05d}" or partition.get("order") != ordinal:
            findings.append(f"{name}: partition order mismatch")
        claim_rows = list(partition.get("claims") or [])
        if not claim_rows:
            findings.append(f"{name}: empty partition")
        if len(claim_rows) > max_claims:
            findings.append(f"{name}: partition Claim count exceeds policy")
        if partition.get("routing_reasons") != sorted(set(partition.get("routing_reasons") or [])):
            findings.append(f"{name}: routing reasons are not canonical")
        try:
            actual_bytes = grouping_request_bytes(name, claim_rows)
            if partition.get("grouping_request_bytes") != actual_bytes:
                findings.append(f"{name}: grouping request byte count mismatch")
            if actual_bytes > target or actual_bytes > ceiling:
                findings.append(f"{name}: grouping request exceeds partition policy")
        except (KeyError, TypeError, ValueError) as exc:
            findings.append(f"{name}: invalid grouping request: {exc}")
        for row in claim_rows:
            claim_id = str(row.get("claim_id") or "")
            owners[claim_id] += 1
            rows_by_id[claim_id] = row
            owner_by_id[claim_id] = name
            pin = expected_index.get(claim_id)
            if pin is None:
                findings.append(f"{claim_id}: foreign primary Claim")
                continue
            for field in ("pinned_claim_revision", "claim_revision_sha256", "source_id"):
                if row.get(field) != pin.get(field):
                    findings.append(f"{claim_id}: {field} drift")
            candidates = list(row.get("route_candidates") or [])
            if not candidates or row.get("primary_route") != candidates[0]:
                findings.append(f"{claim_id}: invalid primary route")
            if row.get("primary_route") not in (partition.get("routing_reasons") or []):
                findings.append(f"{claim_id}: routing reason missing from partition")
    for claim_id, count in owners.items():
        if count > 1:
            findings.append(f"{claim_id}: duplicate primary ownership")

    role_artifact = manifest.get("passage_role_attestation")
    raw_units = manifest.get("passage_units") or {}
    if manifest.get("mode") == "final" and not role_artifact:
        findings.append("final manifest lacks reviewed passage roles")
    if role_artifact:
        try:
            passage_units = {
                key: [Passage(**item) for item in values]
                for key, values in raw_units.items()
            }
            admissions = validate_passage_scope_attestation(
                role_artifact,
                claims=[rows_by_id[key] for key in sorted(rows_by_id)],
                claim_manifest_sha256=claim_manifest_sha,
                passage_units=passage_units,
            )
            for claim_id, row in rows_by_id.items():
                expected_routes = sorted({
                    f"passage:{unit}"
                    for admission in admissions.get(claim_id, ())
                    for unit in admission["passage_unit_ids"]
                })
                observed_routes = sorted(
                    route for route in row.get("route_candidates") or []
                    if route.startswith("passage:")
                )
                if observed_routes != expected_routes:
                    findings.append(f"{claim_id}: passage routing differs from reviewed roles")
        except (TypeError, ValueError, KeyError) as exc:
            findings.append(f"passage role attestation invalid: {exc}")

    disposition_ids: Counter[str] = Counter()
    for row in manifest.get("dispositions") or []:
        claim_id = str(row.get("claim_id") or "")
        disposition_ids[claim_id] += 1
        pin = expected_index.get(claim_id)
        if pin is None:
            findings.append(f"{claim_id}: foreign disposition")
            continue
        for field in ("pinned_claim_revision", "claim_revision_sha256", "source_id"):
            if row.get(field) != pin.get(field):
                findings.append(f"{claim_id}: disposition {field} drift")
        if not str(row.get("reason_code") or "").strip():
            findings.append(f"{claim_id}: unexplained residual or exclusion")
        if manifest.get("mode") == "final" and row.get("disposition") != "excluded":
            findings.append(f"{claim_id}: unresolved final disposition")
    for claim_id, count in disposition_ids.items():
        if count > 1:
            findings.append(f"{claim_id}: duplicate disposition")
        if claim_id in owners:
            findings.append(f"{claim_id}: both primary and disposition")
    for claim_id, pin in expected_index.items():
        if pin.get("source_id") in source_exclusion_ids and claim_id in owners:
            findings.append(f"{claim_id}: excluded source has primary ownership")
    missing = set(expected_index) - set(owners) - set(disposition_ids)
    if missing:
        findings.append(f"missing Claim ownership/disposition: {sorted(missing)}")

    for partition in partitions:
        name = str(partition.get("partition_id") or "")
        seen_context: set[str] = set()
        for context in partition.get("context_refs") or []:
            claim_id = str(context.get("claim_id") or "")
            if claim_id in seen_context:
                findings.append(f"{name}: duplicate context {claim_id}")
            seen_context.add(claim_id)
            if claim_id not in owners or owner_by_id.get(claim_id) != context.get("primary_owner"):
                findings.append(f"{name}: invalid context owner for {claim_id}")
            if owner_by_id.get(claim_id) == name:
                findings.append(f"{name}: context repeats primary Claim {claim_id}")
            if context.get("claim_revision_sha256") != (rows_by_id.get(claim_id) or {}).get("claim_revision_sha256"):
                findings.append(f"{name}: context SHA drift for {claim_id}")

    if manifest.get("execution_contract") != {
        "registry_apply": "global_serial",
        "registry_context": "reload_current_before_each_partition",
        "completion": "requires_cross_partition_duplicate_review",
    }:
        findings.append("global Registry execution contract mismatch")

    if partition_id is not None:
        if manifest.get("mode") != "final":
            findings.append("preview manifest cannot authorize model execution")
        selected = next(
            (row for row in partitions if row.get("partition_id") == partition_id), None
        )
        if selected is None:
            findings.append(f"unknown partition ID: {partition_id}")
        if scope_packet is None or partition_freeze is None:
            findings.append("partition execution requires scope packet and local freeze")
        elif selected is not None:
            local = partition_freeze
            if local.get("scope_packet_sha256") != scope_packet.get("packet_sha256"):
                findings.append("local freeze scope packet mismatch")
            if local.get("claim_manifest_sha256") != manifest.get("claim_manifest_sha256"):
                findings.append("local freeze Claim manifest mismatch")
            if local.get("corpus_fingerprint_sha256") != manifest.get("corpus_fingerprint_sha256"):
                findings.append("local freeze corpus mismatch")
            if local.get("global_lock_path") != freeze.get("global_lock_path"):
                findings.append("local freeze uses another global Registry lock")
            if str(scope_packet.get("scope_label") or "") != partition_id:
                findings.append("scope label differs from partition ID")
            if scope_packet.get("partition_manifest_sha256") != manifest.get("artifact_sha256"):
                findings.append("scope packet partition manifest binding mismatch")
            expected_claims = {
                str(row["claim_id"]): grouping_claim(row) for row in selected["claims"]
            }
            actual_claims = {
                str(row["claim_id"]): grouping_claim(row)
                for row in scope_packet.get("claims") or []
            }
            if actual_claims != expected_claims or len(scope_packet.get("claims") or []) != len(expected_claims):
                findings.append("scope packet differs from partition ownership/projection")

    if findings:
        raise CvpProductionBlocked(sorted(set(findings)))
    return {
        "status": "valid",
        "manifest_sha256": manifest["artifact_sha256"],
        "mode": manifest.get("mode"),
        "denominator_count": len(expected_index),
        "primary_claim_count": sum(owners.values()),
        "partition_count": len(partitions),
        "disposition_count": sum(disposition_ids.values()),
        "context_count": sum(len(row.get("context_refs") or []) for row in partitions),
        "oversized_count": 0,
        "missing_count": 0,
        "duplicate_ownership_count": 0,
        "foreign_count": 0,
        "unexplained_residual_count": 0,
        "would_call_models": False,
        "master_data_mutations": 0,
    }
