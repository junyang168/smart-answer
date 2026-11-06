"""Real PostgreSQL, real FFmpeg and competing processes; no paid model calls."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
import multiprocessing as mp
import time
import uuid
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from backend.pipeline.media_batch.store import Store, Conflict
from backend.pipeline.media_batch.runtime import (
    slot,
    WaitingCapacity,
    capacity_status,
    batch_call,
    atomic,
    file_sha,
)
from backend.pipeline.media_batch.planner import plan
from backend.pipeline.media_batch.stages import media


@pytest.fixture
def store():
    dsn = os.getenv("MEDIA_BATCH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set MEDIA_BATCH_TEST_DATABASE_URL to a dedicated test database")
    s = Store(dsn)
    s.install()
    with s.connection() as c:
        c.execute(
            "TRUNCATE media_ingest.controls,media_ingest.calls,media_ingest.batch_tasks,media_ingest.tasks,media_ingest.assets,media_ingest.recordings,media_ingest.batches"
        )
    return s


def config(tmp):
    return dict(
        title="試跑",
        artifact_root=str(tmp / "artifacts"),
        media_root=str(tmp / "media"),
        review_root=str(tmp / "review"),
        capacity_root=str(tmp / "capacity"),
        aligner_python="/test/python",
        profile="test",
        pipeline_sha="test-v1",
        budget_usd=10,
        max_calls=10,
        ssd_reserve_gib=0,
    )


def audio(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            str(path),
        ],
        check=True,
    )
    return path


def test_rename_cross_folder_and_container_reuse_no_duplicate_tasks(store, tmp_path):
    a = audio(tmp_path / "first/a.wav")
    cfg = config(tmp_path)
    b1 = plan(store, a.parent, cfg)
    other = tmp_path / "second"
    other.mkdir()
    shutil.copyfile(a, other / "renamed.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(a),
            "-c:a",
            "copy",
            str(other / "same.mka"),
        ],
        check=True,
    )
    # Scan the supported byte copy; unchanged runs use exactly the same batch.
    b2 = plan(store, other, cfg)
    assert b1 != b2 and plan(store, other, cfg) == b2
    with store.connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM media_ingest.recordings").fetchone()[
                "n"
            ]
            == 1
        )
        assert (
            c.execute("SELECT count(*) AS n FROM media_ingest.tasks").fetchone()["n"]
            == 8
        )
    assert store.snapshot(b2)["summary"]["total"] == 1


def task_fixture(store, tmp_path):
    cfg = config(tmp_path)
    b = store.batch(cfg)
    with store.connection() as c:
        c.execute(
            "INSERT INTO media_ingest.recordings VALUES ('R',NULL,'sha','I',%s)",
            (Jsonb(dict(name="標題", duration=3)),),
        )
        tid = store.task(c, b, "R", "prepare", "all", "input", dict(config=cfg))
    return b, tid


def test_claim_is_exclusive_versioned_and_progress_cannot_be_forged(store, tmp_path):
    b, tid = task_fixture(store, tmp_path)
    with ThreadPoolExecutor(4) as pool:
        claims = list(pool.map(lambda i: store.claim(b, str(i)), range(4)))
    claims = [c for c in claims if c]
    assert len(claims) == 1
    t = claims[0]
    store.update(t, progress=dict(completed=1, total=3))
    assert store.snapshot(b)["summary"]["running"] == 1
    stale = {**t, "attempt": 999}
    with pytest.raises(Conflict):
        store.update(stale, state="complete")
    store.update(t, state="complete", result={"ok": True})
    assert store.snapshot(b)["summary"]["complete"] == 1


def test_pause_cas_idempotency_and_unknown_call_not_retried(store, tmp_path):
    b, tid = task_fixture(store, tmp_path)
    assert store.control(b, "pause-op", 1, "pause") == 2
    assert store.control(b, "pause-op", 1, "pause") == 2
    assert store.claim(b, "W") is None
    with pytest.raises(Conflict):
        store.control(b, "new-op", 1, "resume")
    store.control(b, "resume-op", 2, "resume")
    t = store.claim(b, "W")
    store.reserve(b, tid, "call", "gpt-6.1-sol", 1)
    store.update(t, state="failed", error="connection lost")
    store.control(b, "retry-op", 3, "retry")
    assert store.claim(b, "W") is None


def test_budget_reservation_atomic_across_workers(store, tmp_path):
    b, tid = task_fixture(store, tmp_path)
    with store.connection() as c:
        c.execute(
            "UPDATE media_ingest.batches SET config=jsonb_set(config,'{budget_usd}','1') WHERE id=%s",
            (b,),
        )

    def reserve(i):
        try:
            store.reserve(b, tid, "C" + str(i), "gpt-6.1-sol", 0.6)
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(reserve, range(4))) == 1


def hold(root, queue):
    try:
        with slot(root, "sol", 2):
            queue.put(True)
            time.sleep(0.6)
    except WaitingCapacity:
        queue.put(False)


def test_two_actual_process_slots_and_unknown_quarantine(tmp_path):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    children = [ctx.Process(target=hold, args=(str(tmp_path), q)) for _ in range(4)]
    for p in children:
        p.start()
    results = [q.get(timeout=10) for p in children]
    for p in children:
        p.join(10)
    assert sum(results) == 2
    for i in (0, 1):
        atomic(tmp_path / "sol" / f"{i}.json", dict(state="unknown"))
    assert capacity_status(tmp_path)["sol"]["unknown"] == 2
    with pytest.raises(WaitingCapacity):
        with slot(tmp_path, "sol", 2):
            pytest.fail("Unknown slot was reused")


def test_media_installs_valid_playback_and_does_not_overwrite(tmp_path):
    source = audio(tmp_path / "original.wav")
    cfg = config(tmp_path)
    out = tmp_path / "artifacts/r"
    out.mkdir(parents=True)
    payload = dict(
        info=dict(
            name="Test lecture",
            source=str(source),
            source_sha=file_sha(source),
            video=False,
            bytes=source.stat().st_size,
            duration=3,
        ),
        config=cfg,
        item="S",
        out=str(out),
    )
    result = media(payload)
    assert Path(result["path"]).suffix == ".mp3" and result["anchors"]
    assert media(payload) == result
    Path(result["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed"):
        media(payload)
    assert source.exists()


def test_router_denies_forged_or_editor_identity_before_read(
    store, tmp_path, monkeypatch
):
    from backend.api.sc_api.media_batch_router import create_media_batch_router

    root = tmp_path / "review"
    (root / "config").mkdir(parents=True)
    (root / "config/config.json").write_text(
        json.dumps(
            dict(users=[{"id": "a"}], user_roles=[dict(user="a", role="editor")])
        )
    )
    monkeypatch.setenv("TRANSCRIPT_REVIEW_ACTION_SECRET", "test-secret")
    app = FastAPI()
    app.include_router(create_media_batch_router(root, lambda: store))
    client = TestClient(app)
    assert client.get("/media_batches").status_code == 401
    import hashlib, hmac

    ts = str(int(time.time()))
    message = "\n".join(
        ("GET", "/media_batches", "a", ts, hashlib.sha256(b"").hexdigest())
    )
    headers = {
        "x-review-actor": "a",
        "x-review-time": ts,
        "x-review-signature": hmac.new(
            b"test-secret", message.encode(), hashlib.sha256
        ).hexdigest(),
    }
    assert client.get("/media_batches", headers=headers).status_code == 403
    (root / "config/config.json").write_text(
        json.dumps(dict(users=[{"id": "a"}], user_roles=[dict(user="a", role="admin")]))
    )
    response = client.get("/media_batches", headers=headers)
    assert response.status_code == 200 and response.json()["batches"] == []


def test_speaker_labels_keep_text_and_timing_separate_and_reject_source_drift(tmp_path):
    from backend.pipeline.media_batch.speakers import label_rows

    record = dict(
        duration_ms=10000, paragraphs=[dict(id="p1", text="為什麼？因為如此。")]
    )
    atomic(tmp_path / "full_transcript.json", record)
    turns = [
        dict(
            text="為什麼？",
            role="audience",
            confidence="clear",
            clip_start_s=0,
            clip_end_s=3,
            acoustic_basis="Different quiet voice",
        ),
        dict(
            text="因為如此。",
            role="professor",
            confidence="clear",
            clip_start_s=3,
            clip_end_s=6,
            acoustic_basis="Main lecturer",
        ),
    ]
    atomic(
        tmp_path / "speaker-candidate.json",
        dict(
            source_record_sha256=file_sha(tmp_path / "full_transcript.json"),
            results=[dict(paragraph_id="p1", turns=turns, limitations="")],
        ),
    )
    rows = [dict(index="1", text=record["paragraphs"][0]["text"])]
    labelled, proof = label_rows(tmp_path, record, rows, dict(p1="1"))
    assert labelled[0]["text"] == "**聽眾：**為什麼？\n\n**教授：**因為如此。"
    assert rows[0]["text"] == "為什麼？因為如此。" and proof["spoken_text_unchanged"]
    record["paragraphs"][0]["text"] = "改過了"
    atomic(tmp_path / "full_transcript.json", record)
    with pytest.raises(ValueError, match="changed"):
        label_rows(tmp_path, record, rows, dict(p1="1"))


def test_media_resume_after_install_before_receipt_keeps_same_bytes(tmp_path):
    source = audio(tmp_path / "source.wav")
    cfg = config(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    payload = dict(
        info=dict(
            name="Test",
            source=str(source),
            source_sha=file_sha(source),
            video=False,
            bytes=source.stat().st_size,
            duration=3,
        ),
        config=cfg,
        item="S",
        out=str(out),
    )
    first = media(payload)
    (out / "media-receipt.json").unlink()
    assert media(payload) == first


def test_paging_does_not_change_batch_summary(store, tmp_path):
    b, _ = task_fixture(store, tmp_path)
    assert store.snapshot(b, offset=1, limit=1)["records"] == []
    assert store.snapshot(b, offset=1, limit=1)["summary"]["total"] == 1


def test_mmap_audio_matches_source_and_uses_correct_wave_data_offset(
    tmp_path, monkeypatch
):
    from backend.pipeline.transcript_audio_pilot import read_audio
    import numpy as np

    path = audio(tmp_path / "audio.wav")
    # Normalize to the exact rate required by this module.
    normalized = tmp_path / "normalized.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(normalized),
        ],
        check=True,
    )
    original = read_audio(normalized)
    monkeypatch.setenv("MEDIA_BATCH_CONTEXT", "{}")
    mapped = read_audio(normalized)
    assert isinstance(mapped, np.memmap) and np.array_equal(mapped, original)


def test_full_audio_lossy_duplicate_reuses_existing_item_without_asr(store, tmp_path):
    from backend.pipeline.media_batch.planner import index_existing

    root = tmp_path / "old"
    media_root = tmp_path / "old-media"
    media_root.mkdir()
    original = audio(tmp_path / "new" / "recording.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(original),
            "-b:a",
            "96k",
            str(media_root / "OLD.mp3"),
        ],
        check=True,
    )
    atomic(root / "config/sermon.json", [dict(item="OLD", title="Existing")])
    atomic(root / "script_review/OLD.json", [dict(index="1", text="人工稿")])
    assert index_existing(store, root, media_root) == 1
    batch = plan(store, original.parent, config(tmp_path))
    snap = store.snapshot(batch)
    assert snap["summary"]["reused"] == 1 and snap["records"][0]["item"] == "OLD"
    assert snap["records"][0]["tasks"][0]["result"]["reused"]


def test_mp3_encoder_origin_has_explicit_presentation_mapping(tmp_path, monkeypatch):
    from backend.pipeline.full_video_transcript import prepare

    original = audio(tmp_path / "original.wav")
    mp3 = tmp_path / "recording.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(original), str(mp3)], check=True)
    monkeypatch.setenv("MEDIA_BATCH_CONTEXT", "{}")
    result = prepare(mp3, tmp_path / "out")
    assert result["source_clock_mapping"]["presentation_origin_ms"] == 0
    assert result["duration_ms"] == 3000
