"""Ingest a re-extraction and retire the extraction it replaces, in one change set.

Planning is the default and `--apply` is opt-in, for the same reason the rest
of this store works that way: seeing what a change set would do is a question
anyone may ask, and changing the authoring authority is a decision somebody
makes once.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
)
from backend.pipeline.extraction_supersede import package_source_ids, superseded
from backend.pipeline.source_keys import package_row_key
from backend.pipeline.run_ledger import run_record
from backend.pipeline.record_withdrawal import ANCHORED_COLLECTIONS
from backend.pipeline.relation_id_namespace import (
    migrate_legacy_cross_section_relation_ids,
    source_namespace,
)

RELATION_COLLECTIONS = ("claim_relations", "knowledge_relations")

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


def plan(store: PostgresKnowledgeStore, package: dict[str, Any], *, source_kind: str):
    """The one change set that lands `package` and withdraws its predecessor.

    Returns the plan, the withdrawal, and the downstream products it invalidates.
    """

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
        dependencies = _live(cursor, "product_dependencies")
    keys = withdrawal.closure()
    change_set = store.plan_package(
        package,
        source_kind=source_kind,
        retiring_keys=keys,
    )
    products = products_to_rebuild(
        product_impact_keys(change_set),
        dependencies=dependencies,
    )
    return change_set, withdrawal, products


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
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    original_package = json.loads(args.package.read_text(encoding="utf-8"))
    package, relation_id_migration = migrate_legacy_cross_section_relation_ids(
        original_package
    )
    store = PostgresKnowledgeStore(args.database_url)
    change_set, withdrawal, products = plan(store, package, source_kind=args.source_kind)
    output: dict[str, Any] = {
        "package": str(args.package),
        "sources": sorted(package_source_ids(package)),
        "supersedes": withdrawal.as_dict(),
        "change_set_id": change_set.change_set_id,
        "summary": change_set.as_dict()["summary"],
        "relation_id_namespace_migration": relation_id_migration,
        # The only thing a person has to act on: new material means every
        # current downstream consumer bound to the old records gets rebuilt.
        "products_to_rebuild": products,
    }
    if args.apply:
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
                },
            )
            record.quality({
                "status": (output["result"] or {}).get("status"),
                **{key: value for key, value in (output.get("summary") or {}).items()},
            })
            record.metadata({
                "change_set_id": output.get("change_set_id"),
                "products_to_rebuild": products,
                "relation_id_namespace_migration": relation_id_migration,
            })
            record.outputs(args.package)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
