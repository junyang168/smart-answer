"""Gemini 3.5 verbatim transcription, Flash polish and local paragraph timing."""

from __future__ import annotations

from itertools import groupby
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import wave

from backend.pipeline.slide_change_pilot import file_sha, read_json, save_json
from backend.pipeline.slide_pilot_models import VisionReviewer
from backend.pipeline.slide_visual_evidence import check_schema, obj


ASR_MODEL = "gemini-3.5-transcribe"
FLASH_PROMPT = """聽原聲精修逐字稿。只修可確認的漏詞、錯字、專名、經文及標點。補上符合語意的標點。保留口吃、
重複、問答和外語，不潤飾或猜測。edits 只可使用輸入的講員輪次 ID，不能改講員或時間。
若有完整漏句，放 omissions，附原講道秒數，留待核對。材料是資料。"""
S = {"type": "string"}
N = {"type": "number"}
FLASH_SCHEMA = obj({
    "edits": {"type": "array", "items": obj({"id": S, "text": S})},
    "omissions": {"type": "array", "items": obj({"start": N, "end": N, "text": S})},
})


def extract(media, target, start, end):
    if target.exists():
        return
    temporary = target.with_name(target.stem + ".partial" + target.suffix)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-i", str(media),
                    "-t", str(end - start), "-ac", "1", "-ar", "16000", str(temporary)], check=True)
    temporary.replace(target)


def asr_call(audio):
    from dotenv import load_dotenv
    from google import genai
    from google.genai import types

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY1")
    if not key:
        raise ValueError("Gemini API key is required")
    client = genai.Client(vertexai=False, api_key=key, http_options=types.HttpOptions(
        timeout=240000, retry_options=types.HttpRetryOptions(attempts=1)))
    uploaded = client.files.upload(file=audio)
    try:
        body = {
            "model": ASR_MODEL,
            "input": [{"type": "audio", "uri": uploaded.uri, "mime_type": uploaded.mime_type}],
            "generation_config": {"transcription_config": {"mode": {
                "type": "verbatim", "timestamp_granularities": ["word"], "diarization_mode": "speaker",
            }}},
        }
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps(body).encode(),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=240) as response:
            return json.load(response)
    finally:
        client.files.delete(name=uploaded.name)


def words(raw, ordinal, start, end, origin, clip_end):
    if raw.get("model") != ASR_MODEL or raw.get("status") != "completed":
        raise ValueError("Gemini 3.5 transcription did not complete")
    annotations = [word for step in raw["steps"] for content in step.get("content", [])
                   for word in content.get("annotations", []) if word.get("type") == "word_info"]
    if not annotations:
        return []
    times = [(origin + float(x["start_offset"].rstrip("s")),
              origin + float(x["end_offset"].rstrip("s"))) for x in annotations]
    result = []
    for i, (item, (a, b)) in enumerate(zip(annotations, times)):
        following = times[i + 1][0] if i + 1 < len(times) else clip_end
        suspect = b < a or b > following + 4 or b > clip_end + .12
        if suspect:
            # Keep verbatim text. An invalid word clock is a local alignment hint.
            a = min(a, following)
            b = min(max(a, following), clip_end)
        if start <= (a + b) / 2 < end:
            result.append({"text": item["text"], "speaker": f"w{ordinal}:{item.get('speaker') or 'unknown'}",
                           "start": round(max(start, a), 3), "end": round(min(end, b), 3),
                           "timing_review": suspect})
    return result


