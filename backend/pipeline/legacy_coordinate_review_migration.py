"""Plan/apply status-only #397 review reconciliation after coordinate proof.

The input must be a frozen coordinate preflight.  Planning re-verifies its
ready rows, freezes the complete related graph for CAS, and writes no DB rows.
Apply uses the shared backup-gated legacy ChangeSet path, which re-verifies the
historical review and source before each atomic status/review-event write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore, _contains_exact_value, sha256_json,
    validate_change_set_plan_integrity,
)
from backend.pipeline.legacy_candidate_reconciliation import (
    _current_claims, _encode_report, apply_frozen, plan_review_migration,
)
from backend.pipeline.legacy_coordinate_review_preflight import (
    READY_REASON, verify_frozen_rows,
)


SOURCE_KIND = "legacy_coordinate_only_review_reconciliation_v1"


def proof_code_sha256() -> str:
    """Bind the frozen decision to the exact verifier and planner source."""

    repo_backend = Path(__file__).resolve().parents[1]
    paths = (
        repo_backend / "pipeline" / "legacy_coordinate_review_proof.py",
        repo_backend / "pipeline" / "legacy_coordinate_review_preflight.py",
        repo_backend / "pipeline" / "legacy_coordinate_review_migration.py",
        repo_backend / "pipeline" / "legacy_candidate_reconciliation.py",
        repo_backend / "api" / "canonical_repository" / "postgres_store.py",
    )
    return sha256_json({
        path.relative_to(repo_backend).as_posix():
            hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    })


def _graph_guards_for_source(
    rows: list[dict[str, Any]], store: PostgresKnowledgeStore,
) -> dict[str, list[dict[str, Any]]]:
    """Freeze the exact related-record set used by apply_plan's graph CAS."""

    claim_ids = sorted(str(row["claim_id"]) for row in rows)
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            """SELECT object_id,payload FROM wang_knowledge.objects
               WHERE collection='claims' AND retired_at IS NULL
                 AND object_id=ANY(%s)""", (claim_ids,),
        )
        claims = {str(object_id): payload for object_id, payload in cursor}
        if set(claims) != set(claim_ids):
            raise ValueError("planned Claim disappeared before graph freeze")
        evidence_ids = sorted({
            str(evidence_id) for claim in claims.values()
            for evidence_id in claim.get("evidence_step_ids") or []
        })
        cursor.execute(
            """SELECT object_id,payload FROM wang_knowledge.objects
               WHERE collection='evidence_steps' AND retired_at IS NULL
                 AND object_id=ANY(%s)""", (evidence_ids,),
        )
        evidence = {str(object_id): payload for object_id, payload in cursor}
        if set(evidence) != set(evidence_ids):
            raise ValueError("planned EvidenceStep disappeared before graph freeze")
        fragment_ids = sorted({
            str(fragment_id) for step in evidence.values()
            for fragment_id in (
                list(step.get("source_fragment_ids") or [])
                + ([step["source_fragment_id"]]
                   if step.get("source_fragment_id") else [])
            )
        })
        expected_keys: dict[str, set[tuple[str, str]]] = {}
        own_evidence: dict[str, set[str]] = {}
        for claim_id, claim in claims.items():
            own_evidence[claim_id] = set(claim.get("evidence_step_ids") or [])
            own_fragments = {
                str(fragment_id)
                for evidence_id in own_evidence[claim_id]
                for fragment_id in (
                    list(evidence[evidence_id].get("source_fragment_ids") or [])
                    + ([evidence[evidence_id]["source_fragment_id"]]
                       if evidence[evidence_id].get("source_fragment_id") else [])
                )
            }
            expected_keys[claim_id] = (
                {("evidence_steps", evidence_id) for evidence_id in own_evidence[claim_id]}
                | {("source_fragments", fragment_id) for fragment_id in own_fragments}
            )
        cursor.execute(
            """SELECT collection,object_id,revision,content_sha256,payload
               FROM wang_knowledge.objects
               WHERE retired_at IS NULL AND collection <> 'claims'
                 AND (payload::text LIKE ANY(%s) OR object_id=ANY(%s))""",
            ([f"%{value}%" for value in claim_ids + evidence_ids],
             sorted(set(evidence_ids + fragment_ids))),
        )
        guards: dict[str, list[dict[str, Any]]] = {claim_id: [] for claim_id in claim_ids}
        for collection, object_id, revision, content_sha, payload in cursor:
            collection = str(collection)
            object_id = str(object_id)
            for claim_id in claim_ids:
                if (
                    _contains_exact_value(payload, claim_id)
                    or (collection, object_id) in expected_keys[claim_id]
                    or (collection == "knowledge_relations" and any(
                        _contains_exact_value(payload, evidence_id)
                        for evidence_id in own_evidence[claim_id]
                    ))
                ):
                    guards[claim_id].append({
                        "collection": collection, "object_id": object_id,
                        "revision": int(revision), "content_sha256": str(content_sha),
                    })
    for claim_id in claim_ids:
        guards[claim_id].sort(key=lambda item: (item["collection"], item["object_id"]))
        observed_keys = {(item["collection"], item["object_id"])
                         for item in guards[claim_id]}
        if not expected_keys[claim_id] <= observed_keys:
            raise ValueError(f"incomplete related graph freeze: {claim_id}")
    return guards


