from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SearchMode(str, Enum):
    normal = "normal"
    deep = "deep"


class RefRole(str, Enum):
    primary = "primary"
    cross = "cross"
    mention = "mention"


class CanonicalRef(BaseModel):
    raw: str
    book: str
    book_zh: Optional[str] = None
    chapter_start: int
    verse_start: Optional[int] = None
    chapter_end: Optional[int] = None
    verse_end: Optional[int] = None
    osis: str


class DiscoveredManuscript(BaseModel):
    series_id: str
    series_title: str
    series_description: Optional[str] = None
    lecture_id: str
    lecture_title: str
    lecture_description: Optional[str] = None
    project_id: str
    project_title: str
    project_type: str = "sermon_note"
    bible_verse: Optional[str] = None
    google_doc_id: Optional[str] = None
    manuscript_path: Path
    content_hash: str
    modified_time: float


class SourceUnit(BaseModel):
    source_id: str
    document_id: str
    series_id: str
    series_title: str
    lecture_id: str
    lecture_title: str
    project_id: str
    project_title: str
    heading_path: List[str] = Field(default_factory=list)
    text: str
    primary_passage_refs: List[CanonicalRef] = Field(default_factory=list)
    cross_refs: List[CanonicalRef] = Field(default_factory=list)
    all_canonical_refs: List[CanonicalRef] = Field(default_factory=list)
    document_scope_refs: List[CanonicalRef] = Field(default_factory=list)
    topic_tags: List[str] = Field(default_factory=list)
    content_types: List[str] = Field(default_factory=list)
    terms: List[str] = Field(default_factory=list)
    ordinal: int = 0


class SearchFilters(BaseModel):
    series_ids: List[str] = Field(default_factory=list)
    project_types: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    canonical_refs: List[str] = Field(default_factory=list)
    content_types: List[str] = Field(default_factory=list)


class SermonSearchRequest(BaseModel):
    question: str
    mode: SearchMode = SearchMode.normal
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: Optional[int] = None


class SourceCard(BaseModel):
    source_id: str
    content_id: str
    score: float
    doc_title: str
    series_title: str
    lecture_title: str
    heading_path: List[str]
    snippet: str
    topics: List[str] = Field(default_factory=list)
    canonical_refs: List[str] = Field(default_factory=list)


class Citation(BaseModel):
    source_id: str
    doc_title: str
    heading_path: List[str]
    quote: str
    supports: str


class SearchRoundTrace(BaseModel):
    round: int
    tools_used: List[str]
    query: str
    candidate_count: int
    selected_count: int


class SearchTrace(BaseModel):
    mode: SearchMode
    rounds: int
    tools_used: List[str]
    notes: List[str] = Field(default_factory=list)
    round_traces: List[SearchRoundTrace] = Field(default_factory=list)


class SermonSearchResponse(BaseModel):
    answer: str
    citations: List[Citation] = Field(default_factory=list)
    sources: List[SourceCard] = Field(default_factory=list)
    related_questions: List[str] = Field(default_factory=list)
    search_trace: SearchTrace


class IndexStatus(BaseModel):
    db_path: str
    document_count: int
    source_unit_count: int
    indexed_at: Optional[str] = None
    embedding_enabled: bool = False


class ReindexRequest(BaseModel):
    series_ids: List[str] = Field(default_factory=list)
    project_types: List[str] = Field(default_factory=lambda: ["sermon_note"])
    include_embeddings: bool = False


class ReindexResponse(BaseModel):
    status: str
    documents_indexed: int
    source_units_indexed: int
    skipped: List[Dict[str, Any]] = Field(default_factory=list)
