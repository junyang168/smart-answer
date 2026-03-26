"""
subtitle.py - Generate timed Traditional Chinese SRT captions.

Whisper is used only for timing. The ground-truth caption text comes from
storyboard scene voiceover_text, which is the same source used to generate TTS.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from openai import OpenAI
from opencc import OpenCC

from backend.api.config import OPENAI_API_KEY


_CUE_MARKER_RE = re.compile(r"\[[^\[\]]+\]")
_PUNCTUATION = set("，。！？；：、")


def _strip_cue_markers(text: str) -> str:
    return _CUE_MARKER_RE.sub("", text or "")


def _to_caption_text(text: str, cc: OpenCC) -> str:
    cleaned = _strip_cue_markers(text)
    cleaned = cc.convert(cleaned)
    return re.sub(r"\s+", "", cleaned.strip())


def _parse_srt_time(time_str: str) -> int:
    h, m, s_ms = time_str.split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def _format_srt_time(ms_time: int) -> str:
    ms_time = int(ms_time)
    h = ms_time // 3600000
    m = (ms_time % 3600000) // 60000
    s = (ms_time % 60000) // 1000
    ms = ms_time % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _split_for_srt(text: str, target_max_chars: int = 18, min_chunk_chars: int = 3) -> list[str]:
    """
    Deterministically split Traditional Chinese subtitle text into readable chunks.
    - Prefer punctuation boundaries.
    - Avoid tiny orphan chunks by merging back.
    """
    normalized = re.sub(r"\s+", "", text.strip())
    if not normalized:
        return []

    units = []
    current = ""
    for ch in normalized:
        current += ch
        if ch in _PUNCTUATION:
            units.append(current)
            current = ""
    if current:
        units.append(current)

    chunks: list[str] = []
    acc = ""
    for unit in units:
        if len(unit) > target_max_chars:
            if acc:
                chunks.append(acc)
                acc = ""
            start = 0
            while start < len(unit):
                end = min(len(unit), start + target_max_chars)
                chunks.append(unit[start:end])
                start = end
            continue

        if not acc:
            acc = unit
        elif len(acc) + len(unit) <= target_max_chars:
            acc += unit
        else:
            chunks.append(acc)
            acc = unit
    if acc:
        chunks.append(acc)

    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) <= min_chunk_chars:
            merged[-1] += chunk
        else:
            merged.append(chunk)

    if len(merged) >= 2 and len(merged[0]) <= min_chunk_chars:
        merged[1] = merged[0] + merged[1]
        merged = merged[1:]

    return [c for c in merged if c]


def _allocate_chunk_times(start_ms: int, end_ms: int, chunks: list[str]) -> list[tuple[int, int]]:
    if not chunks:
        return []
    if len(chunks) == 1:
        return [(start_ms, end_ms)]

    total = max(1, end_ms - start_ms)
    weights = [max(1, len(chunk)) for chunk in chunks]
    weight_sum = sum(weights)

    ranges: list[tuple[int, int]] = []
    cursor = start_ms
    cumulative = 0
    for i, weight in enumerate(weights):
        if i == len(weights) - 1:
            seg_end = end_ms
        else:
            cumulative += weight
            seg_end = start_ms + round(total * cumulative / weight_sum)
            if seg_end <= cursor:
                seg_end = min(end_ms, cursor + 1)
        ranges.append((cursor, seg_end))
        cursor = seg_end
    return ranges


def _parse_whisper_srt(raw_srt: str, cc: OpenCC) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    raw_cursor = 0

    blocks = [block for block in str(raw_srt).strip().split("\n\n") if block.strip()]
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue

        timestamp = lines[1].strip()
        start_str, end_str = timestamp.split(" --> ")
        text = " ".join(lines[2:])
        aligned_text = _to_caption_text(text, cc)

        char_start = raw_cursor
        raw_cursor += len(aligned_text)
        char_end = raw_cursor

        segments.append(
            {
                "id": lines[0].strip(),
                "time": timestamp,
                "start_ms": _parse_srt_time(start_str.strip()),
                "end_ms": _parse_srt_time(end_str.strip()),
                "raw_text": text,
                "aligned_text": aligned_text,
                "char_start": char_start,
                "char_end": char_end,
            }
        )

    return segments


def _build_script_text(storyboard: dict, cc: OpenCC) -> str:
    scenes = storyboard.get("scenes", []) if isinstance(storyboard, dict) else storyboard
    parts = [_to_caption_text(item.get("voiceover_text", ""), cc) for item in scenes if item.get("voiceover_text")]
    return "".join(parts)


def _build_boundary_map(raw_text: str, script_text: str) -> list[int]:
    matcher = SequenceMatcher(None, raw_text, script_text, autojunk=False)
    boundary_map: list[int | None] = [None] * (len(raw_text) + 1)

    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        raw_len = a1 - a0
        script_len = b1 - b0

        if tag == "insert":
            boundary_map[a0] = b1
            continue

        if raw_len == 0:
            boundary_map[a0] = b1
            continue

        if tag == "delete":
            for offset in range(raw_len + 1):
                boundary_map[a0 + offset] = b0
            continue

        for offset in range(raw_len + 1):
            mapped = b0 + round(offset * script_len / raw_len)
            boundary_map[a0 + offset] = mapped

    boundary_map[0] = 0 if boundary_map[0] is None else boundary_map[0]
    boundary_map[-1] = len(script_text)

    last_seen = 0
    for idx, value in enumerate(boundary_map):
        if value is None:
            boundary_map[idx] = last_seen
        else:
            clipped = max(last_seen, min(len(script_text), value))
            boundary_map[idx] = clipped
            last_seen = clipped

    return [int(value) for value in boundary_map]


def _align_segments_to_script(segments: list[dict[str, Any]], script_text: str) -> list[dict[str, Any]]:
    raw_text = "".join(segment["aligned_text"] for segment in segments)
    if not raw_text or not script_text:
        return segments

    boundary_map = _build_boundary_map(raw_text, script_text)

    aligned_segments: list[dict[str, Any]] = []
    for segment in segments:
        start_idx = boundary_map[segment["char_start"]]
        end_idx = boundary_map[segment["char_end"]]
        if end_idx < start_idx:
            end_idx = start_idx

        aligned_segments.append(
            {
                **segment,
                "caption_text": script_text[start_idx:end_idx],
            }
        )

    return aligned_segments


def generate_srt(storyboard: dict, output_path: Path) -> Path | None:
    """
    Extract timed, Traditional Chinese SRT captions.

    Whisper provides timing. storyboard.scene.voiceover_text provides the
    caption text, with cue markers stripped before alignment/output.
    """
    if not OPENAI_API_KEY:
        print("⚠️ OPENAI_API_KEY is missing from .env. Cannot call Whisper for timing.")
        return None

    work_dir = output_path.parent
    audio_file_path = work_dir / "full_audio.mp3"
    if not audio_file_path.exists():
        print("⚠️ full_audio.mp3 not found! Cannot generate subtitles.")
        return None

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=45.0)
    cc = OpenCC("s2t")

    print("🎙️ Sending audio to OpenAI Whisper for precision timing...")
    with open(audio_file_path, "rb") as audio_file:
        raw_srt = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="srt",
        )

    if not raw_srt:
        print("⚠️ Whisper returned empty SRT.")
        return None

    segments = _parse_whisper_srt(str(raw_srt), cc)
    script_text = _build_script_text(storyboard, cc)
    aligned_segments = _align_segments_to_script(segments, script_text)

    final_srt_lines = []
    current_idx = 1

    for segment in aligned_segments:
        text_content = segment.get("caption_text", "").strip()
        if not text_content:
            continue

        chunks = _split_for_srt(text_content, target_max_chars=18, min_chunk_chars=3)
        if not chunks:
            chunks = [text_content]

        chunk_ranges = _allocate_chunk_times(segment["start_ms"], segment["end_ms"], chunks)
        for (chunk_start, chunk_end), chunk in zip(chunk_ranges, chunks):
            final_srt_lines.append(str(current_idx))
            final_srt_lines.append(f"{_format_srt_time(chunk_start)} --> {_format_srt_time(chunk_end)}")
            final_srt_lines.append(chunk)
            final_srt_lines.append("")
            current_idx += 1

    final_srt_str = "\n".join(final_srt_lines).strip()
    output_path.write_text(final_srt_str, encoding="utf-8")
    print(f"✅ Closed Captions (aligned to voiceover_text) saved → {output_path}")
    return output_path
