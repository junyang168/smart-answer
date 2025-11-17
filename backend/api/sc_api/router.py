from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api.config import DATA_BASE_PATH

from .copilot import ChatMessage
from .qaManager import QAItem, qaManager
from .sermon_manager import Permission, sermonManager

sermon_manager = sermonManager
qa_manager = qaManager

SLIDE_WEB_PREFIX = "/web/data/slides"

router = APIRouter(prefix="/sc_api", tags=["sc-api"])


class Paragraph(BaseModel):
    index: Optional[Union[int, str]] = None
    text: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    type: Optional[str] = None
    end_time: Optional[int] = None
    s_index: Optional[int] = None
    start_index: Optional[Union[int, str]] = None
    start_time: Optional[int] = None
    start_timeline: Optional[str] = None
    end_index: Optional[Union[int, str]] = None


class Slide(BaseModel):
    time: int
    text: str


class UpdateRequest(BaseModel):
    user_id: Optional[str] = None
    item: str
    type: str
    data: List[Union[Slide, Paragraph]]


class BibleVerse(BaseModel):
    book: Optional[str] = None
    chapter_verse: Optional[str] = None
    text: Optional[str] = None


class UpdateHeaderRequest(BaseModel):
    user_id: Optional[str] = None
    item: str
    title: str
    summary: Optional[str] = None
    keypoints: Optional[str] = None
    core_bible_verse: Optional[List[BibleVerse]] = None


class GenerateMetadataRequest(BaseModel):
    user_id: Optional[str] = None
    item: str
    paragraphs: Optional[List[Paragraph]] = None


class AssignRequest(BaseModel):
    user_id: str
    item: str
    action: str


class SearchRequest(BaseModel):
    item: str
    text_list: List[str]


class ChatRequest(BaseModel):
    item: str
    history: List[ChatMessage]


class SurmonSlideAsset(BaseModel):
    id: str
    image: str
    image_url: str
    timestamp_seconds: Optional[float] = None
    average_rgb: Optional[List[int]] = None
    extracted_text: Optional[str] = None


class SurmonSlideResponse(BaseModel):
    item: str
    video: Optional[str] = None
    slides: List[SurmonSlideAsset]


class SeriesMarkdownRequest(BaseModel):
    user_id: str


class SeriesMarkdownResponse(BaseModel):
    seriesId: str
    outputDir: str
    sermonCount: int
    generatedFiles: List[str]


