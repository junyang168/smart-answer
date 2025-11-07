from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import cv2
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from .config import DATA_BASE_PATH
from .models import SurmonSlideAsset
from .slide_detector import (
    ExecutableNotFoundError,
    FrameRegion,
    SlideDetectionConfig,
    capture_video_frame,
    run_slide_detection,
    build_slide_summary,
)
from .sc_api.image_to_text import ImageToText

SLIDES_DIR_NAME = "slides"
SLIDE_WEB_PREFIX = "/web/data/slides"
FRAME_IMAGE_NAME = "frame.jpg"
FRAME_METADATA_NAME = "frame.json"
FRAME_REFERENCE_SECONDS = 60.0
FRAMES_SUBDIR = "frames"
CAPTURES_SUBDIR = "captures"
RAW_SUBDIR = "_raw"
VIDEO_DIR_NAME = "../video"

router = APIRouter(prefix="/slides", tags=["slides"])


class FrameDimensions(BaseModel):
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)


class FrameCoordinates(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)


class FrameInfo(BaseModel):
    timestamp_seconds: float
    image: str
    image_url: str
    coordinates: Optional[FrameCoordinates] = None
    frame_dimensions: Optional[FrameDimensions] = None
    updated_at: Optional[str] = None


class FrameUpdatePayload(BaseModel):
    coordinates: FrameCoordinates
    frame_dimensions: FrameDimensions


class FrameCapturePayload(BaseModel):
    timestamp_seconds: float = Field(..., ge=0.0)


class CapturePersistPayload(BaseModel):
    timestamp_seconds: Optional[float] = Field(default=None, ge=0.0)
    extracted_text: Optional[str] = None


class SlideGenerationResponse(BaseModel):
    count: int
    metadata_path: str
    slides: List[SurmonSlideAsset]


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _sanitize_item(item_id: str) -> str:
    item = item_id.strip()
    if not item:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Item id is required")
    return item


def _slide_root(item: str) -> Path:
    return DATA_BASE_PATH / SLIDES_DIR_NAME / item


def _frames_output_dir(item: str) -> Path:
    return _slide_root(item) / FRAMES_SUBDIR


def _captures_output_dir(item: str) -> Path:
    return _slide_root(item) / CAPTURES_SUBDIR


def _raw_output_dir(item: str) -> Path:
    return _slide_root(item) / RAW_SUBDIR


def _frame_image_path(item: str) -> Path:
    return _slide_root(item) / FRAME_IMAGE_NAME


def _frame_metadata_path(item: str) -> Path:
    return _slide_root(item) / FRAME_METADATA_NAME


def _normalize_image_path(item: str, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        try:
            slides_index = candidate.parts.index(SLIDES_DIR_NAME)
            candidate = Path(*candidate.parts[slides_index + 1 :])
        except ValueError as exc:  # pragma: no cover - defensive branch
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Slide image path must be relative") from exc

    candidate = Path(*(part for part in candidate.parts if part not in ("..", ".")))
    if not candidate.parts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid slide image path")

    if candidate.parts[0] != item:
        candidate = Path(item) / candidate
    return candidate


def _build_image_url(relative_path: Path) -> str:
    return f"{SLIDE_WEB_PREFIX}/{quote(relative_path.as_posix(), safe='/')}"


def _parse_slide(item: str, data: dict[str, Any]) -> SurmonSlideAsset:
    image_value = data.get("image")
    if not isinstance(image_value, str) or not image_value.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Slide entry missing image field")

    relative_path = _normalize_image_path(item, image_value)
    full_path = DATA_BASE_PATH / SLIDES_DIR_NAME / relative_path
    if not full_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Slide image not found: {relative_path.as_posix()}")

    timestamp_value = data.get("timestamp_seconds")
    timestamp = float(timestamp_value) if isinstance(timestamp_value, (int, float)) else None

    average_rgb: Optional[List[int]] = None
    rgb_value = data.get("average_rgb")
    if isinstance(rgb_value, (list, tuple)) and len(rgb_value) >= 3:
        try:
            average_rgb = [int(rgb_value[0]), int(rgb_value[1]), int(rgb_value[2])]
        except (TypeError, ValueError):  # pragma: no cover - defensive
            average_rgb = None

    extracted_text_value = data.get("extracted_text") or data.get("text")
    extracted_text = extracted_text_value if isinstance(extracted_text_value, str) else None

    return SurmonSlideAsset(
        id=relative_path.stem,
        image=relative_path.as_posix(),
        image_url=_build_image_url(relative_path),
        timestamp_seconds=timestamp,
        average_rgb=average_rgb,
        extracted_text=extracted_text,
    )


def _list_slide_assets(item: str) -> list[SurmonSlideAsset]:
    try:
        metadata = _load_slide_metadata_document(item)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            return []
        raise
    raw_slides = metadata.get("slides", [])
    if not isinstance(raw_slides, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid slides format in metadata")

    slides: list[SurmonSlideAsset] = []
    for entry in raw_slides:
        if not isinstance(entry, dict):
            continue
        try:
            slides.append(_parse_slide(item, entry))
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    slides.sort(key=lambda slide: (slide.timestamp_seconds is None, slide.timestamp_seconds))
    return slides


def _load_slide_metadata_document(item: str) -> dict[str, Any]:
    slides_root = _slide_root(item)
    if not slides_root.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "slides directory not found")

    metadata_path = slides_root / "slide_meta.json"
    if not metadata_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "slide_meta.json not found")

    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid slide metadata JSON") from exc
    slides_value = metadata.get("slides")
    if isinstance(slides_value, list):
        metadata["slides"] = _sort_slide_entries(slides_value)
    return metadata


