"""Execute edge additions the owner ruled on.

#231 set the pattern: a judgment a review cannot settle arrives as input,
never as inference, and the machinery only executes it -- with provenance,
a ChangeSet, and readback. This runner extends the pattern from reversing
an existing edge to adding ones that were never recorded: the owner ruled
(2026-09-01, #232/#322) that two viewpoints stand in a relation the batch
flow never wrote, and that three Daniel-7 Claims are load-bearing premises
of the Son-of-Man argument whose extraction missed the supporting edges.

The ruling file is the authority. It carries the decided edges, the owner's
words, and the issue-comment URLs where the decision was recorded; its SHA
becomes the review provenance the written records point back to. The runner
refuses whatever the ruling does not license: unknown endpoints, relation
types outside the argument-dependency allowlist, cross-source claim edges,
duplicate active edges, or a viewpoint revision that is no longer current
(a ruling made against wording that has since moved must return to the
owner, not silently follow the head).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.knowledge_models import (
    ViewpointGraphReviewProvenance,
    ViewpointRelationRecord,
)
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_scope_selection import (
    ARGUMENT_DEPENDENCY_RELATION_TYPES,
)
from backend.pipeline.viewpoint_resolution_runtime import (
    PROJECT_ROOT,
    write_derived as _write_derived,
    write_immutable as _write_immutable,
)

RULING_POLICY_VERSION = "viewpoint_ruling_edge_v1"
RULING_SCHEMA_VERSION = "wang_owner_edge_ruling_v1"


class RulingExecutionError(ValueError):
    pass


def _claim_source_token(claim_id: str) -> str:
    return claim_id.split("-CL", 1)[0].split("-P", 1)[0]


def _validated_ruling(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    stated = str(payload.get("artifact_sha256") or "")
    if payload.get("schema_version") != RULING_SCHEMA_VERSION:
        raise RulingExecutionError("ruling file declares another schema")
    if not stated or stated != sha256_json(body):
        raise RulingExecutionError("ruling file SHA mismatch")
    for field in ("decided_by", "decided_at", "recorded_at_urls"):
        if not body.get(field):
            raise RulingExecutionError(f"ruling file lacks {field}")
    return dict(body) | {"artifact_sha256": stated}


def build_claim_relation_additions(
    ruling: Mapping[str, Any],
    *,
    claims_by_id: Mapping[str, Mapping[str, Any]],
    active_relations: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    existing = {
        (str(row.get("from_id")), str(row.get("to_id")), str(row.get("relation_type")))
        for row in active_relations
    }
    additions: list[dict[str, Any]] = []
    for edge in ruling.get("claim_relation_additions") or []:
        from_id = str(edge["from_claim_id"])
        to_id = str(edge["to_claim_id"])
        relation_type = str(edge["relation_type"])
        reason = str(edge.get("reason") or "")
        if relation_type not in ARGUMENT_DEPENDENCY_RELATION_TYPES:
            raise RulingExecutionError(
                f"{from_id}->{to_id}: {relation_type} is not an argument dependency"
            )
        for claim_id in (from_id, to_id):
            if claim_id not in claims_by_id:
                raise RulingExecutionError(f"{claim_id}: no such Claim")
        # Claim records carry no source_id field; the source is encoded in the
        # id itself (DK-<source>-P..-CL..), the same convention the batch
        # machinery reads. Ids that do not encode one fail closed.
        if _claim_source_token(from_id) != _claim_source_token(to_id):
            raise RulingExecutionError(
                f"{from_id}->{to_id}: claim relations are source-local; "
                "cross-source membership travels by ArgumentRoute"
            )
        if (from_id, to_id, relation_type) in existing:
            raise RulingExecutionError(
                f"{from_id}->{to_id}: an active {relation_type} edge already exists"
            )
        if not reason:
            raise RulingExecutionError(f"{from_id}->{to_id}: the ruling must say why")
        seed = {
            "policy_version": RULING_POLICY_VERSION,
            "from_id": from_id,
            "to_id": to_id,
            "relation_type": relation_type,
        }
        additions.append(
            {
                "claim_relation_id": f"CR-{sha256_json(seed)[:20]}",
                "schema_version": 1,
                "from_id": from_id,
                "to_id": to_id,
                "relation_type": relation_type,
                "reason": reason,
                "review_status": "human_approved",
                "visibility": "internal",
            }
        )
    return additions


def build_viewpoint_relation_additions(
    ruling: Mapping[str, Any],
    *,
    viewpoints_by_id: Mapping[str, Mapping[str, Any]],
    revisions_by_id: Mapping[str, Mapping[str, Any]],
    active_relations: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    existing = {
        (
            str(row.get("source_viewpoint_id")),
            str(row.get("target_viewpoint_id")),
            str(row.get("relation_type")),
        )
        for row in active_relations
        if str(row.get("effective_state") or "active") == "active"
    }
    provenance = ViewpointGraphReviewProvenance(
        review_artifact_sha256=str(ruling["artifact_sha256"])
    ).model_dump(mode="json")
    additions: list[dict[str, Any]] = []
    for edge in ruling.get("viewpoint_relation_additions") or []:
        source_revision = str(edge["source_viewpoint_revision_id"])
        target_revision = str(edge["target_viewpoint_revision_id"])
        relation_type = str(edge["relation_type"])
        reason = str(edge.get("reason") or "")
        endpoints: dict[str, str] = {}
        for label, revision_id in (
            ("source", source_revision),
            ("target", target_revision),
        ):
            revision = revisions_by_id.get(revision_id)
            if revision is None:
                raise RulingExecutionError(f"{revision_id}: no such viewpoint revision")
            viewpoint_id = str(revision.get("viewpoint_id") or "")
            viewpoint = viewpoints_by_id.get(viewpoint_id)
            if viewpoint is None:
                raise RulingExecutionError(f"{viewpoint_id}: no such viewpoint")
            if str(viewpoint.get("current_revision_id")) != revision_id:
                raise RulingExecutionError(
                    f"{revision_id}: no longer the current revision of {viewpoint_id}; "
                    "the ruling must return to the owner against the new wording"
                )
            endpoints[label] = viewpoint_id
        if (endpoints["source"], endpoints["target"], relation_type) in existing:
            raise RulingExecutionError(
                f"{endpoints['source']}->{endpoints['target']}: "
                f"an active {relation_type} edge already exists"
            )
        seed = {
            "policy_version": RULING_POLICY_VERSION,
            "source": source_revision,
            "target": target_revision,
            "relation_type": relation_type,
        }
        additions.append(
            ViewpointRelationRecord(
                viewpoint_relation_id=f"VREL-{sha256_json(seed)[:20]}",
                source_viewpoint_id=endpoints["source"],
                target_viewpoint_id=endpoints["target"],
                validated_source_viewpoint_revision_id=source_revision,
                validated_target_viewpoint_revision_id=target_revision,
                relation_type=relation_type,  # type: ignore[arg-type]
                reason=reason,
                effective_state="active",
                review_status="human_approved",
                review_provenance=provenance,
            ).model_dump(mode="json")
        )
    return additions


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ruling", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    ruling = _validated_ruling(json.loads(args.ruling.read_text(encoding="utf-8")))
    store = PostgresKnowledgeStore(args.database_url)
    claim_rows = {
        str(row["claim_id"]): row for row in store.list_records("claims")
    }
    claim_additions = build_claim_relation_additions(
        ruling,
        claims_by_id=claim_rows,
        active_relations=store.list_records("claim_relations"),
    )
    viewpoint_additions = build_viewpoint_relation_additions(
        ruling,
        viewpoints_by_id={
            str(row["viewpoint_id"]): row
            for row in store.list_records("canonical_viewpoints")
        },
        revisions_by_id={
            str(row["viewpoint_revision_id"]): row
            for row in store.list_records("viewpoint_revisions")
        },
        active_relations=store.list_records("viewpoint_relations"),
    )
    if not claim_additions and not viewpoint_additions:
        raise SystemExit("the ruling decides no edges")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    package: dict[str, Any] = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"RULING-EDGE-{ruling['artifact_sha256'][:20]}",
    }
    if claim_additions:
        package["claim_relations"] = claim_additions
    if viewpoint_additions:
        package["viewpoint_relations"] = viewpoint_additions
    _write_immutable(args.output_dir / "ruling-edge-package.json", package)

    plan = store.plan_package(package, source_kind="owner_ruling_edge_addition")
    plan_document = plan.as_dict()
    plan_document["schema_version"] = "wang_owner_ruling_edge_plan_v1"
    plan_document["ruling_artifact_sha256"] = ruling["artifact_sha256"]
    plan_document["apply_allowed"] = bool(args.apply)
    plan_document["artifact_sha256"] = sha256_json(plan_document)
    _write_derived(args.output_dir / "ruling-edge-plan.json", plan_document)

    mutations = 0
    if args.apply:
        result = store.apply_plan(plan)
        if result.get("status") == "applied":
            mutations = len(plan.operations)
        observed_claim = {
            str(item["claim_relation_id"]) for item in store.list_records("claim_relations")
        }
        observed_viewpoint = {
            str(item["viewpoint_relation_id"])
            for item in store.list_records("viewpoint_relations")
        }
        for row in claim_additions:
            if row["claim_relation_id"] not in observed_claim:
                raise SystemExit(f"readback failed: {row['claim_relation_id']} absent")
        for row in viewpoint_additions:
            if row["viewpoint_relation_id"] not in observed_viewpoint:
                raise SystemExit(
                    f"readback failed: {row['viewpoint_relation_id']} absent"
                )

    print(
        json.dumps(
            {
                "schema_version": "wang_owner_ruling_edge_result_v1",
                "claim_relations_added": [
                    row["claim_relation_id"] for row in claim_additions
                ],
                "viewpoint_relations_added": [
                    row["viewpoint_relation_id"] for row in viewpoint_additions
                ],
                "ruling_artifact_sha256": ruling["artifact_sha256"],
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
