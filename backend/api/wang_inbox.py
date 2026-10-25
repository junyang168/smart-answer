"""What is waiting for a person, across the library's lines of work.

The architecture needs people in two places only: a ruling where two models
disagree and the rules cannot settle it, and the final review before
something is published (solution architecture §5, D4). This endpoint lists
exactly those, each with where to go to handle it. Model disagreements,
exhausted budgets and engineering failures are not on it: they mean the work
is unfinished, not that someone must decide, and they belong to the
operations pages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from backend.api import wang_essay_runs
from backend.api.wang_article_reviews import REVIEW_MANIFEST_ROOT

router = APIRouter(prefix="/admin/wang/inbox", tags=["wang-admin"])

REVIEW_PAGE = "/admin/wang/operations/articles/reviews/{review_id}"
ESSAY_PAGE = "/admin/wang/operations/articles/essays/{review_id}"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def final_reviews(root: Path | None = None) -> list[dict[str, Any]]:
    """Draft-first articles that passed review and nobody has published or withdrawn."""

    root = root or REVIEW_MANIFEST_ROOT
    items = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        manifest = _read(path)
        if not isinstance(manifest, dict) or manifest.get("variant") != "draft_first":
            continue
        if manifest.get("workflow_status") != "review_passed" or manifest.get("publication_decision"):
            continue
        review_id = manifest.get("review_id") or path.stem
        items.append({
            "kind": "final_review",
            "line": "文章",
            "title": manifest.get("title") or review_id,
            "summary": "审稿已通过，等终审与发布",
            "waiting_for": "负责人",
            "at": manifest.get("registered_at"),
            "href": REVIEW_PAGE.format(review_id=review_id),
        })
    return items


def essay_stops(root: Path | None = None) -> list[dict[str, Any]]:
    """Essay runs stopped for the owner, other than the final review listed above."""

    items = []
    for run in wang_essay_runs.list_runs(root):
        stopped = run.get("stopped")
        if not stopped or stopped["stage"] == "final_review":
            continue
        items.append({
            "kind": f"essay_{stopped['stage']}",
            "line": "文章",
            "title": run["title"],
            "summary": stopped["summary"],
            "waiting_for": "负责人",
            "at": stopped.get("at"),
            "href": ESSAY_PAGE.format(review_id=run["review_id"]),
        })
    return items


def legacy_viewpoint_exceptions() -> list[dict[str, Any]]:
    """CVP 1 exception bundles, while a queue file is still configured."""

    from backend.api.viewpoint_admin import _exception_queue

    try:
        queue = _exception_queue()
    except Exception:  # an unreadable queue is an operations problem, not a decision
        return []
    if queue is None:
        return []
    return [{
        "kind": "legacy_viewpoint_exception",
        "line": "观点",
        "title": f"旧观点例外 {len(queue.bundles)} 件",
        "summary": "CVP 1 留下的身份例外，处理完即下线",
        "waiting_for": "负责人",
        "at": None,
        "href": "/admin/wang/viewpoint-exceptions",
    }]


@router.get("")
def inbox() -> dict[str, Any]:
    items = final_reviews() + essay_stops() + legacy_viewpoint_exceptions()
    items.sort(key=lambda item: item.get("at") or "", reverse=True)
    return {"items": items}