def _load_or_initialize_slide_metadata(item: str) -> Tuple[dict[str, Any], Path]:
    slides_root = _slide_root(item)
    slides_root.mkdir(parents=True, exist_ok=True)
    metadata_path = slides_root / "slide_meta.json"
    if metadata_path.exists():
        try:
            with metadata_path.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)
                slides_value = metadata.get("slides")
                if isinstance(slides_value, list):
                    metadata["slides"] = _sort_slide_entries(slides_value)
                return metadata, metadata_path
        except json.JSONDecodeError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid slide metadata JSON") from exc
    return {"slides": []}, metadata_path


def _locate_video_file(item: str) -> Path:
    video_root = Path('/Volumes/Jun SSD/data/video').resolve()
    if not video_root.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video directory is missing on the server")

    for extension in (".mp4", ".mov", ".m4v", ".MP4", ".MOV", ".M4V"):
        candidate = video_root / f"{item}{extension}"
        if candidate.exists():
            return candidate
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"Video not found for item {item}")


def _ensure_frame_image(item: str) -> Path:
    slides_root = _slide_root(item)
    slides_root.mkdir(parents=True, exist_ok=True)
    frame_path = _frame_image_path(item)
    if frame_path.exists():
        return frame_path

    video_path = _locate_video_file(item)
    try:
        capture_video_frame(video_path, FRAME_REFERENCE_SECONDS, frame_path)
    except ExecutableNotFoundError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to capture reference frame") from exc
    return frame_path


def _load_frame_metadata(item: str) -> Optional[Dict[str, Any]]:
    metadata_path = _frame_metadata_path(item)
    if not metadata_path.exists():
        return None
    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid frame metadata JSON") from exc


def _build_frame_info(item: str, metadata: Optional[Dict[str, Any]]) -> FrameInfo:
    frame_path = _ensure_frame_image(item)
    relative_image = Path(item) / frame_path.name

    coordinates_model: Optional[FrameCoordinates] = None
    dimensions_model: Optional[FrameDimensions] = None
    updated_at: Optional[str] = None
    timestamp_seconds = FRAME_REFERENCE_SECONDS

    if metadata:
        coordinates_data = metadata.get("coordinates")
        dimensions_data = metadata.get("frame_dimensions")
        updated_at = metadata.get("updated_at")
        timestamp_value = metadata.get("timestamp_seconds")
        if isinstance(timestamp_value, (int, float)):
            timestamp_seconds = float(timestamp_value)

        try:
            if isinstance(coordinates_data, dict):
                coordinates_model = FrameCoordinates(**coordinates_data)
            if isinstance(dimensions_data, dict):
                dimensions_model = FrameDimensions(**dimensions_data)
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid frame metadata: {exc}") from exc

    return FrameInfo(
        timestamp_seconds=timestamp_seconds,
        image=relative_image.as_posix(),
        image_url=_build_image_url(relative_image),
        coordinates=coordinates_model,
        frame_dimensions=dimensions_model,
        updated_at=updated_at,
    )


