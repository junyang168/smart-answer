"""Untimed headings must not become the preceding transcript timeline anchor."""
from copy import deepcopy
import hashlib
import json

import pytest

from backend.api.sc_api.script_delta import ScriptDelta


@pytest.fixture
def script(tmp_path):
    for folder in ("script", "script_review", "script_patched"):
        (tmp_path / folder).mkdir()
    entries = [
        {"index": f"1_{i}", "start": f"00:00:0{i},000", "end": f"00:00:0{i},500"}
        for i in range(1, 7)
    ]
    (tmp_path / "script/test.json").write_text(json.dumps({"entries": entries}))
    return tmp_path, [
        {"index": "1_2", "text": "第一段。"},
        {"index": "1_4", "text": "第二段。"},
        {"index": "1_6", "end_index": "1_6", "text": "第三段。"},
    ]


@pytest.mark.parametrize("heading", [
    {"index": "subtitle-pipeline-fddb67009a62-01", "type": "subtitle", "text": "## 標題"},
    {"index": "subtitle-legacy", "text": "## 舊標題"},
    {"index": "heading-id", "type": "subtitle", "text": "## 人工標題"},
])
def test_leading_and_interleaved_headings_preserve_transcript_timing(script, heading):
    root, rows = script
    originals = deepcopy(rows)
    headings = [dict(heading, index=heading["index"] + str(i)) for i in range(4)]
    comment = {"index": "comment-1", "type": "comment", "text": "校對註記"}
    augmented = [headings[0], rows[0], headings[1], comment, headings[2], rows[1], rows[2], headings[3]]

    ScriptDelta(str(root), "test").add_timeline(augmented)

    assert [(p["start_index"], p["start_time"], p["end_time"]) for p in rows] == [
        ("0", 0, 2), ("1_3", 3, 4), ("1_6", 6, 6)
    ]
    assert [p["text"] for p in rows] == [p["text"] for p in originals]
    assert headings == [dict(heading, index=heading["index"] + str(i)) for i in range(4)]
    assert comment == {"index": "comment-1", "type": "comment", "text": "校對註記"}


def test_editor_read_keeps_source_bytes_and_sha_with_generated_heading(script):
    root, rows = script
    rows.insert(0, {"index": "subtitle-pipeline-fddb67009a62-01", "type": "subtitle", "text": "## 新標題"})
    path = root / "script_review/test.json"
    raw = json.dumps(rows, ensure_ascii=False).encode()
    path.write_bytes(raw)

    result, sha = ScriptDelta(str(root), "test").get_review_script_with_sha()

    assert path.read_bytes() == raw
    assert sha == hashlib.sha256(raw).hexdigest()
    assert result[0] == rows[0]
    assert [p["index"] for p in result] == [p["index"] for p in rows]
    assert result[1]["start_time"] == 0


def test_unknown_spoken_row_is_not_silently_assigned_fake_timing(script):
    root, _ = script
    with pytest.raises(KeyError):
        ScriptDelta(str(root), "test").add_timeline([{"index": "missing-spoken", "text": "原文段落"}])
