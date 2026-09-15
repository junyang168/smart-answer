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
    resolve_visual_source_descriptor,
    script_from_markdown_blocks,
    validate_visual_fragment_against_block,
    validate_visual_source_attestations,
)
from backend.pipeline.transcript_source import resolve_transcript_path


PUBLICATION_READINESS_DECISIONS = {
    "article_ready",
    "brief_note_only",
    "source_index_only",
    "insufficient_material",
}

MARKDOWN_IMAGE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)\)'
)


def markdown_blocks(markdown: str) -> list[str]:
    """Return deterministic Markdown blocks without rewriting source text."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    return [block.strip() for block in re.split(r"\n[ \t]*\n+", normalized) if block.strip()]


def _bind_markdown_visual_assets(
    script: list[dict[str, Any]], source: dict[str, Any]
) -> list[dict[str, Any]]:
    """Attach SHA-bound SVG files without rewriting the notes manuscript.

    The Markdown image token remains the textual source.  A projection-only
    row field carries the exact SVG bytes, path and physical SHA so visual
    evidence can be rendered and verified against that file.  The field is
    excluded from body identity by ``source_projection``; replacing the link
    with SVG here would create a body that ``source_path`` cannot reproduce.
    """

    assets = source.get("visual_source_assets") or []
    if not assets:
        return script
    if not isinstance(assets, list):
        raise ValueError("visual_source_assets must be a list")

    by_url: dict[str, dict[str, Any]] = {}
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ValueError(f"visual_source_assets row {index} is not an object")
        url = str(asset.get("markdown_url") or "").strip()
        path_value = str(asset.get("source_path") or "").strip()
        expected_sha256 = str(asset.get("source_sha256") or "").strip()
        if not url or not path_value or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError(
                f"visual_source_assets row {index} requires markdown_url, "
                "source_path and a lowercase SHA256"
            )
        if url in by_url:
            raise ValueError(f"duplicate visual source asset URL: {url}")
        by_url[url] = asset

    materialized: set[str] = set()
    result: list[dict[str, Any]] = []
    for original in script:
        row = dict(original)
        text = str(row.get("text") or "")
        bindings: list[dict[str, Any]] = []
        for match in MARKDOWN_IMAGE.finditer(text):
            url = match.group("url")
            asset = by_url.get(url)
            if asset is None:
                continue
            path = Path(str(asset["source_path"]))
            raw = path.read_bytes()
            actual_sha256 = hashlib.sha256(raw).hexdigest()
            expected_sha256 = str(asset["source_sha256"])
            if actual_sha256 != expected_sha256:
                raise ValueError(f"visual source asset hash mismatch: {path}")
            try:
                svg = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"visual source asset is not UTF-8 SVG: {path}") from exc
            if not re.search(r"<svg\b", svg, flags=re.I):
                raise ValueError(f"visual source asset is not SVG: {path}")
            materialized.add(url)
            bindings.append(
                {
                    "char_start": match.start(),
                    "char_end": match.end(),
                    "markdown_url": url,
                    "source_url": url,
                    "source_path": str(path),
                    "source_file_sha256": actual_sha256,
                    "raw_svg": svg,
                }
            )
        if bindings:
            row["_visual_source_assets"] = bindings
        result.append(row)

    unresolved = sorted(set(by_url) - materialized)
    if unresolved:
        raise ValueError(
            "visual source asset URL does not resolve to a Markdown image: "
            + ", ".join(unresolved)
        )
    return result


def markdown_source_document(source: dict[str, Any]) -> tuple[dict[str, Any], bytes, Path]:
    path = Path(str(source["source_path"]))
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    script = script_from_markdown_blocks(markdown_blocks(text))
    script = _bind_markdown_visual_assets(script, source)
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


def validate_package_current_source_provenance(
    package: dict[str, Any], transcript_dirs: list[Path]
) -> None:
    """Re-read every current source before a reviewed package may be stored.

    Review is not a durable source lock.  A Markdown manuscript, transcript, or
    separately bound SVG can change after review and before ingestion.  This
    boundary therefore resolves the package's own SourceDocument descriptors
    again and verifies every visual fragment against the freshly read visual
    block.  ``load_knowledge_source_document`` performs the body/file/visual
    identity checks; the fragment check proves that the stored visual locator
    still names the exact SVG asset rather than merely a processed projection.
    """

    sources = {
        str(row.get("source_id") or ""): row
        for row in package.get("source_documents") or []
        if isinstance(row, dict)
    }
    projections: dict[str, Any] = {}
    for source_id, source in sources.items():
        if not source_id:
            raise ValueError("package SourceDocument is missing source_id")
        payload, _raw, _path = load_knowledge_source_document(
            source, transcript_dirs
        )
        projection = project_script(payload.get("script"))
        projections[source_id] = projection
        current_visuals = {
            block.locator: block for block in projection.visual_blocks
        }
        persisted_visuals = source.get("visual_sources") or []
        if not isinstance(persisted_visuals, list):
            raise ValueError(f"{source_id}: visual_sources must be a list")
        if len(persisted_visuals) != len(current_visuals):
            raise ValueError(
                f"{source_id}: persisted and current visual-source counts differ"
            )
        for descriptor in persisted_visuals:
            if not isinstance(descriptor, dict):
                raise ValueError(f"{source_id}: visual source is not an object")
            locator = str(descriptor.get("locator") or "")
            current = current_visuals.get(locator)
            if current is None:
                raise ValueError(
                    f"{source_id}: persisted visual locator {locator!r} is not current"
                )
            row_index = int(current.segment_index[1:]) - 1
            paragraph_text = str(projection.body_rows[row_index].get("text") or "")
            resolved = resolve_visual_source_descriptor(
                descriptor, paragraph_text=paragraph_text
            )
            expected_descriptor = current.descriptor()
            for key, value in expected_descriptor.items():
                if descriptor.get(key) != value:
                    raise ValueError(
                        f"{source_id}: visual source {locator} has stale {key}"
                    )
            if resolved != current:
                raise ValueError(
                    f"{source_id}: persisted visual source {locator} differs from "
                    "the current source binding"
                )

    for fragment in package.get("source_fragments") or []:
        if not isinstance(fragment, dict):
            raise ValueError("package source fragment is not an object")
        source_id = str(fragment.get("source_id") or "")
        projection = projections.get(source_id)
        if projection is None:
            raise ValueError(
                f"{fragment.get('fragment_id')}: source fragment has no current "
                f"SourceDocument {source_id!r}"
            )
        if str(fragment.get("source_modality") or "spoken") != "visual":
            continue
        locator = str(
            fragment.get("visual_locator") or fragment.get("paragraph_key") or ""
        )
        matches = [
            block for block in projection.visual_blocks
            if block.locator == locator
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{fragment.get('fragment_id')}: current visual locator "
                f"{locator!r} resolves {len(matches)} times"
            )
        validate_visual_fragment_against_block(fragment, matches[0])


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
