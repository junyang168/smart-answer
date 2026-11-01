"""Shared by form writes and assistant merges, across threads and processes."""
from contextlib import contextmanager
from functools import wraps
import fcntl
import os
from pathlib import Path
import threading

_mutex = threading.RLock()
_local = threading.local()


@contextmanager
def sunday_data_lock():
    with _mutex:
        if getattr(_local, "held", False):
            yield
            return
        p = Path(os.environ["DATA_BASE_DIR"]) / "config" / ".sunday-data.lock"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            _local.held = True
            try:
                yield
            finally:
                _local.held = False
                fcntl.flock(f, fcntl.LOCK_UN)


def sunday_write(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with sunday_data_lock():
            return fn(*args, **kwargs)
    return wrapped
