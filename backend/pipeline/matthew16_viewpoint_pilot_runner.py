"""Compile the zero-call Matthew 16 CanonicalViewpoint pilot scope."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.api.canonical_repository.matthew16_viewpoint_pilot import (
    build_matthew16_pilot_scope,
    file_sha256,
)
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.occurrence_section_projection import (
    projection_admissions_by_claim,
    verify_projection_artifact,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-catalog", type=Path, required=True)
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--source-selection", type=Path, required=True)
    parser.add_argument("--claim-manifest", type=Path, required=True)
    parser.add_argument("--occurrence-projection", type=Path, required=True)
    parser.add_argument("--article-dir", type=Path, action="append", default=[])
    parser.add_argument("--thematic-source-id", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url")
    args = parser.parse_args()
    store = PostgresKnowledgeStore(args.database_url)
    occurrence = _read(args.occurrence_projection) if args.occurrence_projection else None
    if occurrence is not None:
        verify_projection_artifact(occurrence)
    claim_manifest = _read(args.claim_manifest)
    unsigned_manifest = dict(claim_manifest)
    supplied_manifest_sha = str(unsigned_manifest.pop("manifest_sha256", ""))
    if not supplied_manifest_sha or supplied_manifest_sha != sha256_json(
        unsigned_manifest
    ):
        raise ValueError("Claim manifest SHA mismatch")
    if occurrence is not None:
        current_manifest = dict(occurrence["current_claim_manifest"])
        if (
            current_manifest.get("parent_claim_manifest_sha256")
            != supplied_manifest_sha
        ):
            raise ValueError("occurrence projection parent Claim manifest mismatch")
        claim_manifest = current_manifest
    artifact = build_matthew16_pilot_scope(
        source_catalog=_read(args.source_catalog),
        source_catalog_sha256=file_sha256(args.source_catalog),
        source_map_sha256=file_sha256(args.source_map),
        source_selection=_read(args.source_selection),
        claim_manifest=claim_manifest,
        source_documents=store.list_records("source_documents"),
        claims=store.list_records("claims"),
        claim_relations=store.list_records("claim_relations"),
        viewpoint_claim_links=store.list_records("viewpoint_claim_links"),
        argument_routes=store.list_records("argument_routes"),
        argument_route_revisions=store.list_records("argument_route_revisions"),
        argument_route_attestations=store.list_records("argument_route_attestations"),
        occurrence_admissions_by_claim=(
            projection_admissions_by_claim(occurrence)
            if occurrence is not None
            else None
        ),
        occurrence_projection_sha256=(
            str(occurrence["artifact_sha256"]) if occurrence is not None else None
        ),
        occurrence_projection_claim_universe_sha256=(
            str(occurrence["claim_universe_sha256"])
            if occurrence is not None
            else None
        ),
        article_dirs=args.article_dir,
        thematic_source_ids=args.thematic_source_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable pilot scope {args.output}")
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(
        json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(artifact.statistics | {"artifact_sha256": artifact.artifact_sha256}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
