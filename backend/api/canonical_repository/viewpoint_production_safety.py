"""Fail-closed production boundary for CanonicalViewpoint resolution.

The semantic runners intentionally remain small.  This module supplies the
operational authority they may not infer from a packet filename or a local
``current-state.json``: one content-addressed input freeze, one grouping bound
to that freeze, and one apply authorization backed by a readable database
backup.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from contextlib import contextmanager
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import fcntl
from backend.pipeline.viewpoint_validation_context import current_check, typed_records

from backend.api.canonical_repository.knowledge_models import (
    ClaimRecord,
    EvidenceStepRecord,
    SourceFragmentRecord,
    evidence_fragment_ids,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    canonical_json,
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ClaimGroupingResponse,
    RESIDUAL_GROUP_KEY,
    validate_grouping,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    IDENTITY_ELIGIBLE_CLAIM_REVIEW_STATUSES,
    IDENTITY_TERMINALLY_EXCLUDED_CLAIM_REVIEW_STATUSES,
    ReviewClaim,
    claim_evidence_integrity_findings,
)


CVP_FREEZE_VERSION = "wang_cvp_production_freeze_v1"
CVP_GROUPING_ENVELOPE_VERSION = "wang_canonical_viewpoint_grouping_envelope_v2"
CVP_CANARY_SELECTION_VERSION = "wang_cvp_canary_selection_v2"
CVP_APPLY_AUTHORIZATION_VERSION = "wang_cvp_apply_authorization_v1"

CORPUS_COLLECTIONS = (
    "source_documents",
    "source_fragments",
    "claims",
    "evidence_steps",
    "claim_relations",
)
REGISTRY_COLLECTIONS = (
    "canonical_viewpoints",
    "viewpoint_revisions",
    "viewpoint_claim_links",
    "viewpoint_relations",
    "viewpoint_structures",
    "viewpoint_structure_revisions",
    "argument_routes",
    "argument_route_revisions",
    "argument_route_attestations",
)


class CvpProductionBlocked(ValueError):
    def __init__(self, findings: Sequence[str]):
        self.findings = list(findings)
        super().__init__("CVP production blocked: " + " | ".join(self.findings))


def file_sha256(path: Path) -> str:
    from backend.pipeline.viewpoint_validation_context import active_context
    ctx = active_context()
    if ctx is not None:
        return ctx.pin(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_content_addressed(payload: Mapping[str, Any], field: str) -> str:
    stated = str(payload.get(field) or "")
    body = {key: value for key, value in payload.items() if key != field}
    if not stated or stated != sha256_json(body):
        raise CvpProductionBlocked([f"invalid {field}"])
    return stated


def collection_fingerprint(store: Any, collections: Sequence[str]) -> str:
    return sha256_json(collection_cut(store, collections))


def collection_cut(store: Any, collections: Sequence[str]) -> dict[str, list[str]]:
    from backend.pipeline.viewpoint_validation_context import active_context, current_registry_view
    if active_context() is not None and hasattr(store, 'database_url'):
        view = current_registry_view(store)
        return {c: view.canonical_cut(c) for c in collections}
    cuts: dict[str, list[str]] = {}
    for collection in collections:
        cuts[collection] = sorted(
            sha256_json(dict(row)) for row in store.list_records(collection)
        )
    return cuts


def build_apply_intent(
    *,
    plan: Any,
    store: Any,
    batch_identity: Mapping[str, Any],
    freeze_sha256: str,
    grouping_sha256: str,
    expected_pre_registry_fingerprint_sha256: str,
) -> dict[str, Any]:
    pre_cut = collection_cut(store, REGISTRY_COLLECTIONS)
    if sha256_json(pre_cut) != expected_pre_registry_fingerprint_sha256:
        raise CvpProductionBlocked(["Registry drifted before apply intent"])
    post_cut = deepcopy(pre_cut)
    for operation in plan.operations:
        if operation.collection not in REGISTRY_COLLECTIONS:
            continue
        current = store.get_record_state(operation.collection, operation.object_id)
        if current is not None and not current["retired"]:
            current_sha = sha256_json(current["payload"])
            try:
                post_cut[operation.collection].remove(current_sha)
            except ValueError as exc:
                raise CvpProductionBlocked(
                    [f"{operation.collection}/{operation.object_id}: pre-cut row missing"]
                ) from exc
        if operation.operation != "retire":
            payload = dict(operation.payload)
            payload["revision"] = operation.after_revision
            post_cut[operation.collection].append(sha256_json(payload))
            post_cut[operation.collection].sort()
    body = {
        "schema_version": "wang_cvp_apply_intent_v1",
        "batch_identity": dict(batch_identity),
        "freeze_sha256": freeze_sha256,
        "grouping_sha256": grouping_sha256,
        "change_set_sha256": plan.fingerprint_sha256,
        "pre_registry_cut": pre_cut,
        "pre_registry_fingerprint_sha256": sha256_json(pre_cut),
        "expected_post_registry_cut": post_cut,
        "expected_post_registry_fingerprint_sha256": sha256_json(post_cut),
    }
    return body | {"artifact_sha256": sha256_json(body)}


def validate_registry_transition(intent: Mapping[str, Any], *, store: Any) -> str:
    validate_content_addressed(intent, "artifact_sha256")
    if intent.get("schema_version") != "wang_cvp_apply_intent_v1":
        raise CvpProductionBlocked(["unsupported CVP apply intent"])
    expected = intent.get("expected_post_registry_cut") or {}
    observed = collection_cut(store, REGISTRY_COLLECTIONS)
    if observed != expected:
        raise CvpProductionBlocked(
            ["Registry post-cut contains changes outside the authorized CVP plan"]
        )
    return sha256_json(observed)


@current_check
def _validate_scope_packet(scope_packet: Mapping[str, Any], store: Any) -> list[str]:
    findings: list[str] = []
    try:
        validate_content_addressed(scope_packet, "packet_sha256")
    except CvpProductionBlocked as exc:
        findings.extend(exc.findings)
        return findings
    blocked_claim_ids = [
        str(item.get("claim_id") or "")
        for item in scope_packet.get("blocked_claims") or []
    ]
    if blocked_claim_ids:
        findings.append("scope packet contains blocked Claims")
    if len(blocked_claim_ids) != len(set(blocked_claim_ids)):
        findings.append("scope packet contains duplicate blocked Claim IDs")

    current_claims = typed_records(store,'claims',ClaimRecord,'claim_id')
    current_evidence = typed_records(store,'evidence_steps',EvidenceStepRecord,'evidence_step_id')
    current_fragments = typed_records(store,'source_fragments',SourceFragmentRecord,'fragment_id')
    source_records = store.list_records("source_documents")
    context_sources = {str(row.get("source_id") or ""): row for row in source_records}
    context_cache = {}
    current_sources = {
        str(row.get("source_id") or ""): str(row.get("source_sha256") or "")
        for row in source_records
    }
    packet_claim_ids: list[str] = []
    for raw in scope_packet.get("claims") or []:
        claim = ReviewClaim.model_validate(raw)
        packet_claim_ids.append(claim.claim_id)
        current = current_claims.get(claim.claim_id)
        if current is None:
            findings.append(f"{claim.claim_id}: Claim missing from current corpus")
            continue
        if current.revision != claim.pinned_claim_revision:
            findings.append(f"{claim.claim_id}: Claim revision drift")
        if semantic_record_sha(current) != claim.claim_revision_sha256:
            findings.append(f"{claim.claim_id}: Claim content drift")
        expected_claim_projection = {
            "statement": current.statement,
            "attribution": current.attribution,
            "scripture_refs": sorted(
                value if isinstance(value, str) else canonical_json(value)
                for value in current.scripture_refs
            ),
            "review_status": current.review_status,
        }
        actual_claim_projection = {
            "statement": claim.statement,
            "attribution": claim.attribution,
            "scripture_refs": claim.scripture_refs,
            "review_status": claim.review_status,
        }
        if actual_claim_projection != expected_claim_projection:
            findings.append(
                f"{claim.claim_id}: scope packet Claim projection differs from current Claim"
            )
        if current.review_status not in IDENTITY_ELIGIBLE_CLAIM_REVIEW_STATUSES:
            findings.append(
                f"{claim.claim_id}: Claim review status {current.review_status!r} "
                "is not identity eligible"
            )
        projected_evidence_ids = {item.evidence_step_id for item in claim.evidence}
        if projected_evidence_ids != set(current.evidence_step_ids):
            findings.append(
                f"{claim.claim_id}: scope packet evidence set differs from current Claim"
            )
        expected_evidence_pairs = {
            (evidence_id, fragment_id)
            for evidence_id in current.evidence_step_ids
            if (evidence := current_evidence.get(evidence_id)) is not None
            for fragment_id in evidence_fragment_ids(evidence)
        }
        packet_evidence_pairs = {
            (item.evidence_step_id, item.source_fragment_id) for item in claim.evidence
        }
        if packet_evidence_pairs != expected_evidence_pairs:
            findings.append(
                f"{claim.claim_id}: scope packet evidence/fragment projection differs "
                "from current corpus"
            )
        for item in claim.evidence:
            if (scope_packet.get("source_context_policy") == "same_registered_live_paragraph_v1"
                    and item.source_modality != "visual" and item.source_context is None):
                findings.append(f"{claim.claim_id}: required source paragraph context missing")
            if item.source_context is not None:
                from backend.pipeline.viewpoint_evidence_source_context import verify_source_context, load_context_source
                try:
                    source = context_sources[item.source_id]
                    if item.source_id not in context_cache:
                        context_cache[item.source_id] = load_context_source(source)
                    verify_source_context(item, source, context_cache[item.source_id])
                except (KeyError, OSError, ValueError) as exc:
                    findings.append(f"{claim.claim_id}: source context validation failed: {exc}")
            evidence = current_evidence.get(item.evidence_step_id)
            fragment = current_fragments.get(item.source_fragment_id)
            if evidence is None or fragment is None:
                findings.append(
                    f"{claim.claim_id}: scope packet references missing evidence/fragment"
                )
                continue
            expected_evidence_projection = {
                "evidence_statement": evidence.statement,
                "discourse_role": evidence.discourse_role,
                "scripture_refs": sorted(set(evidence.scripture_refs)),
                "support_eligibility": evidence.support_eligibility,
                "source_id": fragment.source_id,
                "paragraph_key": fragment.paragraph_key,
                "media_time": fragment.media_time,
                "verbatim_excerpt": fragment.verbatim_excerpt,
                "citation_id": str(fragment.citation_id or ""),
                "source_sha256": str(fragment.source_sha256 or ""),
                "anchor_state": fragment.anchor_state,
            }
            actual_evidence_projection = {
                "evidence_statement": item.evidence_statement,
                "discourse_role": item.discourse_role,
                "scripture_refs": item.scripture_refs,
                "support_eligibility": item.support_eligibility,
                "source_id": item.source_id,
                "paragraph_key": item.paragraph_key,
                "media_time": item.media_time,
                "verbatim_excerpt": item.verbatim_excerpt,
                "citation_id": item.citation_id,
                "source_sha256": item.source_sha256,
                "anchor_state": item.anchor_state,
            }
            # SVG evidence must not silently become a spoken quotation. Compare
            # the typed provenance, including literal facts, on every freeze
            # validation, not only when initially compiling the packet.
            for field in ("source_modality", "visual_locator", "visual_block_sha256", "visual_facts"):
                expected_evidence_projection[field] = getattr(fragment, field, None)
                actual_evidence_projection[field] = getattr(item, field, None)
            # Historical spoken fragments use either None or "spoken"; the
            # review projection deliberately omits that default.
            if expected_evidence_projection["source_modality"] != "visual":
                expected_evidence_projection["source_modality"] = None
                if not expected_evidence_projection["visual_facts"]:
                    expected_evidence_projection["visual_facts"] = None
            if actual_evidence_projection != expected_evidence_projection:
                findings.append(
                    f"{claim.claim_id}/{item.evidence_step_id}/"
                    f"{item.source_fragment_id}: scope packet evidence projection drift"
                )
        expected_source_sha = current_sources.get(claim.source_id)
        if not expected_source_sha:
            findings.append(f"{claim.claim_id}: source missing from current corpus")
            continue
        evidence_shas = {
            str(item.source_sha256 or "")
            for item in claim.evidence
            if item.source_id == claim.source_id
        }
        if evidence_shas != {expected_source_sha}:
            findings.append(f"{claim.claim_id}: source SHA drift")
    if len(packet_claim_ids) != len(set(packet_claim_ids)):
        findings.append("scope packet contains duplicate Claim IDs")
    excluded_claim_ids: list[str] = []
    for raw in scope_packet.get("excluded_claims") or []:
        claim_id = str(raw.get("claim_id") or "")
        excluded_claim_ids.append(claim_id)
        current = current_claims.get(claim_id)
        if not claim_id or current is None:
            findings.append(f"{claim_id or '<empty>'}: excluded Claim missing from corpus")
            continue
        if (
            raw.get("reason_code") != "superseded_claim"
            or raw.get("review_status") != current.review_status
            or current.review_status
            not in IDENTITY_TERMINALLY_EXCLUDED_CLAIM_REVIEW_STATUSES
            or raw.get("claim_revision") != current.revision
            or raw.get("claim_revision_sha256") != semantic_record_sha(current)
        ):
            findings.append(
                f"{claim_id}: excluded Claim is not bound to a current superseded record"
            )
    if len(excluded_claim_ids) != len(set(excluded_claim_ids)):
        findings.append("scope packet contains duplicate excluded Claim IDs")
    overlap = sorted(set(packet_claim_ids) & set(excluded_claim_ids))
    if overlap:
        findings.append(f"scope packet includes and excludes the same Claims: {overlap}")
    overlap = sorted(set(blocked_claim_ids) & set(excluded_claim_ids))
    if overlap:
        findings.append(f"scope packet blocks and excludes the same Claims: {overlap}")
    findings.extend(
        claim_evidence_integrity_findings(
            claim_ids=packet_claim_ids,
            claims=current_claims,
            evidence_steps=current_evidence,
        )
    )
    return findings


def _artifact_binding(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise CvpProductionBlocked([f"required artifact missing: {path}"])
    from backend.pipeline.viewpoint_backup_archive import logical_binding_path
    return {"path": str(logical_binding_path(path)), "sha256": file_sha256(path)}


def _passed_status(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("status") or "").casefold() in {
        "pass",
        "passed",
        "complete",
        "completed",
        "ready",
        "resolved",
    }


def accepted_grouping_in_passage_order(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Schedule intact Matthew groups by numeric passage, never size or group ID.

    Ties retain the upstream argument-boundary order. The accepted manifest is
    not modified: this is only an execution schedule over its original groups.
    """
    units = manifest.get("units") or []
    by_id = {row["unit_id"]: row for row in units}
    if len(by_id) != len(units):
        raise CvpProductionBlocked(["duplicate passage unit in accepted grouping"])
    ranked = []
    for position, group in enumerate(manifest["groups"]):
        unit = by_id.get(group.get("unit_id"))
        match = re.fullmatch(r"Matt\.(\d+)\.(\d+)(?:-Matt\.(\d+)\.(\d+))?", str((unit or {}).get("passage_key") or ""))
        if not match:
            raise CvpProductionBlocked([f"{group['group_key']}: missing or invalid Matthew passage order"])
        start_chapter, start_verse = map(int, match.groups()[:2])
        end_chapter, end_verse = map(int, match.groups()[2:]) if match.group(3) is not None else (start_chapter, start_verse)
        if not (1 <= start_chapter <= end_chapter <= 28 and start_verse >= 1 and end_verse >= 1) or (start_chapter, start_verse) > (end_chapter, end_verse):
            raise CvpProductionBlocked([f"{group['group_key']}: reversed or invalid passage range"])
        ranked.append(((start_chapter, start_verse, end_chapter, end_verse, position), group))
    return [dict(group) for _, group in sorted(ranked, key=lambda row: row[0])]


