from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .models import NotesMapEntry, SourceMap, TranscriptMapEntry


PAGE_MARKER = re.compile(r"^\s*<!--\s*Page:\s*(.+?)(?:\s+\(Not Processed\))?\s*-->\s*$")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _line_number_at(text: str, char_index: int) -> int:
    return text.count("\n", 0, char_index) + 1


def load_transcript_paragraphs(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    paragraphs = payload.get("script", []) if isinstance(payload, dict) else payload
    if not isinstance(paragraphs, list):
        raise ValueError("Sermon transcript has an unsupported format")
    return [
        paragraph
        for paragraph in paragraphs
        if isinstance(paragraph, dict)
        and paragraph.get("type") != "comment"
        and str(paragraph.get("text") or "").strip()
    ]


def build_transcript_source_map(
    source_id: str,
    transcript_path: Path,
    unified_source_path: Path,
) -> SourceMap:
    unified = unified_source_path.read_text(encoding="utf-8")
    source_bytes = transcript_path.read_bytes()
    entries: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []
    cursor = 0

    for position, paragraph in enumerate(load_transcript_paragraphs(transcript_path)):
        paragraph_text = str(paragraph.get("text") or "").strip()
        matches: List[int] = []
        search_at = cursor
        while True:
            found = unified.find(paragraph_text, search_at)
            if found < 0:
                break
            matches.append(found)
            search_at = found + max(1, len(paragraph_text))

        paragraph_key = str(paragraph.get("index") if paragraph.get("index") is not None else position)
        if not matches:
            missing.append({"paragraph_key": paragraph_key, "paragraph_position": position})
            continue
        start = matches[0]
        if len(matches) > 1:
            ambiguous.append({"paragraph_key": paragraph_key, "paragraph_position": position, "match_count": len(matches)})
        end = start + len(paragraph_text)
        cursor = end
        entries.append(
            TranscriptMapEntry(
                source_line_start=_line_number_at(unified, start),
                source_line_end=_line_number_at(unified, max(start, end - 1)),
                paragraph_key=paragraph_key,
                paragraph_position=position,
                paragraph_text_sha256=sha256_text(paragraph_text),
                start_time=paragraph.get("start_time"),
                end_time=paragraph.get("end_time"),
                start_index=paragraph.get("start_index"),
                end_index=paragraph.get("end_index"),
            ).model_dump(mode="json")
        )

    return SourceMap(
        source_id=source_id,
        source_type="sermon_transcript",
        source_sha256=sha256_bytes(source_bytes),
        unified_source_sha256=sha256_text(unified),
        entries=entries,
        missing=missing,
        ambiguous=ambiguous,
    )


def build_notes_source_map(
    source_id: str,
    unified_source_path: Path,
    raw_ocr_dir: Path,
) -> SourceMap:
    unified = unified_source_path.read_text(encoding="utf-8")
    lines = unified.splitlines()
    markers: List[Tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        match = PAGE_MARKER.match(line)
        if match:
            markers.append((line_number, match.group(1).strip()))

    entries: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    for position, (marker_line, page_file) in enumerate(markers):
        next_marker = markers[position + 1][0] if position + 1 < len(markers) else len(lines) + 1
        first_content = marker_line + 1
        while first_content < next_marker and not lines[first_content - 1].strip():
            first_content += 1
        last_content = next_marker - 1
        while last_content >= first_content and not lines[last_content - 1].strip():
            last_content -= 1
        safe_name = page_file.replace("/", "_").replace("\\", "_")
        ocr_path = raw_ocr_dir / f"{safe_name}.md"
        ocr_hash = sha256_bytes(ocr_path.read_bytes()) if ocr_path.is_file() else None
        if not ocr_path.is_file():
            missing.append({"page_file": page_file, "reason": "raw_ocr_missing"})
        page_line_count = len(ocr_path.read_text(encoding="utf-8").splitlines()) if ocr_path.is_file() else max(1, last_content - first_content + 1)
        entries.append(
            NotesMapEntry(
                source_line_start=first_content,
                source_line_end=max(first_content, last_content),
                page_file=page_file,
                page_position=position,
                ocr_line_start=1,
                ocr_line_end=max(1, page_line_count),
                page_ocr_sha256=ocr_hash,
            ).model_dump(mode="json")
        )

    return SourceMap(
        source_id=source_id,
        source_type="scanned_notes",
        source_sha256=sha256_text("\n".join(item[1] for item in markers)),
        unified_source_sha256=sha256_text(unified),
        entries=entries,
        missing=missing,
    )


def entries_for_line_range(source_map: SourceMap, start_line: int, end_line: int) -> List[Dict[str, Any]]:
    return [
        entry
        for entry in source_map.entries
        if int(entry["source_line_start"]) <= end_line and int(entry["source_line_end"]) >= start_line
    ]
