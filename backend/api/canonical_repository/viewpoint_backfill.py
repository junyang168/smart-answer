"""Deterministic, fail-closed preflight for CanonicalViewpoint backfills."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .knowledge_models import evidence_fragment_ids
from .viewpoint_foundation import (
    LEDGER_BUILDER_VERSION,
    QUALITY_DIMENSIONS,
    ViewpointFoundationValidationError,
    build_coverage_snapshot,
    build_input_claim_manifest,
    semantic_record_sha,
    sha256_json,
)


SELECTION_VERSION = "wang_viewpoint_backfill_source_selection_v1"
MANIFEST_VERSION = "wang_source_universe_manifest_v1"
READINESS_VERSION = "wang_viewpoint_backfill_readiness_v1"
PACKET_VERSION = "wang_viewpoint_backfill_preflight_packet_v1"
APPLY_AUTHORIZATION_VERSION = "wang_viewpoint_backfill_apply_authorization_v1"


def _with_sha(payload: Mapping[str, Any], field: str = "artifact_sha256") -> dict[str, Any]:
    result = dict(payload)
    result[field] = sha256_json(payload)
    return result


def freeze_source_manifest(
    selection: Mapping[str, Any], sources: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Bind an explicit operator-selected cohort to current source revisions and SHAs."""

    if selection.get("schema_version") != SELECTION_VERSION:
        raise ViewpointFoundationValidationError(["unsupported backfill source selection"])
    stated = str(selection.get("selection_sha256") or "")
    unsigned = {key: value for key, value in selection.items() if key != "selection_sha256"}
    if not stated or stated != sha256_json(unsigned):
        raise ViewpointFoundationValidationError(["source selection SHA mismatch"])
    members = list(selection.get("members") or [])
    source_ids = [str(item.get("source_id") or "") for item in members]
    if not source_ids or source_ids != sorted(set(source_ids)):
        raise ViewpointFoundationValidationError(
            ["source selection members must be non-empty, sorted, and unique"]
        )
    index = {str(item.get("source_id")): dict(item) for item in sources}
    findings: list[str] = []
    rows: list[dict[str, Any]] = []
    for member in members:
        source_id = str(member["source_id"])
        source = index.get(source_id)
        if not source:
            findings.append(f"{source_id}: selected source is missing")
            continue
        source_sha = str(source.get("source_sha256") or "")
        if not source_sha:
            findings.append(f"{source_id}: source_sha256 is missing")
            continue
        revision = int(source.get("revision") or 0)
        lineage_ref = str(member.get("lineage_ref") or "")
        if member.get("latest_extraction_status") == "applied" and not lineage_ref.startswith(
            "KCS-"
        ):
            findings.append(f"{source_id}: applied source requires KCS ChangeSet lineage")
            continue
        rows.append(
            {
                "source_id": source_id,
                "source_revision_id": f"{source_id}@{revision}",
                "source_sha256": source_sha,
                "source_record_sha256": semantic_record_sha(source),
                "latest_extraction_status": str(member.get("latest_extraction_status") or "unknown"),
                "lineage_ref": lineage_ref,
            }
        )
    if findings:
        raise ViewpointFoundationValidationError(findings)
    payload = {
        "schema_version": MANIFEST_VERSION,
        "source_universe_manifest_id": f"SUM-{stated[:20]}",
        "selection_id": str(selection.get("selection_id") or ""),
        "selection_sha256": stated,
        "sources": rows,
    }
    return _with_sha(payload, "manifest_sha256")


