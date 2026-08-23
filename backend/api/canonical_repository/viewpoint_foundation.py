"""Deterministic foundations for CanonicalViewpoint master data.

This module never calls a content model and never writes the authoring store.
It gives the model/review cards immutable inputs: source-bound coverage, a
Claim denominator, candidate seeds, an exact-once resolution ledger, and a
per-dimension quality report.  PostgreSQL ingestion remains a separate,
explicit ChangeSet operation.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .knowledge_models import (
    CanonicalViewpointRecord,
    ClaimRecord,
    ClaimRelationConstraintRecord,
    ClaimRelationRecord,
    KnowledgeSourceDocument,
    evidence_fragment_ids,
    ViewpointClaimLinkRecord,
    ViewpointCoverageSnapshotRecord,
    ViewpointCoverageSource,
    ViewpointIdentityCandidateRecord,
    ViewpointQualityDimension,
    ViewpointQualityFailure,
    ViewpointQualityReportRecord,
    ViewpointResolutionLedgerRecord,
    ViewpointResolutionRow,
    ViewpointResolutionStatistics,
)


COVERAGE_BUILDER_VERSION = "viewpoint_coverage_builder_v1"
CLAIM_MANIFEST_VERSION = "viewpoint_input_claim_manifest_v1"
LEDGER_BUILDER_VERSION = "viewpoint_resolution_ledger_builder_v1"
QUALITY_VALIDATOR_VERSION = "viewpoint_quality_validator_v1"
CANDIDATE_BLOCKING_VERSION = "viewpoint_candidate_blocking_v2"
SOURCE_ELIGIBILITY_POLICY_VERSION = "viewpoint_source_eligibility_v1"
REVIEWED_DUPLICATE_STATUSES = frozenset(
    {"ai_consensus", "system_approved", "human_approved", "approved"}
)
APPROVED_CONSTRAINT_STATUSES = frozenset(
    {"system_approved", "human_approved", "approved"}
)

QUALITY_DIMENSIONS = (
    "provenance_integrity",
    "source_maturity",
    "resolution_coverage",
    "identity_precision",
    "candidate_recall",
    "route_fidelity",
    "temporal_correctness",
    "consumer_projection_integrity",
)


class ViewpointFoundationValidationError(ValueError):
    def __init__(self, findings: Sequence[str]):
        self.findings = list(findings)
        super().__init__("Viewpoint foundation validation failed: " + " | ".join(self.findings))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def semantic_record_sha(value: Mapping[str, Any] | Any) -> str:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    else:
        payload = dict(value)
    payload.pop("revision", None)
    return sha256_json(payload)


def _as_model(value: Mapping[str, Any] | Any, model: type[Any]) -> Any:
    return value if isinstance(value, model) else model.model_validate(value)


def build_coverage_snapshot(
    sources: Sequence[Mapping[str, Any] | KnowledgeSourceDocument],
    *,
    roles_by_source: Mapping[str, Iterable[str]],
    source_universe_manifest: Mapping[str, Any],
    created_at: str,
    historical_survey_baseline_id: str | None = None,
    coverage_status: str = "partial",
) -> ViewpointCoverageSnapshotRecord:
    """Build a byte-stable source-revision denominator.

    `source_sha256` is required here even though legacy source records allow it
    to be absent.  A source without a pinned body cannot safely participate in
    cross-source identity decisions.
    """

    manifest_payload = dict(source_universe_manifest)
    stated_manifest_sha = str(manifest_payload.pop("manifest_sha256", ""))
    actual_manifest_sha = sha256_json(manifest_payload)
    if stated_manifest_sha and stated_manifest_sha != actual_manifest_sha:
        raise ViewpointFoundationValidationError(["source-universe manifest SHA mismatch"])
    manifest_id = str(manifest_payload.get("source_universe_manifest_id") or "")
    if not manifest_id:
        raise ViewpointFoundationValidationError(["source-universe manifest id is required"])
    manifest_source_rows = list(manifest_payload.get("sources") or [])
    manifest_sources = {
        str(item.get("source_id") or ""): (
            str(item.get("source_revision_id") or ""),
            str(item.get("source_sha256") or ""),
        )
        for item in manifest_source_rows
    }
    if not manifest_sources or "" in manifest_sources:
        raise ViewpointFoundationValidationError(
            ["source-universe manifest needs addressed source revisions"]
        )
    if len(manifest_sources) != len(manifest_source_rows):
        raise ViewpointFoundationValidationError(
            ["source-universe manifest contains duplicate source ids"]
        )
    manifest_revision_order = [
        str(item.get("source_revision_id") or "") for item in manifest_source_rows
    ]
    if manifest_revision_order != sorted(manifest_revision_order):
        raise ViewpointFoundationValidationError(
            ["source-universe manifest sources must be sorted by source_revision_id"]
        )

    rows: list[ViewpointCoverageSource] = []
    findings: list[str] = []
    for raw in sources:
        source = _as_model(raw, KnowledgeSourceDocument)
        roles = sorted(set(map(str, roles_by_source.get(source.source_id, ()))))
        if not source.source_sha256:
            findings.append(f"{source.source_id}: source_sha256 is required")
            continue
        if not roles:
            findings.append(f"{source.source_id}: at least one coverage role is required")
            continue
        if "source_universe" not in roles:
            findings.append(f"{source.source_id}: source_universe role is required")
            continue
        manifest_binding = manifest_sources.get(source.source_id)
        expected_binding = (f"{source.source_id}@{source.revision}", source.source_sha256)
        if manifest_binding != expected_binding:
            findings.append(f"{source.source_id}: source revision does not match universe manifest")
            continue
        rows.append(
            ViewpointCoverageSource(
                source_id=source.source_id,
                source_revision_id=f"{source.source_id}@{source.revision}",
                source_sha256=source.source_sha256,
                roles=roles,
            )
        )
    supplied_source_ids = {item.source_id for item in rows}
    missing_from_coverage = sorted(set(manifest_sources) - supplied_source_ids)
    extra_in_coverage = sorted(supplied_source_ids - set(manifest_sources))
    if missing_from_coverage:
        findings.append(
            "source-universe revisions missing from coverage: " + ", ".join(missing_from_coverage)
        )
    if extra_in_coverage:
        findings.append(
            "coverage revisions absent from source universe: " + ", ".join(extra_in_coverage)
        )
    if coverage_status == "complete":
        not_reviewed = sorted(
            item.source_id for item in rows if "viewpoint_reviewed" not in item.roles
        )
        if not_reviewed:
            findings.append(
                "complete coverage requires viewpoint_reviewed for: " + ", ".join(not_reviewed)
            )
    if findings:
        raise ViewpointFoundationValidationError(findings)
    rows.sort(key=lambda item: item.source_revision_id)
    serialized_sources = [item.model_dump(mode="json") for item in rows]
    sources_sha = sha256_json(serialized_sources)
    identity = {
        "builder_version": COVERAGE_BUILDER_VERSION,
        "source_universe_manifest_id": manifest_id,
        "source_universe_manifest_sha256": actual_manifest_sha,
        "historical_survey_baseline_id": historical_survey_baseline_id,
        "sources_sha256": sources_sha,
        "coverage_status": coverage_status,
        "created_at": created_at,
    }
    return ViewpointCoverageSnapshotRecord(
        coverage_snapshot_id=f"CVS-{sha256_json(identity)[:20]}",
        historical_survey_baseline_id=historical_survey_baseline_id,
        source_universe_manifest_id=manifest_id,
        source_universe_manifest_sha256=actual_manifest_sha,
        sources=rows,
        sources_sha256=sources_sha,
        coverage_status=coverage_status,
        created_at=created_at,
    )


def build_input_claim_manifest(
    claims: Sequence[Mapping[str, Any] | ClaimRecord],
    evidence_steps: Sequence[Mapping[str, Any]],
    source_fragments: Sequence[Mapping[str, Any]],
    coverage_snapshot: Mapping[str, Any] | ViewpointCoverageSnapshotRecord,
) -> dict[str, Any]:
    """Address every source-local Claim revision inside one coverage snapshot."""

    coverage = _as_model(
        coverage_snapshot, ViewpointCoverageSnapshotRecord
    )
    covered_sources = {item.source_id for item in coverage.sources}
    fragments = {str(item["fragment_id"]): item for item in source_fragments}
    evidence = {str(item["evidence_step_id"]): item for item in evidence_steps}
    entries: list[dict[str, Any]] = []
    findings: list[str] = []
    seen: set[tuple[str, int]] = set()
    for raw in claims:
        claim = _as_model(raw, ClaimRecord)
        key = (claim.claim_id, claim.revision)
        if key in seen:
            findings.append(f"{claim.claim_id}@{claim.revision}: duplicate Claim revision")
            continue
        seen.add(key)
        source_ids: set[str] = set()
        for evidence_id in claim.evidence_step_ids:
            step = evidence.get(evidence_id)
            if step is None:
                findings.append(f"{claim.claim_id}: missing evidence step {evidence_id}")
                continue
            bound = [fragments.get(value) for value in evidence_fragment_ids(step)]
            if not bound or any(fragment is None for fragment in bound):
                findings.append(f"{claim.claim_id}: evidence {evidence_id} has no source fragment")
                continue
            source_ids.update(
                str(fragment.get("source_id") or "") for fragment in bound if fragment
            )
        if not source_ids:
            findings.append(f"{claim.claim_id}: no source-bound evidence")
            continue
        if len(source_ids) != 1:
            findings.append(
                f"{claim.claim_id}: Claim is not source-local ({', '.join(sorted(source_ids))})"
            )
            continue
        source_id = next(iter(source_ids))
        if source_id not in covered_sources:
            continue
        entries.append(
            {
                "claim_id": claim.claim_id,
                "pinned_claim_revision": claim.revision,
                "claim_revision_sha256": semantic_record_sha(claim),
                "source_id": source_id,
            }
        )
    if findings:
        raise ViewpointFoundationValidationError(findings)
    entries.sort(key=lambda item: (item["claim_id"], item["pinned_claim_revision"]))
    manifest_payload = {
        "schema_version": CLAIM_MANIFEST_VERSION,
        "coverage_snapshot_id": coverage.coverage_snapshot_id,
        "claims": entries,
    }
    manifest_payload["manifest_sha256"] = sha256_json(manifest_payload)
    return manifest_payload


def build_resolution_ledger(
    claim_manifest: Mapping[str, Any],
    proposed_rows: Sequence[Mapping[str, Any] | ViewpointResolutionRow],
    *,
    coverage_snapshot_id: str,
    eligibility_policy_version: str = SOURCE_ELIGIBILITY_POLICY_VERSION,
    candidate_blocking_version: str = CANDIDATE_BLOCKING_VERSION,
) -> ViewpointResolutionLedgerRecord:
    """Reconcile proposals to the manifest and materialize missing rows as unprocessed."""

    if claim_manifest.get("schema_version") != CLAIM_MANIFEST_VERSION:
        raise ViewpointFoundationValidationError(["unsupported input Claim manifest"])
    if claim_manifest.get("coverage_snapshot_id") != coverage_snapshot_id:
        raise ViewpointFoundationValidationError(["Claim manifest coverage snapshot mismatch"])
    payload_without_sha = dict(claim_manifest)
    stated_manifest_sha = str(payload_without_sha.pop("manifest_sha256", ""))
    actual_manifest_sha = sha256_json(payload_without_sha)
    if stated_manifest_sha != actual_manifest_sha:
        raise ViewpointFoundationValidationError(["input Claim manifest SHA mismatch"])

    manifest_entries: dict[tuple[str, int], dict[str, Any]] = {}
    findings: list[str] = []
    for item in claim_manifest.get("claims") or []:
        key = (str(item.get("claim_id") or ""), int(item.get("pinned_claim_revision") or 0))
        if key in manifest_entries:
            findings.append(f"{key[0]}@{key[1]}: duplicate input manifest row")
        manifest_entries[key] = dict(item)
    proposals: dict[tuple[str, int], ViewpointResolutionRow] = {}
    for raw in proposed_rows:
        row = _as_model(raw, ViewpointResolutionRow)
        key = (row.claim_id, row.pinned_claim_revision)
        if key in proposals:
            findings.append(f"{key[0]}@{key[1]}: duplicate proposed resolution")
        elif key not in manifest_entries:
            findings.append(f"{key[0]}@{key[1]}: proposed resolution is outside input manifest")
        elif row.claim_revision_sha256 != manifest_entries[key]["claim_revision_sha256"]:
            findings.append(f"{key[0]}@{key[1]}: proposed resolution Claim SHA mismatch")
        else:
            proposals[key] = row
    if findings:
        raise ViewpointFoundationValidationError(findings)

    rows: list[ViewpointResolutionRow] = []
    for key, entry in sorted(manifest_entries.items()):
        rows.append(
            proposals.get(key)
            or ViewpointResolutionRow(
                claim_id=key[0],
                pinned_claim_revision=key[1],
                claim_revision_sha256=str(entry["claim_revision_sha256"]),
                processing_status="unprocessed",
            )
        )
    counts = {
        "input_claim_count": len(rows),
        "resolved_count": sum(item.processing_status == "resolved" for item in rows),
        "source_ineligible_count": sum(
            item.processing_status == "source_ineligible" for item in rows
        ),
        "deferred_count": sum(item.processing_status == "deferred" for item in rows),
        "unprocessed_count": sum(item.processing_status == "unprocessed" for item in rows),
    }
    build_payload = {
        "builder_version": LEDGER_BUILDER_VERSION,
        "coverage_snapshot_id": coverage_snapshot_id,
        "input_claim_manifest_sha256": stated_manifest_sha,
        "eligibility_policy_version": eligibility_policy_version,
        "candidate_blocking_version": candidate_blocking_version,
        "rows": [item.model_dump(mode="json") for item in rows],
    }
    fingerprint = sha256_json(build_payload)
    record_payload = {
        "resolution_ledger_id": f"VRL-{fingerprint[:20]}",
        "coverage_snapshot_id": coverage_snapshot_id,
        "input_claim_manifest_sha256": stated_manifest_sha,
        "eligibility_policy_version": eligibility_policy_version,
        "candidate_blocking_version": candidate_blocking_version,
        "rows": rows,
        "statistics": ViewpointResolutionStatistics(**counts),
        "coverage_status": "complete" if counts["unprocessed_count"] == 0 else "partial",
        "build_fingerprint_sha256": fingerprint,
        "artifact_sha256": "pending",
    }
    artifact_payload = dict(record_payload)
    artifact_payload["rows"] = [item.model_dump(mode="json") for item in rows]
    artifact_payload["statistics"] = counts
    artifact_payload.pop("artifact_sha256")
    record_payload["artifact_sha256"] = sha256_json(artifact_payload)
    return ViewpointResolutionLedgerRecord.model_validate(record_payload)


def build_identity_candidate_seeds(
    claim_manifest: Mapping[str, Any],
    claim_relations: Sequence[Mapping[str, Any] | ClaimRelationRecord],
    constraints: Sequence[Mapping[str, Any] | ClaimRelationConstraintRecord],
    existing_links: Sequence[Mapping[str, Any] | ViewpointClaimLinkRecord] = (),
    *,
    candidate_blocking_version: str = CANDIDATE_BLOCKING_VERSION,
) -> list[ViewpointIdentityCandidateRecord]:
    """Project pairwise duplicate seeds without inventing a transitive closure."""

    if claim_manifest.get("schema_version") != CLAIM_MANIFEST_VERSION:
        raise ViewpointFoundationValidationError(["unsupported input Claim manifest"])
    manifest_payload = dict(claim_manifest)
    stated_manifest_sha = str(manifest_payload.pop("manifest_sha256", ""))
    if stated_manifest_sha != sha256_json(manifest_payload):
        raise ViewpointFoundationValidationError(["input Claim manifest SHA mismatch"])
    coverage_snapshot_id = str(claim_manifest.get("coverage_snapshot_id") or "")
    manifest_claims = {
        str(item["claim_id"]): int(item["pinned_claim_revision"])
        for item in claim_manifest.get("claims") or []
    }
    if not coverage_snapshot_id or not manifest_claims:
        raise ViewpointFoundationValidationError(
            ["candidate generation requires a non-empty Claim manifest"]
        )

    owners: dict[str, set[str]] = defaultdict(set)
    for raw in existing_links:
        link = _as_model(raw, ViewpointClaimLinkRecord)
        if (
            link.effective_state == "active"
            and link.link_type == "equivalent_full"
            and link.review_status
            in {"system_approved", "human_approved", "approved"}
            and manifest_claims.get(link.claim_id) == link.pinned_claim_revision
        ):
            owners[link.claim_id].add(link.viewpoint_id)
    conflicts = [claim_id for claim_id, values in owners.items() if len(values) > 1]
    if conflicts:
        raise ViewpointFoundationValidationError(
            [f"{claim_id}: multiple active equivalent_full memberships" for claim_id in conflicts]
        )

    forbidden_duplicate_pairs: set[tuple[str, str]] = set()
    for raw in constraints:
        item = _as_model(raw, ClaimRelationConstraintRecord)
        if (
            item.review_status in APPROVED_CONSTRAINT_STATUSES
            and "duplicate" in item.forbidden_relation_types
        ):
            forbidden_duplicate_pairs.add(tuple(sorted((item.source_id, item.target_id))))

    duplicate_relations: dict[tuple[str, str], list[str]] = defaultdict(list)
    material_relations: dict[tuple[str, str], list[str]] = defaultdict(list)
    findings: list[str] = []
    for raw in claim_relations:
        relation = _as_model(raw, ClaimRelationRecord)
        if relation.review_status not in REVIEWED_DUPLICATE_STATUSES:
            continue
        pair = tuple(sorted((relation.from_id, relation.to_id)))
        if pair[0] == pair[1]:
            if relation.relation_type == "duplicate":
                findings.append(f"{relation.claim_relation_id}: duplicate self relation")
            continue
        missing = set(pair) - set(manifest_claims)
        if missing:
            if relation.relation_type == "duplicate":
                findings.append(
                    f"{relation.claim_relation_id}: duplicate seed outside Claim manifest: "
                    f"{', '.join(sorted(missing))}"
                )
            continue
        if relation.relation_type == "duplicate":
            duplicate_relations[pair].append(relation.claim_relation_id)
        elif relation.relation_type in {"unrelated", "contrasts", "qualifies", "supersedes"}:
            material_relations[pair].append(relation.claim_relation_id)
    if findings:
        raise ViewpointFoundationValidationError(findings)

    candidates: list[ViewpointIdentityCandidateRecord] = []
    claims_in_pairs: set[str] = set()
    global_fingerprint = sha256_json(
        {
            "candidate_blocking_version": candidate_blocking_version,
            "claim_manifest_sha256": claim_manifest.get("manifest_sha256"),
            "duplicate_relations": {
                "|".join(pair): sorted(ids) for pair, ids in sorted(duplicate_relations.items())
            },
            "material_relations": {
                "|".join(pair): sorted(ids) for pair, ids in sorted(material_relations.items())
            },
            "forbidden_duplicate_pairs": [list(pair) for pair in sorted(forbidden_duplicate_pairs)],
            "owners": {claim_id: sorted(values) for claim_id, values in sorted(owners.items())},
        }
    )

    def append_candidate(
        claim_ids: Sequence[str], relation_ids: Sequence[str], *, blockers: Sequence[str] = ()
    ) -> None:
        viewpoint_ids = sorted(
            {owner for claim_id in claim_ids for owner in owners.get(claim_id, set())}
        )
        if blockers or len(viewpoint_ids) > 1:
            action = "defer"
            blocker_codes = sorted(
                set(blockers)
                | ({"different_active_viewpoints"} if len(viewpoint_ids) > 1 else set())
            )
        elif viewpoint_ids:
            action = "match_existing"
            blocker_codes = []
        else:
            action = "create_new"
            blocker_codes = []
        identity_payload = {
            "claims": sorted(claim_ids),
            "viewpoints": viewpoint_ids,
            "relations": sorted(relation_ids),
            "action": action,
            "blockers": blocker_codes,
            "coverage_snapshot_id": coverage_snapshot_id,
            "generation_fingerprint": global_fingerprint,
        }
        candidates.append(
            ViewpointIdentityCandidateRecord(
                identity_candidate_id=f"VIC-{sha256_json(identity_payload)[:20]}",
                candidate_claim_ids=sorted(claim_ids),
                candidate_viewpoint_ids=viewpoint_ids,
                seed_relation_ids=sorted(relation_ids),
                proposed_action=action,
                coverage_snapshot_id=coverage_snapshot_id,
                blocker_codes=blocker_codes,
                generation_fingerprint=global_fingerprint,
            )
        )

    adjacency: dict[str, set[str]] = defaultdict(set)
    for left, right in duplicate_relations:
        adjacency[left].add(right)
        adjacency[right].add(left)
    unseen = set(adjacency)
    while unseen:
        stack = [min(unseen)]
        component: set[str] = set()
        while stack:
            claim_id = stack.pop()
            if claim_id in component:
                continue
            component.add(claim_id)
            unseen.discard(claim_id)
            stack.extend(sorted(adjacency[claim_id] - component, reverse=True))
        component_ids = sorted(component)
        claims_in_pairs.update(component_ids)
        component_pairs = {
            pair for pair in duplicate_relations if set(pair).issubset(component)
        }
        component_material_pairs = {
            pair for pair in material_relations if set(pair).issubset(component)
        }
        component_forbidden_pairs = {
            pair for pair in forbidden_duplicate_pairs if set(pair).issubset(component)
        }
        relation_ids = sorted(
            {
                relation_id
                for pair in component_pairs
                for relation_id in duplicate_relations[pair]
            }
        )
        blockers: list[str] = []
        if component_forbidden_pairs:
            blockers.append("approved_negative_duplicate_constraint")
        if component_material_pairs:
            blockers.append("reviewed_material_relation")
        append_candidate(component_ids, relation_ids, blockers=blockers)
    for claim_id in sorted(set(manifest_claims) - claims_in_pairs):
        if owners.get(claim_id):
            continue
        append_candidate([claim_id], [])
    return sorted(candidates, key=lambda item: item.identity_candidate_id)


def build_foundation_quality_report(
    *,
    scope_ids: Sequence[str],
    coverage_snapshot: Mapping[str, Any] | ViewpointCoverageSnapshotRecord,
    ledger: Mapping[str, Any] | ViewpointResolutionLedgerRecord,
    claims: Sequence[Mapping[str, Any] | ClaimRecord],
    evidence_steps: Sequence[Mapping[str, Any]],
    source_fragments: Sequence[Mapping[str, Any]],
    candidate_regression_artifact_sha256: str,
    candidate_regression_passed: bool,
    approved_source_statuses: frozenset[str] = frozenset(
        {"approved", "system_approved", "human_approved"}
    ),
    source_eligibility_attestations: Mapping[str, str] | None = None,
) -> ViewpointQualityReportRecord:
    """Generate the foundation's non-compensating identity-decision quality report."""

    coverage = _as_model(coverage_snapshot, ViewpointCoverageSnapshotRecord)
    resolution = _as_model(ledger, ViewpointResolutionLedgerRecord)
    claim_by_id = {
        item.claim_id: item for item in (_as_model(raw, ClaimRecord) for raw in claims)
    }
    evidence_by_id = {
        str(item["evidence_step_id"]): dict(item) for item in evidence_steps
    }
    fragment_by_id = {
        str(item["fragment_id"]): dict(item) for item in source_fragments
    }
    eligibility_attestations = dict(source_eligibility_attestations or {})
    failures: list[ViewpointQualityFailure] = []
    dimensions: list[ViewpointQualityDimension] = []

    provenance_bad: list[str] = []
    maturity_bad: list[str] = []
    for row in resolution.rows:
        claim = claim_by_id.get(row.claim_id)
        if (
            claim is None
            or claim.revision != row.pinned_claim_revision
            or semantic_record_sha(claim) != row.claim_revision_sha256
        ):
            provenance_bad.append(row.claim_id)
        elif row.processing_status != "source_ineligible":
            attested = bool(eligibility_attestations.get(row.claim_id))
            usable_evidence = False
            for evidence_id in claim.evidence_step_ids:
                step = evidence_by_id.get(evidence_id)
                allowed_support = {"eligible", "eligible_with_label"} | (
                    {"eligible_candidate"} if attested else set()
                )
                if not step or step.get("support_eligibility") not in allowed_support:
                    continue
                bound = [
                    fragment_by_id.get(value) for value in evidence_fragment_ids(step)
                ]
                if attested:
                    if (
                        step.get("support_eligibility")
                        not in {"eligible", "eligible_candidate", "eligible_with_label"}
                        or not bound
                        or any(
                            not fragment
                            or fragment.get("anchor_state")
                            not in {
                                "source_version_bound", "canonical_citation_bound",
                                "verified", "valid",
                            }
                            or not fragment.get("source_sha256")
                            for fragment in bound
                        )
                    ):
                        continue
                else:
                    citation_ids = set(map(str, step.get("citation_ids") or []))
                    if not citation_ids or not bound or any(
                        not fragment
                        or fragment.get("anchor_state")
                        not in {"source_version_bound", "canonical_citation_bound", "verified", "valid"}
                        or not fragment.get("source_sha256")
                        or str(fragment.get("citation_id") or "") not in citation_ids
                        for fragment in bound
                    ):
                        continue
                usable_evidence = True
                break
            if not usable_evidence:
                provenance_bad.append(row.claim_id)
            if claim.review_status not in approved_source_statuses and not attested:
                maturity_bad.append(row.claim_id)
    if provenance_bad:
        failures.append(
            ViewpointQualityFailure(
                code="claim_revision_or_sha_mismatch",
                dimension="provenance_integrity",
                record_ids=sorted(set(provenance_bad)),
                detail=(
                    "Ledger Claim dependency is missing or does not match its "
                    "pinned revision/SHA."
                ),
            )
        )
    dimensions.append(
        ViewpointQualityDimension(
            dimension="provenance_integrity",
            applicable=True,
            minimum_policy="all_pinned_claim_revisions_resolve_and_match_sha",
            observed={"invalid_claim_dependencies": len(set(provenance_bad))},
            status="fail" if provenance_bad else "pass",
            evidence_artifact_sha256s=[resolution.artifact_sha256],
        )
    )
    if maturity_bad:
        failures.append(
            ViewpointQualityFailure(
                code="source_claim_below_approval_policy",
                dimension="source_maturity",
                record_ids=sorted(set(maturity_bad)),
                detail="A resolved/deferred Claim is below the identity-decision source policy.",
            )
        )
    dimensions.append(
        ViewpointQualityDimension(
            dimension="source_maturity",
            applicable=True,
            minimum_policy="all_in_scope_claims_meet_viewpoint_source_eligibility_v1",
            observed={"ineligible_claim_dependencies": len(set(maturity_bad))},
            status="fail" if maturity_bad else "pass",
            evidence_artifact_sha256s=[resolution.artifact_sha256],
        )
    )

    unresolved = resolution.statistics.unprocessed_count
    deferred = resolution.statistics.deferred_count
    if unresolved or deferred:
        failures.append(
            ViewpointQualityFailure(
                code="identity_scope_not_resolved",
                dimension="resolution_coverage",
                record_ids=[
                    row.claim_id
                    for row in resolution.rows
                    if row.processing_status in {"unprocessed", "deferred"}
                ],
                detail="Identity-decision scope contains unprocessed or deferred Claim rows.",
            )
        )
    dimensions.append(
        ViewpointQualityDimension(
            dimension="resolution_coverage",
            applicable=True,
            minimum_policy="exact_once_and_identity_scope_unprocessed_deferred_zero",
            observed={"unprocessed": unresolved, "deferred": deferred},
            status="fail" if unresolved or deferred else "pass",
            evidence_artifact_sha256s=[resolution.artifact_sha256],
        )
    )

    identity_blocked = [
        row.claim_id
        for row in resolution.rows
        if row.processing_status == "resolved" and row.blocker_codes
    ]
    if identity_blocked:
        failures.append(
            ViewpointQualityFailure(
                code="resolved_identity_retains_blocker",
                dimension="identity_precision",
                record_ids=identity_blocked,
                detail="A resolved identity row still carries a blocker.",
            )
        )
    dimensions.append(
        ViewpointQualityDimension(
            dimension="identity_precision",
            applicable=True,
            minimum_policy="resolved_rows_have_no_identity_blockers",
            observed={"blocked_resolved_rows": len(identity_blocked)},
            status="fail" if identity_blocked else "pass",
            evidence_artifact_sha256s=[resolution.artifact_sha256],
        )
    )

    if not candidate_regression_passed:
        failures.append(
            ViewpointQualityFailure(
                code="candidate_regression_failed",
                dimension="candidate_recall",
                detail="Golden/adversarial candidate generation suite did not pass.",
            )
        )
    dimensions.append(
        ViewpointQualityDimension(
            dimension="candidate_recall",
            applicable=True,
            minimum_policy="golden_and_adversarial_candidate_suite_passes",
            observed={"regression_passed": candidate_regression_passed},
            status="pass" if candidate_regression_passed else "fail",
            evidence_artifact_sha256s=[candidate_regression_artifact_sha256],
        )
    )

    for dimension, reason in (
        ("route_fidelity", "ArgumentRoute belongs to the next implementation outcome."),
        ("temporal_correctness", "No temporal or supersedes decision is in this scope."),
        ("consumer_projection_integrity", "No consumer projection is built by this card."),
    ):
        dimensions.append(
            ViewpointQualityDimension(
                dimension=dimension,
                applicable=False,
                minimum_policy="not_applicable_to_foundation_identity_decision",
                status="not_applicable",
                reason_not_applicable=reason,
            )
        )

    dimensions.sort(key=lambda item: QUALITY_DIMENSIONS.index(item.dimension))
    input_shas = sorted(
        {
            coverage.sources_sha256,
            resolution.artifact_sha256,
            candidate_regression_artifact_sha256,
            *eligibility_attestations.values(),
        }
    )
    build_payload = {
        "validator_version": QUALITY_VALIDATOR_VERSION,
        "scope_kind": "identity_decision",
        "scope_ids": sorted(scope_ids),
        "coverage_snapshot_id": coverage.coverage_snapshot_id,
        "resolution_ledger_id": resolution.resolution_ledger_id,
        "input_artifact_sha256s": input_shas,
        "dimensions": [item.model_dump(mode="json") for item in dimensions],
        "hard_failures": [item.model_dump(mode="json") for item in failures],
    }
    fingerprint = sha256_json(build_payload)
    record_payload = {
        "quality_report_id": f"VQR-{fingerprint[:20]}",
        "scope_kind": "identity_decision",
        "scope_ids": sorted(scope_ids),
        "coverage_snapshot_id": coverage.coverage_snapshot_id,
        "resolution_ledger_id": resolution.resolution_ledger_id,
        "input_artifact_sha256s": input_shas,
        "dimensions": dimensions,
        "hard_failures": failures,
        "eligibility_decision": "fail" if failures else "pass",
        "validator_version": QUALITY_VALIDATOR_VERSION,
        "build_fingerprint_sha256": fingerprint,
        "artifact_sha256": "pending",
    }
    artifact_payload = dict(record_payload)
    artifact_payload["dimensions"] = [item.model_dump(mode="json") for item in dimensions]
    artifact_payload["hard_failures"] = [item.model_dump(mode="json") for item in failures]
    artifact_payload.pop("artifact_sha256")
    record_payload["artifact_sha256"] = sha256_json(artifact_payload)
    return ViewpointQualityReportRecord.model_validate(record_payload)


