"""Read-only, SHA-bound previews of unpublished Wang topic essays.

These records live in staging and are deliberately separate from the public
Wang repository.  A preview manifest makes one draft visible to authenticated
admin readers; it never supplies a publication decision and can therefore
never make the public article endpoint accept the manuscript.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.config import WANG_STAGING_DIR


router = APIRouter(prefix="/admin/wang/article-reviews", tags=["wang-admin"])

MANIFEST_SCHEMA = "wang_topic_essay_review_preview.v1"
RESPONSE_SCHEMA = "wang_topic_essay_review_read_model.v1"
REVIEW_MANIFEST_ROOT = WANG_STAGING_DIR / "topic-essay-reviews"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_staging_child(relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("review artifact path must be relative to Wang staging")
    root = WANG_STAGING_DIR.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("review artifact path leaves Wang staging") from exc
    return candidate


def _reader_markdown(markdown: str) -> str:
    """Hide provenance comments without rewriting a byte of reader prose."""

    return re.sub(r"<!--\s*provenance:\s*[\s\S]*?-->\s*", "", markdown).strip()


def _stage_checks(workflow: dict[str, Any]) -> list[dict[str, str]]:
    status = str(workflow.get("status") or "unknown")
    grounding = "passed" if status == "draft_grounded" else "not_run"
    if status == "grounding_gate_failed":
        grounding = "failed"
    return [
        {"id": "author", "label": "Author 初稿", "state": "complete"},
        {"id": "grounding", "label": "Grounding", "state": grounding},
        {"id": "editorial_review", "label": "Editorial Review", "state": "not_run"},
        {"id": "program_audit", "label": "Program Audit", "state": "not_run"},
        {"id": "publication", "label": "正式出版", "state": "not_run"},
    ]


def _review_data(manifest_path: Path, *, include_markdown: bool) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("unsupported or unreadable review preview manifest")
    review_id = str(manifest.get("review_id") or "").strip()
    if not review_id or manifest_path.stem != review_id:
        raise ValueError("review id does not match its manifest filename")

    manuscript_path = _safe_staging_child(str(manifest.get("manuscript_relative_path") or ""))
    workflow_path = _safe_staging_child(str(manifest.get("workflow_status_relative_path") or ""))
    if not manuscript_path.is_file() or not workflow_path.is_file():
        raise ValueError("review manuscript or workflow status is missing")

    expected_manuscript_sha = str(manifest.get("manuscript_sha256") or "")
    expected_workflow_sha = str(manifest.get("workflow_status_sha256") or "")
    current_manuscript_sha = _sha256(manuscript_path)
    current_workflow_sha = _sha256(workflow_path)
    integrity = (
        "verified"
        if expected_manuscript_sha == current_manuscript_sha
        and expected_workflow_sha == current_workflow_sha
        else "changed"
    )
    workflow = _read_json(workflow_path)
    result: dict[str, Any] = {
        "review_id": review_id,
        "title": str(manifest.get("title") or "").strip(),
        "passage": str(manifest.get("passage") or "").strip(),
        "registered_at": str(manifest.get("registered_at") or ""),
        "status": "internal_review",
        "integrity_status": integrity,
        "manuscript_sha256": expected_manuscript_sha,
        "brief_sha256": str(manifest.get("brief_sha256") or ""),
        "authoring_packet_sha256": str(manifest.get("authoring_packet_sha256") or ""),
        "workflow_status": str(workflow.get("status") or "unknown"),
        "stage_checks": _stage_checks(workflow),
        "href": f"/admin/wang/operations/articles/reviews/{review_id}",
    }
    if include_markdown:
        if integrity != "verified":
            raise HTTPException(
                status_code=409,
                detail="审稿预览绑定的稿件或状态已经改变，请重新登记后再审。",
            )
        result["markdown"] = _reader_markdown(manuscript_path.read_text(encoding="utf-8"))
    return result


@router.get("")
def list_article_reviews() -> dict[str, Any]:
    reviews: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    if REVIEW_MANIFEST_ROOT.is_dir():
        for manifest_path in sorted(REVIEW_MANIFEST_ROOT.glob("*.json")):
            try:
                reviews.append(_review_data(manifest_path, include_markdown=False))
            except ValueError as exc:
                warnings.append({"manifest": manifest_path.name, "message": str(exc)})
    return {"schema_version": RESPONSE_SCHEMA, "reviews": reviews, "warnings": warnings}


@router.get("/{review_id}")
def article_review(review_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", review_id):
        raise HTTPException(status_code=404, detail="找不到这份审稿预览。")
    manifest_path = REVIEW_MANIFEST_ROOT / f"{review_id}.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="找不到这份审稿预览。")
    try:
        return {"schema_version": RESPONSE_SCHEMA, **_review_data(manifest_path, include_markdown=True)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
