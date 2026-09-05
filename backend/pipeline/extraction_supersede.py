"""Retire the extraction a new one replaces, in the change set that lands it.

Ingesting a re-extraction does not overwrite its predecessor. Record ids are
derived from the ids of the records that produced them -- `E001`, `CL007` --
and a second extraction of the same source renumbers all of them, so
`ingest_package` upserts nothing and simply adds. Measured on the two packages
that prompted this: 0 of 185 and 0 of 317 incoming fragment ids already
existed in the store. Left alone, the store holds both extractions of the same
source, live, with no field that says which one replaced which.

So the withdrawal has to happen in the same change set as the arrival, and it
is computed the same way as any other: the predecessor's fragments are the
seed, and `record_withdrawal` closes it over what depended on them.

"0 of 185 and 0 of 317 already existed" was measured on a first sectioned
re-extraction, where the predecessor was a whole-document package: its ids
were `CL007`, the new ones `P01-CL007`, and the two generations could not
collide. They do collide from the second sectioned re-extraction onward --
same sections, same numbering -- and the first source to reach that point
shared 82 of 93 evidence-step ids with its predecessor. Such a record arrives
and is withdrawn in the same change set: the arrival writes it, the retirement
still expects the sha it had before, and the whole change set aborts on its own
work. Anything the package carries is therefore an update and never a casualty,
whichever way the closure reaches it.
"""

from __future__ import annotations

from typing import Any, Mapping

from backend.pipeline.record_withdrawal import Withdrawal, closure_from_fragments
from backend.pipeline.relation_id_namespace import (
    is_source_extraction_relation_id,
    package_source_key,
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
) -> Withdrawal:
    """What this package replaces: live records of its sources that it does not carry.

    Scoped to the package's own sources, and to records the package does not
    itself contain. A fragment the new extraction happens to reproduce with
    the same id is an update, not a casualty, so it stays out of the closure
    and `ingest_package` handles it as it always did.
    """

    sources = package_source_ids(package)
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
    # Relations are repository objects too. Cross-section v2 exposed why they
    # cannot be retired only as collateral damage from removed fragments: a
    # re-run can keep every fragment and endpoint while replacing a source-
    # local edge id (for example XER001) with its globally namespaced form.
    # In that case fragment closure is empty and the predecessor edge otherwise
    # remains live forever.
    #
    # Endpoint locality is not ownership: a later curated relation can connect
    # two records from this source without belonging to extraction.  The ID
    # dialect is the explicit ownership marker, so only a relation in this
    # source's extraction namespace can be superseded here.  One-time bare v2
    # IDs are handled by the incident migration, not inferred from endpoints.
    incoming_keys = arriving_keys(package)
    source_key = package_source_key(package)
    for collection, rows in (relations or {}).items():
        id_field = PACKAGE_ID_FIELDS.get(collection)
        if id_field is None:
            continue
        for relation_id in rows:
            key = (collection, str(relation_id))
            if key in incoming_keys or key in withdrawal.dangling_relations:
                continue
            if is_source_extraction_relation_id(source_key, relation_id):
                withdrawal.superseded_relations.append(key)
    # The same rule the fragments already got, applied to everything the
    # closure walked to. A record the new extraction reproduces under the same
    # id is an update; retiring it in the change set that writes it makes the
    # change set conflict with itself.
    return withdrawal.excluding(incoming_keys)
