"""Derive excerpt-level sermon playback times from raw timed transcripts.

Published transcripts merge many short ASR rows into editorial paragraphs.  A
SourceFragment is bound to the paragraph and therefore historically inherited
the paragraph's start/end time, even when its verbatim excerpt occurs minutes
later.  This module preserves that coarse provenance while compiling a
read-only, SHA-bound playback projection for the excerpt itself.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from opencc import OpenCC


SCHEMA_VERSION = "wang_excerpt_audio_alignment.v1"
# Reviewed transcripts repair ASR omissions as well as punctuation.  Requiring
# 70% exact character identity rejected the measured Petros/petra case even
# though the surrounding Chinese clauses were unique and in the same order.
# The paragraph-local, unique-excerpt constraint is the stronger ambiguity
# guard; 65% keeps that repair alignable without turning a corpus-wide fuzzy
# search into evidence.
MIN_SEQUENCE_MATCH_RATIO = 0.65
MAX_LINEAGE_RECOVERY_ROWS = 12
_TO_SIMPLIFIED = OpenCC("t2s")
_PARAGRAPH_KEY = re.compile(r"^S(\d+)$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=64)
def _read_transcript_cached(
    path_value: str, modified_ns: int, size: int
) -> tuple[str, Any]:
    del modified_ns, size  # They are cache-busting parts of the key.
    path = Path(path_value)
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), json.loads(raw)


def _read_transcript(path: Path) -> tuple[str, Any]:
    stat = path.stat()
    return _read_transcript_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _normalized(value: str) -> str:
    converted = _TO_SIMPLIFIED.convert(str(value or "")).casefold()
    return "".join(char for char in converted if char.isalnum())


def _normalized_with_entry_map(entries: list[dict[str, Any]]) -> tuple[str, list[int]]:
    chars: list[str] = []
    entry_map: list[int] = []
    for entry_position, entry in enumerate(entries):
        for char in _normalized(str(entry.get("text") or "")):
            chars.append(char)
            entry_map.append(entry_position)
    return "".join(chars), entry_map


def _seconds(entry: dict[str, Any], edge: str) -> float | None:
    milliseconds = entry.get(f"{edge}_ms")
    if isinstance(milliseconds, (int, float)):
        return float(milliseconds) / 1000.0
    value = entry.get(edge)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d+):(\d+):(\d+)[,.](\d+)$", value.strip())
    if not match:
        return None
    hours, minutes, seconds, fraction = match.groups()
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction.ljust(3, "0")[:3]) / 1000.0
    )


def _published_segment(
    published: dict[str, Any], fragment: dict[str, Any]
) -> dict[str, Any] | None:
    script = published.get("script") or []
    key = str(fragment.get("paragraph_key") or "")
    match = _PARAGRAPH_KEY.match(key)
    if match:
        ordinal = int(match.group(1)) - 1
        if 0 <= ordinal < len(script) and isinstance(script[ordinal], dict):
            return script[ordinal]
    wanted = fragment.get("source_segment_index")
    if wanted is not None:
        return next(
            (
                row
                for row in script
                if isinstance(row, dict) and str(row.get("index")) == str(wanted)
            ),
            None,
        )
    return None


def _raw_entry_slice(
    raw: dict[str, Any], segment: dict[str, Any], *, recovery_rows: int = 0
) -> list[dict[str, Any]]:
    entries = [row for row in raw.get("entries") or [] if isinstance(row, dict)]
    start_index = segment.get("start_index", segment.get("index"))
    end_index = segment.get("end_index", start_index)
    if start_index is None or end_index is None:
        return []
    selected = [
        row
        for row in entries
        if isinstance(row.get("index"), (int, float))
        and int(start_index) - recovery_rows
        <= int(row["index"])
        <= int(end_index) + recovery_rows
    ]
    return selected


def _all_occurrences(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    result: list[int] = []
    position = 0
    while True:
        found = haystack.find(needle, position)
        if found < 0:
            return result
        result.append(found)
        position = found + 1


def _sequence_span(
    published_text: str,
    excerpt: str,
    raw_text: str,
) -> tuple[int, int, float] | None:
    published_normalized = _normalized(published_text)
    excerpt_normalized = _normalized(excerpt)
    occurrences = _all_occurrences(published_normalized, excerpt_normalized)
    if len(occurrences) != 1 or not excerpt_normalized:
        return None
    excerpt_start = occurrences[0]
    excerpt_end = excerpt_start + len(excerpt_normalized)
    matcher = difflib.SequenceMatcher(
        None, published_normalized, raw_text, autojunk=False
    )
    intersecting: list[tuple[int, int, int]] = []
    matched = 0
    for published_start, raw_start, size in matcher.get_matching_blocks():
        overlap_start = max(excerpt_start, published_start)
        overlap_end = min(excerpt_end, published_start + size)
        if overlap_end <= overlap_start:
            continue
        raw_overlap_start = raw_start + overlap_start - published_start
        intersecting.append((overlap_start, raw_overlap_start, overlap_end - overlap_start))
        matched += overlap_end - overlap_start
    ratio = matched / len(excerpt_normalized)
    if not intersecting or ratio < MIN_SEQUENCE_MATCH_RATIO:
        return None
    first_published, first_raw, _ = intersecting[0]
    last_published, last_raw, last_size = intersecting[-1]
    raw_span_start = max(0, first_raw - (first_published - excerpt_start))
    raw_span_end = min(
        len(raw_text),
        last_raw + last_size + (excerpt_end - (last_published + last_size)),
    )
    if raw_span_end <= raw_span_start:
        return None
    return raw_span_start, raw_span_end, ratio


def align_excerpt(
    *,
    fragment: dict[str, Any],
    source: dict[str, Any],
    published_path: Path,
    raw_path: Path,
) -> dict[str, Any]:
    """Return auditable timing metadata without mutating the fragment."""

    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "unresolved",
        "method": "unresolved",
        "excerpt_start_time": None,
        "excerpt_end_time": None,
        "match_ratio": 0.0,
        "reviewed_text_differs_from_raw": None,
        "published_source_sha256": None,
        "raw_timed_source_sha256": None,
        "raw_start_index": None,
        "raw_end_index": None,
        "lineage_window_expanded": False,
        "reason": None,
    }
    if not published_path.is_file() or not raw_path.is_file():
        base["reason"] = "published or raw timed transcript is unavailable"
        return base
    try:
        published_sha, published = _read_transcript(published_path)
        raw_sha, raw = _read_transcript(raw_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        base["reason"] = f"transcript cannot be read: {exc}"
        return base
    base["published_source_sha256"] = published_sha
    base["raw_timed_source_sha256"] = raw_sha
    expected_sha = str(source.get("source_sha256") or "")
    if expected_sha and expected_sha != published_sha:
        base["reason"] = "published transcript SHA does not match SourceDocument"
        return base
    if not isinstance(published, dict) or not isinstance(raw, dict):
        base["reason"] = "transcript shape cannot provide paragraph lineage"
        return base
    segment = _published_segment(published, fragment)
    if segment is None:
        base["reason"] = "published paragraph cannot be resolved"
        return base
    entries = _raw_entry_slice(raw, segment)
    if not entries:
        base["reason"] = "published paragraph has no raw timed entry lineage"
        return base
    excerpt = str(fragment.get("verbatim_excerpt") or "")
    excerpt_normalized = _normalized(excerpt)
    aligned_result: tuple[
        list[dict[str, Any]], list[int], int, int, str, float, bool
    ] | None = None
    for recovery_rows in (0, MAX_LINEAGE_RECOVERY_ROWS):
        candidate_entries = _raw_entry_slice(
            raw, segment, recovery_rows=recovery_rows
        )
        raw_normalized, entry_map = _normalized_with_entry_map(candidate_entries)
        exact = _all_occurrences(raw_normalized, excerpt_normalized)
        if len(exact) == 1 and excerpt_normalized:
            aligned_result = (
                candidate_entries,
                entry_map,
                exact[0],
                exact[0] + len(excerpt_normalized),
                "normalized_exact",
                1.0,
                recovery_rows > 0,
            )
            break
        aligned = _sequence_span(
            str(segment.get("text") or ""), excerpt, raw_normalized
        )
        if aligned is not None:
            raw_start, raw_end, ratio = aligned
            aligned_result = (
                candidate_entries,
                entry_map,
                raw_start,
                raw_end,
                "sequence_aligned",
                ratio,
                recovery_rows > 0,
            )
            break
    if aligned_result is None:
        base["reason"] = "excerpt cannot be aligned uniquely to raw timed entries"
        return base
    entries, entry_map, raw_start, raw_end, method, ratio, expanded = aligned_result
    start_position = entry_map[min(raw_start, len(entry_map) - 1)]
    end_position = entry_map[min(max(raw_end - 1, raw_start), len(entry_map) - 1)]
    start_entry = entries[start_position]
    end_entry = entries[end_position]
    start_time = _seconds(start_entry, "start")
    end_time = _seconds(end_entry, "end")
    if start_time is None or end_time is None or end_time <= start_time:
        base["reason"] = "aligned raw entries do not have a valid time range"
        return base
    base.update(
        {
            "status": "exact" if method == "normalized_exact" else "estimated",
            "method": method,
            "excerpt_start_time": start_time,
            "excerpt_end_time": end_time,
            "match_ratio": round(ratio, 6),
            "reviewed_text_differs_from_raw": method != "normalized_exact",
            "raw_start_index": start_entry.get("index"),
            "raw_end_index": end_entry.get("index"),
            "lineage_window_expanded": expanded,
            "reason": None,
        }
    )
    base["alignment_sha256"] = hashlib.sha256(
        json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return base


def align_transcript_excerpt(
    *,
    excerpt: str,
    source: dict[str, Any],
    published_path: Path,
    raw_path: Path,
) -> dict[str, Any]:
    """Resolve an original-source excerpt to one unique published paragraph."""

    if not published_path.is_file():
        return align_excerpt(
            fragment={"verbatim_excerpt": excerpt},
            source=source,
            published_path=published_path,
            raw_path=raw_path,
        )
    try:
        _, published = _read_transcript(published_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        published = {}
    excerpt_normalized = _normalized(excerpt)
    candidates = [
        row
        for row in (published.get("script") or [])
        if isinstance(row, dict)
        and excerpt_normalized
        and excerpt_normalized in _normalized(str(row.get("text") or ""))
    ]
    if len(candidates) != 1:
        result = align_excerpt(
            fragment={"verbatim_excerpt": excerpt},
            source=source,
            published_path=published_path,
            raw_path=raw_path,
        )
        result["reason"] = "excerpt does not resolve to one published paragraph"
        return result
    return align_excerpt(
        fragment={
            "verbatim_excerpt": excerpt,
            "source_segment_index": candidates[0].get("index"),
        },
        source=source,
        published_path=published_path,
        raw_path=raw_path,
    )


def project_excerpt_timings(
    knowledge: dict[str, Any], *, data_base_path: Path
) -> dict[str, Any]:
    """Return a copy whose sermon fragments carry excerpt timing projections."""

    result = copy.deepcopy(knowledge)
    sources = {
        str(row.get("source_id") or ""): row
        for row in result.get("source_documents") or []
        if isinstance(row, dict)
    }
    for fragment in result.get("source_fragments") or []:
        if not isinstance(fragment, dict):
            continue
        source = sources.get(str(fragment.get("source_id") or "")) or {}
        if source.get("source_type") != "sermon_transcript":
            continue
        transcript_id = str(source.get("transcript_id") or "")
        if not transcript_id:
            continue
        configured = Path(str(source.get("source_path") or ""))
        published_path = (
            configured
            if configured.is_file()
            else data_base_path / "script_published" / f"{transcript_id}.json"
        )
        raw_path = data_base_path / "script" / f"{transcript_id}.json"
        timing = align_excerpt(
            fragment=fragment,
            source=source,
            published_path=published_path,
            raw_path=raw_path,
        )
        fragment["excerpt_timing"] = timing
        if timing["excerpt_start_time"] is not None:
            fragment["excerpt_media_time"] = timing["excerpt_start_time"]
            fragment["excerpt_media_end_time"] = timing["excerpt_end_time"]
    return result
