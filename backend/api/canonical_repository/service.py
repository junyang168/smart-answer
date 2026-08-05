from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

from opencc import OpenCC

from backend.api.config import CONFIG_DIR, DATA_BASE_PATH
from backend.api import sermon_converter_service as sermon_service

from .compiler import RepositoryCompiler
from .models import (
    BibleReference,
    CanonicalUnit,
    Citation,
    CitationLocator,
    CitationResolution,
    MediaRecord,
    ManuscriptLocator,
    RepositoryStatus,
    SourceDocument,
    TopicAssignment,
)
from .source_maps import (
    build_notes_source_map,
    build_transcript_source_map,
    entries_for_line_range,
    load_transcript_paragraphs,
    sha256_bytes,
    sha256_text,
    stable_id,
)
from .store import RepositoryStore


class CanonicalRepositoryService:
    _traditionalizer = OpenCC("s2t")

    def __init__(self, root: Optional[Path] = None):
        self.store = RepositoryStore(root or DATA_BASE_PATH / "canonical_repository")
        self.compiler = RepositoryCompiler(self.store)

    @staticmethod
    def _sermon_catalog_record(transcript_id: str) -> Dict[str, Any]:
        catalog_path = CONFIG_DIR / "sermon.json"
        if catalog_path.is_file():
            try:
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                return next(
                    (item for item in catalog if isinstance(item, dict) and item.get("item") == transcript_id),
                    {},
                )
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    @staticmethod
    def _sermon_media(
        transcript_id: str,
        transcript_metadata: Dict[str, Any],
        catalog_record: Dict[str, Any],
    ) -> MediaRecord:
        """Resolve media type from the authoritative sermon catalog, with file fallback."""
        media_type = catalog_record.get("type") or transcript_metadata.get("type")

        media_root = DATA_BASE_PATH.parent / "video"
        audio_path = media_root / f"{transcript_id}.mp3"
        video_path = media_root / f"{transcript_id}.mp4"
        if media_type not in {"audio", "video"}:
            if audio_path.is_file() and not video_path.is_file():
                media_type = "audio"
            else:
                media_type = "video"

        extension = "mp3" if media_type == "audio" else "mp4"
        return MediaRecord(
            kind=media_type,
            url=f"/web/video/{quote(transcript_id, safe='')}.{extension}",
        )

    def save_unit_and_refresh_public_index(self, unit: CanonicalUnit) -> CanonicalUnit:
        """Save an editorial change and keep the published read model in sync.

        The public site reads a compiled snapshot. Any edit that publishes an
        existing unit, changes an already-published unit, or removes a unit from
        publication must therefore rebuild that snapshot in the same operation.
        If compilation fails, restore the previous authoring record so the admin
        status cannot claim a change is public when the public snapshot rejected it.
        """
        existing = self.store.get_unit(unit.unit_id)
        refresh_public_index = existing.status == "published" or unit.status == "published"
        self.store.save_unit(unit)
        if refresh_public_index:
            try:
                self.compiler.build()
            except Exception:
                self.store.save_unit(existing)
                raise
        return unit

    @staticmethod
    def _project_dir(project_id: str) -> Path:
        sermon_service._normalize_sermon_transcript_id(project_id)
        for root in (sermon_service.TRANSCRIPTS_TO_MANUSCRIPT_DIR, sermon_service.NOTES_TO_SERMON_DIR):
            candidate = root / project_id
            if (candidate / "meta.json").is_file():
                return candidate.resolve()
        raise FileNotFoundError(f"Sermon project {project_id} not found")

    def register_project_source(self, project_id: str) -> Dict[str, Any]:
        project_dir = self._project_dir(project_id)
        metadata = json.loads((project_dir / "meta.json").read_text(encoding="utf-8"))
        unified_path = project_dir / "unified_source.md"
        if not unified_path.is_file():
            raise FileNotFoundError(f"Unified Input for {project_id} not found")
        if metadata.get("project_type") == "transcript":
            transcript_id = str(metadata.get("sermon_transcript_id") or "").strip()
            if not transcript_id:
                raise ValueError("Transcript project has no linked sermon transcript ID")
            resolved = sermon_service.resolve_sermon_transcript(transcript_id)
            transcript_payload = json.loads(resolved["path"].read_text(encoding="utf-8"))
            transcript_metadata = transcript_payload.get("metadata", {}) if isinstance(transcript_payload, dict) else {}
            catalog_record = self._sermon_catalog_record(resolved["transcript_id"])
            source_id = stable_id("SD", "sermon_transcript", resolved["transcript_id"])
            source_map = build_transcript_source_map(source_id, resolved["path"], unified_path)
            source = SourceDocument(
                source_id=source_id,
                source_type="sermon_transcript",
                origin_id=resolved["transcript_id"],
                project_id=project_id,
                title=(
                    catalog_record.get("title")
                    or transcript_metadata.get("title")
                    or resolved["transcript_id"]
                ),
                source_stage=resolved["source_stage"],
                public_url=f"/resources/sermons/{quote(resolved['transcript_id'], safe='')}",
                media=self._sermon_media(resolved["transcript_id"], transcript_metadata, catalog_record),
                source_sha256=source_map.source_sha256,
                unified_source_sha256=source_map.unified_source_sha256,
            )
        else:
            source_id = stable_id("SD", "scanned_notes", project_id)
            source_map = build_notes_source_map(
                source_id,
                unified_path,
                sermon_service.NOTES_TO_SERMON_DIR / "raw_ocr",
                metadata.get("pages") or [],
            )
            source = SourceDocument(
                source_id=source_id,
                source_type="scanned_notes",
                origin_id=project_id,
                project_id=project_id,
                title=metadata.get("title") or project_id,
                source_stage="reviewed",
                public_url=f"/resources/notes-sources/{quote(source_id, safe='')}",
                source_sha256=source_map.source_sha256,
                unified_source_sha256=source_map.unified_source_sha256,
            )
        self.store.save_source(source)
        self.store.save_source_map(source_map)
        refreshed_candidates = self._refresh_candidate_citations_for_source(source, unified_path)
        return {
            "source": source.model_dump(mode="json"),
            "mapped_count": len(source_map.entries),
            "missing": source_map.missing,
            "ambiguous": source_map.ambiguous,
            "refreshed_candidate_citations": refreshed_candidates,
        }

    def _refresh_candidate_citations_for_source(self, source: SourceDocument, unified_path: Path) -> int:
        """Refresh only unreviewed citations that still resolve exactly after a source-map rebuild."""
        if source.source_type != "scanned_notes" or not unified_path.is_file():
            return 0
        unified = unified_path.read_text(encoding="utf-8")
        refreshed = 0
        for citation in self.store.list_citations():
            if (
                citation.source_id != source.source_id
                or citation.status != "candidate"
                or citation.source_sha256 == source.source_sha256
                or citation.locator.highlight_text not in unified
            ):
                continue
            citation.source_sha256 = source.source_sha256
            citation.revision += 1
            self.store.save_citation(citation)
            refreshed += 1
        return refreshed

    def import_seed_catalog(self, catalog_path: Path) -> Dict[str, Any]:
        """Import seed candidates without approving or publishing editorial decisions."""
        payload = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        records = payload.get("units", []) if isinstance(payload, dict) else []
        imported = 0
        skipped = 0
        citations_created = 0
        errors: List[Dict[str, str]] = []
        for record in records:
            try:
                unit_id = str(record["unit_id"])
                try:
                    self.store.get_unit(unit_id)
                    skipped += 1
                    continue
                except FileNotFoundError:
                    pass
                sources = record.get("sources") or []
                if not sources:
                    raise ValueError("seed unit has no manuscript source")
                manuscript_source = sources[0]
                headings = manuscript_source.get("resolved_source_headings") or manuscript_source.get("source_sections") or []
                anchors = manuscript_source.get("section_anchors") or []
                unit = CanonicalUnit(
                    unit_id=unit_id,
                    title=str(record.get("title") or unit_id),
                    unit_type=record.get("unit_type", "concept"),
                    status="candidate",
                    primary_bible_refs=[
                        BibleReference(osis=str(item["osis"]), display=str(item.get("display") or item["osis"]))
                        for item in record.get("primary_bible_refs", [])
                    ],
                    topic_assignments=[
                        TopicAssignment(
                            topic_ids=[str(value) for value in item.get("topic_ids", [])],
                            path=[str(value) for value in item.get("path", [])],
                            role=item.get("role", "primary") if item.get("role") in {"primary", "secondary"} else "secondary",
                        )
                        for item in record.get("topic_assignments", [])
                    ],
                    manuscript=ManuscriptLocator(
                        project_id=str(manuscript_source["project_id"]),
                        project_type="transcript" if manuscript_source.get("project_type") == "transcript" else "sermon_note",
                        heading_title=str(headings[0] if headings else record.get("title") or ""),
                        heading_anchor=str(anchors[0] if anchors else ""),
                        final_sha256=manuscript_source.get("source_sha256"),
                    ),
                    aliases=[str(value) for value in record.get("aliases", [])],
                )
                self.store.save_unit(unit)
                if unit.manuscript.project_type in {"sermon_note", "transcript"}:
                    result = self._backfill_citations_for_record(record, unit)
                    citations_created += result["citations_created"]
                imported += 1
            except Exception as exc:
                errors.append({"unit_id": str(record.get("unit_id") or "unknown"), "error": str(exc)})
        return {
            "imported": imported,
            "skipped": skipped,
            "citations_created": citations_created,
            "errors": errors,
            "total": len(records),
        }

    @staticmethod
    def _normalized_heading(value: str) -> str:
        text = CanonicalRepositoryService._traditionalizer.convert(
            unicodedata.normalize("NFKC", str(value or ""))
        ).strip().lstrip("#").strip()
        text = re.sub(r"^[一二三四五六七八九十百零〇0-9]+\s*[、.．:：)）-]\s*", "", text)
        return re.sub(r"[\s「」『』【】()（）,，、:：.．—–_-]+", "", text).casefold()

    @staticmethod
    def _is_heading_only_excerpt(value: str) -> bool:
        """Return true when a citation contains Markdown headings but no source prose."""
        content_lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
        return bool(content_lines) and all(
            re.fullmatch(r"#{1,6}\s+\S.*", line) is not None for line in content_lines
        )

    @staticmethod
    def _heading_ordinal(value: str) -> Optional[str]:
        match = re.match(r"^\s*([一二三四五六七八九十百零〇]+|\d+)\s*[、.．:：)）-]", str(value or ""))
        return match.group(1) if match else None

    @staticmethod
    def _ordinal_number(value: str) -> Optional[int]:
        if value.isdigit():
            return int(value)
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if value == "十":
            return 10
        if "十" in value:
            left, right = value.split("十", 1)
            return (digits.get(left, 1) * 10) + digits.get(right, 0)
        return digits.get(value)

    def _parent_manuscript_headings(self, project_dir: Path, headings: List[str]) -> List[str]:
        final_path = project_dir / "final.md"
        if not final_path.is_file():
            return []
        targets = {self._normalized_heading(item) for item in headings if str(item).strip()}
        parents: List[str] = []
        current_h2: Optional[str] = None
        for line in final_path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
            if not match:
                continue
            level = len(match.group(1))
            title = match.group(2).strip()
            if level == 2:
                current_h2 = title
            normalized = self._normalized_heading(title)
            if normalized in targets and current_h2 and current_h2 not in parents:
                parents.append(current_h2)
        return parents

    def _unified_heading_ranges(self, project_dir: Path, headings: List[str]) -> List[tuple[int, int]]:
        unified_path = project_dir / "unified_source.md"
        if not unified_path.is_file():
            return []
        lines = unified_path.read_text(encoding="utf-8").splitlines()
        targets = {self._normalized_heading(item) for item in headings if str(item).strip()}
        parsed: List[tuple[int, int, str]] = []
        for index, line in enumerate(lines, start=1):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match:
                parsed.append((index, len(match.group(1)), match.group(2).strip()))
        ranges: List[tuple[int, int]] = []
        for position, (start, level, title) in enumerate(parsed):
            normalized = self._normalized_heading(title)
            if normalized not in targets:
                continue
            end = len(lines)
            for next_start, next_level, _ in parsed[position + 1 :]:
                if next_level <= level:
                    end = next_start - 1
                    break
            ranges.append((start, end))
        return ranges

    def _evidence_source_ranges_for_headings(
        self,
        project_dir: Path,
        headings: List[str],
    ) -> List[tuple[int, int]]:
        """Resolve generated manuscript headings back to original transcript lines.

        ``chunks_meta.json`` describes the generated manuscript and its line numbers
        are therefore not safe to use as transcript coordinates.  Stage 1 preserves
        the real provenance chain in ``draft_chunks_meta.json`` (heading -> evidence
        IDs) and ``evidence_inventory.json`` (evidence ID -> source ranges).
        """
        chunks_path = project_dir / "draft_chunks_meta.json"
        evidence_path = project_dir / "evidence_inventory.json"
        if not chunks_path.is_file() or not evidence_path.is_file():
            return []
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(chunks, list) or not isinstance(evidence_payload, dict):
            return []
        evidence_items = (evidence_payload.get("payload") or {}).get("evidence") or []
        evidence_by_id = {
            str(item.get("evidence_id")): item
            for item in evidence_items
            if isinstance(item, dict) and item.get("evidence_id")
        }
        wanted = {self._normalized_heading(item) for item in headings if str(item).strip()}
        evidence_ids: List[str] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            title = self._normalized_heading(str(chunk.get("title") or ""))
            if title not in wanted:
                continue
            for evidence_id in chunk.get("evidence_ids") or []:
                value = str(evidence_id)
                if value not in evidence_ids:
                    evidence_ids.append(value)
        ranges: List[tuple[int, int]] = []
        for evidence_id in evidence_ids:
            evidence = evidence_by_id.get(evidence_id) or {}
            for source_range in evidence.get("source_ranges") or []:
                if not isinstance(source_range, dict):
                    continue
                start = source_range.get("start_line")
                end = source_range.get("end_line")
                if start is None or end is None:
                    continue
                pair = (int(start), int(end))
                if pair not in ranges:
                    ranges.append(pair)
        return ranges

    def _source_ranges_for_seed_source(self, project_id: str, source_record: Dict[str, Any]) -> List[tuple[int, int]]:
        project_dir = self._project_dir(project_id)
        chunks_path = project_dir / "chunks_meta.json"
        if not chunks_path.is_file():
            return []
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        if not isinstance(chunks, list):
            return []
        headings = (
            source_record.get("resolved_source_headings")
            or source_record.get("source_sections")
            or []
        )
        evidence_ranges = self._evidence_source_ranges_for_headings(
            project_dir,
            [str(item) for item in headings],
        )
        if evidence_ranges:
            return evidence_ranges
        direct_ranges = self._unified_heading_ranges(project_dir, [str(item) for item in headings])
        if direct_ranges:
            return direct_ranges
        wanted = [self._normalized_heading(item) for item in headings if str(item).strip()]
        parent_headings = self._parent_manuscript_headings(project_dir, [str(item) for item in headings])
        parent_wanted = [self._normalized_heading(item) for item in parent_headings]
        wanted_ordinals = {
            item for item in (self._heading_ordinal(value) for value in [*headings, *parent_headings]) if item
        }
        wanted_positions = {
            number for number in (self._ordinal_number(item) for item in wanted_ordinals) if number is not None
        }
        substantive_position = 0
        ranges: List[tuple[int, int]] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            raw_title = str(chunk.get("title", ""))
            if raw_title != "(Preamble / Front Matter)":
                substantive_position += 1
            title = self._normalized_heading(raw_title)
            ordinal_match = self._heading_ordinal(raw_title) in wanted_ordinals if wanted_ordinals else False
            position_match = substantive_position in wanted_positions if wanted_positions else False
            exact_match = title in wanted or title in parent_wanted
            fuzzy_match = any(len(item) >= 8 and (title in item or item in title) for item in [*wanted, *parent_wanted])
            if wanted and not (exact_match or ordinal_match or position_match or fuzzy_match):
                continue
            start = chunk.get("source_start_line", chunk.get("start_line"))
            end = chunk.get("source_end_line", chunk.get("end_line"))
            if start is None or end is None:
                continue
            pair = (int(start), int(end))
            if pair not in ranges:
                ranges.append(pair)
        return ranges

    def _existing_citation_for_excerpt(
        self,
        source_id: str,
        locator_kind: str,
        locator_key: str,
        highlight_text: str,
    ) -> Optional[Citation]:
        highlight_hash = sha256_text(highlight_text)
        for citation in self.store.list_citations():
            if (
                citation.source_id == source_id
                and citation.locator.kind == locator_kind
                and (
                    (locator_kind == "notes" and citation.locator.page_file == locator_key)
                    or (locator_kind == "transcript" and citation.locator.paragraph_keys == [locator_key])
                )
                and citation.locator.highlight_text_sha256 == highlight_hash
            ):
                return citation
        return None

    def _backfill_citations_for_record(
        self,
        record: Dict[str, Any],
        unit: CanonicalUnit,
        registered_sources: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if unit.citation_ids:
            return {"citations_created": 0, "citations_reused": 0, "unresolved_sources": []}
        created = 0
        reused = 0
        unresolved: List[str] = []
        source_cache = registered_sources if registered_sources is not None else {}
        unified_cache: Dict[str, List[str]] = {}

        for source_record in record.get("sources") or []:
            if source_record.get("project_type") not in {"sermon_note", "transcript"}:
                continue
            project_id = str(source_record.get("project_id") or "").strip()
            if not project_id:
                continue
            if project_id not in source_cache:
                source_cache[project_id] = self.register_project_source(project_id)
            source_id = source_cache[project_id]["source"]["source_id"]
            source_map = self.store.get_source_map(source_id)
            ranges = self._source_ranges_for_seed_source(project_id, source_record)
            if not ranges:
                unresolved.append(project_id)
                continue
            if project_id not in unified_cache:
                project_dir = self._project_dir(project_id)
                unified_cache[project_id] = (project_dir / "unified_source.md").read_text(encoding="utf-8").splitlines()
            lines = unified_cache[project_id]

            for range_start, range_end in ranges:
                for entry in entries_for_line_range(source_map, range_start, range_end):
                    start = max(range_start, int(entry["source_line_start"]))
                    end = min(range_end, int(entry["source_line_end"]))
                    excerpt = "\n".join(lines[start - 1 : end]).strip()
                    if not excerpt or self._is_heading_only_excerpt(excerpt):
                        continue
                    locator_kind = "notes" if source_map.source_type == "scanned_notes" else "transcript"
                    locator_key = str(entry["page_file"] if locator_kind == "notes" else entry["paragraph_key"])
                    existing = self._existing_citation_for_excerpt(
                        source_id,
                        locator_kind,
                        locator_key,
                        excerpt,
                    )
                    if existing is None:
                        existing = self.create_citation_from_source_range(
                            source_id=source_id,
                            start_line=start,
                            end_line=end,
                            highlight_text=excerpt,
                            supports_claim=unit.title,
                        )
                        created += 1
                    else:
                        reused += 1
                    if existing.citation_id not in unit.citation_ids:
                        unit.citation_ids.append(existing.citation_id)

        if unit.citation_ids:
            self.store.save_unit(unit)
        return {
            "citations_created": created,
            "citations_reused": reused,
            "unresolved_sources": unresolved,
        }

    def detach_heading_only_citations(self) -> Dict[str, Any]:
        """Detach non-evidentiary Markdown headings while preserving citation records."""
        citations = {item.citation_id: item for item in self.store.list_citations()}
        affected_units: List[Dict[str, Any]] = []
        removed_links = 0
        units_without_substantive_sources: List[str] = []

        for unit in self.store.list_units():
            removed = [
                citation_id
                for citation_id in unit.citation_ids
                if citation_id in citations
                and self._is_heading_only_excerpt(citations[citation_id].locator.highlight_text or "")
            ]
            if not removed:
                continue
            unit.citation_ids = [citation_id for citation_id in unit.citation_ids if citation_id not in removed]
            self.store.save_unit(unit)
            removed_links += len(removed)
            affected_units.append({"unit_id": unit.unit_id, "removed_citation_ids": removed})
            if not unit.citation_ids:
                units_without_substantive_sources.append(unit.unit_id)

        return {
            "affected_units": affected_units,
            "removed_links": removed_links,
            "units_without_substantive_sources": units_without_substantive_sources,
        }

    def backfill_seed_citations(self, catalog_path: Path) -> Dict[str, Any]:
        """Attach reviewable original-note or transcript citations to existing seed units."""
        payload = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        records = payload.get("units", []) if isinstance(payload, dict) else []
        processed = 0
        skipped = 0
        citations_created = 0
        citations_reused = 0
        errors: List[Dict[str, str]] = []
        registered_sources: Dict[str, Dict[str, Any]] = {}
        for record in records:
            unit_id = str(record.get("unit_id") or "")
            try:
                unit = self.store.get_unit(unit_id)
                if unit.citation_ids:
                    skipped += 1
                    continue
                result = self._backfill_citations_for_record(record, unit, registered_sources)
                if result["unresolved_sources"]:
                    raise ValueError(
                        "No source ranges found for " + ", ".join(result["unresolved_sources"])
                    )
                citations_created += result["citations_created"]
                citations_reused += result["citations_reused"]
                processed += 1
            except Exception as exc:
                errors.append({"unit_id": unit_id or "unknown", "error": str(exc)})
        return {
            "processed": processed,
            "skipped": skipped,
            "citations_created": citations_created,
            "citations_reused": citations_reused,
            "errors": errors,
            "total": len(records),
        }

    def backfill_seed_note_citations(self, catalog_path: Path) -> Dict[str, Any]:
        """Backward-compatible alias for the original admin action."""
        return self.backfill_seed_citations(catalog_path)

    def list_unit_summaries(
        self,
        status: Optional[str] = None,
        unit_type: Optional[str] = None,
        source_origin_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        units = list(self.store.list_units())
        if status:
            units = [item for item in units if item.status == status]
        if unit_type:
            units = [item for item in units if item.unit_type == unit_type]
        if source_origin_id:
            citations = {item.citation_id: item for item in self.store.list_citations()}
            sources = {item.source_id: item for item in self.store.list_sources()}

            def cites_source(unit: CanonicalUnit) -> bool:
                return any(
                    citation is not None
                    and (source := sources.get(citation.source_id)) is not None
                    and source.origin_id == source_origin_id
                    for citation_id in unit.citation_ids
                    if (citation := citations.get(citation_id)) is not None
                )

            units = [item for item in units if cites_source(item)]
        def sort_key(item: CanonicalUnit):
            if item.unit_type == "passage" and item.primary_bible_refs:
                match = re.match(r"^[A-Za-z0-9]+\.(\d+)(?:\.(\d+))?", item.primary_bible_refs[0].osis)
                if match:
                    return (0, int(match.group(1)), int(match.group(2) or 0), item.title)
            return (1, 9999, 9999, item.title.casefold())

        return [
            {
                "unit_id": item.unit_id,
                "title": item.title,
                "unit_type": item.unit_type,
                "status": item.status,
                "primary_bible_refs": [ref.model_dump(mode="json") for ref in item.primary_bible_refs],
                "topic_assignments": [topic.model_dump(mode="json") for topic in item.topic_assignments],
                "manuscript": item.manuscript.model_dump(mode="json"),
                "citation_count": len(item.citation_ids),
            }
            for item in sorted(units, key=sort_key)
        ]

    def create_citation_from_source_range(
        self,
        source_id: str,
        start_line: int,
        end_line: int,
        highlight_text: Optional[str] = None,
        evidence_ids: Optional[List[str]] = None,
        role: str = "primary_evidence",
        supports_claim: str = "",
    ) -> Citation:
        if start_line < 1 or end_line < start_line:
            raise ValueError("Invalid source line range")
        source = self.store.get_source(source_id)
        source_map = self.store.get_source_map(source_id)
        matched = entries_for_line_range(source_map, start_line, end_line)
        if not matched:
            raise ValueError("Source range does not resolve through the source map")

        if source.source_type == "sermon_transcript":
            resolved = sermon_service.resolve_sermon_transcript(source.origin_id)
            paragraphs_by_key = {
                str(item.get("index") if item.get("index") is not None else position): item
                for position, item in enumerate(load_transcript_paragraphs(resolved["path"]))
            }
            paragraph_keys = [str(item["paragraph_key"]) for item in matched]
            fragment = "\n\n".join(str(paragraphs_by_key[key].get("text") or "").strip() for key in paragraph_keys)
            exact = (highlight_text or fragment).strip()
            if exact not in fragment:
                raise ValueError("Highlight text is not an exact substring of the mapped transcript fragment")
            locator = CitationLocator(
                kind="transcript",
                paragraph_keys=paragraph_keys,
                highlight_text=exact,
                highlight_text_sha256=sha256_text(exact),
                char_start=fragment.index(exact),
                char_end=fragment.index(exact) + len(exact),
                start_time=next((item.get("start_time") for item in matched if item.get("start_time") is not None), None),
                end_time=next((item.get("end_time") for item in reversed(matched) if item.get("end_time") is not None), None),
            )
        else:
            entry = matched[0]
            project_dir = self._project_dir(source.project_id)
            lines = (project_dir / "unified_source.md").read_text(encoding="utf-8").splitlines()
            fragment = "\n".join(lines[start_line - 1 : end_line]).strip()
            exact = (highlight_text or fragment).strip()
            if exact not in fragment:
                raise ValueError("Highlight text is not an exact substring of the mapped notes fragment")
            locator = CitationLocator(
                kind="notes",
                page_file=entry["page_file"],
                page_position=entry["page_position"],
                highlight_text=exact,
                highlight_text_sha256=sha256_text(exact),
                ocr_char_start=fragment.index(exact),
                ocr_char_end=fragment.index(exact) + len(exact),
            )
        citation = Citation(
            citation_id=f"CIT-{uuid4().hex}",
            source_id=source_id,
            source_sha256=source.source_sha256,
            locator=locator,
            evidence_ids=evidence_ids or [],
            role=role,
            supports_claim=supports_claim,
        )
        self.store.save_citation(citation)
        return citation

    def resolve_citation(self, citation_id: str) -> CitationResolution:
        citation = self.store.get_citation(citation_id)
        source = self.store.get_source(citation.source_id)
        if source.access == "restricted" or citation.status == "restricted":
            return self._resolution(citation, source, "restricted", "Source access is restricted", "", "")
        if citation.source_sha256 != source.source_sha256:
            return self._resolution(citation, source, "stale", "Source version changed; citation requires review", "", "")
        try:
            if citation.locator.kind == "transcript":
                resolved = sermon_service.resolve_sermon_transcript(source.origin_id)
                if sha256_bytes(resolved["path"].read_bytes()) != citation.source_sha256:
                    return self._resolution(citation, source, "stale", "Original transcript changed; citation requires review", "", "")
                paragraphs = load_transcript_paragraphs(resolved["path"])
                by_key = {
                    str(item.get("index") if item.get("index") is not None else position): item
                    for position, item in enumerate(paragraphs)
                }
                fragment = "\n\n".join(str(by_key[key].get("text") or "").strip() for key in citation.locator.paragraph_keys)
            else:
                project_dir = self._project_dir(source.project_id)
                fragment = (project_dir / "unified_source.md").read_text(encoding="utf-8")
        except (FileNotFoundError, KeyError, ValueError) as exc:
            return self._resolution(citation, source, "unresolved", str(exc), "", "")
        occurrence_start = self._nth_occurrence(fragment, citation.locator.highlight_text, citation.locator.occurrence)
        if occurrence_start < 0:
            return self._resolution(citation, source, "stale", "Exact highlighted text no longer resolves", "", "")
        before = fragment[max(0, occurrence_start - 160) : occurrence_start]
        after_start = occurrence_start + len(citation.locator.highlight_text)
        after = fragment[after_start : after_start + 160]
        return self._resolution(citation, source, "valid", None, before, after)

    @staticmethod
    def _nth_occurrence(text: str, needle: str, occurrence: int) -> int:
        start = -1
        cursor = 0
        for _ in range(max(1, occurrence)):
            start = text.find(needle, cursor)
            if start < 0:
                return -1
            cursor = start + len(needle)
        return start

    @staticmethod
    def _resolution(citation: Citation, source: SourceDocument, state: str, message: Optional[str], before: str, after: str) -> CitationResolution:
        if citation.locator.kind == "transcript":
            deep_link = f"{source.public_url}?citation={quote(citation.citation_id, safe='')}"
            if citation.locator.start_time is not None:
                deep_link += f"&t={int(citation.locator.start_time)}"
        else:
            deep_link = f"{source.public_url}?citation={quote(citation.citation_id, safe='')}"
        return CitationResolution(
            citation_id=citation.citation_id,
            state=state,
            source=source,
            locator=citation.locator,
            highlight_text=citation.locator.highlight_text,
            context_before=before,
            context_after=after,
            deep_link_url=deep_link,
            message=message,
        )

    def unit_detail(self, unit_id: str) -> Dict[str, Any]:
        unit = self.store.get_unit(unit_id)
        manuscript = self._manuscript_markdown_for_unit(unit)
        citations = [self.resolve_citation(item).model_dump(mode="json") for item in unit.citation_ids]
        relationships = [
            item.model_dump(mode="json")
            for item in self.store.list_relationships()
            if item.from_unit_id == unit_id or item.to_unit_id == unit_id
        ]
        return {"unit": unit.model_dump(mode="json"), "manuscript_markdown": manuscript, "citations": citations, "relationships": relationships}

    def _manuscript_markdown_for_unit(self, unit: CanonicalUnit) -> str:
        project_dir = self._project_dir(unit.manuscript.project_id)
        final_path = project_dir / "final.md"
        full_manuscript = final_path.read_text(encoding="utf-8") if final_path.is_file() else ""
        return self._extract_manuscript_section(full_manuscript, unit.manuscript.heading_title)

    @staticmethod
    def _extract_manuscript_section(markdown: str, heading_title: str) -> str:
        if not markdown.strip() or not heading_title.strip():
            return markdown
        lines = markdown.splitlines()
        target = heading_title.strip().lstrip("#").strip()
        start = None
        level = None
        for index, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match and match.group(2).strip() == target:
                start = index
                level = len(match.group(1))
                break
        if start is None or level is None:
            return markdown
        end = len(lines)
        for index in range(start + 1, len(lines)):
            match = re.match(r"^(#{1,6})\s+", lines[index])
            if match and len(match.group(1)) <= level:
                end = index
                break
        return "\n".join(lines[start:end]).strip() + "\n"

    def authoring_unit_detail(self, unit_id: str) -> Dict[str, Any]:
        unit = self.store.get_unit(unit_id)
        citations = []
        for citation_id in unit.citation_ids:
            citation = self.store.get_citation(citation_id)
            resolution = self.resolve_citation(citation_id)
            citations.append({"citation": citation.model_dump(mode="json"), "resolution": resolution.model_dump(mode="json")})
        return {
            "unit": unit.model_dump(mode="json"),
            "manuscript_markdown": self._manuscript_markdown_for_unit(unit),
            "citations": citations,
            "relationships": [
                item.model_dump(mode="json") for item in self.store.list_relationships()
                if item.from_unit_id == unit_id or item.to_unit_id == unit_id
            ],
        }

    def merge_units(self, target_unit_id: str, absorbed_unit_ids: List[str], apply: bool = False) -> Dict[str, Any]:
        from .models import UnitRelationship

        target = self.store.get_unit(target_unit_id)
        absorbed = [self.store.get_unit(item) for item in absorbed_unit_ids if item != target_unit_id]
        if not absorbed:
            raise ValueError("Select at least one different unit to merge")
        merged = target.model_copy(deep=True)
        merged.status = "candidate"
        for unit in absorbed:
            for ref in unit.primary_bible_refs:
                if ref.osis not in {item.osis for item in merged.primary_bible_refs}:
                    merged.primary_bible_refs.append(ref)
            for topic in unit.topic_assignments:
                key = (tuple(topic.topic_ids), tuple(topic.path), topic.role)
                existing = {(tuple(item.topic_ids), tuple(item.path), item.role) for item in merged.topic_assignments}
                if key not in existing:
                    merged.topic_assignments.append(topic)
            for value in [unit.title, *unit.aliases]:
                if value and value != merged.title and value not in merged.aliases:
                    merged.aliases.append(value)
            for citation_id in unit.citation_ids:
                if citation_id not in merged.citation_ids:
                    merged.citation_ids.append(citation_id)
        preview = {
            "target": merged.model_dump(mode="json"),
            "absorbed": [item.model_dump(mode="json") for item in absorbed],
            "changes": {
                "bible_reference_count": len(merged.primary_bible_refs),
                "topic_assignment_count": len(merged.topic_assignments),
                "citation_count": len(merged.citation_ids),
                "alias_count": len(merged.aliases),
            },
        }
        if not apply:
            return {"applied": False, **preview}
        relationships = self.store.list_relationships()
        for unit in absorbed:
            unit.status = "archived"
            relationship = UnitRelationship(
                relationship_id=stable_id("REL", unit.unit_id, target_unit_id, "merged_into"),
                from_unit_id=unit.unit_id,
                to_unit_id=target_unit_id,
                relationship_type="merged_into",
                status="approved",
                reason="Editorial merge into a canonical unit",
            )
            if relationship.relationship_id not in {item.relationship_id for item in relationships}:
                relationships.append(relationship)
            if relationship.relationship_id not in unit.relationship_ids:
                unit.relationship_ids.append(relationship.relationship_id)
            self.store.save_unit(unit)
        self.store.save_unit(merged)
        self.store.save_relationships(relationships)
        return {"applied": True, **preview}

    def status(self) -> RepositoryStatus:
        active = self.store.get_active_build()
        units = list(self.store.list_units())
        return RepositoryStatus(
            available=bool(active),
            active_build_id=active.get("build_id") if active else None,
            generated_at=active.get("generated_at") if active else None,
            unit_count=len(units),
            passage_count=sum(item.unit_type == "passage" for item in units),
            concept_count=sum(item.unit_type == "concept" for item in units),
            source_count=len(list(self.store.list_sources())),
            citation_count=len(list(self.store.list_citations())),
        )

    def compiled_index(self, name: str) -> Dict[str, Any]:
        active = self.store.get_active_build()
        if not active:
            return {"available": False}
        path = self.store.builds_dir / active["build_id"] / name
        if not path.is_file():
            return {"available": False}
        return json.loads(path.read_text(encoding="utf-8"))


canonical_repository_service = CanonicalRepositoryService()
