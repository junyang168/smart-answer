"""Freeze and audit an explicit CanonicalViewpoint backfill source cohort."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_backfill import (
    audit_backfill_readiness,
    freeze_source_manifest,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    build_identity_candidate_seeds,
    build_resolution_ledger,
    sha256_json,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def run_preflight(
    *,
    selection_path: Path,
    output_dir: Path,
    database_url: str | None = None,
    argument_layer_path: Path | None = None,
    created_at: str | None = None,
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
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "source-manifest.json": manifest,
        "coverage-snapshot.json": artifacts["coverage_snapshot"],
        "claim-manifest.json": artifacts["claim_manifest"],
        "readiness-report.json": artifacts["readiness"],
        "preflight-packet.json": artifacts["preflight_packet"],
        "resolution-ledger.json": ledger.model_dump(mode="json"),
        "resolution-queue.json": queue,
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
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--argument-layer", type=Path)
    parser.add_argument("--created-at")
    args = parser.parse_args()
    print(
        json.dumps(
            run_preflight(
                selection_path=args.selection,
                output_dir=args.output_dir,
                database_url=args.database_url,
                argument_layer_path=args.argument_layer,
                created_at=args.created_at,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
