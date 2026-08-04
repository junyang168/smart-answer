from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .compiler import RepositoryValidationError
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
    catalog_path: str = "output/seed-catalog/matthew-review-v1/canonical_units.json"


class MergeUnitsRequest(BaseModel):
    absorbed_unit_ids: List[str]
    apply: bool = False


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
def list_units(status: Optional[str] = None, unit_type: Optional[str] = None):
    return {"units": canonical_repository_service.list_unit_summaries(status=status, unit_type=unit_type)}


@admin_router.get("/units/{unit_id}")
def authoring_unit_detail(unit_id: str):
    try:
        return canonical_repository_service.authoring_unit_detail(unit_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Canonical unit not found") from exc


@admin_router.post("/units/import-candidates")
def import_candidates(payload: ImportSeedRequest):
    project_root = Path(__file__).resolve().parents[3]
    requested = (project_root / payload.catalog_path).resolve()
    allowed_root = (project_root / "output" / "seed-catalog").resolve()
    if not requested.is_relative_to(allowed_root) or requested.name != "canonical_units.json":
        raise HTTPException(status_code=400, detail="Catalog must be a canonical_units.json file under output/seed-catalog")
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="Seed catalog not found")
    return canonical_repository_service.import_seed_catalog(requested)


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
