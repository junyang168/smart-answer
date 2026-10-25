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
from backend.api.draft_first_review_report import review_report

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


def latest_review_dir(run_dir: Path, state: dict[str, Any]) -> Path | None:
    """The review the run stands on: the accepted one, else the newest round."""

    if state.get("current_review"):
        return Path(state["current_review"])
    stopped = state.get("stopped") or {}
    if stopped.get("review_dir"):
        return Path(stopped["review_dir"])
    dirs = [run_dir / "review", *sorted(run_dir.glob("review-owner-*"))]
    dirs = [d for d in dirs if (d / "review-run.json").is_file()]
    return dirs[-1] if dirs else None


def coverage(run_dir: Path) -> list[dict[str, Any]]:
    """Every coverage candidate beside the owner's decision on it, if made."""

    candidates = (_read(run_dir / "coverage" / "coverage-candidates.json") or {}).get("candidates") or []
    decisions = {str(d.get("id")): d for d in _read(run_dir / "coverage-decisions.json") or []}
    rows = []
    for candidate in candidates:
        decision = decisions.get(str(candidate.get("id"))) or {}
        rows.append({
            "kind": candidate.get("kind"),
            "text": candidate.get("text"),
            "why_relevant": candidate.get("why_relevant"),
            "suggestion": candidate.get("suggestion"),
            "suggestion_reason": candidate.get("reason"),
            "decision": decision.get("decision"),
            "structure_role": decision.get("structure_role"),
            "decision_reason": decision.get("reason"),
        })
    return rows


def run_detail(run_dir: Path) -> dict[str, Any] | None:
    summary = run_summary(run_dir)
    if summary is None:
        return None
    state = _read(run_dir / "state.json") or {}
    selection = _read(run_dir / "editorial-selection.json") or {}
    review_dir = latest_review_dir(run_dir, state)
    stopped = state.get("stopped") or {}
    return {
        **summary,
        "decision_basis": selection.get("decision_basis"),
        "coverage": coverage(run_dir),
        "cvp_gap": stopped.get("claims") if stopped.get("stage") == "cvp_gap" else [],
        "calibration": stopped.get("reason") if stopped.get("stage") == "calibration" else None,
        "review": review_report(review_dir) if review_dir else None,
    }


def article_rows(run_root: Path | None = None, manifest_root: Path | None = None) -> list[dict[str, Any]]:
    """Every draft-first article: its run if it has one, its registration if it has one.

    人子 and the two September articles were registered before runs existed,
    so a row may have a review page and no progress page, or the reverse.
    """

    from backend.api.wang_article_reviews import REVIEW_MANIFEST_ROOT

    manifest_root = manifest_root or REVIEW_MANIFEST_ROOT
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(manifest_root.glob("*.json")) if manifest_root.is_dir() else []:
        manifest = _read(path)
        if not isinstance(manifest, dict) or manifest.get("variant") != "draft_first" or manifest.get("archived"):
            continue
        review_id = str(manifest.get("review_id") or path.stem)
        decision = (manifest.get("publication_decision") or {}).get("decision")
        rows[review_id] = {
            "review_id": review_id,
            "title": manifest.get("title") or review_id,
            "passage": manifest.get("passage"),
            "stage_label": "已发布" if decision == "approved" else "已撤下" if decision == "withdrawn" else "终审",
            "waiting": None if decision else "审稿已通过，等终审与发布",
            "published": decision == "approved",
            "review_href": f"/admin/wang/operations/articles/reviews/{review_id}",
            "run_href": None,
            "updated_at": manifest.get("registered_at"),
        }
    for run in list_runs(run_root):
        essay = _read((run_root or ESSAY_ROOT) / run["review_id"] / "essay.json") or {}
        row = rows.setdefault(run["review_id"], {
            "review_id": run["review_id"], "title": run["title"], "passage": essay.get("passage"),
            "stage_label": run["stage_label"], "waiting": None, "published": False,
            "review_href": None, "updated_at": run["updated_at"],
        })
        row["run_href"] = f"/admin/wang/operations/articles/essays/{run['review_id']}"
        if not row.get("review_href") or (run.get("stopped") or {}).get("stage") not in (None, "final_review"):
            row["stage_label"] = run["stage_label"]
            row["waiting"] = (run.get("stopped") or {}).get("summary")
    return sorted(rows.values(), key=lambda row: row.get("updated_at") or "", reverse=True)


@router.get("")
def essay_runs() -> dict[str, Any]:
    return {"stage_labels": STAGE_LABELS, "runs": list_runs(), "articles": article_rows()}


@router.get("/{review_id}")
def essay_run(review_id: str) -> dict[str, Any]:
    run_dir = ESSAY_ROOT / review_id
    if "/" in review_id or review_id.startswith(".") or not (run_dir / "essay.json").is_file():
        raise HTTPException(status_code=404, detail="没有这篇文章的运行记录")
    detail = run_detail(run_dir)
    if detail is None:
        raise HTTPException(status_code=404, detail="没有这篇文章的运行记录")
    return detail
