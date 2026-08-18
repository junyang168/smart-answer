"""Serve the argument layer of one source at a time, straight from the store.

`backend.pipeline.argument_layer_view` builds the same data for a standalone
file.  This router exists so the reviewer does not have to regenerate anything:
the store is the authority, and what the page shows is what it holds right now.

Nothing here writes.  A decision recorded against a claim goes through
`record_review` in the canonical repository, not through this view.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    PostgresKnowledgeStoreError,
    database_url_from_env,
)
from backend.pipeline.argument_layer_view import ArgumentLayerReader

router = APIRouter(prefix="/admin/argument-layer", tags=["argument-layer-admin"])

# Building the whole corpus reads every node, edge and fragment, so the result
# is held until the store changes.  The fingerprint is one cheap query, which
# keeps a stale page impossible rather than merely unlikely.
_CACHE: dict[str, Any] = {"fingerprint": None, "data": None}


def _store() -> PostgresKnowledgeStore:
    try:
        return PostgresKnowledgeStore(database_url_from_env())
    except PostgresKnowledgeStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _fingerprint(store: PostgresKnowledgeStore) -> tuple:
    with store.connect() as conn:
        row = conn.execute(
            """SELECT count(*), max(updated_at) FROM wang_knowledge.objects
                UNION ALL
               SELECT count(*), max(updated_at) FROM wang_knowledge.edges"""
        ).fetchall()
    return tuple((int(count), str(updated)) for count, updated in row)


def _data() -> dict[str, Any]:
    store = _store()
    try:
        fingerprint = _fingerprint(store)
        if _CACHE["fingerprint"] != fingerprint:
            _CACHE["data"] = ArgumentLayerReader(store).build()
            _CACHE["fingerprint"] = fingerprint
    except PostgresKnowledgeStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _CACHE["data"]


def _summary(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: source[key]
        for key in ("key", "title", "note", "source_type", "source_ids", "stats")
    }


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """Every source with its counts — enough for the overview, without the text."""
    data = _data()
    return {
        "lanes": data["lanes"],
        "totals": data["totals"],
        "sources": [_summary(source) for source in data["sources"]],
    }


@router.get("/search")
def search(q: str, limit: int = 40) -> dict[str, Any]:
    """Find a node anywhere in the corpus.

    A reviewer looking for what the professor said about 「人子」 has no way to
    know which of twenty-four sources to open first, so the search has to cross
    sources; scanning server-side keeps the page from having to hold all of it.
    """
    needle = q.strip()
    if not needle:
        return {"query": q, "total": 0, "hits": []}
    folded = needle.casefold()
    data = _data()
    kinds = (
        ("claim", "claims"),
        ("step", "steps"),
        ("question", "questions"),
        ("observation", "observations"),
        ("position", "positions"),
    )

    # An id typed in full is a request for one node, so it outranks the dozens
    # of statements that merely contain the same characters.  `E017` is a label
    # every source reuses, so a bare label stays a match rather than a jump.
    EXACT, PARTIAL_ID, TEXT = 0, 1, 2

    def rank(item: dict[str, Any], statement: str) -> int | None:
        item_id = str(item["id"]).casefold()
        label = str(item.get("label", "")).casefold()
        if folded in {item_id, label}:
            return EXACT
        if folded in item_id or folded in label:
            return PARTIAL_ID
        if folded in statement.casefold():
            return TEXT
        return None

    ranked: list[tuple[int, dict[str, Any]]] = []
    for source in data["sources"]:
        for kind, collection in kinds:
            for item in source[collection]:
                statement = item.get("statement", "")
                score = rank(item, statement)
                if score is None:
                    continue
                ranked.append(
                    (
                        score,
                        {
                            "kind": kind,
                            "id": item["id"],
                            "label": item["label"],
                            "statement": statement,
                            "source_key": source["key"],
                            "source_title": source["title"],
                        },
                    )
                )
    # A stable sort keeps claims ahead of the steps that produced them.
    ranked.sort(key=lambda entry: entry[0])
    hits = [entry[1] for entry in ranked]
    return {"query": q, "total": len(hits), "hits": hits[: max(1, min(limit, 200))]}


@router.get("/sources/{source_key}")
def source_detail(source_key: str) -> dict[str, Any]:
    data = _data()
    for source in data["sources"]:
        if source["key"] == source_key:
            return {"lanes": data["lanes"], "source": source}
    raise HTTPException(status_code=404, detail=f"沒有這個來源：{source_key}")