def accepted_grouping_canary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Qualify the first chapter-ordered group; never skip ahead to an easier one."""
    groups = accepted_grouping_in_passage_order(manifest)
    if not groups:
        raise CvpProductionBlocked(["accepted grouping has no first passage group"])
    first = groups[0]
    claims = {row["claim_id"]: row for row in manifest["claim_packets"]}
    ids = first["claim_ids"]
    if first["group_key"] == RESIDUAL_GROUP_KEY or not 2 <= len(ids) <= 20 or len({claims[cid]["source_id"] for cid in ids}) < 2:
        raise CvpProductionBlocked(["first passage group does not meet multi-source canary requirements; cannot skip chapter order"])
    return first


def accepted_grouping_phase_group(manifest: Mapping[str, Any], stage: str) -> dict[str, Any]:
    """Select one intact accepted group, never an operator-edited Claim subset."""
    if stage == "canary":
        return accepted_grouping_canary(manifest)
    if not stage.startswith("group:"):
        raise CvpProductionBlocked(["unsupported accepted grouping admission stage"])
    key = stage[len("group:"):]
    rows = [g for g in accepted_grouping_in_passage_order(manifest) if g["group_key"] == key]
    if len(rows) != 1 or key == RESIDUAL_GROUP_KEY or not 1 <= len(rows[0]["claim_ids"]) <= 20:
        raise CvpProductionBlocked(["invalid intact accepted group phase"])
    return rows[0]


def accepted_grouping_audit_scope(manifest: Mapping[str, Any], stage: str | None) -> dict[str, Any]:
    if stage is None:
        return dict(manifest)
    ids = set(accepted_grouping_phase_group(manifest, stage)["claim_ids"])
    # This is a derived scope over the original manifest, not a new manifest.
    # Its artifact_sha256 deliberately names the unchanged parent artifact.
    return dict(manifest) | {"claim_packets": [row for row in manifest["claim_packets"] if row["claim_id"] in ids]}


def validate_scoped_audit_admission(
    *, audit: Mapping[str, Any], disposition: Mapping[str, Any],
    reference: Mapping[str, Any], audit_file_sha256: str,
    selected_claim_ids: Sequence[str],
    admission_stage: str | None = None,
    source_epoch_binding=None,
) -> dict[str, dict[str, Any]]:
    """Verify owner-authorized isolation against the unfiltered original audit.

    A 'passed' flag is not sufficient. Recompute every classified finding from
    accepted input, and require the exact frozen input denominator.
    """
    from backend.pipeline.viewpoint_reviewed_grouping import validate_accepted_grouping
    from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition

    validate_content_addressed(reference, "artifact_sha256")
    validate_content_addressed(disposition, "artifact_sha256")
    if reference.get("schema_version") != "wang_shared_grouping_reference_v1":
        raise CvpProductionBlocked(["scoped audit requires a shared accepted grouping reference"])
    manifest_sha = str(reference.get("manifest_sha256") or "")
    upstream = validate_reviewed_grouping_bindings(reference.get("bindings"), manifest_sha256=manifest_sha,
                                                 source_epoch_binding=source_epoch_binding)
    validate_accepted_grouping(**upstream, bindings=reference["bindings"], expected_manifest_sha256=manifest_sha,
                               source_epoch_binding=source_epoch_binding)
    audit_scope = accepted_grouping_audit_scope(upstream["manifest"], admission_stage)
    expected_ids = [row["claim_id"] for row in audit_scope["claim_packets"]]
    if len(selected_claim_ids) != len(set(selected_claim_ids)) or set(selected_claim_ids) != set(expected_ids):
        raise CvpProductionBlocked(["scoped audit production denominator differs from accepted grouping"])
    expected = scope_audit_disposition(
        audit=audit, manifest=audit_scope, audit_file_sha256=audit_file_sha256,
        authorized_by=str(disposition.get("authorized_by") or ""),
    )
    if dict(disposition) != expected or expected["status"] != "passed":
        raise CvpProductionBlocked(["scoped audit disposition does not match original findings or has in-scope failures"])
    return upstream


def _validate_prerequisites(
    paths: Mapping[str, Path], *, scope_packet: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    required = {
        "source_universe_manifest",
        "claim_manifest",
        "source_lineage_manifest",
        "source_attestation",
        "source_state_reconciliation",
        "source_state_validation",
        "independent_audit",
        "audit_disposition",
    }
    findings = [f"missing prerequisite role: {role}" for role in sorted(required - set(paths))]
    bindings: dict[str, Any] = {}
    payloads: dict[str, dict[str, Any]] = {}
    for role, path in sorted(paths.items()):
        try:
            bindings[role] = _artifact_binding(path)
            payloads[role] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, CvpProductionBlocked) as exc:
            findings.append(f"{role}: unreadable prerequisite: {exc}")
    for role in ("source_state_reconciliation", "source_state_validation", "audit_disposition"):
        payload = payloads.get(role)
        if payload is not None and not _passed_status(payload):
            findings.append(f"{role}: status is not passed")
    audit = payloads.get("independent_audit")
    disposition = payloads.get("audit_disposition") or {}
    scoped_audit = disposition.get("schema_version") == "wang_cvp_scoped_audit_disposition_v1"
    if scoped_audit:
        try:
            from backend.pipeline.viewpoint_g041_source_epoch import GROUPING_EPOCH_KEY
            if audit is None or scope_packet is None or "reviewed_grouping_reference" not in payloads:
                raise CvpProductionBlocked(["scoped audit lacks full audit, scope packet or shared grouping reference"])
            upstream = validate_scoped_audit_admission(
                audit=audit, disposition=disposition,
                reference=payloads["reviewed_grouping_reference"],
                audit_file_sha256=bindings["independent_audit"]["sha256"],
                selected_claim_ids=[row["claim_id"] for row in scope_packet["claims"]],
                admission_stage=scope_packet.get("accepted_grouping_stage"),
                source_epoch_binding=scope_packet.get(GROUPING_EPOCH_KEY),
            )
            manifest_ids = [row["claim_id"] for row in (payloads.get("claim_manifest") or {}).get("claims") or []]
            if set(manifest_ids) != {row["claim_id"] for row in scope_packet["claims"]} or len(manifest_ids) != len(set(manifest_ids)):
                raise CvpProductionBlocked(["scoped audit Claim manifest denominator mismatch"])
            reference = payloads["reviewed_grouping_reference"]
            for role in upstream:
                bindings[f"accepted_grouping_{role}"] = _artifact_binding(Path(reference["bindings"][role]["path"]))
        except (CvpProductionBlocked, KeyError, TypeError) as exc:
            findings.append(f"scoped audit admission: {exc}")
    if audit is not None and not scoped_audit:
        layers = audit.get("layers") or {}
        for number in ("1", "2"):
            layer = layers.get(number) or layers.get(int(number)) or {}
            if not layer:
                findings.append(f"independent_audit: Layer {number} missing")
            elif layer.get("findings"):
                findings.append(f"independent_audit: Layer {number} has findings")
    claim_manifest = payloads.get("claim_manifest")
    if claim_manifest is not None:
        body = dict(claim_manifest)
        stated = str(body.pop("manifest_sha256", ""))
        if not stated or stated != sha256_json(body):
            findings.append("claim_manifest: SHA mismatch")
    for role in ("source_lineage_manifest", "source_attestation"):
        payload = payloads.get(role)
        if payload is not None:
            body = dict(payload)
            stated = str(body.pop("artifact_sha256", ""))
            if not stated or stated != sha256_json(body):
                findings.append(f"{role}: SHA mismatch")
    source_attestation = payloads.get("source_attestation")
    if source_attestation is not None:
        bindings["source_attestation"]["artifact_sha256"] = str(
            source_attestation.get("artifact_sha256") or ""
        )
    return bindings, findings


def build_cvp_freeze(
    *,
    ticket_id: int,
    scope_packet_path: Path,
    prerequisite_paths: Mapping[str, Path],
    store: Any,
    cvp_policy_sha256: str,
    route_policy_sha256: str,
    runner_commit: str,
    worktree_root: Path,
    output_root: Path,
    global_lock_path: Path,
) -> dict[str, Any]:
    if ticket_id != 357:
        raise CvpProductionBlocked(["production CVP freeze must belong to ticket 357"])
    if not runner_commit or runner_commit == "unknown":
        raise CvpProductionBlocked(["runner commit is required"])
    scope_packet = json.loads(scope_packet_path.read_text(encoding="utf-8"))
    findings = _validate_scope_packet(scope_packet, store)
    bindings, prerequisite_findings = _validate_prerequisites(prerequisite_paths, scope_packet=scope_packet)
    findings.extend(prerequisite_findings)
    attestation_binding = bindings.get("source_attestation") or {}
    if (
        scope_packet.get("source_attestation_artifact_sha256")
        != attestation_binding.get("artifact_sha256")
    ):
        findings.append("scope packet source attestation binding mismatch")
    if findings:
        raise CvpProductionBlocked(findings)
    source_attestation_path = prerequisite_paths.get("source_attestation")
    if source_attestation_path is not None:
        attestation = json.loads(source_attestation_path.read_text(encoding="utf-8"))
        if attestation.get("schema_version") == "wang_historical_viewpoint_source_eligibility_v1":
            from backend.pipeline.viewpoint_historical_source_eligibility import validate_historical_source_eligibility
            claim_manifest = json.loads(prerequisite_paths["claim_manifest"].read_text(encoding="utf-8"))
            validate_historical_source_eligibility(attestation, claim_manifest=claim_manifest, store=store)
    body = {
        "schema_version": CVP_FREEZE_VERSION,
        "ticket_id": ticket_id,
        "scope_label": str(scope_packet["scope_label"]),
        "scope_packet": _artifact_binding(scope_packet_path),
        "scope_packet_sha256": str(scope_packet["packet_sha256"]),
        "claim_manifest_sha256": str(scope_packet["claim_manifest_sha256"]),
        "coverage_snapshot_id": str(scope_packet["coverage_snapshot_id"]),
        "prerequisites": bindings,
        "corpus_fingerprint_sha256": collection_fingerprint(store, CORPUS_COLLECTIONS),
        "registry_fingerprint_sha256": collection_fingerprint(store, REGISTRY_COLLECTIONS),
        "cvp_policy_sha256": cvp_policy_sha256,
        "route_policy_sha256": route_policy_sha256,
        "runner_commit": runner_commit,
        "worktree_root": str(worktree_root.resolve()),
        "output_root": str(output_root.resolve()),
        "global_lock_path": str(global_lock_path.resolve()),
        "status": "frozen",
    }
    if "accepted_grouping_stage" in scope_packet:
        if scope_packet["accepted_grouping_stage"] != "canary" and not str(scope_packet["accepted_grouping_stage"]).startswith("group:"):
            raise CvpProductionBlocked(["unsupported accepted grouping admission stage"])
        body["accepted_grouping_stage"] = scope_packet["accepted_grouping_stage"]
    from backend.pipeline.viewpoint_applied_source_epochs import GROUPING_EPOCH_KEY, verified_applied
    if GROUPING_EPOCH_KEY in scope_packet:
        if 'accepted_grouping_stage' not in scope_packet:
            raise CvpProductionBlocked(['applied source epoch requires accepted grouping scope'])
        verified_applied(scope_packet[GROUPING_EPOCH_KEY], store)
        body[GROUPING_EPOCH_KEY] = scope_packet[GROUPING_EPOCH_KEY]
    return body | {"artifact_sha256": sha256_json(body)}


def validate_cvp_freeze(
    freeze: Mapping[str, Any],
    *,
    store: Any,
    cvp_policy_sha256: str,
    route_policy_sha256: str,
    runner_commit: str,
    expected_registry_fingerprint_sha256: str | None = None,
    validate_registry: bool = True,
) -> dict[str, Any]:
    findings: list[str] = []
    try:
        validate_content_addressed(freeze, "artifact_sha256")
    except CvpProductionBlocked as exc:
        findings.extend(exc.findings)
    if freeze.get("schema_version") != CVP_FREEZE_VERSION:
        findings.append("unsupported CVP freeze schema")
    if int(freeze.get("ticket_id") or 0) != 357:
        findings.append("CVP freeze belongs to another ticket")
    expected = {
        "cvp_policy_sha256": cvp_policy_sha256,
        "route_policy_sha256": route_policy_sha256,
        "runner_commit": runner_commit,
        "corpus_fingerprint_sha256": collection_fingerprint(store, CORPUS_COLLECTIONS),
    }
    if validate_registry:
        expected["registry_fingerprint_sha256"] = collection_fingerprint(
            store, REGISTRY_COLLECTIONS
        )
    expected_registry = (
        expected_registry_fingerprint_sha256
        or str(freeze.get("registry_fingerprint_sha256") or "")
    )
    for field, current in expected.items():
        wanted = expected_registry if field == "registry_fingerprint_sha256" else freeze.get(field)
        if wanted != current:
            findings.append(f"{field} drift")
    for role, binding in {
        "scope_packet": freeze.get("scope_packet") or {},
        **dict(freeze.get("prerequisites") or {}),
    }.items():
        path = Path(str(binding.get("path") or ""))
        if not path.is_file() or file_sha256(path) != binding.get("sha256"):
            findings.append(f"{role}: bound artifact drift")
    scope_path = Path(str((freeze.get("scope_packet") or {}).get("path") or ""))
    if scope_path.is_file():
        packet = json.loads(scope_path.read_text(encoding="utf-8"))
        findings.extend(_validate_scope_packet(packet, store))
        if packet.get("packet_sha256") != freeze.get("scope_packet_sha256"):
            findings.append("scope packet identity drift")
        from backend.pipeline.viewpoint_g041_source_epoch import GROUPING_EPOCH_KEY
        if packet.get(GROUPING_EPOCH_KEY) != freeze.get(GROUPING_EPOCH_KEY):
            findings.append('scope packet applied source epoch identity drift')
    if findings:
        raise CvpProductionBlocked(sorted(set(findings)))
    return dict(freeze)


def validate_execution_boundary(
    freeze: Mapping[str, Any], *, worktree_root: Path, output_root: Path
) -> None:
    findings: list[str] = []
    expected_worktree = Path(str(freeze.get("worktree_root") or "")).resolve()
    expected_output = Path(str(freeze.get("output_root") or "")).resolve()
    if worktree_root.resolve() != expected_worktree:
        findings.append("runner is executing from another worktree")
    if output_root.resolve() != expected_output:
        findings.append("output root differs from the frozen output root")
    if expected_output == expected_worktree or expected_output in expected_worktree.parents:
        findings.append("output root may not contain the worktree")
    if expected_worktree in expected_output.parents:
        findings.append("output root may not be inside the worktree")
    if findings:
        raise CvpProductionBlocked(findings)


def claim_output_ownership(
    *, output_root: Path, freeze: Mapping[str, Any], runner_commit: str
) -> dict[str, Any]:
    """Create or verify the immutable ticket/worktree/run ownership record."""

    output_root.mkdir(parents=True, exist_ok=True)
    body = {
        "schema_version": "wang_cvp_output_ownership_v1",
        "ticket_id": int(freeze.get("ticket_id") or 0),
        "freeze_sha256": str(freeze.get("artifact_sha256") or ""),
        "worktree_root": str(Path(str(freeze.get("worktree_root") or "")).resolve()),
        "output_root": str(output_root.resolve()),
        "runner_commit": runner_commit,
    }
    ownership = body | {"artifact_sha256": sha256_json(body)}
    path = output_root / "ownership.json"
    encoded = json.dumps(ownership, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CvpProductionBlocked([f"output ownership is unreadable: {exc}"]) from exc
        if existing != ownership:
            raise CvpProductionBlocked(["output root is owned by another CVP run"])
        return ownership
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return claim_output_ownership(
            output_root=output_root, freeze=freeze, runner_commit=runner_commit
        )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return ownership


@contextmanager
def exclusive_cvp_run_lock(freeze: Mapping[str, Any]):
    """Hold the one host-wide CVP identity lock for the semantic run."""

    lock_path = Path(str(freeze.get("global_lock_path") or ""))
    if not lock_path.is_absolute():
        raise CvpProductionBlocked(["global CVP lock path must be absolute"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CvpProductionBlocked(["another CVP identity run holds the global lock"]) from exc
        yield
    finally:
        handle.close()


def build_batch_identity(
    *,
    scope_label: str,
    freeze_sha256: str,
    grouping_sha256: str,
    claim_ids: Sequence[str],
    cvp_policy_sha256: str,
) -> dict[str, Any]:
    body = {
        "scope_label": scope_label,
        "freeze_sha256": freeze_sha256,
        "grouping_sha256": grouping_sha256,
        "claim_ids": sorted(set(claim_ids)),
        "cvp_policy_sha256": cvp_policy_sha256,
    }
    digest = sha256_json(body)
    return body | {
        "batch_id": f"CVB-{scope_label}-{digest[:20]}",
        "batch_key_sha256": digest,
    }


@current_check
def validate_reviewed_grouping_bindings(
    bindings: Mapping[str, Any], *, manifest_sha256: str, source_epoch_binding=None,
) -> dict[str, dict[str, Any]]:
    """Reopen the four upstream artifacts; a cached acceptance is not enough."""
    roles = {"manifest", "validation", "acceptance", "physical_review"}
    if not isinstance(bindings, Mapping) or set(bindings) != roles:
        raise CvpProductionBlocked(["reviewed grouping requires all four upstream bindings"])
    artifacts: dict[str, dict[str, Any]] = {}
    for role in sorted(roles):
        binding = bindings[role]
        try:
            path = Path(binding["path"])
            if not path.is_absolute() or file_sha256(path) != binding["file_sha256"]:
                raise CvpProductionBlocked([f"reviewed grouping {role}: physical SHA drift"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            validate_content_addressed(payload, "artifact_sha256")
            if payload["artifact_sha256"] != binding["artifact_sha256"]:
                raise CvpProductionBlocked([f"reviewed grouping {role}: artifact SHA drift"])
            artifacts[role] = payload
        except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
            raise CvpProductionBlocked([f"reviewed grouping {role}: unreadable binding"]) from exc
    manifest, acceptance = artifacts["manifest"], artifacts["acceptance"]
    if (
        manifest["artifact_sha256"] != manifest_sha256
        or acceptance.get("manifest_sha256") != manifest_sha256
        or artifacts["physical_review"].get("manifest_sha256") != manifest_sha256
        or acceptance.get("job_validation_sha256") != artifacts["validation"]["artifact_sha256"]
        or acceptance.get("physical_review_evidence_sha256") != artifacts["physical_review"]["artifact_sha256"]
    ):
        raise CvpProductionBlocked(["reviewed grouping upstream lineage drift"])
    source_rows = manifest.get("sources") or []
    source_ids = [row.get("source_id") for row in source_rows]
    selected_sources = {row["source_id"] for row in manifest["claim_packets"]}
    if set(source_ids) != selected_sources or len(source_ids) != len(set(source_ids)):
        raise CvpProductionBlocked(["reviewed grouping physical source denominator differs"])
    transitions = {}
    if source_epoch_binding is not None:
        try:
            from backend.pipeline.viewpoint_applied_source_epochs import grouping_physical_transition
            transitions = grouping_physical_transition(manifest, source_epoch_binding)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise CvpProductionBlocked(['reviewed grouping applied source epoch proof invalid: ' + str(exc)]) from exc
    for source in source_rows:
        for binding in [source, *(source.get("linked_files") or [])]:
            try:
                path = Path(binding["path"])
                expected = binding['file_sha256']
                # Never apply a published-source transition to a linked asset.
                if binding is source and str(path) in transitions:
                    old_sha, expected = transitions[str(path)]
                    if binding['file_sha256'] != old_sha:
                        raise CvpProductionBlocked(['reviewed grouping predecessor SHA drift'])
                if not path.is_absolute() or file_sha256(path) != expected:
                    raise CvpProductionBlocked([f"{source['source_id']}: physical source/linked asset SHA drift"])
            except (KeyError, TypeError, OSError) as exc:
                raise CvpProductionBlocked([f"{source['source_id']}: missing physical source/linked asset"]) from exc
    return artifacts


def validate_grouping_envelope(
    envelope: Mapping[str, Any], *, freeze: Mapping[str, Any]
) -> str:
    findings: list[str] = []
    try:
        stated = validate_content_addressed(envelope, "artifact_sha256")
    except CvpProductionBlocked as exc:
        findings.extend(exc.findings)
        stated = ""
    if envelope.get("schema_version") != CVP_GROUPING_ENVELOPE_VERSION:
        findings.append("grouping is not a production v2 envelope")
    if envelope.get("freeze_sha256") != freeze.get("artifact_sha256"):
        findings.append("grouping belongs to another freeze")
    if envelope.get("scope_packet_sha256") != freeze.get("scope_packet_sha256"):
        findings.append("grouping belongs to another scope packet")
    if envelope.get("cvp_policy_sha256") != freeze.get("cvp_policy_sha256"):
        findings.append("grouping belongs to another CVP policy")
    if "upstream_bindings" in envelope and envelope.get("admission_kind") != "independently_accepted_exegesis_grouping":
        findings.append("reviewed grouping admission kind may not be removed or changed")
    if envelope.get("admission_kind") == "independently_accepted_exegesis_grouping":
        try:
            from backend.pipeline.viewpoint_g041_source_epoch import GROUPING_EPOCH_KEY
            if envelope.get(GROUPING_EPOCH_KEY) != freeze.get(GROUPING_EPOCH_KEY):
                raise CvpProductionBlocked(['grouping applied source epoch differs from freeze'])
            upstream = validate_reviewed_grouping_bindings(
                envelope.get("upstream_bindings"),
                manifest_sha256=str(envelope.get("upstream_manifest_sha256") or ""),
                source_epoch_binding=freeze.get(GROUPING_EPOCH_KEY),
            )
            original_groups = [
                {key: row[key] for key in ("group_key", "claim_ids", "rationale")}
                for row in accepted_grouping_in_passage_order(upstream["manifest"])
            ]
            stage = envelope.get("accepted_grouping_stage")
            if stage is not None:
                if freeze.get("accepted_grouping_stage") != stage:
                    findings.append("canary grouping requires an explicitly phased freeze")
                canary = accepted_grouping_phase_group(upstream["manifest"], stage)
                original_groups = [{key: canary[key] for key in ("group_key", "claim_ids", "rationale")}]
                if envelope.get("parent_claim_count") != len(upstream["manifest"]["claim_packets"]) or envelope.get("parent_group_count") != len(upstream["manifest"]["groups"]):
                    findings.append("canary parent denominator drift")
                expected_deferred = sorted({row["claim_id"] for row in upstream["manifest"]["claim_packets"]} - set(canary["claim_ids"]))
                if envelope.get("deferred_claim_ids") != expected_deferred:
                    findings.append("canary deferred denominator drift")
            elif envelope.get("accepted_grouping_stage") is not None or freeze.get("accepted_grouping_stage") is not None:
                findings.append("accepted grouping stage drift")
            if (envelope.get("grouping") or {}).get("groups") != original_groups:
                findings.append("reviewed grouping boundaries/order differ from accepted manifest")
            if envelope.get("grouping_reexecuted") is not False:
                findings.append("reviewed grouping must not be reexecuted")
        except CvpProductionBlocked as exc:
            findings.extend(exc.findings)
        except (KeyError, TypeError):
            findings.append("reviewed grouping upstream shape is invalid")
    if findings:
        raise CvpProductionBlocked(findings)
    return stated


def build_canary_selection(
    *,
    grouping_envelope: Mapping[str, Any],
    freeze: Mapping[str, Any],
    scope_packet: Mapping[str, Any],
    batch_size: int,
) -> dict[str, Any]:
    """Select an intact multi-source group without semantic proxies.

    The grouping model has already seen the complete frozen scope.  This step
    may only choose among its intact groups using fields already present in the
    frozen packet; it cannot regroup Claims or infer identity. Accepted Matthew
    groups must start at the first passage; only the legacy path ranks by size.
    """

    grouping_sha256 = validate_grouping_envelope(grouping_envelope, freeze=freeze)
    try:
        packet_sha256 = validate_content_addressed(scope_packet, "packet_sha256")
    except CvpProductionBlocked as exc:
        raise CvpProductionBlocked(
            [f"canary scope packet: {finding}" for finding in exc.findings]
        ) from exc
    if packet_sha256 != freeze.get("scope_packet_sha256"):
        raise CvpProductionBlocked(["canary scope packet belongs to another freeze"])
    if batch_size < 2:
        raise CvpProductionBlocked(["canary batch ceiling must allow a non-trivial group"])

    claims = {
        item.claim_id: item
        for item in (
            ReviewClaim.model_validate(raw) for raw in scope_packet.get("claims") or []
        )
    }
    claim_positions = {
        str(raw["claim_id"]): index
        for index, raw in enumerate(scope_packet.get("claims") or [])
    }
    grouping = ClaimGroupingResponse.model_validate(grouping_envelope.get("grouping"))
    validate_grouping(
        grouping=grouping,
        scope_label=str(scope_packet.get("scope_label") or ""),
        claim_ids=list(claims),
    )

    candidates: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    passage_ordered = grouping_envelope.get("admission_kind") == "independently_accepted_exegesis_grouping"
    for group_position, group in enumerate(grouping.groups):
        group_claims = [claims[claim_id] for claim_id in group.claim_ids]
        source_ids = sorted({claim.source_id for claim in group_claims})
        if (
            group.group_key == RESIDUAL_GROUP_KEY
            or not 2 <= len(group_claims) <= batch_size
            or len(source_ids) < 2
        ):
            if passage_ordered and group_position == 0:
                raise CvpProductionBlocked(["first passage group cannot be skipped for canary selection"])
            continue
        first_scope_position = min(
            claim_positions[claim_id] for claim_id in group.claim_ids
        )
        details = {
            "group_key": group.group_key,
            "claim_ids": sorted(group.claim_ids),
            "claim_count": len(group_claims),
            "source_ids": source_ids,
            "source_count": len(source_ids),
            "first_scope_position": first_scope_position,
        }
        rank = (group_position, 0, group.group_key) if passage_ordered else (
            len(group_claims), first_scope_position, group.group_key,
        )
        candidates.append((rank, details))

    if not candidates:
        raise CvpProductionBlocked(
            [
                "no intact non-residual canary group has 2+ Claims from 2+ sources"
            ]
        )
    selected = min(candidates, key=lambda item: item[0])[1]
    body = {
        "schema_version": CVP_CANARY_SELECTION_VERSION,
        "freeze_sha256": freeze["artifact_sha256"],
        "scope_packet_sha256": packet_sha256,
        "grouping_sha256": grouping_sha256,
        "selection_policy": {
            "excluded_group_keys": [RESIDUAL_GROUP_KEY],
            "minimum_claim_count": 2,
            "maximum_claim_count": batch_size,
            "minimum_source_count": 2,
            "ranking": ["accepted_passage_order_ascending"] if passage_ordered else [
                "claim_count_ascending",
                "first_scope_position_ascending",
                "group_key_ascending",
            ],
        },
        "qualifying_group_count": len(candidates),
        "selected_group": selected,
    }
    return body | {"artifact_sha256": sha256_json(body)}


def validate_canary_selection(
    selection: Mapping[str, Any],
    *,
    grouping_envelope: Mapping[str, Any],
    freeze: Mapping[str, Any],
    scope_packet: Mapping[str, Any],
    batch_size: int,
) -> str:
    expected = build_canary_selection(
        grouping_envelope=grouping_envelope,
        freeze=freeze,
        scope_packet=scope_packet,
        batch_size=batch_size,
    )
    if dict(selection) != expected:
        raise CvpProductionBlocked(
            ["canary selection is stale, altered, or not the deterministic choice"]
        )
    return str(expected["selected_group"]["group_key"])


def validate_apply_authorization(
    authorization: Mapping[str, Any],
    *,
    freeze: Mapping[str, Any],
    grouping_sha256: str,
) -> None:
    findings: list[str] = []
    try:
        validate_content_addressed(authorization, "artifact_sha256")
    except CvpProductionBlocked as exc:
        findings.extend(exc.findings)
    if authorization.get("schema_version") != CVP_APPLY_AUTHORIZATION_VERSION:
        findings.append("unsupported apply authorization schema")
    if authorization.get("status") != "authorized":
        findings.append("apply authorization is not authorized")
    if authorization.get("freeze_sha256") != freeze.get("artifact_sha256"):
        findings.append("apply authorization belongs to another freeze")
    if authorization.get("grouping_sha256") != grouping_sha256:
        findings.append("apply authorization belongs to another grouping")
    backup = authorization.get("backup_dump") or {}
    backup_path = Path(str(backup.get("path") or ""))
    if not backup_path.is_file() or file_sha256(backup_path) != backup.get("sha256"):
        findings.append("backup dump binding is invalid")
    if not authorization.get("pg_restore_list_sha256"):
        findings.append("pg_restore verification is missing")
    if findings:
        raise CvpProductionBlocked(findings)


def build_plan_readback_receipt(
    *,
    plan: Any,
    store: Any,
    batch_identity: Mapping[str, Any],
    freeze_sha256: str,
    grouping_sha256: str,
    pre_registry_fingerprint_sha256: str,
    post_registry_fingerprint_sha256: str,
) -> dict[str, Any]:
    """Verify every operation, not only CanonicalViewpoint head pointers."""

    findings: list[str] = []
    records: list[dict[str, Any]] = []
    if store.get_change_set_status(plan.fingerprint_sha256) != "applied":
        findings.append("ChangeSet is not authoritatively applied")
    for operation in plan.operations:
        state = store.get_record_state(operation.collection, operation.object_id)
        expected_retired = operation.operation == "retire"
        if state is None:
            findings.append(f"{operation.collection}/{operation.object_id}: missing")
            continue
        record = {
            "operation": operation.operation,
            "collection": operation.collection,
            "object_id": operation.object_id,
            "expected_revision": operation.after_revision,
            "observed_revision": state["revision"],
            "expected_content_sha256": operation.after_sha256,
            "observed_content_sha256": state["content_sha256"],
            "expected_retired": expected_retired,
            "observed_retired": state["retired"],
        }
        records.append(record)
        if state["revision"] != operation.after_revision:
            findings.append(f"{operation.collection}/{operation.object_id}: revision mismatch")
        if state["content_sha256"] != operation.after_sha256:
            findings.append(f"{operation.collection}/{operation.object_id}: content SHA mismatch")
        if state["retired"] != expected_retired:
            findings.append(f"{operation.collection}/{operation.object_id}: retirement mismatch")
        if not expected_retired:
            expected_payload = dict(operation.payload)
            expected_payload["revision"] = operation.after_revision
            if state["payload"] != expected_payload:
                findings.append(f"{operation.collection}/{operation.object_id}: payload mismatch")
    if findings:
        raise CvpProductionBlocked(findings)
    body = {
        "schema_version": "wang_cvp_plan_readback_receipt_v1",
        "batch_identity": dict(batch_identity),
        "freeze_sha256": freeze_sha256,
        "grouping_sha256": grouping_sha256,
        "pre_registry_fingerprint_sha256": pre_registry_fingerprint_sha256,
        "post_registry_fingerprint_sha256": post_registry_fingerprint_sha256,
        "change_set_id": plan.change_set_id,
        "change_set_sha256": plan.fingerprint_sha256,
        "change_set_status": "applied",
        "records": records,
        "status": "passed",
    }
    return body | {"artifact_sha256": sha256_json(body)}


def validate_plan_readback_receipt(
    receipt: Mapping[str, Any],
    *,
    store: Any,
    batch_identity: Mapping[str, Any],
    expected_pre_registry_fingerprint_sha256: str,
    validate_current_records: bool = True,
) -> None:
    findings: list[str] = []
    try:
        validate_content_addressed(receipt, "artifact_sha256")
    except CvpProductionBlocked as exc:
        findings.extend(exc.findings)
    if receipt.get("schema_version") != "wang_cvp_plan_readback_receipt_v1":
        findings.append("unsupported CVP readback receipt")
    if receipt.get("batch_identity") != dict(batch_identity):
        findings.append("readback receipt belongs to another batch identity")
    if (
        receipt.get("pre_registry_fingerprint_sha256")
        != expected_pre_registry_fingerprint_sha256
    ):
        findings.append("readback receipt does not continue the Registry cut chain")
    if store.get_change_set_status(str(receipt.get("change_set_sha256") or "")) != "applied":
        findings.append("readback ChangeSet is not applied")
    for expected in receipt.get("records") or []:
        if not validate_current_records:
            continue
        collection = str(expected.get("collection") or "")
        object_id = str(expected.get("object_id") or "")
        state = store.get_record_state(collection, object_id)
        if state is None:
            findings.append(f"{collection}/{object_id}: missing after resume")
            continue
        comparisons = {
            "revision": (expected.get("expected_revision"), state["revision"]),
            "content SHA": (
                expected.get("expected_content_sha256"),
                state["content_sha256"],
            ),
            "retirement": (expected.get("expected_retired"), state["retired"]),
        }
        for label, (wanted, observed) in comparisons.items():
            if wanted != observed:
                findings.append(f"{collection}/{object_id}: {label} drift after resume")
    if findings:
        raise CvpProductionBlocked(findings)
