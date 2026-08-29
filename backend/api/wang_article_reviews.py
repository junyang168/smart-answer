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
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from backend.api.config import WANG_STAGING_DIR
from backend.api.canonical_repository.service import CanonicalRepositoryService


router = APIRouter(prefix="/admin/wang/article-reviews", tags=["wang-admin"])

MANIFEST_SCHEMA = "wang_topic_essay_review_preview.v1"
RESPONSE_SCHEMA = "wang_topic_essay_review_read_model.v1"
REVIEW_MANIFEST_ROOT = WANG_STAGING_DIR / "topic-essay-reviews"
PROVENANCE_COMMENT_RE = re.compile(r"<!--\s*provenance:\s*(\{.*?\})\s*-->", re.S)
FOOTNOTE_DEFINITION_RE = re.compile(r"^\[\^[^\]]+\]:")
SOURCE_MARKER_PREFIX = "#review-source-evidence-"


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


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("result") or payload
    return dict(value) if isinstance(value, dict) else {}


def _fragment_ids(step: dict[str, Any]) -> list[str]:
    values = step.get("source_fragment_ids") or []
    result = [str(value) for value in values if value]
    single = str(step.get("source_fragment_id") or "").strip()
    if single:
        result.append(single)
    return list(dict.fromkeys(result))


def _safe_resource_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if url.startswith("/resources/") and not url.startswith("//"):
        return url
    return None


