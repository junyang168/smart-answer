import json
import uuid
from typing import List, Optional
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

# --- Series CRUD ---

def list_series() -> List[LectureSeries]:
    data = _load_db()
    return [LectureSeries(**item) for item in data]

def get_series(series_id: str) -> Optional[LectureSeries]:
    all_series = list_series()
    for s in all_series:
        if s.id == series_id:
            return s
    return None

def create_series(title: str, description: Optional[str] = None, folder: Optional[str] = None) -> LectureSeries:
    new_series = LectureSeries(
        id=str(uuid.uuid4()),
        title=title,
        description=description,
        folder=folder,
        lectures=[],
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat()
    )
    data = _load_db()
    data.append(new_series.dict())
    _save_db(data)
    return new_series

def update_series_metadata(series_id: str, title: str, description: Optional[str] = None, folder: Optional[str] = None) -> Optional[LectureSeries]:
    data = _load_db()
    for item in data:
        if item["id"] == series_id:
            item["title"] = title
            item["description"] = description
            if folder is not None:
                item["folder"] = folder
            item["updated_at"] = datetime.utcnow().isoformat()
            _save_db(data)
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
    from backend.api.sermon_converter_service import update_sermon_project_linking
    
    data = _load_db()
    for s in data:
        if s["id"] == series_id:
            for l in s["lectures"]:
                if l["id"] == lecture_id:
                    if project_id not in l["project_ids"]:
                        l["project_ids"].append(project_id)
                        l["updated_at"] = datetime.utcnow().isoformat()
                        s["updated_at"] = datetime.utcnow().isoformat()
                        _save_db(data)
                        
                        # Update Project Meta
                        update_sermon_project_linking(project_id, series_id, lecture_id)
                        return True
                    return True # Already assigned
    return False

def remove_project_from_lecture(series_id: str, lecture_id: str, project_id: str) -> bool:
    from backend.api.sermon_converter_service import update_sermon_project_linking

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
                        
                        # Unlink Project Meta
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
