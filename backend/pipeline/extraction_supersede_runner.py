"""Ingest a re-extraction and retire the extraction it replaces, in one change set.

Planning is the default and `--apply` is opt-in, for the same reason the rest
of this store works that way: seeing what a change set would do is a question
anyone may ask, and changing the authoring authority is a decision somebody
makes once.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    EXTRACTION_RECORD_COLLECTIONS,
    OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS,
    PostgresKnowledgeStore,
    SEMANTIC_REFERENCE_COLLECTIONS,
    record_content_sha,
    sha256_json,
    uncoordinated_semantic_reference_blockers,
)
from backend.pipeline.extraction_supersede import package_source_ids, superseded
from backend.pipeline.knowledge_package_merge import (
    KnowledgePackageMergeError,
    validate_merged_package,
)
from backend.pipeline.knowledge_consensus_applier import (
    ConsensusApplicationError,
    validate_reviewed_candidate_artifact,
)
from backend.pipeline.knowledge_source import validate_package_current_source_provenance
from backend.api.canonical_repository.reviewed_candidate_contract import (
    reseal_after_relation_id_migration,
    validate_store_package_authorization,
)
from backend.pipeline.source_keys import package_row_key
from backend.pipeline.run_ledger import run_record
from backend.pipeline.record_withdrawal import ANCHORED_COLLECTIONS
from backend.pipeline.relation_id_namespace import (
    migrate_legacy_cross_section_relation_ids,
    source_namespace,
)

RELATION_COLLECTIONS = ("claim_relations", "knowledge_relations")
SOURCE_BOUND_CVR_COLLECTIONS = {
    "argument_route_attestations",
    "viewpoint_claim_links",
    "viewpoint_identity_candidates",
}
DEFAULT_TRANSCRIPT_DIRS = [
    Path("/opt/homebrew/var/www/church/web/data/script_published"),
    Path("/opt/homebrew/var/www/church/web/data/script_review"),
]


def _seal_audit(audit: dict[str, Any]) -> dict[str, Any]:
    audit["scope_sha256"] = sha256_json(audit)
    return audit


def _retiring_extraction_ids(change_set: Any) -> set[str]:
    return {
        operation.object_id
        for operation in change_set.operations
        if operation.operation == "retire"
        and (
            operation.collection in EXTRACTION_RECORD_COLLECTIONS
            or operation.collection == "source_documents"
        )
    }


def stale_source_cvr_retirement(
    *,
    source_id: str,
    change_set: Any,
    semantic_rows: Sequence[tuple[str, str, Mapping[str, Any]]],
    record_states: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Retire source-bound CVR rows invalidated by a new extraction generation.

    This never maps an old extraction ID to a new one.  It removes only the
    source occurrence/link/candidate layer, leaving canonical viewpoints and
    route revisions intact for a later CVR rebuild from the new Claims.
    """

    source_id = str(source_id or "").strip()
    if not source_id.startswith("SRC-"):
        raise ValueError("stale source CVR retirement requires an exact SRC- source id")
    retired_ids = _retiring_extraction_ids(change_set)
    retiring_claim_ids = {
        operation.object_id
        for operation in change_set.operations
        if operation.operation == "retire" and operation.collection == "claims"
    }
    blocker_keys = {
        tuple(item.split(" ->", 1)[0].split("/", 1))
        for item in uncoordinated_semantic_reference_blockers(change_set, semantic_rows)
    }
    if not blocker_keys:
        audit = {
            "schema_version": "wang_stale_source_cvr_retirement_v1",
            "source_id": source_id,
            "reason_code": "source_extraction_generation_superseded",
            "status": "not_needed",
            "retired_extraction_ids_sha256": sha256_json(sorted(retired_ids)),
            "summary": {collection: 0 for collection in sorted(SOURCE_BOUND_CVR_COLLECTIONS)},
            "records": [],
        }
        audit["summary"]["total"] = 0
        return [], _seal_audit(audit)

    unexpected = sorted(key for key in blocker_keys if key[0] not in SOURCE_BOUND_CVR_COLLECTIONS)
    if unexpected:
        raise ValueError(
            "source CVR retirement cannot retire unrelated semantic authority: "
            + ", ".join(f"{collection}/{object_id}" for collection, object_id in unexpected)
        )
    rows = {
        (str(collection), str(object_id)): payload
        for collection, object_id, payload in semantic_rows
    }
    records: list[dict[str, Any]] = []
    for key in sorted(blocker_keys):
        payload = rows.get(key)
        if payload is None or str(payload.get("visibility") or "") != "internal":
            raise ValueError(f"source CVR retirement row is absent or public: {key[0]}/{key[1]}")
        if key[0] == "argument_route_attestations":
            if str(payload.get("source_id") or "") != source_id:
                raise ValueError(f"route attestation belongs to another source: {key[1]}")
        elif key[0] == "viewpoint_claim_links":
            if str(payload.get("claim_id") or "") not in retiring_claim_ids:
                raise ValueError(f"viewpoint link does not pin a retiring Claim: {key[1]}")
        else:
            candidate_claim_ids = set(map(str, payload.get("candidate_claim_ids") or []))
            if (
                not candidate_claim_ids
                or not candidate_claim_ids.intersection(retiring_claim_ids)
                or str(payload.get("review_status") or "") != "candidate"
            ):
                raise ValueError(
                    "identity candidate is not a pending affected candidate: "
                    f"{key[1]}"
                )
        state = record_states.get(key) or {}
        content_sha256 = str(state.get("content_sha256") or record_content_sha(payload))
        if content_sha256 != record_content_sha(payload):
            raise ValueError(f"source CVR row has inconsistent stored SHA: {key[0]}/{key[1]}")
        record = {
            "collection": key[0],
            "object_id": key[1],
            "expected_revision": state.get("revision", payload.get("revision")),
            "expected_content_sha256": content_sha256,
            "review_status": str(payload.get("review_status") or ""),
        }
        if key[0] == "viewpoint_identity_candidates":
            record["other_claim_ids_requiring_regroup"] = sorted(
                candidate_claim_ids - retiring_claim_ids
            )
        records.append(record)

    def strings(value: Any) -> set[str]:
        if isinstance(value, Mapping):
            found = {str(key) for key in value if isinstance(key, str)}
            for child in value.values():
                found.update(strings(child))
            return found
        if isinstance(value, (list, tuple, set)):
            found: set[str] = set()
            for child in value:
                found.update(strings(child))
            return found
        return {value} if isinstance(value, str) else set()

    selected_ids = {row["object_id"] for row in records}
    external = sorted(
        f"{collection}/{object_id}"
        for collection, object_id, payload in semantic_rows
        if (str(collection), str(object_id)) not in blocker_keys
        and strings(payload) & selected_ids
    )
    if external:
        raise ValueError(
            "source CVR rows still have current external references: " + ", ".join(external)
        )
    summary = {
        collection: sum(row["collection"] == collection for row in records)
        for collection in sorted(SOURCE_BOUND_CVR_COLLECTIONS)
    }
    summary["total"] = len(records)
    audit = {
        "schema_version": "wang_stale_source_cvr_retirement_v1",
        "source_id": source_id,
        "reason_code": "source_extraction_generation_superseded",
        "status": "planned",
        "retired_extraction_ids_sha256": sha256_json(sorted(retired_ids)),
        "summary": summary,
        "records": records,
    }
    return [(row["collection"], row["object_id"]) for row in records], _seal_audit(audit)

