from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from backend.api.sermon_converter_service import (
    get_sermon_draft_path,
    get_sermon_final_path,
    get_sermon_project_metadata,
    reset_theological_audit_state,
    save_sermon_draft,
    update_transcript_coverage_audit_state,
)
from backend.api.series_manuscript_service import (
    _load_json,
    _save_json,
    _sha256_text,
    _utcnow,
    get_series_manuscript_dir,
)


class IntegratedManuscriptStatus(BaseModel):
    series_id: str
    project_id: str
    status: str = "idle"
    message: str = "Integrated manuscript has not been generated."
    application_id: Optional[str] = None
    proposal_id: Optional[str] = None
    generated_at: Optional[str] = None
    draft_path: Optional[str] = None
    local_unit_count: int = 0
    pending_patch_count: int = 0
    evidence_count: int = 0
    applied_patch_count: int = 0
    conflict_patch_count: int = 0
    patches: list[Dict[str, Any]] = Field(default_factory=list)


def _application_path(project_id: str) -> Path:
    return get_sermon_draft_path(project_id).parent / "integration_application.json"


def _normalize_markdown(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _unit_span(markdown: str, title: str) -> Optional[tuple[int, int, str]]:
    match = re.search(rf"^##\s+{re.escape(title)}\s*$", markdown, re.MULTILINE)
    if not match:
        return None
    next_heading = re.search(r"^##\s+", markdown[match.end():], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(markdown)
    while end > match.start() and markdown[end - 1].isspace():
        end -= 1
    return match.start(), end, markdown[match.start():end].strip()


def _review_patch(patch: Dict[str, Any], stored_result: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "canonical_unit_id": patch.get("canonical_unit_id"),
        "target_project_id": patch.get("target_project_id"),
        "previous_title": patch.get("previous_title"),
        "unit_title": patch.get("unit_title"),
        "change_summary": patch.get("change_summary"),
        "evidence_ids": patch.get("evidence_ids", []),
        "evidence_lineage": patch.get("evidence_lineage", []),
        "status": "safe",
        "conflict_reason": None,
    }
    if stored_result.get("status") == "applied":
        result.update(stored_result)
        return result
    project_id = str(patch.get("target_project_id") or "")
    final_path = get_sermon_final_path(project_id)
    draft_path = get_sermon_draft_path(project_id)
    if not final_path.is_file() or not draft_path.is_file():
        result.update(status="conflict", conflict_reason="Target Draft or final.md is unavailable.")
        return result
    final_text = final_path.read_text(encoding="utf-8")
    if patch.get("target_final_sha256") and _sha256_text(final_text) != patch.get("target_final_sha256"):
        result.update(status="conflict", conflict_reason="Target final.md changed after Proposal review.")
        return result
    previous_title = str(patch.get("previous_title") or "")
    final_unit = _unit_span(final_text, previous_title)
    draft_unit = _unit_span(draft_path.read_text(encoding="utf-8"), previous_title)
    if not final_unit or not draft_unit:
        result.update(status="conflict", conflict_reason="The reviewed existing unit can no longer be located.")
        return result
    if patch.get("previous_unit_sha256") and _sha256_text(final_unit[2]) != patch.get("previous_unit_sha256"):
        result.update(status="conflict", conflict_reason="The existing unit no longer matches the reviewed baseline.")
        return result
    if _normalize_markdown(final_unit[2]) != _normalize_markdown(draft_unit[2]):
        result.update(status="conflict", conflict_reason="Target Draft contains edits not present in final.md; manual merge required.")
    return result


def _patch_reviews(application: Dict[str, Any]) -> list[Dict[str, Any]]:
    stored = application.get("patch_results", {})
    return [
        _review_patch(patch, stored.get(str(patch.get("canonical_unit_id")), {}))
        for patch in application.get("pending_patches", [])
    ]


def _load_build(series_id: str, proposal_id: str) -> Dict[str, Any]:
    root = get_series_manuscript_dir(series_id)
    build = _load_json(root / "merge_runs" / proposal_id / "build.json")
    if build.get("status") != "draft_ready":
        raise ValueError("Approved Series integration build is not ready")
    return build


def _validate_patch_targets(changes: list[Dict[str, Any]]) -> None:
    checked: set[str] = set()
    for change in changes:
        project_id = change.get("target_project_id")
        expected_hash = change.get("target_final_sha256")
        if not project_id or project_id in checked:
            continue
        checked.add(project_id)
        final_path = get_sermon_final_path(project_id)
        if not final_path.is_file():
            raise ValueError(f"Patch target manuscript is unavailable: {project_id}")
        if expected_hash and _sha256_text(final_path.read_text(encoding="utf-8")) != expected_hash:
            raise ValueError(f"Patch target changed after integration review: {project_id}")


def _sort_local_changes(changes: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    order = {"new": 0, "appendix": 1}
    return sorted(
        (item for item in changes if item.get("change_type") in order),
        key=lambda item: (order[item["change_type"]], item.get("unit_title", "")),
    )


def _render_local_draft(changes: list[Dict[str, Any]]) -> str:
    local = _sort_local_changes(changes)
    return "\n\n".join(str(item.get("markdown") or "").strip() for item in local).strip() + "\n"


def _save_integration_coverage_check(
    project_id: str,
    application: Dict[str, Any],
) -> None:
    project_root = get_sermon_draft_path(project_id).parent
    source_path = project_root / "unified_source.md"
    source_hash = (
        _sha256_text(source_path.read_text(encoding="utf-8"))
        if source_path.is_file()
        else ""
    )
    dispositions = application.get("evidence_dispositions", [])
    payload = {
        "overall_status": "pass",
        "audit_kind": "integration_coverage_check",
        "findings": [],
        "missing_evidence_ids": [],
        "unanswered_question_ids": [],
        "misclassified_evidence_ids": [],
        "evidence_count": len(dispositions),
        "local_unit_count": len(application.get("local_units", [])),
        "pending_patch_count": len(application.get("pending_patches", [])),
        "application_id": application.get("application_id"),
        "proposal_id": application.get("proposal_id"),
        "checked_at": _utcnow(),
    }
    _save_json(
        project_root / "coverage_audit.json",
        {
            "source_sha256": source_hash,
            "pipeline_signature": "series-integration-coverage-v1",
            "model": "deterministic",
            "updated_at": payload["checked_at"],
            "payload": payload,
        },
    )


def get_integrated_manuscript_status(series_id: str, project_id: str) -> IntegratedManuscriptStatus:
    payload = _load_json(_application_path(project_id))
    if not payload or payload.get("series_id") != series_id:
        return IntegratedManuscriptStatus(series_id=series_id, project_id=project_id)
    patches = _patch_reviews(payload)
    return IntegratedManuscriptStatus(
        series_id=series_id,
        project_id=project_id,
        status=payload.get("status", "generated"),
        message="Integrated Project draft is ready; existing-Project patches remain review-only.",
        application_id=payload.get("application_id"),
        proposal_id=payload.get("proposal_id"),
        generated_at=payload.get("generated_at"),
        draft_path=payload.get("draft_path"),
        local_unit_count=len(payload.get("local_units", [])),
        pending_patch_count=len(payload.get("pending_patches", [])),
        evidence_count=len(payload.get("evidence_dispositions", [])),
        applied_patch_count=sum(item.get("status") == "applied" for item in patches),
        conflict_patch_count=sum(item.get("status") == "conflict" for item in patches),
        patches=patches,
    )


def _invalidate_target_review(project_id: str) -> None:
    project_root = get_sermon_draft_path(project_id).parent
    meta_path = project_root / "meta.json"
    meta = _load_json(meta_path)
    project_type = meta.get("project_type") or "sermon_note"
    if project_type == "transcript":
        update_transcript_coverage_audit_state(project_id, stale=True)
        reset_theological_audit_state(project_id)
    else:
        fidelity_path = project_root / "fidelity_audit.json"
        if fidelity_path.exists():
            fidelity_path.unlink()
    meta = _load_json(meta_path)
    meta["audit_passed"] = False
    _save_json(meta_path, meta)


def _save_target_patch_coverage_check(
    project_id: str,
    patches: list[Dict[str, Any]],
    application: Dict[str, Any],
) -> bool:
    """Certify a transcript target only when applied patches are its sole Draft changes."""
    project_root = get_sermon_draft_path(project_id).parent
    meta_path = project_root / "meta.json"
    meta = _load_json(meta_path)
    if meta.get("project_type") != "transcript":
        return False

    final_path = get_sermon_final_path(project_id)
    draft_path = get_sermon_draft_path(project_id)
    if not final_path.is_file() or not draft_path.is_file():
        return False
    final_text = final_path.read_text(encoding="utf-8")
    draft_text = draft_path.read_text(encoding="utf-8")
    reconstructed = draft_text
    for patch in patches:
        applied_unit = _unit_span(reconstructed, str(patch.get("unit_title") or ""))
        baseline_unit = _unit_span(final_text, str(patch.get("previous_title") or ""))
        expected_markdown = str(patch.get("markdown") or "").strip()
        if not applied_unit or not baseline_unit:
            return False
        if _normalize_markdown(applied_unit[2]) != _normalize_markdown(expected_markdown):
            return False
        reconstructed = (
            reconstructed[: applied_unit[0]]
            + baseline_unit[2]
            + reconstructed[applied_unit[1] :]
        )
    if _normalize_markdown(reconstructed) != _normalize_markdown(final_text):
        return False

    evidence_ids = sorted(
        {
            str(evidence_id)
            for patch in patches
            for evidence_id in patch.get("evidence_ids", [])
            if evidence_id
        }
    )
    checked_at = _utcnow()
    payload = {
        "overall_status": "pass",
        "audit_kind": "integration_patch_coverage_check",
        "findings": [],
        "missing_evidence_ids": [],
        "unanswered_question_ids": [],
        "misclassified_evidence_ids": [],
        "application_id": application.get("application_id"),
        "proposal_id": application.get("proposal_id"),
        "source_project_id": application.get("project_id"),
        "target_project_id": project_id,
        "patch_count": len(patches),
        "evidence_ids": evidence_ids,
        "target_final_sha256": _sha256_text(final_text),
        "target_draft_sha256": _sha256_text(draft_text),
        "checked_at": checked_at,
    }
    _save_json(
        project_root / "coverage_audit.json",
        {
            "source_sha256": payload["target_final_sha256"],
            "pipeline_signature": "series-integration-patch-coverage-v1",
            "model": "deterministic",
            "updated_at": checked_at,
            "payload": payload,
        },
    )
    update_transcript_coverage_audit_state(
        project_id,
        stale=False,
        overall_status="pass",
    )
    reset_theological_audit_state(project_id)
    meta = _load_json(meta_path)
    meta["theological_review_stale"] = True
    _save_json(meta_path, meta)
    return True


def _prepare_target_review(
    project_id: str,
    patches: list[Dict[str, Any]],
    application: Dict[str, Any],
) -> None:
    if _save_target_patch_coverage_check(project_id, patches, application):
        return
    _invalidate_target_review(project_id)


def apply_safe_integration_patches(
    series_id: str,
    project_id: str,
    application_id: str,
) -> IntegratedManuscriptStatus:
    path = _application_path(project_id)
    application = _load_json(path)
    if (
        not application
        or application.get("series_id") != series_id
        or application.get("application_id") != application_id
    ):
        raise ValueError("Integration Application was not found")
    reviews = _patch_reviews(application)
    review_by_id = {item["canonical_unit_id"]: item for item in reviews}
    patch_results = application.setdefault("patch_results", {})
    by_project: Dict[str, list[Dict[str, Any]]] = {}
    for patch in application.get("pending_patches", []):
        review = review_by_id.get(patch.get("canonical_unit_id"), {})
        if review.get("status") == "safe":
            by_project.setdefault(str(patch["target_project_id"]), []).append(patch)

    applied_at = _utcnow()
    for target_project_id, patches in by_project.items():
        draft_path = get_sermon_draft_path(target_project_id)
        draft = draft_path.read_text(encoding="utf-8")
        for patch in patches:
            span = _unit_span(draft, str(patch.get("previous_title") or ""))
            if not span:
                raise ValueError(f"Patch target disappeared during apply: {target_project_id}")
            replacement = str(patch.get("markdown") or "").strip()
            draft = draft[:span[0]] + replacement + draft[span[1]:]
            patch_results[str(patch["canonical_unit_id"])] = {
                "status": "applied",
                "applied_at": applied_at,
                "target_project_id": target_project_id,
            }
        save_sermon_draft(target_project_id, draft.strip() + "\n")

    applied_by_project: Dict[str, list[Dict[str, Any]]] = {}
    for patch in application.get("pending_patches", []):
        result = patch_results.get(str(patch.get("canonical_unit_id")), {})
        if result.get("status") == "applied":
            applied_by_project.setdefault(str(patch["target_project_id"]), []).append(patch)
    for target_project_id, patches in applied_by_project.items():
        _prepare_target_review(target_project_id, patches, application)

    application["patch_application_status"] = (
        "all_applied"
        if len(patch_results) == len(application.get("pending_patches", []))
        else "partially_applied"
    )
    application["patches_updated_at"] = applied_at
    _save_json(path, application)
    root = get_series_manuscript_dir(series_id)
    _save_json(root / "applications" / application_id / "application.json", application)
    return get_integrated_manuscript_status(series_id, project_id)


def materialize_integrated_manuscript(
    series_id: str,
    project_id: str,
    proposal_id: str,
) -> IntegratedManuscriptStatus:
    project = get_sermon_project_metadata(project_id)
    if not project or project.project_type != "transcript":
        raise ValueError("Integrated manuscript generation requires a transcript Project")
    if project.series_id != series_id:
        raise ValueError("Project is not assigned to the selected Series")

    build = _load_build(series_id, proposal_id)
    if build.get("project_id") != project_id or build.get("proposal_id") != proposal_id:
        raise ValueError("Series integration build does not match this Project")
    changes = build.get("integration_changes", [])
    if not changes:
        raise ValueError("Series integration build has no reviewable changes")
    _validate_patch_targets(changes)

    root = get_series_manuscript_dir(series_id)
    registry = _load_json(root / "evidence_registry.json")
    dispositions = registry.get("evidence", [])
    evidence_ids = [item.get("evidence_id") for item in dispositions]
    if len(evidence_ids) != len(set(evidence_ids)) or any(not item for item in evidence_ids):
        raise ValueError("Series evidence registry is incomplete or contains duplicate assignments")

    draft = _render_local_draft(changes)
    if not draft.strip():
        raise ValueError("Integration contains no new Project-local manuscript units")
    draft_path = get_sermon_draft_path(project_id)
    existing_application = _load_json(_application_path(project_id))
    if draft_path.is_file() and draft_path.read_text(encoding="utf-8").strip():
        expected_existing_hash = existing_application.get("draft_sha256")
        actual_existing_hash = _sha256_text(draft_path.read_text(encoding="utf-8"))
        if not expected_existing_hash or actual_existing_hash != expected_existing_hash:
            raise ValueError("Current Project draft has human edits; refusing to overwrite it")

    application_id = str(existing_application.get("application_id") or uuid.uuid4())
    generated_at = _utcnow()
    local_units = _sort_local_changes(changes)
    pending_patches = [item for item in changes if item.get("change_type") == "updated"]
    application = {
        "schema_version": 1,
        "application_id": application_id,
        "series_id": series_id,
        "project_id": project_id,
        "proposal_id": proposal_id,
        "status": "draft_generated_pending_patch_review",
        "generated_at": generated_at,
        "draft_path": str(draft_path),
        "draft_sha256": _sha256_text(draft),
        "local_units": local_units,
        "pending_patches": pending_patches,
        "evidence_dispositions": dispositions,
    }

    # Save lineage before chunking so transcript draft chunks inherit the
    # integrated unit evidence IDs rather than the original standalone plan.
    _save_json(_application_path(project_id), application)
    save_sermon_draft(project_id, draft)
    _save_integration_coverage_check(project_id, application)
    update_transcript_coverage_audit_state(
        project_id,
        stale=False,
        overall_status="pass",
    )
    reset_theological_audit_state(project_id)

    application_root = root / "applications" / application_id
    _save_json(application_root / "application.json", application)
    patches_root = application_root / "patches"
    patches_root.mkdir(parents=True, exist_ok=True)
    for patch in pending_patches:
        patch_path = patches_root / f"{patch['canonical_unit_id']}.md"
        patch_path.write_text(str(patch.get("markdown") or "").strip() + "\n", encoding="utf-8")

    return get_integrated_manuscript_status(series_id, project_id)
