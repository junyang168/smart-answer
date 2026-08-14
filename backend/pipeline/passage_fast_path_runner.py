"""Resolve one passage from PostgreSQL, reusing reviewed packages before any model work."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    database_url_from_env,
)
from backend.pipeline.passage_knowledge_slice import Passage, build_passage_slice


def resolve_fast_path(
    database_package: dict[str, Any],
    passage: Passage,
    fallback_package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    database_slice = build_passage_slice(database_package, passage)
    if not database_slice["passage_slice"]["requires_model_extraction"]:
        return {
            "resolution": "postgresql_reuse",
            "requires_database_ingest": False,
            "requires_model_extraction": False,
            "slice": database_slice,
        }
    if fallback_package is not None:
        fallback_slice = build_passage_slice(fallback_package, passage)
        if not fallback_slice["passage_slice"]["requires_model_extraction"]:
            return {
                "resolution": "reviewed_package_reuse",
                "requires_database_ingest": True,
                "requires_model_extraction": False,
                "slice": fallback_slice,
            }
    return {
        "resolution": "model_extraction_required",
        "requires_database_ingest": False,
        "requires_model_extraction": True,
        "slice": database_slice,
    }


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default="Matt")
    parser.add_argument("--chapter", required=True, type=int)
    parser.add_argument("--start", required=True, type=int)
    parser.add_argument("--end", required=True, type=int)
    parser.add_argument("--fallback-package", type=Path)
    parser.add_argument("--apply-fallback", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--database-url")
    args = parser.parse_args()

    store = PostgresKnowledgeStore(database_url_from_env(args.database_url))
    passage = Passage(args.book, args.chapter, args.start, args.end)
    fallback = (
        json.loads(args.fallback_package.read_text(encoding="utf-8"))
        if args.fallback_package
        else None
    )
    result = resolve_fast_path(store.compile_package(), passage, fallback)
    ingest = None
    if result["requires_database_ingest"] and args.apply_fallback:
        ingest = store.ingest_package(
            result["slice"],
            source_kind="passage_fast_path",
            apply=True,
            metadata={"passage": passage.display},
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result["slice"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "passage": passage.display,
                "resolution": result["resolution"],
                "requires_database_ingest": result["requires_database_ingest"],
                "requires_model_extraction": result["requires_model_extraction"],
                "summary": result["slice"]["summary"],
                "coverage": result["slice"]["passage_slice"],
                "ingest_status": (ingest or {}).get("status"),
                "output": str(args.output) if args.output else None,
            },
            ensure_ascii=False,
        )
    )
    return 2 if result["requires_model_extraction"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
