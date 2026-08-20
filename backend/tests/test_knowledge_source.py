from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.pipeline.knowledge_source import (
    live_script,
    live_text,
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


# ---------------------------------------------------------------------------
# soft deletion: what the proofreader struck through is not in the source
# ---------------------------------------------------------------------------


def test_struck_through_text_is_gone() -> None:
    assert live_text("留下的。~~刪掉的。~~也留下的。") == "留下的。\n也留下的。"


def test_a_deletion_leaves_a_boundary_rather_than_joining_its_neighbours() -> None:
    """`甲~~乙~~丙` must not become `甲丙`.

    That string was never spoken, and every `verbatim_excerpt` check in the
    extraction path asks only whether an excerpt appears contiguously in the
    segment -- so a join manufactures source text that would pass validation.
    A newline is also where `sentence_spans` breaks, so the two survivors
    cannot be read as one sentence either.
    """

    assert "甲丙" not in live_text("甲~~乙~~丙")
    assert live_text("甲~~乙~~丙") == "甲\n丙"


def test_an_unpaired_marker_deletes_nothing() -> None:
    """11 segments in the corpus carry one, and the editor shows them as text.

    A greedier rule would swallow the rest of the segment on each of them.
    """

    assert live_text("這句留著 ~~ 這句也留著。") == "這句留著 ~~ 這句也留著。"


def test_a_marker_opened_in_one_segment_does_not_delete_the_next() -> None:
    """The editor renders each segment as its own Markdown document.

    So a marker that opens in one segment and never closes there strikes
    nothing on screen. Matching across segments would delete 39,282 characters
    of the published corpus that no proofreader deleted.
    """

    rows = live_script([{"text": "第一段開始 ~~刪不掉"}, {"text": "第二段~~ 結束"}])
    assert [row["text"] for row in rows] == ["第一段開始 ~~刪不掉", "第二段~~ 結束"]


def test_a_fully_struck_segment_stays_in_place_as_an_empty_one() -> None:
    """Dropping it would renumber every segment after it.

    `S0007` is a position, and every anchor, exclusion id and section boundary
    in the claim layer resolves through it.
    """

    rows = live_script([{"text": "一。"}, {"text": "~~整段刪掉。~~"}, {"text": "三。"}])
    assert [row["text"] for row in rows] == ["一。", "\n", "三。"]
    assert len(rows) == 3


def test_the_other_fields_of_a_segment_survive() -> None:
    rows = live_script([{"index": "subtitle-1", "start_time": 12, "text": "~~刪~~留"}])
    assert rows[0]["index"] == "subtitle-1" and rows[0]["start_time"] == 12
    assert rows[0]["text"] == "\n留"