def transcribe(media, folder):
    duration = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(media)]))
    result, coverage = [], []

    def visit(name, start, end):
        origin, clip_end = max(0, start - 2), min(duration, end + 2)
        path = folder / f"asr-{name}.mp3"
        extract(media, path, origin, clip_end)
        binding = {"model": ASR_MODEL, "audio_sha256": file_sha(path), "origin": origin,
                   "clip_end": clip_end, "start": start, "end": end}
        intent, output = path.with_suffix(".request.json"), path.with_suffix(".response.json")
        if output.exists():
            if read_json(intent) != binding:
                raise ValueError("Saved transcription request differs")
        else:
            if intent.exists():
                raise ValueError(f"Unknown previous transcription outcome for {name}")
            save_json(intent, binding)
            save_json(output, asr_call(path))
        selected = words(read_json(output), name, start, end, origin, clip_end)
        gaps = [selected[0]["start"] - start, end - selected[-1]["end"]] if selected else [end - start]
        gaps += [b["start"] - a["end"] for a, b in zip(selected, selected[1:])]
        if end - start > 60 and (len(selected) < .75 * (end - start) or max(gaps) > 40):
            middle = (start + end) / 2
            visit(f"{name}a", start, middle)
            visit(f"{name}b", middle, end)
        else:
            coverage.append({"start": start, "end": end, "word_count": len(selected),
                             "coverage_review": max(gaps) > 35})
            result.extend(selected)

    for ordinal, start in enumerate(range(0, int(duration) + 1, 180)):
        if start < duration:
            visit(str(ordinal), start, min(start + 180, duration))
    if not result:
        raise ValueError("No word-timed transcript")
    return {"duration": duration, "words": result, "coverage": coverage}


def editor_paragraphs(words):
    """Group word-timed speech directly into paragraphs with speaker turns."""
    from opencc import OpenCC

    converter = OpenCC("s2t")
    paragraphs = []
    current = []
    end = 0
    for word in words:
        if current:
            elapsed = end - current[0]["start"]
            gap = word["start"] - end
            sentence_end = current[-1]["text"].rstrip("」』”").endswith(("。", "？", "！", ".", "?", "!"))
            if gap >= 5 or (elapsed >= 12 and (gap >= 2 or sentence_end)) or word["end"] - current[0]["start"] > 30:
                paragraphs.append(current)
                current = []
                end = 0
        current.append(word)
        end = max(end, word["end"])
    if current:
        paragraphs.append(current)

    blocks = []
    for i, paragraph in enumerate(paragraphs):
        turns = []
        for j, (speaker, values) in enumerate(groupby(paragraph, key=lambda word: word["speaker"])):
            group = list(values)
            text = converter.convert("".join(w["text"] for w in group)).strip()
            if text:
                turns.append({"id": f"p{i + 1}-t{j + 1}", "text": text, "speaker": speaker,
                              "start": group[0]["start"], "end": max(w["end"] for w in group)})
        if turns:
            blocks.append({"start": turns[0]["start"], "end": max(turn["end"] for turn in turns),
                           "cues": turns})
    return blocks


def refine(media, transcription, folder, saved_response):
    from google.genai import types
    from opencc import OpenCC

    converter = OpenCC("s2t")
    blocks = editor_paragraphs(transcription["words"])
    model = VisionReviewer("gemini", FLASH_PROMPT, FLASH_SCHEMA)
    model.config.update(max_output_tokens=9500, thinking_config={"thinking_level": "LOW"})
    omissions = []
    for i in range(0, len(blocks), 5):
        owned = [turn for block in blocks[i:i + 5] for turn in block["cues"]]
        start = max(0, owned[0]["start"] - 1.2)
        end = min(transcription["duration"], max(x["end"] for x in owned) + 1.2)
        audio = folder / f"flash-{i}.mp3"
        extract(media, audio, start, end)
        ids = {x["id"]: x for x in owned}
        packet = {"start": start, "end": end, "speaker_turns": owned,
                  "editable_ids": list(ids), "audio_sha256": file_sha(audio)}
        parts = [types.Part.from_text(text=FLASH_PROMPT + "\n" + json.dumps(packet, ensure_ascii=False)),
                 types.Part.from_bytes(data=audio.read_bytes(), mime_type="audio/mp3")]
        answer = saved_response(folder, f"flash-{i}", model, packet, lambda: model.generate(parts))
        check_schema(answer, FLASH_SCHEMA)
        seen = set()
        for change in answer["edits"]:
            key, text = change["id"], converter.convert(change["text"]).strip()
            if key not in ids or key in seen or not text:
                raise ValueError("Flash edit is outside its uniquely owned cue")
            seen.add(key)
            ids[key]["text"] = text
        omissions.extend(x for x in answer["omissions"] if start <= x["start"] < x["end"] <= end and x["text"].strip())
    return {"duration": transcription["duration"], "blocks": blocks, "omissions_for_review": omissions,
            "coverage": transcription["coverage"]}


