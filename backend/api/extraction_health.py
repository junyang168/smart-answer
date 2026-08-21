"""Serve the corpus-wide extraction health view.

`/admin/wang` answers "which sources have been run".  This router answers "is
there anything I should be looking at", and it answers it from the packages and
reviews already written to disk -- no model is called, and nothing here is a
gate: the numbers route attention, they do not block publication.

Everything is read fresh.  Measuring all thirty-seven packages costs a fraction
of a second, and the two inputs that change -- the staging tree and the sermon
catalog -- change independently of each other, so a cache keyed on either would
keep showing a document as healthy after it was re-extracted, which is the one
failure this view exists to catch.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.config import DATA_BASE_PATH, WANG_PLATFORM_PATHS
from backend.api.wang_operations import corpus_rows
from backend.pipeline import extraction_health

router = APIRouter(prefix="/admin/extraction-health", tags=["extraction-health-admin"])


@router.get("")
def health_report() -> dict[str, Any]:
    """The whole corpus: what was measured, what was never run, what stands out."""

    return extraction_health.build_report(
        staging_root=WANG_PLATFORM_PATHS.claim_layer_staging,
        # The same enumeration the overview table counts its rows from, so the
        # two pages cannot report a different-sized corpus.
        corpus=extraction_health.corpus_documents(
            corpus_rows(WANG_PLATFORM_PATHS, DATA_BASE_PATH)
        ),
    )


@router.get("/documents/{argument_layer_key}")
def document_findings(argument_layer_key: str) -> dict[str, Any]:
    """The records one exception is pointing at, by their ids.

    The argument layer draws these; it does not decide which of them are
    stranded.  That decision is made once, here, so the two pages cannot
    report different records for the same document.
    """

    payload = extraction_health.document_findings(
        WANG_PLATFORM_PATHS.claim_layer_staging, argument_layer_key
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"沒有這個來源的抽取包：{argument_layer_key}")
    return payload
