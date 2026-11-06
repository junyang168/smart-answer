"""Real PostgreSQL, real FFmpeg and competing processes; no paid model calls."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
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


def test_isolated_alignment_entrypoint_starts_without_loading_model():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.pipeline.transcript_alignment_worker",
            "--help",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--decode-profile" in result.stdout


@pytest.fixture
def store():
    dsn = os.getenv("MEDIA_BATCH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set MEDIA_BATCH_TEST_DATABASE_URL to a dedicated test database")
    s = Store(dsn)
    s.install()
    with s.connection() as c:
        c.execute(
            "TRUNCATE media_ingest.batch_files,media_ingest.disk_reservations,media_ingest.controls,media_ingest.calls,media_ingest.batch_tasks,media_ingest.tasks,media_ingest.assets,media_ingest.recordings,media_ingest.batches"
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
        internal_reserve_gib=0,
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


def test_media_only_publication_hides_draft_without_creating_scripts(tmp_path):
    from backend.pipeline.media_batch.publication import media_only_payload

    (tmp_path / "config").mkdir()
    catalog = tmp_path / "config/sermon.json"
    catalog.write_text(
        json.dumps(
            [
                dict(
                    item="new",
                    title="講道",
                    type="audio",
                    status="ready",
                    media_ingest_source_sha256="abc",
                    private_note="not public",
                ),
                dict(item="legacy", title="舊稿"),
            ]
        )
    )
    result = media_only_payload(tmp_path, "new")
    assert result["script"] == []
    assert result["transcript_status"] == "pending_review"
    assert result["metadata"]["type"] == "audio"
    assert "private_note" not in result["metadata"]
    assert not (tmp_path / "script").exists()
    assert media_only_payload(tmp_path, "legacy") is None
    assert media_only_payload(tmp_path, "absent") is None
    (tmp_path / "script_review").mkdir()
    (tmp_path / "script_review/new.json").write_text('[{"text":"private draft"}]')
    assert media_only_payload(tmp_path, "new")["script"] == []
    (tmp_path / "script_published").mkdir()
    (tmp_path / "script_published/new.json").write_text('{"script":[]}')
    assert media_only_payload(tmp_path, "new") is None


def test_pipeline_version_and_destination_do_not_reuse_stale_tasks(store, tmp_path):
    source = audio(tmp_path / "input/a.wav")
    cfg = config(tmp_path)
    first = plan(store, source.parent, cfg)
    revised = plan(store, source.parent, {**cfg, "stage_shas": {"speech": "new"}})
    elsewhere = plan(
        store, source.parent, {**cfg, "review_root": str(tmp_path / "other")}
    )
    with store.connection() as c:
        task_sets = [
            {
                r["task"]
                for r in c.execute(
                    "SELECT task FROM media_ingest.batch_tasks WHERE batch=%s", (batch,)
                )
            }
            for batch in (first, revised, elsewhere)
        ]
        assert not (task_sets[0] & task_sets[1])
        assert not (task_sets[0] & task_sets[2])
        assert (
            c.execute("SELECT count(*) AS n FROM media_ingest.recordings").fetchone()[
                "n"
            ]
            == 1
        )


def test_renamed_source_resumes_queued_work_without_new_tasks(store, tmp_path):
    from backend.pipeline.media_batch.stages import resolve_source

    source = audio(tmp_path / "input/original.wav")
    cfg = config(tmp_path)
    batch = plan(store, source.parent, cfg)
    task = store.claim(batch, "before-rename")
    renamed = source.with_name("renamed.wav")
    source.rename(renamed)
    next_batch = plan(store, renamed.parent, cfg)
    assert next_batch != batch  # Inventory path changed, content task identity did not.
    assert resolve_source(task["payload"], store=store) == renamed
    with store.connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM media_ingest.tasks").fetchone()["n"]
            == 8
        )


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
    assert client.get("/media_batches/any/files").status_code == 401
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


def test_local_budget_rejection_does_not_quarantine_or_reserve_remote_call(
    store, tmp_path, monkeypatch
):
    from backend.pipeline.transcript_audio_pilot import saved_call

    b, tid = task_fixture(store, tmp_path)
    ctx = dict(
        batch=b,
        task=tid,
        capacity_root=str(tmp_path / "capacity"),
        progress_path=str(tmp_path / "progress.json"),
    )
    monkeypatch.setenv("MEDIA_BATCH_CONTEXT", json.dumps(ctx))
    monkeypatch.setenv("MEDIA_BATCH_DATABASE_URL", store.dsn)
    with pytest.raises(ValueError, match="reservation limit"):
        saved_call(
            tmp_path / "calls",
            dict(stage="test", model="gpt-6.1-sol"),
            1,
            lambda: pytest.fail("Must not send"),
            max_calls=0,
        )
    with store.connection() as c:
        assert (
            c.execute("SELECT count(*) n FROM media_ingest.calls").fetchone()["n"] == 0
        )
    assert capacity_status(tmp_path / "capacity")["sol"]["unknown"] == 0


def test_speech_windows_are_separate_resumable_work_and_shared_dependencies(
    store, tmp_path, monkeypatch
):
    from backend.pipeline.media_batch.window_tasks import prepare

    b, initial = task_fixture(store, tmp_path)
    first = store.claim(b, "worker")
    store.update(first, state="complete")
    payload = dict(out=str(tmp_path / "out"), config=config(tmp_path))
    atomic(tmp_path / "out/master.json", dict(windows=[dict(id="w00")]))
    with store.connection() as c:
        parent_id = store.task(
            c, b, "R", "speech", "all", "binding", payload, [initial]
        )
    parent = store.claim(b, "worker")
    monkeypatch.setenv("MEDIA_BATCH_DATABASE_URL", store.dsn)
    monkeypatch.setenv(
        "MEDIA_BATCH_CONTEXT",
        json.dumps(
            dict(task=parent_id, batch=b, unit="all", attempt=parent["attempt"])
        ),
    )
    with pytest.raises(WaitingCapacity):
        prepare(payload)
    store.update(parent, state="pending")
    window = store.claim(b, "worker")
    assert window["unit"] == "w00" and window["id"] != parent_id
    store.update(window, state="complete")
    with store.connection() as c:
        c.execute(
            "UPDATE media_ingest.tasks SET not_before=now() WHERE id=%s", (parent_id,)
        )
    parent = store.claim(b, "worker")
    monkeypatch.setenv(
        "MEDIA_BATCH_CONTEXT",
        json.dumps(
            dict(task=parent_id, batch=b, unit="all", attempt=parent["attempt"])
        ),
    )
    assert prepare(payload) is None
    store.update(parent, state="complete")
    other = store.batch({**config(tmp_path), "title": "Another batch"})
    with store.connection() as c:
        store.task(c, other, "R", "speech", "all", "binding", payload, [initial])
    assert len(store.snapshot(other)["records"][0]["tasks"]) == 3


def test_disk_reservations_are_atomic_across_competing_workers(
    store, tmp_path, monkeypatch
):
    from backend.pipeline.media_batch import disk
    from types import SimpleNamespace

    cfg = config(tmp_path)
    batch = store.batch(cfg)
    with store.connection() as c:
        for n in range(4):
            rid = "space-" + str(n)
            c.execute(
                "INSERT INTO media_ingest.recordings(id,source_sha,item,info) VALUES (%s,%s,%s,'{}')",
                (rid, rid, rid),
            )
            store.task(
                c,
                batch,
                rid,
                "prepare",
                "all",
                rid,
                dict(config=cfg, out=str(tmp_path / rid)),
            )
    tasks = [store.claim(batch, str(n)) for n in range(4)]
    monkeypatch.setattr(
        disk.shutil, "disk_usage", lambda path: SimpleNamespace(free=1000)
    )
    request = [
        dict(
            device=str(tmp_path.stat().st_dev),
            path=str(tmp_path),
            bytes=600,
            floor_bytes=0,
            limit_bytes=10000,
        )
    ]

    def acquire(task):
        try:
            disk.reserve(store, task, request)
            return task
        except WaitingCapacity:
            return None

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(acquire, tasks))
    winners = [r for r in results if r]
    assert len(winners) == 1
    disk.reserve(
        store, winners[0], request
    )  # An identical retry does not reserve twice.
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT sum(bytes) AS n FROM media_ingest.disk_reservations"
            ).fetchone()["n"]
            == 600
        )
    disk.release(store, winners[0])
    loser = next(t for t, r in zip(tasks, results) if r is None)
    disk.reserve(store, loser, request)
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM media_ingest.disk_reservations"
            ).fetchone()["n"]
            == 1
        )


def test_disk_reservation_rollback_and_working_limit(store, tmp_path, monkeypatch):
    from backend.pipeline.media_batch import disk
    from types import SimpleNamespace

    b, tid = task_fixture(store, tmp_path)
    t = store.claim(b, "worker")
    monkeypatch.setattr(
        disk.shutil, "disk_usage", lambda path: SimpleNamespace(free=10000)
    )
    # Two devices must be admitted together; rejection leaves neither reserved.
    requests = [
        dict(device="a", path=str(tmp_path), bytes=50, floor_bytes=0, limit_bytes=100),
        dict(device="b", path=str(tmp_path), bytes=101, floor_bytes=0, limit_bytes=100),
    ]
    with pytest.raises(WaitingCapacity):
        disk.reserve(store, t, requests)
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM media_ingest.disk_reservations"
            ).fetchone()["n"]
            == 0
        )
    with pytest.raises(Conflict):
        disk.reserve(store, {**t, "attempt": 999}, requests)


def test_disk_reservation_recovery_waits_for_descendant_lease(store, tmp_path):
    from backend.pipeline.media_batch import disk
    import fcntl

    b, tid = task_fixture(store, tmp_path)
    t = store.claim(b, "worker")
    out = tmp_path / "out"
    t["payload"]["out"] = str(out)
    with store.connection() as c:
        c.execute(
            "UPDATE media_ingest.tasks SET payload=%s,heartbeat=now()-interval '2 minutes' WHERE id=%s",
            (Jsonb(t["payload"]), tid),
        )
    request = [
        dict(
            device=str(tmp_path.stat().st_dev),
            path=str(tmp_path),
            bytes=10,
            floor_bytes=0,
            limit_bytes=100,
        )
    ]
    disk.reserve(store, t, request)
    lease = out / "leases" / tid
    lease.parent.mkdir(parents=True)
    with lease.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        assert store.reconcile(b) == []
        with store.connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM media_ingest.disk_reservations"
                ).fetchone()["n"]
                == 1
            )
    assert store.reconcile(b)[0]["state"] == "interrupted"
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM media_ingest.disk_reservations"
            ).fetchone()["n"]
            == 0
        )
    # Completion can precede release. Its orphaned reservation is recovered too.
    with store.connection() as c:
        c.execute("UPDATE media_ingest.tasks SET state='running' WHERE id=%s", (tid,))
    disk.reserve(store, t, request)
    store.update(t, state="complete")
    store.reconcile(b)
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM media_ingest.disk_reservations"
            ).fetchone()["n"]
            == 0
        )


def test_worker_waits_before_starting_child_when_disk_budget_is_too_small(
    store, tmp_path, monkeypatch
):
    from backend.pipeline.media_batch import worker

    source = audio(tmp_path / "source.wav")
    cfg = {**config(tmp_path), "working_set_gib": 0.000001}
    b = store.batch(cfg)
    payload = dict(
        config=cfg,
        out=str(tmp_path / "work"),
        item="disk-wait",
        info=dict(
            source=str(source), duration=3, bytes=source.stat().st_size, video=False
        ),
    )
    with store.connection() as c:
        c.execute(
            "INSERT INTO media_ingest.recordings(id,source_sha,item,info) VALUES ('disk-wait','sha','disk-wait','{}')"
        )
        tid = store.task(c, b, "disk-wait", "prepare", "all", "input", payload)
    task = store.claim(b, "worker")

    def forbidden(*args, **kwargs):
        pytest.fail("Worker spawned before disk admission")

    monkeypatch.setattr(worker.subprocess, "Popen", forbidden)
    assert worker.run_task(store, b, task) == "pending"
    with store.connection() as c:
        row = c.execute(
            "SELECT state,error FROM media_ingest.tasks WHERE id=%s", (tid,)
        ).fetchone()
        assert row["state"] == "pending" and "寫入預算" in row["error"]
        assert (
            c.execute(
                "SELECT count(*) AS n FROM media_ingest.disk_reservations"
            ).fetchone()["n"]
            == 0
        )


def test_operation_inventory_lists_missing_and_copied_files_without_model_jobs(
    store, tmp_path
):
    from backend.pipeline.media_batch.inventory import create, refresh
    from backend.pipeline.media_batch.files import snapshot

    dst = tmp_path / "copied"
    dst.mkdir()
    paths = [
        "a.wav",
        "b.wav",
        "camera.MTS",
        "Andrew Wang Prayer.wav",
        "notes.txt",
        "not-downloaded.wav",
    ]
    for name in paths[:-1]:
        (dst / name).write_bytes(b"source-" + name.encode())
    (dst / "b.wav").write_bytes((dst / "a.wav").read_bytes())
    manifest = tmp_path / "inventory.json"
    receipts = tmp_path / "receipts.jsonl"
    entries = [
        dict(
            path=name, bytes=(dst / name).stat().st_size if (dst / name).exists() else 3
        )
        for name in paths
    ]
    manifest.write_text(json.dumps(dict(destination=str(dst), files=entries)))
    lines = [
        dict(path=name, bytes=(dst / name).stat().st_size, sha256=file_sha(dst / name))
        for name in paths[:-1]
    ]
    receipts.write_text("".join(json.dumps(row) + "\n" for row in lines) + '{"path":')
    batch = create(store, manifest, receipts, config(tmp_path))
    assert create(store, manifest, receipts, config(tmp_path)) == batch
    data = snapshot(store, batch, limit=100)
    assert data["total"] == 6 and data["counts"] == dict(
        ready_to_plan=1,
        duplicate=1,
        needs_assembly=1,
        needs_scope_review=1,
        excluded=1,
        waiting_download=1,
    )
    assert snapshot(store, batch, state="actionable")["filtered_total"] == 4
    (dst / "not-downloaded.wav").write_bytes(b"abc")
    receipts.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in lines
            + [
                dict(
                    path="not-downloaded.wav",
                    bytes=3,
                    sha256=file_sha(dst / "not-downloaded.wav"),
                )
            ]
        )
    )
    refresh(store, batch)
    assert snapshot(store, batch, state="waiting_download")["filtered_total"] == 0
    with store.connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM media_ingest.tasks").fetchone()["n"]
            == 0
        )
        assert (
            c.execute("SELECT count(*) AS n FROM media_ingest.calls").fetchone()["n"]
            == 0
        )


def test_operation_file_search_filters_entire_folder_before_pagination(store, tmp_path):
    from backend.pipeline.media_batch.files import snapshot

    b = store.batch(config(tmp_path))
    with store.connection() as c:
        for n in range(65):
            c.execute(
                "INSERT INTO media_ingest.batch_files(batch,path,bytes,state) VALUES (%s,%s,100,%s)",
                (
                    b,
                    f"folder/{n:03d}-講道.wav",
                    "waiting_download" if n == 64 else "ready_to_plan",
                ),
            )
    first = snapshot(store, b, limit=30)
    assert first["total"] == 65 and len(first["files"]) == 30
    found = snapshot(store, b, limit=30, search="064-講道")
    assert (
        found["filtered_total"] == 1
        and found["files"][0]["path"] == "folder/064-講道.wav"
    )
    assert snapshot(store, b, state="waiting_download")["filtered_total"] == 1
    assert snapshot(store, b, search="%")["filtered_total"] == 0
    assert snapshot(store, b, offset=60, limit=30)["filtered_total"] == 65


def test_operation_file_tracks_its_actual_transcription_task(store, tmp_path):
    from backend.pipeline.media_batch.files import snapshot

    p = audio(tmp_path / "input/sermon.wav")
    batch = plan(store, p.parent, config(tmp_path))
    assert snapshot(store, batch)["files"][0]["state"] == "pending"
    assert snapshot(store, batch)["files"][0]["job_batch"] == batch
    task = store.claim(batch, "worker")
    assert snapshot(store, batch)["files"][0]["state"] == "running"
    store.update(task, state="failed", error="Known test failure")
    failed = snapshot(store, batch)["files"][0]
    assert failed["state"] == "failed" and failed["reason"] == "Known test failure"
    with store.connection() as c:
        c.execute(
            "UPDATE media_ingest.tasks SET state='complete',error=NULL WHERE recording=%s",
            (task["recording"],),
        )
    assert snapshot(store, batch)["files"][0]["state"] == "complete"


def test_existing_manuscript_names_enrich_inventory_and_block_unverified_retranscription(
    store, tmp_path
):
    from backend.pipeline.media_batch.inventory import create
    from backend.pipeline.media_batch.files import snapshot

    root = tmp_path / "catalog"
    (root / "config").mkdir(parents=True)
    (root / "script_review").mkdir()
    (root / "config/sermon.json").write_text(
        json.dumps(
            [dict(item="Rev Wang-Friday Mar 16 2007", title="警醒預備", status="ready")]
        )
    )
    (root / "script_review/Rev Wang-Friday Mar 16 2007.json").write_text(
        '[{"text":"已有逐字稿"}]'
    )
    source = audio(tmp_path / "input/Rev Wang-Friday Mar 16 2007.wav")
    manifest = tmp_path / "manifest.json"
    receipts = tmp_path / "receipt.jsonl"
    manifest.write_text(
        json.dumps(
            dict(
                destination=str(source.parent),
                files=[dict(path=source.name, bytes=source.stat().st_size)],
            )
        )
    )
    receipts.write_text(
        json.dumps(
            dict(path=source.name, bytes=source.stat().st_size, sha256=file_sha(source))
        )
        + "\n"
    )
    cfg = config(tmp_path)
    batch = create(store, manifest, receipts, cfg)
    assert snapshot(store, batch)["counts"] == {"ready_to_plan": 1}
    # Enrich the same stable dashboard; no need to lose the operation URL.
    assert (
        create(store, manifest, receipts, {**cfg, "catalog_root": str(root)}) == batch
    )
    row = snapshot(store, batch)["files"][0]
    assert row["state"] == "existing_transcript" and row["title"] == "警醒預備"
    assert snapshot(store, batch, search="警醒")["filtered_total"] == 1
    planned = plan(store, source.parent, {**cfg, "review_root": str(root)})
    assert store.claim(planned, "worker") is None
    assert store.snapshot(planned)["summary"]["needs_resolution"] == 1
    with store.connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM media_ingest.calls").fetchone()["n"]
            == 0
        )
