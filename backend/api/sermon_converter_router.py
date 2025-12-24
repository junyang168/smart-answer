from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks

from fastapi.responses import FileResponse
from backend.api.sermon_converter_service import (
    list_note_images, 
    process_note_image,
    get_image_path,
    get_page_segments,
    create_sermon_project,
    get_sermon_source,
    save_sermon_source,
    get_sermon_images,
    get_sermon_project_metadata,
    update_sermon_pages,
    trigger_project_page_ocr,
    trigger_project_batch_ocr,
    list_sermon_projects,
    generate_sermon_draft,
    get_sermon_draft,
    save_sermon_draft,
    commit_sermon_project,
    export_sermon_to_doc,
    refine_sermon_draft,
    update_sermon_project_metadata,
    NoteImage,
    Segment,
    NoteImage,
    Segment,
    SermonProject
)
from backend.api.prompt_manager import (
    list_prompts,
    get_prompt,
    create_prompt,
    update_prompt,
    delete_prompt,
    Prompt
)
from pydantic import BaseModel

# Use the prefix/tags from the plan
router = APIRouter(prefix="/admin/notes-to-sermon", tags=["notes-to-sermon"])

@router.get("/images", response_model=List[NoteImage])
def get_images() -> List[NoteImage]:
    """
    List all available note images from the source directory.
    """
    return list_note_images()

@router.post("/page/{filename}/process")
def process_page_endpoint(filename: str):
    """
    Trigger the OCR and initial segmentation for a specific page.
    This might take time, so in production this should be a background task.
    For this prototype/dev tool, we'll keep it synchronous or rely on client timeout handling,
    but ideally we should offload to a worker.
    """
    try:
        page_id = process_note_image(filename)
        return {"status": "success", "page_id": page_id, "message": "OCR complete."}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Image {filename} not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/image/{filename}")
