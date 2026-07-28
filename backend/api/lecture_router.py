from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional

from backend.api.lecture_manager import (
    LectureSeries, Lecture,
    list_series, get_series, create_series, update_series_metadata, delete_series,
    add_lecture, update_lecture, delete_lecture, reorder_lectures,
    assign_project_to_lecture, remove_project_from_lecture
)
from backend.api.sermon_converter_service import get_sermon_project_metadata
from backend.api.sermon_converter_service import get_sermon_final_path
from backend.api.series_index_refresh import (
    SeriesIndexRefreshStatus,
    get_series_index_refresh_status,
    queue_series_index_refresh,
    run_series_index_refresh,
)
from backend.api.series_manuscript_service import (
    ContinuityStatus,
    get_continuity_status,
    queue_continuity_analysis,
    run_continuity_analysis,
)
from backend.api.series_manuscript_builder import (
    SeriesBuildStatus,
    get_series_build_status,
    get_series_draft,
    get_series_draft_review,
    queue_series_draft_build,
    run_series_draft_build,
)
from backend.api.series_manuscript_application import (
    apply_safe_integration_patches,
    IntegratedManuscriptStatus,
    get_integrated_manuscript_status,
    materialize_integrated_manuscript,
)

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

class ReorderLecturesRequest(BaseModel):
    lecture_ids: List[str]


class ContinuityAnalysisRequest(BaseModel):
    project_id: str


class SeriesDraftBuildRequest(BaseModel):
    project_id: str
    proposal_id: str


class IntegratedManuscriptRequest(BaseModel):
    project_id: str
    proposal_id: str


class ApplyIntegrationPatchesRequest(BaseModel):
    project_id: str
    application_id: str


class SeriesDraftContent(BaseModel):
    series_id: str
    markdown: str
    proposal_id: Optional[str] = None
    project_id: Optional[str] = None
    built_at: Optional[str] = None
    changed_unit_count: int = 0
    new_unit_count: int = 0
    evidence_count: int = 0
    changes: List[dict] = Field(default_factory=list)


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


class PublicProjectManuscript(BaseModel):
    id: str
    title: str
    markdown: str


def _build_google_doc_url(doc_id: Optional[str]) -> Optional[str]:
    if not doc_id:
        return None
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def _project_has_final_markdown(project_id: str) -> bool:
    return get_sermon_final_path(project_id).is_file()


def _project_belongs_to_public_series(project_id: str) -> bool:
    for series in list_series():
        if series.project_type != "sermon_note":
            continue
        if any(project_id in lecture.project_ids for lecture in series.lectures):
            return True
    return False


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
                    available=bool(project and _project_has_final_markdown(project_id)),
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
        if series.project_type != "sermon_note":
            continue
        lecture_count = len(series.lectures)
        project_count = sum(len(lecture.project_ids) for lecture in series.lectures)
        available_project_count = 0
        for lecture in series.lectures:
            for project_id in lecture.project_ids:
                project = get_sermon_project_metadata(project_id)
                if project and _project_has_final_markdown(project_id):
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
    if series.project_type != "sermon_note":
        raise HTTPException(status_code=404, detail="Series not found")
    return _build_public_series_detail(series)


