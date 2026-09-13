"""Retire the extraction a new one replaces in the ChangeSet that lands it.

Model-local ordinals such as ``E001`` and ``CL007`` are not record identity.
New packages place them in an exact generation namespace; predecessor
namespaces and source-fragment ownership make the complete old generation
explicit. Left alone, the store would hold both generations live, with no
field saying which one replaced which.

The predecessor is found in two independent ways. Its fragments seed the
dependency closure, while its generation namespace finds records that an old
or malformed package left unreachable from a fragment. Agreement is not
assumed: the union is retired atomically with the arrival, excluding every key
the incoming package itself carries.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from backend.pipeline.record_withdrawal import Withdrawal, closure_from_fragments
from backend.pipeline.relation_id_namespace import (
    is_namespaced_extraction_relation_id,
    package_record_namespace,
)
#: Where each collection keeps its object id, for the collections a knowledge
#: package can carry. `source_fragments` is handled separately and earlier.
PACKAGE_ID_FIELDS = {
    "questions": "question_id",
    "position_nodes": "position_id",
    "observations": "observation_id",
    "evidence_steps": "evidence_step_id",
    "claims": "claim_id",
    "knowledge_relations": "relation_id",
    "claim_relations": "claim_relation_id",
}
RELATION_COLLECTIONS = {"knowledge_relations", "claim_relations"}
GENERATED_RECORD_SUFFIX = {
    # Historical models occasionally appended one discriminator letter when
    # they split a numbered object (E020G/E020H, OBS006A, CL020H). The old
    # compiler still prefixed those IDs with the source generation namespace.
    # Match that observed dialect precisely; a broad namespace-prefix delete
    # could sweep up later curated IDs such as ``...-MERGED-001``.
    "questions": r"Q\d+[A-Z]?",
    "position_nodes": r"POS\d+[A-Z]?",
    "observations": r"OBS\d+[A-Z]?",
    "evidence_steps": r"E\d+[A-Z]?",
    "claims": r"CL\d+[A-Z]?",
}


def arriving_keys(package: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Every record the package carries, as the store keys them."""

    keys: set[tuple[str, str]] = set()
    for collection, id_field in PACKAGE_ID_FIELDS.items():
        for row in package.get(collection) or []:
            object_id = str(row.get(id_field) or "")
            if object_id:
                keys.add((collection, object_id))
    return keys


def package_source_ids(package: Mapping[str, Any]) -> set[str]:
    """The sources this package speaks for.

    Read from `source_documents` rather than from the fragments, because a
    package that carries no document for a source is not claiming to replace
    that source's records and must not retire them.
    """

    return {
        str(row.get("source_id"))
        for row in (package.get("source_documents") or [])
        if row.get("source_id")
    }


