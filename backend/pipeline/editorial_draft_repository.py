"""Publish one manifest-bound editorial draft into ``DATA_BASE_DIR/wang_repository``."""

from __future__ import annotations

import argparse
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
    ]
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


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

    destination = repository_root(destination_root) / "editorial_drafts" / draft_id
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for relative in _bound_paths(draft):
        source = (source_root / relative).resolve()
        try:
            source.relative_to(source_root)
        except ValueError as exc:
            raise ValueError(f"artifact escapes manifest directory: {relative}") from exc
        if not source.is_file():
            raise FileNotFoundError(source)
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