def audit_backfill_readiness(
    *,
    manifest: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    fragments: Sequence[Mapping[str, Any]],
    evidence_steps: Sequence[Mapping[str, Any]],
    claims: Sequence[Mapping[str, Any]],
    claim_scope_ids: Sequence[str] | None = None,
    created_at: str,
) -> dict[str, Any]:
    """Audit the frozen cohort without treating historical records as latest output."""

    selected = {str(item["source_id"]): dict(item) for item in manifest.get("sources") or []}
    source_index = {str(item.get("source_id")): dict(item) for item in sources}
    fragment_index = {str(item.get("fragment_id")): dict(item) for item in fragments}
    fragments_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fragment in fragments:
        source_id = str(fragment.get("source_id") or "")
        expected = selected.get(source_id)
        if expected and fragment.get("source_sha256") == expected["source_sha256"]:
            fragments_by_source[source_id].append(dict(fragment))

    evidence_by_id = {str(item.get("evidence_step_id")): dict(item) for item in evidence_steps}
    evidence_source: dict[str, str] = {}
    evidence_issues: dict[str, list[str]] = defaultdict(list)
    for evidence_id, step in evidence_by_id.items():
        ids = evidence_fragment_ids(step)
        bound = [fragment_index.get(value) for value in ids]
        if not ids or any(item is None for item in bound):
            evidence_issues[evidence_id].append("missing_source_fragment")
            continue
        source_ids = {str(item.get("source_id") or "") for item in bound if item}
        if len(source_ids) != 1:
            evidence_issues[evidence_id].append("cross_source_evidence")
            continue
        source_id = next(iter(source_ids))
        evidence_source[evidence_id] = source_id
        expected = selected.get(source_id)
        if not expected:
            continue
        if any(item.get("source_sha256") != expected["source_sha256"] for item in bound if item):
            evidence_issues[evidence_id].append("stale_fragment_sha")
            continue

    scoped_claim_ids = set(map(str, claim_scope_ids)) if claim_scope_ids is not None else None
    claims_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected_claims: dict[str, list[str]] = defaultdict(list)
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        if scoped_claim_ids is not None and claim_id not in scoped_claim_ids:
            continue
        ids = [str(value) for value in claim.get("evidence_step_ids") or []]
        if not ids:
            continue
        missing = [value for value in ids if value not in evidence_by_id]
        bad = [value for value in ids if evidence_issues.get(value)]
        source_ids = {evidence_source[value] for value in ids if value in evidence_source}
        if source_ids and source_ids.isdisjoint(selected):
            continue
        if missing:
            rejected_claims[claim_id].append("missing_evidence_step")
        if bad:
            rejected_claims[claim_id].append("invalid_evidence_binding")
        if len(source_ids) != 1:
            rejected_claims[claim_id].append("claim_not_source_local")
        if rejected_claims[claim_id]:
            continue
        source_id = next(iter(source_ids))
        if source_id in selected:
            claims_by_source[source_id].append(dict(claim))

    rows: list[dict[str, Any]] = []
    ready_claims: list[dict[str, Any]] = []
    roles: dict[str, list[str]] = {}
    for source_id, frozen in sorted(selected.items()):
        blockers: list[str] = []
        if frozen["latest_extraction_status"] != "applied":
            blockers.append("latest_extraction_not_applied")
        source_fragments = fragments_by_source[source_id]
        source_claims = claims_by_source[source_id]
        source_evidence = {
            value
            for claim in source_claims
            for value in map(str, claim.get("evidence_step_ids") or [])
        }
        if not source_fragments:
            blockers.append("no_current_source_fragments")
        if not source_claims:
            blockers.append("no_source_local_claims")
        role_values = ["source_universe"]
        if not blockers:
            role_values.append("detailed_extraction")
            ready_claims.extend(source_claims)
        roles[source_id] = role_values
        rows.append(
            {
                "source_id": source_id,
                "source_revision_id": frozen["source_revision_id"],
                "source_sha256": frozen["source_sha256"],
                "latest_extraction_status": frozen["latest_extraction_status"],
                "fragment_count": len(source_fragments),
                "evidence_step_count": len(source_evidence),
                "claim_count": len(source_claims),
                "resolution_eligible": not blockers,
                "blocker_codes": blockers,
            }
        )

    coverage = build_coverage_snapshot(
        [source_index[source_id] for source_id in sorted(selected)],
        roles_by_source=roles,
        source_universe_manifest=manifest,
        created_at=created_at,
        coverage_status="partial",
    )
    manifest_fragments = [
        row for row in fragments if str(row.get("source_id") or "") in selected
    ]
    manifest_evidence_ids = {
        value
        for claim in ready_claims
        for value in map(str, claim.get("evidence_step_ids") or [])
    }
    claim_manifest = build_input_claim_manifest(
        ready_claims,
        [row for row in evidence_steps if str(row.get("evidence_step_id")) in manifest_evidence_ids],
        manifest_fragments,
        coverage,
    )
    summary = {
        "selected_source_count": len(rows),
        "resolution_ready_source_count": sum(row["resolution_eligible"] for row in rows),
        "blocked_source_count": sum(not row["resolution_eligible"] for row in rows),
        "resolution_ready_claim_count": len(claim_manifest["claims"]),
    }
    if scoped_claim_ids is not None:
        summary["lineage_claim_count"] = len(scoped_claim_ids)
    readiness = _with_sha(
        {
            "schema_version": READINESS_VERSION,
            "source_universe_manifest_id": manifest["source_universe_manifest_id"],
            "source_universe_manifest_sha256": manifest["manifest_sha256"],
            "created_at": created_at,
            "summary": summary,
            "sources": rows,
            "rejected_claims": [
                {"claim_id": claim_id, "blocker_codes": sorted(set(values))}
                for claim_id, values in sorted(rejected_claims.items())
                if values
            ],
        }
    )
    packet = _with_sha(
        {
            "schema_version": PACKET_VERSION,
            "source_universe_manifest_id": manifest["source_universe_manifest_id"],
            "source_universe_manifest_sha256": manifest["manifest_sha256"],
            "coverage_snapshot_id": coverage.coverage_snapshot_id,
            "coverage_snapshot_sha256": semantic_record_sha(coverage),
            "claim_manifest_sha256": claim_manifest["manifest_sha256"],
            "input_claim_count": len(claim_manifest["claims"]),
            "readiness_artifact_sha256": readiness["artifact_sha256"],
            "resolution_allowed": summary["blocked_source_count"] == 0,
            "apply_allowed": False,
            "blocker_codes": (
                ["resolution_not_completed"]
                if summary["blocked_source_count"] == 0
                else ["source_readiness_incomplete", "resolution_not_completed"]
            ),
        }
    )
    return {
        "coverage_snapshot": coverage.model_dump(mode="json"),
        "claim_manifest": claim_manifest,
        "readiness": readiness,
        "preflight_packet": packet,
    }


