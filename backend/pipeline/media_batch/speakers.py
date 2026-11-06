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
    if not result["complete"]:
        raise ValueError(
            "Speaker attribution incomplete; inspect saved speaker progress"
        )
    accepted = []
    missing = []
    for path in sorted(evidence.glob("w*/candidate.json")):
        value = json.loads(path.read_text())
        accepted.extend(value["accepted"])
        missing.extend(value["missing_speech"])
    if missing:
        raise ValueError(
            "Audible speech is missing from transcript; retain speaker evidence for correction"
        )
    candidate = dict(
        schema_version="media-batch-speakers.v1",
        source_record_sha256=file_sha(out / "full_transcript.json"),
        results=accepted,
        evidence_root=str(evidence),
        review_status="unreviewed_model_candidate",
    )
    atomic(out / "speaker-candidate.json", candidate)
    return dict(
        speaker_candidate_sha256=file_sha(out / "speaker-candidate.json"),
        paragraphs=len(accepted),
    )


def label_rows(source, record, rows, mapping):
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
        texts[mapping[p["id"]]] = insert_labels(p["text"], row["turns"])
    return [{**r, "text": texts[r["index"]]} for r in rows], dict(
        source_sha256=file_sha(path),
        status="unreviewed_model_candidate",
        spoken_text_unchanged=True,
    )
