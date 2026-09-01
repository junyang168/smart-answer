"""Execute a wording revision the owner ruled on, dependents and all.

The batch flow revises a viewpoint only while resolving Claims, and its
reviewer confirms each dragged dependent. An owner ruling (#232 exception 3:
strike 赦罪/留罪 from the subject and let the John 20:23 analogy live in its
own viewpoint) arrives outside any batch, so this runner is the executable
form of that confirmation: the ruling file names the target, the exact old
wording it was made against, the new wording, and every dependent it expects
to drag. The machinery re-derives the dependent set from the registry and
refuses to proceed unless the two lists agree -- a dependent the owner never
saw must not follow silently, and one the owner confirmed must not have
vanished.

Immutable records follow by succession, never by edit: the route revision is
bumped with a supersedes chain, its attestation is minted anew under the
route worker's own id recipe and the stranded original is retired in the
same ChangeSet (#318), and the structure revision gains a successor whose
focal pins the new wording. Mutable records -- claim links, viewpoint
relations, the viewpoint and structure heads -- repoint in place.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_route_changeset import (
    ROUTE_CHANGESET_POLICY_VERSION,
)
from backend.pipeline.viewpoint_resolution_runtime import (
    PROJECT_ROOT,
    write_derived as _write_derived,
    write_immutable as _write_immutable,
)

REVISION_RULING_SCHEMA_VERSION = "wang_owner_revision_ruling_v1"


class RevisionRulingError(ValueError):
    pass


def _validated_ruling(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    stated = str(payload.get("artifact_sha256") or "")
    if payload.get("schema_version") != REVISION_RULING_SCHEMA_VERSION:
        raise RevisionRulingError("ruling file declares another schema")
    if not stated or stated != sha256_json(body):
        raise RevisionRulingError("ruling file SHA mismatch")
    for field in (
        "decided_by",
        "decided_at",
        "recorded_at_urls",
        "target_viewpoint_revision_id",
        "expected_core_proposition",
        "new_core_proposition",
        "new_proposition_signature",
        "reason",
        "expected_dependents",
    ):
        if not body.get(field):
            raise RevisionRulingError(f"ruling file lacks {field}")
    return dict(body) | {"artifact_sha256": stated}


def build_revision_package(
    ruling: Mapping[str, Any],
    *,
    store_records: Mapping[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[tuple[str, str]], dict[str, Any]]:
    """The package, the retiring keys, and a human-readable manifest."""

    target = str(ruling["target_viewpoint_revision_id"])
    revisions = {
        str(row["viewpoint_revision_id"]): row
        for row in store_records["viewpoint_revisions"]
    }
    old = revisions.get(target)
    if old is None:
        raise RevisionRulingError(f"{target}: no such viewpoint revision")
    viewpoint_id = str(old["viewpoint_id"])
    heads = {
        str(row["viewpoint_id"]): row
        for row in store_records["canonical_viewpoints"]
    }
    head = heads.get(viewpoint_id)
    if head is None or str(head.get("current_revision_id")) != target:
        raise RevisionRulingError(
            f"{target}: not the current revision of {viewpoint_id}; "
            "the ruling must return to the owner against the new wording"
        )
    if str(old.get("core_proposition")) != str(ruling["expected_core_proposition"]):
        raise RevisionRulingError(
            f"{target}: wording moved since the ruling; return to the owner"
        )

    # -- the successor revision -------------------------------------------------
    new_revision = deepcopy(old)
    new_revision["core_proposition"] = str(ruling["new_core_proposition"])
    new_revision["proposition_signature"] = deepcopy(
        ruling["new_proposition_signature"]
    )
    if ruling.get("new_scope"):
        new_revision["scope"] = deepcopy(ruling["new_scope"])
    new_revision["revision"] = int(old["revision"]) + 1
    new_revision["revision_number"] = int(old.get("revision_number") or old["revision"]) + 1
    new_revision["supersedes_revision_id"] = target
    # The provenance model deliberately holds two fields and no more (the
    # 2026-08-25 outage note on ViewpointRevisionProvenance); the ruling's
    # words live in the ruling artifact the SHA binds, and the identity basis
    # is unchanged from the wording being superseded.
    old_provenance = dict(old.get("provenance") or {})
    new_revision["provenance"] = {
        "basis_identity_decision_ids": list(
            old_provenance.get("basis_identity_decision_ids") or []
        ),
        "review_artifact_sha256": str(ruling["artifact_sha256"]),
    }
    new_revision["approved_by"] = str(ruling["decided_by"])
    new_revision["approved_at"] = str(ruling["decided_at"])
    revision_seed = {
        "viewpoint_id": viewpoint_id,
        "supersedes": target,
        "core_proposition": new_revision["core_proposition"],
        "proposition_signature": new_revision["proposition_signature"],
        "scope": new_revision.get("scope"),
    }
    new_revision_id = f"CVR-{sha256_json(revision_seed)[:20]}"
    new_revision["viewpoint_revision_id"] = new_revision_id

    # -- enumerate dependents from the registry --------------------------------
    dragged: dict[str, list[str]] = {
        "viewpoint_claim_links": [],
        "viewpoint_relations": [],
        "argument_route_revisions": [],
        "argument_route_attestations": [],
        "viewpoint_structure_revisions": [],
    }
    links_out: list[dict[str, Any]] = []
    for row in store_records["viewpoint_claim_links"]:
        if str(row.get("validated_against_viewpoint_revision_id")) != target:
            continue
        if str(row.get("effective_state") or "active") != "active":
            continue
        dragged["viewpoint_claim_links"].append(str(row["viewpoint_claim_link_id"]))
        moved = deepcopy(row)
        moved["validated_against_viewpoint_revision_id"] = new_revision_id
        links_out.append(moved)

    relations_out: list[dict[str, Any]] = []
    for row in store_records["viewpoint_relations"]:
        if str(row.get("effective_state") or "active") != "active":
            continue
        touched = False
        moved = deepcopy(row)
        for side in ("source", "target"):
            field = f"validated_{side}_viewpoint_revision_id"
            if str(row.get(field)) == target:
                moved[field] = new_revision_id
                touched = True
        if touched:
            dragged["viewpoint_relations"].append(str(row["viewpoint_relation_id"]))
            relations_out.append(moved)

    route_heads = {
        str(row["argument_route_id"]): row
        for row in store_records["argument_routes"]
    }
    route_revisions_out: list[dict[str, Any]] = []
    routes_out: list[dict[str, Any]] = []
    bumped_route_revisions: dict[str, str] = {}
    for row in store_records["argument_route_revisions"]:
        if str(row.get("validated_against_conclusion_viewpoint_revision_id")) != target:
            continue
        route_id = str(row["argument_route_id"])
        route_head = route_heads.get(route_id)
        if route_head is None or str(route_head.get("current_revision_id")) != str(
            row["argument_route_revision_id"]
        ):
            # History pins the wording it was validated against (#318); only
            # the current revision follows.
            continue
        previous_id = str(row["argument_route_revision_id"])
        dragged["argument_route_revisions"].append(previous_id)
        bumped = deepcopy(row)
        bumped["validated_against_conclusion_viewpoint_revision_id"] = new_revision_id
        for node in bumped.get("ordered_inference_nodes") or []:
            if node.get("conclusion_viewpoint_revision_id") == target:
                node["conclusion_viewpoint_revision_id"] = new_revision_id
        number = int(bumped.get("revision_number") or bumped["revision"]) + 1
        bumped["revision"] = number
        bumped["revision_number"] = number
        bumped["supersedes_revision_id"] = previous_id
        seed = {
            "policy_version": ROUTE_CHANGESET_POLICY_VERSION,
            "argument_route_id": route_id,
            "revision_number": number,
            "conclusion_viewpoint_revision_id": new_revision_id,
        }
        bumped["argument_route_revision_id"] = f"ARR-{sha256_json(seed)[:20]}"
        bumped_route_revisions[previous_id] = bumped["argument_route_revision_id"]
        route_revisions_out.append(bumped)
        moved_head = deepcopy(route_head)
        moved_head["current_revision_id"] = bumped["argument_route_revision_id"]
        routes_out.append(moved_head)

    attestations_out: list[dict[str, Any]] = []
    retiring: list[tuple[str, str]] = []
    for row in store_records["argument_route_attestations"]:
        previous_route_revision = str(row.get("validated_against_route_revision_id"))
        bumped_id = bumped_route_revisions.get(previous_route_revision)
        if bumped_id is None:
            continue
        if str(row.get("effective_state") or "active") != "active":
            continue
        old_attestation_id = str(row["argument_route_attestation_id"])
        dragged["argument_route_attestations"].append(old_attestation_id)
        minted = deepcopy(row)
        minted["validated_against_route_revision_id"] = bumped_id
        attestation_seed = {
            "policy_version": ROUTE_CHANGESET_POLICY_VERSION,
            "route_revision_id": bumped_id,
            "source_id": str(minted["source_id"]),
            "source_revision_sha256": str(minted["source_revision_sha256"]),
            "claim_ids": sorted({str(v) for v in minted.get("claim_ids") or []}),
            "step_bindings": minted.get("step_bindings") or [],
            "terminal_claim_link_id": str(minted["terminal_claim_link_id"]),
        }
        minted["argument_route_attestation_id"] = f"ARA-{sha256_json(attestation_seed)[:20]}"
        minted["review_artifact_sha256"] = str(ruling["artifact_sha256"])
        attestations_out.append(minted)
        retiring.append(("argument_route_attestations", old_attestation_id))

    structure_heads = {
        str(row["structure_id"]): row
        for row in store_records["viewpoint_structures"]
    }
    structure_revisions_out: list[dict[str, Any]] = []
    structures_out: list[dict[str, Any]] = []
    for row in store_records["viewpoint_structure_revisions"]:
        focal = row.get("focal_viewpoints") or []
        if not any(str(f.get("viewpoint_revision_id")) == target for f in focal):
            continue
        structure_id = str(row["structure_id"])
        head_row = structure_heads.get(structure_id)
        if head_row is None or str(head_row.get("current_revision_id")) != str(
            row["structure_revision_id"]
        ):
            continue
        previous_id = str(row["structure_revision_id"])
        dragged["viewpoint_structure_revisions"].append(previous_id)
        successor = deepcopy(row)
        for f in successor.get("focal_viewpoints") or []:
            if str(f.get("viewpoint_revision_id")) == target:
                f["viewpoint_revision_id"] = new_revision_id
        number = int(successor.get("revision_number") or successor["revision"]) + 1
        successor["revision"] = number
        successor["revision_number"] = number
        successor["supersedes_revision_id"] = previous_id
        successor["review_provenance"] = {
            "review_artifact_sha256": str(ruling["artifact_sha256"]),
            "basis_identity_decision_ids": [],
        }
        seed = {
            "structure_id": structure_id,
            "supersedes": previous_id,
            "focal": [
                str(f.get("viewpoint_revision_id"))
                for f in successor.get("focal_viewpoints") or []
            ],
        }
        successor["structure_revision_id"] = f"VSR-{sha256_json(seed)[:20]}"
        structure_revisions_out.append(successor)
        moved_head = deepcopy(head_row)
        moved_head["current_revision_id"] = successor["structure_revision_id"]
        structures_out.append(moved_head)

    # -- owner confirmation must cover exactly what the registry drags ----------
    expected = {
        key: sorted(str(v) for v in values)
        for key, values in dict(ruling["expected_dependents"]).items()
    }
    actual = {key: sorted(values) for key, values in dragged.items() if values}
    if expected != actual:
        raise RevisionRulingError(
            "dependents diverge from the ruling; return to the owner: "
            + json.dumps({"expected": expected, "actual": actual}, ensure_ascii=False)
        )

    moved_viewpoint_head = deepcopy(head)
    moved_viewpoint_head["current_revision_id"] = new_revision_id

    package: dict[str, Any] = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": f"RULING-REVISION-{str(ruling['artifact_sha256'])[:20]}",
        "canonical_viewpoints": [moved_viewpoint_head],
        "viewpoint_revisions": [new_revision],
    }
    if links_out:
        package["viewpoint_claim_links"] = links_out
    if relations_out:
        package["viewpoint_relations"] = relations_out
    if route_revisions_out:
        package["argument_route_revisions"] = route_revisions_out
        package["argument_routes"] = routes_out
    if attestations_out:
        package["argument_route_attestations"] = attestations_out
    if structure_revisions_out:
        package["viewpoint_structure_revisions"] = structure_revisions_out
        package["viewpoint_structures"] = structures_out

    manifest = {
        "new_viewpoint_revision_id": new_revision_id,
        "supersedes": target,
        "dragged": dragged,
        "minted_attestations": [
            row["argument_route_attestation_id"] for row in attestations_out
        ],
        "retired_attestations": [key for _, key in retiring],
        "bumped_route_revisions": bumped_route_revisions,
        "successor_structure_revisions": [
            row["structure_revision_id"] for row in structure_revisions_out
        ],
    }
    return package, retiring, manifest


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
    store_records = {
        collection: store.list_records(collection)
        for collection in (
            "viewpoint_revisions",
            "canonical_viewpoints",
            "viewpoint_claim_links",
            "viewpoint_relations",
            "argument_routes",
            "argument_route_revisions",
            "argument_route_attestations",
            "viewpoint_structures",
            "viewpoint_structure_revisions",
        )
    }
    package, retiring, manifest = build_revision_package(
        ruling, store_records=store_records
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_immutable(args.output_dir / "ruling-revision-package.json", package)

    plan = store.plan_package(
        package, source_kind="owner_ruling_revision", retiring_keys=retiring
    )
    plan_document = plan.as_dict()
    plan_document["schema_version"] = "wang_owner_ruling_revision_plan_v1"
    plan_document["ruling_artifact_sha256"] = ruling["artifact_sha256"]
    plan_document["manifest"] = manifest
    plan_document["apply_allowed"] = bool(args.apply)
    plan_document["artifact_sha256"] = sha256_json(plan_document)
    _write_derived(args.output_dir / "ruling-revision-plan.json", plan_document)

    mutations = 0
    if args.apply:
        result = store.apply_plan(plan)
        if result.get("status") == "applied":
            mutations = len(plan.operations)
        observed = {
            str(row["viewpoint_id"]): str(row.get("current_revision_id"))
            for row in store.list_records("canonical_viewpoints")
        }
        new_id = manifest["new_viewpoint_revision_id"]
        viewpoint_id = str(package["canonical_viewpoints"][0]["viewpoint_id"])
        if observed.get(viewpoint_id) != new_id:
            raise SystemExit(f"readback failed: {viewpoint_id} does not head {new_id}")

    print(
        json.dumps(
            {
                "schema_version": "wang_owner_ruling_revision_result_v1",
                **manifest,
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
