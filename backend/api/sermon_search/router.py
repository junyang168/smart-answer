from __future__ import annotations

from typing import List

from fastapi import APIRouter

from .models import (
    IndexStatus,
    ReindexRequest,
    ReindexResponse,
    SermonSearchRequest,
    SermonSearchResponse,
    SourceCard,
)
from .service import sermon_search_service


router = APIRouter(prefix="/sermon_search", tags=["sermon-search"])
compat_router = APIRouter(tags=["sermon-search"])


@router.get("/status", response_model=IndexStatus)
def status() -> IndexStatus:
    return sermon_search_service.status()


@router.post("/reindex", response_model=ReindexResponse)
def reindex(payload: ReindexRequest) -> ReindexResponse:
    return sermon_search_service.reindex(payload)


@router.post("/query", response_model=SermonSearchResponse)
def query(payload: SermonSearchRequest) -> SermonSearchResponse:
    return sermon_search_service.query(payload)


@compat_router.get("/semantic_search/{q}", response_model=List[SourceCard])
def semantic_search_compat(q: str) -> List[SourceCard]:
    response = sermon_search_service.query(SermonSearchRequest(question=q))
    return response.sources

