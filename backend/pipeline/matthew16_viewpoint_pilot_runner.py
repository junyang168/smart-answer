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


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-catalog", type=Path, required=True)
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--source-selection", type=Path, required=True)
    parser.add_argument("--claim-manifest", type=Path, required=True)
    parser.add_argument("--article-dir", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url")
    args = parser.parse_args()
    store = PostgresKnowledgeStore(args.database_url)
    artifact = build_matthew16_pilot_scope(
        source_catalog=_read(args.source_catalog),
        source_catalog_sha256=file_sha256(args.source_catalog),
        source_map_sha256=file_sha256(args.source_map),
        source_selection=_read(args.source_selection),
        claim_manifest=_read(args.claim_manifest),
        source_documents=store.list_records("source_documents"),
        claims=store.list_records("claims"),
        article_dirs=args.article_dir,
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
