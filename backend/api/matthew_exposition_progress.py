from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from backend.api.config import WANG_CLAIM_LAYER_STAGING_DIR, WANG_REPOSITORY_DIR
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.public_wang_articles import (
    APPROVAL_SCHEMA_VERSIONS,
    _manifest_passage_slug,
    _public_scripture_reference,
    public_article_data,
)
from backend.api.sermon_search.bible_refs import normalize_ref
from backend.pipeline.editorial_draft_repository import (
    _review_gate,
    _validate_publication_gates,
)


router = APIRouter(prefix="/admin/wang", tags=["wang-admin"])

MATTHEW_PLAN_FALLBACK = (
    WANG_CLAIM_LAYER_STAGING_DIR
    / "matthew-16-notes"
    / "composition_plan_matthew_16_notes.json"
)
MATTHEW_VERSE_COUNTS = (
    25, 23, 17, 25, 48, 34, 29, 34, 38, 42, 30, 50, 58, 36,
    39, 28, 27, 35, 30, 34, 46, 46, 39, 51, 46, 75, 66, 20,
)
STAGES = (
    "composition_ready",
    "knowledge_ready",
    "authoring",
    "independent_editorial_review",
    "revision",
    "final_delta_review",
    "program_audit",
    "publication_decision",
    "repository_published",
    "production_visible",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_child(root: Path, relative: Any) -> Path | None:
    value = str(relative or "").strip()
    if not value:
        return None
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _passage(raw: Any) -> dict[str, Any] | None:
    ref = normalize_ref(str(raw or ""))
    if ref is None or ref.book != "Matt" or ref.verse_start is None:
        return None
    end_chapter = ref.chapter_end or ref.chapter_start
    end_verse = ref.verse_end or ref.verse_start
    return {
        "osis": ref.osis,
        "display": str(raw).strip(),
        "start": {"chapter": ref.chapter_start, "verse": ref.verse_start},
        "end": {"chapter": end_chapter, "verse": end_verse},
        "cross_chapter": end_chapter != ref.chapter_start,
    }


def _verse_keys(passage: dict[str, Any]) -> list[tuple[int, int]]:
    start = passage["start"]
    end = passage["end"]
    keys: list[tuple[int, int]] = []
    for chapter in range(start["chapter"], end["chapter"] + 1):
        first = start["verse"] if chapter == start["chapter"] else 1
        last = end["verse"] if chapter == end["chapter"] else MATTHEW_VERSE_COUNTS[chapter - 1]
        keys.extend((chapter, verse) for verse in range(first, last + 1))
    return keys


def _stage_rows(current_stage: str, *, production_unknown: bool = False) -> list[dict[str, str]]:
    current_index = STAGES.index(current_stage)
    rows = []
    for index, stage in enumerate(STAGES):
        if index < current_index:
            state = "complete"
        elif index == current_index:
            state = "active" if stage not in {"repository_published", "production_visible"} else "complete"
        else:
            state = "unknown" if production_unknown and stage == "production_visible" else "not_started"
        rows.append({"stage": stage, "state": state})
    return rows


def _plan_rows(plan: dict[str, Any], authority: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for decision in plan.get("decisions", []):
        if decision.get("readiness") != "article_ready":
            continue
        passage = _passage(decision.get("passage"))
        if not passage:
            warnings.append(
                {
                    "code": "unresolved_passage_scope",
                    "severity": "error",
                    "plan_id": plan.get("plan_id"),
                    "decision_id": decision.get("decision_id"),
                }
            )
            continue
        rows.append(
            {
                "article_unit_id": passage["osis"],
                "passage": passage,
                "title": decision.get("section_title") or decision.get("title") or passage["display"],
                "draft_id": None,
                "plan_refs": [
                    {
                        "plan_id": plan.get("plan_id"),
                        "decision_id": decision.get("decision_id"),
                        "authority": authority,
                    }
                ],
                "claim_ids": decision.get("claim_ids", []),
                "coverage": decision.get("coverage"),
            }
        )
    return rows, warnings


def _planned_candidates() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    database_url = os.getenv("KNOWLEDGE_DATABASE_URL")
    if database_url:
        try:
            package = PostgresKnowledgeStore(database_url).compile_package()
            for plan in package.get("product_plans", []):
                plan_rows, plan_warnings = _plan_rows(plan, "postgresql_authoring_store")
                rows.extend(plan_rows)
                warnings.extend(plan_warnings)
        except Exception as exc:
            warnings.append(
                {
                    "code": "postgresql_planning_unavailable",
                    "severity": "warning",
                    "message": f"PostgreSQL CompositionPlan 暂时无法读取：{exc}",
                }
            )
    else:
        warnings.append(
            {
                "code": "postgresql_planning_not_configured",
                "severity": "warning",
                "message": "未配置 PostgreSQL authoring store；计划状态只使用迁移期 artifact 回退。",
            }
        )

    plan = _read_json(MATTHEW_PLAN_FALLBACK)
    if not plan:
        warnings.append({"code": "planning_fallback_missing", "severity": "warning"})
        return rows, warnings

    fallback_rows, fallback_warnings = _plan_rows(plan, "composition_plan_artifact_fallback")
    warnings.extend(fallback_warnings)
    database_passages = {item["article_unit_id"] for item in rows}
    supplemental = [item for item in fallback_rows if item["article_unit_id"] not in database_passages]
    if supplemental:
        rows.extend(supplemental)
        warnings.append(
            {
                "code": "planning_artifact_fallback",
                "severity": "warning",
                "message": "部分太16文章单元仍来自迁移期 CompositionPlan artifact，尚未全部进入 PostgreSQL。",
            }
        )
    return rows, warnings


def _production_slugs() -> tuple[set[str] | None, dict[str, Any]]:
    environment = os.getenv("WANG_RUNTIME_ENV", "unknown").strip().lower()
    origin = os.getenv("WANG_PRODUCTION_API_ORIGIN", "").rstrip("/")
    runtime: dict[str, Any] = {
        "environment": environment,
        "api_schema_version": "wang-matthew-exposition-progress.v1",
        "recognized_publication_decision_schemas": sorted(APPROVAL_SCHEMA_VERSIONS),
        "production_probe_configured": bool(origin) or environment == "production",
        "production_probe_checked_at": datetime.now(timezone.utc).isoformat(),
        "production_probe_available": False,
        "deployment_state": "unknown",
    }
    if environment == "production" and not origin:
        from backend.api.public_wang_articles import list_public_articles

        slugs = {item["slug"] for item in list_public_articles().get("articles", [])}
        runtime.update(production_probe_available=True, deployment_state="current")
        return slugs, runtime
    if not origin:
        return None, runtime
    try:
        response = httpx.get(f"{origin}/public/wang-articles", timeout=3.0)
        response.raise_for_status()
        slugs = {
            str(item.get("slug"))
            for item in response.json().get("articles", [])
            if item.get("slug")
        }
    except (httpx.HTTPError, ValueError, TypeError):
        runtime["deployment_state"] = "unreachable"
        return None, runtime
    runtime.update(production_probe_available=True, deployment_state="current")
    return slugs, runtime


def _repository_articles(production_slugs: set[str] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = WANG_REPOSITORY_DIR.resolve()
    for manifest_path in sorted(root.glob("editorial_drafts/*/editorial-draft-manifest.json")):
        manifest = _read_json(manifest_path)
        for item in manifest.get("drafts", []):
            passage = _passage(item.get("passage"))
            draft_id = str(item.get("draft_id") or "").strip()
            if not passage or not draft_id:
                continue
            config = item.get("audit_config") or {}
            manuscript_path = _safe_child(manifest_path.parent, item.get("relative_path"))
            audit_path = _safe_child(manifest_path.parent, config.get("audit_output_path"))
            review_path = _safe_child(manifest_path.parent, config.get("editorial_review_path"))
            decision_path = _safe_child(manifest_path.parent, config.get("publication_decision_path"))
            audit = _read_json(audit_path) if audit_path else {}
            review = _read_json(review_path) if review_path else {}
            decision = _read_json(decision_path) if decision_path else {}
            review_gate = _review_gate(review)
            repository_valid = False
            blockers: list[dict[str, Any]] = []
            try:
                _validate_publication_gates(manifest_path.parent, item, draft_id)
                repository_valid = True
            except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
                blockers.append(
                    {"code": "repository_gate_failed", "severity": "error", "message": str(exc)}
                )
            slug = _manifest_passage_slug(item)
            production_visible = None if production_slugs is None else slug in production_slugs
            if repository_valid and production_visible is False:
                blockers.append(
                    {
                        "code": "production_deployment_lag",
                        "severity": "warning",
                        "message": (
                            "Wang repository 已有 automated-publication-decision.v1，但 production 文章目录尚未返回此文章；可能是部署滞后或 backend 尚未识别新版决定 schema。"
                            if decision.get("schema_version") == "automated-publication-decision.v1"
                            else "Wang repository 已有有效稿件，但 production 文章目录尚未返回此文章。"
                        ),
                    }
                )
            sha_checks = []
            manuscript_sha = _sha256(manuscript_path) if manuscript_path else None
            for name, actual, expected in (
                ("audit_manuscript", manuscript_sha, (audit.get("fingerprint") or {}).get("draft_sha256")),
                ("review_manuscript", manuscript_sha, review_gate.get("manuscript_sha256")),
                ("decision_manuscript", manuscript_sha, decision.get("manuscript_sha256")),
                ("decision_audit", _sha256(audit_path) if audit_path else None, decision.get("technical_audit_sha256")),
                ("decision_review", _sha256(review_path) if review_path else None, decision.get("editorial_review_sha256")),
            ):
                sha_checks.append(
                    {
                        "name": name,
                        "status": "match" if actual and expected and actual == expected else "unknown" if not actual or not expected else "mismatch",
                        "actual": actual,
                        "expected": expected,
                    }
                )
            if any(check["status"] == "mismatch" for check in sha_checks):
                blockers.append(
                    {"code": "sha_mismatch", "severity": "error", "message": "稿件、审核或出版决定 SHA 不一致。"}
                )
            local_article = None
            if repository_valid and slug:
                try:
                    local_article = public_article_data(slug)
                except Exception:
                    local_article = None
            decision_kind = (
                "automated"
                if decision.get("schema_version") == "automated-publication-decision.v1"
                else "human"
                if decision.get("schema_version") == "human-publication-decision.v1"
                else "unknown"
            )
            current_stage = (
                "production_visible"
                if production_visible is True
                else "repository_published"
                if repository_valid
                else "publication_decision"
                if decision
                else "program_audit"
                if audit
                else "authoring"
            )
            timestamps = [
                path.stat().st_mtime
                for path in (manifest_path, manuscript_path, audit_path, review_path, decision_path)
                if path and path.is_file()
            ]
            rows.append(
                {
                    "article_unit_id": passage["osis"],
                    "passage": passage,
                    "title": item.get("title") or passage["display"],
                    "draft_id": draft_id,
                    "slug": slug or None,
                    "manifest_status": item.get("status"),
                    "current_stage": current_stage,
                    "stages": _stage_rows(current_stage, production_unknown=production_slugs is None),
                    "editorial": {
                        "score": review_gate.get("total_score"),
                        "passed": review_gate.get("passed"),
                        "hard_gate_failures": review_gate.get("hard_gate_failures") or [],
                        "declared_hard_failures": review_gate.get("declared_hard_failures") or review.get("hard_failures") or [],
                    },
                    "program_audit": {
                        "status": audit.get("status") or "missing",
                        "error_count": (audit.get("summary") or {}).get("error_total"),
                        "warning_count": (audit.get("summary") or {}).get("warning_total"),
                    },
                    "publication_decision": {
                        "kind": decision_kind,
                        "schema_version": decision.get("schema_version"),
                        "authority": decision.get("approval_authority") or ("human_editor" if decision_kind == "human" else None),
                        "valid": repository_valid,
                    },
                    "sha_integrity": {
                        "status": "mismatch" if any(check["status"] == "mismatch" for check in sha_checks) else "consistent" if all(check["status"] == "match" for check in sha_checks) else "partial",
                        "checks": sha_checks,
                    },
                    "media": {
                        "covered_decision_count": (local_article or {}).get("audio_section_count", 0),
                        "player_count": (local_article or {}).get("player_count", 0),
                    },
                    "repository_published": repository_valid,
                    "production_visible": production_visible,
                    "blockers": blockers,
                    "next_step": (
                        "处理阻塞项"
                        if blockers
                        else "等待 production 探测"
                        if production_visible is None
                        else None
                    ),
                    "updated_at": datetime.fromtimestamp(max(timestamps), timezone.utc).isoformat() if timestamps else None,
                    "links": {
                        "draft": f"/admin/thought-review/candidates/drafts/{draft_id}",
                        "public": f"/resources/wang-repository/articles/{slug}" if slug else None,
                        "manifest": f"/api/admin/wang/matthew-progress/artifacts/{draft_id}/manifest",
                        "manuscript": f"/api/admin/wang/matthew-progress/artifacts/{draft_id}/manuscript",
                        "editorial_review": f"/api/admin/wang/matthew-progress/artifacts/{draft_id}/editorial-review" if review_path and review_path.is_file() else None,
                        "program_audit": f"/api/admin/wang/matthew-progress/artifacts/{draft_id}/program-audit" if audit_path and audit_path.is_file() else None,
                        "publication_decision": f"/api/admin/wang/matthew-progress/artifacts/{draft_id}/publication-decision" if decision_path and decision_path.is_file() else None,
                    },
                }
            )
    return rows


def _residual_plans(planned: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actual_verses = {key for item in actual for key in _verse_keys(item["passage"])}
    residual = []
    fallback_updated_at = (
        datetime.fromtimestamp(MATTHEW_PLAN_FALLBACK.stat().st_mtime, timezone.utc).isoformat()
        if MATTHEW_PLAN_FALLBACK.is_file()
        else None
    )
    for item in planned:
        remaining = [key for key in _verse_keys(item["passage"]) if key not in actual_verses]
        if not remaining:
            continue
        # Current canonical fallback units are contiguous. If that changes, do
        # not invent a new boundary; retain the plan with a warning.
        row = dict(item)
        coverage = str(item.get("coverage") or "")
        knowledge_ready = bool(item.get("claim_ids")) and "requires" not in coverage
        row.update(
            {
                "current_stage": "knowledge_ready" if knowledge_ready else "composition_ready",
                "stages": _stage_rows("knowledge_ready" if knowledge_ready else "composition_ready", production_unknown=True),
                "editorial": None,
                "program_audit": None,
                "publication_decision": None,
                "sha_integrity": {"status": "not_applicable", "checks": []},
                "media": {"covered_decision_count": 0, "player_count": 0},
                "repository_published": False,
                "production_visible": False,
                "blockers": (
                    [{"code": "knowledge_scope_incomplete", "severity": "warning", "message": "跨章知识范围仍需由完整 passage slice 验证。"}]
                    if not knowledge_ready
                    else []
                ),
                "next_step": "运行 Matthew exposition authoring runner" if knowledge_ready else "完成并验证跨章 passage knowledge slice",
                "updated_at": fallback_updated_at,
                "links": {"draft": None, "public": None, "manifest": None, "manuscript": None, "editorial_review": None, "program_audit": None, "publication_decision": None},
            }
        )
        residual.append(row)
    return residual


def progress_data() -> dict[str, Any]:
    production_slugs, runtime = _production_slugs()
    actual = _repository_articles(production_slugs)
    planned, warnings = _planned_candidates()
    articles = actual + _residual_plans(planned, actual)
    articles.sort(
        key=lambda item: (
            item["passage"]["start"]["chapter"],
            item["passage"]["start"]["verse"],
            item["passage"]["end"]["chapter"],
            item["passage"]["end"]["verse"],
        )
    )

    verse_states: dict[tuple[int, int], dict[str, bool | None]] = {
        (chapter, verse): {
            "planned": False,
            "generated": False,
            "repository_published": False,
            "production_visible": False if production_slugs is not None else None,
        }
        for chapter, count in enumerate(MATTHEW_VERSE_COUNTS, start=1)
        for verse in range(1, count + 1)
    }
    for article in articles:
        for key in _verse_keys(article["passage"]):
            state = verse_states[key]
            state["planned"] = True
            if article.get("draft_id"):
                state["generated"] = True
            if article.get("repository_published"):
                state["repository_published"] = True
            if article.get("production_visible") is True:
                state["production_visible"] = True

    chapters = []
    for chapter, count in enumerate(MATTHEW_VERSE_COUNTS, start=1):
        states = [verse_states[(chapter, verse)] for verse in range(1, count + 1)]
        chapters.append(
            {
                "chapter": chapter,
                "verse_count": count,
                "planned_verse_count": sum(bool(item["planned"]) for item in states),
                "generated_verse_count": sum(bool(item["generated"]) for item in states),
                "repository_verse_count": sum(bool(item["repository_published"]) for item in states),
                "production_verse_count": None if production_slugs is None else sum(bool(item["production_visible"]) for item in states),
                "coverage_gap_count": sum(not bool(item["planned"]) for item in states),
                "article_unit_ids": [
                    item["article_unit_id"]
                    for item in articles
                    if any(key[0] == chapter for key in _verse_keys(item["passage"]))
                ],
            }
        )

    total_verses = sum(MATTHEW_VERSE_COUNTS)
    summary = {
        "planned_article_count": len(articles),
        "generated_article_count": sum(bool(item.get("draft_id")) for item in articles),
        "repository_published_count": sum(bool(item.get("repository_published")) for item in articles),
        "production_visible_count": None if production_slugs is None else sum(bool(item.get("production_visible")) for item in articles),
        "cross_chapter_article_count": sum(bool(item["passage"]["cross_chapter"]) for item in articles),
        "blocked_article_count": sum(bool(item.get("blockers")) for item in articles),
        "total_verse_count": total_verses,
        "planned_verse_count": sum(bool(item["planned"]) for item in verse_states.values()),
        "generated_verse_count": sum(bool(item["generated"]) for item in verse_states.values()),
        "repository_verse_count": sum(bool(item["repository_published"]) for item in verse_states.values()),
        "production_verse_count": None if production_slugs is None else sum(bool(item["production_visible"]) for item in verse_states.values()),
    }
    return {
        "schema_version": "wang-matthew-exposition-progress.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "book": {"osis": "Matt", "label": "马太福音", "chapter_count": 28},
        "runtime": runtime,
        "summary": summary,
        "chapters": chapters,
        "articles": articles,
        "warnings": warnings,
    }


@router.get("/matthew-progress")
def get_matthew_progress():
    return progress_data()


@router.get("/matthew-progress/artifacts/{draft_id}/{artifact_kind}")
def get_matthew_progress_artifact(draft_id: str, artifact_kind: str):
    if not draft_id or "/" in draft_id or ".." in draft_id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    root = WANG_REPOSITORY_DIR.resolve()
    for manifest_path in root.glob("editorial_drafts/*/editorial-draft-manifest.json"):
        manifest = _read_json(manifest_path)
        item = next((row for row in manifest.get("drafts", []) if row.get("draft_id") == draft_id), None)
        if not item:
            continue
        config = item.get("audit_config") or {}
        relative = {
            "manifest": manifest_path.name,
            "manuscript": item.get("relative_path"),
            "editorial-review": config.get("editorial_review_path"),
            "program-audit": config.get("audit_output_path"),
            "publication-decision": config.get("publication_decision_path"),
        }.get(artifact_kind)
        path = _safe_child(manifest_path.parent, relative)
        if not path or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        if path.suffix.lower() in {".md", ".txt"}:
            return PlainTextResponse(path.read_text(encoding="utf-8"))
        payload = _read_json(path)
        if not payload:
            raise HTTPException(status_code=422, detail="Artifact is not valid JSON")
        return payload
    raise HTTPException(status_code=404, detail="Draft not found")