def _build_capture_asset(
    item: str,
    capture_path: Path,
    *,
    timestamp_seconds: Optional[float] = None,
    extracted_text: Optional[str] = None,
) -> SurmonSlideAsset:
    relative_path = Path(item) / CAPTURES_SUBDIR / capture_path.name
    return SurmonSlideAsset(
        id=capture_path.stem,
        image=relative_path.as_posix(),
        image_url=_build_image_url(relative_path),
        timestamp_seconds=timestamp_seconds,
        extracted_text=extracted_text,
    )


def _capture_image_path(item: str, capture_id: str) -> Path:
    sanitized = capture_id.strip()
    if not sanitized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Capture id is required")
    capture_dir = _captures_output_dir(item)
    candidate = capture_dir / f"{sanitized}.jpg"
    if not candidate.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Capture {sanitized} not found")
    return candidate


def _timestamp_from_capture_name(path: Path) -> Optional[float]:
    stem = path.stem
    if not stem.startswith("capture-"):
        return None
    parts = stem.split("-")
    if len(parts) < 3:
        return None
    try:
        millis = int(parts[1])
    except ValueError:
        return None
    return millis / 1000.0


def _sort_slide_entries(slide_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid_entries: List[Dict[str, Any]] = [entry for entry in slide_entries if isinstance(entry, dict)]
    invalid_entries = [entry for entry in slide_entries if not isinstance(entry, dict)]

    def sort_key(entry: Dict[str, Any]) -> Tuple[bool, float, str]:
        timestamp = entry.get("timestamp_seconds")
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            value = float(timestamp)
            missing = False
        else:
            value = float("inf")
            missing = True
        image_value = entry.get("image")
        image_key = image_value if isinstance(image_value, str) else ""
        return (missing, value, image_key)

    return sorted(valid_entries, key=sort_key) + invalid_entries


def _persist_capture_metadata(
    item: str,
    capture_path: Path,
    *,
    timestamp_seconds: Optional[float] = None,
    extracted_text: Optional[str] = None,
) -> SurmonSlideAsset:
    metadata, metadata_path = _load_or_initialize_slide_metadata(item)
    slides_value = metadata.get("slides")
    if not isinstance(slides_value, list):
        slides_value = []
        metadata["slides"] = slides_value

    relative_image = (Path(item) / CAPTURES_SUBDIR / capture_path.name).as_posix()
    existing_entry: Optional[dict[str, Any]] = None
    for entry in slides_value:
        if isinstance(entry, dict) and entry.get("image") == relative_image:
            existing_entry = entry
            break

    if existing_entry is None:
        existing_entry = {"image": relative_image}
        slides_value.append(existing_entry)

    if timestamp_seconds is not None:
        existing_entry["timestamp_seconds"] = float(timestamp_seconds)

    if extracted_text:
        sanitized = extracted_text.strip()
        if sanitized:
            existing_entry["extracted_text"] = sanitized
            existing_entry["text"] = sanitized

    metadata["slides"] = _sort_slide_entries(slides_value)
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, ensure_ascii=False)

    return _build_capture_asset(
        item,
        capture_path,
        timestamp_seconds=existing_entry.get("timestamp_seconds"),
        extracted_text=existing_entry.get("extracted_text"),
    )


def _validate_coordinates(payload: FrameUpdatePayload) -> None:
    dims = payload.frame_dimensions
    coords = payload.coordinates
    if coords.width > dims.width or coords.height > dims.height:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Frame size cannot exceed the captured frame dimensions",
        )
    if coords.x + coords.width > dims.width or coords.y + coords.height > dims.height:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Frame selection must stay within the captured frame",
        )


def _write_frame_metadata(item: str, payload: FrameUpdatePayload) -> Dict[str, Any]:
    _validate_coordinates(payload)
    metadata_path = _frame_metadata_path(item)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: Dict[str, Any] = {
        "timestamp_seconds": FRAME_REFERENCE_SECONDS,
        "image": FRAME_IMAGE_NAME,
        "coordinates": payload.coordinates.dict(),
        "frame_dimensions": payload.frame_dimensions.dict(),
        "updated_at": _utc_now_iso(),
    }
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
    return metadata


