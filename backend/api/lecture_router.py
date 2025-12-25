from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional

from backend.api.lecture_manager import (
    LectureSeries, Lecture,
    list_series, get_series, create_series, update_series_metadata, delete_series,
    add_lecture, update_lecture, delete_lecture,
    assign_project_to_lecture, remove_project_from_lecture
)

router = APIRouter(prefix="/admin/notes-to-sermon/series", tags=["Lecture Series"])

# --- Request Models ---

class CreateSeriesRequest(BaseModel):
    title: str
    description: Optional[str] = None

class CreateLectureRequest(BaseModel):
    title: str
    description: Optional[str] = None

class AssignProjectRequest(BaseModel):
    project_id: str

# --- Series Endpoints ---

@router.get("/debug-path")
def debug_path():
    from backend.api.lecture_manager import SERIES_DB_PATH
    return {"path": str(SERIES_DB_PATH), "exists": SERIES_DB_PATH.exists(), "parent_exists": SERIES_DB_PATH.parent.exists()}

@router.get("", response_model=List[LectureSeries])
def list_series_endpoint():
    return list_series()

@router.post("", response_model=LectureSeries)
def create_series_endpoint(payload: CreateSeriesRequest):
    return create_series(payload.title, payload.description)

@router.get("/{series_id}", response_model=LectureSeries)
def get_series_endpoint(series_id: str):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return series

@router.put("/{series_id}", response_model=LectureSeries)
def update_series_endpoint(series_id: str, payload: CreateSeriesRequest):
    series = update_series_metadata(series_id, payload.title, payload.description)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return series

@router.delete("/{series_id}")
def delete_series_endpoint(series_id: str):
    if not delete_series(series_id):
        raise HTTPException(status_code=404, detail="Series not found")
    return {"status": "success"}

# --- Lecture Endpoints ---

@router.post("/{series_id}/lectures", response_model=Lecture)
def add_lecture_endpoint(series_id: str, payload: CreateLectureRequest):
    lecture = add_lecture(series_id, payload.title, payload.description)
    if not lecture:
        raise HTTPException(status_code=404, detail="Series not found")
    return lecture

@router.put("/{series_id}/lectures/{lecture_id}", response_model=Lecture)
def update_lecture_endpoint(series_id: str, lecture_id: str, payload: CreateLectureRequest):
    lecture = update_lecture(series_id, lecture_id, payload.title, payload.description)
    if not lecture:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return lecture

@router.delete("/{series_id}/lectures/{lecture_id}")
def delete_lecture_endpoint(series_id: str, lecture_id: str):
    if not delete_lecture(series_id, lecture_id):
        raise HTTPException(status_code=404, detail="Lecture not found")
    return {"status": "success"}

# --- Project Assignment Endpoints ---

@router.post("/{series_id}/lectures/{lecture_id}/projects")
def assign_project_endpoint(series_id: str, lecture_id: str, payload: AssignProjectRequest):
    success = assign_project_to_lecture(series_id, lecture_id, payload.project_id)
    if not success:
         raise HTTPException(status_code=400, detail="Failed to assign project (Series/Lecture not found or already assigned)")
    return {"status": "success"}

class ReorderProjectsRequest(BaseModel):
    project_ids: List[str]

@router.put("/{series_id}/lectures/{lecture_id}/projects/reorder")
def reorder_projects_endpoint(series_id: str, lecture_id: str, payload: ReorderProjectsRequest):
    from backend.api.lecture_manager import reorder_lecture_projects
    success = reorder_lecture_projects(series_id, lecture_id, payload.project_ids)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to reorder projects. Ensure all provided IDs match the existing ones.")
    return {"status": "success"}

@router.delete("/{series_id}/lectures/{lecture_id}/projects/{project_id}")
def remove_project_endpoint(series_id: str, lecture_id: str, project_id: str):
    success = remove_project_from_lecture(series_id, lecture_id, project_id)
    if not success:
         raise HTTPException(status_code=404, detail="Project assignment not found")
    return {"status": "success"}
