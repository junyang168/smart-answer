"""Compile the deterministic Claim packet one viewpoint scope is resolved from.

A scope is a passage range, not a target proposition.  The old targeted packet
asked "which Claims bear on this viewpoint?", which only works when a person
already named the viewpoint.  Batch resolution asks the opposite question — how
many viewpoints are in this passage — so the packet carries the passage's whole
Claim set and lets the proposer find the boundaries.

No semantic prefilter runs here.  A Claim leaves the denominator only for a
mechanical reason (stale revision, unusable evidence, no source attestation),
and it leaves with a reason code rather than silently.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from backend.api.canonical_repository.knowledge_models import (
    ArgumentRouteRecord,
    ArgumentRouteRevisionRecord,
    CanonicalViewpointRecord,
    ClaimRecord,
    EvidenceStepRecord,
    SourceFragmentRecord,
    ViewpointRevisionRecord,
)
from backend.api.canonical_repository.matthew16_viewpoint_pilot import (
    validate_pilot_scope_artifact,
)
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.store import RepositoryStore
from backend.api.canonical_repository.viewpoint_foundation import (
    CLAIM_MANIFEST_VERSION,
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_resolution import compile_review_claim
from backend.api.canonical_repository.viewpoint_claim_repin import (
    substantive_difference,
)
from backend.api.canonical_repository.viewpoint_source_attestation import (
    IdentitySourceEligibilityArtifact,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PACKET_VERSION = "wang_canonical_viewpoint_scope_packet_v3"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_sha(payload: dict[str, Any], field: str = "artifact_sha256") -> str:
    stated = str(payload.get(field) or "")
    body = {key: value for key, value in payload.items() if key != field}
    if not stated or stated != sha256_json(body):
        raise ValueError(f"invalid {field}")
    return stated


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def scope_claim_ids(scope: dict[str, Any], passage_unit_ids: list[str]) -> list[str]:
    """Every core Claim assigned to the requested units. No semantic prefilter."""

    wanted = set(passage_unit_ids)
    return sorted(
        {
            str(row["claim_id"])
            for row in scope.get("claims") or []
            if row.get("lane") == "core"
            and (not wanted or wanted & set(row.get("passage_unit_ids") or []))
        }
    )


def registry_context(
    viewpoints: list[dict[str, Any]], revisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Synopses of the active viewpoints the proposer may match against.

    Deliberately small: identity, wording, truth-condition signature and scope.
    The proposer compares propositions, so handing it whole member sets would
    spend context without changing a judgment.
    """

    revision_index = {
        item.viewpoint_revision_id: item
        for item in (ViewpointRevisionRecord.model_validate(row) for row in revisions)
    }
    context: list[dict[str, Any]] = []
    for row in viewpoints:
        viewpoint = CanonicalViewpointRecord.model_validate(row)
        if viewpoint.identity_status != "active":
            continue
        revision = revision_index.get(viewpoint.current_revision_id)
        if revision is None:
            continue
        context.append(
            {
                "viewpoint_id": viewpoint.viewpoint_id,
                "viewpoint_revision_id": revision.viewpoint_revision_id,
                # Evidence is deliberately absent: a 2026-08-25 experiment gave
                # both models the attested verbatim and it changed no judgment
                # for Opus and reversed Sol's, so the context cost buys nothing.
                "core_proposition": revision.core_proposition,
                "proposition_signature": revision.proposition_signature.model_dump(mode="json"),
                "scope": revision.scope.model_dump(mode="json"),
            }
        )
    context.sort(key=lambda item: item["viewpoint_id"])
    return context


