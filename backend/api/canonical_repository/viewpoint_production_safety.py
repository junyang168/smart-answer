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
from copy import deepcopy
from contextlib import contextmanager
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import fcntl

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
from backend.api.canonical_repository.viewpoint_resolution import (
    IDENTITY_ELIGIBLE_CLAIM_REVIEW_STATUSES,
    IDENTITY_TERMINALLY_EXCLUDED_CLAIM_REVIEW_STATUSES,
    ReviewClaim,
    claim_evidence_integrity_findings,
)


CVP_FREEZE_VERSION = "wang_cvp_production_freeze_v1"
CVP_GROUPING_ENVELOPE_VERSION = "wang_canonical_viewpoint_grouping_envelope_v2"
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

    current_claims = {
        row.claim_id: row
        for row in (
            ClaimRecord.model_validate(raw) for raw in store.list_records("claims")
        )
    }
    current_evidence = {
        row.evidence_step_id: row
        for row in (
            EvidenceStepRecord.model_validate(raw)
            for raw in store.list_records("evidence_steps")
        )
    }
    current_fragments = {
        row.fragment_id: row
        for row in (
            SourceFragmentRecord.model_validate(raw)
            for raw in store.list_records("source_fragments")
        )
    }
    current_sources = {
        str(row.get("source_id") or ""): str(row.get("source_sha256") or "")
        for row in store.list_records("source_documents")
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
    return {"path": str(path.resolve()), "sha256": file_sha256(path)}


def _passed_status(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("status") or "").casefold() in {
        "pass",
        "passed",
        "complete",
        "completed",
        "ready",
        "resolved",
    }


def _validate_prerequisites(paths: Mapping[str, Path]) -> tuple[dict[str, Any], list[str]]:
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
    if audit is not None:
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
    bindings, prerequisite_findings = _validate_prerequisites(prerequisite_paths)
    findings.extend(prerequisite_findings)
    attestation_binding = bindings.get("source_attestation") or {}
    if (
        scope_packet.get("source_attestation_artifact_sha256")
        != attestation_binding.get("artifact_sha256")
    ):
        findings.append("scope packet source attestation binding mismatch")
    if findings:
        raise CvpProductionBlocked(findings)
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
    if findings:
        raise CvpProductionBlocked(findings)
    return stated


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
