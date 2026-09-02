"""The article pipeline: one row per materialized article draft.

Sermons and articles are two independent processes and the relation between
them is many-to-many -- the 太16:13-20 article stands on eight sources, and one
sermon can feed several articles.  So this is a second table, not five more
columns on the first one. Retired CompositionPlans remain historical master
data; they are no longer treated as an unwritten-article queue here.

Almost nothing is computed here.  `matthew_exposition_progress` already derives
the stage chain, the SHA integrity checks and the blockers on every read, and
duplicating that would produce a second answer to the same question.  This adds
what that model does not carry: which plans exist beyond Matthew, what each run
cost, and which sources the article actually stands on.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from backend.config.wang_platform_paths import wang_platform_paths
from backend.api.wang_operations import (
    _article_citations,
    _effective_status,
    _load_json,
    _load_runs,
    _run_summary,
)


router = APIRouter(prefix="/admin/wang/operations", tags=["wang-admin"])

SCHEMA_VERSION = "wang-operations-articles.v2"

QUALITY_PROFILE_DIR = Path(__file__).resolve().parents[1] / "config" / "editorial_quality_profiles"


def _data_base() -> Path:
    value = os.getenv("DATA_BASE_DIR")
    if not value:
        raise HTTPException(status_code=503, detail="DATA_BASE_DIR is required")
    return Path(value).expanduser().resolve()


def _quality_profiles() -> dict[str, dict[str, Any]]:
    """Each profile's per-dimension minimum, keyed by profile id."""

    profiles: dict[str, dict[str, Any]] = {}
    if not QUALITY_PROFILE_DIR.is_dir():
        return profiles
    for path in sorted(QUALITY_PROFILE_DIR.glob("*.json")):
        payload = _load_json(path)
        if not payload:
            continue
        profiles[str(payload.get("profile_id") or path.stem)] = {
            "minimums": {
                str(item.get("id")): item.get("minimum")
                for item in payload.get("dimensions") or []
            },
            "hard_gate_dimensions": list(payload.get("hard_gate_dimensions") or []),
        }
    return profiles