def get_page_image(filename: str) -> FileResponse:
    """
    Serve the source image file.
    """
    try:
        path = get_image_path(filename)
        return FileResponse(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image not found")

@router.get("/page/{filename}/segments", response_model=List[Segment])
def get_page_detail(filename: str) -> List[Segment]:
    """
    Get the segments/content for a specific page.
    """
    return get_page_segments(filename)

class CreateSermonRequest(BaseModel):
    title: str
    pages: List[str]

@router.post("/sermon-project", response_model=SermonProject)
def create_sermon(payload: CreateSermonRequest) -> SermonProject:
    """
    Create a new logical sermon project from a list of pages.
    """
    return create_sermon_project(payload.title, payload.pages)

@router.get("/sermon-project/{sermon_id}/source")
def get_project_source(sermon_id: str):
    """
    Get the Unified Markdown source for the sermon.
    """
    return {"content": get_sermon_source(sermon_id)}

class SaveSourceRequest(BaseModel):
    content: str
    
@router.post("/sermon-project/{sermon_id}/source")
def save_project_source(sermon_id: str, payload: SaveSourceRequest):
    """
    Update the Unified Markdown source for the sermon.
    """
    try:
        save_sermon_source(sermon_id, payload.content)
        return {"status": "success", "message": "Source saved."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Draft Generation Endpoints ---

class GenerateDraftRequest(BaseModel):
    prompt_id: Optional[str] = None

@router.post("/sermon-project/{sermon_id}/generate-draft")
def trigger_draft_generation(sermon_id: str, payload: Optional[GenerateDraftRequest] = None, background_tasks: BackgroundTasks = None):
    """
    Generate the sermon draft in background.
    """
    try:
        # FastAPI dependency fix: If payload is missing, it might be None
        # Actually standard for optional body is to declare it. 
        # But we need to handle if client sends nothing.
        prompt_id = payload.prompt_id if payload else None
        
        # We wrap the synchronous call in a simple lambda or direct call
        # But wait, generate_sermon_draft is blocking and calls the API. BackgroundTasks is perfect.
        background_tasks.add_task(generate_sermon_draft, sermon_id, prompt_id)
        return {"status": "success", "message": "Draft generation started."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sermon-project/{sermon_id}/draft")
def get_project_draft(sermon_id: str):
    try:
        content = get_sermon_draft(sermon_id)
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sermon-project/{sermon_id}/draft")
def save_project_draft(sermon_id: str, payload: SaveSourceRequest):
    try:
        save_sermon_draft(sermon_id, payload.content)
        return {"status": "success", "message": "Draft saved."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sermon-project/{sermon_id}/images")
def get_project_images(sermon_id: str) -> List[str]:
    """
    Get the ordered list of pages (filenames) for the sermon.
    """
    return get_sermon_images(sermon_id)

@router.get("/sermon-project/{sermon_id}", response_model=SermonProject)
def get_project_meta(sermon_id: str) -> SermonProject:
    """
    Get full metadata for the sermon project (title, pages, etc).
    """
    project = get_sermon_project_metadata(sermon_id)
    if not project:
        raise HTTPException(status_code=404, detail="Sermon project not found")
    return project

class UpdatePageRequest(BaseModel):
    action: str # "add" or "remove"
    filename: str

@router.post("/sermon-project/{sermon_id}/page")
def update_project_page(sermon_id: str, payload: UpdatePageRequest) -> SermonProject:
    """
    Add or Remove a page from the project and re-sync the source.
    """
    try:
        return update_sermon_pages(sermon_id, payload.action, payload.filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class OcrRequest(BaseModel):
    filename: str

@router.post("/sermon-project/{sermon_id}/ocr")
def trigger_ocr(sermon_id: str, payload: OcrRequest):
    """
    Trigger manual OCR for a page within a project and update the source.
    """
    try:
        trigger_project_page_ocr(sermon_id, payload.filename)
        return {"status": "success", "message": "OCR started and source updated."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sermon-project/{sermon_id}/ocr-all")
def trigger_batch_ocr(sermon_id: str, background_tasks: BackgroundTasks):
    """
    Trigger batch OCR for all unprocessed pages in the project.
    Runs in background to prevent timeout.
    """
    try:
        background_tasks.add_task(trigger_project_batch_ocr, sermon_id)
        return {"status": "success", "message": "Batch OCR started in background."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sermon-project/{sermon_id}/check-in")
def check_in_project(sermon_id: str):
    """
    Commit the sermon project (source, draft, meta) to the local git repo.
    """
    try:
        commit_hash = commit_sermon_project(sermon_id)
        return {"status": "success", "commit_hash": commit_hash, "message": "Project checked in successfully."}
    except Exception as e:
        if "Nothing to commit" in str(e):
             return {"status": "success", "message": "Nothing to commit."}
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sermon-project/{sermon_id}/export-doc")
def export_project_doc(sermon_id: str):
    """
    Export the sermon draft to a Google Doc.
    Returns the URL of the created document.
    """
    try:
        doc_url = export_sermon_to_doc(sermon_id)
        return {"status": "success", "url": doc_url, "message": "Google Doc created."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sermon-projects", response_model=List[SermonProject])
def get_projects() -> List[SermonProject]:
    """
    List all available sermon projects.
    """
    return list_sermon_projects()

class RefineRequest(BaseModel):
    selection: str
    instruction: str

@router.post("/sermon-project/{sermon_id}/refine-draft")
def refine_draft_endpoint(sermon_id: str, payload: RefineRequest):
    """
    Refine a specific part of the draft based on user instruction.
    """
    try:
        refined_text = refine_sermon_draft(sermon_id, payload.selection, payload.instruction)
        return {"status": "success", "refined_text": refined_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class UpdateMetadataRequest(BaseModel):
    title: str
    bible_verse: Optional[str] = None

@router.post("/sermon-project/{sermon_id}/metadata", response_model=SermonProject)
def update_project_metadata(sermon_id: str, payload: UpdateMetadataRequest):
    """
    Update project title and bible verse.
    """
    try:
        # Import locally to ensure fresh reload during dev
        from backend.api.sermon_converter_service import update_sermon_project_metadata
        return update_sermon_project_metadata(sermon_id, payload.title, payload.bible_verse)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Sermon project not found")
    except ValueError as ve:
        # Handle the validation error we explicitly raise
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")


# --- Prompt Management Endpoints ---

@router.get("/prompts", response_model=List[Prompt])
def get_prompts_endpoint():
    return list_prompts()

@router.get("/prompts/{prompt_id}", response_model=Prompt)
def get_prompt_detail_endpoint(prompt_id: str):
    p = get_prompt(prompt_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return p

class CreatePromptRequest(BaseModel):
    name: str
    content: str
    temperature: float = 0.7

@router.post("/prompts", response_model=Prompt)
def create_prompt_endpoint(payload: CreatePromptRequest):
    return create_prompt(payload.name, payload.content, payload.temperature)

@router.put("/prompts/{prompt_id}", response_model=Prompt)
def update_prompt_endpoint(prompt_id: str, payload: CreatePromptRequest):
    p = update_prompt(prompt_id, payload.name, payload.content, payload.temperature)
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return p

@router.delete("/prompts/{prompt_id}")
def delete_prompt_endpoint(prompt_id: str):
    success = delete_prompt(prompt_id)
    if not success:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "success"}
