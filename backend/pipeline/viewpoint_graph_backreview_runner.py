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
    read_artifact as _read,
    subscription_client as _subscription_client,
    write_derived as _write_derived,
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


def unreviewed(
    records: list[dict[str, Any]], *, live: set[str] | None = None, id_key: str = ""
) -> list[dict[str, Any]]:
    """Live records with no review behind them -- `None` is how they say so.

    A retired relation or a superseded structure revision is not what anyone is
    reading, and approving one would attach a review to a record already out of
    service.
    """

    return [
        item
        for item in records
        if not item.get("review_provenance")
        and item.get("effective_state", "active") == "active"
        and (live is None or str(item[id_key]) in live)
    ]


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
    current_structure_revisions = {
        str(item["current_revision_id"]) for item in store.list_records("viewpoint_structures")
    }
    structures = unreviewed(
        store.list_records("viewpoint_structure_revisions"),
        live=current_structure_revisions,
        id_key="structure_revision_id",
    )
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

    raw_path = args.output_dir / "raw-backreview.json"
    raw, calls, seconds = _call(
        build_backreviewer(args.review_model, args.review_effort, provider=args.review_provider),
        packet,
        raw_path,
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

    # The artifact sha lives on the file `call_model` wrote, not on the response
    # it returned -- reading it off `raw` yields None and silently records a
    # hash of the response body instead, which points at nothing retrievable.
    # `viewpoint_batch_resolution_runner` reads it the same way.
    provenance = ViewpointGraphReviewProvenance(
        review_artifact_sha256=str(_read(raw_path)["artifact_sha256"]),
    ).model_dump(mode="json")

    # Attaching provenance goes through the ChangeSet like every other write:
    # planned, diffed against the store, applied atomically. Reaching for a
    # direct UPDATE is what a production repair looks like, not a review.
    approved_structures = set(report["approved_structure_revision_ids"])
    approved_relations = set(report["approved_viewpoint_relation_ids"])

    # A structure revision is an immutable snapshot -- `viewpoint_foundation`
    # refuses an in-place edit, and rightly: editing one rewrites what a past
    # decision recorded. So the review is attached by superseding it, the same
    # shape a reworded viewpoint uses. Relations are not in that set and take
    # the provenance directly.
    structures_out: list[dict[str, Any]] = []
    structure_pointer_updates: list[dict[str, Any]] = []
    structures_by_id = {str(item["structure_id"]): item for item in store.list_records("viewpoint_structures")}
    for item in structures:
        if str(item["structure_revision_id"]) not in approved_structures:
            continue
        superseded = str(item["structure_revision_id"])
        successor = dict(item)
        # A successor is a NEW store object: bookkeeping stays at store
        # revision 1 and generations are counted by the supersedes chain --
        # unified with the ruling runners after #326's live fire (old+1 here
        # left records whose stored revision could never equal the field).
        successor["revision_number"] = 1
        successor["revision"] = 1
        successor["supersedes_revision_id"] = superseded
        successor["review_provenance"] = provenance
        seed = {
            "structure_id": item["structure_id"],
            "supersedes": superseded,
            "review_artifact_sha256": provenance["review_artifact_sha256"],
        }
        successor["structure_revision_id"] = f"VSR-{sha256_json(seed)[:20]}"
        structures_out.append(successor)
        pointer = dict(structures_by_id[str(item["structure_id"])])
        pointer["current_revision_id"] = successor["structure_revision_id"]
        structure_pointer_updates.append(pointer)

    # A relation that did not pass is acted on, not left looking like one that
    # did. Retyping and retiring are corrections with one answer; a reversed
    # direction re-decides how two viewpoints stand to each other and stays for
    # a person.
    retype = {
        item["viewpoint_relation_id"]: item["relation_type"]
        for item in report["retyped_relations"]
    }
    retire = set(report["retired_relation_ids"])
    relations_out: list[dict[str, Any]] = []
    for item in relations:
        record_id = str(item["viewpoint_relation_id"])
        if record_id in approved_relations:
            relations_out.append({**item, "review_provenance": provenance})
        elif record_id in retype:
            relations_out.append(
                {**item, "relation_type": retype[record_id], "review_provenance": provenance}
            )
        elif record_id in retire:
            relations_out.append(
                {**item, "effective_state": "retired", "review_provenance": provenance}
            )

    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"GRAPH-BACKREVIEW-{report['artifact_sha256'][:20]}",
        "viewpoint_structures": structure_pointer_updates,
        "viewpoint_structure_revisions": structures_out,
        "viewpoint_relations": relations_out,
    }
    _write_immutable(args.output_dir / "backreview-change-package.json", package)
    plan = store.plan_package(package, source_kind="viewpoint_graph_backreview")
    plan_document = plan.as_dict()
    plan_document["schema_version"] = "wang_viewpoint_graph_backreview_plan_v1"
    plan_document["apply_allowed"] = bool(args.apply)
    plan_document["artifact_sha256"] = sha256_json(plan_document)
    # Derived, not immutable: the documented flow is a plan-only run followed by
    # a `--apply` rerun off the cached call, and `apply_allowed` differs between
    # the two. Written immutably the second run dies here, after the model call
    # has already been paid for.
    _write_derived(args.output_dir / "backreview-change-plan.json", plan_document)

    mutations = 0
    if args.apply:
        # `apply_plan` returns `already_applied` without touching anything when
        # a change set with the same fingerprint already landed; counting the
        # operations regardless reports mutations for a run that made none.
        apply_result = store.apply_plan(plan)
        if apply_result.get("status") == "applied":
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
        # Structures are verified at their successor id, which is what the
        # structure now points at; the superseded revision keeps its own state.
        expected = (
            {str(item["structure_revision_id"]) for item in structures_out}
            | approved_relations
            | set(retype)
            | retire
        )
        unverified = sorted(
            record_id
            for record_id in expected
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
        "retyped_relations": report["retyped_relations"],
        "retired_relations": report["retired_relation_ids"],
        "needs_human_relations": report["needs_human_relation_ids"],
        "model_calls": calls,
        "wall_seconds": round(seconds, 3),
        "master_data_mutations": mutations,
        "apply_allowed": bool(args.apply),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
