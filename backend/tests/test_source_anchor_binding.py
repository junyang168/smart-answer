from __future__ import annotations

import json
from pathlib import Path

from backend.pipeline.source_anchor_binding import build_anchor_binding_package


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


def test_anchor_binding_does_not_guess_non_verbatim_text(tmp_path: Path) -> None:
    _write_transcript(tmp_path)

    result, summary = build_anchor_binding_package(_package("改写后的话"), tmp_path)

    assert result["source_fragments"] == []
    assert summary["unresolved_fragments"] == 1
    assert summary["unresolved"][0]["reason"] == "paragraph_or_verbatim_mismatch"