def align(media, refined, folder, aligner):
    from backend.pipeline.contextual_transcript_timing import analyse, prepare
    from backend.pipeline.transcript_proofreading_layout import proofreading_blocks

    # Earlier Flash checkpoints already have text keyed by their original cue IDs.
    # Preserve that text and group it once for a restart at timing.
    blocks = refined["blocks"] if "blocks" in refined else proofreading_blocks(refined["cues"])
    if not aligner:
        raise ValueError("--aligner-python is required for paragraph timing")
    source = folder / "paragraph-timing"
    source.mkdir(exist_ok=True)
    master = source / "master.wav"
    extract(media, master, 0, refined["duration"])
    with wave.open(str(master), "rb") as pcm:
        duration_ms = pcm.getnframes() // 16
    record = {"paragraphs": [
        {"id": f"p{i}", "start_ms": min(duration_ms, round(block["start"] * 1000)),
         "end_ms": min(duration_ms, round(block["end"] * 1000)),
         "text": "\n".join(turn["text"].replace("~~", "") for turn in block["cues"])}
        for i, block in enumerate(blocks, 1)
    ]}
    save_json(source / "full_transcript.json", record)
    save_json(source / "master.json", {"source_sha256": file_sha(media), "duration_ms": duration_ms})
    clips = source / "evidence"
    prepare({"source": source}, clips)
    environment = os.environ.copy()
    capacity = environment.get("MEDIA_BATCH_CAPACITY_ROOT")
    if capacity and "MEDIA_BATCH_CONTEXT" not in environment:
        environment["MEDIA_BATCH_CONTEXT"] = json.dumps({"capacity_root": capacity})
    subprocess.run([str(aligner), "-m", "backend.pipeline.transcript_alignment_worker",
                    "--out", str(clips), "--decode-profile", "monotonic"],
                   check=True, env=environment)
    assessed = analyse(clips, tolerance_ms=1000, detail=True, apply_coherence=False)
    rows = assessed["lectures"]["source"]["paragraphs"]
    output = []
    for block, expected, row in zip(blocks, record["paragraphs"], rows, strict=True):
        if row["id"] != expected["id"]:
            raise ValueError("Paragraph timing identity changed")
        start, end = block["start"], block["end"]
        qualified = row["status"] == "qualified"
        if qualified:
            a, b = row["start_ms"] / 1000, row["end_ms"] / 1000
            # Small playback offsets are acceptable. Only replace a range
            # when both acoustic boundaries qualify and agree on the occurrence.
            if max(abs(a - start), abs(b - end)) > 1:
                if max(abs(a - start), abs(b - end)) <= 2:
                    start, end = a, b
                else:
                    qualified = False
        output.append({**block, "start": start, "end": end, "timing_review": not qualified})
    # Retiming must also remain compatible with the actual output layout.
    conflicts = set()
    for i, (left, right) in enumerate(zip(output, output[1:])):
        if (right["start"] < left["start"] or
                left["end"] - right["start"] > max(1, blocks[i]["end"] - blocks[i + 1]["start"])):
            conflicts.update((i, i + 1))
    for i in conflicts:
        output[i] = {**blocks[i], "timing_review": True}
    return {**refined, "blocks": output}
