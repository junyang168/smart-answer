"""Read-only view of draft-first essay runs for the library admin.

A run directory (`staging/essays/<review-id>/`, see
`backend.pipeline.draft_first_essay`) is the only memory of where an essay
stands: what the owner chose, what the review could not settle, where it
stopped. This module reads it for the admin pages; it never writes, and the
owner's decisions are still made in conversation (`/topic-essay`), because a
decision file does nothing until the command is run again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.config import WANG_STAGING_DIR

router = APIRouter(prefix="/admin/wang/essays", tags=["wang-admin"])

ESSAY_ROOT = WANG_STAGING_DIR / "essays"

# The command's stage names, in the words the admin uses.
STAGE_LABELS = {
    "scope": "定问题",
    "coverage": "覆盖搜索",
    "decisions": "选材",
    "cvp_gap": "补观点",
    "calibration": "审稿校准",
    "packet": "编材料包",
    "author": "写稿",
    "review": "审稿",
    "binding": "绑定出处",
    "register": "登记",
    "final_review": "终审",
}
STAGE_ORDER = list(STAGE_LABELS)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def stop_summary(stopped: dict[str, Any]) -> str:
    """One line saying what the owner has to decide, in the owner's terms."""

    stage = stopped.get("stage")
    if stage == "scope":
        return "文章的问题、经文或选定的观点还没写全"
    if stage == "decisions":
        return f"覆盖搜索找到 {len(stopped.get('candidates') or [])} 个候选，等逐项定纳入还是相邻"
    if stage == "cvp_gap":
        return f"{len(stopped.get('claims') or [])} 条纳入的主张还不属于任何观点，要先补观点"
    if stage == "calibration":
        return "审稿规则的校准结果和负责人的判定表对不上"
    if stage == "review":
        return f"审稿还有 {len(stopped.get('open') or [])} 个问题没解决"
    if stage == "final_review":
        return "审稿已通过，等终审与发布"
    return str(stopped.get("needs") or "")


def run_summary(run_dir: Path) -> dict[str, Any] | None:
    essay = _read(run_dir / "essay.json")
    if not isinstance(essay, dict) or not essay.get("review_id"):
        return None
    scope = _read(run_dir / "scope.json") or {}
    state = _read(run_dir / "state.json") or {"stages": {}}
    done = [s for s in STAGE_ORDER if (state.get("stages") or {}).get(s, {}).get("status") == "done"]
    stopped = state.get("stopped")
    stage = stopped["stage"] if stopped else (STAGE_ORDER[len(done)] if len(done) < len(STAGE_ORDER) else "final_review")
    return {
        "review_id": essay["review_id"],
        "title": scope.get("working_title") or essay["review_id"],
        "reader_question": scope.get("reader_question"),
        "passages": scope.get("passage_refs") or [],
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "stages_done": done,
        "stopped": {"stage": stopped["stage"], "summary": stop_summary(stopped), "at": stopped.get("at")} if stopped else None,
        "updated_at": (stopped or {}).get("at") or max(
            (entry.get("at") or "" for entry in (state.get("stages") or {}).values()), default=None),
    }


def list_runs(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or ESSAY_ROOT
    if not root.is_dir():
        return []
    runs = [run_summary(path) for path in sorted(root.iterdir()) if path.is_dir()]
    return [run for run in runs if run]


@router.get("")
def essay_runs() -> dict[str, Any]:
    return {"stage_labels": STAGE_LABELS, "runs": list_runs()}


@router.get("/{review_id}")
def essay_run(review_id: str) -> dict[str, Any]:
    run_dir = ESSAY_ROOT / review_id
    if "/" in review_id or review_id.startswith(".") or not (run_dir / "essay.json").is_file():
        raise HTTPException(status_code=404, detail="没有这篇文章的运行记录")
    summary = run_summary(run_dir)
    if summary is None:
        raise HTTPException(status_code=404, detail="没有这篇文章的运行记录")
    return summary
