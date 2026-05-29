from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

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


@router.post("/query_stream")
def query_stream(payload: SermonSearchRequest) -> StreamingResponse:
    def events():
        for event in sermon_search_service.stream_query_events(payload):
            event_type = str(event.get("type") or "message")
            yield f"event: {event_type}\n"
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@compat_router.get("/semantic_search/{q}", response_model=List[SourceCard])
def semantic_search_compat(q: str) -> List[SourceCard]:
    response = sermon_search_service.query(SermonSearchRequest(question=q))
    return response.sources
