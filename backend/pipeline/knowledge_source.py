"""Load stable, anchorable source documents for knowledge extraction."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.pipeline.transcript_source import resolve_transcript_path


PUBLICATION_READINESS_DECISIONS = {
    "article_ready",
    "brief_note_only",
    "source_index_only",
    "insufficient_material",
}


#: A proofreader deletes from a transcript by striking the text through rather
#: than removing it, so the cut stays reversible. Everything between the two
#: markers is to be read as absent.
#:
#: The pattern is `SurmonEditor`'s own (`FALLBACK_STRIKETHROUGH_PATTERN`),
#: deliberately, because what the proofreader saw struck through on screen is
#: the definition of what was deleted. Two consequences follow from copying it
#: rather than inventing a looser one, and both were measured on the 115
#: published transcripts:
#:
#: * The editor renders each segment as its own Markdown document, so a marker
#:   opened in one segment and closed in another strikes nothing and shows as
#:   literal tildes. Matching across segments would have deleted 39,282
#:   characters nobody deleted.
#: * `[^~]+?` means an unpaired marker deletes nothing at all. 11 segments in
#:   the corpus carry one; under a greedier rule each would swallow the rest of
#:   its segment.
SOFT_DELETION = re.compile(r"~~([^~]+?)~~", re.S)


def live_text(text: str) -> str:
    """One segment with its soft-deleted spans removed.

    The span becomes a newline, never nothing. Deleting from the middle of
    `甲~~乙~~丙` and closing the gap yields `甲丙` -- a string the professor
    never said, which `verbatim_excerpt` validation would then happily accept
    as contiguous source text. A newline is also where `sentence_spans` breaks,
    so the two survivors cannot be read as one sentence either.
    """

    return SOFT_DELETION.sub("\n", str(text or ""))


def live_script(script: Any) -> list[dict[str, Any]]:
    """A transcript's segments with the deleted text gone, positions intact.

    A segment struck in full stays in the list as an empty one. Dropping it
    would renumber every segment after it, and `S0007` is a position -- every
    anchor, every exclusion id and every section boundary in the claim layer
    resolves through it. An empty segment contributes no sentences and no
    anchors, which is the whole of what "deleted" has to mean here.
    """

    rows: list[dict[str, Any]] = []
    for segment in script or []:
        row = dict(segment) if isinstance(segment, dict) else {"text": str(segment or "")}
        row["text"] = live_text(row.get("text"))
        rows.append(row)
    return rows


def markdown_blocks(markdown: str) -> list[str]:
    """Return deterministic Markdown blocks without rewriting source text."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    return [block.strip() for block in re.split(r"\n[ \t]*\n+", normalized) if block.strip()]


def markdown_source_document(source: dict[str, Any]) -> tuple[dict[str, Any], bytes, Path]:
    path = Path(str(source["source_path"]))
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    blocks = markdown_blocks(text)
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
        "script": [
            {
                "index": index,
                "start_time": None,
                "end_time": None,
                "text": block,
            }
            for index, block in enumerate(blocks, start=1)
        ],
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

    expected_sha256 = str(source.get("source_sha256") or "")
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and expected_sha256 != actual_sha256:
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
