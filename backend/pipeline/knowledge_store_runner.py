"""CLI for the PostgreSQL shared-knowledge authoring store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    ActiveSnapshotBlocked,
    PostgresKnowledgeStore,
    canonical_json,
    reviewed_relations_package,
)
from backend.pipeline.source_anchor_binding import bind_source_versions


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Override KNOWLEDGE_DATABASE_URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate")
    subparsers.add_parser("status")

    ingest = subparsers.add_parser("ingest-package")
    ingest.add_argument("package", type=Path)
    ingest.add_argument("--source-kind", default="knowledge_package")
    ingest.add_argument("--apply", action="store_true")

    relations = subparsers.add_parser("ingest-reviewed-relations")
    relations.add_argument("artifact", type=Path)
    relations.add_argument("--apply", action="store_true")

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("output", type=Path)
    compile_parser.add_argument("--package-id")

    active_parser = subparsers.add_parser("compile-active")
    active_parser.add_argument(
        "output_root",
        nargs="?",
        type=Path,
        default=Path("output/claim-layer/compiled"),
    )

    review_parser = subparsers.add_parser("sync-review-state")
    review_parser.add_argument(
        "review_state",
        nargs="?",
        type=Path,
        default=Path("output/claim-layer/review_state.json"),
    )

    anchor_parser = subparsers.add_parser("bind-source-anchors")
    anchor_parser.add_argument(
        "transcript_root",
        nargs="?",
        type=Path,
        default=Path("/opt/homebrew/var/www/church/web/data/script_published"),
    )
    anchor_parser.add_argument("--apply", action="store_true")

    args = parser.parse_args()
    store = PostgresKnowledgeStore(args.database_url)
    if args.command == "migrate":
        result: Any = {"applied": store.migrate()}
    elif args.command == "status":
        result = store.status()
    elif args.command == "ingest-package":
        package = _load(args.package)
        result = store.ingest_package(
            package,
            source_kind=args.source_kind,
            apply=args.apply,
            metadata={"input_path": str(args.package)},
        )
    elif args.command == "ingest-reviewed-relations":
        package = reviewed_relations_package(_load(args.artifact))
        result = store.ingest_package(
            package,
            source_kind="cross_sermon_relation_consensus",
            apply=args.apply,
            metadata={"input_path": str(args.artifact)},
        )
    elif args.command == "compile":
        package = store.compile_package(package_id=args.package_id)
        _write(args.output, package)
        result = {"status": "compiled", "output": str(args.output), "summary": package["summary"]}
    elif args.command == "sync-review-state":
        state = _load(args.review_state)
        collection_map = {
            "claims": "claims",
            "syntheses": "editorial_syntheses",
            "composition_decisions": "composition_decisions",
        }
        changed = []
        skipped = []
        for state_key, collection in collection_map.items():
            for object_id, review in (state.get(state_key) or {}).items():
                current = store.get_record(collection, object_id)
                if not current:
                    skipped.append({"collection": collection, "object_id": object_id, "reason": "missing"})
                    continue
                if (
                    current.get("review_status") == review.get("status")
                    and current.get("review_note", "") == review.get("note", "")
                ):
                    skipped.append({"collection": collection, "object_id": object_id, "reason": "unchanged"})
                    continue
                changed.append(
                    store.record_review(
                        collection,
                        object_id,
                        decision=review.get("status", "candidate"),
                        reason=review.get("note", ""),
                        reviewer_id=review.get("reviewer", "同工"),
                        reviewer_kind="human",
                    )
                )
        result = {"status": "synced", "changed": changed, "skipped": skipped}
    elif args.command == "bind-source-anchors":
        result = bind_source_versions(store, args.transcript_root, apply=args.apply)
    else:
        try:
            result = store.publish_active_snapshot(args.output_root)
        except ActiveSnapshotBlocked as exc:
            result = {"status": "blocked", "findings": exc.findings}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