def authorize_backfill_apply(
    *,
    preflight_packet: Mapping[str, Any],
    resolution_ledger: Mapping[str, Any],
    quality_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Open the apply boundary only for a complete, quality-passed pinned resolution."""

    packet = dict(preflight_packet)
    stated_packet_sha = str(packet.pop("artifact_sha256", ""))
    findings: list[str] = []
    if stated_packet_sha != sha256_json(packet):
        findings.append("preflight packet SHA mismatch")
    if not preflight_packet.get("resolution_allowed"):
        findings.append("source readiness does not allow resolution")
    coverage_id = str(preflight_packet.get("coverage_snapshot_id") or "")
    ledger_id = str(resolution_ledger.get("resolution_ledger_id") or "")
    if resolution_ledger.get("coverage_snapshot_id") != coverage_id:
        findings.append("resolution ledger coverage mismatch")
    if resolution_ledger.get("input_claim_manifest_sha256") != preflight_packet.get(
        "claim_manifest_sha256"
    ):
        findings.append("resolution ledger Claim manifest mismatch")
    if resolution_ledger.get("coverage_status") != "complete":
        findings.append("resolution ledger is incomplete")
    if (resolution_ledger.get("statistics") or {}).get("input_claim_count") != preflight_packet.get(
        "input_claim_count"
    ):
        findings.append("resolution ledger Claim count mismatch")
    ledger_rows = list(resolution_ledger.get("rows") or [])
    if len(ledger_rows) != preflight_packet.get("input_claim_count") or any(
        row.get("processing_status") == "unprocessed" for row in ledger_rows
    ):
        findings.append("resolution ledger rows do not close the Claim denominator")
    ledger_keys = [
        (str(row.get("claim_id") or ""), int(row.get("pinned_claim_revision") or 0))
        for row in ledger_rows
    ]
    if len(ledger_keys) != len(set(ledger_keys)) or any(
        row.get("processing_status")
        not in {"resolved", "source_ineligible", "deferred", "unprocessed"}
        for row in ledger_rows
    ):
        findings.append("resolution ledger rows are duplicate or invalid")
    expected_statistics = {
        "input_claim_count": len(ledger_rows),
        "resolved_count": sum(row.get("processing_status") == "resolved" for row in ledger_rows),
        "source_ineligible_count": sum(
            row.get("processing_status") == "source_ineligible" for row in ledger_rows
        ),
        "deferred_count": sum(row.get("processing_status") == "deferred" for row in ledger_rows),
        "unprocessed_count": sum(
            row.get("processing_status") == "unprocessed" for row in ledger_rows
        ),
    }
    if resolution_ledger.get("statistics") != expected_statistics:
        findings.append("resolution ledger statistics mismatch")
    if quality_report.get("coverage_snapshot_id") != coverage_id:
        findings.append("quality report coverage mismatch")
    if quality_report.get("resolution_ledger_id") != ledger_id:
        findings.append("quality report ledger mismatch")
    if quality_report.get("eligibility_decision") != "pass":
        findings.append("quality report did not pass")
    dimensions = list(quality_report.get("dimensions") or [])
    if len(dimensions) != len(QUALITY_DIMENSIONS) or {
        row.get("dimension") for row in dimensions
    } != set(QUALITY_DIMENSIONS):
        findings.append("quality report does not cover every dimension")
    if quality_report.get("hard_failures") or any(
        (row.get("applicable") and row.get("status") != "pass")
        or (not row.get("applicable") and row.get("status") != "not_applicable")
        for row in dimensions
    ):
        findings.append("quality report contains a failed applicable dimension")
    ledger_artifact_payload = {
        key: resolution_ledger.get(key)
        for key in (
            "resolution_ledger_id",
            "coverage_snapshot_id",
            "input_claim_manifest_sha256",
            "eligibility_policy_version",
            "candidate_blocking_version",
            "rows",
            "statistics",
            "coverage_status",
            "build_fingerprint_sha256",
        )
    }
    ledger_sha = str(resolution_ledger.get("artifact_sha256") or "")
    if ledger_sha != sha256_json(ledger_artifact_payload):
        findings.append("resolution ledger artifact SHA mismatch")
    ledger_build_payload = {
        "builder_version": LEDGER_BUILDER_VERSION,
        "coverage_snapshot_id": resolution_ledger.get("coverage_snapshot_id"),
        "input_claim_manifest_sha256": resolution_ledger.get(
            "input_claim_manifest_sha256"
        ),
        "eligibility_policy_version": resolution_ledger.get(
            "eligibility_policy_version"
        ),
        "candidate_blocking_version": resolution_ledger.get(
            "candidate_blocking_version"
        ),
        "rows": ledger_rows,
    }
    expected_ledger_fingerprint = sha256_json(ledger_build_payload)
    if resolution_ledger.get("build_fingerprint_sha256") != expected_ledger_fingerprint:
        findings.append("resolution ledger build fingerprint mismatch")
    if ledger_id != f"VRL-{expected_ledger_fingerprint[:20]}":
        findings.append("resolution ledger id is unstable")
    quality_artifact_payload = {
        key: quality_report.get(key)
        for key in (
            "quality_report_id",
            "scope_kind",
            "scope_ids",
            "coverage_snapshot_id",
            "resolution_ledger_id",
            "input_artifact_sha256s",
            "dimensions",
            "hard_failures",
            "eligibility_decision",
            "validator_version",
            "build_fingerprint_sha256",
        )
    }
    quality_sha = str(quality_report.get("artifact_sha256") or "")
    if quality_sha != sha256_json(quality_artifact_payload):
        findings.append("quality report artifact SHA mismatch")
    quality_build_payload = {
        "validator_version": quality_report.get("validator_version"),
        "scope_kind": quality_report.get("scope_kind"),
        "scope_ids": quality_report.get("scope_ids") or [],
        "coverage_snapshot_id": quality_report.get("coverage_snapshot_id"),
        "resolution_ledger_id": quality_report.get("resolution_ledger_id"),
        "input_artifact_sha256s": quality_report.get("input_artifact_sha256s") or [],
        "dimensions": quality_report.get("dimensions") or [],
        "hard_failures": quality_report.get("hard_failures") or [],
    }
    expected_quality_fingerprint = sha256_json(quality_build_payload)
    if quality_report.get("build_fingerprint_sha256") != expected_quality_fingerprint:
        findings.append("quality report build fingerprint mismatch")
    if quality_report.get("quality_report_id") != f"VQR-{expected_quality_fingerprint[:20]}":
        findings.append("quality report id is unstable")
    if ledger_sha not in set(map(str, quality_report.get("input_artifact_sha256s") or [])):
        findings.append("quality report does not bind resolution ledger SHA")
    if findings:
        raise ViewpointFoundationValidationError(findings)
    return _with_sha(
        {
            "schema_version": APPLY_AUTHORIZATION_VERSION,
            "preflight_packet_sha256": stated_packet_sha,
            "coverage_snapshot_id": coverage_id,
            "resolution_ledger_id": ledger_id,
            "resolution_ledger_sha256": ledger_sha,
            "quality_report_id": str(quality_report.get("quality_report_id") or ""),
            "quality_report_sha256": quality_sha,
            "apply_allowed": True,
        }
    )