def route_registry_context(
    routes: list[dict[str, Any]], revisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Active route synopses, kept separate from CVP identity retrieval."""

    revision_index = {
        item.argument_route_revision_id: item
        for item in (
            ArgumentRouteRevisionRecord.model_validate(row) for row in revisions
        )
    }
    context: list[dict[str, Any]] = []
    for row in routes:
        route = ArgumentRouteRecord.model_validate(row)
        if route.route_status != "active":
            continue
        revision = revision_index.get(route.current_revision_id)
        if revision is None:
            continue
        context.append(
            {
                "route_id": route.argument_route_id,
                "route_revision_id": revision.argument_route_revision_id,
                "conclusion_viewpoint_revision_id": (
                    revision.validated_against_conclusion_viewpoint_revision_id
                ),
                "route_label": revision.route_label,
                "route_signature": revision.route_signature.model_dump(mode="json"),
            }
        )
    return sorted(context, key=lambda item: item["route_revision_id"])


def _pinned_claim_payloads(
    store: Any, wanted: Sequence[tuple[str, int]]
) -> dict[tuple[str, int], dict[str, Any]]:
    """Each Claim payload as it stood at the revision the manifest pinned."""

    found: dict[tuple[str, int], dict[str, Any]] = {}
    if not wanted:
        return found
    with store.connect() as conn, conn.cursor() as cursor:
        for claim_id, revision in sorted(set(wanted)):
            cursor.execute(
                """SELECT payload FROM wang_knowledge.object_versions
                   WHERE collection='claims' AND object_id=%s AND revision=%s""",
                (claim_id, revision),
            )
            row = cursor.fetchone()
            if row:
                found[(claim_id, revision)] = row[0]
    return found


def build_scope_packet(
    *,
    scope: dict[str, Any],
    scope_label: str,
    passage_unit_ids: list[str],
    claim_manifest: dict[str, Any],
    source_attestation: IdentitySourceEligibilityArtifact,
    repository_root: Path,
    database_url: str | None,
) -> dict[str, Any]:
    scope_sha = _validate_sha(scope)
    # The SHA proves the artifact was not altered; conformance is proved by
    # the model. A non-conforming hand-derived scope once rode a valid SHA
    # straight into resolution (#327).
    validate_pilot_scope_artifact(scope)
    manifest_body = {
        key: value for key, value in claim_manifest.items() if key != "manifest_sha256"
    }
    manifest_sha = str(claim_manifest.get("manifest_sha256") or "")
    if (
        claim_manifest.get("schema_version") != CLAIM_MANIFEST_VERSION
        or manifest_sha != sha256_json(manifest_body)
    ):
        raise ValueError("scope packet requires a valid Claim manifest")
    if source_attestation.claim_manifest_sha256 != manifest_sha:
        raise ValueError("source attestation belongs to another Claim manifest")
    coverage_snapshot_id = str(claim_manifest.get("coverage_snapshot_id") or "")
    if not coverage_snapshot_id:
        raise ValueError("Claim manifest is not bound to a coverage snapshot")

    in_scope = scope_claim_ids(scope, passage_unit_ids)
    if not in_scope:
        raise ValueError("scope selects no core Claims")

    manifest_rows = {
        str(row["claim_id"]): dict(row) for row in claim_manifest.get("claims") or []
    }
    attestations = {
        row.claim_id: row.attestation_sha256 for row in source_attestation.attestations
    }
    exceptions = {row.claim_id: row for row in source_attestation.exceptions}

    store = PostgresKnowledgeStore(database_url)
    claims = {
        row.claim_id: row
        for row in (
            ClaimRecord.model_validate(raw) for raw in store.list_records("claims")
        )
    }
    evidence_index = {
        row.evidence_step_id: row
        for row in (
            EvidenceStepRecord.model_validate(raw)
            for raw in store.list_records("evidence_steps")
        )
    }
    fragment_index = {
        row.fragment_id: row
        for row in (
            SourceFragmentRecord.model_validate(raw)
            for raw in store.list_records("source_fragments")
        )
    }
    citation_index = {
        item.citation_id: item for item in RepositoryStore(repository_root).list_citations()
    }
    source_sha256 = {
        fragment.source_id: str(fragment.source_sha256 or "")
        for fragment in fragment_index.values()
    }

    # A Claim review moves every reviewed Claim's revision and fingerprint
    # without altering a word it says, and the manifest pins both. On
    # 2026-08-26 that took a 214-Claim scope from 190 resolvable to 2: the
    # pipeline's own review stage had made the corpus unusable to the stage
    # that consumes it. The pinned revision's payload is still in the version
    # history, so a pin that moved for review metadata alone is a pin that
    # still holds -- and anything else is still stale.
    pinned_claim_payloads = _pinned_claim_payloads(
        store,
        [
            (claim_id, int(manifest_rows[claim_id]["pinned_claim_revision"]))
            for claim_id in in_scope
            if claim_id in manifest_rows
        ],
    )
    review_claims = []
    blocked: list[dict[str, Any]] = []
    advanced_review_pins: list[dict[str, Any]] = []
    for claim_id in in_scope:
        manifest_row = manifest_rows.get(claim_id)
        claim = claims.get(claim_id)
        if manifest_row is None:
            blocked.append({"claim_id": claim_id, "reason_code": "missing_from_manifest",
                            "detail": "Claim is not in the pinned Claim manifest."})
            continue
        if claim is None:
            blocked.append({"claim_id": claim_id, "reason_code": "missing_claim",
                            "detail": "Claim is not in the authoring store."})
            continue
        pinned_revision = int(manifest_row["pinned_claim_revision"])
        pin_moved = (
            claim.revision != pinned_revision
            or semantic_record_sha(claim) != manifest_row["claim_revision_sha256"]
        )
        if pin_moved:
            pinned_payload = pinned_claim_payloads.get((claim_id, pinned_revision))
            differences = (
                substantive_difference(pinned_payload, claim.model_dump(mode="json"))
                if pinned_payload is not None
                else ["<pinned revision is not in the version history>"]
            )
            if not differences:
                pin_moved = False
                advanced_review_pins.append(
                    {
                        "claim_id": claim_id,
                        "manifest_pinned_claim_revision": pinned_revision,
                        "store_claim_revision": claim.revision,
                    }
                )
        if pin_moved:
            blocked.append({"claim_id": claim_id, "reason_code": "stale_claim_revision",
                            "detail": "Claim revision or SHA no longer matches the manifest."})
            continue
        attestation_sha = attestations.get(claim_id)
        if not attestation_sha:
            exception = exceptions.get(claim_id)
            blocked.append({
                "claim_id": claim_id,
                "reason_code": exception.code if exception else "missing_attestation",
                "detail": exception.detail if exception
                else "No current source-eligibility attestation exists.",
            })
            continue
        review_claim, findings = compile_review_claim(
            claim=claim,
            evidence_index=evidence_index,
            fragment_index=fragment_index,
            citation_index=citation_index,
            coverage_source_sha256=source_sha256,
            attestation_sha=attestation_sha,
        )
        if review_claim is None:
            blocked.append({"claim_id": claim_id, "reason_code": "invalid_source_evidence",
                            "detail": "; ".join(findings) or "Claim evidence is unusable."})
            continue
        if not any(item.valid_for_identity_review for item in review_claim.evidence):
            blocked.append({"claim_id": claim_id, "reason_code": "no_identity_eligible_evidence",
                            "detail": "No evidence pair is citation- or attestation-bound."})
            continue
        review_claims.append(review_claim)

    if len(review_claims) < 2:
        raise ValueError("scope packet needs at least two resolvable Claims")

    blocked.sort(key=lambda item: item["claim_id"])
    packet = {
        "schema_version": SCOPE_PACKET_VERSION,
        "scope_label": scope_label,
        "passage_unit_ids": sorted(set(passage_unit_ids)),
        "scope_artifact_sha256": scope_sha,
        "claim_manifest_sha256": manifest_sha,
        "coverage_snapshot_id": coverage_snapshot_id,
        "source_attestation_artifact_sha256": source_attestation.artifact_sha256,
        "claims": [item.model_dump(mode="json") for item in review_claims],
        "registry_context": registry_context(
            store.list_records("canonical_viewpoints"),
            store.list_records("viewpoint_revisions"),
        ),
        "route_registry_context": route_registry_context(
            store.list_records("argument_routes"),
            store.list_records("argument_route_revisions"),
        ),
        "blocked_claims": blocked,
        # Named, not silent: the manifest and the store disagree on these
        # Claims' revisions, and the packet says so even though the difference
        # is review metadata and the pin still holds.
        "advanced_review_pins": advanced_review_pins,
        "statistics": {
            "scope_claim_count": len(in_scope),
            "resolvable_claim_count": len(review_claims),
            "blocked_claim_count": len(blocked),
            "advanced_review_pin_count": len(advanced_review_pins),
        },
        "semantic_prefilter_applied": False,
        "model_calls_executed": 0,
        "master_data_mutations": 0,
    }
    packet["packet_sha256"] = sha256_json(packet)
    return packet


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--scope-label", required=True)
    parser.add_argument(
        "--passage-unit-id",
        action="append",
        help="passage unit to include; omit for every core Claim in the scope",
    )
    parser.add_argument("--claim-manifest", type=Path, required=True)
    parser.add_argument("--source-attestation", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    packet = build_scope_packet(
        scope=_read(args.scope),
        scope_label=args.scope_label,
        passage_unit_ids=args.passage_unit_id or [],
        claim_manifest=_read(args.claim_manifest),
        source_attestation=IdentitySourceEligibilityArtifact.model_validate(
            _read(args.source_attestation)
        ),
        repository_root=args.repository_root,
        database_url=args.database_url,
    )
    _write_immutable(args.output, packet)
    print(
        json.dumps(
            {
                **packet["statistics"],
                "registry_context_count": len(packet["registry_context"]),
                "route_registry_context_count": len(packet["route_registry_context"]),
                "packet_sha256": packet["packet_sha256"],
                "blocked": packet["blocked_claims"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
