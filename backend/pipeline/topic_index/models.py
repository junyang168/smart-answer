from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TopicSource:
    project_id: str
    project_title: str
    series_id: str
    lecture_title: str
    source_sections: List[str]
    lun_dian: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_title": self.project_title,
            "series_id": self.series_id,
            "lecture_title": self.lecture_title,
            "source_sections": self.source_sections,
            "lun_dian": self.lun_dian,
        }


@dataclass
class TopicEntry:
    id: str
    name: str
    type: str   # "concept" | "passage"
    size: str   # "large" | "medium" | "embedded"
    sources: List[TopicSource]
    notes: Optional[str] = None
    taxonomy_aliases: List[str] = field(default_factory=list)
    canonical_ref: Optional[str] = None        # OSIS, e.g. "Matt.1.22-Matt.1.23"
    canonical_ref_raw: Optional[str] = None     # display form, e.g. "太 1:22–23"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "size": self.size,
            "canonical_ref": self.canonical_ref,
            "canonical_ref_raw": self.canonical_ref_raw,
            "sources": [s.to_dict() for s in self.sources],
            "notes": self.notes,
            "taxonomy_aliases": self.taxonomy_aliases,
        }


@dataclass
class TopicIndex:
    generated_at: str
    corpus_size: int
    topics: List[TopicEntry]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "corpus_size": self.corpus_size,
            "topics": [t.to_dict() for t in self.topics],
        }