def _frame_region_from_metadata(item: str, metadata: Optional[Dict[str, Any]]) -> FrameRegion:
    if not metadata:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Frame coordinates are required before generating slides",
        )
    coordinates_data = metadata.get("coordinates")
    dimensions_data = metadata.get("frame_dimensions")
    if not isinstance(coordinates_data, dict) or not isinstance(dimensions_data, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Frame coordinates are required before generating slides",
        )
    try:
        coordinates = FrameCoordinates(**coordinates_data)
        FrameDimensions(**dimensions_data)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid frame metadata: {exc}") from exc

    return FrameRegion(
        x=coordinates.x,
        y=coordinates.y,
        width=coordinates.width,
        height=coordinates.height,
    )


def _build_slide_entries(item: str, records: list[SurmonSlideAsset]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for asset in records:
        entry: dict[str, Any] = {
            "image": asset.image,
            "timestamp_seconds": asset.timestamp_seconds,
            "average_rgb": asset.average_rgb,
        }
        if asset.extracted_text:
            entry["extracted_text"] = asset.extracted_text
        entries.append(entry)
    return entries


def _write_slide_metadata(
    item: str,
    metadata: Dict[str, Any],
    slides: list[SurmonSlideAsset],
) -> None:
    slides_root = _slide_root(item)
    slides_root.mkdir(parents=True, exist_ok=True)
    metadata_path = slides_root / "slide_meta.json"

    document: Dict[str, Any] = {
        "video": metadata.get("video"),
        "scene_threshold": metadata.get("scene_threshold"),
        "blue_min": metadata.get("blue_min"),
        "blue_dominance": metadata.get("blue_dominance"),
        "generated_at": metadata.get("generated_at"),
        "frame": metadata.get("frame"),
        "slides": _sort_slide_entries(_build_slide_entries(item, slides)),
    }

    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)


@router.post("/{item_id}/captures", response_model=SurmonSlideAsset)
def capture_custom_frame(item_id: str, payload: FrameCapturePayload) -> SurmonSlideAsset:
    item = _sanitize_item(item_id)
    timestamp = max(0.0, float(payload.timestamp_seconds))
    capture_dir = _captures_output_dir(item)
    capture_dir.mkdir(parents=True, exist_ok=True)

    timestamp_ms = int(round(timestamp * 1000))
    suffix = uuid.uuid4().hex[:6]
    filename = f"capture-{timestamp_ms}-{suffix}.jpg"
    destination = capture_dir / filename

    video_path = _locate_video_file(item)
    try:
        capture_video_frame(video_path, timestamp, destination)
    except ExecutableNotFoundError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to capture video frame") from exc

    return _build_capture_asset(item, destination, timestamp_seconds=timestamp)


@router.post("/{item_id}/captures/{capture_id}/extract_text", response_model=SurmonSlideAsset)
def extract_custom_frame_text(item_id: str, capture_id: str) -> SurmonSlideAsset:
    item = _sanitize_item(item_id)
    image_path = _capture_image_path(item, capture_id)

    frame = cv2.imread(str(image_path))
    if frame is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unable to load capture image")

    extractor = ImageToText(item)
    extracted_markdown = extractor.extract_text_from_frame(frame, as_markdown=True)
    if not extracted_markdown:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Gemini 未回傳任何文字")

    timestamp_seconds = _timestamp_from_capture_name(image_path)
    markdown_text = extracted_markdown.strip()
    return _build_capture_asset(
        item,
        image_path,
        timestamp_seconds=timestamp_seconds,
        extracted_text=markdown_text,
    )


@router.post("/{item_id}/captures/{capture_id}/persist", response_model=SurmonSlideAsset)
def persist_capture_entry(item_id: str, capture_id: str, payload: CapturePersistPayload) -> SurmonSlideAsset:
    item = _sanitize_item(item_id)
    image_path = _capture_image_path(item, capture_id)
    timestamp = payload.timestamp_seconds
    if timestamp is None:
        timestamp = _timestamp_from_capture_name(image_path)
    return _persist_capture_metadata(
        item,
        image_path,
        timestamp_seconds=timestamp,
        extracted_text=payload.extracted_text,
    )


@router.get("/{item_id}", response_model=list[SurmonSlideAsset])
def list_surmon_slides(item_id: str) -> list[SurmonSlideAsset]:
    item = _sanitize_item(item_id)
    return _list_slide_assets(item)


