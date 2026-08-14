from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.editorial_draft_repository import publish_editorial_draft


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_publish_editorial_draft_copies_only_manifest_bound_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write(source / "draft.md", "# 正文\n")
    _write(source / "presentation.json", "{}")
    _write(source / "audit.json", "{}")
    _write(source / "private-generation.json", "{}")
    manifest = {
        "schema_version": "editorial-draft-manifest.v1",
        "drafts": [
            {
                "draft_id": "DRAFT-1",
                "relative_path": "draft.md",
                "presentation_package_path": "presentation.json",
                "audit_config": {
                    "knowledge_snapshot_path": "presentation.json",
                    "audit_output_path": "audit.json",
                },
            }
        ],
    }
    manifest_path = source / "editorial-draft-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = publish_editorial_draft(
        manifest_path,
        "DRAFT-1",
        destination_root=tmp_path / "wang_repository",
    )

    destination = Path(result["destination"])
    assert (destination / "draft.md").read_text(encoding="utf-8") == "# 正文\n"
    assert (destination / "presentation.json").is_file()
    assert (destination / "audit.json").is_file()
    assert not (destination / "private-generation.json").exists()
    published = json.loads(
        (destination / "editorial-draft-manifest.json").read_text(encoding="utf-8")
    )
    assert [item["draft_id"] for item in published["drafts"]] == ["DRAFT-1"]


def test_publish_editorial_draft_rejects_path_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest_path = source / "editorial-draft-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "drafts": [
                    {"draft_id": "DRAFT-1", "relative_path": "../outside.md"}
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes manifest directory"):
        publish_editorial_draft(
            manifest_path,
            "DRAFT-1",
            destination_root=tmp_path / "repository",
        )
