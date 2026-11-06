"""Content identities and plans. Never generates or overwrites a human transcript."""

from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import hashlib
from psycopg.types.json import Jsonb
from .store import Store, key
from .runtime import file_sha

EXTENSIONS = {
    ".wav",
    ".mp3",
    ".aac",
    ".m4a",
    ".flac",
    ".mp4",
    ".mov",
    ".m4v",
    ".mts",
    ".m2ts",
}


def probe(path):
    p = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(p.stdout)
    if not any(s["codec_type"] == "audio" for s in value["streams"]):
        raise ValueError("No audio stream")
    return value


def pcm_identity(path):
    p = subprocess.Popen(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    h = hashlib.sha256()
    size = 0
    try:
        while data := p.stdout.read(1024 * 1024):
            h.update(data)
            size += len(data)
        if p.wait() != 0 or size == 0:
            raise ValueError("Audio decode failed")
    finally:
        p.stdout.close()
        if p.poll() is None:
            p.kill()
            p.wait()
    return key(["pcm-s16le-mono-16000-v1", h.hexdigest(), size])


def plan(store, folder, config):
    folder = Path(folder).resolve(strict=True)
    artifact = Path(config["artifact_root"]).resolve()
    if artifact == folder or folder in artifact.parents:
        raise ValueError("Artifact root must be outside the scanned folder")
    files = sorted(
        p
        for p in folder.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and p.suffix.lower() in EXTENSIONS
        and not any(x.startswith(".") for x in p.relative_to(folder).parts)
    )
    config = {**config, "folder": str(folder), "scope": []}
    for p in files:
        if folder not in p.resolve().parents:
            raise ValueError("Source escaped folder")
        before = p.stat()
        if before.st_flags & 0x40000000:
            raise ValueError(
                "Source is cloud-only; finish verified copying first: "
                + str(p.relative_to(folder))
            )
        sha = file_sha(p)
        if (before.st_size, before.st_mtime_ns) != (
            p.stat().st_size,
            p.stat().st_mtime_ns,
        ):
            raise ValueError("Source changed during inventory")
        with store.connection() as c:
            asset = c.execute(
                "SELECT * FROM media_ingest.assets WHERE sha=%s", (sha,)
            ).fetchone()
        if asset:
            info = asset["info"]
            rid = asset["recording"]
            # A verified rename/move must keep queued work usable, without
            # manufacturing a new recording or rerunning completed tasks.
            with store.connection() as c:
                row = c.execute(
                    "SELECT source_sha,info FROM media_ingest.recordings WHERE id=%s FOR UPDATE",
                    (rid,),
                ).fetchone()
                if (
                    row["source_sha"] == sha
                    and not Path(row["info"]["source"]).is_file()
                ):
                    canonical = {**row["info"], "source": str(p)}
                    c.execute(
                        "UPDATE media_ingest.recordings SET info=%s WHERE id=%s",
                        (Jsonb(canonical), rid),
                    )
        else:
            pr = probe(p)
            pcm = pcm_identity(p)
            video = any(s["codec_type"] == "video" for s in pr["streams"])
            info = dict(
                name=p.stem,
                relative_path=str(p.relative_to(folder)),
                source=str(p),
                source_sha=sha,
                bytes=before.st_size,
                mtime_ns=before.st_mtime_ns,
                duration=float(pr["format"]["duration"]),
                video=video,
                pcm_sha=pcm,
                needs_resolution=p.suffix.lower() in (".mts", ".m2ts")
                or video
                and (
                    abs(float(pr["format"].get("start_time", 0))) > 0.001
                    or any(
                        abs(float(stream.get("start_time", 0))) > 0.001
                        for stream in pr["streams"]
                        if stream["codec_type"] in ("audio", "video")
                    )
                ),
            )
            if any(
                name in str(p.relative_to(folder)).lower()
                for name in ("felix liu", "andrew wang")
            ):
                info.update(
                    needs_resolution=True,
                    reason="Another named speaker: confirm batch scope before publishing",
                )
            with store.connection() as c:
                candidates = c.execute(
                    "SELECT * FROM media_ingest.recordings WHERE (info->>'duration')::float BETWEEN %s AND %s AND info->>'existing_transcript'='true' ORDER BY id",
                    (info["duration"] - 0.3, info["duration"] + 0.3),
                ).fetchall()
            alias = None
            for candidate in candidates:
                from .duplicates import compare

                other = Path(candidate["info"]["source"])
                if not other.is_file():
                    info.update(
                        needs_resolution=True,
                        reason="Legacy media unavailable for duplicate verification",
                    )
                    continue
                proof = compare(p, other)
                if proof["same"]:
                    alias = candidate
                    info["duplicate_proof"] = proof
                    break
                if proof["uncertain"]:
                    info.update(
                        needs_resolution=True,
                        reason="Partial acoustic match to existing sermon "
                        + candidate["item"],
                        duplicate_proof=proof,
                    )
            with store.connection() as c:
                c.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (pcm,)
                )
                existing = c.execute(
                    "SELECT * FROM media_ingest.recordings WHERE pcm_sha=%s", (pcm,)
                ).fetchone()
                if existing is None:
                    existing = c.execute(
                        "SELECT r.* FROM media_ingest.assets a JOIN media_ingest.recordings r ON r.id=a.recording WHERE a.info->>'pcm_sha'=%s LIMIT 1",
                        (pcm,),
                    ).fetchone()
                if existing is None and alias is not None:
                    existing = alias
                rid = existing["id"] if existing else "R-" + sha[:24]
                item = existing["item"] if existing else "INGEST-" + sha[:16]
                # A different video with the same voice needs its own visual run; don't silently skip its slides.
                if existing and video and existing["source_sha"] != sha:
                    info["needs_resolution"] = True
                    info["reason"] = (
                        "Same audio, different video: choose visual source before reuse"
                    )
                    rid = "R-" + sha[:24]
                    item = "INGEST-" + sha[:16]
                    pcm = None
                c.execute(
                    "INSERT INTO media_ingest.recordings(id,pcm_sha,source_sha,item,info) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (rid, pcm, sha, item, Jsonb(info)),
                )
                c.execute(
                    "INSERT INTO media_ingest.assets VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (sha, rid, Jsonb(info)),
                )
        config["scope"].append(
            dict(path=str(p.relative_to(folder)), sha=sha, recording=rid)
        )
    batch = store.batch(config)
    with store.connection() as c:
        for row in config["scope"]:
            r = c.execute(
                "SELECT * FROM media_ingest.recordings WHERE id=%s", (row["recording"],)
            ).fetchone()
            info = r["info"]
            binding = key(
                [
                    r["source_sha"],
                    config["profile"],
                    config.get("vocabulary_sha"),
                    config.get("stage_shas", {}),
                    {
                        name: config.get(name)
                        for name in (
                            "artifact_root",
                            "media_root",
                            "review_root",
                            "aligner_python",
                        )
                    },
                ]
            )
            payload = dict(
                info=info,
                item=r["item"],
                out=str(artifact / r["id"] / binding[:16]),
                config=config,
            )
            review_root = Path(config["review_root"])
            protected = any(
                path.is_file()
                for path in (
                    review_root / "script_review" / f"{r['item']}.json",
                    review_root / "script_published" / f"{r['item']}.json",
                    review_root / "transcript_review" / r["item"] / "context.json",
                )
            )
            if info.get("existing_transcript") or protected:
                tid = store.task(c, batch, r["id"], "existing", "all", binding, payload)
                c.execute(
                    "UPDATE media_ingest.tasks SET state='complete',result=%s WHERE id=%s",
                    (
                        Jsonb(
                            dict(
                                reused=True,
                                item=r["item"],
                                reason="Existing human/published transcript preserved",
                            )
                        ),
                        tid,
                    ),
                )
                continue
            ids = {}
            stages = [
                "prepare",
                "visuals",
                "speech",
                "final-align",
                "finish",
                "speakers",
                "media",
                "handoff",
            ]
            for stage in stages:
                deps = (
                    []
                    if stage in ("prepare", "media")
                    else [ids[stages[stages.index(stage) - 1]]]
                )
                if stage == "handoff":
                    deps = [ids["finish"], ids["speakers"], ids["media"]]
                tid = store.task(
                    c,
                    batch,
                    r["id"],
                    stage,
                    "all",
                    key(
                        [binding, stage, config.get("stage_shas", {}).get(stage), deps]
                    ),
                    payload,
                    deps,
                )
                ids[stage] = tid
                if info.get("needs_resolution"):
                    c.execute(
                        "UPDATE media_ingest.tasks SET state='needs_resolution',error=%s WHERE id=%s AND state='pending'",
                        (
                            info.get(
                                "reason",
                                "Camera segment or nonzero origin needs an ordered source manifest",
                            ),
                            tid,
                        ),
                    )
    return batch


