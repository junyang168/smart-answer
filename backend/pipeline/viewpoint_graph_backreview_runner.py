"""Review the structures and relations that reached the Registry unreviewed.

Sixteen of them were written `system_approved` before the review contract had a
place for them.  They belong to no pending batch, so there is no proposal left
to review and re-running their original batches would re-derive everything
against today's prompts and write the drift as a second set of records.

So the committed records are the input.  Only a record the reviewer passed, and
whose structured question came back true, gets a review provenance; the rest
stay exactly as they are, which is the honest state for a record nobody has
been able to approve.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.knowledge_models import (
    ViewpointGraphReviewProvenance,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_graph_backreview import (
    ViewpointGraphBackReviewResponse,
    build_backreview_packet,
    validate_backreview,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    StructuredJsonReviewerAdapter,
)
from backend.pipeline.viewpoint_resolution_runtime import (
    PROJECT_ROOT,
    PROMPT_DIR,
    call_model as _call,
    subscription_client as _subscription_client,
    write_immutable as _write_immutable,
)


def build_backreviewer(
    model: str, reasoning_effort: str, *, provider: str = "claude"
) -> StructuredJsonReviewerAdapter:
    return StructuredJsonReviewerAdapter(
        client=_subscription_client(provider, model, reasoning_effort),
        prompt=(PROMPT_DIR / "viewpoint_graph_backreview.md").read_text(encoding="utf-8"),
        response_model=ViewpointGraphBackReviewResponse,
        schema_name="wang_viewpoint_graph_backreview_v1",
    )


def unreviewed(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records with no review behind them -- `None` is how they say so."""

    return [item for item in records if not item.get("review_provenance")]


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--review-provider", choices=("claude", "codex"), default="claude")
    parser.add_argument("--review-model", default="claude-opus-5")
    parser.add_argument("--review-effort", choices=("high", "xhigh"), default="high")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="attach provenance to the records the review passed; default is plan-only",
    )
    args = parser.parse_args()

    store = PostgresKnowledgeStore(args.database_url)
    structures = unreviewed(store.list_records("viewpoint_structure_revisions"))
    relations = unreviewed(store.list_records("viewpoint_relations"))
    if not structures and not relations:
        print(json.dumps({"status": "nothing_unreviewed"}, ensure_ascii=False))
        return 0

    packet = build_backreview_packet(
        structure_revisions=structures,
        relations=relations,
        viewpoint_revisions=store.list_records("viewpoint_revisions"),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_immutable(args.output_dir / "backreview-packet.json", packet)

    raw, calls, seconds = _call(
        build_backreviewer(args.review_model, args.review_effort, provider=args.review_provider),
        packet,
        args.output_dir / "raw-backreview.json",
    )
    backreview = ViewpointGraphBackReviewResponse.model_validate(raw)
    report = validate_backreview(backreview=backreview, packet=packet)

    _write_immutable(
        args.output_dir / "backreview.json",
        {
            "schema_version": "wang_viewpoint_graph_backreview_envelope_v1",
            "packet_sha256": packet["packet_sha256"],
            "backreview": backreview.model_dump(mode="json"),
            "validation_report": report,
        },
    )

    provenance = ViewpointGraphReviewProvenance(
        review_artifact_sha256=str(raw.get("artifact_sha256") or sha256_json(dict(raw))),
    ).model_dump(mode="json")

    # Attaching provenance goes through the ChangeSet like every other write:
    # planned, diffed against the store, applied atomically. Reaching for a
    # direct UPDATE is what a production repair looks like, not a review.
    approved_structures = set(report["approved_structure_revision_ids"])
    approved_relations = set(report["approved_viewpoint_relation_ids"])
    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"GRAPH-BACKREVIEW-{report['artifact_sha256'][:20]}",
        "viewpoint_structure_revisions": [
            {**item, "review_provenance": provenance}
            for item in structures
            if str(item["structure_revision_id"]) in approved_structures
        ],
        "viewpoint_relations": [
            {**item, "review_provenance": provenance}
            for item in relations
            if str(item["viewpoint_relation_id"]) in approved_relations
        ],
    }
    _write_immutable(args.output_dir / "backreview-change-package.json", package)
    plan = store.plan_package(package, source_kind="viewpoint_graph_backreview")
    plan_document = plan.as_dict()
    plan_document["schema_version"] = "wang_viewpoint_graph_backreview_plan_v1"
    plan_document["apply_allowed"] = bool(args.apply)
    plan_document["artifact_sha256"] = sha256_json(plan_document)
    _write_immutable(args.output_dir / "backreview-change-plan.json", plan_document)

    mutations = 0
    if args.apply:
        store.apply_plan(plan)
        mutations = len(plan.operations)
        observed = {
            **{
                str(item["structure_revision_id"]): item
                for item in store.list_records("viewpoint_structure_revisions")
            },
            **{
                str(item["viewpoint_relation_id"]): item
                for item in store.list_records("viewpoint_relations")
            },
        }
        unverified = sorted(
            record_id
            for record_id in approved_structures | approved_relations
            if not (observed.get(record_id) or {}).get("review_provenance")
        )
        if unverified:
            raise SystemExit(
                "readback failed; provenance is absent after apply: " + ", ".join(unverified)
            )

    result = {
        "schema_version": "wang_viewpoint_graph_backreview_result_v1",
        "structure_count": report["structure_count"],
        "relation_count": report["relation_count"],
        "approved_structures": len(report["approved_structure_revision_ids"]),
        "approved_relations": len(report["approved_viewpoint_relation_ids"]),
        "held_structures": report["held_structure_revision_ids"],
        "held_relations": report["held_viewpoint_relation_ids"],
        "model_calls": calls,
        "wall_seconds": round(seconds, 3),
        "master_data_mutations": mutations,
        "apply_allowed": bool(args.apply),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
