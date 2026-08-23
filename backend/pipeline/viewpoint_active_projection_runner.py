"""Compile an immutable downstream projection from active viewpoint master data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_runtime_projection import (
    ViewpointRuntimeCompiler,
)


PROJECTION_COLLECTIONS = (
    "source_documents",
    "source_fragments",
    "claims",
    "evidence_steps",
    "claim_relations",
    "canonical_viewpoints",
    "viewpoint_revisions",
    "viewpoint_claim_links",
    "viewpoint_proposition_units",
    "viewpoint_proposition_unit_links",
    "argument_routes",
    "argument_route_revisions",
    "argument_route_attestations",
    "viewpoint_relations",
    "viewpoint_coverage_snapshots",
    "viewpoint_resolution_ledgers",
    "viewpoint_quality_reports",
    "viewpoint_atomic_coverage_snapshots",
    "viewpoint_atomic_resolution_ledgers",
    "viewpoint_atomic_quality_reports",
    "viewpoint_automated_promotion_decisions",
    "knowledge_routes",
    "product_dependencies",
    "impact_events",
)


def _write_immutable(path: Path, payload: dict) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    partial.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-snapshot-id", required=True)
    parser.add_argument("--viewpoint-id", action="append", required=True)
    parser.add_argument(
        "--consumer-kind",
        choices=("registry_review", "composition_plan", "qa_answer", "search_card"),
        default="composition_plan",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    store = PostgresKnowledgeStore()
    records = {
        name: [
            KNOWLEDGE_COLLECTIONS[name][0].model_validate(item)
            for item in store.list_records(name)
        ]
        for name in PROJECTION_COLLECTIONS
    }
    projection = ViewpointRuntimeCompiler(records).compile_projection(
        consumer_kind=args.consumer_kind,
        coverage_snapshot_id=args.coverage_snapshot_id,
        viewpoint_ids=args.viewpoint_id,
    )
    _write_immutable(args.output, projection.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "consumer_kind": projection.consumer_kind,
                "eligibility": projection.eligibility,
                "blocker_codes": projection.blocker_codes,
                "viewpoint_count": len(projection.viewpoints),
                "member_proposition_unit_count": sum(
                    len(item.get("member_proposition_units") or [])
                    for item in projection.viewpoints
                ),
                "dependency_count": len(projection.dependency_manifest),
                "projection_sha256": projection.projection_sha256,
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
