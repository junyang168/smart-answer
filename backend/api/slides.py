from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status

from .config import DATA_BASE_PATH
from .models import SurmonSlideAsset

SLIDES_DIR_NAME = "slides"
SLIDE_WEB_PREFIX = "/web/data/slides"

router = APIRouter(prefix="/slides", tags=["slides"])


def _sanitize_item(item_id: str) -> str:
    item = item_id.strip()
    if not item:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Item id is required")
    return item


def _slide_root(item: str) -> Path:
    return DATA_BASE_PATH / SLIDES_DIR_NAME / item


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

    return SurmonSlideAsset(
        id=relative_path.stem,
        image=relative_path.as_posix(),
        image_url=_build_image_url(relative_path),
        timestamp_seconds=timestamp,
        average_rgb=average_rgb,
    )


def _load_slide_metadata(item: str) -> list[SurmonSlideAsset]:
    slides_root = _slide_root(item)
    if not slides_root.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Slides not found for item {item}")

    metadata_path = slides_root / "slide_meta.json"
    if not metadata_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"slide_meta.json missing for item {item}")

    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid slide metadata JSON") from exc

    raw_slides = metadata.get("slides", [])
    if not isinstance(raw_slides, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid slides format in metadata")

    slides: list[SurmonSlideAsset] = []
    for index, entry in enumerate(raw_slides):
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


@router.get("/{item_id}", response_model=list[SurmonSlideAsset])
def list_surmon_slides(item_id: str) -> list[SurmonSlideAsset]:
    item = _sanitize_item(item_id)
    return _load_slide_metadata(item)
