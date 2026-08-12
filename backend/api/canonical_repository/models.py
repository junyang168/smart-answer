from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BibleReference(BaseModel):
    osis: str
    display: str


class TopicAssignment(BaseModel):
    topic_ids: List[str] = Field(default_factory=list)
    path: List[str] = Field(default_factory=list)
    role: Literal["primary", "secondary"] = "primary"


class ManuscriptLocator(BaseModel):
    project_id: str
    project_type: Literal["sermon_note", "transcript"]
    heading_title: str
    heading_anchor: str
    final_sha256: Optional[str] = None


class ReviewRecord(BaseModel):
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    source_exception_reason: Optional[str] = None


class CanonicalUnit(BaseModel):
    schema_version: int = 1
    unit_id: str
    title: str
    unit_type: Literal["passage", "concept"]
    status: Literal["candidate", "reviewed", "published", "archived"] = "candidate"
    primary_bible_refs: List[BibleReference] = Field(default_factory=list)
    topic_assignments: List[TopicAssignment] = Field(default_factory=list)
    manuscript: ManuscriptLocator
    citation_ids: List[str] = Field(default_factory=list)
    relationship_ids: List[str] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)
    knowledge_managed: bool = False
    knowledge_dependency_ids: List[str] = Field(default_factory=list)
    review: ReviewRecord = Field(default_factory=ReviewRecord)


class MediaRecord(BaseModel):
    kind: Literal["audio", "video", "unknown"] = "unknown"
    url: Optional[str] = None


class SourceDocument(BaseModel):
    schema_version: int = 1
    source_id: str
    source_type: Literal["sermon_transcript", "scanned_notes"]
    origin_id: str
    project_id: str
    title: str
    source_stage: str
    public_url: str
    media: MediaRecord = Field(default_factory=MediaRecord)
    source_sha256: str
    unified_source_sha256: str
    updated_at: str = Field(default_factory=utcnow_iso)
    access: Literal["public", "authenticated_reader", "restricted"] = "authenticated_reader"


class TranscriptMapEntry(BaseModel):
    kind: Literal["transcript"] = "transcript"
    source_line_start: int
    source_line_end: int
    paragraph_key: str
    paragraph_position: int
    paragraph_text_sha256: str
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    start_index: Optional[int] = None
    end_index: Optional[int] = None


class NotesMapEntry(BaseModel):
    kind: Literal["notes"] = "notes"
    source_line_start: int
    source_line_end: int
    page_file: str
    page_position: int
    ocr_line_start: int = 1
    ocr_line_end: int = 1
    page_ocr_sha256: Optional[str] = None


class SourceMap(BaseModel):
    schema_version: int = 1
    source_id: str
    source_type: Literal["sermon_transcript", "scanned_notes"]
    source_sha256: str
    unified_source_sha256: str
    created_at: str = Field(default_factory=utcnow_iso)
    entries: List[Dict[str, Any]] = Field(default_factory=list)
    missing: List[Dict[str, Any]] = Field(default_factory=list)
    ambiguous: List[Dict[str, Any]] = Field(default_factory=list)


class CitationLocator(BaseModel):
    kind: Literal["transcript", "notes"]
    paragraph_keys: List[str] = Field(default_factory=list)
    page_file: Optional[str] = None
    page_position: Optional[int] = None
    highlight_text: str
    highlight_text_sha256: str
    occurrence: int = 1
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    ocr_char_start: Optional[int] = None
    ocr_char_end: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None


class Citation(BaseModel):
    schema_version: int = 1
    citation_id: str
    source_id: str
    source_sha256: str
    locator: CitationLocator
    evidence_ids: List[str] = Field(default_factory=list)
    role: str = "primary_evidence"
    supports_claim: str = ""
    status: Literal["candidate", "approved", "rejected", "stale", "unresolved", "restricted"] = "candidate"
    revision: int = 1
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None


class UnitRelationship(BaseModel):
    relationship_id: str
    from_unit_id: str
    to_unit_id: str
    relationship_type: str
    status: Literal["candidate", "approved", "rejected"] = "candidate"
    reason: str = ""


class CitationResolution(BaseModel):
    citation_id: str
    state: Literal["valid", "stale", "restricted", "unresolved"]
    source: SourceDocument
    locator: CitationLocator
    highlight_text: str
    context_before: str = ""
    context_after: str = ""
    deep_link_url: str
    message: Optional[str] = None


class RepositoryStatus(BaseModel):
    available: bool
    active_build_id: Optional[str] = None
    generated_at: Optional[str] = None
    unit_count: int = 0
    passage_count: int = 0
    concept_count: int = 0
    source_count: int = 0
    citation_count: int = 0
