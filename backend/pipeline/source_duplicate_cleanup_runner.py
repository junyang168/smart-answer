"""Consolidate the three duplicate sermon identities recorded by #368."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backend.api.canonical_repository.postgres_store import (
    ChangeOperation,
    ChangeSetPlan,
    PostgresKnowledgeStore,
    build_retirement_plan,
    canonical_json,
    combined_plan,
    sha256_json,
    validate_change_set_plan_integrity,
)
from backend.pipeline.source_contract_cleanup import (
    BodyLocatorIndex,
    assert_claim_semantics_unchanged,
    build_source_contract_cleanup_plan,
    migrate_claim_occurrence_anchors,
    migrate_route_attestation,
    migrate_source_document,
    migrate_source_fragment,
    remap_claim_occurrence_source,
    unresolved_statuses,
)
from backend.pipeline.source_contract_cleanup_runner import (
    _active_state,
    _clean_git_commit,
    _human_settled_claim_ids,
    _plan_record_audit,
    _preflight,
    _readback,
    _seal,
    _source_material,
    _validate_seal,
    _verify_backup,
    _write_new,
    live_batch_ownership,
)
from backend.pipeline.source_projection import LOCATOR_SPACE, project_script


SCHEMA_VERSION = "wang_source_duplicate_cleanup_dry_run_v1"
PLAN_VERSION = "wang_source_duplicate_cleanup_plan_v1"
RECEIPT_VERSION = "wang_source_duplicate_cleanup_receipt_v1"
SOURCE_KIND = "wkp368_source_duplicate_cleanup"
DUPLICATE_GROUPS = (
    {
        "alias_source_id": "SRC-L3",
        "canonical_source_id": "SRC-2016_NYSC_3-f35be4755f9b",
    },
    {
        "alias_source_id": "SRC-L4",
        "canonical_source_id": "SRC-2016_NYSC_4-5317618c7962",
    },
    {
        "alias_source_id": "SRC-2016_NYSC_4-d05cd6cbe477",
        "canonical_source_id": "SRC-2016_NYSC_4-07c73c44dc11",
    },
)


def _claim_matches_group(
    claim: Mapping[str, Any], *, source_ids: set[str], transcript_id: str
) -> bool:
    return any(
        isinstance(occurrence, Mapping)
        and (
            str(occurrence.get("source_id") or "") in source_ids
            or str(occurrence.get("transcript_id") or "") == transcript_id
        )
        for occurrence in claim.get("occurrences") or []
    )


def _replace_attestation_source(
    attestation: Mapping[str, Any], *, old_source_id: str, canonical_source_id: str
) -> dict[str, Any] | None:
    if str(attestation.get("source_id") or "") != old_source_id:
        return None
    row = dict(attestation)
    row["source_id"] = canonical_source_id
    return row


def _contains_any(value: Any, targets: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_any(child, targets) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_any(child, targets) for child in value)
    return isinstance(value, str) and value in targets


def _plan_from_artifact(payload: Mapping[str, Any]) -> ChangeSetPlan:
    data = dict(payload["plan"])
    data.pop("summary", None)
    data["operations"] = tuple(
        ChangeOperation(**row) for row in data.get("operations") or []
    )
    data["ignored_keys"] = tuple(data.get("ignored_keys") or [])
    data["review_events"] = ()
    plan = ChangeSetPlan(**data)
    validate_change_set_plan_integrity(plan)
    return plan


def build_dry_run(
    *,
    store: PostgresKnowledgeStore,
    data_base_path: Path,
    live_batch_path: Path,
    output_root: Path,
    repo_root: Path,
) -> Path:
    runner_commit = _clean_git_commit(repo_root)
    current = _active_state(store)
    human_settled = _human_settled_claim_ids(store)
    ownership = live_batch_ownership(live_batch_path)
    live_owned = set(ownership["owned_source_ids"])

    sources = {
        object_id: row["payload"]
        for (collection, object_id), row in current.items()
        if collection == "source_documents"
    }
    fragments_by_source: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    claims: list[dict[str, Any]] = []
    attestations: list[dict[str, Any]] = []
    for (collection, _), row in current.items():
        payload = row["payload"]
        if collection == "source_fragments":
            fragments_by_source[str(payload.get("source_id") or "")].append(payload)
        elif collection == "claims":
            claims.append(payload)
        elif collection == "argument_route_attestations":
            attestations.append(payload)

    replacements: dict[tuple[str, str], dict[str, Any]] = {}
    retire_keys: list[tuple[str, str]] = []
    frozen_sources: list[dict[str, Any]] = []
    group_reports: list[dict[str, Any]] = []

    for specification in DUPLICATE_GROUPS:
        old_source_id = specification["alias_source_id"]
        canonical_source_id = specification["canonical_source_id"]
        old_source = sources.get(old_source_id)
        canonical_source = sources.get(canonical_source_id)
        if old_source is None or canonical_source is None:
            raise ValueError(
                f"duplicate group is not active: {old_source_id}, {canonical_source_id}"
            )
        transcript_id = str(canonical_source.get("transcript_id") or "")
        if (
            transcript_id != str(old_source.get("transcript_id") or "")
            or canonical_source.get("source_type") != old_source.get("source_type")
            or canonical_source.get("source_sha256") != old_source.get("source_sha256")
        ):
            raise ValueError(f"duplicate identity proof failed: {transcript_id}")
        if transcript_id in live_owned or {old_source_id, canonical_source_id} & live_owned:
            raise ValueError(f"duplicate group is owned by live #358: {transcript_id}")

        source_payload, raw_source, source_path = _source_material(
            canonical_source, data_base_path
        )
        script = source_payload.get("script")
        projection = project_script(script)
        index = BodyLocatorIndex(script)
        canonical_fragments = fragments_by_source[canonical_source_id]
        alias_fragments = fragments_by_source[old_source_id]

        migrated_fragments: dict[tuple[str, str], dict[str, Any]] = {}
        fragment_blockers: list[dict[str, Any]] = []
        for fragment in canonical_fragments:
            migrated, resolution = migrate_source_fragment(
                fragment, index, source_sha256=projection.body_sha256
            )
            if migrated is None:
                fragment_blockers.append(
                    {
                        "fragment_id": fragment.get("fragment_id"),
                        "status": resolution.status,
                    }
                )
            else:
                migrated_fragments[
                    ("source_fragments", str(fragment["fragment_id"]))
                ] = migrated

        coordinate_claims: dict[tuple[str, str], dict[str, Any]] = {}
        alias_claims: dict[tuple[str, str], dict[str, Any]] = {}
        claim_blockers: list[dict[str, Any]] = []
        protected_claims: list[str] = []
        claim_anchor_changes = 0
        related_claim_count = 0
        for claim in claims:
            if not _claim_matches_group(
                claim,
                source_ids={old_source_id, canonical_source_id},
                transcript_id=transcript_id,
            ):
                continue
            related_claim_count += 1
            claim_id = str(claim.get("claim_id") or "")
            remapped = remap_claim_occurrence_source(
                claim,
                old_source_id=old_source_id,
                canonical_source_id=canonical_source_id,
            )
            base = remapped or dict(claim)
            if remapped is not None:
                assert_claim_semantics_unchanged(claim, remapped)
                alias_claims[("claims", claim_id)] = remapped
            migrated, findings = migrate_claim_occurrence_anchors(
                base,
                index,
                source_id=canonical_source_id,
                transcript_id=transcript_id,
            )
            unresolved = unresolved_statuses(findings)
            if unresolved:
                claim_blockers.append(
                    {
                        "claim_id": claim_id,
                        "findings": [
                            item
                            for item in findings
                            if item.get("status") not in {"changed", "resolved", "unchanged"}
                        ],
                    }
                )
                continue
            final = migrated or (remapped if remapped is not None else None)
            if final is not None:
                assert_claim_semantics_unchanged(claim, final)
                coordinate_claims[("claims", claim_id)] = final
                claim_anchor_changes += sum(
                    item.get("status") == "changed" for item in findings
                )
                if claim_id in human_settled:
                    protected_claims.append(claim_id)

        alias_attestations: dict[tuple[str, str], dict[str, Any]] = {}
        coordinate_attestations: dict[tuple[str, str], dict[str, Any]] = {}
        for attestation in attestations:
            attestation_id = str(attestation.get("argument_route_attestation_id") or "")
            remapped = _replace_attestation_source(
                attestation,
                old_source_id=old_source_id,
                canonical_source_id=canonical_source_id,
            )
            base = remapped or attestation
            if remapped is not None:
                alias_attestations[
                    ("argument_route_attestations", attestation_id)
                ] = remapped
            if str(base.get("source_id") or "") == canonical_source_id:
                migrated = migrate_route_attestation(
                    base, source_sha256=projection.body_sha256
                )
                if migrated is not None:
                    coordinate_attestations[
                        ("argument_route_attestations", attestation_id)
                    ] = migrated

        alias_protected = sorted(
            object_id
            for collection, object_id in alias_claims
            if collection == "claims" and object_id in human_settled
        )
        if alias_protected:
            raise ValueError(
                f"cannot remap human-settled duplicate source aliases: {alias_protected}"
            )
        coordinate_ready = not (
            fragment_blockers or claim_blockers or protected_claims
        )
        if coordinate_ready:
            replacements[("source_documents", canonical_source_id)] = (
                migrate_source_document(
                    canonical_source, raw_source=raw_source, projection=projection
                )
            )
            replacements.update(migrated_fragments)
            replacements.update(coordinate_claims)
            replacements.update(coordinate_attestations)
        else:
            replacements.update(alias_claims)
            replacements.update(alias_attestations)

        retire_keys.append(("source_documents", old_source_id))
        retire_keys.extend(
            ("source_fragments", str(fragment["fragment_id"]))
            for fragment in alias_fragments
        )
        frozen_sources.append(
            {
                "transcript_id": transcript_id,
                "source_path": str(source_path),
                "source_file_sha256": hashlib.sha256(raw_source).hexdigest(),
                "source_body_sha256": projection.body_sha256,
            }
        )
        group_reports.append(
            {
                **specification,
                "transcript_id": transcript_id,
                "canonical_fragment_count": len(canonical_fragments),
                "retired_alias_fragment_count": len(alias_fragments),
                "related_claim_count": related_claim_count,
                "claim_anchor_changes_if_ready": claim_anchor_changes,
                "coordinate_ready": coordinate_ready,
                "coordinate_blockers": {
                    "fragments": fragment_blockers,
                    "claims": claim_blockers,
                    "human_settled_claim_ids": sorted(protected_claims),
                },
            }
        )

    arrival = build_source_contract_cleanup_plan(
        package_id="WKP368-DUPLICATE-CONSOLIDATION",
        current=current,
        replacements=replacements,
        human_settled_claim_ids=human_settled,
        source_kind=SOURCE_KIND,
    )
    withdrawal = build_retirement_plan(
        retire_keys,
        current,
        reason="Retire three duplicate SourceDocument aliases and their unreferenced fragments",
        package_id="WKP368-DUPLICATE-CONSOLIDATION",
        source_kind=SOURCE_KIND,
    )
    plan = combined_plan(arrival, withdrawal)
    validate_change_set_plan_integrity(plan)
    checks = _preflight(store, plan)

    run_dir = output_root / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    artifact = _seal(
        {
            "schema_version": PLAN_VERSION,
            "runner_git_commit": runner_commit,
            "frozen_sources": frozen_sources,
            "groups": group_reports,
            "retired_object_ids": [object_id for _, object_id in retire_keys],
            "plan": plan.as_dict(),
            "changed_records": _plan_record_audit(plan, current),
        }
    )
    plan_path = run_dir / f"plan.{artifact['artifact_sha256'][:20]}.json"
    plan_file_sha = _write_new(plan_path, artifact)
    report = _seal(
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runner_git_commit": runner_commit,
            "database_state_sha256": sha256_json(
                [
                    {
                        "collection": key[0],
                        "object_id": key[1],
                        "revision": row["revision"],
                        "content_sha256": row["content_sha256"],
                    }
                    for key, row in sorted(current.items())
                ]
            ),
            "live_batch_ownership": ownership,
            "groups": group_reports,
            "preflight_checks": checks,
            "operation_counts": dict(
                sorted(
                    collections.Counter(
                        f"{operation.operation}:{operation.collection}"
                        for operation in plan.operations
                    ).items()
                )
            ),
            "change_set_id": plan.change_set_id,
            "plan_path": str(plan_path),
            "plan_file_sha256": plan_file_sha,
            "plan_content_sha256": artifact["artifact_sha256"],
        }
    )
    report_path = run_dir / f"dry-run.{report['artifact_sha256'][:20]}.json"
    _write_new(report_path, report)
    print(report_path)
    print(
        canonical_json(
            {
                "groups": [
                    {
                        "transcript_id": group["transcript_id"],
                        "coordinate_ready": group["coordinate_ready"],
                    }
                    for group in group_reports
                ],
                "operation_counts": report["operation_counts"],
                "change_set_id": plan.change_set_id,
            }
        )
    )
    return report_path


def apply_dry_run(
    *,
    store: PostgresKnowledgeStore,
    report_path: Path,
    live_batch_path: Path,
    repo_root: Path,
    backup_path: Path,
) -> Path:
    runner_commit = _clean_git_commit(repo_root)
    backup = _verify_backup(backup_path)
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    _validate_seal(report)
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported duplicate cleanup report")
    if report.get("runner_git_commit") != runner_commit:
        raise ValueError("runner commit differs from frozen duplicate cleanup")
    ownership = live_batch_ownership(live_batch_path)
    live_owned = set(ownership["owned_source_ids"])
    for group in report.get("groups") or []:
        if (
            group["transcript_id"] in live_owned
            or group["alias_source_id"] in live_owned
            or group["canonical_source_id"] in live_owned
        ):
            raise ValueError(f"duplicate group became owned by #358: {group['transcript_id']}")

    plan_path = Path(str(report["plan_path"]))
    plan_bytes = plan_path.read_bytes()
    if hashlib.sha256(plan_bytes).hexdigest() != report["plan_file_sha256"]:
        raise ValueError("duplicate cleanup plan file changed")
    artifact = json.loads(plan_bytes)
    _validate_seal(artifact)
    if (
        artifact.get("artifact_sha256") != report.get("plan_content_sha256")
        or artifact.get("runner_git_commit") != runner_commit
    ):
        raise ValueError("duplicate cleanup plan seal changed")
    for frozen in artifact.get("frozen_sources") or []:
        path = Path(str(frozen["source_path"]))
        if hashlib.sha256(path.read_bytes()).hexdigest() != frozen["source_file_sha256"]:
            raise ValueError(f"source file changed after dry-run: {frozen['transcript_id']}")

    plan = _plan_from_artifact(artifact)
    if plan.change_set_id != report["change_set_id"]:
        raise ValueError("duplicate cleanup ChangeSet identity changed")
    report_file_sha = hashlib.sha256(report_bytes).hexdigest()
    result = store.apply_plan(
        plan,
        metadata={
            "dry_run_report_path": str(report_path),
            "dry_run_report_file_sha256": report_file_sha,
            "dry_run_report_content_sha256": report["artifact_sha256"],
            "source_plan_path": str(plan_path),
            "source_plan_file_sha256": report["plan_file_sha256"],
            "runner_git_commit": runner_commit,
            "registry_backup": backup,
        },
    )
    _readback(store, plan)

    active = _active_state(store)
    retired_ids = set(artifact.get("retired_object_ids") or [])
    active_payloads = [row["payload"] for row in active.values()]
    if any(object_id in {key[1] for key in active} for object_id in retired_ids):
        raise ValueError("duplicate cleanup left a retired object active")
    if any(_contains_any(payload, retired_ids) for payload in active_payloads):
        raise ValueError("duplicate cleanup left an active alias reference")
    post_groups: list[dict[str, Any]] = []
    for group in report.get("groups") or []:
        canonical_id = str(group["canonical_source_id"])
        source = active.get(("source_documents", canonical_id), {}).get("payload")
        if source is None:
            raise ValueError(f"canonical source is missing after apply: {canonical_id}")
        identity_count = sum(
            key[0] == "source_documents"
            and row["payload"].get("source_type") == source.get("source_type")
            and row["payload"].get("transcript_id") == source.get("transcript_id")
            for key, row in active.items()
        )
        if identity_count != 1:
            raise ValueError(f"duplicate identity remains after apply: {canonical_id}")
        expected_locator = LOCATOR_SPACE if group["coordinate_ready"] else None
        if source.get("locator_space") != expected_locator:
            raise ValueError(f"unexpected locator state after apply: {canonical_id}")
        post_groups.append(
            {
                "transcript_id": group["transcript_id"],
                "canonical_source_id": canonical_id,
                "active_identity_count": identity_count,
                "locator_space": source.get("locator_space"),
            }
        )

    receipt = _seal(
        {
            "schema_version": RECEIPT_VERSION,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "runner_git_commit": runner_commit,
            "change_set_id": plan.change_set_id,
            "result": result,
            "dry_run_report_file_sha256": report_file_sha,
            "source_plan_file_sha256": report["plan_file_sha256"],
            "registry_backup": backup,
            "readback": "verified",
            "post_groups": post_groups,
        }
    )
    receipt_path = report_path.parent / (
        f"apply-receipt.{plan.change_set_id}.{receipt['artifact_sha256'][:16]}.json"
    )
    _write_new(receipt_path, receipt)
    print(receipt_path)
    print(canonical_json({"result": result, "post_groups": post_groups}))
    return receipt_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-base-path",
        type=Path,
        default=Path("/opt/homebrew/var/www/church/web/data"),
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--live-batch", type=Path, default=None)
    parser.add_argument("--apply-report", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    output_root = args.output_root or (
        args.data_base_path
        / "wang-knowledge-platform/staging/source-contract-cleanup/WKP368/duplicate-consolidation"
    )
    live_batch = args.live_batch or (
        args.data_base_path
        / "wang-knowledge-platform/staging/claim-layer/research-batches/"
        "RB-REMAINING-SERMON-CORPUS-2026-09-05/runner-358-transport80-batch.json"
    )
    repo_root = Path(__file__).resolve().parents[2]
    store = PostgresKnowledgeStore(args.database_url)
    if args.apply_report:
        if args.backup is None:
            parser.error("--backup is required with --apply-report")
        apply_dry_run(
            store=store,
            report_path=args.apply_report,
            live_batch_path=live_batch,
            repo_root=repo_root,
            backup_path=args.backup,
        )
    else:
        build_dry_run(
            store=store,
            data_base_path=args.data_base_path,
            live_batch_path=live_batch,
            output_root=output_root,
            repo_root=repo_root,
        )


if __name__ == "__main__":
    main()
