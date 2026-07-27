from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Literal, Optional

from pydantic import BaseModel

from backend.api.sermon_search.discovery import DEFAULT_MANUSCRIPT_PROJECT_TYPES
from backend.api.sermon_search.models import ReindexRequest
from backend.api.sermon_search.service import sermon_search_service
from backend.pipeline.topic_index.pipeline import run_topic_index_pipeline


class SeriesIndexRefreshStatus(BaseModel):
    series_id: str
    status: Literal["idle", "queued", "running", "completed", "failed"] = "idle"
    message: str = "Index has not been refreshed from this page yet."
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    topic_count: Optional[int] = None
    documents_indexed: Optional[int] = None
    source_units_indexed: Optional[int] = None


_state_lock = Lock()
_statuses: dict[str, SeriesIndexRefreshStatus] = {}
_active_series_id: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_series_index_refresh_status(series_id: str) -> SeriesIndexRefreshStatus:
    with _state_lock:
        status = _statuses.get(series_id)
        if status is None:
            return SeriesIndexRefreshStatus(series_id=series_id)
        return status.model_copy(deep=True)


def queue_series_index_refresh(series_id: str) -> tuple[SeriesIndexRefreshStatus, bool]:
    global _active_series_id
    with _state_lock:
        if _active_series_id is not None:
            active = _statuses[_active_series_id]
            return active.model_copy(deep=True), False

        status = SeriesIndexRefreshStatus(
            series_id=series_id,
            status="queued",
            message="Index refresh queued.",
            started_at=_now(),
        )
        _statuses[series_id] = status
        _active_series_id = series_id
        return status.model_copy(deep=True), True


def run_series_index_refresh(series_id: str) -> None:
    global _active_series_id
    with _state_lock:
        status = _statuses[series_id]
        status.status = "running"
        status.message = "Extracting chapter and topic entries…"

    try:
        topic_index = run_topic_index_pipeline(
            series_ids=[series_id],
            project_types=DEFAULT_MANUSCRIPT_PROJECT_TYPES,
        )

        with _state_lock:
            status = _statuses[series_id]
            status.topic_count = len(topic_index.topics)
            status.message = "Refreshing manuscript search…"

        # Preserve semantic search when the existing production index uses it.
        include_embeddings = sermon_search_service.status().embedding_enabled
        search_result = sermon_search_service.reindex(
            ReindexRequest(
                project_types=DEFAULT_MANUSCRIPT_PROJECT_TYPES,
                include_embeddings=include_embeddings,
            )
        )
        if search_result.status != "completed":
            raise RuntimeError(f"Search refresh returned {search_result.status}")

        with _state_lock:
            status = _statuses[series_id]
            status.status = "completed"
            status.message = "Chapter, topic, and search indexes are up to date."
            status.finished_at = _now()
            status.documents_indexed = search_result.documents_indexed
            status.source_units_indexed = search_result.source_units_indexed
    except Exception as exc:
        with _state_lock:
            status = _statuses[series_id]
            status.status = "failed"
            status.message = str(exc)
            status.finished_at = _now()
    finally:
        with _state_lock:
            _active_series_id = None
