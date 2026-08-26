"""Start an audit run from the admin page, and say how far it has got.

The page could only ever show what the audit had already written, so a report
went stale the moment anything changed and nobody reading it could tell. That
happened in practice: a check was corrected, and two findings the fix had
already withdrawn stayed on screen because the newest run predated it.

Running it is a ten-minute job of about 1,400 model calls, so it cannot happen
inside the request. This starts a detached process and returns; the audit
writes its own progress to a file (`--status-file`) and this module reads it.
The audit still imports nothing from `backend/` -- a file passed between them
is not a shared code path, and that independence is the whole point of it.

Only one run at a time. A second one would double the cost for an identical
answer, and both would race to write the same report directory.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


#: How the audit is invoked. The path is configurable because the deployment
#: and a development worktree do not keep the checkout in the same place, and a
#: hardcoded path would make this feature work on exactly one machine.
AUDIT_SCRIPT_ENV = "LIBRARY_AUDIT_SCRIPT"

#: A status file whose process is gone is not a run in progress. Without this a
#: single crash leaves the page saying "still running" until someone deletes a
#: file, and the button stays dead the whole time.
STALE_STATES = {"running"}


def script_path() -> Path:
    override = os.environ.get(AUDIT_SCRIPT_ENV)
    if override:
        return Path(override)
    # The repository root, four parents up from this file.
    return Path(__file__).resolve().parents[2] / "scripts" / "audit-library.py"


def status_file(reports_root: Path) -> Path:
    return reports_root / ".run-status.json"


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process, but it exists.
        return True
    return True


def read_status(reports_root: Path) -> dict[str, Any] | None:
    """What the last (or current) run is doing, as the audit itself reported it."""

    path = status_file(reports_root)
    if not path.is_file():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if status.get("state") in STALE_STATES and not _process_alive(status.get("pid")):
        # The process died without writing a finish. Say so rather than leave
        # the page waiting on something that will never arrive.
        status = {
            **status,
            "state": "died",
            "detail": "審計行程沒了，而且沒有寫完成。看 stderr 或直接重跑一次。",
        }
    return status


def is_running(reports_root: Path) -> bool:
    status = read_status(reports_root)
    return bool(status and status.get("state") == "running")


def start_run(reports_root: Path, *, scope: str = "current-run") -> dict[str, Any]:
    """Launch the audit and return immediately.

    `start_new_session` detaches it from this server: a `uvicorn --reload`
    restart, which happens on every code change in development, must not kill
    a run that is eight minutes in.
    """

    if is_running(reports_root):
        return {"status": "already_running", "run": read_status(reports_root)}

    script = script_path()
    if not script.is_file():
        return {
            "status": "no_script",
            "detail": f"找不到 {script}；用 {AUDIT_SCRIPT_ENV} 指到 audit-library.py",
        }

    reports_root.mkdir(parents=True, exist_ok=True)
    log = reports_root / ".run-latest.log"
    command = [
        sys.executable,
        str(script),
        "--scope",
        scope,
        "--status-file",
        str(status_file(reports_root)),
    ]
    with log.open("wb") as handle:
        process = subprocess.Popen(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(script.parent.parent),
        )
    # The audit writes its own status as soon as it starts, but the page may
    # poll before that: seed the file so the button does not bounce back to
    # "idle" for the first second.
    status_file(reports_root).write_text(
        json.dumps(
            {
                "state": "running",
                "pid": process.pid,
                "stage": "啟動中",
                "done": 0,
                "total": 0,
                "scope": scope,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {"status": "started", "pid": process.pid, "log": str(log)}


def stop_run(reports_root: Path) -> dict[str, Any]:
    """Ask a running audit to stop. It is read-only, so stopping breaks nothing."""

    status = read_status(reports_root)
    if not status or status.get("state") != "running":
        return {"status": "not_running"}
    pid = status.get("pid")
    if not _process_alive(pid):
        return {"status": "not_running"}
    os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
    return {"status": "stopping", "pid": pid}
