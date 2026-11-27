"""macOS supervisor for a UI-controlled batch, independent of a terminal/chat."""

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from .store import Conflict


def label(batch):
    if not batch.startswith("B-") or not all(c in "B-0123456789abcdef" for c in batch):
        raise ValueError("Invalid batch ID")
    return "org.smart-answer.media-batch." + batch


def start(store, batch, workers=4):
    if not 1 <= workers <= 4:
        raise ValueError("workers must be 1..4")
    name = label(batch)
    with store.connection() as c:
        if not c.execute(
            "SELECT 1 FROM media_ingest.batches WHERE id=%s", (batch,)
        ).fetchone():
            raise KeyError(batch)
    state = Path.home() / ".local/state/smart-answer-media-batches" / batch
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = Path(__file__).resolve().parents[3]
    job = dict(
        Label=name,
        ProgramArguments=[
            sys.executable,
            "-m",
            "backend.pipeline.media_batch",
            "run",
            "--batch",
            batch,
            "--workers",
            str(workers),
        ],
        WorkingDirectory=str(root),
        RunAtLoad=True,
        KeepAlive=False,
        EnvironmentVariables=dict(
            MEDIA_BATCH_DATABASE_URL=store.dsn,
            PYTHONPATH=str(root),
            PATH=os.environ.get("PATH", "/opt/homebrew/bin:/usr/bin:/bin"),
        ),
        StandardOutPath=str(state / "stdout.log"),
        StandardErrorPath=str(state / "stderr.log"),
    )
    path = state / "worker.plist"
    if path.exists() and plistlib.loads(path.read_bytes()) != job:
        raise Conflict(
            "Supervisor configuration changed; stop the existing job before replacing it"
        )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        plistlib.dump(job, f)
        f.flush()
        os.fsync(f.fileno())
    target = f"gui/{os.getuid()}/{name}"
    existing = subprocess.run(
        ["launchctl", "print", target], capture_output=True, text=True
    )
    if existing.returncode == 0:
        with store.connection() as c:
            free = c.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok",
                ("media-batch:" + batch,),
            ).fetchone()["ok"]
            if free:
                c.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s,0))",
                    ("media-batch:" + batch,),
                )
        if free:
            subprocess.run(
                ["launchctl", "kickstart", target], check=True, capture_output=True
            )
        return dict(
            status="registered",
            batch=batch,
            progress_href="/admin/media-batches/" + batch,
        )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
        check=True,
        capture_output=True,
    )
    return dict(
        status="submitted", batch=batch, progress_href="/admin/media-batches/" + batch
    )


def stop(store, batch, operation, revision):
    result = store.control(batch, operation, revision, "pause")
    with store.connection() as c:
        active = c.execute(
            "SELECT 1 FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.state='running' LIMIT 1",
            (batch,),
        ).fetchone()
    if active:
        return dict(status="draining", revision=result)
    target = f"gui/{os.getuid()}/{label(batch)}"
    subprocess.run(["launchctl", "bootout", target], check=True, capture_output=True)
    return dict(status="stopped", revision=result)
