"""CLI for the PostgreSQL shared-knowledge authoring store."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.config.wang_platform_paths import wang_platform_paths
from backend.api.canonical_repository.postgres_store import (
    ActiveSnapshotBlocked,
    PostgresKnowledgeStore,
    canonical_json,
)
from backend.pipeline.run_ledger import run_record
from backend.pipeline.source_keys import document_row_key as _document_key
from backend.pipeline.source_anchor_binding import bind_source_versions
from backend.pipeline.reviewed_relation_integration import (
    build_reviewed_relation_integration,
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _package_subject(package: dict[str, Any], package_path: Path) -> str:
    """Which source this package is about.

    A merged or research-batch package covers several sermons, so it has no
    single subject; it is filed under its own package id and reaches the sermon
    rows through `source_ids` instead.
    """

    documents = package.get("source_documents") or []
    if len(documents) == 1:
        key = _document_key(documents[0])
        if key:
            return key
    return str(package.get("package_id") or package_path.stem)


def main() -> None:
    load_dotenv()
    platform_paths = wang_platform_paths()
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
    relations.add_argument(
        "--base-package",
        type=Path,
        help="Knowledge package containing every claim/evidence endpoint",
    )
    relations.add_argument(
        "--output-dir",
        type=Path,
        help="Write the increment, candidate snapshot, report, and human queue",
    )
    relations.add_argument("--apply", action="store_true")

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("output", type=Path)
    compile_parser.add_argument("--package-id")

    # A CompositionPlan lives in the store, but the composition review reads
    # its plan from a file. These two move one plan out and the reviewed
    # result back in, so "the argument layer changed, rebuild the plan" is a
    # chain that runs end to end instead of stopping at a format mismatch.
    export_plan = subparsers.add_parser(
        "export-plan", help="Write one CompositionPlan, decisions inlined, to a file."
    )
    export_plan.add_argument("plan_id")
    export_plan.add_argument("output", type=Path)

    ingest_plan = subparsers.add_parser(
        "ingest-plan",
        help="Ingest a reviewed CompositionPlan document, wrapping it as a package.",
    )
    ingest_plan.add_argument("plan", type=Path)
    ingest_plan.add_argument("--source-kind", default="reviewed_composition_plan")
    ingest_plan.add_argument("--apply", action="store_true")

    active_parser = subparsers.add_parser("compile-active")
    active_parser.add_argument(
        "output_root",
        nargs="?",
        type=Path,
        default=platform_paths.active_snapshots,
    )

    review_parser = subparsers.add_parser("sync-review-state")
    review_parser.add_argument(
        "review_state",
        nargs="?",
        type=Path,
        default=platform_paths.claim_layer_staging / "review_state.json",
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
        # Only an --apply writes to the store; a preview changes nothing and
        # does not belong in a table of work that happened.
        if not args.apply:
            result = store.ingest_package(
                package, source_kind=args.source_kind, apply=False,
                metadata={"input_path": str(args.package)},
            )
        else:
            with run_record(
                subject=_package_subject(package, args.package), stage="ingest"
            ) as record:
                record.inputs({"package_sha256": _sha256_file(args.package)})
                result = store.ingest_package(
                    package,
                    source_kind=args.source_kind,
                    apply=args.apply,
                    metadata={"input_path": str(args.package)},
                )
                # `already_applied` is a real outcome, not a no-op worth hiding:
                # it is the evidence that re-running an ingest is safe, and the
                # overview shows it as "no change" rather than as a fresh write.
                record.quality({
                    "status": result.get("status"),
                    **{k: v for k, v in (result.get("summary") or {}).items()},
                })
                record.metadata({"change_set_id": result.get("change_set_id")})
                # A merged package covers several sermons at once. Declaring
                # them all is what puts this one ingest on every one of their
                # rows in the overview.
                record.sources(
                    key
                    for key in (
                        _document_key(doc)
                        for doc in (package.get("source_documents") or [])
                    )
                    if key
                )
                record.outputs(args.package)
    elif args.command == "ingest-reviewed-relations":
        artifact = _load(args.artifact)
        base = _load(args.base_package) if args.base_package else store.compile_package()
        integration = build_reviewed_relation_integration(artifact, base)
        if args.output_dir:
            _write(args.output_dir / "incremental-package.json", integration["incremental_package"])
            _write(args.output_dir / "candidate-shared-knowledge.json", integration["candidate_snapshot"])
            _write(args.output_dir / "human-review-queue.json", {
                "schema_version": "wang_cross_sermon_human_queue_v1",
                "items": integration["human_review_queue"],
            })
            report = {key: value for key, value in integration.items() if key not in {
                "incremental_package", "candidate_snapshot", "human_review_queue"
            }}
            _write(args.output_dir / "integration-report.json", report)
        if integration["status"] == "blocked":
            result = {
                "status": "blocked",
                "summary": integration["summary"],
                "findings": integration["findings"],
            }
        else:
            base_result = None
            if args.apply and args.base_package:
                base_result = store.ingest_package(
                    base,
                    source_kind="research_batch_knowledge",
                    apply=True,
                    metadata={"input_path": str(args.base_package)},
                )
            relation_result = store.ingest_package(
                integration["incremental_package"],
                source_kind="cross_sermon_relation_consensus",
                apply=args.apply,
                metadata={"input_path": str(args.artifact)},
            )
            result = {
                "status": relation_result["status"],
                "integration_status": integration["status"],
                "summary": integration["summary"],
                "base_ingest": base_result,
                "relation_ingest": relation_result,
                "output_dir": str(args.output_dir) if args.output_dir else None,
            }
    elif args.command == "export-plan":
        plan = store.get_plan_document(args.plan_id)
        if plan is None:
            raise SystemExit(f"plan not found in authoring store: {args.plan_id}")
        _write(args.output, plan)
        result = {
            "status": "exported",
            "plan_id": args.plan_id,
            "revision": plan.get("revision"),
            "decisions": len(plan["decisions"]),
            "output": str(args.output),
        }
    elif args.command == "ingest-plan":
        plan = _load(args.plan)
        plan_id = plan.get("plan_id")
        if not plan_id:
            raise SystemExit(f"not a CompositionPlan document: {args.plan}")
        # The importer splits an inlined `product_plans` entry back into a plan
        # and its decisions, which is the exact shape `export-plan` writes.
        package = {
            "schema_version": "wang_shared_knowledge_v1.3",
            "package_id": f"PLAN-{plan_id}",
            "product_plans": [plan],
        }
        result = store.ingest_package(
            package,
            source_kind=args.source_kind,
            apply=args.apply,
            metadata={"input_path": str(args.plan)},
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
