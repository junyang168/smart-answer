from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .models import CanonicalUnit
from .store import RepositoryStore


class RepositoryValidationError(ValueError):
    def __init__(self, findings: List[str]):
        super().__init__("Repository build validation failed")
        self.findings = findings


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RepositoryCompiler:
    def __init__(self, store: RepositoryStore):
        self.store = store

    def validate(self) -> List[str]:
        findings: List[str] = []
        sources = {item.source_id: item for item in self.store.list_sources()}
        citations = {item.citation_id: item for item in self.store.list_citations()}
        units = list(self.store.list_units())
        unit_ids = {item.unit_id for item in units}
        for unit in units:
            if unit.unit_type == "passage" and not unit.primary_bible_refs:
                findings.append(f"{unit.unit_id}: passage unit has no primary Bible reference")
            if unit.status == "published" and not unit.citation_ids and not unit.review.source_exception_reason:
                findings.append(f"{unit.unit_id}: published unit has no approved citation")
            for citation_id in unit.citation_ids:
                citation = citations.get(citation_id)
                if citation is None:
                    findings.append(f"{unit.unit_id}: missing citation {citation_id}")
                elif unit.status == "published" and citation.status != "approved":
                    findings.append(f"{unit.unit_id}: citation {citation_id} is not approved")
        for citation in citations.values():
            source = sources.get(citation.source_id)
            if source is None:
                findings.append(f"{citation.citation_id}: missing source {citation.source_id}")
            elif citation.status == "approved" and citation.source_sha256 != source.source_sha256:
                findings.append(f"{citation.citation_id}: source hash is stale")
        for relationship in self.store.list_relationships():
            if relationship.from_unit_id not in unit_ids or relationship.to_unit_id not in unit_ids:
                findings.append(f"{relationship.relationship_id}: relationship points to a missing unit")
        return findings

    def build(self) -> Dict[str, Any]:
        findings = self.validate()
        if findings:
            raise RepositoryValidationError(findings)
        self.store.ensure_dirs()
        build_id = f"BUILD-{_utc_compact()}"
        build_dir = self.store.builds_dir / build_id
        build_dir.mkdir(parents=True, exist_ok=False)
        units = [item for item in self.store.list_units() if item.status == "published"]
        sources = list(self.store.list_sources())
        citations = list(self.store.list_citations())
        relationships = [item for item in self.store.list_relationships() if item.status == "approved"]

        bible: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        topics: Dict[str, Dict[str, Any]] = {}
        for unit in units:
            summary = self._unit_summary(unit)
            if unit.unit_type == "passage":
                for reference in unit.primary_bible_refs:
                    bible[reference.osis].append(summary)
            elif unit.unit_type == "concept":
                # A concept may carry secondary topic assignments so readers can
                # understand related themes.  Those assignments are metadata, not
                # additional catalogue locations: otherwise the same unit appears
                # repeatedly under every related topic.
                for assignment in (
                    item for item in unit.topic_assignments if item.role == "primary"
                ):
                    key = assignment.topic_ids[-1] if assignment.topic_ids else "/".join(assignment.path)
                    card = topics.setdefault(key, {"topic_id": key, "path": assignment.path, "units": []})
                    card["units"].append(summary)

        _write_json(build_dir / "bible_index.json", {"references": bible})
        _write_json(build_dir / "topic_index.json", {"topics": sorted(topics.values(), key=lambda item: item["path"])})
        self._write_sqlite(build_dir / "repository.sqlite3", units, sources, citations, relationships)
        generated_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schema_version": 1,
            "build_id": build_id,
            "generated_at": generated_at,
            "unit_count": len(units),
            "passage_count": sum(item.unit_type == "passage" for item in units),
            "concept_count": sum(item.unit_type == "concept" for item in units),
            "source_count": len(sources),
            "citation_count": len(citations),
            "relationship_count": len(relationships),
        }
        _write_json(build_dir / "build_manifest.json", manifest)
        self.store.set_active_build(build_id, generated_at)
        return manifest

    @staticmethod
    def _unit_summary(unit: CanonicalUnit) -> Dict[str, Any]:
        return {
            "unit_id": unit.unit_id,
            "title": unit.title,
            "unit_type": unit.unit_type,
            "primary_bible_refs": [item.model_dump(mode="json") for item in unit.primary_bible_refs],
            "topic_assignments": [item.model_dump(mode="json") for item in unit.topic_assignments],
        }

    @staticmethod
    def _write_sqlite(path: Path, units, sources, citations, relationships) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE canonical_units (unit_id TEXT PRIMARY KEY, title TEXT NOT NULL, unit_type TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE source_documents (source_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, origin_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE citations (citation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE unit_citations (unit_id TEXT NOT NULL, citation_id TEXT NOT NULL, display_order INTEGER NOT NULL, PRIMARY KEY(unit_id, citation_id));
                CREATE TABLE unit_relationships (relationship_id TEXT PRIMARY KEY, from_unit_id TEXT NOT NULL, to_unit_id TEXT NOT NULL, relationship_type TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX citations_source_status ON citations(source_id, status);
                CREATE INDEX relationships_from ON unit_relationships(from_unit_id);
                CREATE INDEX relationships_to ON unit_relationships(to_unit_id);
                """
            )
            for unit in units:
                payload = unit.model_dump_json()
                connection.execute("INSERT INTO canonical_units VALUES (?, ?, ?, ?)", (unit.unit_id, unit.title, unit.unit_type, payload))
                for order, citation_id in enumerate(unit.citation_ids, start=1):
                    connection.execute("INSERT INTO unit_citations VALUES (?, ?, ?)", (unit.unit_id, citation_id, order))
            for source in sources:
                connection.execute("INSERT INTO source_documents VALUES (?, ?, ?, ?)", (source.source_id, source.source_type, source.origin_id, source.model_dump_json()))
            for citation in citations:
                connection.execute("INSERT INTO citations VALUES (?, ?, ?, ?)", (citation.citation_id, citation.source_id, citation.status, citation.model_dump_json()))
            for relationship in relationships:
                connection.execute(
                    "INSERT INTO unit_relationships VALUES (?, ?, ?, ?, ?)",
                    (relationship.relationship_id, relationship.from_unit_id, relationship.to_unit_id, relationship.relationship_type, relationship.model_dump_json()),
                )
            connection.commit()
        finally:
            connection.close()