def validate_foundation_change_set(
    normalized: Mapping[str, Mapping[str, Mapping[str, Any]]],
    existing: Mapping[tuple[str, str], Mapping[str, Any]],
) -> None:
    """Validate viewpoint cross-record invariants before a ChangeSet is planned."""

    viewpoint_collections = {
        "viewpoint_coverage_snapshots",
        "canonical_viewpoints",
        "viewpoint_revisions",
        "viewpoint_claim_links",
        "viewpoint_proposition_units",
        "viewpoint_proposition_unit_links",
        "viewpoint_atomic_coverage_snapshots",
        "viewpoint_atomic_resolution_ledgers",
        "viewpoint_atomic_quality_reports",
        "viewpoint_automated_promotion_decisions",
        "argument_routes",
        "argument_route_revisions",
        "argument_route_attestations",
        "viewpoint_relations",
        "viewpoint_identity_candidates",
        "viewpoint_identity_decisions",
        "viewpoint_resolution_ledgers",
        "viewpoint_quality_reports",
    }
    if not any(normalized.get(collection) for collection in viewpoint_collections):
        return

    immutable_collections = {
        "viewpoint_coverage_snapshots",
        "viewpoint_revisions",
        "viewpoint_proposition_units",
        "viewpoint_proposition_unit_links",
        "viewpoint_atomic_coverage_snapshots",
        "viewpoint_atomic_resolution_ledgers",
        "viewpoint_atomic_quality_reports",
        "viewpoint_automated_promotion_decisions",
        "argument_route_revisions",
        "argument_route_attestations",
        "viewpoint_identity_candidates",
        "viewpoint_identity_decisions",
        "viewpoint_resolution_ledgers",
        "viewpoint_quality_reports",
    }
    immutable_findings: list[str] = []
    for collection in immutable_collections:
        for object_id, incoming in normalized.get(collection, {}).items():
            current = existing.get((collection, object_id))
            if current and current.get("retired_at") is None:
                current_payload = dict(current.get("payload") or {})
                if semantic_record_sha(incoming) != semantic_record_sha(current_payload):
                    immutable_findings.append(
                        f"{collection}/{object_id}: immutable record cannot be updated in place"
                    )
    if immutable_findings:
        raise ViewpointFoundationValidationError(immutable_findings)

    def payloads(collection: str) -> dict[str, dict[str, Any]]:
        values = {
            object_id: dict(row.get("payload") or {})
            for (stored_collection, object_id), row in existing.items()
            if stored_collection == collection and row.get("retired_at") is None
        }
        values.update(
            {
                object_id: dict(payload)
                for object_id, payload in normalized.get(collection, {}).items()
            }
        )
        return values

    sources = payloads("source_documents")
    claims = payloads("claims")
    evidence_steps = payloads("evidence_steps")
    source_fragments = payloads("source_fragments")
    coverages = payloads("viewpoint_coverage_snapshots")
    viewpoints = payloads("canonical_viewpoints")
    revisions = payloads("viewpoint_revisions")
    links = payloads("viewpoint_claim_links")
    proposition_units = payloads("viewpoint_proposition_units")
    proposition_unit_links = payloads("viewpoint_proposition_unit_links")
    atomic_coverages = payloads("viewpoint_atomic_coverage_snapshots")
    atomic_ledgers = payloads("viewpoint_atomic_resolution_ledgers")
    atomic_reports = payloads("viewpoint_atomic_quality_reports")
    automated_promotions = payloads("viewpoint_automated_promotion_decisions")
    claim_relations = payloads("claim_relations")
    candidates = payloads("viewpoint_identity_candidates")
    decisions = payloads("viewpoint_identity_decisions")
    ledgers = payloads("viewpoint_resolution_ledgers")
    reports = payloads("viewpoint_quality_reports")
    findings: list[str] = []

    for snapshot_id, snapshot in normalized.get("viewpoint_coverage_snapshots", {}).items():
        serialized_sources = list(snapshot.get("sources") or [])
        actual_sources_sha = sha256_json(serialized_sources)
        if snapshot.get("sources_sha256") != actual_sources_sha:
            findings.append(f"{snapshot_id}: sources_sha256 mismatch")
        coverage_identity = {
            "builder_version": COVERAGE_BUILDER_VERSION,
            "source_universe_manifest_id": snapshot.get("source_universe_manifest_id"),
            "source_universe_manifest_sha256": snapshot.get("source_universe_manifest_sha256"),
            "historical_survey_baseline_id": snapshot.get("historical_survey_baseline_id"),
            "sources_sha256": actual_sources_sha,
            "coverage_status": snapshot.get("coverage_status"),
            "created_at": snapshot.get("created_at"),
        }
        if snapshot_id != f"CVS-{sha256_json(coverage_identity)[:20]}":
            findings.append(f"{snapshot_id}: unstable coverage snapshot id")
        for source in snapshot.get("sources") or []:
            source_id = str(source["source_id"])
            stored = sources.get(source_id)
            if not stored:
                findings.append(f"{snapshot_id}: missing source {source_id}")
            elif stored.get("source_sha256") != source.get("source_sha256"):
                findings.append(f"{snapshot_id}: source SHA mismatch for {source_id}")
            elif source.get("source_revision_id") != f"{source_id}@{stored.get('revision', 1)}":
                findings.append(f"{snapshot_id}: source revision mismatch for {source_id}")

    for revision_id, revision in normalized.get("viewpoint_revisions", {}).items():
        viewpoint_id = str(revision["viewpoint_id"])
        if viewpoint_id not in viewpoints:
            findings.append(f"{revision_id}: missing viewpoint {viewpoint_id}")
        for decision_id in revision.get("provenance", {}).get("basis_identity_decision_ids", []):
            decision = decisions.get(str(decision_id))
            if not decision:
                findings.append(f"{revision_id}: missing identity decision {decision_id}")
            elif (
                revision.get("review_status")
                in {"system_approved", "human_approved", "approved"}
                and decision.get("review_status")
                not in {"system_approved", "human_approved", "approved"}
            ):
                findings.append(f"{revision_id}: approved revision depends on unapproved decision")
        supersedes = revision.get("supersedes_revision_id")
        if supersedes:
            prior = revisions.get(str(supersedes))
            if not prior or prior.get("viewpoint_id") != viewpoint_id:
                findings.append(f"{revision_id}: invalid superseded revision {supersedes}")
    for viewpoint_id, viewpoint in normalized.get("canonical_viewpoints", {}).items():
        revision_id = str(viewpoint["current_revision_id"])
        revision = revisions.get(revision_id)
        if not revision:
            findings.append(f"{viewpoint_id}: missing current revision {revision_id}")
        elif revision.get("viewpoint_id") != viewpoint_id:
            findings.append(f"{viewpoint_id}: current revision belongs to another viewpoint")
        elif (
            viewpoint.get("review_status")
            in {"system_approved", "human_approved", "approved"}
            and revision.get("review_status")
            not in {"system_approved", "human_approved", "approved"}
        ):
            findings.append(f"{viewpoint_id}: approved viewpoint has unapproved current revision")
        redirect = viewpoint.get("redirect_to_viewpoint_id")
        if redirect and str(redirect) not in viewpoints:
            findings.append(f"{viewpoint_id}: missing redirect target {redirect}")
        candidate_id = str(viewpoint["created_from_candidate_id"])
        atomic_candidate_ids = {
            str(item.get("viewpoint_candidate_id"))
            for item in atomic_coverages.values()
        }
        if candidate_id not in candidates and candidate_id not in atomic_candidate_ids:
            findings.append(f"{viewpoint_id}: missing origin candidate {candidate_id}")

    active_full: dict[tuple[str, int], set[str]] = defaultdict(set)
    active_link_keys: dict[tuple[str, str, int, str], list[str]] = defaultdict(list)
    for link_id, link in links.items():
        claim_id = str(link["claim_id"])
        viewpoint_id = str(link["viewpoint_id"])
        revision_id = str(link["validated_against_viewpoint_revision_id"])
        claim = claims.get(claim_id)
        if viewpoint_id not in viewpoints:
            findings.append(f"{link_id}: missing viewpoint {viewpoint_id}")
        if (
            revision_id not in revisions
            or revisions.get(revision_id, {}).get("viewpoint_id") != viewpoint_id
        ):
            findings.append(f"{link_id}: invalid viewpoint revision {revision_id}")
        elif (
            link.get("effective_state") == "active"
            and viewpoints.get(viewpoint_id, {}).get("current_revision_id") != revision_id
        ):
            findings.append(f"{link_id}: active link is not validated against current revision")
        if not claim:
            findings.append(f"{link_id}: missing claim {claim_id}")
        elif int(claim.get("revision", 1)) != int(link["pinned_claim_revision"]):
            findings.append(f"{link_id}: pinned Claim revision mismatch")
        if str(link["decision_id"]) not in decisions:
            findings.append(f"{link_id}: missing decision {link['decision_id']}")
        else:
            decision = decisions[str(link["decision_id"])]
            allowed = {
                (str(item["claim_id"]), str(item["link_type"]))
                for item in decision.get("claim_link_decisions") or []
            }
            if (claim_id, str(link["link_type"])) not in allowed:
                findings.append(f"{link_id}: claim/link type is not authorized by its decision")
            if decision.get("resolved_viewpoint_id") not in {None, viewpoint_id}:
                findings.append(f"{link_id}: decision resolves a different viewpoint")
            if link.get("effective_state") == "active" and decision.get("review_status") not in {
                "system_approved",
                "human_approved",
                "approved",
            }:
                findings.append(f"{link_id}: active link depends on an unapproved decision")
        component = link.get("component_locator")
        if component and claim and component.get("claim_sha256") != semantic_record_sha(claim):
            findings.append(f"{link_id}: component locator Claim SHA mismatch")
        for relation_id in link.get("supporting_relation_ids") or []:
            if str(relation_id) not in claim_relations:
                findings.append(f"{link_id}: missing supporting relation {relation_id}")
        if link.get("effective_state") == "active":
            locator_sha = sha256_json(component) if component else "full"
            active_link_keys[
                (viewpoint_id, claim_id, int(link["pinned_claim_revision"]), locator_sha)
            ].append(link_id)
            if link.get("link_type") == "equivalent_full":
                active_full[(claim_id, int(link["pinned_claim_revision"]))].add(viewpoint_id)
    for key, link_ids in active_link_keys.items():
        if len(link_ids) > 1:
            findings.append(
                f"{key[0]}/{key[1]}@{key[2]}: duplicate active Claim links "
                + ", ".join(sorted(link_ids))
            )
    for key, owners in active_full.items():
        if len(owners) > 1:
            findings.append(f"{key[0]}@{key[1]}: multiple active equivalent_full memberships")

    for unit_id, unit in proposition_units.items():
        claim_id = str(unit["parent_claim_id"])
        claim = claims.get(claim_id)
        if not claim:
            findings.append(f"{unit_id}: missing parent Claim {claim_id}")
            continue
        if int(claim.get("revision", 1)) != int(unit["pinned_claim_revision"]):
            findings.append(f"{unit_id}: pinned Claim revision mismatch")
        if unit.get("claim_revision_sha256") != semantic_record_sha(claim):
            findings.append(f"{unit_id}: parent Claim SHA mismatch")
        statement = str(claim.get("statement") or "")
        for span in unit.get("claim_statement_spans") or []:
            start = int(span["start_char"])
            end = int(span["end_char"])
            if statement[start:end] != span.get("exact_text"):
                findings.append(f"{unit_id}: statement span does not match pinned Claim")
        for binding in unit.get("evidence_bindings") or []:
            evidence_id = str(binding["evidence_step_id"])
            fragment_id = str(binding["source_fragment_id"])
            evidence = evidence_steps.get(evidence_id)
            fragment = source_fragments.get(fragment_id)
            if not evidence:
                findings.append(f"{unit_id}: missing EvidenceStep {evidence_id}")
            elif fragment_id not in evidence_fragment_ids(evidence):
                findings.append(f"{unit_id}: EvidenceStep does not bind fragment {fragment_id}")
            if not fragment:
                findings.append(f"{unit_id}: missing SourceFragment {fragment_id}")
            elif fragment.get("source_id") != unit.get("source_id"):
                findings.append(f"{unit_id}: SourceFragment belongs to another source")

    active_unit_owners: dict[str, set[str]] = defaultdict(set)
    active_unit_link_keys: dict[tuple[str, str], list[str]] = defaultdict(list)
    for link_id, link in proposition_unit_links.items():
        viewpoint_id = str(link["viewpoint_id"])
        revision_id = str(link["validated_against_viewpoint_revision_id"])
        unit_id = str(link["proposition_unit_id"])
        decision = decisions.get(str(link["decision_id"]))
        if viewpoint_id not in viewpoints:
            findings.append(f"{link_id}: missing viewpoint {viewpoint_id}")
        if revisions.get(revision_id, {}).get("viewpoint_id") != viewpoint_id:
            findings.append(f"{link_id}: invalid viewpoint revision {revision_id}")
        elif (
            link.get("effective_state") == "active"
            and viewpoints.get(viewpoint_id, {}).get("current_revision_id") != revision_id
        ):
            findings.append(f"{link_id}: active link is not validated against current revision")
        unit = proposition_units.get(unit_id)
        if not unit:
            findings.append(f"{link_id}: missing proposition unit {unit_id}")
        elif unit.get("effective_state") != "active":
            findings.append(f"{link_id}: proposition unit is not active")
        if not decision:
            findings.append(f"{link_id}: missing decision {link['decision_id']}")
        else:
            allowed = {
                (str(item["proposition_unit_id"]), str(item["link_type"]))
                for item in decision.get("proposition_unit_link_decisions") or []
            }
            if (unit_id, str(link["link_type"])) not in allowed:
                findings.append(f"{link_id}: proposition unit link is not authorized")
            if decision.get("resolved_viewpoint_id") not in {None, viewpoint_id}:
                findings.append(f"{link_id}: decision resolves a different viewpoint")
            if link.get("effective_state") == "active" and decision.get("review_status") not in {
                "system_approved", "human_approved", "approved",
            }:
                findings.append(f"{link_id}: active link depends on an unapproved decision")
        if link.get("effective_state") == "active":
            active_unit_link_keys[(viewpoint_id, unit_id)].append(link_id)
            active_unit_owners[unit_id].add(viewpoint_id)
    for key, link_ids in active_unit_link_keys.items():
        if len(link_ids) > 1:
            findings.append(
                f"{key[0]}/{key[1]}: duplicate active proposition unit links "
                + ", ".join(sorted(link_ids))
            )
    for unit_id, owners in active_unit_owners.items():
        if len(owners) > 1:
            findings.append(
                f"{unit_id}: multiple active CanonicalViewpoint memberships"
            )

    for coverage_id, coverage in normalized.get(
        "viewpoint_atomic_coverage_snapshots", {}
    ).items():
        coverage_unit_ids = list(coverage.get("proposition_unit_ids") or [])
        actual_units = {
            unit_id: proposition_units.get(str(unit_id))
            for unit_id in coverage_unit_ids
        }
        if any(item is None for item in actual_units.values()):
            missing = sorted(
                unit_id for unit_id, item in actual_units.items() if item is None
            )
            findings.append(f"{coverage_id}: missing proposition units {missing}")
            continue
        actual_claim_ids = sorted(
            {str(item["parent_claim_id"]) for item in actual_units.values() if item}
        )
        actual_source_ids = sorted(
            {str(item["source_id"]) for item in actual_units.values() if item}
        )
        if actual_claim_ids != list(coverage.get("claim_ids") or []):
            findings.append(f"{coverage_id}: Claim denominator mismatch")
        if actual_source_ids != list(coverage.get("source_ids") or []):
            findings.append(f"{coverage_id}: source denominator mismatch")

    for ledger_id, ledger in normalized.get(
        "viewpoint_atomic_resolution_ledgers", {}
    ).items():
        coverage = atomic_coverages.get(str(ledger["atomic_coverage_snapshot_id"]))
        if not coverage:
            findings.append(f"{ledger_id}: missing atomic coverage snapshot")
            continue
        ledger_unit_ids = [str(row["proposition_unit_id"]) for row in ledger.get("rows") or []]
        if ledger_unit_ids != list(coverage.get("proposition_unit_ids") or []):
            findings.append(f"{ledger_id}: atomic unit denominator mismatch")
        member_unit_ids: list[str] = []
        for row in ledger.get("rows") or []:
            unit_id = str(row["proposition_unit_id"])
            unit = proposition_units.get(unit_id)
            if not unit:
                findings.append(f"{ledger_id}: missing proposition unit {unit_id}")
                continue
            if row.get("parent_claim_id") != unit.get("parent_claim_id"):
                findings.append(f"{ledger_id}: parent Claim mismatch for {unit_id}")
            expected_evidence_sha = sha256_json(unit.get("evidence_bindings") or [])
            if row.get("evidence_binding_sha256") != expected_evidence_sha:
                findings.append(f"{ledger_id}: evidence binding SHA mismatch for {unit_id}")
            if row.get("boundary_run_artifact_sha256") != coverage.get(
                "boundary_run_artifact_sha256"
            ):
                findings.append(f"{ledger_id}: boundary binding mismatch for {unit_id}")
            if row.get("disposition") == "member":
                member_unit_ids.append(unit_id)
        active_members = sorted(
            unit_id
            for unit_id in ledger_unit_ids
            if any(
                link.get("effective_state") == "active"
                and str(link.get("proposition_unit_id")) == unit_id
                and str(link.get("viewpoint_id")) == str(ledger["proposed_viewpoint_id"])
                for link in proposition_unit_links.values()
            )
        )
        if sorted(member_unit_ids) != active_members:
            findings.append(f"{ledger_id}: active membership differs from atomic ledger")

    for report_id, report in normalized.get(
        "viewpoint_atomic_quality_reports", {}
    ).items():
        coverage = atomic_coverages.get(str(report["atomic_coverage_snapshot_id"]))
        ledger = atomic_ledgers.get(str(report["atomic_resolution_ledger_id"]))
        if not coverage:
            findings.append(f"{report_id}: missing atomic coverage snapshot")
        if not ledger:
            findings.append(f"{report_id}: missing atomic resolution ledger")
        elif coverage and ledger.get("atomic_coverage_snapshot_id") != coverage.get(
            "atomic_coverage_snapshot_id"
        ):
            findings.append(f"{report_id}: coverage and ledger do not bind")
        if report.get("eligibility_decision") == "pass" and (
            report.get("hard_failures")
            or any(item.get("status") != "pass" for item in report.get("checks") or [])
        ):
            findings.append(f"{report_id}: passing report contains a failed check")

    for promotion_id, promotion in normalized.get(
        "viewpoint_automated_promotion_decisions", {}
    ).items():
        viewpoint_id = str(promotion["viewpoint_id"])
        revision_id = str(promotion["viewpoint_revision_id"])
        identity_decision_id = str(promotion["identity_decision_id"])
        quality = next(
            (
                item
                for item in atomic_reports.values()
                if item.get("artifact_sha256")
                == promotion.get("atomic_quality_report_artifact_sha256")
            ),
            None,
        )
        coverage = next(
            (
                item
                for item in atomic_coverages.values()
                if item.get("artifact_sha256")
                == promotion.get("atomic_coverage_snapshot_artifact_sha256")
            ),
            None,
        )
        ledger = next(
            (
                item
                for item in atomic_ledgers.values()
                if item.get("artifact_sha256")
                == promotion.get("atomic_resolution_ledger_artifact_sha256")
            ),
            None,
        )
        if not quality or quality.get("eligibility_decision") != "pass":
            findings.append(f"{promotion_id}: automated approval requires passing quality")
        if not coverage or not ledger:
            findings.append(f"{promotion_id}: automated approval has missing gate artifacts")
        if viewpoints.get(viewpoint_id, {}).get("current_revision_id") != revision_id:
            findings.append(f"{promotion_id}: viewpoint revision binding mismatch")
        if identity_decision_id not in decisions:
            findings.append(f"{promotion_id}: missing identity decision")
        expected_applied_ids = sorted(
            [
                viewpoint_id,
                revision_id,
                identity_decision_id,
                *(
                    [str(coverage["atomic_coverage_snapshot_id"])]
                    if coverage else []
                ),
                *(
                    [str(ledger["atomic_resolution_ledger_id"])]
                    if ledger else []
                ),
                *(
                    [str(quality["atomic_quality_report_id"])]
                    if quality else []
                ),
                *sorted(
                    unit_id
                    for unit_id, unit in proposition_units.items()
                    if unit.get("effective_state") == "active"
                    and unit_id in set((coverage or {}).get("proposition_unit_ids") or [])
                ),
                *sorted(
                    link_id
                    for link_id, link in proposition_unit_links.items()
                    if link.get("effective_state") == "active"
                    and str(link.get("viewpoint_id")) == viewpoint_id
                ),
            ]
        )
        if list(promotion.get("applied_record_ids") or []) != expected_applied_ids:
            findings.append(f"{promotion_id}: applied record set mismatch")
        if quality and promotion.get("consumer_projection_sha256") != quality.get(
            "consumer_projection_sha256"
        ):
            findings.append(f"{promotion_id}: consumer projection binding mismatch")

    for candidate_id, candidate in normalized.get("viewpoint_identity_candidates", {}).items():
        candidate_identity = {
            "claims": candidate.get("candidate_claim_ids") or [],
            "viewpoints": candidate.get("candidate_viewpoint_ids") or [],
            "relations": candidate.get("seed_relation_ids") or [],
            "action": candidate.get("proposed_action"),
            "blockers": candidate.get("blocker_codes") or [],
            "coverage_snapshot_id": candidate.get("coverage_snapshot_id"),
            "generation_fingerprint": candidate.get("generation_fingerprint"),
        }
        if candidate_id != f"VIC-{sha256_json(candidate_identity)[:20]}":
            findings.append(f"{candidate_id}: unstable identity candidate id")
        for claim_id in candidate.get("candidate_claim_ids") or []:
            if str(claim_id) not in claims:
                findings.append(f"{candidate_id}: missing claim {claim_id}")
        for viewpoint_id in candidate.get("candidate_viewpoint_ids") or []:
            if str(viewpoint_id) not in viewpoints:
                findings.append(f"{candidate_id}: missing viewpoint {viewpoint_id}")
        if str(candidate["coverage_snapshot_id"]) not in coverages:
            findings.append(f"{candidate_id}: missing coverage snapshot")
        for relation_id in candidate.get("seed_relation_ids") or []:
            relation = claim_relations.get(str(relation_id))
            if not relation:
                findings.append(f"{candidate_id}: missing seed relation {relation_id}")
            elif relation.get("relation_type") != "duplicate":
                findings.append(f"{candidate_id}: non-duplicate seed relation {relation_id}")
            elif relation.get("review_status") not in REVIEWED_DUPLICATE_STATUSES:
                findings.append(f"{candidate_id}: unreviewed duplicate seed relation {relation_id}")
    for decision_id, decision in normalized.get("viewpoint_identity_decisions", {}).items():
        candidate = candidates.get(str(decision["identity_candidate_id"]))
        atomic_coverage = next(
            (
                item
                for item in atomic_coverages.values()
                if item.get("viewpoint_candidate_id")
                == decision.get("identity_candidate_id")
            ),
            None,
        )
        if not candidate and not atomic_coverage:
            findings.append(f"{decision_id}: missing identity candidate")
        elif (
            decision.get("review_status")
            in {"system_approved", "human_approved", "approved"}
            and candidate
            and decision.get("input_sha256") != candidate.get("generation_fingerprint")
        ):
            findings.append(f"{decision_id}: approved decision input SHA is stale")
        elif (
            decision.get("review_status") == "system_approved"
            and atomic_coverage
            and decision.get("input_sha256")
            != atomic_coverage.get("boundary_run_artifact_sha256")
        ):
            findings.append(f"{decision_id}: approved atomic decision input SHA is stale")
        resolved = decision.get("resolved_viewpoint_id")
        if resolved and str(resolved) not in viewpoints:
            findings.append(f"{decision_id}: missing resolved viewpoint {resolved}")

    for ledger_id, ledger in normalized.get("viewpoint_resolution_ledgers", {}).items():
        ledger_build_payload = {
            "builder_version": LEDGER_BUILDER_VERSION,
            "coverage_snapshot_id": ledger.get("coverage_snapshot_id"),
            "input_claim_manifest_sha256": ledger.get("input_claim_manifest_sha256"),
            "eligibility_policy_version": ledger.get("eligibility_policy_version"),
            "candidate_blocking_version": ledger.get("candidate_blocking_version"),
            "rows": ledger.get("rows") or [],
        }
        expected_fingerprint = sha256_json(ledger_build_payload)
        if ledger.get("build_fingerprint_sha256") != expected_fingerprint:
            findings.append(f"{ledger_id}: build fingerprint mismatch")
        if ledger_id != f"VRL-{expected_fingerprint[:20]}":
            findings.append(f"{ledger_id}: unstable resolution ledger id")
        ledger_artifact_payload = {
            key: ledger[key]
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
        if ledger.get("artifact_sha256") != sha256_json(ledger_artifact_payload):
            findings.append(f"{ledger_id}: artifact SHA mismatch")
        if str(ledger["coverage_snapshot_id"]) not in coverages:
            findings.append(f"{ledger_id}: missing coverage snapshot")
        for row in ledger.get("rows") or []:
            claim_id = str(row["claim_id"])
            claim = claims.get(claim_id)
            if not claim:
                findings.append(f"{ledger_id}: missing claim {claim_id}")
                continue
            if int(claim.get("revision", 1)) != int(row["pinned_claim_revision"]):
                findings.append(f"{ledger_id}: Claim revision mismatch for {claim_id}")
            if semantic_record_sha(claim) != row["claim_revision_sha256"]:
                findings.append(f"{ledger_id}: Claim SHA mismatch for {claim_id}")
            if (
                row.get("primary_viewpoint_id")
                and str(row["primary_viewpoint_id"]) not in viewpoints
            ):
                findings.append(f"{ledger_id}: missing viewpoint {row['primary_viewpoint_id']}")
            if (
                row.get("new_viewpoint_candidate_id")
                and str(row["new_viewpoint_candidate_id"]) not in candidates
            ):
                findings.append(f"{ledger_id}: missing candidate {row['new_viewpoint_candidate_id']}")
            resolution_link_ids = [row.get("viewpoint_claim_link_id")] + list(
                row.get("secondary_link_ids") or []
            )
            for link_id in resolution_link_ids:
                if link_id and str(link_id) not in links:
                    findings.append(f"{ledger_id}: missing claim link {link_id}")
                elif link_id:
                    resolved_link = links[str(link_id)]
                    if (
                        str(resolved_link.get("claim_id")) != claim_id
                        or int(resolved_link.get("pinned_claim_revision", 0))
                        != int(row["pinned_claim_revision"])
                    ):
                        findings.append(
                            f"{ledger_id}: claim link {link_id} belongs to another Claim revision"
                        )
            primary_link_id = row.get("viewpoint_claim_link_id")
            if primary_link_id and str(primary_link_id) in links:
                primary_link = links[str(primary_link_id)]
                if primary_link.get("viewpoint_id") != row.get("primary_viewpoint_id"):
                    findings.append(f"{ledger_id}: primary Claim link viewpoint mismatch")
                if primary_link.get("decision_id") != row.get("decision_id"):
                    findings.append(f"{ledger_id}: primary Claim link decision mismatch")
            if row.get("decision_id") and str(row["decision_id"]) not in decisions:
                findings.append(f"{ledger_id}: missing decision {row['decision_id']}")
            elif row.get("decision_id"):
                decision = decisions[str(row["decision_id"])]
                if (
                    row.get("primary_viewpoint_id")
                    and decision.get("resolved_viewpoint_id") != row.get("primary_viewpoint_id")
                ):
                    findings.append(f"{ledger_id}: resolution decision viewpoint mismatch")

    for report_id, report in normalized.get("viewpoint_quality_reports", {}).items():
        quality_build_payload = {
            "validator_version": report.get("validator_version"),
            "scope_kind": report.get("scope_kind"),
            "scope_ids": report.get("scope_ids") or [],
            "coverage_snapshot_id": report.get("coverage_snapshot_id"),
            "resolution_ledger_id": report.get("resolution_ledger_id"),
            "input_artifact_sha256s": report.get("input_artifact_sha256s") or [],
            "dimensions": report.get("dimensions") or [],
            "hard_failures": report.get("hard_failures") or [],
        }
        expected_fingerprint = sha256_json(quality_build_payload)
        if report.get("build_fingerprint_sha256") != expected_fingerprint:
            findings.append(f"{report_id}: build fingerprint mismatch")
        if report_id != f"VQR-{expected_fingerprint[:20]}":
            findings.append(f"{report_id}: unstable quality report id")
        quality_artifact_payload = {
            key: report[key]
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
        if report.get("artifact_sha256") != sha256_json(quality_artifact_payload):
            findings.append(f"{report_id}: artifact SHA mismatch")
        if str(report["coverage_snapshot_id"]) not in coverages:
            findings.append(f"{report_id}: missing coverage snapshot")
        ledger = ledgers.get(str(report["resolution_ledger_id"]))
        if not ledger:
            findings.append(f"{report_id}: missing resolution ledger")
        elif ledger.get("coverage_snapshot_id") != report.get("coverage_snapshot_id"):
            findings.append(f"{report_id}: ledger and report coverage mismatch")
        else:
            coverage = coverages.get(str(report["coverage_snapshot_id"]))
            required_input_shas = {
                ledger.get("artifact_sha256"),
                coverage.get("sources_sha256") if coverage else None,
            }
            supplied_input_shas = set(report.get("input_artifact_sha256s") or [])
            if required_input_shas - supplied_input_shas:
                findings.append(f"{report_id}: required input artifact SHA is missing")
        if report.get("scope_kind") == "identity_decision":
            for scope_id in report.get("scope_ids") or []:
                if str(scope_id) not in decisions and str(scope_id) not in candidates:
                    findings.append(f"{report_id}: missing identity scope {scope_id}")

    from .viewpoint_runtime_projection import validate_runtime_authoring_graph

    runtime_collections = {
        collection: payloads(collection)
        for collection in (
            "source_documents", "source_fragments", "claims", "evidence_steps",
            "claim_relations", "canonical_viewpoints", "viewpoint_revisions",
            "viewpoint_claim_links", "viewpoint_proposition_units",
            "viewpoint_proposition_unit_links", "argument_routes", "argument_route_revisions",
            "argument_route_attestations", "viewpoint_relations",
        )
    }
    findings.extend(validate_runtime_authoring_graph(runtime_collections))

    if findings:
        raise ViewpointFoundationValidationError(findings)
