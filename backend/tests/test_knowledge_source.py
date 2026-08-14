from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.pipeline.knowledge_source import (
    load_knowledge_source_document,
    load_publication_readiness,
    load_source_manifest,
    markdown_blocks,
    markdown_source_document,
)


def test_markdown_blocks_are_stable_and_verbatim() -> None:
    source = "# 标题\n\n第一段。\n仍是第一段。\n\n> 引文\n\n- 一\n- 二\n"
    assert markdown_blocks(source) == [
        "# 标题",
        "第一段。\n仍是第一段。",
        "> 引文",
        "- 一\n- 二",
    ]


def test_markdown_source_document_binds_lineage(tmp_path: Path) -> None:
    path = tmp_path / "final.md"
    path.write_text("# 十六章\n\n正文。\n", encoding="utf-8")
    descriptor = {
        "source_id": "notes_manuscript:m16",
        "source_type": "notes_manuscript",
        "source_path": str(path),
        "title": "第十六章",
        "lineage": {"transformation": "notes_to_manuscript"},
    }
    payload, raw, resolved = markdown_source_document(descriptor)
    assert resolved == path
    assert raw == path.read_bytes()
    assert payload["metadata"]["lineage"]["transformation"] == "notes_to_manuscript"
    assert payload["script"][1]["text"] == "正文。"
    assert payload["script"][1]["start_time"] is None


def test_source_manifest_rejects_hash_drift(tmp_path: Path) -> None:
    source_path = tmp_path / "final.md"
    source_path.write_text("原文", encoding="utf-8")
    manifest_path = tmp_path / "sources.json"
    manifest_path.write_text(json.dumps({"sources": [{
        "source_id": "notes:one",
        "source_type": "notes_manuscript",
        "source_path": str(source_path),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }]}), encoding="utf-8")
    assert load_source_manifest(manifest_path)[0]["source_id"] == "notes:one"
    source_path.write_text("改过", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_source_manifest(manifest_path)


def test_publication_readiness_requires_four_tier_enum(tmp_path: Path) -> None:
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps({"units": [{
        "passage": "Matthew 16:28",
        "decision": "brief_note_only",
        "reason": "材料只够短注",
    }]}), encoding="utf-8")
    assert load_publication_readiness(path)["units"][0]["decision"] == "brief_note_only"
    path.write_text(json.dumps({"units": [{
        "passage": "Matthew 16:28",
        "decision": "can_extract",
        "reason": "含糊",
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid publication readiness decision"):
        load_publication_readiness(path)


def test_load_knowledge_source_document_resolves_notes_and_checks_hash(tmp_path: Path) -> None:
    path = tmp_path / "final.md"
    path.write_text("# 十六章\n\n正文。\n", encoding="utf-8")
    source = {
        "source_id": "notes_manuscript:m16",
        "source_type": "notes_manuscript",
        "source_path": str(path),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    payload, raw, resolved = load_knowledge_source_document(source, [])
    assert resolved == path
    assert raw == path.read_bytes()
    assert payload["script"][1]["text"] == "正文。"
    source["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source hash mismatch"):
        load_knowledge_source_document(source, [])


def test_load_knowledge_source_document_normalizes_review_array(tmp_path: Path) -> None:
    transcript_dir = tmp_path / "script_review"
    transcript_dir.mkdir()
    transcript_path = transcript_dir / "reviewed-sermon.json"
    transcript_path.write_text(
        json.dumps(
            [
                {
                    "index": 1,
                    "start_time": 0,
                    "end_time": 12,
                    "text": "逐字稿內容",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw = transcript_path.read_bytes()
    source = {
        "source_id": "SRC-reviewed-sermon",
        "source_type": "sermon_transcript",
        "transcript_id": "reviewed-sermon",
        "title": "人工審閱逐字稿",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
    }

    payload, loaded_raw, resolved = load_knowledge_source_document(
        source, [transcript_dir]
    )

    assert resolved == transcript_path
    assert loaded_raw == raw
    assert payload["metadata"]["title"] == "人工審閱逐字稿"
    assert payload["metadata"]["status"] == "reviewed"
    assert payload["script"][0]["text"] == "逐字稿內容"
