"""Serve one source's text with the claim-layer records anchored into it.

The reviewer question this answers is not "what does this source argue" —
`/admin/argument-layer` answers that — but "how much of what the professor
actually said ever reached the argument at all".  Answering it needs the source
text as the denominator, so this router ships the segments themselves.

Nothing here writes, and nothing here is the ledger's gate.  It is the
report-mode view `source_to_claim_layer_ledger_v1` §十六 asks for before any
gate is switched on.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    PostgresKnowledgeStoreError,
    database_url_from_env,
)
from backend.api import source_coverage_catalog
from backend.api.config import DATA_BASE_PATH
from backend.pipeline.source_coverage_view import SourceCoverageReader

router = APIRouter(prefix="/admin/source-coverage", tags=["source-coverage-admin"])

# Nothing here is cached.  Coverage depends on two things that change
# independently — the store and the source files on disk — so a cache keyed on
# the store alone would keep showing a source as intact after it was edited,
# which is the one failure this view exists to catch.  Reading all twenty-five
# sources costs well under a tenth of a second, so there is nothing to buy.


def _reader() -> SourceCoverageReader:
    try:
        return SourceCoverageReader(PostgresKnowledgeStore(database_url_from_env()), DATA_BASE_PATH)
    except PostgresKnowledgeStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _totals(sources: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for source in sources:
        for key, value in source["stats"].items():
            totals[key] = totals.get(key, 0) + value
    totals["sources"] = len(sources)
    totals["sources_unreadable"] = sum(1 for source in sources if source["file_state"] == "missing")
    totals["sources_drifted"] = sum(1 for source in sources if source["file_state"] == "drifted")
    return totals


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """Every source with its coverage counts, without any of the text."""
    reader = _reader()
    try:
        corpus = reader.load()
        sources = [reader.build(source_id, corpus)["source"] for source_id in sorted(corpus["documents"])]
    except PostgresKnowledgeStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    catalog = source_coverage_catalog.build(sources, DATA_BASE_PATH)
    totals = {**_totals(sources), **catalog["totals"]}
    return {"sources": sources, "totals": totals, "catalog": catalog["entries"]}


@router.get("/sources/{source_id:path}")
def source_detail(source_id: str) -> dict[str, Any]:
    """One source: its segments, the fragments placed on them, and the claims."""
    reader = _reader()
    try:
        return reader.build(source_id, reader.load())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"沒有這個來源：{source_id}") from exc
    except PostgresKnowledgeStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