@router.post("/{item_id}/{slide_id}/extract_text", response_model=SurmonSlideAsset)
def extract_slide_text(item_id: str, slide_id: str) -> SurmonSlideAsset:
    item = _sanitize_item(item_id)
    slide = slide_id.strip()
    if not slide:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Slide id is required")

    try:
        metadata = _load_slide_metadata_document(item)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Slides metadata not found") from exc
        raise

    raw_slides = metadata.get("slides")
    if not isinstance(raw_slides, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid slides format in metadata")

    target_index: Optional[int] = None
    target_entry: Optional[dict[str, Any]] = None
    for index, entry in enumerate(raw_slides):
        if not isinstance(entry, dict):
            continue
        image_value = entry.get("image")
        if not isinstance(image_value, str) or not image_value:
            continue
        relative_path = _normalize_image_path(item, image_value)
        if relative_path.stem == slide:
            target_index = index
            target_entry = entry
            break

    if target_index is None or target_entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Slide {slide} not found")

    relative_path = _normalize_image_path(item, target_entry.get("image", ""))
    image_path = DATA_BASE_PATH / SLIDES_DIR_NAME / relative_path
    if not image_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Slide image not found: {relative_path.as_posix()}")

    frame = cv2.imread(str(image_path))
    if frame is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unable to load slide image")

    extractor = ImageToText(item)
    extracted_markdown = extractor.extract_text_from_frame(frame, as_markdown=True)
    if not extracted_markdown:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Gemini 未回傳任何文字")

    markdown_text = extracted_markdown.strip()
    target_entry["extracted_text"] = markdown_text
    target_entry["text"] = markdown_text  # legacy compatibility
    raw_slides[target_index] = target_entry

    metadata_path = _slide_root(item) / "slide_meta.json"
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, ensure_ascii=False)

    return _parse_slide(item, target_entry)


@router.get("/{item_id}/frame", response_model=FrameInfo)
def get_frame_info(item_id: str) -> FrameInfo:
    item = _sanitize_item(item_id)
    metadata = _load_frame_metadata(item)
    return _build_frame_info(item, metadata)


@router.put("/{item_id}/frame", response_model=FrameInfo)
def update_frame_info(item_id: str, payload: FrameUpdatePayload) -> FrameInfo:
    item = _sanitize_item(item_id)
    _ensure_frame_image(item)
    metadata = _write_frame_metadata(item, payload)
    return _build_frame_info(item, metadata)


@router.post("/{item_id}/generate", response_model=SlideGenerationResponse)
def generate_slides(item_id: str) -> SlideGenerationResponse:
    item = _sanitize_item(item_id)
    frame_metadata = _load_frame_metadata(item)
    region = _frame_region_from_metadata(item, frame_metadata)

    video_path = _locate_video_file(item)
    slides_root = _slide_root(item)
    slides_root.mkdir(parents=True, exist_ok=True)
    frames_dir = _frames_output_dir(item)
    raw_dir = _raw_output_dir(item)

    config = SlideDetectionConfig(
        video=video_path,
        raw_dir=raw_dir,
        output_dir=frames_dir,
        crop_region=region,
    )

    try:
        records = run_slide_detection(config)
    except ExecutableNotFoundError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Slide generation failed") from exc
    finally:
        if raw_dir.exists():
            shutil.rmtree(raw_dir, ignore_errors=True)

    assets: list[SurmonSlideAsset] = []
    for record in records:
        relative_path = Path(item) / FRAMES_SUBDIR / record.image.name
        assets.append(
            SurmonSlideAsset(
                id=relative_path.stem,
                image=relative_path.as_posix(),
                image_url=_build_image_url(relative_path),
                timestamp_seconds=record.timestamp,
                average_rgb=list(record.rgb),
            )
        )

    detection_metadata = build_slide_summary(config, records)
    detection_metadata.update(
        {
            "video": str(video_path),
            "generated_at": _utc_now_iso(),
            "frame": frame_metadata,
        }
    )
    _write_slide_metadata(item, detection_metadata, assets)

    metadata_path = Path(item) / "slide_meta.json"
    return SlideGenerationResponse(
        count=len(assets),
        metadata_path=metadata_path.as_posix(),
        slides=assets,
    )
