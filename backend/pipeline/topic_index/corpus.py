from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from backend.api.sermon_search.discovery import discover_manuscripts
from backend.api.sermon_search.models import DiscoveredManuscript


def load_corpus(
    series_ids: Optional[Iterable[str]] = None,
    project_types: Optional[Iterable[str]] = None,
    project_ids: Optional[Iterable[str]] = None,
) -> List[Tuple[DiscoveredManuscript, str]]:
    """Return (manuscript_metadata, markdown_text) for every readable manuscript."""
    manuscripts = discover_manuscripts(
        series_ids=series_ids,
        project_types=list(project_types) if project_types else ["sermon_note"],
    )
    if project_ids:
        selected = set(project_ids)
        manuscripts = [m for m in manuscripts if m.project_id in selected]
    results: List[Tuple[DiscoveredManuscript, str]] = []
    for manuscript in manuscripts:
        try:
            text = manuscript.manuscript_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"Warning: could not read {manuscript.manuscript_path}: {exc}")
            continue
        if text.strip():
            results.append((manuscript, text))
    return results
