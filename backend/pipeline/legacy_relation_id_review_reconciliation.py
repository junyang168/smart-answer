"""One-time, ID-only reconciliation of legacy reviewed Claim relation graphs.

No model call or graph write: the reviewed package is transformed in memory,
and only Claim review metadata is migrated after source and graph CAS checks.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore, _contains_exact_value, _normalize_records, sha256_json,
)
from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.legacy_auto_applied_review_reconciliation import verify_historical_replay
from backend.pipeline.legacy_candidate_reconciliation import (
    _current_claims, _encode_report, apply_frozen, inspect_legacy_bundle,
    inventory, plan_review_migration,
)
from backend.pipeline.legacy_withdrawn_review_reconciliation import (
    _live_graph_guard, _package_graph_unchanged, _related_records,
)
from backend.pipeline.relation_id_namespace import migrate_legacy_cross_section_relation_ids

SOURCE_KIND = "legacy_relation_id_only_review_reconciliation_v1"
READY_REASON = "relation_id_only_graph_verified"


def _candidate_package(
    reviewed_path: Path, claim_id: str, adjudication_status: str,
) -> tuple[dict[str, Any], dict[str, str]] | None:
    if adjudication_status not in {"auto_applied", "withdrawn"}:
        return None
    bundle = inspect_legacy_bundle(reviewed_path)
    if bundle["reason"] is not None:
        return None
    original = json.loads(Path(bundle["package_path"]).read_text(encoding="utf-8"))
    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
    original_normalized, _ = _normalize_records(original)
    reviewed_normalized, _ = _normalize_records(reviewed)
    if adjudication_status == "withdrawn":
        if not _package_graph_unchanged(original_normalized, reviewed_normalized, claim_id):
            return None
        replay_proof: dict[str, str] = {}
    else:
        replay = verify_historical_replay(reviewed_path)
        if replay is None:
            return None
        replay_proof = replay
    effective, manifest = migrate_legacy_cross_section_relation_ids(reviewed)
    if (
        manifest["status"] != "applied"
        or manifest["semantic_change"] != "none_relation_identifiers_only"
        or manifest["round_trip_verified"] is not True
    ):
        return None
    effective_normalized, _ = _normalize_records(effective)
    if effective_normalized["claims"].get(claim_id) != reviewed_normalized["claims"].get(claim_id):
        raise ValueError(f"relation namespace changed Claim content: {claim_id}")
    _related_records(effective_normalized, claim_id)
    proof = {
        "relation_id_manifest_sha256": sha256_json(manifest),
        "effective_package_sha256": sha256_json(effective),
        **replay_proof,
    }
    return effective_normalized, proof


def verify_relation_id_candidate(
    reviewed_path: Path, claim_id: str, adjudication_status: str,
) -> dict[str, str] | None:
    """Recompute the frozen ID-only proof immediately before a write."""
    result = _candidate_package(reviewed_path, claim_id, adjudication_status)
    return None if result is None else result[1]


def dry_run(artifact_root: Path, output_root: Path, store: PostgresKnowledgeStore) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise ValueError("dry-run output root must be empty")
    report = inventory(artifact_root, store)
    candidates = [row for row in report["rows"] if
                  row["reason"] == "legacy_adjudicated_claim_needs_patch_replay"
                  and row.get("adjudication_status") in {"auto_applied", "withdrawn"}
                  and row.get("review_decision") == "changes_suggested"]
    items = []
    for row in candidates:
        try:
            result = _candidate_package(
                Path(row["reviewed_candidate_path"]), row["claim_id"], row["adjudication_status"]
            )
            if result is not None:
                effective, proof = result
                items.append((row, _related_records(effective, row["claim_id"]), proof))
        except ValueError:
            continue
    ids = [row["claim_id"] for row, _, _ in items]
    evidence_ids = sorted({object_id for _, expected, _ in items
                           for collection, object_id in expected if collection == "evidence_steps"})
    related_ids = sorted({object_id for _, expected, _ in items for _, object_id in expected})
    live: dict[str, dict[tuple[str, str], tuple[int, str, dict[str, Any]]]] = {claim_id: {} for claim_id in ids}
    source_hashes: dict[str, set[str]] = {}
    if ids:
        with store.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT object_id,payload FROM wang_knowledge.objects
                   WHERE collection='source_documents' AND retired_at IS NULL AND object_id = ANY(%s)""",
                (sorted({row["source_id"] for row, _, _ in items}),),
            )
            for source_id, payload in cursor.fetchall():
                source_hashes[str(source_id)] = {str(value) for value in (
                    payload.get("source_file_sha256"), payload.get("source_body_sha256")
                ) if value}
            cursor.execute(
                """SELECT collection,object_id,revision,content_sha256,payload
                   FROM wang_knowledge.objects WHERE retired_at IS NULL AND collection <> 'claims'
                   AND (payload::text LIKE ANY(%s) OR object_id = ANY(%s))""",
                ([f"%{value}%" for value in ids + evidence_ids], related_ids),
            )
            for collection, object_id, revision, content_sha, payload in cursor.fetchall():
                for row, expected, _ in items:
                    claim_id = row["claim_id"]
                    own_evidence = {eid for coll, eid in expected if coll == "evidence_steps"}
                    if (
                        _contains_exact_value(payload, claim_id)
                        or (collection, object_id) in expected
                        or (collection == "knowledge_relations" and any(
                            _contains_exact_value(payload, eid) for eid in own_evidence
                        ))
                    ):
                        live[claim_id][(str(collection), str(object_id))] = (
                            int(revision), str(content_sha), payload
                        )
    guards = {}
    for row, expected, proof in items:
        guard = _live_graph_guard(expected, live[row["claim_id"]], source_hashes)
        if guard is None:
            continue
        row.update({"reason": READY_REASON, "target_review_status": "ai_consensus_reviewed",
                    "graph_guard_sha256": sha256_json(guard), **proof})
        guards[row["claim_id"]] = guard
    report["counts"] = dict(sorted(Counter(row["reason"] for row in report["rows"]).items()))
    report["snapshot_sha256"] = sha256_json(report["rows"])
    report["note"] = "Read-only ID-only proof. Backup and source/graph CAS required before status-only migration."
    ready: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report["rows"]:
        if row["reason"] == READY_REASON:
            ready[row["source_id"]].append(row)
    current = _current_claims(store, list(guards))
    plans = [plan_review_migration(group, current, freeze_sha256=report["snapshot_sha256"],
                                   source_kind=SOURCE_KIND) for _, group in sorted(ready.items())]
    document = {"schema_version": "wang_legacy_relation_id_review_migration_plans_v1",
                "freeze_sha256": report["snapshot_sha256"],
                "claim_related_graph_guards": guards, "plans": [asdict(plan) for plan in plans]}
    _encode_report(output_root / "preflight.json", report)
    _encode_report(output_root / "migration-plans.json", document)
    return {"residual_candidates": len(candidates), "local_graph_valid": len(items),
            "qualified": len(guards), "source_units": len(plans),
            "snapshot_sha256": report["snapshot_sha256"], "output_root": str(output_root)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=wang_platform_paths().claim_layer_staging)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--max-source-units", type=int)
    args = parser.parse_args()
    store = PostgresKnowledgeStore()
    if args.apply:
        if args.backup_path is None:
            parser.error("--apply requires --backup-path")
        result = apply_frozen(args.output_root, args.backup_path, store,
                              max_source_units=args.max_source_units)
    else:
        result = dry_run(args.artifact_root, args.output_root, store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
