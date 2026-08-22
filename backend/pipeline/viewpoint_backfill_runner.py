"""Freeze and audit an explicit CanonicalViewpoint backfill source cohort."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_backfill import (
    audit_backfill_readiness,
    freeze_source_manifest,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    build_identity_candidate_seeds,
    build_resolution_ledger,
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_semantic_scheduler import (
    DEFAULT_MAX_BUNDLE_BYTES,
    DEFAULT_MAX_BUNDLE_ITEMS,
    build_semantic_bundle_schedule,
)
from backend.api.canonical_repository.viewpoint_recall_blocking import (
    DEFAULT_MAX_BLOCK_CLAIMS,
    DEFAULT_MAX_NEIGHBORS,
    build_viewpoint_recall_blocking,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _load_completed_results(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = _read(path)
    if payload.get("schema_version") != "wang_viewpoint_semantic_completed_results_v1":
        raise ValueError("unsupported semantic completed-results artifact")
    unsigned = dict(payload)
    stated_sha = str(unsigned.pop("artifact_sha256", ""))
    if not stated_sha or stated_sha != sha256_json(unsigned):
        raise ValueError("semantic completed-results artifact SHA mismatch")
    rows = list(payload.get("results") or [])
    reuse_keys = [str(row.get("reuse_key_sha256") or "") for row in rows]
    if reuse_keys != sorted(set(reuse_keys)):
        raise ValueError("semantic completed results must be sorted and unique")
    completed: dict[str, str] = {}
    for row in rows:
        if row.get("status") != "complete":
            continue
        reuse_key = str(row.get("reuse_key_sha256") or "")
        artifact_sha = str(row.get("result_artifact_sha256") or "")
        if not reuse_key or not artifact_sha:
            raise ValueError("completed semantic result is missing a SHA")
        completed[reuse_key] = artifact_sha
    return completed


def _claim_set_closure(
    *,
    selection: dict[str, Any],
    source_manifest: dict[str, Any],
    claim_manifest: dict[str, Any],
    change_set_ids: list[str],
    change_set_states: list[dict[str, str]],
) -> dict[str, Any]:
    claim_rows = sorted(
        claim_manifest["claims"], key=lambda item: str(item["claim_id"])
    )
    payload = {
        "schema_version": "wang_viewpoint_claim_set_closure_v1",
        "closure_policy_version": "explicit_applied_change_set_snapshot_v1",
        "selection_id": selection["selection_id"],
        "selection_sha256": selection["selection_sha256"],
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "claim_manifest_sha256": claim_manifest["manifest_sha256"],
        "change_sets": change_set_states,
        "selected_change_sets_terminal": all(
            item["status"] == "applied" for item in change_set_states
        ),
        "source_completeness_claimed": False,
        "closure_basis": (
            "exact Claims written by the explicitly selected applied ChangeSets; "
            "later extraction is outside this immutable cohort"
        ),
        "claim_count": len(claim_rows),
        "claim_set_digest_sha256": sha256_json(claim_rows),
    }
    payload["artifact_sha256"] = sha256_json(payload)
    return payload


def _recheck_claim_set_closure(
    *,
    store: PostgresKnowledgeStore,
    change_set_ids: list[str],
    expected_change_set_states: list[dict[str, str]],
    claim_manifest: dict[str, Any],
) -> None:
    if store.list_change_set_states(change_set_ids) != expected_change_set_states:
        raise ValueError("selected ChangeSet state changed during preflight")
    expected_ids = sorted(str(item["claim_id"]) for item in claim_manifest["claims"])
    current_ids = sorted(store.list_change_set_object_ids(change_set_ids, "claims"))
    if current_ids != expected_ids:
        raise ValueError("selected ChangeSet Claim denominator changed during preflight")
    current_claims = {
        str(item["claim_id"]): item for item in store.list_records("claims")
    }
    for pinned in claim_manifest["claims"]:
        claim_id = str(pinned["claim_id"])
        current = current_claims.get(claim_id)
        if current is None:
            raise ValueError(f"{claim_id}: Claim disappeared during preflight")
        claim = ClaimRecord.model_validate(current)
        if (
            claim.revision != int(pinned["pinned_claim_revision"])
            or semantic_record_sha(claim) != pinned["claim_revision_sha256"]
        ):
            raise ValueError(f"{claim_id}: Claim changed during preflight")


def run_preflight(
    *,
    selection_path: Path,
    output_dir: Path,
    database_url: str | None = None,
    argument_layer_path: Path | None = None,
    created_at: str | None = None,
    max_bundle_items: int = DEFAULT_MAX_BUNDLE_ITEMS,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
    max_recall_neighbors: int = DEFAULT_MAX_NEIGHBORS,
    max_recall_block_claims: int = DEFAULT_MAX_BLOCK_CLAIMS,
    completed_results_path: Path | None = None,
) -> dict[str, Any]:
    selection = _read(selection_path)
    store = PostgresKnowledgeStore(database_url)
    collections = {
        name: store.list_records(name)
        for name in (
            "source_documents",
            "source_fragments",
            "evidence_steps",
            "claims",
            "claim_relations",
            "claim_relation_constraints",
            "viewpoint_claim_links",
        )
    }
    manifest = freeze_source_manifest(selection, collections["source_documents"])
    change_set_ids = [
        str(row["lineage_ref"])
        for row in manifest["sources"]
        if row["latest_extraction_status"] == "applied"
    ]
    change_set_states = store.list_change_set_states(change_set_ids)
    if (
        [item["change_set_id"] for item in change_set_states]
        != sorted(change_set_ids)
        or any(item["status"] != "applied" for item in change_set_states)
    ):
        raise ValueError("selected source cohort requires existing applied ChangeSets")
    claim_scope_ids = store.list_change_set_object_ids(change_set_ids, "claims")
    artifacts = audit_backfill_readiness(
        manifest=manifest,
        sources=collections["source_documents"],
        fragments=collections["source_fragments"],
        evidence_steps=collections["evidence_steps"],
        claims=collections["claims"],
        claim_scope_ids=claim_scope_ids,
        created_at=created_at
        or str(selection.get("selected_at") or datetime.now(timezone.utc).isoformat()),
    )
    manifest_claim_ids = {
        str(row["claim_id"]) for row in artifacts["claim_manifest"]["claims"]
    }
    scoped_relations = [
        row
        for row in collections["claim_relations"]
        if {
            str(row.get("from_id") or row.get("source_id") or row.get("from_claim_id")),
            str(row.get("to_id") or row.get("target_id") or row.get("to_claim_id")),
        }.issubset(manifest_claim_ids)
    ]
    scoped_constraints = [
        row
        for row in collections["claim_relation_constraints"]
        if {str(row.get("source_id")), str(row.get("target_id"))}.issubset(
            manifest_claim_ids
        )
    ]
    recall_blocking = build_viewpoint_recall_blocking(
        claim_manifest=artifacts["claim_manifest"],
        claims=collections["claims"],
        claim_relations=scoped_relations,
        existing_links=collections["viewpoint_claim_links"],
        max_neighbors_per_claim=max_recall_neighbors,
        max_block_claims=max_recall_block_claims,
    )
    candidates = build_identity_candidate_seeds(
        artifacts["claim_manifest"],
        scoped_relations,
        scoped_constraints,
        collections["viewpoint_claim_links"],
    )
    ledger = build_resolution_ledger(
        artifacts["claim_manifest"],
        [],
        coverage_snapshot_id=artifacts["coverage_snapshot"]["coverage_snapshot_id"],
    )
    queue = {
        "schema_version": "wang_viewpoint_backfill_resolution_queue_v1",
        "preflight_packet_sha256": artifacts["preflight_packet"]["artifact_sha256"],
        "claim_manifest_sha256": artifacts["claim_manifest"]["manifest_sha256"],
        "resolution_ledger_id": ledger.resolution_ledger_id,
        "resolution_ledger_sha256": ledger.artifact_sha256,
        "claim_count": len(artifacts["claim_manifest"]["claims"]),
        "identity_candidate_count": len(candidates),
        "scoped_claim_relation_count": len(scoped_relations),
        "excluded_out_of_scope_claim_relation_count": len(collections["claim_relations"])
        - len(scoped_relations),
        "identity_candidates": [item.model_dump(mode="json") for item in candidates],
    }
    queue["artifact_sha256"] = sha256_json(queue)
    semantic_schedule = build_semantic_bundle_schedule(
        preflight_packet_sha256=artifacts["preflight_packet"]["artifact_sha256"],
        resolution_queue_sha256=queue["artifact_sha256"],
        claim_manifest=artifacts["claim_manifest"],
        candidates=candidates,
        claims=collections["claims"],
        evidence_steps=collections["evidence_steps"],
        source_fragments=collections["source_fragments"],
        recall_blocking=recall_blocking,
        completed_results_by_reuse_key=_load_completed_results(completed_results_path),
        max_bundle_items=max_bundle_items,
        max_bundle_bytes=max_bundle_bytes,
    )
    selected_ids = {row["source_id"] for row in manifest["sources"]}
    argument_ids: set[str] = set()
    argument_entry_count: int | None = None
    if argument_layer_path:
        argument = _read(argument_layer_path)
        argument_rows = list(argument.get("sources") or [])
        argument_entry_count = len(argument_rows)
        argument_ids = {
            str(value)
            for row in argument_rows
            for value in (
                list(row.get("source_ids") or [])
                or [row.get("source_id") or row.get("id")]
            )
            if value
        }
    discrepancy = {
        "schema_version": "wang_viewpoint_backfill_discrepancy_v1",
        "selection_id": selection["selection_id"],
        "selected_latest_source_count": len(selected_ids),
        "active_database_source_count": len(collections["source_documents"]),
        "argument_layer_source_count": len(argument_ids) if argument_layer_path else None,
        "argument_layer_entry_count": argument_entry_count,
        "database_only_source_ids": sorted(
            {str(row["source_id"]) for row in collections["source_documents"]} - selected_ids
        ),
        "argument_layer_only_source_ids": sorted(argument_ids - selected_ids),
        "selected_missing_from_argument_layer_ids": sorted(selected_ids - argument_ids)
        if argument_layer_path
        else [],
        "selection_rule": "explicit_manifest_only; database and argument-layer sets are diagnostics",
    }
    discrepancy["artifact_sha256"] = sha256_json(discrepancy)
    closure = _claim_set_closure(
        selection=selection,
        source_manifest=manifest,
        claim_manifest=artifacts["claim_manifest"],
        change_set_ids=change_set_ids,
        change_set_states=change_set_states,
    )
    _recheck_claim_set_closure(
        store=store,
        change_set_ids=change_set_ids,
        expected_change_set_states=change_set_states,
        claim_manifest=artifacts["claim_manifest"],
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "source-manifest.json": manifest,
        "coverage-snapshot.json": artifacts["coverage_snapshot"],
        "claim-manifest.json": artifacts["claim_manifest"],
        "claim-set-closure.json": closure,
        "readiness-report.json": artifacts["readiness"],
        "preflight-packet.json": artifacts["preflight_packet"],
        "resolution-ledger.json": ledger.model_dump(mode="json"),
        "resolution-queue.json": queue,
        "recall-blocking-report.json": recall_blocking.model_dump(mode="json"),
        "semantic-bundle-schedule.json": semantic_schedule.model_dump(mode="json"),
        "source-set-discrepancy.json": discrepancy,
    }
    for name, value in outputs.items():
        _write(output_dir / name, value)
    return {
        "status": "ready_for_resolution"
        if artifacts["preflight_packet"]["resolution_allowed"]
        else "blocked",
        "output_dir": str(output_dir),
        **artifacts["readiness"]["summary"],
        "preflight_packet_sha256": artifacts["preflight_packet"]["artifact_sha256"],
        "identity_candidate_count": len(candidates),
        "recall_covered_claim_count": recall_blocking.statistics["covered_claim_count"],
        "recall_uncovered_claim_count": recall_blocking.statistics["uncovered_claim_count"],
        "recall_candidate_pair_count": recall_blocking.statistics[
            "unique_candidate_pair_count"
        ],
        "semantic_bundle_count": semantic_schedule.statistics["bundle_count"],
        "semantic_exception_count": semantic_schedule.statistics[
            "exception_candidate_count"
        ],
        "semantic_reused_count": semantic_schedule.statistics["reused_candidate_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--argument-layer", type=Path)
    parser.add_argument("--created-at")
    parser.add_argument("--max-bundle-items", type=int, default=DEFAULT_MAX_BUNDLE_ITEMS)
    parser.add_argument("--max-bundle-bytes", type=int, default=DEFAULT_MAX_BUNDLE_BYTES)
    parser.add_argument("--max-recall-neighbors", type=int, default=DEFAULT_MAX_NEIGHBORS)
    parser.add_argument(
        "--max-recall-block-claims", type=int, default=DEFAULT_MAX_BLOCK_CLAIMS
    )
    parser.add_argument("--completed-results", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run_preflight(
                selection_path=args.selection,
                output_dir=args.output_dir,
                database_url=args.database_url,
                argument_layer_path=args.argument_layer,
                created_at=args.created_at,
                max_bundle_items=args.max_bundle_items,
                max_bundle_bytes=args.max_bundle_bytes,
                max_recall_neighbors=args.max_recall_neighbors,
                max_recall_block_claims=args.max_recall_block_claims,
                completed_results_path=args.completed_results,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
