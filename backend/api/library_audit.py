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

from fastapi import APIRouter

from backend.api.config import WANG_PLATFORM_PATHS
from backend.pipeline import library_audit_view

router = APIRouter(prefix="/admin/library-audit", tags=["library-audit-admin"])


@router.get("")
def audit_report() -> dict[str, Any]:
    """The latest run, or an explicit "never run" -- never an empty-looking zero.

    A page showing 0/0 reads as "nothing is wrong". "沒有跑過" reads as what it
    is, which for a constraint that blocks new passages is the difference that
    matters.
    """

    view = library_audit_view.load_view(WANG_PLATFORM_PATHS.library_audit_reports)
    if view is None:
        return {
            "status": "never_run",
            "reports_root": str(WANG_PLATFORM_PATHS.library_audit_reports),
        }
    return {"status": "ok", **view}
