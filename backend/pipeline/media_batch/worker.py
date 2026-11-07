"""Controller supervises bounded stage subprocesses; model payloads stay on disk."""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from psycopg.types.json import Jsonb
from .store import Store
from .runtime import atomic, WaitingCapacity


def child():
    ctx = json.loads(os.environ["MEDIA_BATCH_CONTEXT"])
    payload = json.loads(Path(ctx["payload_path"]).read_text())
    from .stages import execute

    try:
        result = execute(ctx["stage"], payload)
        atomic(Path(ctx["result_path"]), dict(ok=True, result=result))
    except WaitingCapacity as e:
        atomic(Path(ctx["result_path"]), dict(ok=False, waiting=True, error=str(e)))
        sys.exit(75)
    except Exception as e:
        import traceback

        traceback.print_exc()
        atomic(
            Path(ctx["result_path"]),
            dict(ok=False, error=type(e).__name__ + ": " + str(e)),
        )
        sys.exit(1)


def run_task(store, batch, task):
    from .runtime import lock_held
    from .disk import release

    try:
        with ExitStack() as resources:
            return _run_task(store, batch, task, resources)
    except Exception as error:
        lease_path = Path(task["payload"]["out"]) / "leases" / task["id"]
        # A descendant still owns the lease: leave it running for reconciliation,
        # rather than permit another worker to write into the same workspace.
        if lock_held(lease_path):
            raise
        with store.connection() as c:
            unknown = c.execute(
                "SELECT 1 FROM media_ingest.calls WHERE task=%s AND state='unknown' LIMIT 1",
                (task["id"],),
            ).fetchone()
        state = (
            "unknown_outcome"
            if unknown
            else "pending" if isinstance(error, WaitingCapacity) else "failed"
        )
        store.update(task, state=state, error=type(error).__name__ + ": " + str(error))
        return state
    finally:
        lease_path = Path(task["payload"]["out"]) / "leases" / task["id"]
        if not lock_held(lease_path):
            release(store, task)


def _run_task(store, batch, task, resources):
    started = time.time()
    p = task["payload"]
    with store.connection() as c:
        runtime_config = c.execute(
            "SELECT config FROM media_ingest.batches WHERE id=%s", (batch,)
        ).fetchone()["config"]
    runtime_keys = (
        "capacity_root",
        "storage_guards",
        "ssd_reserve_gib",
        "internal_reserve_gib",
        "working_set_gib",
    )
    p = {
        **p,
        "config": {
            **p["config"],
            **{k: runtime_config[k] for k in runtime_keys if k in runtime_config},
        },
    }
    from .runtime import ensure_storage

    ensure_storage(p["config"])
    folder = Path(p["out"]) / "attempts" / task["id"] / str(task["attempt"])
    folder.mkdir(parents=True, exist_ok=True)
    lease_path = Path(p["out"]) / "leases" / task["id"]
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    lease = resources.enter_context(lease_path.open("a"))
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    from .disk import reserve, check_floor

    reserve(store, {**task, "payload": p})
    ctx = dict(
        inherited_fds=[lease.fileno()],
        batch=batch,
        task=task["id"],
        stage=task["stage"],
        unit=task["unit"],
        attempt=task["attempt"],
        capacity_root=p["config"]["capacity_root"],
        payload_path=str(folder / "payload.json"),
        result_path=str(folder / "result.json"),
        progress_path=str(folder / "progress.json"),
    )
    atomic(Path(ctx["payload_path"]), p)
    env = {
        **os.environ,
        "MEDIA_BATCH_CONTEXT": json.dumps(ctx),
        "MEDIA_BATCH_DATABASE_URL": store.dsn,
    }
    code = Path(__file__).resolve().parents[3]
    with (folder / "stdout.log").open("w") as stdout, (folder / "stderr.log").open(
        "w"
    ) as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "backend.pipeline.media_batch.worker", "--child"],
            cwd=code,
            env=env,
            stdout=stdout,
            stderr=stderr,
            pass_fds=(lease.fileno(),),
        )
        try:
            while process.poll() is None:
                check_floor(p["config"])
                path = Path(ctx["progress_path"])
                progress = (
                    json.loads(path.read_text())
                    if path.exists()
                    else dict(stage=task["stage"])
                )
                progress["attempt_started_at"] = started
                store.update(task, progress=progress)
                time.sleep(2)
        except BaseException:
            # Transport may already be running remotely. Do not silently restart it.
            process.terminate()
            process.wait(timeout=15)
            raise
    result = (
        json.loads(Path(ctx["result_path"]).read_text())
        if Path(ctx["result_path"]).exists()
        else dict(ok=False, error="Stage exited without a result receipt")
    )
    with store.connection() as c:
        unknown = c.execute(
            "SELECT 1 FROM media_ingest.calls WHERE task=%s AND state='unknown' LIMIT 1",
            (task["id"],),
        ).fetchone()
    state = (
        "complete"
        if result["ok"]
        else (
            "unknown_outcome"
            if unknown
            else "pending" if result.get("waiting") else "failed"
        )
    )
    store.update(
        task, state=state, result=result.get("result"), error=result.get("error")
    )
    return state


def run(store, batch, workers=4, drain=False):
    if not 1 <= workers <= 4:
        raise ValueError("workers must be 1..4")
    worker_id = uuid.uuid4().hex
    # PG advisory session lock prevents duplicate controllers for this batch, without locking other batches.
    with store.connection() as connection:
        if not connection.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok",
            ("media-batch:" + batch,),
        ).fetchone()["ok"]:
            raise ValueError("This batch already has a controller")
        connection.commit()
        mode = connection.execute(
            "SELECT config->>'mode' AS mode FROM media_ingest.batches WHERE id=%s",
            (batch,),
        ).fetchone()["mode"]
        connection.commit()
        if mode == "inventory":
            from .inventory import watch

            return watch(store, batch, drain)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = set()
            idle = 0
            while True:
                for f in list(futures):
                    if f.done():
                        f.result()
                        futures.remove(f)
                claimed = False
                while len(futures) < workers:
                    task = store.claim(batch, worker_id)
                    if not task:
                        break
                    futures.add(pool.submit(run_task, store, batch, task))
                    claimed = True
                if not futures and not claimed:
                    idle += 1
                    if idle >= 3:
                        snapshot = store.snapshot(batch)
                        if (
                            drain
                            or (
                                snapshot["summary"]["complete"]
                                + snapshot["summary"].get("reused", 0)
                            )
                            == snapshot["summary"]["total"]
                        ):
                            return snapshot
                else:
                    idle = 0
                time.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    if args.child:
        child()