def _source_fragment_read_model(
    fragment: dict[str, Any],
    source: dict[str, Any],
    sermon_cache: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    excerpt = str(fragment.get("verbatim_excerpt") or "").strip()
    fragment_id = str(fragment.get("fragment_id") or "").strip()
    source_type = str(source.get("source_type") or "").strip()
    if not excerpt or not fragment_id or source_type not in {"sermon_transcript", "notes_manuscript"}:
        return None
    title = str(source.get("title") or source.get("transcript_id") or "来源材料").strip()
    result: dict[str, Any] = {
        "fragment_ids": [fragment_id],
        "source_type": source_type,
        "title": title,
        "excerpts": [excerpt],
        "full_source_url": None,
        "media": None,
    }
    if source_type == "notes_manuscript":
        result["full_source_url"] = _safe_resource_url(source.get("source_url"))
        return result

    transcript_id = str(source.get("transcript_id") or "").strip()
    if not transcript_id:
        return None
    sermon = sermon_cache.get(transcript_id)
    if sermon is None:
        catalog = CanonicalRepositoryService._sermon_catalog_record(transcript_id)
        media = CanonicalRepositoryService._sermon_media(transcript_id, {}, catalog)
        sermon = {
            "full_source_url": f"/resources/sermons/{quote(transcript_id, safe='')}",
            "media": media.model_dump(mode="json"),
        }
        sermon_cache[transcript_id] = sermon
    start_seconds = fragment.get("media_time")
    end_seconds = fragment.get("media_end_time")
    result["full_source_url"] = sermon["full_source_url"]
    result["media"] = {
        **sermon["media"],
        "start_seconds": float(start_seconds) if isinstance(start_seconds, (int, float)) else None,
        "end_seconds": float(end_seconds) if isinstance(end_seconds, (int, float)) else None,
    }
    return result


def _paragraph_sources(
    provenance: dict[str, Any],
    knowledge: dict[str, Any],
    sermon_cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    claims = {str(item.get("claim_id") or ""): item for item in knowledge.get("claims", [])}
    steps = {
        str(item.get("evidence_step_id") or ""): item
        for item in knowledge.get("evidence_steps", [])
    }
    fragments = {
        str(item.get("fragment_id") or ""): item
        for item in knowledge.get("source_fragments", [])
    }
    documents = {
        str(item.get("source_id") or ""): item
        for item in knowledge.get("source_documents", [])
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for claim_id in provenance.get("claim_ids") or []:
        claim = claims.get(str(claim_id))
        if not claim:
            continue
        for step_id in claim.get("evidence_step_ids") or []:
            step = steps.get(str(step_id))
            if not step:
                continue
            for fragment_id in _fragment_ids(step):
                if fragment_id in seen:
                    continue
                fragment = fragments.get(fragment_id)
                if not fragment:
                    continue
                source = documents.get(str(fragment.get("source_id") or ""))
                if not source:
                    continue
                item = _source_fragment_read_model(fragment, source, sermon_cache)
                if item:
                    seen.add(fragment_id)
                    group_key = (
                        fragment.get("source_id"),
                        fragment.get("media_time"),
                        fragment.get("media_end_time"),
                        fragment.get("paragraph_key"),
                    )
                    existing = grouped.get(group_key)
                    if existing:
                        existing["fragment_ids"].append(fragment_id)
                        for excerpt in item["excerpts"]:
                            if excerpt not in existing["excerpts"]:
                                existing["excerpts"].append(excerpt)
                    else:
                        grouped[group_key] = item
                        result.append(item)
    return result


def _governed_block_end(markdown: str, comment_end: int, next_comment: int) -> int | None:
    segment = markdown[comment_end:next_comment]
    content_match = re.search(r"\S", segment)
    if not content_match:
        return None
    body_start = content_match.start()
    first_line = segment[body_start:].splitlines()[0].strip()
    if FOOTNOTE_DEFINITION_RE.match(first_line) or re.match(r"^#{1,6}\s+", first_line):
        return None
    separator = re.search(r"\r?\n[ \t]*\r?\n", segment[body_start:])
    relative_end = body_start + (separator.start() if separator else len(segment[body_start:]))
    return comment_end + relative_end


def _annotated_reader_markdown(
    markdown: str,
    knowledge: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Attach hidden-source controls without changing the manuscript's prose."""

    matches = list(PROVENANCE_COMMENT_RE.finditer(markdown))
    insertions: list[tuple[int, str]] = []
    annotations: list[dict[str, Any]] = []
    sermon_cache: dict[str, dict[str, Any]] = {}
    for index, match in enumerate(matches):
        try:
            provenance = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(provenance, dict):
            continue
        sources = _paragraph_sources(provenance, knowledge, sermon_cache)
        if not sources:
            continue
        next_comment = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        block_end = _governed_block_end(markdown, match.end(), next_comment)
        if block_end is None:
            continue
        annotation_id = f"p{len(annotations) + 1}"
        annotations.append({"annotation_id": annotation_id, "sources": sources})
        insertions.append(
            (block_end, f"\n\n[查看本段来源]({SOURCE_MARKER_PREFIX}{annotation_id})")
        )
    annotated = markdown
    for position, marker in reversed(insertions):
        annotated = annotated[:position] + marker + annotated[position:]
    annotated = PROVENANCE_COMMENT_RE.sub("", annotated)
    return annotated.strip(), annotations


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
    integrity_matches = (
        expected_manuscript_sha == current_manuscript_sha
        and expected_workflow_sha == current_workflow_sha
    )
    packet: dict[str, Any] = {}
    packet_relative_path = str(manifest.get("authoring_packet_relative_path") or "").strip()
    if packet_relative_path:
        packet_path = _safe_staging_child(packet_relative_path)
        if not packet_path.is_file():
            raise ValueError("review authoring packet is missing")
        packet = _result(_read_json(packet_path))
        integrity_matches = integrity_matches and (
            str(manifest.get("authoring_packet_file_sha256") or "") == _sha256(packet_path)
            and str(manifest.get("authoring_packet_sha256") or "")
            == str(packet.get("packet_sha256") or "")
        )
    integrity = "verified" if integrity_matches else "changed"
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
        manuscript = manuscript_path.read_text(encoding="utf-8")
        knowledge = packet.get("knowledge") if isinstance(packet.get("knowledge"), dict) else {}
        if knowledge:
            result["markdown"], result["source_annotations"] = _annotated_reader_markdown(
                manuscript,
                knowledge,
            )
        else:
            result["markdown"] = _reader_markdown(manuscript)
            result["source_annotations"] = []
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
