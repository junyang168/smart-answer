"""Freeze and apply the deterministic #368 legacy source-coordinate cohort."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
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
from backend.pipeline.knowledge_source import markdown_blocks, markdown_source_document
from backend.pipeline.source_contract_cleanup import (
    BodyLocatorIndex,
    assert_claim_semantics_unchanged,
    build_source_contract_cleanup_plan,
    changed_paths,
    claim_without_coordinate_provenance,
    migrate_claim_occurrence_anchors,
    migrate_route_attestation,
    migrate_source_document,
    migrate_source_fragment,
    remove_source_fragment_reference,
    unresolved_statuses,
)
from backend.pipeline.source_projection import (
    LOCATOR_SPACE,
    project_script,
    script_from_markdown_blocks,
)
from backend.pipeline.transcript_source import resolve_transcript_path


SCHEMA_VERSION = "wang_source_contract_cleanup_dry_run_v1"
PLAN_ARTIFACT_VERSION = "wang_source_contract_cleanup_source_plan_v1"
APPLY_RECEIPT_VERSION = "wang_source_contract_cleanup_apply_receipt_v1"
APPLY_SUMMARY_VERSION = "wang_source_contract_cleanup_apply_summary_v1"
TARGETED_TRANSCRIPTS = frozenset(
    {"S 210711"}
)
ATTESTED_FRAGMENT_LOCATORS = {
    "FR-2017_NYSC_1-1004660290a2-DK-1004660290a2-P02-E014-01": "S0006",
}
COLLECTIONS = (
    "source_documents",
    "source_fragments",
    "evidence_steps",
    "observations",
    "questions",
    "claims",
    "argument_route_attestations",
)
TRANSCRIPT_DIR_NAMES = ("script_published", "script_review", "script_patched")


def _utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def _seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    row = json.loads(canonical_json(payload))
    row.pop("artifact_sha256", None)
    row["artifact_sha256"] = sha256_json(row)
    return row


def _validate_seal(payload: Mapping[str, Any]) -> None:
    row = dict(payload)
    observed = str(row.pop("artifact_sha256", ""))
    if observed != sha256_json(row):
        raise ValueError("artifact SHA seal is invalid")


def _write_new(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (canonical_json(payload) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(encoded).hexdigest()


def _git_commit(repo_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()


def _clean_git_commit(repo_root: Path) -> str:
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repo_root, text=True
    ).strip()
    if dirty:
        raise ValueError("cleanup runner requires a clean git worktree")
    return _git_commit(repo_root)


def _verify_backup(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"registry backup is missing or empty: {path}")
    listing = subprocess.check_output(
        ["pg_restore", "--list", str(path)], stderr=subprocess.STDOUT
    )
    if not listing.strip():
        raise ValueError(f"registry backup has an empty archive listing: {path}")
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "pg_restore_list_sha256": hashlib.sha256(listing).hexdigest(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def _active_state(store: PostgresKnowledgeStore) -> dict[tuple[str, str], dict[str, Any]]:
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT collection, object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects
               WHERE collection = ANY(%s) AND retired_at IS NULL""",
            (list(COLLECTIONS),),
        )
        return {
            (str(collection), str(object_id)): {
                "revision": int(revision),
                "content_sha256": str(content_sha256),
                "payload": dict(payload),
            }
            for collection, object_id, revision, content_sha256, payload
            in cursor.fetchall()
        }


