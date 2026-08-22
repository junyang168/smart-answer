"""Read-only HTTP boundary for the CanonicalViewpoint admin workbench."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .canonical_repository.service import canonical_repository_service
from .canonical_repository.viewpoint_admin_projection import (
    AdminViewpointProjectionCompiler,
    AdminViewpointProjectionError,
)
from .canonical_repository.viewpoint_resolution import ViewpointExceptionQueueArtifact


router = APIRouter(prefix="/admin/wang", tags=["wang-admin-viewpoints"])


def _exception_queue() -> ViewpointExceptionQueueArtifact | None:
    """Read one explicitly configured, SHA-valid queue; never scan artifacts."""

    configured = os.getenv("WANG_VIEWPOINT_EXCEPTION_QUEUE_FILE")
    if not configured:
        return None
    path = Path(configured).resolve()
    if not path.is_file():
        raise AdminViewpointProjectionError("configured exception queue does not exist")
    return ViewpointExceptionQueueArtifact.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _compiler() -> AdminViewpointProjectionCompiler:
    return AdminViewpointProjectionCompiler(
        canonical_repository_service.store,
        exception_queue=_exception_queue(),
    )


def _run(method: str, *args, **kwargs):
    try:
        return getattr(_compiler(), method)(*args, **kwargs)
    except AdminViewpointProjectionError as exc:
        status = 400 if "cursor" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"Viewpoint projection unavailable: {exc}") from exc


@router.get("/viewpoints/overview")
def viewpoint_overview(coverage_snapshot_id: str | None = None):
    return _run("overview", coverage_snapshot_id)


@router.get("/viewpoints")
def list_viewpoints(
    coverage_snapshot_id: str | None = None,
    q: str | None = None,
    topic_id: str | None = None,
    scripture: str | None = None,
    review_status: str | None = None,
    impact: bool = False,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
):
    return _run(
        "list_viewpoints",
        coverage_snapshot_id=coverage_snapshot_id,
        q=q,
        topic_id=topic_id,
        scripture=scripture,
        review_status=review_status,
        impact_only=impact,
        cursor=cursor,
        limit=limit,
    )


@router.get("/viewpoints/{viewpoint_id}")
def viewpoint_detail(
    viewpoint_id: str,
    coverage_snapshot_id: str | None = None,
    registry_snapshot_id: str | None = None,
):
    return _run(
        "detail",
        viewpoint_id,
        coverage_snapshot_id=coverage_snapshot_id,
        registry_snapshot_id=registry_snapshot_id,
    )


@router.get("/viewpoints/{viewpoint_id}/lineage")
def viewpoint_lineage(
    viewpoint_id: str,
    coverage_snapshot_id: str | None = None,
    registry_snapshot_id: str | None = None,
):
    return _run(
        "lineage", viewpoint_id,
        coverage_snapshot_id=coverage_snapshot_id,
        registry_snapshot_id=registry_snapshot_id,
    )


@router.get("/viewpoints/{viewpoint_id}/impact")
def viewpoint_impact(
    viewpoint_id: str,
    coverage_snapshot_id: str | None = None,
    registry_snapshot_id: str | None = None,
):
    return _run(
        "impact", viewpoint_id,
        coverage_snapshot_id=coverage_snapshot_id,
        registry_snapshot_id=registry_snapshot_id,
    )


@router.get("/viewpoint-exceptions")
def list_viewpoint_exceptions(
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
):
    return _run("exceptions", cursor=cursor, limit=limit)


@router.get("/viewpoint-exceptions/{bundle_id}")
def viewpoint_exception_detail(bundle_id: str):
    return _run("exception_detail", bundle_id)
