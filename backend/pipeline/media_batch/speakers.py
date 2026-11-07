"""Speaker attribution stays a candidate; all spoken characters and timing remain unchanged."""

from pathlib import Path
import json
from .runtime import atomic, file_sha


def generate(out):
    from backend.pipeline.transcript_speaker_batch import run

    out = Path(out)
    record = json.loads((out / "full_transcript.json").read_text())
    snapshot = out / "speaker-snapshot.json"
    rows = [dict(index=p["id"], text=p["text"]) for p in record["paragraphs"]]
    atomic(
        snapshot,
        dict(
            script=rows,
            context=dict(
                revision=0,
                reviews={},
                paragraph_bindings={
                    p["id"]: dict(source_paragraph_id=p["id"])
                    for p in record["paragraphs"]
                },
            ),
        ),
    )
    evidence = out.with_name(out.name + "-speakers")
    result = run(out, snapshot, evidence, workers=1, thinking_level="LOW")
    if any(w["status"] != "complete" for w in result["windows"]):
        raise ValueError(
            "Speaker attribution incomplete; inspect saved speaker progress"
        )
    accepted, notices = assemble(record, evidence)
    candidate = dict(
        schema_version="media-batch-speakers.v1",
        source_record_sha256=file_sha(out / "full_transcript.json"),
        results=accepted,
        evidence_root=str(evidence),
        review_status="unreviewed_model_candidate",
        notices=notices,
        assembly_policy="preserve-source-and-disclose.v1",
    )
    atomic(out / "speaker-candidate.json", candidate)
    return dict(
        speaker_candidate_sha256=file_sha(out / "speaker-candidate.json"),
        paragraphs=len(accepted),
        review_notices=len(notices),
    )


def assemble(record, evidence):
    """Keep uncertain attribution and gap reports visible without editing speech.

    A missing-speech report is a model hypothesis, including deliberately excluded
    setup and overlapping context. It is never an instruction to insert words.
    Unusable turn partitions become unknown, never an inferred professor identity.
    Failed/unknown requests and missing paragraphs still prevent handoff.
    """
    import math

    plan = json.loads((evidence / "plan.json").read_text())
    accepted, notices = [], []
    paragraphs = {p["id"]: p for p in record["paragraphs"]}
    for window in plan["windows"]:
        path = evidence / window["id"] / "candidate.json"
        value = json.loads(path.read_text())
        if value["window_id"] != window["id"]:
            raise ValueError("Speaker window changed")
        valid = {p["paragraph_id"]: p for p in value["accepted"]}
        rejected = {p["paragraph_id"]: p for p in value["rejected"]}
        owned = [p["id"] for p in window["paragraphs"]]
        if (
            set(valid) & set(rejected)
            or set(valid) | set(rejected) != set(owned)
            or len(valid) + len(rejected)
            != len(value["accepted"]) + len(value["rejected"])
        ):
            raise ValueError("Speaker paragraph coverage differs from source")
        lo, hi = window["clip_start_ms"], window["clip_end_ms"]
        for pid in owned:
            p = paragraphs[pid]
            if pid in valid:
                accepted.append(valid[pid])
            else:
                reason = rejected[pid]["reason"]
                accepted.append(
                    dict(
                        paragraph_id=pid,
                        limitations=reason,
                        turns=[
                            dict(
                                text=p["text"],
                                role="unknown",
                                confidence="uncertain",
                                clip_start_s=(p["start_ms"] - lo) / 1000,
                                clip_end_s=(p["end_ms"] - lo) / 1000,
                                acoustic_basis="講員候選未通過文字保全檢查；保留原文，不推測講員。",
                            )
                        ],
                    )
                )
                notices.append(
                    dict(
                        kind="speaker_uncertain",
                        paragraph_id=pid,
                        start_ms=p["start_ms"],
                        end_ms=p["end_ms"],
                        audio_quote="",
                        reason=reason,
                        evidence_sha256=file_sha(path),
                        status="unreviewed_model_candidate",
                    )
                )
        for gap in value["missing_speech"]:
            start, end = gap["clip_start_s"], gap["clip_end_s"]
            valid_time = (
                all(type(n) in (int, float) and math.isfinite(n) for n in (start, end))
                and 0 <= start < end <= (hi - lo) / 1000
            )
            absolute_start, absolute_end = (
                (lo + round(start * 1000), lo + round(end * 1000))
                if valid_time
                else (lo, hi)
            )
            association_start = (
                lo + round(start * 1000)
                if type(start) in (int, float)
                and math.isfinite(start)
                and 0 <= start < (hi - lo) / 1000
                else absolute_start
            )
            association_end = absolute_end if valid_time else association_start
            nearest = min(
                paragraphs.values(),
                key=lambda p: max(
                    p["start_ms"] - association_end, association_start - p["end_ms"], 0
                ),
            )
            notices.append(
                dict(
                    kind="possible_missing_speech",
                    paragraph_id=nearest["id"],
                    start_ms=absolute_start,
                    end_ms=absolute_end,
                    audio_quote=gap["audio_quote"],
                    reason=gap["reason"],
                    evidence_sha256=file_sha(path),
                    status="unreviewed_model_candidate",
                    timing_precision=(
                        "model_estimate" if valid_time else "context_window_only"
                    ),
                    reported_clip_range_s=[start, end],
                )
            )
    if [p["paragraph_id"] for p in accepted] != list(paragraphs):
        raise ValueError("Speaker candidate must cover every paragraph once")
    return accepted, notices


def label_rows(source, record, rows, mapping, *, hidden_insertions=None):
    path = Path(source) / "speaker-candidate.json"
    if not path.exists():
        return rows, None
    candidate = json.loads(path.read_text())
    if candidate["source_record_sha256"] != file_sha(
        Path(source) / "full_transcript.json"
    ):
        raise ValueError("Speaker candidate source changed")
    from backend.pipeline.transcript_speaker_pilot import validate
    from backend.pipeline.transcript_speaker_batch import insert_labels

    by_id = {p["id"]: p for p in record["paragraphs"]}
    results = candidate["results"]
    if [p["paragraph_id"] for p in results] != list(by_id):
        raise ValueError("Speaker candidate must cover every paragraph once")
    texts = {}
    for row in results:
        p = by_id[row["paragraph_id"]]
        # Speaker clip times were checked in their original window at generation. Re-check exact text and roles here.
        validate(
            {k: v for k, v in row.items() if k != "paragraph_id"},
            p["text"],
            record["duration_ms"] / 1000,
        )
        if "".join(t["text"] for t in row["turns"]) != p["text"]:
            raise ValueError("Speaker candidate changes spoken text")
        if any(
            t["role"] not in ("professor", "audience", "unknown") for t in row["turns"]
        ):
            raise ValueError("Invalid speaker role")
        turns = row["turns"]
        if hidden_insertions:
            from backend.pipeline.transcript_editorial_soft_deletions import (
                turns_with_hidden_spans,
            )

            turns = turns_with_hidden_spans(
                p["text"], turns, hidden_insertions.get(p["id"], [])
            )
        marked = "".join(t["text"] for t in turns)
        texts[mapping[p["id"]]] = insert_labels(marked, turns)
    return [{**r, "text": texts[r["index"]]} for r in rows], dict(
        source_sha256=file_sha(path),
        status="unreviewed_model_candidate",
        spoken_text_unchanged=True,
        notices=[
            {**n, "paragraph_id": mapping[n["paragraph_id"]]}
            for n in candidate.get("notices", [])
        ],
    )
