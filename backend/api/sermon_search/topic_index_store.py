from __future__ import annotations

import json
import os
import re
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from backend.api.config import DATA_BASE_PATH

from .models import TopicCard, TopicListResponse, TopicSourceCard, TopicStatus
from .slugify import slugify_heading

_OSIS_CHAPTER_RE = re.compile(r"\.(\d+)")
# First number in a 章-bearing project title/id. For ranges like "14，15章"
# the starting chapter (14) is the right group, so we take the first number
# rather than the one adjacent to 章.
_FIRST_NUMBER_RE = re.compile(r"(\d+)")


def _default_index_path() -> Path:
    configured = os.getenv("SERMON_SEARCH_TOPIC_INDEX_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DATA_BASE_PATH / "sermon_search" / "topic_index.json"


def _chapter_from_osis(osis: Optional[str]) -> Optional[int]:
    if not osis:
        return None
    match = _OSIS_CHAPTER_RE.search(osis)
    return int(match.group(1)) if match else None


def _chapter_from_project(*texts: str) -> Optional[int]:
    """Fallback chapter from a project title/id like '14，15章 …' or '16章釋經'.
    Only trust titles that actually mention 章, then take the first number
    (the starting chapter of any range)."""
    for text in texts:
        if text and "章" in text:
            match = _FIRST_NUMBER_RE.search(text)
            if match:
                return int(match.group(1))
    return None


def _section_anchor(section: str) -> str:
    # Embedded sections are stored as "parent ＞ child"; deep-links land on the
    # parent ## heading, which is what the manuscript renderer anchors.
    parent = section.split("＞")[0].strip() if "＞" in section else section
    return slugify_heading(parent)


class TopicIndexStore:
    """Reads the authoritative topic_index.json. Pure JSON, no SQLite."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _default_index_path()
        self._lock = Lock()
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_mtime: Optional[float] = None

    def _load(self) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None
        mtime = self.path.stat().st_mtime
        with self._lock:
            if self._cache is not None and self._cache_mtime == mtime:
                return self._cache
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return None
            self._cache = data
            self._cache_mtime = mtime
            return data

    def _build_card(self, raw: Dict[str, Any], series_id: Optional[str]) -> Optional[TopicCard]:
        sources_raw = raw.get("sources") or []
        if series_id:
            sources_raw = [s for s in sources_raw if s.get("series_id") == series_id]
            if not sources_raw:
                return None

        sources = [
            TopicSourceCard(
                project_id=s.get("project_id", ""),
                project_title=s.get("project_title", ""),
                lecture_title=s.get("lecture_title", ""),
                source_sections=s.get("source_sections", []) or [],
                section_anchors=[_section_anchor(sec) for sec in (s.get("source_sections") or [])],
                lun_dian=s.get("lun_dian", []) or [],
            )
            for s in sources_raw
        ]
        canonical_ref = raw.get("canonical_ref")
        chapter = _chapter_from_osis(canonical_ref)
        # Passage topics the LLM named thematically (no 太 X:Y prefix) have no
        # OSIS; fall back to the source document's chapter so they still group
        # under the right chapter instead of an "其他" bucket.
        if chapter is None and raw.get("type") == "passage" and sources:
            chapter = _chapter_from_project(sources[0].project_title, sources[0].project_id)
        return TopicCard(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            type=raw.get("type", "concept"),
            size=raw.get("size", "medium"),
            canonical_ref=canonical_ref,
            canonical_ref_raw=raw.get("canonical_ref_raw"),
            chapter=chapter,
            notes=raw.get("notes"),
            sources=sources,
            aliases=raw.get("taxonomy_aliases", []) or [],
        )

    @staticmethod
    def _matches_query(card: TopicCard, q: str) -> bool:
        needle = q.strip().lower()
        if not needle:
            return True
        haystack = [card.name, *card.aliases]
        for src in card.sources:
            haystack.extend(src.lun_dian)
        return any(needle in (h or "").lower() for h in haystack)

    def list_topics(
        self,
        series_id: Optional[str] = None,
        topic_type: Optional[str] = None,
        q: Optional[str] = None,
    ) -> TopicListResponse:
        data = self._load()
        if not data:
            return TopicListResponse(available=False, generated_at=None, count=0, topics=[])

        cards: List[TopicCard] = []
        for raw in data.get("topics", []):
            if topic_type and raw.get("type") != topic_type:
                continue
            card = self._build_card(raw, series_id)
            if card is None:
                continue
            if q and not self._matches_query(card, q):
                continue
            cards.append(card)

        return TopicListResponse(
            available=True,
            generated_at=data.get("generated_at"),
            count=len(cards),
            topics=cards,
        )

    def get_topic(self, topic_id: str, series_id: Optional[str] = None) -> Optional[TopicCard]:
        data = self._load()
        if not data:
            return None
        for raw in data.get("topics", []):
            if raw.get("id") == topic_id:
                return self._build_card(raw, series_id)
        return None

    def status(self) -> TopicStatus:
        data = self._load()
        if not data:
            return TopicStatus(available=False)
        topics = data.get("topics", [])
        passage = sum(1 for t in topics if t.get("type") == "passage")
        concept = sum(1 for t in topics if t.get("type") == "concept")
        return TopicStatus(
            available=True,
            generated_at=data.get("generated_at"),
            passage_count=passage,
            concept_count=concept,
        )


topic_index_store = TopicIndexStore()