def _normalize_slide_path(item: str, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            slides_index = candidate.parts.index("slides")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Slide image path must be relative to slides directory") from exc
        candidate = Path(*candidate.parts[slides_index + 1 :])

    if candidate.parts and candidate.parts[0] != item:
        candidate = Path(item) / candidate

    safe_parts = [part for part in candidate.parts if part not in ("..", ".")]
    return Path(*safe_parts)


def _slides_base_path() -> Path:
    return (DATA_BASE_PATH / "slides").resolve()


def _build_slide_url(relative_path: Path) -> str:
    return f"{SLIDE_WEB_PREFIX}/{relative_path.as_posix()}"


def _load_slide_metadata(item: str) -> SurmonSlideResponse:
    sanitized_item = item.strip()
    if not sanitized_item:
        raise HTTPException(status_code=400, detail="Surmon item is required")

    slides_root = _slides_base_path() / sanitized_item
    if not slides_root.exists():
        raise HTTPException(status_code=404, detail=f"Slides not found for item {sanitized_item}")

    meta_path = slides_root / "slide_meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"slide_meta.json missing for item {sanitized_item}")

    try:
        metadata = meta_path.read_text(encoding="utf-8")
        metadata_dict = json.loads(metadata)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid slide metadata JSON") from exc

    raw_slides = metadata_dict.get("slides", [])
    if not isinstance(raw_slides, list):
        raise HTTPException(status_code=422, detail="Invalid slides format in metadata")

    slide_items: List[SurmonSlideAsset] = []
    slides_base = _slides_base_path()
    for entry in raw_slides:
        if not isinstance(entry, dict):
            continue
        image_value = entry.get("image")
        if not image_value:
            continue
        relative_path = _normalize_slide_path(sanitized_item, image_value)
        slide_path = slides_base / relative_path
        if not slide_path.exists():
            raise HTTPException(status_code=404, detail=f"Slide image not found: {relative_path.as_posix()}")

        timestamp = entry.get("timestamp_seconds")
        avg_rgb = entry.get("average_rgb")
        average_rgb = None
        if isinstance(avg_rgb, (list, tuple)) and len(avg_rgb) >= 3:
            try:
                average_rgb = [int(avg_rgb[0]), int(avg_rgb[1]), int(avg_rgb[2])]
            except (TypeError, ValueError):
                average_rgb = None

        slide_items.append(
            SurmonSlideAsset(
                id=relative_path.stem,
                image=relative_path.as_posix(),
                image_url=_build_slide_url(relative_path),
                timestamp_seconds=float(timestamp) if isinstance(timestamp, (int, float)) else None,
                average_rgb=average_rgb,
                extracted_text=entry.get("extracted_text") or entry.get("text"),
            )
        )

    slide_items.sort(key=lambda slide: (slide.timestamp_seconds is None, slide.timestamp_seconds))

    return SurmonSlideResponse(
        item=sanitized_item,
        video=metadata_dict.get("video"),
        slides=slide_items,
    )


def _sanitize_segment(value: str) -> str:
    cleaned = value.strip()
    cleaned = cleaned.replace("/", "").replace("\\", "")
    return cleaned


def _get_file_path(file_type: str, item: str, ext: str = ".txt") -> Path:
    safe_type = _sanitize_segment(file_type)
    safe_item = _sanitize_segment(item)
    return (DATA_BASE_PATH / safe_type / f"{safe_item}{ext}").resolve()


@router.get("/load/{user_id}/{file_type}/{item}/{ext}")
def load(user_id: str, file_type: str, item: str, ext: str = "txt") -> str:
    permissions: Permission = sermon_manager.get_sermon_permissions(user_id, item)
    if not permissions.canRead:
        raise HTTPException(status_code=403, detail="You don't have permission to read this item")

    file_path = _get_file_path(file_type, item, f".{ext}")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Requested file not found")
    return file_path.read_text(encoding="utf-8")


@router.post("/update_script")
def update_script(request: UpdateRequest):
    return sermon_manager.update_sermon(request.user_id or "", request.type, request.item, request.data)


@router.post("/update_header")
def update_header(request: UpdateHeaderRequest):
    core_bible_verse = (
        [verse.model_dump(exclude_none=True) for verse in request.core_bible_verse]
        if request.core_bible_verse is not None
        else None
    )
    return sermon_manager.update_sermon_header(
        request.user_id or "",
        request.item,
        request.title,
        summary=request.summary,
        keypoints=request.keypoints,
        core_bible_verse=core_bible_verse,
    )


@router.post("/generate_metadata")
def generate_metadata(request: GenerateMetadataRequest):
    try:
        return sermon_manager.generate_sermon_metadata(
            request.user_id or "",
            request.item,
            request.paragraphs,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected failure path
        raise HTTPException(status_code=500, detail="AI 產生講道資訊時發生錯誤") from exc


@router.get("/sermon/{user_id}/{item}/{changes}")
def get_sermon(user_id: str, item: str, changes: Optional[str] = None):
    header, script = sermon_manager.get_sermon_detail(user_id, item, changes)
    return {"header": header, "script": script}


@router.get("/sermons/{user_id}/{item}/history")
def get_sermon_history(user_id: str, item: str, limit: int = 50):
    permissions: Permission = sermon_manager.get_sermon_permissions(user_id, item)
    if not permissions.canRead:
        raise HTTPException(status_code=403, detail="You don't have permission to view this history")
    sanitized_limit = max(1, min(limit, 200))
    return sermon_manager.get_sermon_audit_log(item, sanitized_limit)


@router.get("/slide_text/{user_id}/{item}/{timestamp}")
def get_slide(user_id: str, item: str, timestamp: int):
    return sermon_manager.get_slide_text(user_id, item, timestamp)


@router.get("/slide_image/{user_id}/{item}/{timestamp}")
def get_slide_image(user_id: str, item: str, timestamp: int):
    return sermon_manager.get_slide_image(user_id, item, timestamp)


@router.get("/permissions/{user_id}/{item}")
def get_permissions(user_id: str, item: str) -> Permission:
    return sermon_manager.get_sermon_permissions(user_id, item)


@router.get("/sermons/{user_id}")
def get_sermons(user_id: str):
    return sermon_manager.get_sermons(user_id)


@router.get("/sermons/{item}/slides", response_model=SurmonSlideResponse)
def get_surmon_slides(item: str) -> SurmonSlideResponse:
    return _load_slide_metadata(item)


@router.post("/assign")
def assigned_to(req: AssignRequest):
    result = sermon_manager.assign(req.user_id, req.item, req.action)
    if result is None:
        raise HTTPException(status_code=403, detail="Assignment action not permitted")
    return result


@router.get("/bookmark/{user_id}/{item}")
def get_bookmark(user_id: str, item: str):
    return sermon_manager.get_bookmark(user_id, item)


@router.put("/bookmark/{user_id}/{item}/{index}")
def set_bookmark(user_id: str, item: str, index: str):
    return sermon_manager.set_bookmark(user_id, item, index)


@router.get("/users")
def get_users():
    return sermon_manager.get_users()


@router.get("/user/{user_id}")
def get_user_info(user_id: str):
    return sermon_manager.get_user_info(user_id)


@router.put("/publish/{user_id}/{item}")
def publish(user_id: str, item: str):
    return sermon_manager.publish(user_id, item)


@router.get("/final_sermon/{user_id}/{item}")
def get_final_sermon(user_id: str, item: str, remove_tags: bool = True):
    return sermon_manager.get_final_sermon(user_id, item, remove_tags)


@router.get("/sermon_series")
def get_sermon_series():
    return sermon_manager.get_sermon_series()


@router.post("/series/{series_id}/markdown", response_model=SeriesMarkdownResponse)
def export_series_markdown(series_id: str, payload: SeriesMarkdownRequest) -> SeriesMarkdownResponse:
    try:
        result = sermon_manager.export_series_markdown(payload.user_id, series_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "找不到系列" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return SeriesMarkdownResponse(**result)


@router.get("/article_series")
def get_article_series():
    return sermon_manager.get_article_series()


@router.get("/article/{article_id}")
def get_article(article_id: str):
    return sermon_manager.get_article_with_series(article_id)


@router.get("/top_sermon_articles/{count}")
def get_latest_sermons_articles(count: int = 2):
    tops = sermon_manager.get_latest_sermons_articles(count)
    tops["qas"] = qa_manager.get_top_qas(count)
    return tops


@router.get("/fellowship")
def get_fellowship():
    return sermon_manager.get_next_fellowship()


@router.post("/search")
def search_script(req: SearchRequest):
    return sermon_manager.search_script(req.item, req.text_list)


@router.get("/quick_search/{term}")
def quick_search(term: str) -> List[str]:
    return sermon_manager.quick_search(term)


@router.post("/chat/{user_id}")
def chat(user_id: str, req: ChatRequest):
    return sermon_manager.chat(user_id, req.item, req.history)


@router.post("/surmon_chat/{user_id}")
def surmon_chat(user_id: str, req: ChatRequest):
    return sermon_manager.surmon_llm_chat(user_id, req.item, req.history)


@router.post("/qa/{user_id}")
def qa(user_id: str, history: List[ChatMessage]):
    return sermon_manager.qa(user_id, history)


@router.get("/qas")
def get_qas(articleId: Optional[str] = None) -> List[QAItem]:
    return qa_manager.get_qas(articleId)


@router.post("/qas/{user_id}")
def add_qa(user_id: str, qa_item: QAItem) -> QAItem:
    return qa_manager.add_qa(user_id, qa_item)


@router.put("/qas/{user_id}")
def update_qa(user_id: str, qa_item: QAItem) -> QAItem:
    return qa_manager.update_qa(user_id, qa_item)


@router.delete("/qas/{user_id}/{qa_id}")
def delete_qa(user_id: str, qa_id: str):
    return qa_manager.delete_qa(qa_id)


@router.get("/qas/{user_id}/{qa_id}")
def get_qa_by_id(user_id: str, qa_id: str) -> Optional[QAItem]:
    return qa_manager.get_qa_by_id(user_id, qa_id)
