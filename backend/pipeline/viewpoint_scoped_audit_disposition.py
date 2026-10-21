"""Join a full independent audit to an explicitly authorized production scope.

This is an audit *disposition*, not another independent audit. It preserves
every finding and never changes the full audit's outcome or repairs records.
"""

from __future__ import annotations

from typing import Any, Mapping

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked


def scope_audit_disposition(
    *, audit: Mapping[str, Any], manifest: Mapping[str, Any],
    audit_file_sha256: str, authorized_by: str,
) -> dict[str, Any]:
    if not authorized_by.strip():
        raise CvpProductionBlocked(["scope isolation requires explicit owner authorization"])
    if (audit.get("meta") or {}).get("scope") != "all":
        raise CvpProductionBlocked(["scope disposition requires the unfiltered full-corpus audit"])
    claims = {row["claim_id"] for row in manifest["claim_packets"]}
    sources = {row["source_id"] for row in manifest["claim_packets"]}
    fragments = {
        fragment["fragment_id"] for row in manifest["claim_packets"]
        for step in row["evidence_steps"] for fragment in step["fragments"]
    }
    layers = audit.get("layers") or {}
    if set(layers) != {"1", "2"}:
        raise CvpProductionBlocked(["scope disposition requires a complete mechanical Layer 1/2 audit"])
    first, second = layers["1"], layers["2"]
    first_findings = first.get("findings") or []
    counts = first.get("counts") or {}
    if sum(value for key, value in counts.items() if key != "pass") != len(first_findings):
        raise CvpProductionBlocked(["Layer 1 findings are truncated or incompletely disclosed"])
    anchors = second.get("claim_occurrence_anchors") or {}
    if anchors.get("checked", 0) - anchors.get("resolved", 0) != len(anchors.get("findings") or []):
        raise CvpProductionBlocked(["Layer 2 occurrence findings are truncated or incompletely disclosed"])
    blockers: list[dict[str, Any]] = []
    isolated: list[dict[str, Any]] = []

    def classify(channel: str, finding: Mapping[str, Any], affects_scope: bool):
        row = {"channel": channel, "finding": dict(finding)}
        if affects_scope:
            blockers.append(row | {"reason_code": "affects_selected_input_or_global_registry"})
        else:
            isolated.append(row | {"reason_code": "outside_selected_claim_evidence_scope"})

    for finding in first_findings:
        fragment_id = finding.get("fragment_id")
        if not fragment_id:
            raise CvpProductionBlocked(["Layer 1 finding lacks a scope-resolvable fragment ID"])
        classify("layer1.fragment", finding, fragment_id in fragments)
    binding = first.get("version_binding") or {}
    stale_by_source = binding.get("stale_by_source") or {}
    if sum(stale_by_source.values()) != binding.get("stale", 0):
        raise CvpProductionBlocked(["Layer 1 stale-source denominator is not fully disclosed"])
    for source_id, count in stale_by_source.items():
        classify("layer1.source_version", {"source_id": source_id, "fragment_count": count}, source_id in sources)
    for finding in second.get("source_document_findings") or []:
        if not finding.get("source_id"):
            raise CvpProductionBlocked(["source finding lacks source ID"])
        classify("layer2.source_document", finding, finding["source_id"] in sources)
    for finding in (second.get("claim_occurrence_anchors") or {}).get("findings") or []:
        if not finding.get("claim_id"):
            raise CvpProductionBlocked(["occurrence finding lacks Claim ID"])
        classify("layer2.claim_occurrence", finding, finding["claim_id"] in claims)
    # The full active Registry is context for identity resolution. Broken
    # structural references cannot be hidden by filtering selected Claims.
    for channel in (
        "findings", "dangling", "ambiguous_paths", "attestation_findings",
        "derived_scripture_findings", "component_locator_findings",
        "retired_evidence_findings", "struck_evidence_findings", "change_set_readback_findings",
    ):
        for finding in second.get(channel) or []:
            classify(f"layer2.{channel}", finding if isinstance(finding, Mapping) else {"detail": finding}, True)
    known_findings = {
        "attestation_findings", "derived_scripture_findings", "component_locator_findings",
        "retired_evidence_findings", "struck_evidence_findings", "change_set_readback_findings", "source_document_findings",
    }
    if any(key.endswith("_findings") and key not in known_findings and value for key, value in second.items()):
        raise CvpProductionBlocked(["Layer 2 contains an unsupported finding channel"])
    if second.get("references_dangling", 0) != len(second.get("dangling") or []):
        raise CvpProductionBlocked(["Layer 2 dangling-reference denominator mismatch"])
    reciprocity = second.get("claim_evidence_reciprocity") or {}
    if reciprocity.get("error_count") != 0 or any(reciprocity.get(key) for key in ("claim_only", "evidence_only", "dangling", "duplicate_arrays")):
        blockers.append({"channel": "layer2.reciprocity", "finding": dict(reciprocity), "reason_code": "global_integrity_failure"})
    if second.get("change_set_readback_error_count", 0) != 0:
        blockers.append({"channel": "layer2.readback", "finding": {"error_count": second["change_set_readback_error_count"]}, "reason_code": "global_integrity_failure"})
    body = {
        "schema_version": "wang_cvp_scoped_audit_disposition_v1",
        "status": "passed" if not blockers else "blocked",
        "authorized_by": authorized_by, "audit_file_sha256": audit_file_sha256,
        "grouping_manifest_sha256": manifest["artifact_sha256"],
        "selected_claim_ids": sorted(claims), "selected_fragment_ids": sorted(fragments),
        "selected_source_ids": sorted(sources), "scope_findings": blockers,
        "isolated_findings": isolated, "isolated_finding_count": len(isolated),
        "full_corpus_audit_passed": False if isolated or blockers else True,
        "master_data_mutations": 0, "independent_audit_reexecuted": False,
        "informational_retired_registry_reference_count": second.get("references_to_retired", 0),
    }
    return body | {"artifact_sha256": sha256_json(body)}