def superseded(
    package: Mapping[str, Any],
    *,
    live_fragments: Mapping[str, Mapping[str, Any]],
    owners: Mapping[str, Mapping[str, Mapping[str, Any]]],
    claims: Mapping[str, Mapping[str, Any]],
    relations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    source_alias_ids: set[str] | None = None,
    predecessor_namespaces: set[str] | None = None,
) -> Withdrawal:
    """What this package replaces: live records of its sources that it does not carry.

    Scoped to the package's own sources, and to records the package does not
    itself contain. A fragment the new extraction happens to reproduce with
    the same id is an update, not a casualty, so it stays out of the closure
    and `ingest_package` handles it as it always did.
    """

    incoming_sources = package_source_ids(package)
    aliases = set(source_alias_ids or ()) - incoming_sources
    sources = incoming_sources | aliases
    arriving = {
        str(row.get("fragment_id"))
        for row in (package.get("source_fragments") or [])
        if row.get("fragment_id")
    }
    replaced = {
        fragment_id: str(payload.get("source_id") or "")
        for fragment_id, payload in live_fragments.items()
        if str(payload.get("source_id") or "") in sources and fragment_id not in arriving
    }
    withdrawal = closure_from_fragments(
        replaced, owners=owners, claims=claims, relations=relations
    )
    withdrawal.superseded_sources.extend(sorted(aliases))
    # Fragment reachability alone is insufficient. A malformed predecessor can
    # contain an unanchored question/position, and a cross-section edge can keep
    # both endpoints while being replaced by another model response. Exact
    # generation ownership closes both gaps without inferring ownership merely
    # because endpoints happen to be local to this sermon.
    incoming_keys = arriving_keys(package)
    declared_extraction_namespace = str(
        (package.get("extraction") or {}).get("record_namespace") or ""
    ).strip()
    declared_parent_namespace = str(
        (package.get("cross_section_relations") or {}).get(
            "parent_extraction_record_namespace"
        ) or ""
    ).strip()
    if (
        declared_extraction_namespace
        and declared_parent_namespace
        and declared_extraction_namespace != declared_parent_namespace
    ):
        raise ValueError(
            "cross-section parent namespace does not match extraction namespace"
        )
    try:
        package_namespace = package_record_namespace(package)
    except ValueError:
        package_namespace = ""
    incoming_extraction_namespace = (
        declared_extraction_namespace or declared_parent_namespace or package_namespace
    )
    if predecessor_namespaces is None:
        predecessor_namespaces = {
            value for value in (incoming_extraction_namespace,) if value
        }
    namespaces = {
        str(value) for value in predecessor_namespaces if str(value)
    }
    incoming_cross_section_namespace = str(
        (package.get("cross_section_relations") or {}).get("record_namespace") or ""
    ).strip()

    def owned_by_predecessor(
        collection: str, object_id: str, payload: Mapping[str, Any]
    ) -> bool:
        record_namespace = str(payload.get("record_namespace") or "").strip()
        extraction_namespace = str(
            payload.get("extraction_record_namespace") or ""
        ).strip()
        parent_namespace = str(
            payload.get("parent_extraction_record_namespace") or ""
        ).strip()
        if {record_namespace, extraction_namespace} & namespaces:
            return True
        if parent_namespace in namespaces:
            # A child cross-section generation belongs to its parent only when
            # the parent itself is being superseded. Replaying extraction G1
            # must leave its still-current child X1 alone. A new cross-section
            # response X2 for the same G1 does supersede X1, while replaying X1
            # remains an exact no-op because it arrives under the same child
            # namespace.
            if parent_namespace != incoming_extraction_namespace:
                return True
            if (
                incoming_cross_section_namespace
                and record_namespace
                and record_namespace != incoming_cross_section_namespace
            ):
                return True
        if collection in RELATION_COLLECTIONS:
            matching_namespaces = {
                namespace
                for namespace in namespaces
                if is_namespaced_extraction_relation_id(namespace, object_id)
            }
            if not matching_namespaces:
                return False
            # Before cross-section received its own child namespace, XER/XCR
            # ids were minted directly under the extraction namespace and did
            # not carry a parent field. An exact extraction replay without a
            # cross-section result must not delete that still-current child.
            # A new parent generation or an explicit new cross-section child
            # does replace it.
            belongs_to_older_parent = any(
                namespace != incoming_extraction_namespace
                for namespace in matching_namespaces
            )
            if belongs_to_older_parent:
                return True
            legacy_cross_section = any(
                re.fullmatch(
                    rf"{re.escape(namespace)}-(?:P\d+-)?X(?:ER|CR)\d+",
                    object_id,
                )
                is not None
                for namespace in matching_namespaces
            )
            if legacy_cross_section and not incoming_cross_section_namespace:
                return False
            return True
        suffix = GENERATED_RECORD_SUFFIX.get(collection)
        if suffix is None:
            return False
        return any(
            re.fullmatch(
                rf"{re.escape(namespace)}-(?:P\d+-)?{suffix}", object_id
            )
            is not None
            for namespace in namespaces
        )

    generation_rows: dict[str, Mapping[str, Mapping[str, Any]]] = {
        **{collection: rows for collection, rows in owners.items()},
        "claims": claims,
        **dict(relations or {}),
    }
    existing_closure = set(withdrawal.closure())
    for collection, rows in generation_rows.items():
        for object_id, payload in rows.items():
            key = (collection, str(object_id))
            if key in incoming_keys or key in existing_closure:
                continue
            if not owned_by_predecessor(collection, str(object_id), payload):
                continue
            if collection in RELATION_COLLECTIONS:
                withdrawal.superseded_relations.append(key)
            else:
                withdrawal.superseded_records.append(key)
    # The same rule the fragments already got, applied to everything the
    # closure walked to. A record the new extraction reproduces under the same
    # id is an update; retiring it in the change set that writes it makes the
    # change set conflict with itself.
    return withdrawal.excluding(incoming_keys)