def _dimension_verdict(
    review: Optional[dict[str, Any]], profile: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """How many dimensions cleared their own minimum.

    Not the total score, deliberately.  The quality profile dropped a total
    threshold because one number let a weak dimension ride on the strong ones,
    so the headline here has to be the count that cannot hide one.  `total_score`
    is carried along for a reader and decides nothing.
    """

    if not review:
        return None
    scores = ((review.get("review") or {}).get("dimension_scores")) or []
    if not scores:
        return None
    minimums = (profile or {}).get("minimums") or {}
    below: list[str] = []
    for entry in scores:
        dimension = str(entry.get("dimension_id") or "")
        minimum = minimums.get(dimension)
        if minimum is None:
            continue
        if (entry.get("score") or 0) < minimum:
            below.append(dimension)
    return {
        "dimensions": len(scores),
        "below_minimum": below,
        "passed_dimensions": len(scores) - len(below),
        "hard_gate_failures": list(review.get("hard_gate_failures") or []),
        "declared_hard_failures": list(review.get("declared_hard_failures") or []),
        "passed": bool(review.get("passed")),
        "total_score": review.get("total_score"),
    }


def _drafts(paths: Any) -> dict[str, dict[str, Any]]:
    """Published drafts, keyed by immutable draft ID."""

    drafts: dict[str, dict[str, Any]] = {}
    root = paths.repository / "editorial_drafts"
    if not root.is_dir():
        return drafts
    for manifest_path in sorted(root.glob("*/editorial-draft-manifest.json")):
        manifest = _load_json(manifest_path)
        if not manifest:
            continue
        for draft in manifest.get("drafts") or []:
            draft_id = str(draft.get("draft_id") or "")
            if not draft_id:
                continue
            config = draft.get("audit_config") or {}
            review = _load_json(
                manifest_path.parent
                / str(config.get("editorial_review_path") or "publication-editorial-review.json")
            )
            drafts[draft_id] = {
                "draft_id": draft_id,
                "title": draft.get("title"),
                "passage": draft.get("passage"),
                "slug": draft.get("public_slug"),
                "status": draft.get("status"),
                "profile_id": draft.get("publication_profile_id"),
                "review": review,
                "directory": manifest_path.parent.name,
            }
    return drafts


def _progress_by_draft() -> dict[str, dict[str, Any]]:
    """The Matthew progress read model, indexed by draft.

    Read rather than recomputed: it is the authority for the stage chain and the
    SHA checks, and a second implementation would eventually disagree with it.
    """

    try:
        from backend.api.matthew_exposition_progress import progress_data

        payload = progress_data()
    except Exception:  # pragma: no cover - depends on deployment
        return {}
    return {
        str(article.get("draft_id")): article
        for article in payload.get("articles") or []
        if article.get("draft_id")
    }


@router.get("/articles")
def articles() -> dict[str, Any]:
    """One row per materialized draft, recomputed on every read."""

    data_base = _data_base()
    paths = wang_platform_paths(data_base)
    now = datetime.now(timezone.utc)

    drafts = _drafts(paths)
    progress = _progress_by_draft()
    profiles = _quality_profiles()
    citations, warnings = _article_citations(paths)

    # Which sources each draft cites, inverted from the sermon-side mapping.
    cited_by_draft: dict[str, list[str]] = {}
    for source_id, draft_ids in citations.items():
        for draft_id in draft_ids:
            cited_by_draft.setdefault(draft_id, []).append(source_id)

    runs, run_warnings = _load_runs()
    warnings.extend(run_warnings)
    for run in runs:
        run["effective_status"] = _effective_status(run, now)
    runs_by_subject: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        if run["stage"] == "article":
            runs_by_subject.setdefault(str(run["subject_id"]), []).append(run)

    rows = [
        _row(draft, progress, profiles, cited_by_draft, runs_by_subject)
        for draft in drafts.values()
    ]
    rows.sort(key=lambda row: (str(row.get("passage") or ""), row["draft_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "summary": {
            "articles": len(rows),
            "published": sum(1 for row in rows if row["repository_published"]),
            "spend_usd": round(
                sum(
                    float(run["cost_usd"])
                    for group in runs_by_subject.values()
                    for run in group
                    if run.get("cost_usd") is not None
                ),
                4,
            ),
            "article_runs_recorded": sum(len(group) for group in runs_by_subject.values()),
        },
        "rows": rows,
        "warnings": warnings,
    }


def _row(
    draft: dict[str, Any],
    progress: dict[str, dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    cited_by_draft: dict[str, list[str]],
    runs_by_subject: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    draft_id = str(draft["draft_id"])
    tracked = progress.get(draft_id, {})
    profile = profiles.get(str(draft.get("profile_id") or ""))
    editorial = _dimension_verdict(draft.get("review"), profile)
    runs = runs_by_subject.get(draft_id, [])
    return {
        "draft_id": draft_id,
        "title": draft.get("title") or draft_id,
        "slug": draft.get("slug"),
        "passage": draft.get("passage") or (tracked.get("passage") or {}).get("display"),
        "current_stage": tracked.get("current_stage"),
        "stages": tracked.get("stages") or [],
        "editorial": editorial,
        "program_audit": tracked.get("program_audit"),
        "publication_decision": tracked.get("publication_decision"),
        "sha_integrity": tracked.get("sha_integrity"),
        "repository_published": bool(tracked.get("repository_published")),
        "production_visible": tracked.get("production_visible"),
        "blockers": tracked.get("blockers") or [],
        "next_step": tracked.get("next_step"),
        "cited_sources": sorted(cited_by_draft.get(draft_id or "", [])),
        "links": tracked.get("links") or {},
        "runs": [_run_summary(run) for run in runs],
        "cost_usd": round(
            sum(float(run["cost_usd"]) for run in runs if run.get("cost_usd") is not None), 4
        ) if runs else None,
    }
