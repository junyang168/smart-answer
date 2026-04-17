from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional

from backend.api.lecture_manager import (
    LectureSeries, Lecture,
    list_series, get_series, create_series, update_series_metadata, delete_series,
    add_lecture, update_lecture, delete_lecture,
    assign_project_to_lecture, remove_project_from_lecture
)
from backend.api.sermon_converter_service import get_sermon_project_metadata

router = APIRouter(prefix="/admin/notes-to-sermon/series", tags=["Lecture Series"])
public_router = APIRouter(prefix="/notes-to-sermon/public", tags=["Lecture Series Public"])

# --- Request Models ---

class CreateSeriesRequest(BaseModel):
    title: str
    description: Optional[str] = None
    folder: Optional[str] = None
    project_type: Optional[str] = "sermon_note"

class CreateLectureRequest(BaseModel):
    title: str
    description: Optional[str] = None
    folder: Optional[str] = None

class AssignProjectRequest(BaseModel):
    project_id: str


class PublicLectureProject(BaseModel):
    id: str
    title: str
    google_doc_id: Optional[str] = None
    google_doc_url: Optional[str] = None
    available: bool = False


class PublicLecture(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    folder: Optional[str] = None
    projects: List[PublicLectureProject]


class PublicLectureSeriesSummary(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    folder: Optional[str] = None
    project_type: str = "sermon_note"
    lecture_count: int
    project_count: int
    available_project_count: int


class PublicLectureSeriesDetail(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    folder: Optional[str] = None
    project_type: str = "sermon_note"
    lectures: List[PublicLecture]


def _build_google_doc_url(doc_id: Optional[str]) -> Optional[str]:
    if not doc_id:
        return None
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def _build_public_series_detail(series: LectureSeries) -> PublicLectureSeriesDetail:
    lectures: List[PublicLecture] = []
    for lecture in series.lectures:
        projects: List[PublicLectureProject] = []
        for project_id in lecture.project_ids:
            project = get_sermon_project_metadata(project_id)
            title = project.title if project else project_id
            google_doc_id = project.google_doc_id if project else None
            projects.append(
                PublicLectureProject(
                    id=project_id,
                    title=title,
                    google_doc_id=google_doc_id,
                    google_doc_url=_build_google_doc_url(google_doc_id),
                    available=bool(google_doc_id),
                )
            )
        lectures.append(
            PublicLecture(
                id=lecture.id,
                title=lecture.title,
                description=lecture.description,
                folder=lecture.folder,
                projects=projects,
            )
        )

    return PublicLectureSeriesDetail(
        id=series.id,
        title=series.title,
        description=series.description,
        folder=series.folder,
        project_type=series.project_type,
        lectures=lectures,
    )

# --- Series Endpoints ---

@router.get("/debug-path")
def debug_path():
    from backend.api.lecture_manager import SERIES_DB_PATH
    return {"path": str(SERIES_DB_PATH), "exists": SERIES_DB_PATH.exists(), "parent_exists": SERIES_DB_PATH.parent.exists()}


@public_router.get("/series", response_model=List[PublicLectureSeriesSummary])
def list_public_series_endpoint():
    summaries: List[PublicLectureSeriesSummary] = []
    for series in list_series():
        lecture_count = len(series.lectures)
        project_count = sum(len(lecture.project_ids) for lecture in series.lectures)
        available_project_count = 0
        for lecture in series.lectures:
            for project_id in lecture.project_ids:
                project = get_sermon_project_metadata(project_id)
                if project and project.google_doc_id:
                    available_project_count += 1
        summaries.append(
            PublicLectureSeriesSummary(
                id=series.id,
                title=series.title,
                description=series.description,
                folder=series.folder,
                project_type=series.project_type,
                lecture_count=lecture_count,
                project_count=project_count,
                available_project_count=available_project_count,
            )
        )
    return summaries


@public_router.get("/series/{series_id}", response_model=PublicLectureSeriesDetail)
def get_public_series_endpoint(series_id: str):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return _build_public_series_detail(series)

@router.get("", response_model=List[LectureSeries])
def list_series_endpoint():
    return list_series()

@router.post("", response_model=LectureSeries)
def create_series_endpoint(payload: CreateSeriesRequest):
    return create_series(payload.title, payload.description, payload.folder, payload.project_type)

@router.get("/{series_id}", response_model=LectureSeries)
def get_series_endpoint(series_id: str):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return series

@router.put("/{series_id}", response_model=LectureSeries)
def update_series_endpoint(series_id: str, payload: CreateSeriesRequest):
    series = update_series_metadata(series_id, payload.title, payload.description, payload.folder, payload.project_type)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    return series

@router.delete("/{series_id}")
def delete_series_endpoint(series_id: str):
    if not delete_series(series_id):
        raise HTTPException(status_code=404, detail="Series not found")
    return {"status": "success"}

# --- Folder Listing Endpoints ---

@router.get("/folders/root", response_model=List[str])
def list_series_folders_endpoint():
    from backend.api.lecture_manager import list_series_folders
    return list_series_folders()

@router.get("/folders/{series_folder}", response_model=List[str])
def list_lecture_folders_endpoint(series_folder: str):
    from backend.api.lecture_manager import list_lecture_folders
    headers = list_lecture_folders(series_folder)
    return headers

# --- Lecture Endpoints ---

@router.post("/{series_id}/lectures", response_model=Lecture)
def add_lecture_endpoint(series_id: str, payload: CreateLectureRequest):
    lecture = add_lecture(series_id, payload.title, payload.description, payload.folder)
    if not lecture:
        raise HTTPException(status_code=404, detail="Series not found")
    return lecture

@router.put("/{series_id}/lectures/{lecture_id}", response_model=Lecture)
def update_lecture_endpoint(series_id: str, lecture_id: str, payload: CreateLectureRequest):
    lecture = update_lecture(series_id, lecture_id, payload.title, payload.description, payload.folder)
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
