"""Restore exact automatic exclusions as editor-only Markdown soft deletions.

The timing-certified reading record stays immutable. This storage projection
retains excluded characters without admitting them as reader/Claim evidence.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path


def excluded_playback_context(record, source, paragraph_id):
    """Return original ASR audio context for a fully excluded, untimed row."""
    from backend.pipeline.full_video_transcript import original_paragraph_clip
    from backend.pipeline.slide_change_pilot import read_json

    source = Path(source)
    master = read_json(source/'master.json')
    if master['source_sha256'] != record['source_sha256']:
        raise ValueError('Excluded paragraph source identity changed')
    window_id, local_id = paragraph_id.split('-', 1)
    window = next((w for w in master['windows'] if w['id'] == window_id), None)
    if window is None:
        raise ValueError('Excluded paragraph source window is missing')
    begin, end, binding = original_paragraph_clip(source/'windows'/window_id, {'id': local_id}, window, max_seconds=None)
    return dict(start_ms=begin//16, end_ms=end//16, timing_precision='excluded_original_asr_context',
                word_timing_valid=False, excluded_source_context=binding)


def editor_paragraphs(record, source=None):
    exclusions = record.get("editorial_exclusions", [])
    if not exclusions:
        return record["paragraphs"], {}
    from opencc import OpenCC

    converter = OpenCC("s2t")
    visible = {p["id"]: p for p in record["paragraphs"]}
    uncut = record.get("uncut_corrected_paragraphs", [])
    by_id = {p["id"]: p for p in uncut}
    grouped = defaultdict(list)
    for exclusion in exclusions:
        if exclusion["paragraph_id"] not in by_id:
            raise ValueError("Soft deletion has no preserved source paragraph")
        grouped[exclusion["paragraph_id"]].append(exclusion)
    result, insertions = [], {}
    for original in uncut:
        pid = original["id"]
        changes = sorted(grouped.get(pid, []), key=lambda d: d["start"])
        if not changes:
            if pid not in visible:
                raise ValueError("Hidden paragraph lacks an exact exclusion")
            result.append(visible[pid])
            continue
        candidates = [original["text"], converter.convert(original["text"])]
        base = next(
            (
                text
                for text in candidates
                if all(
                    hashlib.sha256(text.encode()).hexdigest() == d["source_text_sha256"]
                    for d in changes
                )
            ),
            None,
        )
        if base is None:
            raise ValueError("Soft deletion belongs to different source characters")
        retained, marked, offsets, cursor = [], [], [], 0
        for deletion in changes:
            start, end = deletion["start"], deletion["end"]
            if (
                not cursor <= start < end <= len(base)
                or base[start:end] != deletion["old"]
                or "~" in deletion["old"]
            ):
                raise ValueError(
                    "Soft deletion has overlapping or changed source range"
                )
            kept = base[cursor:start]
            retained.append(kept)
            marked.append(kept)
            marker = "~~" + deletion["old"] + "~~"
            offsets.append(dict(offset=sum(map(len, retained)), marker=marker))
            marked.append(marker)
            cursor = end
        retained.append(base[cursor:])
        marked.append(base[cursor:])
        clean = "".join(retained)
        if pid in visible and clean != visible[pid]["text"]:
            raise ValueError("Soft deletion changes the timing-certified reading text")
        if pid not in visible and clean.strip():
            raise ValueError("Excluded paragraph contains unaccounted retained speech")
        p = dict(visible.get(pid, original), text="".join(marked))
        if pid not in visible:
            if (p.get('start_ms') is None or p.get('end_ms') is None) and source is not None:
                p.update(excluded_playback_context(record, source, pid))
            if not (
                type(p.get("start_ms")) is int
                and type(p.get("end_ms")) is int
                and 0 <= p["start_ms"] < p["end_ms"] <= record["duration_ms"]
            ):
                raise ValueError("Excluded paragraph has no source playback range")
            p["excluded_only"] = True
        result.append(p)
        insertions[pid] = offsets
    if [p["id"] for p in result if not p.get("excluded_only")] != list(visible):
        raise ValueError("Soft deletion changed visible paragraph order")
    return result, insertions


def turns_with_hidden_spans(text, turns, insertions):
    """Insert excluded source at exact retained offsets before speaker labels."""
    if "".join(t["text"] for t in turns) != text:
        raise ValueError("Speaker labels changed retained speech")
    markers = defaultdict(list)
    for insertion in insertions:
        if not 0 <= insertion["offset"] <= len(text):
            raise ValueError("Soft deletion outside retained speech")
        markers[insertion["offset"]].append(insertion["marker"])
    result, cursor = [], 0
    for turn in turns:
        parts = []
        for char in turn["text"]:
            parts.extend(markers.pop(cursor, []))
            parts.append(char)
            cursor += 1
        result.append({**turn, "text": "".join(parts)})
    if result:
        result[-1]["text"] += "".join(markers.pop(cursor, []))
    if markers:
        raise ValueError("Unplaced soft deletion")
    return result
