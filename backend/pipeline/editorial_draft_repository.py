"""Publish one manifest-bound editorial draft into ``DATA_BASE_DIR/wang_repository``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any


SAFE_DRAFT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def repository_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    data_base_dir = os.getenv("DATA_BASE_DIR")
    if not data_base_dir:
        raise RuntimeError("DATA_BASE_DIR is required")
    return (Path(data_base_dir) / "wang_repository").resolve()


def _bound_paths(draft: dict[str, Any]) -> list[str]:
    config = draft.get("audit_config") or {}
    values = [
        draft.get("relative_path"),
        draft.get("presentation_package_path"),
        config.get("knowledge_snapshot_path"),
        config.get("audit_output_path"),
        config.get("editorial_review_path"),
        config.get("publication_decision_path"),
    ]
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_bound_file(source_root: Path, relative: str, *, label: str) -> Path:
    source = (source_root / relative).resolve()
    try:
        source.relative_to(source_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes manifest directory: {relative}") from exc
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def _validate_publication_gates(
    source_root: Path,
    draft: dict[str, Any],
    draft_id: str,
) -> None:
    config = draft.get("audit_config") or {}
    manuscript_relative = str(draft.get("relative_path") or "").strip()
    audit_relative = str(config.get("audit_output_path") or "").strip()
    decision_relative = str(config.get("publication_decision_path") or "").strip()
    review_relative = str(config.get("editorial_review_path") or "").strip()
    if not manuscript_relative or not audit_relative or not review_relative or not decision_relative:
        raise ValueError(
            "publication requires manuscript, audit, editorial review, and publication decision paths"
        )

    manuscript_path = _resolve_bound_file(
        source_root, manuscript_relative, label="manuscript"
    )
    audit_path = _resolve_bound_file(source_root, audit_relative, label="audit")
    decision_path = _resolve_bound_file(
        source_root, decision_relative, label="publication decision"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("draft_id") != draft_id:
        raise ValueError("audit draft_id does not match requested draft")
    if audit.get("status") not in {"pass", "pass_with_warnings"}:
        raise ValueError(f"audit is not publishable: {audit.get('status')}")
    if int((audit.get("summary") or {}).get("error_total", -1)) != 0:
        raise ValueError("audit contains errors")
    manuscript_sha256 = _sha256(manuscript_path)
    if (audit.get("fingerprint") or {}).get("draft_sha256") != manuscript_sha256:
        raise ValueError("audit fingerprint does not match manuscript")

    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if decision.get("draft_id") != draft_id or decision.get("decision") != "approved":
        raise ValueError("publication decision is not approved for this draft")
    if decision.get("manuscript_sha256") != manuscript_sha256:
        raise ValueError("publication decision does not match manuscript")
    if decision.get("technical_audit_sha256") != _sha256(audit_path):
        raise ValueError("publication decision does not match technical audit")

    if decision.get("editorial_review_path") != review_relative:
        raise ValueError("publication decision declares a different editorial review")
    review_path = _resolve_bound_file(
        source_root, review_relative, label="editorial review"
    )
    if decision.get("editorial_review_sha256") != _sha256(review_path):
        raise ValueError("publication decision does not match editorial review")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    reviewed_sha256 = review.get("reviewed_draft_sha256") or review.get("manuscript_sha256")
    if reviewed_sha256 != manuscript_sha256 or not review.get("passed"):
        raise ValueError("editorial review is not a passing review of this manuscript")


def publish_editorial_draft(
    manifest_path: Path,
    draft_id: str,
    *,
    destination_root: Path | None = None,
) -> dict[str, Any]:
    """Copy only a draft's declared runtime artifacts to the shared data directory."""
    if not SAFE_DRAFT_ID.fullmatch(draft_id):
        raise ValueError(f"unsafe draft_id: {draft_id}")

    manifest_path = manifest_path.resolve()
    source_root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    draft = next(
        (item for item in manifest.get("drafts", []) if str(item.get("draft_id")) == draft_id),
        None,
    )
    if not draft:
        raise ValueError(f"draft not found in manifest: {draft_id}")

    _validate_publication_gates(source_root, draft, draft_id)

    destination = repository_root(destination_root) / "editorial_drafts" / draft_id
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for relative in _bound_paths(draft):
        source = _resolve_bound_file(source_root, relative, label="artifact")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative)

    published_manifest = {**manifest, "drafts": [draft]}
    target_manifest = destination / "editorial-draft-manifest.json"
    target_manifest.write_text(
        json.dumps(published_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "draft_id": draft_id,
        "destination": str(destination),
        "manifest": str(target_manifest),
        "copied_artifacts": copied,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--repository-root", type=Path)
    args = parser.parse_args()
    result = publish_editorial_draft(
        args.manifest,
        args.draft_id,
        destination_root=args.repository_root,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
