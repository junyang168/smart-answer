from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.api.config import WANG_CLAIM_LAYER_STAGING_DIR, WANG_SEED_CATALOG_DIR
from .compiler import RepositoryValidationError
from .knowledge_importer import KnowledgePackageValidationError
from .knowledge_models import KNOWLEDGE_COLLECTIONS
from .models import CanonicalUnit, Citation, CitationResolution, RepositoryStatus
from .service import canonical_repository_service


router = APIRouter(prefix="/canonical-repository", tags=["canonical-repository"])
admin_router = APIRouter(prefix="/admin/canonical-repository", tags=["canonical-repository-admin"])


class RebuildSourceMapsRequest(BaseModel):
    project_ids: List[str] = Field(default_factory=list)


class CreateCitationRequest(BaseModel):
    source_id: str
    start_line: int
    end_line: int
    highlight_text: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    role: str = "primary_evidence"
    supports_claim: str = ""


class ImportSeedRequest(BaseModel):
    catalog_path: str = str(
        WANG_SEED_CATALOG_DIR / "matthew-review-v1/canonical_units.json"
    )


class MergeUnitsRequest(BaseModel):
    absorbed_unit_ids: List[str]
    apply: bool = False


class ImportKnowledgePackageRequest(BaseModel):
    package_path: str = str(
        WANG_CLAIM_LAYER_STAGING_DIR / "shared_knowledge_pilot_v1.json"
    )


class UpdateKnowledgeRecordRequest(BaseModel):
    changes: Dict[str, Any]
    expected_revision: Optional[int] = None


class SnapshotDependenciesRequest(BaseModel):
    consumer_kind: str
    consumer_id: str
    claim_ids: List[str]
    route_ids: List[str] = Field(default_factory=list)


@router.get("/status", response_model=RepositoryStatus)
def status() -> RepositoryStatus:
    return canonical_repository_service.status()


@router.get("/bible-index")
def bible_index():
    return canonical_repository_service.compiled_index("bible_index.json")


@router.get("/topic-index")
def topic_index():
    return canonical_repository_service.compiled_index("topic_index.json")