def dry_run(
    preflight_path: Path, output_root: Path, store: PostgresKnowledgeStore,
) -> dict[str, Any]:
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("migration output root must be empty")
    report = json.loads(preflight_path.read_text(encoding="utf-8"))
    if (
        report.get("schema_version") != "wang_legacy_coordinate_review_preflight_v1"
        or report.get("snapshot_sha256") != sha256_json(report.get("rows"))
        or report.get("candidate_count") != len(report["rows"])
    ):
        raise ValueError("coordinate preflight is invalid")
    ready = [dict(row) for row in report["rows"] if row["reason"] == READY_REASON]
    verify_frozen_rows(ready, store)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ready:
        by_source[str(row["source_id"])].append(row)
    guards: dict[str, list[dict[str, Any]]] = {}
    for source_id, group in sorted(by_source.items()):
        guards.update(_graph_guards_for_source(group, store))
    for row in ready:
        row["graph_guard_sha256"] = sha256_json(guards[row["claim_id"]])
        row["coordinate_proof_code_sha256"] = proof_code_sha256()
    ready_by_id = {row["claim_id"]: row for row in ready}
    report["rows"] = [ready_by_id.get(row["claim_id"], row) for row in report["rows"]]
    report["counts"] = dict(sorted(Counter(row["reason"] for row in report["rows"]).items()))
    report["snapshot_sha256"] = sha256_json(report["rows"])
    current = _current_claims(store, sorted(ready_by_id))
    plans = [
        plan_review_migration(
            group, current, freeze_sha256=report["snapshot_sha256"],
            source_kind=SOURCE_KIND,
        )
        for _, group in sorted(by_source.items())
    ]
    for plan in plans:
        validate_change_set_plan_integrity(plan)
    document = {
        "schema_version": "wang_legacy_coordinate_review_migration_plans_v1",
        "freeze_sha256": report["snapshot_sha256"],
        "claim_related_graph_guards": guards,
        "plans": [asdict(plan) for plan in plans],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    _encode_report(output_root / "preflight.json", report)
    _encode_report(output_root / "migration-plans.json", document)
    return {
        "planned_claims": len(ready), "source_units": len(plans),
        "snapshot_sha256": report["snapshot_sha256"],
        "output_root": str(output_root),
        "note": "Dry-run only; backup required before apply.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-path", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--max-source-units", type=int)
    args = parser.parse_args()
    store = PostgresKnowledgeStore()
    if args.apply:
        if args.backup_path is None or args.preflight_path is not None:
            parser.error("--apply requires --backup-path and no --preflight-path")
        result = apply_frozen(
            args.output_root, args.backup_path, store,
            max_source_units=args.max_source_units,
        )
    else:
        if args.preflight_path is None or args.backup_path is not None:
            parser.error("dry-run requires --preflight-path and no --backup-path")
        result = dry_run(args.preflight_path, args.output_root, store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
