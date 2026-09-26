"""Read-only all-corpus CVP partition planner and validator.

The preview path may use current Claim projections for sizing. Only a final
manifest built from complete frozen scope packets can authorize model work.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.knowledge_models import (
    ClaimRecord,
    EvidenceStepRecord,
    SourceFragmentRecord,
)
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import (
    CLAIM_MANIFEST_VERSION,
    evidence_fragment_ids,
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_production_safety import (
    CORPUS_COLLECTIONS,
    CvpProductionBlocked,
    _validate_scope_packet,
    collection_fingerprint,
    validate_cvp_freeze,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    IDENTITY_ELIGIBLE_CLAIM_REVIEW_STATUSES,
    IDENTITY_TERMINALLY_EXCLUDED_CLAIM_REVIEW_STATUSES,
)
from backend.pipeline.passage_scope_attestation import validate_passage_scope_attestation
from backend.pipeline.passage_knowledge_slice import Passage, reference_overlaps
from backend.pipeline.viewpoint_cvp_policy import (
    DEFAULT_CVP_POLICY_PATH,
    cvp_policy_fingerprint,
    cvp_policy_prompt_sha256s,
    load_cvp_policy,
)
from backend.pipeline.viewpoint_route_policy import (
    DEFAULT_ROUTE_POLICY_PATH,
    load_route_policy,
    route_policy_fingerprint,
    route_policy_prompt_sha256s,
)
from backend.pipeline.viewpoint_partition_manifest import (
    DEFAULT_PARTITION_POLICY,
    build_partition_manifest,
    load_partition_policy,
)
from backend.pipeline.viewpoint_partition_validation import validate_partition_manifest
from backend.pipeline.viewpoint_resolution_runtime import PROJECT_ROOT, PROMPT_DIR, write_immutable


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _preview_claim_manifest(store: PostgresKnowledgeStore) -> dict[str, Any]:
    """A sizing denominator from the current active store, never a final freeze."""

    evidence = {
        row.evidence_step_id: row
        for row in (
            EvidenceStepRecord.model_validate(raw)
            for raw in store.list_records("evidence_steps")
        )
    }
    fragments = {
        row.fragment_id: row
        for row in (
            SourceFragmentRecord.model_validate(raw)
            for raw in store.list_records("source_fragments")
        )
    }
    claims: list[dict[str, Any]] = []
    for raw in store.list_records("claims"):
        claim = ClaimRecord.model_validate(raw)
        source_ids = {
            fragments[fragment_id].source_id
            for evidence_id in claim.evidence_step_ids
            if (step := evidence.get(evidence_id)) is not None
            for fragment_id in evidence_fragment_ids(step)
            if fragment_id in fragments
        }
        claims.append({
            "claim_id": claim.claim_id,
            "pinned_claim_revision": claim.revision,
            "claim_revision_sha256": semantic_record_sha(claim),
            "source_id": next(iter(source_ids)) if len(source_ids) == 1 else "__unresolved_source__",
        })
    claims.sort(key=lambda row: row["claim_id"])
    body = {
        "schema_version": CLAIM_MANIFEST_VERSION,
        "coverage_snapshot_id": "preview-active-corpus-only",
        "claims": claims,
    }
    return body | {"manifest_sha256": sha256_json(body)}


def _current_projections(
    claim_manifest: dict[str, Any],
    store: PostgresKnowledgeStore,
    excluded_sources: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current = {
        item.claim_id: item
        for item in (
            ClaimRecord.model_validate(raw) for raw in store.list_records("claims")
        )
    }
    claims: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    for pin in claim_manifest.get("claims") or []:
        claim_id = str(pin["claim_id"])
        source_id = str(pin["source_id"])
        if source_id == "__unresolved_source__":
            dispositions.append({
                "claim_id": claim_id, "disposition": "blocked",
                "reason_code": "source_binding_not_unique",
            })
            continue
        if source_id in excluded_sources:
            dispositions.append({
                "claim_id": claim_id,
                "disposition": "excluded",
                "reason_code": "source_repair_deferred_by_owner",
            })
            continue
        item = current.get(claim_id)
        if item is None:
            dispositions.append({
                "claim_id": claim_id, "disposition": "residual",
                "reason_code": "missing_current_claim",
            })
            continue
        if (
            item.revision != pin["pinned_claim_revision"]
            or semantic_record_sha(item) != pin["claim_revision_sha256"]
        ):
            dispositions.append({
                "claim_id": claim_id, "disposition": "residual",
                "reason_code": "stale_claim_pin",
            })
            continue
        if item.review_status in IDENTITY_TERMINALLY_EXCLUDED_CLAIM_REVIEW_STATUSES:
            dispositions.append({
                "claim_id": claim_id, "disposition": "excluded",
                "reason_code": "superseded_claim",
            })
            continue
        if item.review_status not in IDENTITY_ELIGIBLE_CLAIM_REVIEW_STATUSES:
            dispositions.append({
                "claim_id": claim_id, "disposition": "blocked",
                "reason_code": f"review_status_{item.review_status}",
            })
            continue
        claims.append({
            "claim_id": claim_id,
            "pinned_claim_revision": item.revision,
            "claim_revision_sha256": semantic_record_sha(item),
            "source_id": source_id,
            "statement": item.statement,
            "scripture_refs": sorted(
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for value in item.scripture_refs
            ),
        })
    return claims, dispositions


def _packet_projections(
    paths: list[Path], excluded_sources: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    claims: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    for path in paths:
        packet = _read(path)
        body = {key: value for key, value in packet.items() if key != "packet_sha256"}
        if packet.get("packet_sha256") != sha256_json(body):
            raise ValueError(f"scope packet SHA mismatch: {path}")
        for row in packet.get("claims") or []:
            if row.get("source_id") in excluded_sources:
                raise ValueError(f"excluded source appears as eligible Claim: {row['claim_id']}")
            claims.append(row)
        dispositions.extend(
            {"claim_id": row["claim_id"], "disposition": "blocked", "reason_code": row["reason_code"]}
            for row in packet.get("blocked_claims") or []
        )
        dispositions.extend(
            {"claim_id": row["claim_id"], "disposition": "excluded", "reason_code": row["reason_code"]}
            for row in packet.get("excluded_claims") or []
        )
    return claims, dispositions


def _report(manifest: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    partitions = manifest["partitions"]
    all_claims = [claim for partition in partitions for claim in partition["claims"]]
    matthew_windows = [Passage("Matt", chapter, 1, 999) for chapter in range(1, 29)]
    body = {
        "schema_version": "wang_cvp_partition_preflight_report_v1",
        "manifest_sha256": manifest["artifact_sha256"],
        "global_freeze_sha256": manifest["global_freeze_sha256"],
        "cvp_policy_sha256": manifest["cvp_policy_sha256"],
        "partition_policy_sha256": manifest["partition_policy_sha256"],
        "runner_commit": manifest["runner_commit"],
        "mode": manifest["mode"],
        "validation": validation,
        "partitions": [
            {
                "partition_id": row["partition_id"],
                "claim_count": len(row["claims"]),
                "source_count": len({claim["source_id"] for claim in row["claims"]}),
                "grouping_request_bytes": row["grouping_request_bytes"],
                "routing_reasons": row["routing_reasons"],
                "split_parent_routes": row["split_parent_routes"],
                "context_count": len(row["context_refs"]),
            }
            for row in partitions
        ],
        "dispositions_by_reason": {
            reason: sum(row["reason_code"] == reason for row in manifest["dispositions"])
            for reason in sorted({row["reason_code"] for row in manifest["dispositions"]})
        },
        "source_exclusions": manifest["source_exclusions"],
        "passage_role_status": (
            "reviewed" if manifest["passage_role_attestation"] else "not_supplied"
        ),
        "matthew_reference_candidate_count": sum(
            any(
                reference_overlaps(reference, window)
                for reference in claim["scripture_refs"]
                for window in matthew_windows
            )
            for claim in all_claims
        ),
        "reviewed_passage_exegesis_claim_count": sum(
            any(route.startswith("passage:") for route in claim["route_candidates"])
            for claim in all_claims
        ) if manifest["passage_role_attestation"] else None,
        "would_call_models": False,
        "master_data_mutations": 0,
    }
    return body | {"artifact_sha256": sha256_json(body)}


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim-manifest", type=Path)
    parser.add_argument("--global-freeze", type=Path)
    parser.add_argument("--scope-packet", type=Path, action="append", default=[])
    parser.add_argument("--from-current-claims", action="store_true")
    parser.add_argument("--exclude-source", action="append", default=[])
    parser.add_argument("--passage-attestation", type=Path)
    parser.add_argument("--passage-units", type=Path)
    parser.add_argument("--partition-policy", type=Path, default=DEFAULT_PARTITION_POLICY)
    parser.add_argument("--cvp-policy", type=Path, default=DEFAULT_CVP_POLICY_PATH)
    parser.add_argument("--route-policy", type=Path, default=DEFAULT_ROUTE_POLICY_PATH)
    parser.add_argument("--database-url")
    parser.add_argument("--mode", choices=("preview", "final"), default="preview")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.passage_attestation) != bool(args.passage_units):
        parser.error("--passage-attestation and --passage-units must be supplied together")
    if args.from_current_claims and args.scope_packet:
        parser.error("choose current Claim projections or scope packets")
    if args.mode == "final" and (args.from_current_claims or not args.global_freeze):
        parser.error("final mode requires a global freeze and frozen scope packets")
    if args.mode == "final" and not args.scope_packet:
        parser.error("final mode requires scope packets")
    if args.mode == "final" and not args.passage_attestation:
        parser.error("final mode requires reviewed passage-role attestation")
    if not args.claim_manifest and not args.from_current_claims:
        parser.error("--claim-manifest is required unless using --from-current-claims")
    store = PostgresKnowledgeStore(args.database_url) if args.from_current_claims or args.mode == "final" else None
    claim_manifest = _read(args.claim_manifest) if args.claim_manifest else _preview_claim_manifest(store)
    policy = load_cvp_policy(args.cvp_policy)
    cvp_sha = cvp_policy_fingerprint(
        policy,
        prompt_sha256s=cvp_policy_prompt_sha256s(policy, prompt_dir=PROMPT_DIR),
    )
    partition_policy = load_partition_policy(args.partition_policy)
    if partition_policy["max_request_bytes"] != policy["max_request_bytes"]:
        raise ValueError("partition ceiling differs from the CVP production policy")
    if args.global_freeze:
        freeze = _read(args.global_freeze)
        if args.mode == "final":
            assert store is not None
            route_policy = load_route_policy(args.route_policy)
            route_sha = route_policy_fingerprint(
                route_policy,
                prompt_sha256s=route_policy_prompt_sha256s(route_policy, prompt_dir=PROMPT_DIR),
            )
            validate_cvp_freeze(
                freeze,
                store=store,
                cvp_policy_sha256=cvp_sha,
                route_policy_sha256=route_sha,
                runner_commit=str(freeze.get("runner_commit") or ""),
                validate_registry=False,
            )
    else:
        if store is None:
            raise ValueError("preview without a freeze requires --from-current-claims")
        body = {
            "schema_version": "wang_cvp_partition_preview_anchor_v1",
            "status": "preview_only",
            "claim_manifest_sha256": claim_manifest["manifest_sha256"],
            "cvp_policy_sha256": cvp_sha,
            "corpus_fingerprint_sha256": collection_fingerprint(store, CORPUS_COLLECTIONS),
            "prerequisites": {},
        }
        freeze = body | {"artifact_sha256": sha256_json(body)}
    excluded_sources = set(args.exclude_source)
    if args.from_current_claims:
        assert store is not None
        claims, dispositions = _current_projections(claim_manifest, store, excluded_sources)
    else:
        for path in args.scope_packet:
            packet = _read(path)
            if packet.get("claim_manifest_sha256") != claim_manifest["manifest_sha256"]:
                raise ValueError(f"scope packet belongs to another Claim manifest: {path}")
            if args.mode == "final":
                assert store is not None
                findings = _validate_scope_packet(packet, store)
                if findings:
                    raise CvpProductionBlocked([f"{path}: {finding}" for finding in findings])
        claims, dispositions = _packet_projections(args.scope_packet, excluded_sources)
        covered = {row["claim_id"] for row in claims} | {row["claim_id"] for row in dispositions}
        dispositions.extend(
            {
                "claim_id": row["claim_id"], "disposition": "excluded",
                "reason_code": "source_repair_deferred_by_owner",
            }
            for row in claim_manifest.get("claims") or []
            if row["source_id"] in excluded_sources and row["claim_id"] not in covered
        )
    exegesis_units: dict[str, list[str]] = {}
    role_artifact = None
    passage_units = None
    if args.passage_attestation:
        units_artifact = _read(args.passage_units)
        raw_units = units_artifact.get("passage_units", units_artifact)
        passage_units = {
            key: [Passage(**item) for item in values]
            for key, values in raw_units.items()
        }
        role_artifact = _read(args.passage_attestation)
        admissions = validate_passage_scope_attestation(
            role_artifact,
            claims=claims,
            claim_manifest_sha256=claim_manifest["manifest_sha256"],
            passage_units=passage_units,
        )
        exegesis_units = {
            claim_id: sorted({unit for row in admitted for unit in row["passage_unit_ids"]})
            for claim_id, admitted in admissions.items()
        }
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    if args.mode == "final" and subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip():
        raise ValueError("final partition plan requires a clean committed worktree")
    manifest = build_partition_manifest(
        claim_manifest=claim_manifest,
        claims=claims,
        dispositions=dispositions,
        freeze=freeze,
        cvp_policy_sha256=cvp_sha,
        partition_policy=partition_policy,
        runner_commit=commit,
        exegesis_units=exegesis_units,
        passage_role_attestation=role_artifact,
        passage_units=passage_units,
        source_exclusions=sorted(excluded_sources),
        mode=args.mode,
    )
    validation = validate_partition_manifest(
        manifest,
        claim_manifest=claim_manifest,
        freeze=freeze,
        cvp_policy_sha256=cvp_sha,
        store=store,
    )
    report = _report(manifest, validation)
    if args.output and not args.dry_run:
        write_immutable(args.output, manifest)
        write_immutable(args.output.with_suffix(".report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
