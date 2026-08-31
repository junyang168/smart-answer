"""Audit passage scope recall without mutating Wang master data.

The report shows which Claims enter from direct Scripture overlap, which are
recovered through reviewed argument dependencies or approved route
attestations, and which context candidates remain isolated.  It is an impact
report for a later resolution run, never an approval or apply artifact.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_scope_selection import select_scope_claims


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_VERSION = "wang_viewpoint_scope_coverage_audit_v1"

MASTER_DATA_ID_FIELDS = {
    "claim_relations": "claim_relation_id",
    "viewpoint_claim_links": "viewpoint_claim_link_id",
    "argument_routes": "argument_route_id",
    "argument_route_attestations": "argument_route_attestation_id",
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_scope(scope: dict[str, Any]) -> str:
    stated = str(scope.get("artifact_sha256") or "")
    unsigned = {key: value for key, value in scope.items() if key != "artifact_sha256"}
    if not stated or stated != sha256_json(unsigned):
        raise ValueError("scope artifact SHA mismatch")
    return stated


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable audit differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _canonical_master_snapshot(
    *,
    claim_relations: list[dict[str, Any]],
    viewpoint_claim_links: list[dict[str, Any]],
    argument_routes: list[dict[str, Any]],
    route_attestations: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    collections = {
        "claim_relations": claim_relations,
        "viewpoint_claim_links": viewpoint_claim_links,
        "argument_routes": argument_routes,
        "argument_route_attestations": route_attestations,
    }
    return {
        name: sorted(
            rows,
            key=lambda row: str(row.get(MASTER_DATA_ID_FIELDS[name]) or ""),
        )
        for name, rows in collections.items()
    }


def build_scope_coverage_audit(
    *,
    scope: dict[str, Any],
    claim_relations: list[dict[str, Any]],
    viewpoint_claim_links: list[dict[str, Any]],
    argument_routes: list[dict[str, Any]],
    route_attestations: list[dict[str, Any]],
) -> dict[str, Any]:
    scope_sha = _validate_scope(scope)
    claim_index = {
        str(row["claim_id"]): dict(row) for row in (scope.get("claims") or [])
    }
    master_snapshot = _canonical_master_snapshot(
        claim_relations=claim_relations,
        viewpoint_claim_links=viewpoint_claim_links,
        argument_routes=argument_routes,
        route_attestations=route_attestations,
    )
    unit_reports: list[dict[str, Any]] = []
    for unit_id in scope.get("passage_units") or []:
        selection = select_scope_claims(
            scope=scope,
            passage_unit_ids=[str(unit_id)],
            claim_relations=claim_relations,
            viewpoint_claim_links=viewpoint_claim_links,
            argument_routes=argument_routes,
            route_attestations=route_attestations,
        )
        additions = [
            {
                **row,
                "source_id": claim_index[row["claim_id"]].get("source_id"),
                "statement": claim_index[row["claim_id"]].get("statement"),
                "scripture_refs": claim_index[row["claim_id"]].get("scripture_refs")
                or [],
            }
            for row in [
                *selection["dependency_additions"],
                *selection["route_additions"],
            ]
        ]
        unit_reports.append(
            {
                "passage_unit_id": str(unit_id),
                "seed_claim_count": len(selection["seed_claim_ids"]),
                "dependency_addition_count": len(
                    selection["dependency_additions"]
                ),
                "route_addition_count": len(selection["route_additions"]),
                "selected_claim_count": len(selection["selected_claim_ids"]),
                "orphan_context_claim_count": len(
                    selection["orphan_context_claim_ids"]
                ),
                "seed_claim_ids": selection["seed_claim_ids"],
                "additions": sorted(additions, key=lambda row: row["claim_id"]),
                "orphan_context_claim_ids": selection["orphan_context_claim_ids"],
                "dangling_dependencies": selection["dangling_dependencies"],
            }
        )
    payload = {
        "schema_version": REPORT_VERSION,
        "scope_artifact_sha256": scope_sha,
        "master_data_snapshot_sha256": sha256_json(master_snapshot),
        "passage_units": unit_reports,
        "statistics": {
            "passage_unit_count": len(unit_reports),
            "scope_claim_count": len(claim_index),
            "seed_assignment_count": sum(
                row["seed_claim_count"] for row in unit_reports
            ),
            "dependency_addition_count": sum(
                row["dependency_addition_count"] for row in unit_reports
            ),
            "route_addition_count": sum(
                row["route_addition_count"] for row in unit_reports
            ),
            "dangling_dependency_count": sum(
                len(row["dangling_dependencies"]) for row in unit_reports
            ),
        },
        "model_calls_executed": 0,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    payload["artifact_sha256"] = sha256_json(payload)
    return payload


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    store = PostgresKnowledgeStore(args.database_url)
    report = build_scope_coverage_audit(
        scope=_read(args.scope),
        claim_relations=store.list_records("claim_relations"),
        viewpoint_claim_links=store.list_records("viewpoint_claim_links"),
        argument_routes=store.list_records("argument_routes"),
        route_attestations=store.list_records("argument_route_attestations"),
    )
    _write_immutable(args.output, report)
    print(
        json.dumps(
            {
                **report["statistics"],
                "artifact_sha256": report["artifact_sha256"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
