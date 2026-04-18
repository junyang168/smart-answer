import json
import uuid
from typing import List, Optional, Tuple
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path

from backend.api.config import DATA_BASE_PATH, FULL_ARTICLE_ROOT

# Define the storage file
SERIES_DB_PATH = DATA_BASE_PATH / "notes_to_surmon" / "series_db.json"
IMAGES_ROOT = FULL_ARTICLE_ROOT / "images" / "scanned_mat"

def list_series_folders() -> List[str]:
    """List subdirectories in full_article/images"""
    if not IMAGES_ROOT.exists():
        return []
    return [d.name for d in IMAGES_ROOT.iterdir() if d.is_dir()]

def list_lecture_folders(series_folder: str) -> List[str]:
    """List subdirectories in full_article/images/{series_folder}"""
    series_path = IMAGES_ROOT / series_folder
    if not series_path.exists() or not series_path.is_dir():
        return []
    return [d.name for d in series_path.iterdir() if d.is_dir()]

class Lecture(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    folder: Optional[str] = None
    project_ids: List[str] = [] # Ordered list of project IDs (Chapters)
    created_at: str
    updated_at: str

class LectureSeries(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    folder: Optional[str] = None
    project_type: str = "sermon_note"  # Default to existing behavior
    lectures: List[Lecture] = []
    created_at: str
    updated_at: str

def _ensure_db_init():
    if not SERIES_DB_PATH.exists():
        SERIES_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SERIES_DB_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)

def _load_db() -> List[dict]:
    _ensure_db_init()
    try:
        with open(SERIES_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_db(data: List[dict]):
    with open(SERIES_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _normalize_project_type(project_type: Optional[str]) -> str:
    if project_type == "Fellowship Transcript":
        return "transcript"
    return project_type or "sermon_note"


def _find_project_assignment(data: List[dict], project_id: str) -> Tuple[Optional[str], Optional[str]]:
    for series in data:
        for lecture in series.get("lectures", []):
            if project_id in lecture.get("project_ids", []):
                return series.get("id"), lecture.get("id")
    return None, None


def _sanitize_series_project_links(data: List[dict]) -> bool:
    try:
        from backend.api.sermon_converter_service import get_sermon_project_metadata
    except ImportError:
        return False

    changed = False
    timestamp = datetime.utcnow().isoformat()

    for series in data:
        series_type = _normalize_project_type(series.get("project_type"))
        if series.get("project_type") != series_type:
            series["project_type"] = series_type
            changed = True

        for lecture in series.get("lectures", []):
            original_ids = lecture.get("project_ids", [])
            valid_ids: List[str] = []
            seen: set[str] = set()

            for project_id in original_ids:
                if not project_id or project_id in seen:
                    changed = True
                    continue
                seen.add(project_id)

                project = get_sermon_project_metadata(project_id)
                if not project:
                    changed = True
                    continue

                project_type = _normalize_project_type(getattr(project, "project_type", None))
                if project_type != series_type:
                    changed = True
                    continue

                if getattr(project, "series_id", None) != series.get("id"):
                    changed = True
                    continue

                if getattr(project, "lecture_id", None) != lecture.get("id"):
                    changed = True
                    continue

                valid_ids.append(project_id)

            if original_ids != valid_ids:
                lecture["project_ids"] = valid_ids
                lecture["updated_at"] = timestamp
                series["updated_at"] = timestamp
                changed = True

    return changed

# --- Series CRUD ---

def list_series() -> List[LectureSeries]:
    data = _load_db()
    # Handle legacy data missing project_type
    for item in data:
        if "project_type" not in item:
            item["project_type"] = "sermon_note"
    if _sanitize_series_project_links(data):
        _save_db(data)
    return [LectureSeries(**item) for item in data]

def get_series(series_id: str) -> Optional[LectureSeries]:
    all_series = list_series()
    for s in all_series:
        if s.id == series_id:
            return s
    return None

def create_series(title: str, description: Optional[str] = None, folder: Optional[str] = None, project_type: str = "sermon_note") -> LectureSeries:
    new_series = LectureSeries(
        id=str(uuid.uuid4()),
        title=title,
        description=description,
        folder=folder,
        project_type=project_type,
        lectures=[],
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat()
    )
    data = _load_db()
    data.append(new_series.dict())
    _save_db(data)
    return new_series

def update_series_metadata(series_id: str, title: str, description: Optional[str] = None, folder: Optional[str] = None, project_type: Optional[str] = None) -> Optional[LectureSeries]:
    data = _load_db()
    for item in data:
        if item["id"] == series_id:
            item["title"] = title
            item["description"] = description
            if folder is not None:
                item["folder"] = folder
            
            if project_type is not None:
                # Check for change to cascade updates
                old_type = item.get("project_type", "sermon_note")
                item["project_type"] = project_type
                
                if project_type != old_type:
                    try:
                        from backend.api.sermon_converter_service import update_sermon_project_type
                        for lecture in item.get("lectures", []):
                            for project_id in lecture.get("project_ids", []):
                                try:
                                    update_sermon_project_type(project_id, project_type)
                                except Exception as e:
                                    print(f"Failed to update project type for {project_id}: {e}")
                    except ImportError:
                        pass # Should not happen given structure

            item["updated_at"] = datetime.utcnow().isoformat()
            _save_db(data)
            
            # Ensure return object has default if missing in old data (though we just saved it)
            if "project_type" not in item:
                item["project_type"] = "sermon_note"
                
            return LectureSeries(**item)
    return None

def delete_series(series_id: str) -> bool:
    data = _load_db()
    initial_len = len(data)
    data = [item for item in data if item["id"] != series_id]
    if len(data) < initial_len:
        _save_db(data)
        return True
    return False

# --- Lecture CRUD ---

def add_lecture(series_id: str, title: str, description: Optional[str] = None, folder: Optional[str] = None) -> Optional[Lecture]:
    data = _load_db()
    target_series = None
    for item in data:
        if item["id"] == series_id:
            target_series = item
            break
    
    if not target_series:
        return None

    new_lecture = Lecture(
        id=str(uuid.uuid4()),
        title=title,
        description=description,
        folder=folder,
        project_ids=[],
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat()
    )
    
    target_series["lectures"].append(new_lecture.dict())
    target_series["updated_at"] = datetime.utcnow().isoformat()
    _save_db(data)
    return new_lecture

def update_lecture(series_id: str, lecture_id: str, title: str, description: Optional[str] = None, folder: Optional[str] = None) -> Optional[Lecture]:
    data = _load_db()
    for s in data:
        if s["id"] == series_id:
            for l in s["lectures"]:
                if l["id"] == lecture_id:
                    l["title"] = title
                    l["description"] = description
                    if folder is not None:
                        l["folder"] = folder
                    l["updated_at"] = datetime.utcnow().isoformat()
                    s["updated_at"] = datetime.utcnow().isoformat()
                    _save_db(data)
                    return Lecture(**l)
    return None

def delete_lecture(series_id: str, lecture_id: str) -> bool:
    data = _load_db()
    for s in data:
        if s["id"] == series_id:
            initial_len = len(s["lectures"])
            s["lectures"] = [l for l in s["lectures"] if l["id"] != lecture_id]
            if len(s["lectures"]) < initial_len:
                s["updated_at"] = datetime.utcnow().isoformat()
                _save_db(data)
                return True
    return False

# --- Project Assignment ---

def assign_project_to_lecture(series_id: str, lecture_id: str, project_id: str) -> bool:
    """
    Add a project ID to a lecture's project list. 
    """
    from backend.api.sermon_converter_service import (
        get_sermon_project_metadata,
        update_sermon_project_linking,
    )
    
    data = _load_db()
    target_series = None
    target_lecture = None
    for s in data:
        if s["id"] == series_id:
            target_series = s
            for l in s["lectures"]:
                if l["id"] == lecture_id:
                    target_lecture = l
                    break
            break

    if not target_series or not target_lecture:
        return False

    project = get_sermon_project_metadata(project_id)
    if not project:
        return False

    target_series_type = _normalize_project_type(target_series.get("project_type"))
    project_type = _normalize_project_type(getattr(project, "project_type", None))
    if project_type != target_series_type:
        return False

    assigned_series_id, assigned_lecture_id = _find_project_assignment(data, project_id)
    if assigned_series_id and assigned_lecture_id and (assigned_series_id, assigned_lecture_id) != (series_id, lecture_id):
        return False

    if project.series_id and project.lecture_id and (project.series_id, project.lecture_id) != (series_id, lecture_id):
        return False

    if project_id not in target_lecture["project_ids"]:
        target_lecture["project_ids"].append(project_id)
        target_lecture["updated_at"] = datetime.utcnow().isoformat()
        target_series["updated_at"] = datetime.utcnow().isoformat()
        _save_db(data)

    update_sermon_project_linking(project_id, series_id, lecture_id)
    return True

def remove_project_from_lecture(series_id: str, lecture_id: str, project_id: str) -> bool:
    from backend.api.sermon_converter_service import (
        get_sermon_project_metadata,
        update_sermon_project_linking,
    )

    data = _load_db()
    for s in data:
        if s["id"] == series_id:
            for l in s["lectures"]:
                if l["id"] == lecture_id:
                    if project_id in l["project_ids"]:
                        l["project_ids"].remove(project_id)
                        l["updated_at"] = datetime.utcnow().isoformat()
                        s["updated_at"] = datetime.utcnow().isoformat()
                        _save_db(data)

                        project = get_sermon_project_metadata(project_id)
                        if project and (project.series_id, project.lecture_id) == (series_id, lecture_id):
                            update_sermon_project_linking(project_id, None, None)
                        return True
    return False

def reorder_lecture_projects(series_id: str, lecture_id: str, project_ids: List[str]) -> bool:
    data = _load_db()
    for s in data:
        if s["id"] == series_id:
            for l in s["lectures"]:
                if l["id"] == lecture_id:
                    # Validate that all provided IDs are currently in the lecture (or subset/superset handling?)
                    # For safety, let's just ensure we are only reordering the existing set.
                    current_set = set(l["project_ids"])
                    new_set = set(project_ids)
                    
                    if current_set != new_set:
                        # If the sets don't match, we might be accidentally adding/removing.
                        # But for UI "sort", usually we send the whole list.
                        # Let's allow it but warn or strict check? 
                        # Strict check: the new list must contain exactly the same IDs as the old list.
                        return False 
                        
                    l["project_ids"] = project_ids
                    l["updated_at"] = datetime.utcnow().isoformat()
                    s["updated_at"] = datetime.utcnow().isoformat()
                    _save_db(data)
                    return True
    return False
