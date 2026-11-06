"""Adapters invoke the new transcription module without changing its content policy."""

from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import fcntl
from .runtime import atomic, file_sha, progress, slot


def media(payload):
    info = payload["info"]
    source = Path(info["source"])
    root = Path(payload["config"]["media_root"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    suffix = ".mp4" if info["video"] else ".mp3"
    target = root / (payload["item"] + suffix)
    receipt = Path(payload["out"]) / "media-receipt.json"
    if receipt.exists():
        prior = json.loads(receipt.read_text())
        if Path(prior["path"]) != target or file_sha(target) != prior["sha256"]:
            raise ValueError("Published media changed")
        register_catalog(payload)
        return prior
    pending = Path(payload["out"]) / "media-prepared.json"
    if pending.exists():
        prepared = json.loads(pending.read_text())
        if (
            prepared["path"] != str(target)
            or prepared["source_sha256"] != info["source_sha"]
        ):
            raise ValueError("Prepared media binding changed")
        if target.exists():
            if file_sha(target) != prepared["sha256"]:
                raise ValueError("Installed media differs from prepared bytes")
            atomic(receipt, prepared)
            register_catalog(payload)
            return prepared
    if target.exists():
        raise ValueError("Unregistered target exists; refusing overwrite")
    if (
        shutil.disk_usage(root).free
        < payload["config"]["ssd_reserve_gib"] * 2**30 + info["bytes"]
    ):
        raise ValueError("Not enough reserved media disk space")
    tmp = root / ("." + target.stem + ".partial" + suffix)
    with slot(payload["config"]["capacity_root"], "transcode", 1):
        if not info["video"] and source.suffix.lower() == ".mp3":
            try:
                os.link(source, tmp)
            except FileExistsError:
                if file_sha(tmp) != info["source_sha"]:
                    raise ValueError("Existing temporary audio differs")
            except OSError as error:
                import errno

                if error.errno != errno.EXDEV:
                    raise
                shutil.copyfile(source, tmp)
        else:
            command = [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
            ]
            if info["video"]:
                command += [
                    "-map",
                    "0:v:0",
                    "-vf",
                    "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v",
                    "libx264",
                    "-crf",
                    "23",
                    "-preset",
                    "medium",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    "-movflags",
                    "+faststart",
                ]
            else:
                command += ["-vn", "-c:a", "libmp3lame", "-b:a", "96k"]
            process = subprocess.Popen(
                command + ["-progress", "pipe:1", "-nostats", str(tmp)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for line in process.stdout:
                    if line.startswith("out_time_us="):
                        progress(
                            "media",
                            min(float(line.split("=")[1]) / 1e6, info["duration"]),
                            info["duration"],
                            "seconds",
                        )
                error = process.stderr.read()
                if process.wait():
                    raise ValueError("FFmpeg encode failed: " + error[-1000:])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(tmp), "-f", "null", "-"],
            check=True,
            capture_output=True,
        )
        from .planner import probe

        duration = float(probe(tmp)["format"]["duration"])
        if abs(duration - info["duration"]) > 0.3:
            raise ValueError("Playback duration differs from source")
        # Verify acoustic origin and sampled anchors, including AAC/MP3 encoder padding.
        from .planner import pcm_identity

        # Full PCM equality cannot be required for lossy encodings. Check lag/correlation at anchors.
        import numpy as np

        anchors = [0, max(0, info["duration"] / 2 - 5), max(0, info["duration"] - 12)]
        checks = []
        for start in anchors:
            arrays = []
            for path in (source, tmp):
                raw = subprocess.check_output(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-ss",
                        str(start),
                        "-i",
                        str(path),
                        "-t",
                        "8",
                        "-ac",
                        "1",
                        "-ar",
                        "8000",
                        "-f",
                        "f32le",
                        "-",
                    ]
                )
                arrays.append(np.frombuffer(raw, dtype="<f4"))
            n = min(map(len, arrays))
            a, b = (x[:n] for x in arrays)
            if n < 800:
                raise ValueError("Media anchor too short")
            if np.std(a) < 1e-5:
                checks.append(dict(at=start, silent=True))
                continue
            length = 1 << (2 * n - 1).bit_length()
            corr = np.fft.irfft(
                np.fft.rfft(b, length) * np.conj(np.fft.rfft(a, length)), length
            )
            lags = np.arange(-1600, 1601)
            values = corr[lags % length]
            k = np.argmax(values)
            lag = int(lags[k])
            score = float(values[k] / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
            if abs(lag) > 400 or score < 0.65:
                raise ValueError("Playback acoustic clock mismatch")
            checks.append(dict(at=start, lag_ms=lag / 8, correlation=score))
        if not any(not c.get("silent") for c in checks):
            raise ValueError("No audible media anchor")
        with tmp.open("rb") as f:
            os.fsync(f.fileno())
        prepared = dict(
            path=str(target),
            sha256=file_sha(tmp),
            source_sha256=info["source_sha"],
            mime="video/mp4" if info["video"] else "audio/mpeg",
            duration=duration,
            anchors=checks,
        )
        atomic(pending, prepared)
        os.link(tmp, target)
        tmp.unlink()  # link is atomic and refuses to clobber a target
    result = dict(
        path=str(target),
        sha256=file_sha(target),
        source_sha256=info["source_sha"],
        mime="video/mp4" if info["video"] else "audio/mpeg",
        duration=duration,
        anchors=checks,
    )
    atomic(receipt, result)
    register_catalog(payload)
    return result


def register_catalog(payload):
    from backend.api.sc_api.transcript_review import atomic as save, read

    root = Path(payload["config"]["review_root"])
    item = payload["item"]
    root.joinpath("config").mkdir(parents=True, exist_ok=True)
    # Same catalog lock used by the existing review writer, plus per-item ReviewStore lock.
    catalog = root / "config/sermon.json"
    with (root / "config/.sermon-review.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        rows = read(catalog, [])
        if not any(r["item"] == item for r in rows):
            rows.append(
                dict(
                    item=item,
                    title=payload["info"]["name"],
                    type="video" if payload["info"]["video"] else "audio",
                    status="ready",
                    assigned_to=None,
                    media_ingest_source_sha256=payload["info"]["source_sha"],
                )
            )
            save(catalog, rows)


def handoff(payload):
    from backend.api.sc_api.transcript_review import ReviewStore, atomic as save, read
    from backend.pipeline.video_transcript_handoff import export_handoff

    out = Path(payload["out"])
    root = Path(payload["config"]["review_root"])
    item = payload["item"]
    result = export_handoff(out)
    register_catalog(payload)
    store = ReviewStore(root)
    store.register_candidate(item, out, expected_script_sha256=None)
    result.update(
        item=item,
        editor_href="/admin/surmons/" + item,
        source_admission="awaiting_human_publication",
    )
    atomic(out / "review-receipt.json", result)
    return result


def execute(stage, payload):
    out = Path(payload["out"])
    source = Path(payload["info"]["source"])
    cfg = payload["config"]
    from .runtime import ensure_storage

    ensure_storage(cfg)
    if file_sha(source) != payload["info"]["source_sha"]:
        raise ValueError("Source drift before stage")
    root = Path(cfg["review_root"])
    item = payload["item"]
    if stage not in ("media", "handoff") and any(
        p.is_file()
        for p in (
            root / "script_review" / f"{item}.json",
            root / "script_published" / f"{item}.json",
            root / "transcript_review" / item / "context.json",
        )
    ):
        raise ValueError(
            "Existing editable or published transcript is protected; use the migration workflow"
        )
    if stage == "media":
        return media(payload)
    if stage == "handoff":
        return handoff(payload)
    if stage == "speakers":
        from .speakers import generate

        return generate(out)
    for relative in (
        "full_transcript.json",
        "reading-layout/layout.json",
        "speaker-candidate.json",
    ):
        path = out / relative
        if path.is_file():
            archived = out / "candidate-history" / file_sha(path) / Path(relative).name
            archived.parent.mkdir(parents=True, exist_ok=True)
            if not archived.exists():
                os.link(path, archived)
    from backend.pipeline import full_video_transcript as full

    aligner = Path(cfg["aligner_python"])
    if stage == "prepare":
        result = full.prepare(source, out)
        if cfg.get("vocabulary"):
            full.configure_vocabulary(out, Path(cfg["vocabulary"]))
        return dict(
            master_sha=file_sha(out / "master.json"), windows=len(result["windows"])
        )
    if stage == "visuals":
        if not payload["info"]["video"]:
            full.prepare_audio_visuals(out)
        else:
            manifest = full.slides.sample_video(
                source,
                out / "slides",
                start_ms=0,
                end_ms=full.read(out / "master.json")["duration_ms"],
            )
            if not (out / "slides/candidates.json").exists():
                full.slides.detect(out / "slides", manifest)
            full.classify_slides(out)
            full.extract_slides(out, None)
        return dict(report_sha=file_sha(out / "slides/report.json"))
    if stage == "speech":
        full.process_windows(out, aligner, None)
    elif stage == "final-align":
        full.prepare_final_alignment(out)
        for w in full.read(out / "master.json")["windows"]:
            folder = out / f'windows/{w["id"]}'
            full.align(
                folder / full.read(folder / "final_alignment_ref.json")["folder"],
                aligner,
            )
        recovered = full.review_recovered_paragraphs(out)
        if recovered:
            full.prepare_final_alignment(out, set(recovered))
            for wid in recovered:
                folder = out / f"windows/{wid}"
                full.align(
                    folder / full.read(folder / "final_alignment_ref.json")["folder"],
                    aligner,
                )
        full.refine_backward_predictions(out, aligner)
    elif stage == "finish":
        if payload["info"]["video"]:
            full.save(
                out / "frame_pts_audit.json",
                full.audit_frame_pts(source, full.read(out / "slides/manifest.json")),
            )
        full.finish(out, source, aligner)
        from backend.pipeline.slide_reading_layout import infer

        infer(full.read(out / "full_transcript.json"), out / "reading-layout")
    else:
        raise ValueError("Unknown stage")
    return dict(stage=stage, completed=True)
