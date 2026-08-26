"""Serve the newest independent audit run to the admin page.

The audit itself is `scripts/audit-library.py`, which shares no code with this
package on purpose: its whole claim is that it checks the library without
reusing the pipeline's readers, models or assumptions. Reading the JSON it
already wrote does not touch that -- nothing here measures anything, and no
model is called.

Read fresh every time. A run takes minutes and lands a new directory; a cache
would keep showing the previous one, and "the audit says we are fine" going
stale is the one failure this page cannot afford.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.config import WANG_PLATFORM_PATHS
from backend.pipeline import library_audit_runner, library_audit_view

router = APIRouter(prefix="/admin/library-audit", tags=["library-audit-admin"])


@router.get("")
def audit_report() -> dict[str, Any]:
    """The latest run, or an explicit "never run" -- never an empty-looking zero.

    A page showing 0/0 reads as "nothing is wrong". "沒有跑過" reads as what it
    is, which for a constraint that blocks new passages is the difference that
    matters.
    """

    reports_root = WANG_PLATFORM_PATHS.library_audit_reports
    run = library_audit_runner.read_status(reports_root)
    view = library_audit_view.load_view(reports_root)
    if view is None:
        return {
            "status": "never_run",
            "reports_root": str(reports_root),
            "run": run,
        }
    return {"status": "ok", "run": run, **view}


@router.post("/runs")
def start_run(scope: str = "current-run") -> dict[str, Any]:
    """Start an audit. Returns at once; the run outlives this request.

    Read-only work, so there is nothing to undo if it is stopped or dies. What
    it does cost is roughly 1,400 model calls, which is why only one may run at
    a time.
    """

    result = library_audit_runner.start_run(reports_root(), scope=scope)
    if result["status"] == "no_script":
        raise HTTPException(status_code=500, detail=result["detail"])
    if result["status"] == "already_running":
        raise HTTPException(status_code=409, detail="已經有一輪在跑了")
    return result


@router.delete("/runs")
def stop_run() -> dict[str, Any]:
    """Stop the run in progress. Nothing to roll back -- the audit only reads."""

    return library_audit_runner.stop_run(reports_root())


def reports_root():
    return WANG_PLATFORM_PATHS.library_audit_reports
