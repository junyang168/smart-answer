"""Execute a relation correction a person decided.

Retyping and retiring an edge have one answer, so `viewpoint_graph_backreview`
does them from the review's own verdict.  Reversing one does not: swapping the
ends re-decides how two viewpoints stand to each other, and the type that
follows is a judgment the review itself declines to settle -- it recommended
"`qualifies` 或 `specializes`" and left the choice open.

So the decision arrives here as input, not as inference.  This runner only
executes it: retire the edge that asserted the wrong direction, write the
corrected one, and record which review found the fault.

The id is derived from the edge's own content, so a reversed edge cannot keep
the old id without the id contradicting what the record says.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.knowledge_models import (
    ViewpointGraphReviewProvenance,
    ViewpointRelationRecord,
)
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import (
    PROJECT_ROOT,
    write_derived as _write_derived,
    write_immutable as _write_immutable,
)

CORRECTION_POLICY_VERSION = "viewpoint_relation_correction_v1"


def build_correction(
    *,
    original: dict[str, Any],
    relation_type: str,
    reason: str,
    review_artifact_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The retired original and the corrected edge that replaces it.

    Reversal is the only shape here: a correction that keeps the direction is a
    retype, which the back-review already performs on its own verdict.
    """

    seed = {
        "policy_version": CORRECTION_POLICY_VERSION,
        "source": original["validated_target_viewpoint_revision_id"],
        "target": original["validated_source_viewpoint_revision_id"],
        "relation_type": relation_type,
    }
    provenance = ViewpointGraphReviewProvenance(
        review_artifact_sha256=review_artifact_sha256
    ).model_dump(mode="json")
    corrected = ViewpointRelationRecord(
        viewpoint_relation_id=f"VREL-{sha256_json(seed)[:20]}",
        source_viewpoint_id=str(original["target_viewpoint_id"]),
        target_viewpoint_id=str(original["source_viewpoint_id"]),
        validated_source_viewpoint_revision_id=str(
            original["validated_target_viewpoint_revision_id"]
        ),
        validated_target_viewpoint_revision_id=str(
            original["validated_source_viewpoint_revision_id"]
        ),
        relation_type=relation_type,
        reason=reason,
        effective_state="active",
        review_status="human_approved",
        review_provenance=provenance,
    ).model_dump(mode="json")
    retired = {**original, "effective_state": "retired", "review_provenance": provenance}
    return retired, corrected


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relation-id", required=True)
    parser.add_argument(
        "--relation-type",
        required=True,
        help="the type the corrected edge carries; the reviewer left this open",
    )
    parser.add_argument("--reason", required=True, help="why, in the decider's words")
    parser.add_argument(
        "--review-artifact-sha256",
        required=True,
        help="the back-review that found the fault",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    store = PostgresKnowledgeStore(args.database_url)
    original = store.get_record("viewpoint_relations", args.relation_id)
    if original is None:
        raise SystemExit(f"no such relation: {args.relation_id}")
    if original.get("effective_state") != "active":
        raise SystemExit(f"{args.relation_id} is not active; nothing to correct")

    retired, corrected = build_correction(
        original=original,
        relation_type=args.relation_type,
        reason=args.reason,
        review_artifact_sha256=args.review_artifact_sha256,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"REL-CORRECTION-{corrected['viewpoint_relation_id'][5:25]}",
        "viewpoint_relations": [retired, corrected],
    }
    _write_immutable(args.output_dir / "correction-package.json", package)

    plan = store.plan_package(package, source_kind="viewpoint_relation_correction")
    plan_document = plan.as_dict()
    plan_document["schema_version"] = "wang_viewpoint_relation_correction_plan_v1"
    plan_document["apply_allowed"] = bool(args.apply)
    plan_document["artifact_sha256"] = sha256_json(plan_document)
    _write_derived(args.output_dir / "correction-change-plan.json", plan_document)

    mutations = 0
    if args.apply:
        result = store.apply_plan(plan)
        if result.get("status") == "applied":
            mutations = len(plan.operations)
        observed = {
            str(item["viewpoint_relation_id"]): item
            for item in store.list_records("viewpoint_relations")
        }
        if observed.get(args.relation_id, {}).get("effective_state") != "retired":
            raise SystemExit(f"readback failed: {args.relation_id} is still active")
        if corrected["viewpoint_relation_id"] not in observed:
            raise SystemExit("readback failed: the corrected relation is absent")

    print(
        json.dumps(
            {
                "schema_version": "wang_viewpoint_relation_correction_result_v1",
                "retired": args.relation_id,
                "corrected": corrected["viewpoint_relation_id"],
                "direction": (
                    f"{corrected['validated_source_viewpoint_revision_id']} "
                    f"--{args.relation_type}--> "
                    f"{corrected['validated_target_viewpoint_revision_id']}"
                ),
                "master_data_mutations": mutations,
                "apply_allowed": bool(args.apply),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
