from __future__ import annotations

from typing import Optional

import cv2
import numpy as np


_FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
_FACE_ANCHOR_CACHE: dict[str, Optional[tuple[float, float]]] = {}

_MIN_FACE_SIZE_PX = 48
_MIN_FACE_AREA_RATIO = 0.01


def detect_primary_face_anchor(
    frame,
    cache_key: str | None = None,
) -> Optional[tuple[float, float]]:
    if cache_key and cache_key in _FACE_ANCHOR_CACHE:
        return _FACE_ANCHOR_CACHE[cache_key]

    result = _detect_primary_face_anchor(frame)
    if cache_key:
        _FACE_ANCHOR_CACHE[cache_key] = result
    return result


def _detect_primary_face_anchor(frame) -> Optional[tuple[float, float]]:
    if frame is None or not getattr(frame, "size", 0):
        return None
    if _FACE_CASCADE.empty():
        return None

    image = np.asarray(frame)
    if image.ndim != 3 or image.shape[2] < 3:
        return None

    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)

    frame_h, frame_w = image.shape[:2]
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
    min_size = max(_MIN_FACE_SIZE_PX, min(frame_w, frame_h) // 12)
    detections = _FACE_CASCADE.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(min_size, min_size),
    )
    if len(detections) == 0:
        return None

    center_x = frame_w / 2.0
    center_y = frame_h / 2.0
    candidates: list[tuple[int, float, tuple[int, int, int, int]]] = []
    for x, y, w, h in detections:
        if w <= 0 or h <= 0:
            continue
        area = w * h
        if w < _MIN_FACE_SIZE_PX or h < _MIN_FACE_SIZE_PX:
            continue
        if (area / float(frame_w * frame_h)) < _MIN_FACE_AREA_RATIO:
            continue
        face_center_x = x + (w / 2.0)
        face_center_y = y + (h / 2.0)
        center_distance = ((face_center_x - center_x) ** 2 + (face_center_y - center_y) ** 2) ** 0.5
        candidates.append((area, center_distance, (x, y, w, h)))

    if not candidates:
        return None

    _, _, (x, y, w, h) = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
    anchor_x = max(0.0, min(1.0, (x + (w / 2.0)) / frame_w))
    anchor_y = max(0.0, min(1.0, (y + (h / 2.0)) / frame_h))
    return (anchor_x, anchor_y)
