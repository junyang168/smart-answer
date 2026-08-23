"""Read-only HTTP boundary for the CanonicalViewpoint admin workbench."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .canonical_repository.service import canonical_repository_service
from .canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    PostgresKnowledgeStoreError,
    database_url_from_env,
)
from .canonical_repository.viewpoint_admin_projection import (
    AdminViewpointProjectionCompiler,
    AdminViewpointProjectionError,
)
from .canonical_repository.viewpoint_resolution import ViewpointExceptionQueueArtifact
from .canonical_repository.viewpoint_recall_blocking import (
    ViewpointRecallBlockingArtifact,
)
from .canonical_repository.matthew16_viewpoint_candidate import (
    Matthew16ViewpointPilotArtifact,
    build_pilot_composition_projection,
    classify_pilot_viewpoint,
)
from .canonical_repository.matthew16_viewpoint_promotion import (
    Matthew16ViewpointPromotionProposal,
)
from .canonical_repository.matthew16_viewpoint_finalization import (
    Matthew16ViewpointFinalizationBundle,
)
from .canonical_repository.viewpoint_foundation import sha256_json


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


def _recall_blocking() -> ViewpointRecallBlockingArtifact | None:
    """Read one explicitly configured, SHA-valid recall artifact; never scan."""

    configured = os.getenv("WANG_VIEWPOINT_RECALL_BLOCKING_FILE")
    if not configured:
        return None
    path = Path(configured).resolve()
    if not path.is_file():
        raise AdminViewpointProjectionError(
            "configured viewpoint recall blocking artifact does not exist"
        )
    return ViewpointRecallBlockingArtifact.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _viewpoint_pilot() -> Matthew16ViewpointPilotArtifact | None:
    """Read one explicitly configured SHA-valid pilot; never scan staging."""

    configured = os.getenv("WANG_VIEWPOINT_PILOT_FILE")
    if not configured:
        return None
    path = Path(configured).resolve()
    if not path.is_file():
        raise AdminViewpointProjectionError("configured viewpoint pilot does not exist")
    return Matthew16ViewpointPilotArtifact.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _viewpoint_promotion() -> Matthew16ViewpointPromotionProposal | None:
    configured = os.getenv("WANG_VIEWPOINT_PROMOTION_FILE")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        raise AdminViewpointProjectionError(
            f"configured viewpoint promotion does not exist: {path}"
        )
    return Matthew16ViewpointPromotionProposal.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _viewpoint_finalization() -> Matthew16ViewpointFinalizationBundle | None:
    configured = os.getenv("WANG_VIEWPOINT_FINALIZATION_FILE")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        raise AdminViewpointProjectionError(
            f"configured viewpoint finalization does not exist: {path}"
        )
    return Matthew16ViewpointFinalizationBundle.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _viewpoint_pilot_source_files(
    pilot: Matthew16ViewpointPilotArtifact,
) -> tuple[dict[str, dict[str, str]], str]:
    """Resolve navigation labels from current source records, never from IDs."""

    source_ids = sorted(
        {
            evidence.source_id
            for member in pilot.members
            for evidence in member.proposition_unit.evidence
        }
    )
    store = PostgresKnowledgeStore(database_url_from_env())
    records = {
        str(item.get("source_id")): item
        for item in store.list_records("source_documents")
        if str(item.get("source_id")) in source_ids
    }
    missing = sorted(set(source_ids) - set(records))
    if missing:
        raise AdminViewpointProjectionError(
            f"pilot source navigation is missing current source records: {missing}"
        )
    result: dict[str, dict[str, str]] = {}
    for source_id in source_ids:
        record = records[source_id]
        source_path = str(record.get("source_path") or "")
        if not source_path:
            raise AdminViewpointProjectionError(
                f"pilot source navigation has no source_path: {source_id}"
            )
        result[source_id] = {
            "source_id": source_id,
            "title": str(record.get("title") or source_id),
            "source_type": str(record.get("source_type") or ""),
            "file_name": Path(source_path).name,
        }
    return result, sha256_json(result)


def _compiler() -> AdminViewpointProjectionCompiler:
    return AdminViewpointProjectionCompiler(
        canonical_repository_service.store,
        exception_queue=_exception_queue(),
        recall_blocking=_recall_blocking(),
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


@router.get("/viewpoints/recall-blocking")
def viewpoint_recall_blocking_diagnostics(
    cursor: str | None = None,
    limit: int = Query(default=10, ge=1, le=100),
):
    return _run("recall_diagnostics", cursor=cursor, limit=limit)


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


@router.get("/viewpoints/pilot")
def viewpoint_pilot():
    try:
        pilot = _viewpoint_pilot()
    except (
        ValueError,
        json.JSONDecodeError,
        AdminViewpointProjectionError,
        PostgresKnowledgeStoreError,
    ) as exc:
        raise HTTPException(status_code=503, detail=f"Viewpoint pilot unavailable: {exc}") from exc
    if pilot is None:
        raise HTTPException(status_code=404, detail="No viewpoint pilot is configured")
    try:
        consumer_projection = build_pilot_composition_projection(pilot)
        knowledge_classification = classify_pilot_viewpoint(pilot)
        promotion = _viewpoint_promotion()
        finalization = _viewpoint_finalization()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"Viewpoint pilot unavailable: {exc}") from exc
    if promotion and promotion.pilot_artifact_sha256 != pilot.artifact_sha256:
        raise HTTPException(
            status_code=503,
            detail="Viewpoint pilot unavailable: promotion proposal is bound to another pilot",
        )
    if finalization:
        if promotion is None:
            raise HTTPException(
                status_code=503,
                detail="Viewpoint pilot unavailable: finalization requires its promotion proposal",
            )
        if not (
            finalization.promotion_proposal_artifact_sha256
            == promotion.artifact_sha256
            and finalization.atomic_coverage_snapshot.pilot_artifact_sha256
            == pilot.artifact_sha256
            and finalization.automated_promotion_decision.consumer_projection_sha256
            == consumer_projection.projection_sha256
        ):
            raise HTTPException(
                status_code=503,
                detail="Viewpoint pilot unavailable: finalization artifact bindings differ",
            )
    try:
        source_files, source_files_sha256 = _viewpoint_pilot_source_files(pilot)
    except (AdminViewpointProjectionError, PostgresKnowledgeStoreError) as exc:
        raise HTTPException(status_code=503, detail=f"Viewpoint pilot unavailable: {exc}") from exc
    return {
        "schema_version": "wang_admin_viewpoint_pilot_projection_v1",
        "authority": {
            "kind": "sha_bound_internal_candidate",
            "projection": "Matthew16ViewpointPilotArtifact",
            "representation": "read_only",
            "read_only": True,
        },
        "projection_sha256": pilot.artifact_sha256,
        "consumer_projection": {
            "consumer_kind": consumer_projection.consumer_kind,
            "eligibility": consumer_projection.eligibility,
            "projection_sha256": consumer_projection.projection_sha256,
            "blocker_codes": consumer_projection.blocker_codes,
        },
        "knowledge_classification": knowledge_classification.model_dump(mode="json"),
        "promotion": promotion.model_dump(mode="json") if promotion else None,
        "finalization": (
            finalization.model_dump(mode="json") if finalization else None
        ),
        "source_files": source_files,
        "source_files_sha256": source_files_sha256,
        "data": pilot.model_dump(mode="json"),
    }


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
