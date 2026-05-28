from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, List, Optional

from backend.api.lecture_manager import list_series
from backend.api.sermon_converter_service import get_sermon_final_path, get_sermon_project_metadata

from .models import DiscoveredManuscript


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_manuscripts(
    series_ids: Optional[Iterable[str]] = None,
    project_types: Optional[Iterable[str]] = None,
) -> List[DiscoveredManuscript]:
    selected_series = set(series_ids or [])
    selected_types = set(project_types or ["sermon_note"])
    manuscripts: List[DiscoveredManuscript] = []

    for series in list_series():
        if selected_series and series.id not in selected_series:
            continue
        if selected_types and series.project_type not in selected_types:
            continue
        for lecture in series.lectures:
            for project_id in lecture.project_ids:
                project = get_sermon_project_metadata(project_id)
                path = get_sermon_final_path(project_id)
                if not project or not path.exists():
                    continue
                stat = path.stat()
                manuscripts.append(
                    DiscoveredManuscript(
                        series_id=series.id,
                        series_title=series.title,
                        series_description=series.description,
                        lecture_id=lecture.id,
                        lecture_title=lecture.title,
                        lecture_description=lecture.description,
                        project_id=project_id,
                        project_title=project.title,
                        project_type=project.project_type,
                        bible_verse=project.bible_verse,
                        google_doc_id=project.google_doc_id,
                        manuscript_path=path,
                        content_hash=_hash_file(path),
                        modified_time=stat.st_mtime,
                    )
                )
    return manuscripts

