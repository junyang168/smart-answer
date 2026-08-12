from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Type, TypeVar

from pydantic import BaseModel

from .knowledge_models import (
    KNOWLEDGE_COLLECTIONS,
    EvolvingKnowledgeRecord,
    KnowledgePackageManifest,
)
from .models import CanonicalUnit, Citation, SourceDocument, SourceMap, UnitRelationship


T = TypeVar("T", bound=BaseModel)


class RepositoryStore:
    """Reviewable JSON authoring store with atomic record writes."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.units_dir = self.root / "units"
        self.sources_dir = self.root / "sources"
        self.source_maps_dir = self.root / "source_maps"
        self.citations_dir = self.root / "citations"
        self.relationships_dir = self.root / "relationships"
        self.knowledge_dir = self.root / "knowledge"
        self.knowledge_packages_dir = self.knowledge_dir / "packages"
        self.builds_dir = self.root / "builds"

    def ensure_dirs(self) -> None:
        for path in (
            self.units_dir,
            self.sources_dir,
            self.source_maps_dir,
            self.citations_dir,
            self.relationships_dir,
            self.knowledge_dir,
            self.knowledge_packages_dir,
            self.builds_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_id(value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("Invalid repository record ID")
        return value

    def _write_json(self, path: Path, value: Any) -> None:
        self.ensure_dirs()
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _read_model(path: Path, model: Type[T]) -> T:
        if not path.is_file():
            raise FileNotFoundError(path)
        return model.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _list_models(folder: Path, model: Type[T]) -> Iterable[T]:
        if not folder.is_dir():
            return []
        return [model.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]

    def save_unit(self, unit: CanonicalUnit) -> None:
        self._write_json(self.units_dir / f"{self._validate_id(unit.unit_id)}.json", unit)

    def get_unit(self, unit_id: str) -> CanonicalUnit:
        return self._read_model(self.units_dir / f"{self._validate_id(unit_id)}.json", CanonicalUnit)

    def list_units(self) -> Iterable[CanonicalUnit]:
        return self._list_models(self.units_dir, CanonicalUnit)

    def save_source(self, source: SourceDocument) -> None:
        self._write_json(self.sources_dir / f"{self._validate_id(source.source_id)}.json", source)

    def get_source(self, source_id: str) -> SourceDocument:
        return self._read_model(self.sources_dir / f"{self._validate_id(source_id)}.json", SourceDocument)

    def list_sources(self) -> Iterable[SourceDocument]:
        return self._list_models(self.sources_dir, SourceDocument)

    def save_source_map(self, source_map: SourceMap) -> None:
        self._write_json(self.source_maps_dir / f"{self._validate_id(source_map.source_id)}.json", source_map)

    def get_source_map(self, source_id: str) -> SourceMap:
        return self._read_model(self.source_maps_dir / f"{self._validate_id(source_id)}.json", SourceMap)

    def save_citation(self, citation: Citation) -> None:
        self._write_json(self.citations_dir / f"{self._validate_id(citation.citation_id)}.json", citation)

    def get_citation(self, citation_id: str) -> Citation:
        return self._read_model(self.citations_dir / f"{self._validate_id(citation_id)}.json", Citation)

    def list_citations(self) -> Iterable[Citation]:
        return self._list_models(self.citations_dir, Citation)

    def save_relationships(self, relationships: list[UnitRelationship]) -> None:
        self._write_json(self.relationships_dir / "relationships.json", [item.model_dump(mode="json") for item in relationships])

    def list_relationships(self) -> list[UnitRelationship]:
        path = self.relationships_dir / "relationships.json"
        if not path.is_file():
            return []
        return [UnitRelationship.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]

    def set_active_build(self, build_id: str, generated_at: str) -> None:
        self._write_json(self.root / "active.json", {"build_id": self._validate_id(build_id), "generated_at": generated_at})

    def get_active_build(self) -> Optional[Dict[str, Any]]:
        path = self.root / "active.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    @staticmethod
    def _knowledge_definition(collection: str) -> tuple[type[EvolvingKnowledgeRecord], str]:
        try:
            return KNOWLEDGE_COLLECTIONS[collection]
        except KeyError as exc:
            raise ValueError(f"Unknown knowledge collection: {collection}") from exc

    def knowledge_record_path(self, collection: str, record_id: str) -> Path:
        self._knowledge_definition(collection)
        return self.knowledge_dir / collection / f"{self._validate_id(record_id)}.json"

    def save_knowledge_record(self, collection: str, record: EvolvingKnowledgeRecord) -> None:
        model, id_field = self._knowledge_definition(collection)
        if not isinstance(record, model):
            raise TypeError(f"{collection} requires {model.__name__}")
        record_id = getattr(record, id_field)
        self._write_json(self.knowledge_record_path(collection, record_id), record)

    def get_knowledge_record(self, collection: str, record_id: str) -> EvolvingKnowledgeRecord:
        model, _ = self._knowledge_definition(collection)
        return self._read_model(self.knowledge_record_path(collection, record_id), model)

    def list_knowledge_records(self, collection: str) -> Iterable[EvolvingKnowledgeRecord]:
        model, _ = self._knowledge_definition(collection)
        return self._list_models(self.knowledge_dir / collection, model)

    def update_knowledge_record(
        self,
        collection: str,
        record_id: str,
        changes: Dict[str, Any],
        expected_revision: Optional[int] = None,
    ) -> EvolvingKnowledgeRecord:
        model, id_field = self._knowledge_definition(collection)
        existing = self.get_knowledge_record(collection, record_id)
        if expected_revision is not None and existing.revision != expected_revision:
            raise ValueError(
                f"Revision conflict: expected {expected_revision}, current {existing.revision}"
            )
        if id_field in changes and str(changes[id_field]) != record_id:
            raise ValueError(f"{id_field} cannot be changed")
        payload = existing.model_dump(mode="json")
        payload.update(changes)
        payload[id_field] = record_id
        payload["revision"] = existing.revision + 1
        updated = model.model_validate(payload)
        self.save_knowledge_record(collection, updated)
        return updated

    def knowledge_counts(self) -> Dict[str, int]:
        return {
            collection: len(list(self.list_knowledge_records(collection)))
            for collection in KNOWLEDGE_COLLECTIONS
        }

    def save_knowledge_package(self, manifest: KnowledgePackageManifest) -> None:
        self._write_json(
            self.knowledge_packages_dir / f"{self._validate_id(manifest.package_id)}.json",
            manifest,
        )

    def get_knowledge_package(self, package_id: str) -> KnowledgePackageManifest:
        return self._read_model(
            self.knowledge_packages_dir / f"{self._validate_id(package_id)}.json",
            KnowledgePackageManifest,
        )

    def list_knowledge_packages(self) -> Iterable[KnowledgePackageManifest]:
        return self._list_models(self.knowledge_packages_dir, KnowledgePackageManifest)