@public_router.get("/projects/{project_id}/manuscript", response_model=PublicProjectManuscript)
def get_public_project_manuscript_endpoint(project_id: str):
    project = get_sermon_project_metadata(project_id)
    if not project or not _project_belongs_to_public_series(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    final_path = get_sermon_final_path(project_id)
    if not final_path.is_file():
        raise HTTPException(status_code=404, detail="Manuscript not found")

    try:
        markdown = final_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read manuscript: {exc}") from exc

    return PublicProjectManuscript(
        id=project.id,
        title=project.title,
        markdown=markdown,
    )

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


@router.get("/{series_id}/index-refresh", response_model=SeriesIndexRefreshStatus)
def get_index_refresh_status_endpoint(series_id: str):
    if not get_series(series_id):
        raise HTTPException(status_code=404, detail="Series not found")
    return get_series_index_refresh_status(series_id)


@router.post("/{series_id}/index-refresh", response_model=SeriesIndexRefreshStatus, status_code=202)
def start_index_refresh_endpoint(series_id: str, background_tasks: BackgroundTasks):
    if not get_series(series_id):
        raise HTTPException(status_code=404, detail="Series not found")

    status, accepted = queue_series_index_refresh(series_id)
    if not accepted:
        raise HTTPException(
            status_code=409,
            detail=f"Index refresh is already running for series {status.series_id}.",
        )
    background_tasks.add_task(run_series_index_refresh, series_id)
    return status


@router.get("/{series_id}/continuity/{project_id}", response_model=ContinuityStatus)
def get_continuity_status_endpoint(series_id: str, project_id: str):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    if project_id not in [item for lecture in series.lectures for item in lecture.project_ids]:
        raise HTTPException(status_code=404, detail="Project is not assigned to this series")
    return get_continuity_status(series_id, project_id)


@router.post("/{series_id}/continuity", response_model=ContinuityStatus, status_code=202)
def start_continuity_analysis_endpoint(
    series_id: str,
    payload: ContinuityAnalysisRequest,
    background_tasks: BackgroundTasks,
):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    if payload.project_id not in [item for lecture in series.lectures for item in lecture.project_ids]:
        raise HTTPException(status_code=404, detail="Project is not assigned to this series")
    project = get_sermon_project_metadata(payload.project_id)
    if not project or project.project_type != "transcript":
        raise HTTPException(status_code=400, detail="Continuity analysis requires a transcript project")
    status, accepted = queue_continuity_analysis(series_id, payload.project_id)
    if not accepted:
        raise HTTPException(status_code=409, detail="Continuity analysis is already running")
    background_tasks.add_task(run_continuity_analysis, series_id, payload.project_id)
    return status


@router.get("/{series_id}/series-draft/{project_id}", response_model=SeriesBuildStatus)
def get_series_draft_status_endpoint(series_id: str, project_id: str):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    if project_id not in [item for lecture in series.lectures for item in lecture.project_ids]:
        raise HTTPException(status_code=404, detail="Project is not assigned to this series")
    return get_series_build_status(series_id, project_id)


@router.get("/{series_id}/series-draft", response_model=SeriesDraftContent)
def get_series_draft_content_endpoint(series_id: str):
    if not get_series(series_id):
        raise HTTPException(status_code=404, detail="Series not found")
    markdown = get_series_draft(series_id)
    if not markdown:
        raise HTTPException(status_code=404, detail="Series Draft not found")
    review = get_series_draft_review(series_id)
    return SeriesDraftContent(series_id=series_id, markdown=markdown, **review)


@router.post("/{series_id}/series-draft", response_model=SeriesBuildStatus, status_code=202)
def start_series_draft_build_endpoint(
    series_id: str,
    payload: SeriesDraftBuildRequest,
    background_tasks: BackgroundTasks,
):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    if payload.project_id not in [item for lecture in series.lectures for item in lecture.project_ids]:
        raise HTTPException(status_code=404, detail="Project is not assigned to this series")
    status, accepted = queue_series_draft_build(series_id, payload.project_id, payload.proposal_id)
    if not accepted:
        raise HTTPException(status_code=409, detail="Series Draft build is already running")
    background_tasks.add_task(
        run_series_draft_build,
        series_id,
        payload.project_id,
        payload.proposal_id,
    )
    return status


@router.get("/{series_id}/integrated-manuscript/{project_id}", response_model=IntegratedManuscriptStatus)
def get_integrated_manuscript_status_endpoint(series_id: str, project_id: str):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    if project_id not in [item for lecture in series.lectures for item in lecture.project_ids]:
        raise HTTPException(status_code=404, detail="Project is not assigned to this series")
    return get_integrated_manuscript_status(series_id, project_id)


@router.post("/{series_id}/integrated-manuscript", response_model=IntegratedManuscriptStatus)
def generate_integrated_manuscript_endpoint(
    series_id: str,
    payload: IntegratedManuscriptRequest,
):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    if payload.project_id not in [item for lecture in series.lectures for item in lecture.project_ids]:
        raise HTTPException(status_code=404, detail="Project is not assigned to this series")
    try:
        return materialize_integrated_manuscript(
            series_id,
            payload.project_id,
            payload.proposal_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{series_id}/integrated-manuscript/apply-patches", response_model=IntegratedManuscriptStatus)
def apply_integration_patches_endpoint(
    series_id: str,
    payload: ApplyIntegrationPatchesRequest,
):
    series = get_series(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    if payload.project_id not in [item for lecture in series.lectures for item in lecture.project_ids]:
        raise HTTPException(status_code=404, detail="Project is not assigned to this series")
    try:
        return apply_safe_integration_patches(
            series_id,
            payload.project_id,
            payload.application_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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

@router.put("/{series_id}/lectures/reorder")
def reorder_lectures_endpoint(series_id: str, payload: ReorderLecturesRequest):
    success = reorder_lectures(series_id, payload.lecture_ids)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to reorder lectures. Ensure all provided IDs match the existing ones.")
    return {"status": "success"}

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
