"""Load stable, anchorable source documents for knowledge extraction."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.pipeline.source_projection import (
    SOFT_DELETION,
    assert_locator_space_compatible,
    live_script,
    live_text,
    project_script,
    script_from_markdown_blocks,
    validate_visual_source_attestations,
)
from backend.pipeline.transcript_source import resolve_transcript_path


PUBLICATION_READINESS_DECISIONS = {
    "article_ready",
    "brief_note_only",
    "source_index_only",
    "insufficient_material",
}


def markdown_blocks(markdown: str) -> list[str]:
    """Return deterministic Markdown blocks without rewriting source text."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    return [block.strip() for block in re.split(r"\n[ \t]*\n+", normalized) if block.strip()]


def markdown_source_document(source: dict[str, Any]) -> tuple[dict[str, Any], bytes, Path]:
    path = Path(str(source["source_path"]))
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    blocks = markdown_blocks(text)
    script = script_from_markdown_blocks(blocks)
    payload = {
        "metadata": {
            "title": source.get("title") or path.stem,
            "status": "reviewed_editorial_source",
            "source_id": source["source_id"],
            "source_type": source.get("source_type", "notes_manuscript"),
            "project_id": source.get("project_id"),
            "source_url": source.get("source_url"),
            "lineage": source.get("lineage") or {},
        },
        "script": script,
    }
    return payload, raw, path


def load_knowledge_source_document(
    source: dict[str, Any], transcript_dirs: list[Path]
) -> tuple[dict[str, Any], bytes, Path]:
    """Resolve a package source without assuming every source is a transcript.

    Detailed knowledge packages may be anchored either to a sermon transcript or
    to a reviewed notes-to-manuscript Markdown file.  Review and adjudication
    must read the same canonical source used during extraction.
    """
    source_type = str(source.get("source_type") or "sermon_transcript")
    if source_type == "notes_manuscript":
        payload, raw, path = markdown_source_document(source)
    else:
        transcript_id = str(source.get("transcript_id") or source.get("source_id") or "")
        path = resolve_transcript_path(transcript_id, transcript_dirs)
        if path is None:
            raise FileNotFoundError(f"transcript not found: {transcript_id}")
        raw = path.read_bytes()
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            payload = {
                "metadata": {
                    "title": source.get("title") or transcript_id,
                    "status": "reviewed",
                },
                "script": parsed,
            }
        elif isinstance(parsed, dict):
            payload = parsed
        else:
            raise ValueError(f"{path}: transcript JSON must be an object or an array")

    actual_file_sha256 = hashlib.sha256(raw).hexdigest()
    projection = project_script(payload.get("script"))
    try:
        validate_visual_source_attestations(
            projection, source.get("visual_source_attestations")
        )
    except ValueError as exc:
        raise ValueError(f"visual source attestation mismatch: {exc}: {path}") from exc
    try:
        uses_body_coordinates = assert_locator_space_compatible(
            source, payload.get("script")
        )
    except ValueError as exc:
        raise ValueError(f"{exc}: {path}") from exc
    expected_file_sha256 = str(source.get("source_file_sha256") or "")
    expected_body_sha256 = str(source.get("source_body_sha256") or "")
    expected_visual_sha256 = source.get("source_visual_sha256")
    legacy_sha256 = str(source.get("source_sha256") or "")
    # For a new semantic descriptor the physical SHA is provenance, not the
    # staleness key: a comment-only editor save changes the bytes but neither
    # model input nor evidence. Legacy/file-only descriptors still require the
    # exact bytes they were originally bound to.
    if (
        expected_file_sha256
        and not uses_body_coordinates
        and expected_file_sha256 != actual_file_sha256
    ):
        raise ValueError(f"source file hash mismatch: {path}")
    if uses_body_coordinates and expected_body_sha256 != projection.body_sha256:
        raise ValueError(f"source body hash mismatch: {path}")
    if expected_visual_sha256 is not None and (
        str(expected_visual_sha256) != str(projection.visual_content_sha256 or "")
    ):
        raise ValueError(f"source visual hash mismatch: {path}")
    # Editorial structure is provenance and model/cache input, not a source
    # validity gate. Callers that show headings include the current rendered
    # structure in their own fingerprint; body-only consumers remain usable
    # after a heading typo is fixed.
    if legacy_sha256:
        expected = projection.body_sha256 if uses_body_coordinates else actual_file_sha256
        if legacy_sha256 != expected:
            raise ValueError(f"source hash mismatch: {path}")
    return payload, raw, path


def load_source_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"source manifest has no sources: {path}")
    required = {"source_id", "source_path", "source_type"}
    for index, row in enumerate(rows):
        missing = sorted(required - set(row))
        if missing:
            raise ValueError(f"source manifest row {index} missing: {', '.join(missing)}")
        source_path = Path(str(row["source_path"]))
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        expected = row.get("source_sha256")
        if expected and hashlib.sha256(source_path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"source hash mismatch: {source_path}")
    return rows


def load_publication_readiness(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(units, list) or not units:
        raise ValueError(f"publication readiness has no units: {path}")
    seen: set[str] = set()
    for index, row in enumerate(units):
        if not isinstance(row, dict):
            raise ValueError(f"publication readiness row {index} is not an object")
        passage = str(row.get("passage") or "").strip()
        decision = str(row.get("decision") or "").strip()
        if not passage:
            raise ValueError(f"publication readiness row {index} has no passage")
        if passage in seen:
            raise ValueError(f"duplicate publication readiness passage: {passage}")
        seen.add(passage)
        if decision not in PUBLICATION_READINESS_DECISIONS:
            raise ValueError(f"invalid publication readiness decision for {passage}: {decision}")
        if not str(row.get("reason") or "").strip():
            raise ValueError(f"publication readiness row {index} has no reason")
    return payload
