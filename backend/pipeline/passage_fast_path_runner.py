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
    expected_sermon_ids: set[str] | None = None,
) -> dict[str, Any]:
    expected_sermon_ids = expected_sermon_ids or set()

    def has_expected_media(slice_package: dict[str, Any]) -> bool:
        if not expected_sermon_ids:
            return True
        sermon_by_source_id = {
            str(row.get("source_id")): str(row.get("transcript_id"))
            for row in slice_package.get("source_documents", [])
            if str(row.get("transcript_id")) in expected_sermon_ids
            and row.get("source_type") == "sermon_transcript"
        }
        sermon_by_timed_fragment_id = {
            str(row.get("fragment_id")): sermon_by_source_id[str(row.get("source_id"))]
            for row in slice_package.get("source_fragments", [])
            if str(row.get("source_id")) in sermon_by_source_id
            and isinstance(row.get("media_time"), (int, float))
            and isinstance(row.get("media_end_time"), (int, float))
            and row["media_end_time"] > row["media_time"]
        }
        claim_ids = {str(row.get("claim_id")) for row in slice_package.get("claims", [])}
        covered_sermons: set[str] = set()
        for row in slice_package.get("evidence_steps", []):
            if not claim_ids & set(map(str, row.get("produced_claim_ids") or [])):
                continue
            covered_sermons.update(
                sermon_by_timed_fragment_id[fragment_id]
                for fragment_id in map(str, row.get("source_fragment_ids") or [])
                if fragment_id in sermon_by_timed_fragment_id
            )
        return expected_sermon_ids <= covered_sermons

    database_slice = build_passage_slice(database_package, passage)
    if not database_slice["passage_slice"]["requires_model_extraction"]:
        if not has_expected_media(database_slice):
            return {
                "resolution": "postgresql_reuse_media_projection_required",
                "requires_database_ingest": False,
                "requires_model_extraction": False,
                "requires_media_projection": True,
                "slice": database_slice,
            }
        return {
            "resolution": "postgresql_reuse",
            "requires_database_ingest": False,
            "requires_model_extraction": False,
            "requires_media_projection": False,
            "slice": database_slice,
        }
    if fallback_package is not None:
        fallback_slice = build_passage_slice(fallback_package, passage)
        if not fallback_slice["passage_slice"]["requires_model_extraction"]:
            fallback_has_media = has_expected_media(fallback_slice)
            return {
                "resolution": "reviewed_package_reuse",
                "requires_database_ingest": True,
                "requires_model_extraction": False,
                "requires_media_projection": not fallback_has_media,
                "slice": fallback_slice,
            }
    return {
        "resolution": "model_extraction_required",
        "requires_database_ingest": False,
        "requires_model_extraction": True,
        "requires_media_projection": bool(expected_sermon_ids),
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
    parser.add_argument("--expected-sermon", action="append", default=[])
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
    result = resolve_fast_path(
        store.compile_package(), passage, fallback, set(args.expected_sermon)
    )
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
                "requires_media_projection": result["requires_media_projection"],
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
