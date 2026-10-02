"""Lossless admission of an independently accepted exegesis grouping.

This does not produce a freeze or source approval. The caller must first
compile a production scope from current qualified master data. All existing
freeze, backup, review, apply and readback gates remain in force.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ClaimGroupingResponse, validate_grouping,
)
from backend.api.canonical_repository.viewpoint_foundation import canonical_json, sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import (
    CVP_GROUPING_ENVELOPE_VERSION, CvpProductionBlocked,
    validate_content_addressed, validate_grouping_envelope,
    validate_reviewed_grouping_bindings,
    accepted_grouping_canary,
    accepted_grouping_in_passage_order,
)


def validate_accepted_grouping(
    *, manifest: Mapping[str, Any], validation: Mapping[str, Any],
    acceptance: Mapping[str, Any], physical_review: Mapping[str, Any],
    expected_manifest_sha256: str, bindings: Mapping[str, Mapping[str, str]],
) -> None:
    """Validate repository input; this grants no production/source eligibility."""
    for artifact in (manifest, validation, acceptance, physical_review):
        validate_content_addressed(artifact, "artifact_sha256")
    upstream = validate_reviewed_grouping_bindings(bindings, manifest_sha256=expected_manifest_sha256)
    for role, artifact in (("manifest", manifest), ("validation", validation), ("acceptance", acceptance), ("physical_review", physical_review)):
        if upstream[role] != artifact:
            raise CvpProductionBlocked([f"{role}: supplied artifact differs from its physical binding"])
    if manifest["artifact_sha256"] != expected_manifest_sha256:
        raise CvpProductionBlocked(["reviewed grouping differs from the approved digest"])
    if manifest.get("schema_version") != "wang_exegesis_grouping_manifest_v1":
        raise CvpProductionBlocked(["unsupported reviewed grouping manifest"])
    if manifest.get("status") != "complete" or manifest.get("scope") != "Matthew only":
        raise CvpProductionBlocked(["reviewed grouping is not complete Matthew input"])
    if acceptance.get("status") != "accepted" or any(
        acceptance.get(key) is not True for key in (
            "semantic_passed", "primary_unchanged",
            "claim_source_revision_evidence_secondary_graph_unchanged",
            "current_database_graph_verified_read_only", "physical_source_shas_verified",
        )
    ):
        raise CvpProductionBlocked(["reviewed grouping lacks terminal acceptance"])
    if (
        acceptance.get("manifest_sha256") != expected_manifest_sha256
        or physical_review.get("manifest_sha256") != expected_manifest_sha256
        or acceptance.get("job_validation_sha256") != validation["artifact_sha256"]
        or acceptance.get("physical_review_evidence_sha256") != physical_review["artifact_sha256"]
    ):
        raise CvpProductionBlocked(["reviewed grouping acceptance lineage mismatch"])
    if any(acceptance.get(key) != 0 for key in ("missing", "duplicate", "foreign", "unresolved")):
        raise CvpProductionBlocked(["reviewed grouping has unresolved coverage"])
    if any(validation.get(key) != 0 for key in ("missing", "duplicate", "foreign")) or any(
        validation.get(key) is not True
        for key in ("semantic_passed", "group_ceiling_pass", "primary_unchanged", "provenance_preserved")
    ):
        raise CvpProductionBlocked(["reviewed grouping validation did not pass"])
    if physical_review.get("verbatim_errors") or physical_review.get("svg_crossing_quotes"):
        raise CvpProductionBlocked(["reviewed grouping physical quotation checks did not pass"])
    if manifest.get("unresolved") or manifest.get("unprocessed_unit_ids"):
        raise CvpProductionBlocked(["reviewed grouping retains unfinished units"])
    ids = [row["claim_id"] for row in manifest["claim_packets"]]
    groups = manifest["groups"]
    keys = [row["group_key"] for row in groups]
    counts = Counter(cid for row in groups for cid in row["claim_ids"])
    if len(ids) != len(set(ids)) or len(keys) != len(set(keys)) or set(counts) != set(ids) or any(n != 1 for n in counts.values()):
        raise CvpProductionBlocked(["reviewed grouping is not exact-once"])
    if any(not 1 <= len(row["claim_ids"]) <= 20 for row in groups):
        raise CvpProductionBlocked(["reviewed grouping exceeds the Claim ceiling"])
    if len(groups) != acceptance["groups"] or len(ids) != acceptance["claims"]:
        raise CvpProductionBlocked(["reviewed grouping denominator differs from acceptance"])


def admit_reviewed_grouping(
    *, manifest: Mapping[str, Any], validation: Mapping[str, Any],
    acceptance: Mapping[str, Any], physical_review: Mapping[str, Any],
    expected_manifest_sha256: str, scope_packet: Mapping[str, Any],
    freeze: Mapping[str, Any], bindings: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Rebind accepted groups to a qualified current scope without regrouping."""
    validate_accepted_grouping(
        manifest=manifest, validation=validation, acceptance=acceptance,
        physical_review=physical_review, expected_manifest_sha256=expected_manifest_sha256,
        bindings=bindings,
    )
    validate_content_addressed(scope_packet, "packet_sha256")
    frozen_claims = {row["claim_id"]: row for row in manifest["claim_packets"]}
    all_frozen_claims = dict(frozen_claims)
    stage = scope_packet.get("accepted_grouping_stage")
    if stage is not None:
        if stage != "canary" or freeze.get("accepted_grouping_stage") != stage:
            raise CvpProductionBlocked(["canary admission requires an explicitly phased freeze"])
        selected_group = accepted_grouping_canary(manifest)
        frozen_claims = {cid: frozen_claims[cid] for cid in selected_group["claim_ids"]}
    current_claims = {row["claim_id"]: row for row in scope_packet["claims"]}
    if len(all_frozen_claims) != len(manifest["claim_packets"]) or len(current_claims) != len(scope_packet["claims"]):
        raise CvpProductionBlocked(["duplicate Claim packet"])
    if set(frozen_claims) != set(current_claims):
        raise CvpProductionBlocked(["reviewed grouping and production scope Claim sets differ"])
    for claim_id, prior in frozen_claims.items():
        current = current_claims[claim_id]
        if any(prior.get(key) != current.get(key) for key in ("source_id", "statement")):
            raise CvpProductionBlocked([f"{claim_id}: reviewed Claim projection drift"])
        if prior["claim_revision"] != current["pinned_claim_revision"]:
            raise CvpProductionBlocked([f"{claim_id}: reviewed Claim revision drift"])
        if sorted(value if isinstance(value, str) else canonical_json(value) for value in prior.get("scripture_refs") or []) != current.get("scripture_refs", []):
            raise CvpProductionBlocked([f"{claim_id}: reviewed Claim scripture projection drift"])
        prior_steps = {step["evidence_step_id"]: step for step in prior["evidence_steps"]}
        prior_pairs = {
            (step["evidence_step_id"], fragment["fragment_id"])
            for step in prior["evidence_steps"] for fragment in step["fragments"]
        }
        current_pairs = {(step["evidence_step_id"], step["source_fragment_id"]) for step in current["evidence"]}
        if prior_pairs != current_pairs:
            raise CvpProductionBlocked([f"{claim_id}: reviewed evidence graph drift"])
        prior_quotes = {
            fragment["fragment_id"]: fragment["verbatim_excerpt"]
            for step in prior["evidence_steps"] for fragment in step["fragments"]
        }
        if any(step["verbatim_excerpt"] != prior_quotes[step["source_fragment_id"]] for step in current["evidence"]):
            raise CvpProductionBlocked([f"{claim_id}: reviewed source excerpt drift"])
        for step in current["evidence"]:
            old = prior_steps[step["evidence_step_id"]]
            if old.get("statement") != step.get("evidence_statement") or sorted(set(old.get("scripture_refs") or [])) != step.get("scripture_refs", []):
                raise CvpProductionBlocked([f"{claim_id}: reviewed EvidenceStep semantics drift"])
    group_rows = [selected_group] if stage == "canary" else accepted_grouping_in_passage_order(manifest)
    groups = [{key: group[key] for key in ("group_key", "claim_ids", "rationale")} for group in group_rows]
    counts = Counter(claim_id for group in groups for claim_id in group["claim_ids"])
    if set(counts) != set(current_claims) or any(count != 1 for count in counts.values()):
        raise CvpProductionBlocked(["reviewed grouping is not exact-once"])
    if any(not 1 <= len(group["claim_ids"]) <= 20 for group in groups):
        raise CvpProductionBlocked(["reviewed grouping exceeds the Claim ceiling"])
    grouping = ClaimGroupingResponse(scope_label=scope_packet["scope_label"], groups=groups)
    report = validate_grouping(grouping=grouping, scope_label=scope_packet["scope_label"], claim_ids=list(current_claims))
    if stage is None and (len(groups) != acceptance["groups"] or len(current_claims) != acceptance["claims"]):
        raise CvpProductionBlocked(["reviewed grouping denominator differs from acceptance"])
    if scope_packet["packet_sha256"] != freeze["scope_packet_sha256"]:
        raise CvpProductionBlocked(["production scope does not belong to the freeze"])
    body = {
        "schema_version": CVP_GROUPING_ENVELOPE_VERSION,
        "scope_label": scope_packet["scope_label"],
        "freeze_sha256": freeze["artifact_sha256"],
        "scope_packet_sha256": scope_packet["packet_sha256"],
        "cvp_policy_sha256": freeze["cvp_policy_sha256"],
        "grouping": grouping.model_dump(mode="json"), "coverage_repairs": [],
        "validation_report": report,
        "admission_kind": "independently_accepted_exegesis_grouping",
        "upstream_manifest_sha256": expected_manifest_sha256,
        "upstream_bindings": dict(bindings),
        "upstream_model": acceptance["model"],
        "upstream_reviewer_model": acceptance["reviewer_model"],
        "grouping_reexecuted": False,
    }
    if stage == "canary":
        body.update(accepted_grouping_stage="canary", parent_claim_count=len(all_frozen_claims),
                    parent_group_count=len(manifest["groups"]),
                    deferred_claim_ids=sorted(set(all_frozen_claims) - set(current_claims)))
    result = body | {"artifact_sha256": sha256_json(body)}
    validate_grouping_envelope(result, freeze=freeze)
    return result