def _human_settled_claim_ids(store: PostgresKnowledgeStore) -> set[str]:
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT DISTINCT ON (re.object_id)
                      re.object_id, re.reviewer_kind, re.decision,
                      re.object_revision, o.revision, o.payload->>'review_status'
               FROM wang_knowledge.review_events re
               JOIN wang_knowledge.objects o
                 ON o.collection=re.collection AND o.object_id=re.object_id
                AND o.retired_at IS NULL
               WHERE re.collection='claims'
               ORDER BY re.object_id, re.object_revision DESC,
                        re.created_at DESC, re.review_event_id DESC"""
        )
        return {
            str(object_id)
            for object_id, reviewer_kind, decision, event_revision,
                current_revision, current_status in cursor.fetchall()
            if str(reviewer_kind) == "human"
            and int(event_revision) == int(current_revision)
            and str(decision) == str(current_status)
            and str(current_status) in {"approved", "human_approved", "superseded"}
        }


def parse_only_sources(command: str, known_sources: list[str]) -> set[str]:
    """Recover exact argv members from macOS ps output using the batch manifest."""

    marker = " --only "
    if marker not in command:
        return set(known_sources)
    remaining = command.split(marker, 1)[1].strip()
    selected: set[str] = set()
    while remaining:
        matches = [
            source
            for source in known_sources
            if remaining == source or remaining.startswith(source + " ")
        ]
        if not matches:
            raise ValueError(f"cannot parse live --only list near {remaining[:80]!r}")
        source = max(matches, key=len)
        selected.add(source)
        remaining = remaining[len(source) :].lstrip(" ")
    return selected


def live_batch_ownership(batch_path: Path) -> dict[str, Any]:
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    known = [str(value) for value in batch.get("transcript_ids") or []]
    if not known:
        raise ValueError(f"batch has no transcript_ids: {batch_path}")
    output = subprocess.check_output(["ps", "-Aww", "-o", "pid=,command="], text=True)
    owners: list[dict[str, Any]] = []
    owned: set[str] = set()
    batch_token = str(batch_path)
    for line in output.splitlines():
        if "backend.pipeline.research_batch_runner" not in line or batch_token not in line:
            continue
        match = re.match(r"\s*(\d+)\s+(.*)$", line)
        if not match:
            continue
        pid, command = int(match.group(1)), match.group(2)
        selected = parse_only_sources(command, known)
        owners.append({"pid": pid, "source_ids": sorted(selected)})
        owned.update(selected)
    return {
        "batch_path": str(batch_path),
        "batch_file_sha256": hashlib.sha256(batch_path.read_bytes()).hexdigest(),
        "owners": owners,
        "owned_source_ids": sorted(owned),
    }


def _source_material(
    source: Mapping[str, Any], data_base_path: Path
) -> tuple[dict[str, Any], bytes, Path]:
    if source.get("source_type") == "notes_manuscript":
        return markdown_source_document(dict(source))
    transcript_id = str(source.get("transcript_id") or "")
    path = resolve_transcript_path(
        transcript_id,
        [data_base_path / name for name in TRANSCRIPT_DIR_NAMES],
    )
    if path is None:
        raise FileNotFoundError(f"current transcript not found: {transcript_id}")
    raw = path.read_bytes()
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return {"metadata": {"title": transcript_id}, "script": parsed}, raw, path
    if isinstance(parsed, dict):
        return parsed, raw, path
    raise ValueError(f"invalid transcript shape: {path}")


def _claim_matches_source(
    claim: Mapping[str, Any], *, source_id: str, transcript_id: str
) -> bool:
    return any(
        isinstance(occurrence, Mapping)
        and (
            str(occurrence.get("source_id") or "") == source_id
            or str(occurrence.get("transcript_id") or "") == transcript_id
        )
        for occurrence in claim.get("occurrences") or []
    )


def _plan_record_audit(
    plan: ChangeSetPlan, current: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for operation in plan.operations:
        before = dict(current[(operation.collection, operation.object_id)]["payload"])
        row = {
            "operation": operation.operation,
            "collection": operation.collection,
            "object_id": operation.object_id,
            "before_revision": operation.before_revision,
            "after_revision": operation.after_revision,
            "before_sha256": operation.before_sha256,
            "after_sha256": operation.after_sha256,
            "changed_paths": changed_paths(before, operation.payload),
        }
        if operation.collection == "claims":
            before_semantic = sha256_json(claim_without_coordinate_provenance(before))
            after_semantic = sha256_json(
                claim_without_coordinate_provenance(operation.payload)
            )
            if before_semantic != after_semantic:
                raise ValueError(f"Claim semantic proof failed: {operation.object_id}")
            row["claim_semantic_sha256_before"] = before_semantic
            row["claim_semantic_sha256_after"] = after_semantic
        rows.append(row)
    return rows


def _preflight(store: PostgresKnowledgeStore, plan: ChangeSetPlan) -> list[str]:
    checks = (
        "_assert_global_id_uniqueness",
        "_assert_source_identity_uniqueness",
        "_assert_edge_integrity",
        "_assert_no_dangling_package_references",
        "_assert_no_uncoordinated_semantic_references",
    )
    with store.connect() as conn, conn.cursor() as cursor:
        for name in checks:
            getattr(store, name)(cursor, plan)
    return list(checks)


def _evidence_fragment_locator_proofs(
    owners: list[dict[str, Any]], fragment_locators: Mapping[str, str]
) -> dict[str, str]:
    """Return EvidenceStep locators proved by all of their SourceFragments."""

    result: dict[str, str] = {}
    for owner in owners:
        evidence_id = str(owner.get("evidence_step_id") or "")
        if not evidence_id:
            continue
        fragment_ids = [
            str(value)
            for value in owner.get("source_fragment_ids") or []
            if str(value)
        ]
        singular = str(owner.get("source_fragment_id") or "")
        if singular:
            fragment_ids.append(singular)
        if not fragment_ids or any(
            fragment_id not in fragment_locators for fragment_id in fragment_ids
        ):
            continue
        locators = {fragment_locators[fragment_id] for fragment_id in fragment_ids}
        if len(locators) == 1:
            result[evidence_id] = next(iter(locators))
    return result


def build_dry_run(
    *,
    store: PostgresKnowledgeStore,
    data_base_path: Path,
    live_batch_path: Path,
    output_root: Path,
    repo_root: Path,
) -> Path:
    runner_git_commit = _clean_git_commit(repo_root)
    current = _active_state(store)
    human_settled = _human_settled_claim_ids(store)
    ownership = live_batch_ownership(live_batch_path)
    live_owned = set(ownership["owned_source_ids"])
    sources = [
        row["payload"] for key, row in current.items() if key[0] == "source_documents"
    ]
    fragments = [
        row["payload"] for key, row in current.items() if key[0] == "source_fragments"
    ]
    claims = [row["payload"] for key, row in current.items() if key[0] == "claims"]
    placeholder_owners = [
        row["payload"]
        for key, row in current.items()
        if key[0] in {"evidence_steps", "observations", "questions"}
    ]
    attestations = [
        row["payload"]
        for key, row in current.items()
        if key[0] == "argument_route_attestations"
    ]
    fragments_by_source: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for fragment in fragments:
        fragments_by_source[str(fragment.get("source_id") or "")].append(fragment)
    identity_counts = collections.Counter(
        (source.get("source_type"), source.get("transcript_id")) for source in sources
    )
    duplicate_identities = {key for key, count in identity_counts.items() if count > 1}

    run_dir = output_root / _utcstamp()
    plan_dir = run_dir / "plans"
    scenarios: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    source_plans: list[tuple[dict[str, Any], ChangeSetPlan, dict[str, Any]]] = []
    all_replacements: dict[tuple[str, str], dict[str, Any]] = {}
    all_retire_keys: list[tuple[str, str]] = []
    replacement_owner: dict[tuple[str, str], str] = {}

    for source in sorted(sources, key=lambda row: str(row.get("source_id") or "")):
        source_id = str(source.get("source_id") or "")
        transcript_id = str(source.get("transcript_id") or "")
        if source.get("locator_space") == LOCATOR_SPACE:
            scenarios["already_modern"].append(
                {"source_id": source_id, "transcript_id": transcript_id}
            )
            continue
        if source_id in live_owned or transcript_id in live_owned:
            scenarios["live_owned"].append(
                {"source_id": source_id, "transcript_id": transcript_id}
            )
            continue
        identity = (source.get("source_type"), source.get("transcript_id"))
        if identity in duplicate_identities:
            scenarios["duplicate_group"].append(
                {"source_id": source_id, "transcript_id": transcript_id}
            )
            continue
        if source_id in TARGETED_TRANSCRIPTS or transcript_id in TARGETED_TRANSCRIPTS:
            scenarios["targeted_rerun"].append(
                {"source_id": source_id, "transcript_id": transcript_id}
            )
            continue

        try:
            payload, raw, source_path = _source_material(source, data_base_path)
        except Exception as exc:
            scenarios["blocked"].append(
                {
                    "source_id": source_id,
                    "transcript_id": transcript_id,
                    "findings": [{"status": "source_error", "detail": str(exc)}],
                }
            )
            continue
        script = payload.get("script")
        projection = project_script(script)
        index = BodyLocatorIndex(script)
        coordinate_replacements: dict[tuple[str, str], dict[str, Any]] = {
            ("source_documents", source_id): migrate_source_document(
                source, raw_source=raw, projection=projection
            )
        }
        placeholder_replacements: dict[tuple[str, str], dict[str, Any]] = {}
        placeholder_retire_keys: list[tuple[str, str]] = []
        placeholder_records: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        preserved_human_claims: list[str] = []
        claim_anchor_changes = 0
        fragment_locators: dict[str, str] = {}
        for fragment in fragments_by_source[source_id]:
            fragment_id = str(fragment.get("fragment_id") or "")
            migrated, resolution = migrate_source_fragment(
                fragment,
                index,
                source_sha256=projection.body_sha256,
                attested_locator=ATTESTED_FRAGMENT_LOCATORS.get(fragment_id),
            )
            if migrated is None:
                owners: list[tuple[str, str, dict[str, Any]]] = []
                for owner in placeholder_owners:
                    owner_collection = (
                        "evidence_steps"
                        if owner.get("evidence_step_id")
                        else "observations"
                        if owner.get("observation_id")
                        else "questions"
                    )
                    owner_id_field = {
                        "evidence_steps": "evidence_step_id",
                        "observations": "observation_id",
                        "questions": "question_id",
                    }[owner_collection]
                    replacement = remove_source_fragment_reference(
                        placeholder_replacements.get(
                            (owner_collection, str(owner.get(owner_id_field) or "")),
                            owner,
                        ),
                        fragment_id=fragment_id,
                    )
                    if replacement is not None:
                        owners.append(
                            (
                                owner_collection,
                                str(owner.get(owner_id_field) or ""),
                                replacement,
                            )
                        )
                strict_placeholder = (
                    resolution.status == "no_excerpt"
                    and str(fragment.get("anchor_state") or "") == "unresolved"
                    and not str(fragment.get("verbatim_excerpt") or "")
                    and bool(owners)
                    and all(
                        str(owner.get("review_status") or "") == "candidate"
                        and str(owner.get("visibility") or "") == "internal"
                        and (
                            collection != "evidence_steps"
                            or str(owner.get("support_eligibility") or "")
                            == "withheld_missing_anchor"
                        )
                        for collection, owner_id, _ in owners
                        for owner in [current[(collection, owner_id)]["payload"]]
                    )
                )
                if strict_placeholder:
                    for collection, owner_id, replacement in owners:
                        placeholder_replacements[(collection, owner_id)] = replacement
                    placeholder_retire_keys.append(("source_fragments", fragment_id))
                    placeholder_records.append(
                        {
                            "fragment_id": fragment_id,
                            "owner_ids": [owner_id for _, owner_id, _ in owners],
                        }
                    )
                else:
                    findings.append(
                        {
                            "collection": "source_fragments",
                            "object_id": fragment.get("fragment_id"),
                            "status": resolution.status,
                        }
                    )
            else:
                fragment_locators[str(fragment["fragment_id"])] = str(
                    migrated["paragraph_key"]
                )
                coordinate_replacements[
                    ("source_fragments", str(fragment["fragment_id"]))
                ] = migrated
        evidence_locator_proofs = _evidence_fragment_locator_proofs(
            placeholder_owners, fragment_locators
        )
        for claim in claims:
            if not _claim_matches_source(
                claim, source_id=source_id, transcript_id=transcript_id
            ):
                continue
            migrated, claim_findings = migrate_claim_occurrence_anchors(
                claim,
                index,
                source_id=source_id,
                transcript_id=transcript_id,
                anchor_locator_by_evidence_id=evidence_locator_proofs,
            )
            unresolved = unresolved_statuses(claim_findings)
            if unresolved:
                findings.append(
                    {
                        "collection": "claims",
                        "object_id": claim.get("claim_id"),
                        "status": ",".join(sorted(unresolved)),
                    }
                )
            elif migrated is not None:
                assert_claim_semantics_unchanged(claim, migrated)
                claim_id = str(claim["claim_id"])
                if claim_id in human_settled:
                    # Numeric legacy keys in these pilot Claims are the source
                    # segment index, not an editorial/physical ordinal.  The
                    # resolver has just proved every exact highlight against
                    # the current professor body.  Preserve the human-settled
                    # record byte-for-byte instead of advancing its revision
                    # merely to spell the same body coordinate as Sxxxx.
                    preserved_human_claims.append(claim_id)
                    continue
                coordinate_replacements[("claims", claim_id)] = migrated
                claim_anchor_changes += sum(
                    item.get("status") == "changed" for item in claim_findings
                )
        for attestation in attestations:
            if str(attestation.get("source_id") or "") != source_id:
                continue
            migrated = migrate_route_attestation(
                attestation, source_sha256=projection.body_sha256
            )
            if migrated is not None:
                coordinate_replacements[
                    (
                        "argument_route_attestations",
                        str(attestation["argument_route_attestation_id"]),
                    )
                ] = migrated
        if findings:
            scenarios["blocked"].append(
                {
                    "source_id": source_id,
                    "transcript_id": transcript_id,
                    "findings": findings,
                }
            )
            replacements = placeholder_replacements
            retire_keys = placeholder_retire_keys
            cleanup_kind = "unresolved_placeholder_retirement"
            if not replacements and not retire_keys:
                continue
        else:
            replacements = {**coordinate_replacements, **placeholder_replacements}
            retire_keys = placeholder_retire_keys
            cleanup_kind = "full_source_coordinate_cleanup"

        for key in replacements:
            prior = replacement_owner.get(key)
            if prior is not None and prior != source_id:
                raise ValueError(
                    f"two source plans update {key[0]}/{key[1]}: {prior}, {source_id}"
                )
            replacement_owner[key] = source_id
        package_id = "WKP368-SOURCE-" + sha256_json(
            {
                "source_id": source_id,
                "source_body_sha256": projection.body_sha256,
                "source_before_sha256": current[("source_documents", source_id)][
                    "content_sha256"
                ],
            }
        )[:20]
        arrival = build_source_contract_cleanup_plan(
            package_id=package_id,
            current=current,
            replacements=replacements,
            human_settled_claim_ids=human_settled,
        )
        withdrawal = build_retirement_plan(
            retire_keys,
            current,
            reason="Retire unresolved empty SourceFragment placeholders after clearing candidate owner pointers",
            package_id=package_id,
            source_kind="wkp368_source_contract_cleanup",
        )
        plan = combined_plan(arrival, withdrawal)
        validate_change_set_plan_integrity(plan)
        summary = {
            "source_id": source_id,
            "transcript_id": transcript_id,
            "source_type": source.get("source_type"),
            "source_path": str(source_path),
            "source_file_sha256": hashlib.sha256(raw).hexdigest(),
            "source_body_sha256": projection.body_sha256,
            "claim_anchor_changes": claim_anchor_changes,
            "preserved_human_claim_ids": sorted(preserved_human_claims),
            "cleanup_kind": cleanup_kind,
            "placeholder_retirements": placeholder_records,
            "operation_counts": dict(
                sorted(collections.Counter(op.collection for op in plan.operations).items())
            ),
        }
        source_plans.append((summary, plan, replacements))
        all_replacements.update(replacements)
        all_retire_keys.extend(retire_keys)

    combined_arrival = build_source_contract_cleanup_plan(
        package_id="WKP368-SAFE-COHORT-" + sha256_json(
            [summary["source_id"] for summary, _, _ in source_plans]
        )[:20],
        current=current,
        replacements=all_replacements,
        human_settled_claim_ids=human_settled,
    )
    combined_withdrawal = build_retirement_plan(
        all_retire_keys,
        current,
        reason="Retire unresolved empty SourceFragment placeholders after clearing candidate owner pointers",
        package_id="WKP368-SAFE-COHORT",
        source_kind="wkp368_source_contract_cleanup",
    )
    combined = combined_plan(combined_arrival, combined_withdrawal)
    validate_change_set_plan_integrity(combined)
    preflight_checks = _preflight(store, combined)

    plan_refs: list[dict[str, Any]] = []
    for summary, plan, _ in source_plans:
        artifact = _seal(
            {
                "schema_version": PLAN_ARTIFACT_VERSION,
                "runner_git_commit": runner_git_commit,
                "source": summary,
                "plan": plan.as_dict(),
                "changed_records": _plan_record_audit(plan, current),
            }
        )
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", summary["source_id"]).strip("_")
        path = plan_dir / f"{slug}.{artifact['artifact_sha256'][:16]}.json"
        file_sha = _write_new(path, artifact)
        plan_refs.append(
            {
                **summary,
                "change_set_id": plan.change_set_id,
                "plan_artifact_path": str(path),
                "plan_artifact_sha256": file_sha,
                "plan_content_sha256": artifact["artifact_sha256"],
            }
        )

    report = _seal(
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runner_git_commit": runner_git_commit,
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
            "active_source_document_rows": len(sources),
            "active_logical_sources": len(identity_counts),
            "scenarios": {key: value for key, value in sorted(scenarios.items())},
            "ready_source_count": len(plan_refs),
            "operation_counts": dict(
                sorted(
                    collections.Counter(op.collection for op in combined.operations).items()
                )
            ),
            "preflight_checks": preflight_checks,
            "combined_plan_fingerprint_sha256": combined.fingerprint_sha256,
            "source_plans": plan_refs,
        }
    )
    report_path = run_dir / f"dry-run.{report['artifact_sha256'][:20]}.json"
    _write_new(report_path, report)
    print(report_path)
    print(
        canonical_json(
            {
                "ready_source_count": report["ready_source_count"],
                "operation_counts": report["operation_counts"],
                "scenario_counts": {
                    key: len(value) for key, value in report["scenarios"].items()
                },
                "artifact_sha256": report["artifact_sha256"],
            }
        )
    )
    return report_path


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


def _readback(store: PostgresKnowledgeStore, plan: ChangeSetPlan) -> None:
    with store.connect() as conn, conn.cursor() as cursor:
        for operation in plan.operations:
            cursor.execute(
                """SELECT revision, content_sha256, retired_at
                   FROM wang_knowledge.objects
                   WHERE collection=%s AND object_id=%s""",
                (operation.collection, operation.object_id),
            )
            row = cursor.fetchone()
            if (
                row is None
                or int(row[0]) != operation.after_revision
                or str(row[1]) != operation.after_sha256
                or (row[2] is not None) != (operation.operation == "retire")
            ):
                raise ValueError(
                    f"post-apply readback failed: {operation.collection}/{operation.object_id}"
                )


def _validate_existing_receipt(
    path: Path,
    *,
    source_id: str,
    change_set_id: str,
    report_file_sha256: str,
    plan_file_sha256: str,
) -> dict[str, Any]:
    receipt = json.loads(path.read_bytes())
    _validate_seal(receipt)
    expected = {
        "schema_version": APPLY_RECEIPT_VERSION,
        "source_id": source_id,
        "change_set_id": change_set_id,
        "dry_run_report_file_sha256": report_file_sha256,
        "source_plan_file_sha256": plan_file_sha256,
        "readback": "verified",
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError(f"existing receipt mismatch for {source_id}: {key}")
    return receipt


def apply_dry_run(
    *,
    store: PostgresKnowledgeStore,
    report_path: Path,
    live_batch_path: Path,
    repo_root: Path,
    backup_path: Path,
) -> None:
    runner_git_commit = _clean_git_commit(repo_root)
    backup = _verify_backup(backup_path)
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    _validate_seal(report)
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported dry-run report")
    if str(report.get("runner_git_commit")) != runner_git_commit:
        raise ValueError("runner commit differs from the frozen dry-run")
    report_file_sha = hashlib.sha256(report_bytes).hexdigest()
    receipt_dir = report_path.parent / "apply-receipts"
    receipts: list[dict[str, Any]] = []
    for position, ref in enumerate(report.get("source_plans") or [], 1):
        source_id = str(ref["source_id"])
        current_ownership = live_batch_ownership(live_batch_path)
        if source_id in set(current_ownership["owned_source_ids"]) or str(
            ref.get("transcript_id") or ""
        ) in set(current_ownership["owned_source_ids"]):
            raise ValueError(f"source became owned by the live batch: {source_id}")
        plan_path = Path(str(ref["plan_artifact_path"]))
        plan_bytes = plan_path.read_bytes()
        if hashlib.sha256(plan_bytes).hexdigest() != ref["plan_artifact_sha256"]:
            raise ValueError(f"plan file SHA changed: {plan_path}")
        artifact = json.loads(plan_bytes)
        _validate_seal(artifact)
        if artifact.get("runner_git_commit") != runner_git_commit:
            raise ValueError(f"plan runner commit changed: {plan_path}")
        if artifact.get("artifact_sha256") != ref.get("plan_content_sha256"):
            raise ValueError(f"plan content SHA changed: {plan_path}")
        source_path = Path(str(artifact["source"]["source_path"]))
        raw = source_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact["source"]["source_file_sha256"]:
            raise ValueError(f"source file changed after dry-run: {source_id}")
        parsed = json.loads(raw) if source_path.suffix == ".json" else None
        if parsed is not None:
            script = parsed if isinstance(parsed, list) else parsed.get("script")
        else:
            script = script_from_markdown_blocks(
                markdown_blocks(raw.decode("utf-8"))
            )
        if project_script(script).body_sha256 != artifact["source"]["source_body_sha256"]:
            raise ValueError(f"source body changed after dry-run: {source_id}")
        plan = _plan_from_artifact(artifact)
        if plan.change_set_id != ref["change_set_id"]:
            raise ValueError(f"ChangeSet identity changed: {source_id}")
        result = store.apply_plan(
            plan,
            metadata={
                "dry_run_report_path": str(report_path),
                "dry_run_report_file_sha256": report_file_sha,
                "dry_run_report_content_sha256": report["artifact_sha256"],
                "source_plan_path": str(plan_path),
                "source_plan_file_sha256": ref["plan_artifact_sha256"],
                "runner_git_commit": report["runner_git_commit"],
                "registry_backup": backup,
            },
        )
        _readback(store, plan)
        receipt = _seal(
            {
                "schema_version": APPLY_RECEIPT_VERSION,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "source_id": source_id,
                "transcript_id": ref.get("transcript_id"),
                "change_set_id": plan.change_set_id,
                "result": result,
                "dry_run_report_file_sha256": report_file_sha,
                "source_plan_file_sha256": ref["plan_artifact_sha256"],
                "readback": "verified",
            }
        )
        receipt_path = receipt_dir / (
            re.sub(r"[^A-Za-z0-9._-]+", "_", source_id).strip("_")
            + f".{plan.change_set_id}.json"
        )
        if receipt_path.exists():
            receipt = _validate_existing_receipt(
                receipt_path,
                source_id=source_id,
                change_set_id=plan.change_set_id,
                report_file_sha256=report_file_sha,
                plan_file_sha256=ref["plan_artifact_sha256"],
            )
        else:
            receipt["registry_backup"] = backup
            receipt = _seal(receipt)
            _write_new(receipt_path, receipt)
        receipts.append(
            {
                "source_id": source_id,
                "change_set_id": plan.change_set_id,
                "receipt_path": str(receipt_path),
                "receipt_content_sha256": receipt["artifact_sha256"],
            }
        )
        print(f"[{position}/{len(report['source_plans'])}] {source_id} {result['status']}", flush=True)

    summary = _seal(
        {
            "schema_version": APPLY_SUMMARY_VERSION,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "runner_git_commit": runner_git_commit,
            "dry_run_report_path": str(report_path),
            "dry_run_report_file_sha256": report_file_sha,
            "dry_run_report_content_sha256": report["artifact_sha256"],
            "registry_backup": backup,
            "source_count": len(receipts),
            "receipts": receipts,
        }
    )
    summary_path = report_path.parent / f"apply-summary.{report_file_sha[:20]}.json"
    if summary_path.exists():
        existing = json.loads(summary_path.read_bytes())
        _validate_seal(existing)
        if (
            existing.get("dry_run_report_file_sha256") != report_file_sha
            or existing.get("source_count") != len(receipts)
        ):
            raise ValueError("existing apply summary does not match this dry-run")
    else:
        _write_new(summary_path, summary)
    print(summary_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-base-path", type=Path,
        default=Path("/opt/homebrew/var/www/church/web/data"),
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--live-batch", type=Path, default=None)
    parser.add_argument("--apply-report", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    data_base_path = args.data_base_path
    output_root = args.output_root or (
        data_base_path
        / "wang-knowledge-platform/staging/source-contract-cleanup/WKP368"
    )
    live_batch = args.live_batch or (
        data_base_path
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
            data_base_path=data_base_path,
            live_batch_path=live_batch,
            output_root=output_root,
            repo_root=repo_root,
        )


if __name__ == "__main__":
    main()
