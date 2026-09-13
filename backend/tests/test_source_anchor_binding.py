from __future__ import annotations

import json
from pathlib import Path

from backend.pipeline.source_anchor_binding import build_anchor_binding_package
from backend.pipeline.source_projection import LOCATOR_SPACE, project_script


def _package(excerpt: str = "教授的原话") -> dict:
    return {
        "source_documents": [
            {
                "source_id": "SRC-1",
                "source_type": "sermon_transcript",
                "transcript_id": "SERMON-1",
                "title": "测试讲道",
            }
        ],
        "source_fragments": [
            {
                "fragment_id": "FR-1",
                "source_id": "SRC-1",
                "paragraph_key": "7",
                "verbatim_excerpt": excerpt,
                "anchor_state": "unresolved",
            }
        ],
    }


def _write_transcript(root: Path) -> None:
    (root / "SERMON-1.json").write_text(
        json.dumps(
            {
                "metadata": {"title": "测试讲道"},
                "script": [{"index": 7, "text": "这是教授的原话，用来核对。"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_anchor_binding_records_source_and_excerpt_hashes(tmp_path: Path) -> None:
    _write_transcript(tmp_path)

    result, summary = build_anchor_binding_package(_package(), tmp_path)

    assert summary["bound_fragments"] == 1
    assert summary["unresolved_fragments"] == 0
    fragment = result["source_fragments"][0]
    assert fragment["anchor_state"] == "source_version_bound"
    assert fragment["source_sha256"]
    assert fragment["paragraph_text_sha256"]
    assert fragment["verbatim_excerpt_sha256"]


def test_anchor_binding_accepts_top_level_script_array(tmp_path: Path) -> None:
    (tmp_path / "SERMON-1.json").write_text(
        json.dumps(
            [{"index": 7, "text": "这是教授的原话，用来核对。"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result, summary = build_anchor_binding_package(_package(), tmp_path)

    assert summary["bound_fragments"] == 1
    assert summary["unresolved_fragments"] == 0
    assert result["source_documents"][0]["title"] == "测试讲道"


def test_anchor_binding_does_not_guess_non_verbatim_text(tmp_path: Path) -> None:
    _write_transcript(tmp_path)

    result, summary = build_anchor_binding_package(_package("改写后的话"), tmp_path)

    assert result["source_fragments"] == []
    assert summary["unresolved_fragments"] == 1
    assert summary["unresolved"][0]["reason"] == "paragraph_or_verbatim_mismatch"


def test_anchor_binding_cannot_relabel_legacy_physical_locators_as_body_locators(
    tmp_path: Path,
) -> None:
    (tmp_path / "SERMON-1.json").write_text(
        json.dumps({
            "metadata": {"title": "测试讲道"},
            "script": [
                {"index": "subtitle-1", "type": "subtitle", "text": "## 编辑标题"},
                {"index": 7, "text": "这是教授的原话，用来核对。"},
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    result, summary = build_anchor_binding_package(_package(), tmp_path)

    assert result["source_documents"] == []
    assert result["source_fragments"] == []
    assert summary["unresolved"] == [{
        "fragment_id": "FR-1",
        "reason": "legacy_locator_space_requires_reextraction",
    }]


def test_transcript_cache_does_not_lend_one_alias_locator_semantics_to_another(
    tmp_path: Path,
) -> None:
    rows = [
        {"index": "subtitle-1", "type": "subtitle", "text": "## 编辑标题"},
        {"index": 7, "text": "这是教授的原话，用来核对。"},
    ]
    (tmp_path / "SERMON-1.json").write_text(
        json.dumps({"metadata": {"title": "测试讲道"}, "script": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    body_sha = project_script(rows).body_sha256
    package = {
        "source_documents": [
            {
                "source_id": "SRC-NEW",
                "source_type": "sermon_transcript",
                "transcript_id": "SERMON-1",
                "source_sha256": body_sha,
                "source_body_sha256": body_sha,
                "locator_space": LOCATOR_SPACE,
            },
            {
                "source_id": "SRC-LEGACY",
                "source_type": "legacy_sermon_transcript",
                "transcript_id": "SERMON-1",
            },
        ],
        "source_fragments": [
            {
                "fragment_id": "FR-NEW",
                "source_id": "SRC-NEW",
                "paragraph_key": "7",
                "verbatim_excerpt": "教授的原话",
                "anchor_state": "unresolved",
            },
            {
                "fragment_id": "FR-LEGACY",
                "source_id": "SRC-LEGACY",
                "paragraph_key": "7",
                "verbatim_excerpt": "教授的原话",
                "anchor_state": "unresolved",
            },
        ],
    }

    result, summary = build_anchor_binding_package(package, tmp_path)

    assert [row["source_id"] for row in result["source_documents"]] == ["SRC-NEW"]
    assert [row["fragment_id"] for row in result["source_fragments"]] == ["FR-NEW"]
    assert summary["unresolved"] == [{
        "fragment_id": "FR-LEGACY",
        "reason": "legacy_locator_space_requires_reextraction",
    }]
