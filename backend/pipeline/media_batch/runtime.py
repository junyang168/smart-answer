"""Cross-process capacity and dispatch receipts for actual batch model calls."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time
import uuid


class WaitingCapacity(RuntimeError):
    pass


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def file_sha(path):
    import hashlib

    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


@contextmanager
def slot(root, resource, count):
    root = Path(root) / resource
    root.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        lock = (root / f"{i}.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            continue
        journal = root / f"{i}.json"
        if (
            journal.exists()
            and json.loads(journal.read_text()).get("state") == "unknown"
        ):
            lock.close()
            continue
        try:
            yield journal
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        return
    raise WaitingCapacity("等待 " + resource + " 共用名額")


def capacity_status(root):
    result = {}
    for resource, count in [
        ("sol", 2),
        ("asr", 2),
        ("gemini", 2),
        ("mps", 1),
        ("transcode", 1),
    ]:
        active = unknown = 0
        for i in range(count):
            p = Path(root) / resource / f"{i}.lock"
            if not p.exists():
                continue
            with p.open("a") as f:
                try:
                    # Observers may overlap each other; only an exclusive worker
                    # lease should appear as active model work.
                    fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    active += 1
                    continue
                j = p.with_suffix(".json")
                if j.exists() and json.loads(j.read_text()).get("state") == "unknown":
                    unknown += 1
        result[resource] = dict(active=active, unknown=unknown, limit=count)
    return result


@contextmanager
def batch_call(out, request, reserved):
    context = os.getenv("MEDIA_BATCH_CONTEXT")
    if not context:
        yield lambda: None
        return
    ctx = json.loads(context)
    out = Path(out)
    # Existing saved_call cache is content-bound; no new reservation for a saved answer.
    from backend.pipeline.slide_change_pilot import object_sha

    folder = out / "calls" / object_sha(request)
    if (folder / "state.json").exists():
        yield lambda: None
        return
    model = request.get("model") or request.get("config", {}).get("model") or "gemini"
    resource = (
        "sol"
        if model == "gpt-6.1-sol"
        else "asr" if model == "gpt-transcribe" else "gemini"
    )
    from .store import Store, key

    store = Store()
    call = key([ctx["task"], str(out.resolve()), request])
    with slot(ctx["capacity_root"], resource, 2) as journal:
        admitted = False

        def admit():
            nonlocal admitted
            store.reserve(ctx["batch"], ctx["task"], call, model, reserved)
            admitted = True
            atomic(
                journal,
                dict(
                    state="unknown",
                    call=call,
                    batch=ctx["batch"],
                    task=ctx["task"],
                    model=model,
                ),
            )

        try:
            progress(request.get("stage", model))
            yield admit
        finally:
            path = folder / "state.json"
            state = json.loads(path.read_text()) if path.exists() else {}
            if admitted and state.get("status") in ("complete", "rejected"):
                if state["status"] == "complete":
                    try:
                        from .costs import estimate

                        state["usage_cost"] = estimate(
                            request, json.loads((folder / "response.json").read_text())
                        )
                    except (ValueError, KeyError, TypeError, OSError) as error:
                        state["usage_cost_error"] = str(error)
                store.call_complete(call, state)
                if state.get("provider_error_code") in ("credit_balance_exhausted", "insufficient_quota"):
                    store.stop_for_credit(ctx["batch"], call)
                atomic(journal, dict(state=state["status"], call=call))
            # No response proof: journal remains unknown even after the OS lock is released.


def progress(stage, completed=None, total=None, unit=None):
    raw = os.getenv("MEDIA_BATCH_CONTEXT")
    if raw:
        ctx = json.loads(raw)
        atomic(
            Path(ctx["progress_path"]),
            dict(
                stage=stage, completed=completed, total=total, unit=unit, at=time.time()
            ),
        )


def inherited_fds():
    context = os.getenv("MEDIA_BATCH_CONTEXT")
    return tuple(json.loads(context).get("inherited_fds", [])) if context else ()


def lock_held(path):
    path = Path(path)
    if not path.exists():
        return False
    with path.open("rb") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def storage_guards(config):
    result = []
    for name in ("artifact_root", "media_root", "review_root"):
        path = Path(config[name]).absolute()
        if path.parts[:2] == ("/", "Volumes") and len(path.parts) > 2:
            anchor = Path(*path.parts[:3])
            mounted = True
            if not os.path.ismount(anchor):
                raise ValueError("Required volume is not mounted: " + str(anchor))
        else:
            anchor = path
            while not anchor.exists():
                anchor = anchor.parent
            mounted = False
        result.append(
            dict(path=str(anchor), device=anchor.stat().st_dev, mount=mounted)
        )
    return result


def ensure_storage(config):
    for guard in config.get("storage_guards", []):
        path = Path(guard["path"])
        if (
            not path.exists()
            or path.stat().st_dev != guard["device"]
            or guard["mount"]
            and not os.path.ismount(path)
        ):
            raise ValueError("Storage volume missing or changed: " + str(path))