@router.get("/units/{unit_id}")
def unit_detail(unit_id: str):
    try:
        unit = canonical_repository_service.store.get_unit(unit_id)
        if unit.status != "published":
            raise HTTPException(status_code=404, detail="Canonical unit not found")
        return canonical_repository_service.unit_detail(unit_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canonical unit not found") from exc


@router.get("/citations/{citation_id}", response_model=CitationResolution)
def citation_detail(citation_id: str) -> CitationResolution:
    try:
        return canonical_repository_service.resolve_citation(citation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Citation not found") from exc


@admin_router.post("/source-maps/rebuild")
def rebuild_source_maps(payload: RebuildSourceMapsRequest):
    results = []
    for project_id in payload.project_ids:
        try:
            results.append({"project_id": project_id, "status": "completed", **canonical_repository_service.register_project_source(project_id)})
        except Exception as exc:
            results.append({"project_id": project_id, "status": "failed", "error": str(exc)})
    return {"results": results}


@admin_router.get("/units")
def list_units(
    status: Optional[str] = None,
    unit_type: Optional[str] = None,
    source_origin_id: Optional[str] = None,
):
    return {
        "units": canonical_repository_service.list_unit_summaries(
            status=status,
            unit_type=unit_type,
            source_origin_id=source_origin_id,
        )
    }


@admin_router.get("/units/{unit_id}")
def authoring_unit_detail(unit_id: str):
    try:
        return canonical_repository_service.authoring_unit_detail(unit_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canonical unit not found") from exc


@admin_router.post("/units/import-candidates")
def import_candidates(payload: ImportSeedRequest):
    requested = _validated_catalog_path(payload.catalog_path)
    return canonical_repository_service.import_seed_catalog(requested)


def _validated_catalog_path(catalog_path: str) -> Path:
    requested = Path(catalog_path).expanduser().resolve()
    allowed_root = WANG_SEED_CATALOG_DIR.resolve()
    if not requested.is_relative_to(allowed_root) or requested.name != "canonical_units.json":
        raise HTTPException(
            status_code=400,
            detail="Catalog must be a canonical_units.json file under the Wang catalog root",
        )
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="Seed catalog not found")
    return requested


def _validated_knowledge_package_path(package_path: str) -> Path:
    requested = Path(package_path).expanduser().resolve()
    allowed_root = WANG_CLAIM_LAYER_STAGING_DIR.resolve()
    if not requested.is_relative_to(allowed_root) or requested.suffix != ".json":
        raise HTTPException(
            status_code=400,
            detail="Knowledge package must be a JSON file under Wang claim-layer staging",
        )
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="Knowledge package not found")
    return requested


@admin_router.get("/knowledge/status")
def knowledge_status():
    return canonical_repository_service.knowledge_status()


@admin_router.post("/knowledge/topics/reconcile")
def reconcile_topic_identity():
    return canonical_repository_service.reconcile_topic_identity()


@admin_router.get("/knowledge/claims/{claim_id}/impact")
def claim_impact(claim_id: str):
    try:
        return canonical_repository_service.analyze_claim_impact(claim_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Claim not found") from exc


@admin_router.post("/knowledge/dependencies/snapshot")
def snapshot_dependencies(payload: SnapshotDependenciesRequest):
    try:
        return canonical_repository_service.snapshot_product_dependencies(
            payload.consumer_kind,
            payload.consumer_id,
            payload.claim_ids,
            payload.route_ids,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail="A dependency claim was not found") from exc


@admin_router.post("/knowledge/impact-events/{impact_event_id}/withdraw")
def withdraw_impacted_units(impact_event_id: str):
    try:
        return canonical_repository_service.withdraw_impacted_units(impact_event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Impact event not found") from exc
    except RepositoryValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "findings": exc.findings}) from exc


@admin_router.post("/knowledge/import-package")
def import_knowledge_package(payload: ImportKnowledgePackageRequest):
    requested = _validated_knowledge_package_path(payload.package_path)
    try:
        return canonical_repository_service.import_knowledge_package(requested)
    except (json.JSONDecodeError, KnowledgePackageValidationError) as exc:
        findings = getattr(exc, "findings", [str(exc)])
        raise HTTPException(
            status_code=422,
            detail={"message": "Knowledge package validation failed", "findings": findings},
        ) from exc


@admin_router.get("/knowledge/{collection}")
def list_knowledge_records(collection: str):
    if collection not in KNOWLEDGE_COLLECTIONS:
        raise HTTPException(status_code=404, detail="Knowledge collection not found")
    return {
        "collection": collection,
        "records": [
            item.model_dump(mode="json")
            for item in canonical_repository_service.store.list_knowledge_records(collection)
        ],
    }


@admin_router.get("/knowledge/{collection}/{record_id}")
def get_knowledge_record(collection: str, record_id: str):
    if collection not in KNOWLEDGE_COLLECTIONS:
        raise HTTPException(status_code=404, detail="Knowledge collection not found")
    try:
        return canonical_repository_service.store.get_knowledge_record(collection, record_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Knowledge record not found") from exc


@admin_router.patch("/knowledge/{collection}/{record_id}")
def update_knowledge_record(
    collection: str,
    record_id: str,
    payload: UpdateKnowledgeRecordRequest,
):
    if collection not in KNOWLEDGE_COLLECTIONS:
        raise HTTPException(status_code=404, detail="Knowledge collection not found")
    try:
        updated = canonical_repository_service.update_knowledge_record(
            collection,
            record_id,
            payload.changes,
            expected_revision=payload.expected_revision,
        )
        return updated.model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Knowledge record not found") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@admin_router.post("/units/backfill-source-citations")
def backfill_source_citations(payload: ImportSeedRequest):
    requested = _validated_catalog_path(payload.catalog_path)
    return canonical_repository_service.backfill_seed_citations(requested)


@admin_router.put("/units/{unit_id}", response_model=CanonicalUnit)
def put_unit(unit_id: str, unit: CanonicalUnit) -> CanonicalUnit:
    if unit_id != unit.unit_id:
        raise HTTPException(status_code=400, detail="Unit ID does not match request path")
    if unit.status == "published":
        if not unit.citation_ids and not unit.review.source_exception_reason:
            raise HTTPException(status_code=422, detail="Published units require an approved citation or a reviewed source exception")
        for citation_id in unit.citation_ids:
            try:
                citation = canonical_repository_service.store.get_citation(citation_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=422, detail=f"Citation {citation_id} does not exist") from exc
            if citation.status != "approved":
                raise HTTPException(status_code=422, detail=f"Citation {citation_id} is not approved")
    try:
        return canonical_repository_service.save_unit_and_refresh_public_index(unit)
    except RepositoryValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "Published index validation failed", "findings": exc.findings},
        ) from exc


@admin_router.post("/units/{unit_id}/merge")
def merge_units(unit_id: str, payload: MergeUnitsRequest):
    try:
        return canonical_repository_service.merge_units(unit_id, payload.absorbed_unit_ids, apply=payload.apply)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Merge unit not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@admin_router.post("/units/{unit_id}/citations", response_model=Citation)
def create_citation(unit_id: str, payload: CreateCitationRequest) -> Citation:
    try:
        unit = canonical_repository_service.store.get_unit(unit_id)
        citation = canonical_repository_service.create_citation_from_source_range(**payload.model_dump())
        if citation.citation_id not in unit.citation_ids:
            unit.citation_ids.append(citation.citation_id)
            canonical_repository_service.store.save_unit(unit)
        return citation
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Unit or source not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@admin_router.patch("/citations/{citation_id}", response_model=Citation)
def update_citation(citation_id: str, citation: Citation) -> Citation:
    if citation_id != citation.citation_id:
        raise HTTPException(status_code=400, detail="Citation ID does not match request path")
    existing = canonical_repository_service.store.get_citation(citation_id)
    citation.revision = existing.revision + 1
    canonical_repository_service.store.save_citation(citation)
    return citation


@admin_router.post("/build")
def build_repository():
    try:
        return canonical_repository_service.compiler.build()
    except RepositoryValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "findings": exc.findings}) from exc
