"""One lecture's argument for a church co-worker: read, listen, and rule on its core point.

Reads the argument-layer knowledge instance (`backend.pipeline.lecture_reader`).
The only write is an owner ruling on a core-point candidate, appended to a file
under DATA_BASE_DIR; nothing in the instance changes.
"""
from __future__ import annotations

from typing import Any, Optional

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api.config import DATA_BASE_PATH
from backend.pipeline import lecture_reader
from backend.pipeline import series_unit_reader
from backend.pipeline.argument_review import database_url

router = APIRouter(prefix="/admin/lecture-argument", tags=["lecture-argument-admin"])


class Ruling(BaseModel):
    verdict: str
    claim_id: Optional[str] = None
    note: str = ""


class UnitRuling(BaseModel):
    verdict: str
    note: str = ""


def _connect():
    try:
        return psycopg.connect(database_url())
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/lectures")
def list_lectures() -> dict[str, Any]:
    with _connect() as conn:
        return {"lectures": lecture_reader.lectures(conn)}


@router.get("/lectures/{change_set_id}")
def get_lecture(change_set_id: str) -> dict[str, Any]:
    with _connect() as conn:
        try:
            return lecture_reader.lecture(conn, change_set_id, DATA_BASE_PATH)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/lectures/{change_set_id}/core-rulings")
def post_ruling(change_set_id: str, ruling: Ruling) -> dict[str, Any]:
    try:
        return lecture_reader.record_ruling(DATA_BASE_PATH, change_set_id, ruling.verdict, ruling.claim_id, ruling.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/series")
def list_series() -> dict[str, Any]:
    return {"series": series_unit_reader.list_series(DATA_BASE_PATH)}


@router.get("/series/{series_id}")
def get_series(series_id: str) -> dict[str, Any]:
    try:
        return series_unit_reader.get_series(DATA_BASE_PATH, series_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/series/{series_id}/units/{unit_id}")
def get_series_unit(series_id: str, unit_id: str) -> dict[str, Any]:
    try:
        return series_unit_reader.get_unit(DATA_BASE_PATH, series_id, unit_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/series/{series_id}/units/{unit_id}/rulings")
def post_series_unit_ruling(series_id: str, unit_id: str, ruling: UnitRuling) -> dict[str, Any]:
    try:
        return series_unit_reader.record_unit_ruling(DATA_BASE_PATH, series_id, unit_id, ruling.verdict, ruling.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