def index_existing(store, review_root, media_root):
    """One explicit backfill; routine plans reuse the content index, not all history."""
    root = Path(review_root)
    media = Path(media_root)
    catalog = json.loads((root / "config/sermon.json").read_text())
    indexed = 0
    for row in catalog:
        item = row["item"]
        if "/" in item or "\\" in item or item in (".", ".."):
            raise ValueError("Invalid legacy item")
        script = next(
            (
                p
                for p in (
                    root / "script_review" / f"{item}.json",
                    root / "script_published" / f"{item}.json",
                    root / "script_patched" / f"{item}.json",
                    root / "script" / f"{item}.json",
                )
                if p.is_file()
            ),
            None,
        )
        for path in (media / f"{item}.mp3", media / f"{item}.mp4"):
            if not path.is_file():
                continue
            sha = file_sha(path)
            with store.connection() as c:
                if c.execute(
                    "SELECT 1 FROM media_ingest.assets WHERE sha=%s", (sha,)
                ).fetchone():
                    continue
            if script is not None:
                saved = json.loads(script.read_text())
                paragraphs = (
                    saved
                    if isinstance(saved, list)
                    else saved.get("script", saved.get("entries", []))
                )
                has_text = any(
                    isinstance(p, dict) and str(p.get("text") or "").strip()
                    for p in paragraphs
                )
                if not has_text and row.get("status") != "published":
                    script = None
            pr = probe(path)
            pcm = pcm_identity(path)
            rid = "LEGACY-" + key(item)[:24]
            info = dict(
                name=row.get("title") or item,
                source=str(path.resolve()),
                source_sha=sha,
                bytes=path.stat().st_size,
                duration=float(pr["format"]["duration"]),
                video=path.suffix == ".mp4",
                legacy_item=item,
                existing_transcript=script is not None,
                script_sha=file_sha(script) if script else None,
                needs_resolution=bool(row.get("assigned_to")) and script is None,
                reason="An existing claimed item must be resolved without overwriting its draft",
            )
            with store.connection() as c:
                # Both legacy media variants preserve the same item even if PCM differs after compression.
                old = c.execute(
                    "SELECT id FROM media_ingest.recordings WHERE pcm_sha=%s", (pcm,)
                ).fetchone()
                if old and old["id"] != rid:
                    pcm = None
                c.execute(
                    "INSERT INTO media_ingest.recordings VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (rid, pcm, sha, item, Jsonb(info)),
                )
                c.execute(
                    "INSERT INTO media_ingest.assets VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (sha, rid, Jsonb(info)),
                )
            indexed += 1
    return indexed