def products_to_rebuild(
    changed_records: set[tuple[str, str]],
    *,
    dependencies: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Current downstream products invalidated by this withdrawal.

    This is the read-only preview of ``PostgresKnowledgeStore`` dependency
    invalidation.  It deliberately reads the generic ProductDependency
    records used by draft-first products instead of reconstructing an older
    CompositionPlan citation graph.  Keeping preview and apply on the same
    authority prevents an ingest from silently making a product stale.

    Claim dependencies have a dedicated field for backwards compatibility;
    every other dependency is represented in ``dependency_manifest``.
    """

    if not changed_records:
        return []
    by_consumer: dict[tuple[str, str], dict[str, set[Any]]] = {}
    for dependency_id, payload in dependencies.items():
        if str(payload.get("status") or "current") != "current":
            continue
        refs = {
            (str(item.get("collection") or ""), str(item.get("record_id") or ""))
            for item in payload.get("dependency_manifest") or []
            if isinstance(item, Mapping)
        }
        claim_id = str(payload.get("claim_id") or "").strip()
        if claim_id:
            refs.add(("claims", claim_id))
        affected = refs & changed_records
        if not affected:
            continue
        consumer_kind = str(payload.get("consumer_kind") or "unknown")
        consumer_id = str(payload.get("consumer_id") or "")
        group = by_consumer.setdefault(
            (consumer_kind, consumer_id),
            {"dependency_ids": set(), "changed_records": set()},
        )
        group["dependency_ids"].add(str(dependency_id))
        group["changed_records"].update(affected)

    return [
        {
            "consumer_kind": consumer_kind,
            "consumer_id": consumer_id,
            "affected_dependency_ids": sorted(group["dependency_ids"]),
            "changed_records": [
                {"collection": collection, "record_id": record_id}
                for collection, record_id in sorted(group["changed_records"])
            ],
        }
        for (consumer_kind, consumer_id), group in sorted(by_consumer.items())
    ]


def product_impact_keys(change_set: Any) -> set[tuple[str, str]]:
    """Records whose revision/state change can invalidate a consumer."""

    return {
        (operation.collection, operation.object_id)
        for operation in change_set.operations
        if operation.operation in {"update", "retire", "revive"}
    }


def _live(cursor: Any, collection: str) -> dict[str, dict[str, Any]]:
    cursor.execute(
        """SELECT object_id, payload FROM wang_knowledge.objects
           WHERE collection=%s AND retired_at IS NULL""",
        (collection,),
    )
    return {str(object_id): payload for object_id, payload in cursor.fetchall()}


def obsolete_candidate_batch_retirement(
    *,
    batch_id: str,
    live: Mapping[str, Mapping[str, Mapping[str, Any]]],
    all_live_rows: list[tuple[str, str, Mapping[str, Any]]],
    known_plan_ids: set[str] | None = None,
    record_states: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Freeze one retired-workflow candidate batch into an explicit retire set.

    This is intentionally opt-in. It is not a license to delete semantic
    master data whenever re-extraction encounters a reference. The only
    eligible rows are internal, unapproved candidates owned by the named old
    CompositionPlan batch. Every exact reference from outside the selected set
    blocks the cleanup, and revisions/content SHAs are reported before apply.
    """

    normalized_batch = str(batch_id or "").strip()
    if not normalized_batch.startswith("RB-") or len(normalized_batch) <= 3:
        raise ValueError("obsolete candidate batch id must start with RB-")
    plan_pattern = re.compile(
        rf"^CP-{re.escape(normalized_batch[3:])}-[ST]-[0-9a-f]{{12}}$"
    )
    synthesis_pattern = re.compile(
        rf"^SYN-{re.escape(normalized_batch[3:])}-[ST]-[0-9a-f]{{12}}$"
    )
    known = sorted(
        object_id
        for object_id in (known_plan_ids or set())
        if plan_pattern.fullmatch(object_id)
    )
    plans = {
        object_id: payload
        for object_id, payload in (live.get("composition_plans") or {}).items()
        if plan_pattern.fullmatch(object_id)
    }
    batch_plan_ids = set(known) | set(plans)
    if not plans:
        if not known:
            raise ValueError(
                f"obsolete candidate batch {normalized_batch} has no known CompositionPlan rows"
            )
        lingering = sorted(
            [
                f"composition_decisions/{object_id}"
                for object_id, payload in (
                    live.get("composition_decisions") or {}
                ).items()
                if str(payload.get("plan_id") or "") in batch_plan_ids
            ]
            + [
                f"knowledge_routes/{object_id}"
                for object_id, payload in (live.get("knowledge_routes") or {}).items()
                if str(payload.get("target_id") or "") in batch_plan_ids
            ]
            + [
                f"editorial_syntheses/{object_id}"
                for object_id, payload in (
                    live.get("editorial_syntheses") or {}
                ).items()
                if synthesis_pattern.fullmatch(object_id)
            ]
        )
        if lingering:
            raise ValueError(
                "obsolete candidate batch is only partially retired: "
                + ", ".join(lingering)
            )
        audit = {
            "schema_version": "wang_obsolete_candidate_batch_retirement_v1",
            "batch_id": normalized_batch,
            "reason_code": "composition_plan_candidate_retired_by_draft_first",
            "selection_policy": (
                "explicit batch; exact retired-workflow id/foreign-key ownership; "
                "candidate+internal only; "
                "zero external current references"
            ),
            "status": "already_retired",
            "known_plan_ids": known,
            "summary": {
                **{
                    collection: 0
                    for collection in OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS
                },
                "total": 0,
            },
            "records": [],
        }
        return [], _seal_audit(audit)
    plan_ids = batch_plan_ids
    selected: dict[tuple[str, str], Mapping[str, Any]] = {
        ("composition_plans", object_id): payload
        for object_id, payload in plans.items()
    }
    for object_id, payload in (live.get("composition_decisions") or {}).items():
        if str(payload.get("plan_id") or "") in plan_ids:
            selected[("composition_decisions", object_id)] = payload
    for object_id, payload in (live.get("knowledge_routes") or {}).items():
        if str(payload.get("target_id") or "") in plan_ids:
            selected[("knowledge_routes", object_id)] = payload
    for object_id, payload in (live.get("editorial_syntheses") or {}).items():
        if synthesis_pattern.fullmatch(object_id):
            selected[("editorial_syntheses", object_id)] = payload

    invalid = [
        f"{collection}/{object_id}"
        for (collection, object_id), payload in sorted(selected.items())
        if str(payload.get("review_status") or "") != "candidate"
        or str(payload.get("visibility") or "") != "internal"
    ]
    if invalid:
        raise ValueError(
            "obsolete candidate retirement includes non-candidate authority: "
            + ", ".join(invalid)
        )

    selected_ids = {object_id for _collection, object_id in selected}

    def strings(value: Any) -> set[str]:
        if isinstance(value, Mapping):
            found = {str(key) for key in value if isinstance(key, str)}
            for child in value.values():
                found.update(strings(child))
            return found
        if isinstance(value, (list, tuple, set)):
            found: set[str] = set()
            for child in value:
                found.update(strings(child))
            return found
        return {value} if isinstance(value, str) else set()

    external_references: list[str] = []
    for collection, object_id, payload in all_live_rows:
        key = (str(collection), str(object_id))
        if key in selected:
            continue
        referenced = strings(payload) & selected_ids
        if referenced:
            external_references.append(
                f"{key[0]}/{key[1]} -> {','.join(sorted(referenced))}"
            )
    if external_references:
        raise ValueError(
            "obsolete candidate batch still has current external references: "
            + " | ".join(sorted(external_references))
        )

    records = []
    for (collection, object_id), payload in sorted(selected.items()):
        state = (record_states or {}).get((collection, object_id)) or {}
        content_sha256 = str(
            state.get("content_sha256") or record_content_sha(payload)
        )
        if content_sha256 != record_content_sha(payload):
            raise ValueError(
                "obsolete candidate row has inconsistent stored content SHA: "
                f"{collection}/{object_id}"
            )
        records.append({
            "collection": collection,
            "object_id": object_id,
            "expected_revision": state.get("revision", payload.get("revision")),
            "expected_content_sha256": content_sha256,
        })
    summary = {
        collection: sum(row["collection"] == collection for row in records)
        for collection in OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS
    }
    summary["total"] = len(records)
    audit = {
        "schema_version": "wang_obsolete_candidate_batch_retirement_v1",
        "batch_id": normalized_batch,
        "reason_code": "composition_plan_candidate_retired_by_draft_first",
        "selection_policy": (
            "explicit batch; exact retired-workflow id/foreign-key ownership; "
            "candidate+internal only; "
            "zero external current references"
        ),
        "status": "planned",
        "known_plan_ids": known,
        "summary": summary,
        "records": records,
    }
    _seal_audit(audit)
    return [
        (row["collection"], row["object_id"])
        for row in records
    ], audit


def stale_candidate_projection_retirement(
    *,
    batch_ids: Sequence[str],
    change_set: Any,
    all_live_rows: list[tuple[str, str, Mapping[str, Any]]],
    known_plan_ids: set[str],
    record_states: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Retire only old-workflow projections invalidated by this extraction.

    Historical CompositionPlans and decisions remain readable and are not part
    of the retirement.  They prove ownership only.  The operator names every
    allowed batch, and only candidate/internal routes and syntheses whose
    authoritative claim fields name this ChangeSet's retiring generation are
    eligible.  A mixed synthesis is retired whole; claim ids are never patched.
    """

    normalized_batches = sorted({str(value or "").strip() for value in batch_ids})
    if not normalized_batches or any(
        not value.startswith("RB-") or len(value) <= 3
        for value in normalized_batches
    ):
        raise ValueError("stale projection batch ids must start with RB-")
    plan_patterns = {
        batch_id: re.compile(
            rf"^CP-{re.escape(batch_id[3:])}-[ST]-[0-9a-f]{{12}}$"
        )
        for batch_id in normalized_batches
    }
    synthesis_patterns = {
        batch_id: re.compile(
            rf"^SYN-{re.escape(batch_id[3:])}-[ST]-[0-9a-f]{{12}}$"
        )
        for batch_id in normalized_batches
    }
    unknown_batches = sorted(
        batch_id
        for batch_id, pattern in plan_patterns.items()
        if not any(pattern.fullmatch(plan_id) for plan_id in known_plan_ids)
    )
    if unknown_batches:
        raise ValueError(
            "unknown stale projection batch ids: " + ", ".join(unknown_batches)
        )
    retired_ids = _retiring_extraction_ids(change_set)
    retired_ids_sha256 = sha256_json(sorted(retired_ids))
    live_plans = {
        object_id: payload
        for collection, object_id, payload in all_live_rows
        if collection == "composition_plans"
    }
    decisions_by_plan: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for collection, object_id, payload in all_live_rows:
        if collection != "composition_decisions":
            continue
        decisions_by_plan.setdefault(
            str(payload.get("plan_id") or ""), []
        ).append((object_id, payload))

    def owner(
        collection: str, object_id: str, payload: Mapping[str, Any]
    ) -> tuple[str, str, list[str]] | None:
        if collection == "knowledge_routes":
            claim_id = str(payload.get("claim_id") or "")
            stale_claim_ids = [claim_id] if claim_id in retired_ids else []
            plan_id = str(payload.get("target_id") or "")
            matches = [
                batch_id
                for batch_id, pattern in plan_patterns.items()
                if pattern.fullmatch(plan_id)
            ]
        elif collection == "editorial_syntheses":
            stale_claim_ids = sorted(
                set(map(str, payload.get("claim_ids") or [])) & retired_ids
            )
            matches = [
                batch_id
                for batch_id, pattern in synthesis_patterns.items()
                if pattern.fullmatch(object_id)
                and str(payload.get("corpus_scope") or "") == batch_id
            ]
            plan_id = f"CP{object_id[3:]}" if object_id.startswith("SYN-") else ""
        else:
            return None
        if not stale_claim_ids:
            return None
        if len(matches) != 1:
            raise ValueError(
                "stale candidate projection has ambiguous or mismatched batch ownership: "
                f"{collection}/{object_id}"
            )
        batch_id = matches[0]
        if plan_id not in known_plan_ids:
            raise ValueError(
                "stale candidate projection lacks a known historical CompositionPlan: "
                f"{collection}/{object_id} -> {plan_id or '<missing>'}"
            )
        plan = live_plans.get(plan_id)
        if plan is not None and (
            str(plan.get("review_status") or "") != "candidate"
            or str(plan.get("visibility") or "") != "internal"
        ):
            raise ValueError(
                "stale candidate projection is attached to current plan authority: "
                f"composition_plans/{plan_id}"
            )
        authoritative_decisions = sorted(
            decision_id
            for decision_id, decision in decisions_by_plan.get(plan_id, [])
            if str(decision.get("review_status") or "") != "candidate"
            or str(decision.get("visibility") or "") != "internal"
        )
        if authoritative_decisions:
            raise ValueError(
                "stale candidate projection is attached to current decision authority: "
                + ", ".join(authoritative_decisions)
            )
        return batch_id, plan_id, stale_claim_ids

    selected: dict[
        tuple[str, str], tuple[Mapping[str, Any], str, str, list[str]]
    ] = {}
    invalid: list[str] = []
    for collection, object_id, payload in all_live_rows:
        owned = owner(collection, object_id, payload)
        if owned is None:
            continue
        batch_id, plan_id, stale_claim_ids = owned
        key = (str(collection), str(object_id))
        if (
            str(payload.get("review_status") or "") != "candidate"
            or str(payload.get("visibility") or "") != "internal"
        ):
            invalid.append(f"{key[0]}/{key[1]}")
            continue
        selected[key] = (payload, batch_id, plan_id, stale_claim_ids)
    if invalid:
        raise ValueError(
            "stale candidate projection includes non-candidate authority: "
            + ", ".join(sorted(invalid))
        )

    def strings(value: Any) -> set[str]:
        if isinstance(value, Mapping):
            found = {str(key) for key in value if isinstance(key, str)}
            for child in value.values():
                found.update(strings(child))
            return found
        if isinstance(value, (list, tuple, set)):
            found: set[str] = set()
            for child in value:
                found.update(strings(child))
            return found
        return {value} if isinstance(value, str) else set()

    selected_ids = {object_id for _collection, object_id in selected}
    external_references = []
    for collection, object_id, payload in all_live_rows:
        key = (str(collection), str(object_id))
        if key in selected:
            continue
        referenced = strings(payload) & selected_ids
        if referenced:
            external_references.append(
                f"{key[0]}/{key[1]} -> {','.join(sorted(referenced))}"
            )
    if external_references:
        raise ValueError(
            "stale candidate projections still have current external references: "
            + " | ".join(sorted(external_references))
        )

    records = []
    for (collection, object_id), (
        payload,
        batch_id,
        plan_id,
        stale_claim_ids,
    ) in sorted(selected.items()):
        state = (record_states or {}).get((collection, object_id)) or {}
        content_sha256 = str(
            state.get("content_sha256") or record_content_sha(payload)
        )
        if content_sha256 != record_content_sha(payload):
            raise ValueError(
                "stale candidate projection has inconsistent stored content SHA: "
                f"{collection}/{object_id}"
            )
        records.append({
            "collection": collection,
            "object_id": object_id,
            "batch_id": batch_id,
            "owner_plan_id": plan_id,
            "expected_revision": state.get("revision", payload.get("revision")),
            "expected_content_sha256": content_sha256,
            "stale_claim_ids": stale_claim_ids,
        })
    summary = {
        "knowledge_routes": sum(
            row["collection"] == "knowledge_routes" for row in records
        ),
        "editorial_syntheses": sum(
            row["collection"] == "editorial_syntheses" for row in records
        ),
        "total": len(records),
    }
    audit = {
        "schema_version": "wang_stale_candidate_projection_retirement_v1",
        "batch_ids": normalized_batches,
        "reason_code": (
            "retired_composition_projection_invalidated_by_extraction_supersession"
        ),
        "selection_policy": (
            "explicit batch allow-list; exact authoritative claim reference; "
            "candidate+internal only; historical plan ownership; "
            "zero external current references"
        ),
        "status": "planned" if records else "not_needed",
        "retired_extraction_ids_sha256": retired_ids_sha256,
        "summary": summary,
        "records": records,
    }
    _seal_audit(audit)
    return [(row["collection"], row["object_id"]) for row in records], audit


def stale_ai_cross_sermon_constraint_retirement(
    *,
    constraint_ids: Sequence[str],
    change_set: Any,
    all_live_rows: list[tuple[str, str, Mapping[str, Any]]],
    known_constraint_ids: set[str],
    record_states: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Retire named AI cross-sermon judgments whose endpoint is superseded.

    No claim-id mapping is inferred.  The old judgment remains in object
    history; its current edge is retired atomically with the endpoint and a new
    cross-sermon run must decide whether any relation still holds.
    """

    normalized_ids = sorted({str(value or "").strip() for value in constraint_ids})
    if not normalized_ids or any(
        re.fullmatch(r"CRC-XSR-[0-9a-f]{16}", value) is None
        for value in normalized_ids
    ):
        raise ValueError("stale cross-sermon constraint ids must be CRC-XSR-<16 hex>")
    unknown = sorted(set(normalized_ids) - known_constraint_ids)
    if unknown:
        raise ValueError(
            "unknown stale cross-sermon constraint ids: " + ", ".join(unknown)
        )
    retired_ids = _retiring_extraction_ids(change_set)
    retired_ids_sha256 = sha256_json(sorted(retired_ids))
    selected: dict[tuple[str, str], tuple[Mapping[str, Any], list[str]]] = {}
    live_requested: set[str] = set()
    invalid: list[str] = []
    for collection, object_id, payload in all_live_rows:
        if collection != "claim_relation_constraints" or object_id not in normalized_ids:
            continue
        live_requested.add(object_id)
        stale_claim_ids = sorted(
            {str(payload.get("source_id") or ""), str(payload.get("target_id") or "")}
            & retired_ids
        )
        expected_artifact_id = object_id.removeprefix("CRC-")
        if (
            not stale_claim_ids
            or str(payload.get("constraint_id") or "") != object_id
            or str(payload.get("review_artifact_id") or "") != expected_artifact_id
            or str(payload.get("review_status") or "") != "ai_consensus"
            or str(payload.get("visibility") or "") != "internal"
        ):
            invalid.append(f"{collection}/{object_id}")
            continue
        selected[(collection, object_id)] = (payload, stale_claim_ids)
    if invalid:
        raise ValueError(
            "stale cross-sermon constraint is not an eligible AI judgment: "
            + ", ".join(sorted(invalid))
        )

    def strings(value: Any) -> set[str]:
        if isinstance(value, Mapping):
            found = {str(key) for key in value if isinstance(key, str)}
            for child in value.values():
                found.update(strings(child))
            return found
        if isinstance(value, (list, tuple, set)):
            found: set[str] = set()
            for child in value:
                found.update(strings(child))
            return found
        return {value} if isinstance(value, str) else set()

    selected_ids = {object_id for _collection, object_id in selected}
    external_references = []
    for collection, object_id, payload in all_live_rows:
        key = (str(collection), str(object_id))
        if key in selected:
            continue
        referenced = strings(payload) & selected_ids
        if referenced:
            external_references.append(
                f"{key[0]}/{key[1]} -> {','.join(sorted(referenced))}"
            )
    if external_references:
        raise ValueError(
            "stale cross-sermon constraints still have current external references: "
            + " | ".join(sorted(external_references))
        )

    records = []
    for (collection, object_id), (payload, stale_claim_ids) in sorted(selected.items()):
        state = (record_states or {}).get((collection, object_id)) or {}
        content_sha256 = str(
            state.get("content_sha256") or record_content_sha(payload)
        )
        if content_sha256 != record_content_sha(payload):
            raise ValueError(
                "stale cross-sermon constraint has inconsistent stored content SHA: "
                f"{collection}/{object_id}"
            )
        records.append({
            "collection": collection,
            "object_id": object_id,
            "expected_revision": state.get("revision", payload.get("revision")),
            "expected_content_sha256": content_sha256,
            "source_id": str(payload.get("source_id") or ""),
            "target_id": str(payload.get("target_id") or ""),
            "review_artifact_id": str(payload.get("review_artifact_id") or ""),
            "reason": str(payload.get("reason") or ""),
            "stale_claim_ids": stale_claim_ids,
        })
    already_retired_ids = sorted(set(normalized_ids) - live_requested)
    audit = {
        "schema_version": "wang_stale_ai_cross_sermon_constraint_retirement_v1",
        "constraint_ids": normalized_ids,
        "reason_code": "cross_sermon_judgment_invalidated_by_claim_supersession",
        "selection_policy": (
            "explicit exact constraint id; internal ai_consensus only; exact "
            "retiring endpoint; zero external current references; never retarget"
        ),
        "status": "planned" if records else "already_retired",
        "retired_extraction_ids_sha256": retired_ids_sha256,
        "already_retired_ids": already_retired_ids,
        "summary": {"claim_relation_constraints": len(records), "total": len(records)},
        "records": records,
    }
    _seal_audit(audit)
    return [(row["collection"], row["object_id"]) for row in records], audit


def stale_pending_topic_identity_retirement(
    *,
    batch_id: str,
    change_set: Any,
    all_live_rows: list[tuple[str, str, Mapping[str, Any]]],
    record_states: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Select only pending topic identities made stale by this replacement.

    Topic identity reconciliation belongs to intelligent grouping, not to the
    retired CompositionPlan workflow.  A re-extraction cannot preserve a
    pending proposal that names claims from the retired generation, however;
    the proposal has to be regenerated from the new claims.  The operator must
    name its origin batch explicitly, and approved/resolved rows remain hard
    semantic blockers rather than being silently discarded.
    """

    normalized_batch = str(batch_id or "").strip()
    if not normalized_batch.startswith("RB-") or len(normalized_batch) <= 3:
        raise ValueError("pending topic identity batch id must start with RB-")
    retired_ids = _retiring_extraction_ids(change_set)
    retired_ids_sha256 = sha256_json(sorted(retired_ids))
    selected: dict[tuple[str, str], tuple[Mapping[str, Any], list[str]]] = {}
    invalid: list[str] = []
    for collection, object_id, payload in all_live_rows:
        if (
            collection != "topic_identity_reconciliations"
            or str(payload.get("origin_batch_id") or "") != normalized_batch
        ):
            continue
        stale_refs = sorted(
            set(map(str, payload.get("claim_ids") or [])) & retired_ids
        )
        if not stale_refs:
            continue
        key = (str(collection), str(object_id))
        if (
            str(payload.get("review_status") or "") != "candidate"
            or str(payload.get("visibility") or "") != "internal"
            or str(payload.get("status") or "")
            not in {"pending_match", "pending_new"}
        ):
            invalid.append(f"{key[0]}/{key[1]}")
            continue
        selected[key] = (payload, stale_refs)
    if invalid:
        raise ValueError(
            "stale topic identity retirement includes resolved or approved authority: "
            + ", ".join(sorted(invalid))
        )

    selected_ids = {object_id for _collection, object_id in selected}

    def strings(value: Any) -> set[str]:
        if isinstance(value, Mapping):
            found = {str(key) for key in value if isinstance(key, str)}
            for child in value.values():
                found.update(strings(child))
            return found
        if isinstance(value, (list, tuple, set)):
            found: set[str] = set()
            for child in value:
                found.update(strings(child))
            return found
        return {value} if isinstance(value, str) else set()

    external_references: list[str] = []
    for collection, object_id, payload in all_live_rows:
        key = (str(collection), str(object_id))
        if key in selected:
            continue
        referenced = strings(payload) & selected_ids
        if referenced:
            external_references.append(
                f"{key[0]}/{key[1]} -> {','.join(sorted(referenced))}"
            )
    if external_references:
        raise ValueError(
            "stale pending topic identities still have current external references: "
            + " | ".join(sorted(external_references))
        )

    records = []
    for (collection, object_id), (payload, stale_refs) in sorted(selected.items()):
        state = (record_states or {}).get((collection, object_id)) or {}
        content_sha256 = str(
            state.get("content_sha256") or record_content_sha(payload)
        )
        if content_sha256 != record_content_sha(payload):
            raise ValueError(
                "pending topic identity row has inconsistent stored content SHA: "
                f"{collection}/{object_id}"
            )
        records.append({
            "collection": collection,
            "object_id": object_id,
            "expected_revision": state.get("revision", payload.get("revision")),
            "expected_content_sha256": content_sha256,
            "stale_claim_ids": stale_refs,
        })
    audit = {
        "schema_version": "wang_stale_pending_topic_identity_retirement_v1",
        "batch_id": normalized_batch,
        "reason_code": (
            "pending_topic_identity_invalidated_by_extraction_supersession"
        ),
        "selection_policy": (
            "explicit origin batch; exact retiring claim reference; "
            "candidate+internal+pending only; zero external current references"
        ),
        "status": "planned" if records else "not_needed",
        "retired_extraction_ids_sha256": retired_ids_sha256,
        "summary": {"topic_identity_reconciliations": len(records), "total": len(records)},
        "records": records,
    }
    _seal_audit(audit)
    return [
        (row["collection"], row["object_id"])
        for row in records
    ], audit


def validate_obsolete_retirement_plan(
    change_set: Any, audit: Mapping[str, Any] | None
) -> None:
    """Bind the reported candidate snapshot to the exact retirement plan."""

    if not audit or audit.get("status") == "already_retired":
        return
    operations = {
        (operation.collection, operation.object_id): operation
        for operation in change_set.operations
    }
    mismatches: list[str] = []
    for row in audit.get("records") or []:
        key = (str(row.get("collection") or ""), str(row.get("object_id") or ""))
        operation = operations.get(key)
        if operation is None or operation.operation != "retire":
            mismatches.append(f"{key[0]}/{key[1]} is not planned for retirement")
            continue
        if operation.before_revision != row.get("expected_revision"):
            mismatches.append(f"{key[0]}/{key[1]} revision drifted")
        if operation.before_sha256 != row.get("expected_content_sha256"):
            mismatches.append(f"{key[0]}/{key[1]} content drifted")
    if mismatches:
        raise ValueError(
            "obsolete candidate retirement snapshot does not match ChangeSet: "
            + " | ".join(mismatches)
        )


def validate_stale_topic_identity_retirement_plan(
    change_set: Any, audit: Mapping[str, Any] | None
) -> None:
    """Bind the stale pending identity audit to the exact ChangeSet."""

    if not audit:
        return
    if audit.get("retired_extraction_ids_sha256") != sha256_json(
        sorted(_retiring_extraction_ids(change_set))
    ):
        raise ValueError(
            "stale topic identity audit does not match retiring extraction ids"
        )
    operations = {
        (operation.collection, operation.object_id): operation
        for operation in change_set.operations
    }
    mismatches: list[str] = []
    for row in audit.get("records") or []:
        key = (str(row.get("collection") or ""), str(row.get("object_id") or ""))
        operation = operations.get(key)
        if operation is None or operation.operation != "retire":
            mismatches.append(f"{key[0]}/{key[1]} is not planned for retirement")
            continue
        if operation.before_revision != row.get("expected_revision"):
            mismatches.append(f"{key[0]}/{key[1]} revision drifted")
        if operation.before_sha256 != row.get("expected_content_sha256"):
            mismatches.append(f"{key[0]}/{key[1]} content drifted")
    if mismatches:
        raise ValueError(
            "stale topic identity retirement snapshot does not match ChangeSet: "
            + " | ".join(mismatches)
        )


def validate_stale_candidate_projection_retirement_plan(
    change_set: Any, audit: Mapping[str, Any] | None
) -> None:
    """Bind a stale route/synthesis audit to the exact ChangeSet."""

    if not audit:
        return
    if audit.get("retired_extraction_ids_sha256") != sha256_json(
        sorted(_retiring_extraction_ids(change_set))
    ):
        raise ValueError(
            "stale candidate projection audit does not match retiring extraction ids"
        )
    operations = {
        (operation.collection, operation.object_id): operation
        for operation in change_set.operations
    }
    mismatches: list[str] = []
    for row in audit.get("records") or []:
        key = (str(row.get("collection") or ""), str(row.get("object_id") or ""))
        operation = operations.get(key)
        if operation is None or operation.operation != "retire":
            mismatches.append(f"{key[0]}/{key[1]} is not planned for retirement")
            continue
        if operation.before_revision != row.get("expected_revision"):
            mismatches.append(f"{key[0]}/{key[1]} revision drifted")
        if operation.before_sha256 != row.get("expected_content_sha256"):
            mismatches.append(f"{key[0]}/{key[1]} content drifted")
    if mismatches:
        raise ValueError(
            "stale candidate projection snapshot does not match ChangeSet: "
            + " | ".join(mismatches)
        )


def validate_stale_ai_cross_sermon_constraint_retirement_plan(
    change_set: Any, audit: Mapping[str, Any] | None
) -> None:
    """Bind named stale cross-sermon judgments to the exact ChangeSet."""

    if not audit:
        return
    if audit.get("retired_extraction_ids_sha256") != sha256_json(
        sorted(_retiring_extraction_ids(change_set))
    ):
        raise ValueError(
            "stale cross-sermon constraint audit does not match retiring extraction ids"
        )
    operations = {
        (operation.collection, operation.object_id): operation
        for operation in change_set.operations
    }
    mismatches: list[str] = []
    for row in audit.get("records") or []:
        key = (str(row.get("collection") or ""), str(row.get("object_id") or ""))
        operation = operations.get(key)
        if operation is None or operation.operation != "retire":
            mismatches.append(f"{key[0]}/{key[1]} is not planned for retirement")
            continue
        if operation.before_revision != row.get("expected_revision"):
            mismatches.append(f"{key[0]}/{key[1]} revision drifted")
        if operation.before_sha256 != row.get("expected_content_sha256"):
            mismatches.append(f"{key[0]}/{key[1]} content drifted")
    if mismatches:
        raise ValueError(
            "stale cross-sermon constraint snapshot does not match ChangeSet: "
            + " | ".join(mismatches)
        )


def transcript_source_aliases(
    package: Mapping[str, Any], live_documents: Mapping[str, Mapping[str, Any]]
) -> set[str]:
    """Legacy source IDs that name the same explicit transcript identity."""

    incoming = {
        str(row.get("source_id") or ""): (
            str(row.get("source_type") or "").strip(),
            str(row.get("transcript_id") or "").strip(),
        )
        for row in package.get("source_documents") or []
        if str(row.get("source_id") or "").strip()
        and str(row.get("transcript_id") or "").strip()
    }
    if len(incoming.values()) != len(set(incoming.values())):
        raise ValueError(
            "incoming package has multiple source IDs for the same transcript identity"
        )

    incoming_identities = set(incoming.values())
    incoming_transcripts = {transcript_id for _, transcript_id in incoming_identities}

    aliases: set[str] = set()
    for source_id, row in live_documents.items():
        if source_id in incoming:
            continue
        transcript_id = str(row.get("transcript_id") or source_id).strip()
        if transcript_id not in incoming_transcripts:
            continue
        source_type = str(row.get("source_type") or "").strip()
        if not source_type or any(
            not incoming_type
            for incoming_type, incoming_transcript in incoming_identities
            if incoming_transcript == transcript_id
        ):
            raise ValueError(
                "cannot safely alias source with missing source_type: "
                f"{source_id!r} and transcript {transcript_id!r}"
            )
        if (source_type, transcript_id) in incoming_identities:
            aliases.add(source_id)
    return aliases


def transcript_predecessor_namespaces(
    package: Mapping[str, Any], live_documents: Mapping[str, Mapping[str, Any]]
) -> set[str]:
    """Every explicit extraction namespace currently serving this transcript.

    The current source id can remain stable while a new extraction generation
    arrives, so this includes both aliases and the row that will be updated.
    Exact generations come from SourceDocument provenance. The legacy compiler
    used ``transcript_id`` for direct sermon runs but a manifest ``source_id``
    for manifest runs, and the SourceDocument does not record which entry point
    produced it. Include both candidates, but only after matching the complete
    ``(source_type, transcript_id)`` identity, so a same-named source of another
    type cannot be swept into this replacement.
    """

    incoming_identities = {
        (
            str(row.get("source_type") or "").strip(),
            str(row.get("transcript_id") or "").strip(),
        )
        for row in package.get("source_documents") or []
        if str(row.get("source_type") or "").strip()
        and str(row.get("transcript_id") or "").strip()
    }
    live_types_by_transcript: dict[str, set[str]] = {}
    for source_id, row in live_documents.items():
        transcript_id = str(row.get("transcript_id") or source_id).strip()
        source_type = str(row.get("source_type") or "").strip()
        if transcript_id and source_type:
            live_types_by_transcript.setdefault(transcript_id, set()).add(source_type)
    ambiguous_transcripts = sorted(
        transcript_id
        for _source_type, transcript_id in incoming_identities
        if len(live_types_by_transcript.get(transcript_id, set())) > 1
    )
    if ambiguous_transcripts:
        raise ValueError(
            "cannot safely infer a legacy transcript namespace shared by multiple "
            "source types: " + ", ".join(ambiguous_transcripts)
        )
    result: set[str] = set()
    for source_id, row in live_documents.items():
        transcript_id = str(row.get("transcript_id") or source_id).strip()
        identity = (
            str(row.get("source_type") or "").strip(),
            transcript_id,
        )
        if identity not in incoming_identities:
            continue
        declared = str(row.get("extraction_record_namespace") or "").strip()
        if declared:
            result.add(declared)
        for legacy_key in {str(source_id).strip(), transcript_id}:
            if legacy_key:
                result.add(source_namespace(legacy_key))
    return result


def plan(
    store: PostgresKnowledgeStore,
    package: dict[str, Any],
    *,
    source_kind: str,
    retire_obsolete_candidate_batch: str | None = None,
    retire_stale_pending_topic_identity_batch: str | None = None,
    retire_stale_candidate_projection_batches: Sequence[str] = (),
    retire_stale_ai_cross_sermon_constraint_ids: Sequence[str] = (),
    retire_stale_source_cvr: str | None = None,
    return_stale_source_cvr_audit: bool = False,
):
    """The one change set that lands `package` and withdraws its predecessor.

    Returns the plan, the withdrawal, and the downstream products it invalidates.
    """

    # This is the last package boundary before PostgreSQL.  Upstream runners
    # validate their own output, but an operator can also invoke supersede
    # directly with an older artifact.  Re-run the complete graph contract
    # here so a stale package cannot bypass newer integrity gates merely by
    # entering through the ingest CLI.
    try:
        validate_merged_package(package)
    except KnowledgePackageMergeError as exc:
        raise ValueError(
            f"supersede package violates graph integrity: {exc}"
        ) from exc
    try:
        validate_store_package_authorization(package)
    except ConsensusApplicationError as exc:
        raise ValueError(
            f"supersede input lacks final review authority: {exc}"
        ) from exc

    with store.connect() as conn, conn.cursor() as cursor:
        live_documents = _live(cursor, "source_documents")
        aliases = transcript_source_aliases(package, live_documents)
        predecessor_namespaces = transcript_predecessor_namespaces(
            package, live_documents
        )
        withdrawal = superseded(
            package,
            live_fragments=_live(cursor, "source_fragments"),
            owners={name: _live(cursor, name) for name in ANCHORED_COLLECTIONS},
            claims=_live(cursor, "claims"),
            relations={name: _live(cursor, name) for name in RELATION_COLLECTIONS},
            source_alias_ids=aliases,
            predecessor_namespaces=predecessor_namespaces,
        )
        cursor.execute(
            """SELECT collection, object_id, revision, content_sha256, payload
               FROM wang_knowledge.objects WHERE retired_at IS NULL"""
        )
        current_rows = [
            (str(collection), str(object_id), int(revision), str(content_sha256), payload)
            for collection, object_id, revision, content_sha256, payload
            in cursor.fetchall()
        ]
        all_live_rows = [
            (collection, object_id, payload)
            for collection, object_id, _revision, _content_sha256, payload
            in current_rows
        ]
        record_states = {
            (collection, object_id): {
                "revision": revision,
                "content_sha256": content_sha256,
            }
            for collection, object_id, revision, content_sha256, _payload
            in current_rows
        }
        dependencies = {
            object_id: payload
            for collection, object_id, payload in all_live_rows
            if collection == "product_dependencies"
        }
        semantic_rows = [
            (collection, object_id, payload)
            for collection, object_id, payload in all_live_rows
            if collection in SEMANTIC_REFERENCE_COLLECTIONS
        ]
        obsolete_retirement = None
        obsolete_keys: list[tuple[str, str]] = []
        known_plan_ids: set[str] = set()
        if (
            retire_obsolete_candidate_batch
            or retire_stale_candidate_projection_batches
        ):
            cursor.execute(
                """SELECT object_id FROM wang_knowledge.objects
                   WHERE collection='composition_plans'"""
            )
            known_plan_ids = {str(row[0]) for row in cursor.fetchall()}
        if retire_obsolete_candidate_batch:
            candidate_live = {
                collection: {
                    object_id: payload
                    for row_collection, object_id, payload in all_live_rows
                    if row_collection == collection
                }
                for collection in OBSOLETE_CANDIDATE_RETIREMENT_COLLECTIONS
            }
            obsolete_keys, obsolete_retirement = obsolete_candidate_batch_retirement(
                batch_id=retire_obsolete_candidate_batch,
                live=candidate_live,
                all_live_rows=all_live_rows,
                known_plan_ids=known_plan_ids,
                record_states=record_states,
            )
        known_constraint_ids: set[str] = set()
        if retire_stale_ai_cross_sermon_constraint_ids:
            cursor.execute(
                """SELECT object_id FROM wang_knowledge.objects
                   WHERE collection='claim_relation_constraints'"""
            )
            known_constraint_ids = {str(row[0]) for row in cursor.fetchall()}
    keys = sorted(set(withdrawal.closure()) | set(obsolete_keys))
    change_set = store.plan_package(
        package,
        source_kind=source_kind,
        retiring_keys=keys,
    )
    validate_obsolete_retirement_plan(change_set, obsolete_retirement)
    stale_projection_retirement = None
    stale_projection_keys: list[tuple[str, str]] = []
    if retire_stale_candidate_projection_batches:
        stale_projection_keys, stale_projection_retirement = (
            stale_candidate_projection_retirement(
                batch_ids=retire_stale_candidate_projection_batches,
                change_set=change_set,
                all_live_rows=all_live_rows,
                known_plan_ids=known_plan_ids,
                record_states=record_states,
            )
        )
    stale_topic_identity_retirement = None
    stale_topic_keys: list[tuple[str, str]] = []
    if retire_stale_pending_topic_identity_batch:
        stale_topic_keys, stale_topic_identity_retirement = (
            stale_pending_topic_identity_retirement(
                batch_id=retire_stale_pending_topic_identity_batch,
                change_set=change_set,
                all_live_rows=all_live_rows,
                record_states=record_states,
            )
        )
    stale_cross_sermon_constraint_retirement = None
    stale_constraint_keys: list[tuple[str, str]] = []
    if retire_stale_ai_cross_sermon_constraint_ids:
        stale_constraint_keys, stale_cross_sermon_constraint_retirement = (
            stale_ai_cross_sermon_constraint_retirement(
                constraint_ids=retire_stale_ai_cross_sermon_constraint_ids,
                change_set=change_set,
                all_live_rows=all_live_rows,
                known_constraint_ids=known_constraint_ids,
                record_states=record_states,
            )
        )
    stale_source_cvr_retirement_audit = None
    stale_source_cvr_keys: list[tuple[str, str]] = []
    if retire_stale_source_cvr:
        stale_source_cvr_keys, stale_source_cvr_retirement_audit = (
            stale_source_cvr_retirement(
                source_id=retire_stale_source_cvr,
                change_set=change_set,
                semantic_rows=semantic_rows,
                record_states=record_states,
            )
        )
    extra_keys = {
        *stale_projection_keys,
        *stale_topic_keys,
        *stale_constraint_keys,
        *stale_source_cvr_keys,
    }
    if extra_keys:
        keys = sorted(set(keys) | extra_keys)
        change_set = store.plan_package(
            package,
            source_kind=source_kind,
            retiring_keys=keys,
        )
        validate_obsolete_retirement_plan(change_set, obsolete_retirement)
    validate_stale_candidate_projection_retirement_plan(
        change_set, stale_projection_retirement
    )
    validate_stale_topic_identity_retirement_plan(
        change_set, stale_topic_identity_retirement
    )
    validate_stale_ai_cross_sermon_constraint_retirement_plan(
        change_set, stale_cross_sermon_constraint_retirement
    )
    products = products_to_rebuild(
        product_impact_keys(change_set),
        dependencies=dependencies,
    )
    semantic_blockers = uncoordinated_semantic_reference_blockers(
        change_set, semantic_rows
    )
    result = (
        change_set,
        withdrawal,
        products,
        semantic_blockers,
        obsolete_retirement,
        stale_topic_identity_retirement,
        stale_projection_retirement,
        stale_cross_sermon_constraint_retirement,
    )
    return (*result, stale_source_cvr_retirement_audit) if return_stale_source_cvr_audit else result


def no_op_result(change_set: Any) -> dict[str, Any] | None:
    """Return a terminal result when this exact store state needs no write.

    After a replacement lands, replanning the same package sees the new rows
    as unchanged and the old rows as already retired. Its withdrawal
    fingerprint is therefore different from the first apply, so relying only
    on ChangeSet fingerprint idempotency would insert a second, empty ChangeSet.
    Zero operations means zero database writes instead.
    """

    if change_set.operations:
        return None
    return {
        "status": "unchanged",
        "change_set_id": None,
        "summary": change_set.as_dict()["summary"],
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--database-url")
    parser.add_argument("--source-kind", default="knowledge_package")
    parser.add_argument(
        "--retire-obsolete-candidate-batch",
        help=(
            "explicitly retire one internal candidate CompositionPlan batch in "
            "the same ChangeSet; refuses approved/public/external-referenced rows"
        ),
    )
    parser.add_argument(
        "--retire-stale-pending-topic-identities",
        metavar="BATCH_ID",
        help=(
            "retire only candidate/internal pending topic identity proposals from "
            "the named origin batch that cite claims retired by this re-extraction"
        ),
    )
    parser.add_argument(
        "--retire-stale-candidate-projection-batch",
        metavar="BATCH_ID",
        action="append",
        default=[],
        help=(
            "repeatable explicit allow-list; retire only candidate/internal old "
            "CompositionPlan routes and syntheses that cite claims retired by "
            "this re-extraction; plans and decisions remain historical records"
        ),
    )
    parser.add_argument(
        "--retire-stale-ai-cross-sermon-constraint",
        metavar="CONSTRAINT_ID",
        action="append",
        default=[],
        help=(
            "repeatable exact allow-list; retire an internal ai_consensus "
            "CRC-XSR constraint whose claim endpoint is retired, without "
            "retargeting the old judgment"
        ),
    )
    parser.add_argument(
        "--retire-stale-source-cvr",
        metavar="SOURCE_ID",
        help=(
            "retire the exact internal source-bound CVR links, route attestations, "
            "and unresolved identity candidates invalidated by this source generation; "
            "canonical viewpoints and route revisions remain active"
        ),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--transcript-dir",
        action="append",
        type=Path,
        dest="transcript_dirs",
        help=(
            "repeatable current transcript root used for the final provenance "
            "read before planning or applying"
        ),
    )
    args = parser.parse_args(argv)

    original_package = json.loads(args.package.read_text(encoding="utf-8"))
    if original_package.get("consensus_application") is not None:
        try:
            validate_reviewed_candidate_artifact(original_package)
        except ConsensusApplicationError as exc:
            raise ValueError(
                f"supersede reviewed candidate violates artifact integrity: {exc}"
            ) from exc
    elif args.apply:
        raise ValueError(
            "supersede --apply requires a fully reviewed consensus candidate"
        )
    package, relation_id_migration = migrate_legacy_cross_section_relation_ids(
        original_package
    )
    if original_package.get("consensus_application") is not None:
        package = reseal_after_relation_id_migration(
            original_package, package, relation_id_migration
        )
    try:
        validate_package_current_source_provenance(
            package, args.transcript_dirs or DEFAULT_TRANSCRIPT_DIRS
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"supersede package no longer matches its current source: {exc}"
        ) from exc
    store = PostgresKnowledgeStore(args.database_url)
    (
        change_set,
        withdrawal,
        products,
        semantic_blockers,
        obsolete_retirement,
        stale_topic_identity_retirement,
        stale_projection_retirement,
        stale_cross_sermon_constraint_retirement,
        stale_source_cvr_retirement_audit,
    ) = plan(
        store,
        package,
        source_kind=args.source_kind,
        retire_obsolete_candidate_batch=args.retire_obsolete_candidate_batch,
        retire_stale_pending_topic_identity_batch=(
            args.retire_stale_pending_topic_identities
        ),
        retire_stale_candidate_projection_batches=(
            args.retire_stale_candidate_projection_batch
        ),
        retire_stale_ai_cross_sermon_constraint_ids=(
            args.retire_stale_ai_cross_sermon_constraint
        ),
        retire_stale_source_cvr=args.retire_stale_source_cvr,
        return_stale_source_cvr_audit=True,
    )
    claim_evidence_guard = (
        store.read_claim_evidence_reciprocity_guard(change_set)
        if change_set.operations
        else None
    )
    claim_evidence_snapshot = (
        {
            "guard_sha256": claim_evidence_guard["guard_sha256"],
            "snapshot_sha256": claim_evidence_guard[
                "expected_active_snapshot"
            ]["snapshot_sha256"],
            "counts": claim_evidence_guard["expected_active_snapshot"]["counts"],
        }
        if claim_evidence_guard is not None
        else None
    )
    output: dict[str, Any] = {
        "package": str(args.package),
        "sources": sorted(package_source_ids(package)),
        "supersedes": withdrawal.as_dict(),
        "change_set_id": change_set.change_set_id,
        "summary": change_set.as_dict()["summary"],
        "relation_id_namespace_migration": relation_id_migration,
        "upstream_reviewed_candidate_artifact_sha256": (
            (original_package.get("consensus_application") or {}).get(
                "artifact_sha256"
            )
        ),
        "effective_reviewed_candidate_artifact_sha256": (
            (package.get("consensus_application") or {}).get("artifact_sha256")
        ),
        # The only thing a person has to act on: new material means every
        # current downstream consumer bound to the old records gets rebuilt.
        "products_to_rebuild": products,
        "semantic_rebind_required": bool(semantic_blockers),
        "semantic_references_to_rebind": semantic_blockers,
        "current_claim_evidence_snapshot": claim_evidence_snapshot,
    }
    if obsolete_retirement is not None:
        output["obsolete_candidate_batch_retirement"] = obsolete_retirement
    if stale_topic_identity_retirement is not None:
        output["stale_pending_topic_identity_retirement"] = (
            stale_topic_identity_retirement
        )
    if stale_projection_retirement is not None:
        output["stale_candidate_projection_retirement"] = (
            stale_projection_retirement
        )
    if stale_cross_sermon_constraint_retirement is not None:
        output["stale_ai_cross_sermon_constraint_retirement"] = (
            stale_cross_sermon_constraint_retirement
        )
    if stale_source_cvr_retirement_audit is not None:
        output["stale_source_cvr_retirement"] = stale_source_cvr_retirement_audit
    if args.apply:
        if semantic_blockers:
            output["result"] = {
                "status": "blocked",
                "reason": "coordinated CVR update required",
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 2
        unchanged = no_op_result(change_set)
        if unchanged is not None:
            output["result"] = unchanged
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        # Only a run that writes files a row. Planning is a question anybody may
        # ask and one row per question would bury the writes among them. The
        # row matters more since this became the batch runner's ingest stage:
        # without it a re-extracted source reaches the store with nothing in
        # the ledger saying so, which is the state the overview exists to stop.
        # The row key, not the package's `source_id`: for a sermon those are
        # different strings, and extraction files under the row key.
        row_key = package_row_key(package)
        with run_record(
            subject=row_key or str(args.package.name),
            stage="ingest",
            subject_kind="source" if row_key else "batch",
            sources=[row_key] if row_key else sorted(package_source_ids(package)),
        ) as record:
            output["result"] = store.apply_plan(
                change_set,
                metadata={
                    "input_path": str(args.package),
                    "supersedes": withdrawal.as_dict(),
                    "relation_id_namespace_migration": relation_id_migration,
                    "upstream_reviewed_candidate_artifact_sha256": output.get(
                        "upstream_reviewed_candidate_artifact_sha256"
                    ),
                    "effective_reviewed_candidate_artifact_sha256": output.get(
                        "effective_reviewed_candidate_artifact_sha256"
                    ),
                    "obsolete_candidate_batch_retirement": obsolete_retirement,
                    "stale_pending_topic_identity_retirement": (
                        stale_topic_identity_retirement
                    ),
                    "stale_candidate_projection_retirement": (
                        stale_projection_retirement
                    ),
                    "stale_ai_cross_sermon_constraint_retirement": (
                        stale_cross_sermon_constraint_retirement
                    ),
                    "stale_source_cvr_retirement": (
                        stale_source_cvr_retirement_audit
                    ),
                    "current_claim_evidence_snapshot": claim_evidence_snapshot,
                },
                expected_claim_evidence_guard=claim_evidence_guard,
            )
            record.quality({
                "status": (output["result"] or {}).get("status"),
                **{key: value for key, value in (output.get("summary") or {}).items()},
            })
            record.metadata({
                "change_set_id": output.get("change_set_id"),
                "products_to_rebuild": products,
                "relation_id_namespace_migration": relation_id_migration,
                "obsolete_candidate_batch_retirement": obsolete_retirement,
                "stale_pending_topic_identity_retirement": (
                    stale_topic_identity_retirement
                ),
                "stale_candidate_projection_retirement": (
                    stale_projection_retirement
                ),
                "stale_ai_cross_sermon_constraint_retirement": (
                    stale_cross_sermon_constraint_retirement
                ),
                "stale_source_cvr_retirement": (
                    stale_source_cvr_retirement_audit
                ),
                "current_claim_evidence_snapshot": claim_evidence_snapshot,
            })
            record.input_artifacts(args.package)
            record.outputs(args.package)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
