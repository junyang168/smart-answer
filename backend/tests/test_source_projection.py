from __future__ import annotations

from backend.pipeline.detailed_knowledge_extraction_runner import (
    _section_prompt_body,
    section_sentences,
)
from backend.pipeline.extraction_sections import Section, plan_sections
import pytest

from backend.pipeline.source_projection import (
    LOCATOR_SPACE,
    LocatorSpaceError,
    assert_locator_space_compatible,
    live_script,
    project_script,
    source_uses_body_locator_space,
)
from backend.pipeline.source_projection import script_from_markdown_blocks


def _body_rows() -> list[dict]:
    return [
        {"index": 10, "text": "第一句。", "start_time": 0, "end_time": 5},
        {"index": 20, "text": "第二句。", "start_time": 5, "end_time": 10},
        {"index": 30, "text": "第三句。", "start_time": 10, "end_time": 15},
    ]


def test_editorial_rows_never_change_spoken_body_identity_or_coordinates() -> None:
    plain = project_script(_body_rows())
    titled = project_script([
        {"index": "subtitle-a", "type": "subtitle", "text": "## 第一部分"},
        _body_rows()[0],
        {"index": "comment-a", "type": "comment", "text": "编辑备注"},
        _body_rows()[1],
        {"index": "subtitle-b", "type": "subtitle", "text": "## 第二部分"},
        _body_rows()[2],
    ])
    renamed_and_moved = project_script([
        {"index": "comment-b", "type": "comment", "text": "另一条备注"},
        {"index": "subtitle-a", "type": "subtitle", "text": "## 改名后的第一部分"},
        *_body_rows(),
    ])

    assert titled.body_rows == plain.body_rows == renamed_and_moved.body_rows
    assert titled.body_sha256 == plain.body_sha256 == renamed_and_moved.body_sha256
    assert (
        titled.spoken_text_sha256
        == plain.spoken_text_sha256
        == renamed_and_moved.spoken_text_sha256
    )
    assert titled.editorial_structure_sha256 != renamed_and_moved.editorial_structure_sha256

    same_titles_different_comments = project_script([
        {"index": "comment-z", "type": "comment", "text": "改过的编辑备注"},
        {"index": "subtitle-a", "type": "subtitle", "text": "## 第一部分"},
        _body_rows()[0],
        _body_rows()[1],
        {"index": "subtitle-b", "type": "subtitle", "text": "## 第二部分"},
        _body_rows()[2],
    ])
    assert same_titles_different_comments.editorial_structure_sha256 == (
        titled.editorial_structure_sha256
    )

    markdown_comment = project_script([
        {"index": "comment-md", "type": "comment", "text": "## 这仍是编辑备注"},
        {"index": "subtitle-a", "type": "subtitle", "text": "## 第一部分"},
        _body_rows()[0],
        _body_rows()[1],
        {"index": "subtitle-b", "type": "subtitle", "text": "## 第二部分"},
        _body_rows()[2],
    ])
    assert markdown_comment.headings == titled.headings
    assert markdown_comment.editorial_structure_sha256 == titled.editorial_structure_sha256


def test_timing_changes_anchor_binding_but_not_spoken_text_identity() -> None:
    before = project_script(_body_rows())
    changed = [dict(row) for row in _body_rows()]
    changed[0]["start_time"] = 37
    changed[0]["end_time"] = 42
    after = project_script(changed)

    assert before.spoken_text_sha256 == after.spoken_text_sha256
    assert before.body_sha256 != after.body_sha256


def test_heading_rename_preserves_topology_but_boundary_move_does_not() -> None:
    body = _body_rows()
    first = project_script([
        {"type": "subtitle", "text": "## 旧标题"},
        body[0],
        {"type": "subtitle", "text": "## 第二节"},
        *body[1:],
    ])
    renamed = project_script([
        {"type": "subtitle", "text": "## 新标题"},
        body[0],
        {"type": "subtitle", "text": "## 第二节改名"},
        *body[1:],
    ])
    moved = project_script([
        {"type": "subtitle", "text": "## 新标题"},
        *body[:2],
        {"type": "subtitle", "text": "## 第二节改名"},
        body[2],
    ])

    assert first.editorial_topology_sha256 == renamed.editorial_topology_sha256
    assert first.editorial_structure_sha256 != renamed.editorial_structure_sha256
    assert first.editorial_topology_sha256 != moved.editorial_topology_sha256


def test_legacy_empty_content_row_in_subtitle_index_namespace_is_editorial() -> None:
    body = _body_rows()
    legacy_mixed = [
        body[0],
        {
            "index": "subtitle-1765825133761-0",
            "type": "content",
            "user_id": "editor@example.org",
            "text": "",
        },
        *body[1:],
    ]

    plain = project_script(body)
    projected = project_script(legacy_mixed)

    assert projected.body_rows == plain.body_rows
    assert projected.body_sha256 == plain.body_sha256


def test_locator_space_must_be_explicit_before_body_coordinates_are_used() -> None:
    body_sha = project_script(_body_rows()).body_sha256

    with pytest.raises(LocatorSpaceError, match="explicit locator_space"):
        source_uses_body_locator_space({"source_body_sha256": body_sha})

    assert source_uses_body_locator_space({
        "locator_space": LOCATOR_SPACE,
        "source_body_sha256": body_sha,
    }) is True
    assert assert_locator_space_compatible({}, _body_rows()) is False


def test_legacy_editorial_rows_cannot_be_silently_reinterpreted() -> None:
    legacy_mixed = [
        {"index": "subtitle-a", "type": "subtitle", "text": "## 编辑标题"},
        *_body_rows(),
    ]

    with pytest.raises(LocatorSpaceError, match="re-extraction required"):
        assert_locator_space_compatible({}, legacy_mixed)


def test_markdown_headings_have_a_separate_index_namespace() -> None:
    plain = project_script(script_from_markdown_blocks(["第一段。", "第二段。"]))
    titled = project_script(
        script_from_markdown_blocks(["# 文稿", "第一段。", "## 分段", "第二段。"])
    )

    assert [row["index"] for row in titled.body_rows] == [1, 2]
    assert [row["index"] for row in plain.body_rows] == [1, 2]
    assert titled.body_sha256 == plain.body_sha256


def test_legacy_untyped_heading_remains_editorial_after_soft_deletion() -> None:
    rows = [
        {"index": "legacy-heading", "text": "## ~~旧标题~~ 新标题"},
        _body_rows()[0],
    ]
    projection = project_script(rows)
    reprojected_after_shared_loader = project_script(live_script(rows))

    assert [row["index"] for row in projection.body_rows] == [10]
    assert reprojected_after_shared_loader == projection
    assert [(row.boundary, row.level, row.title) for row in projection.headings] == [
        (0, 2, "新标题")
    ]


def test_section_boundaries_are_body_coordinates_and_headings_are_not_sentences() -> None:
    projection = project_script([
        {"index": "subtitle-a", "type": "subtitle", "text": "## 第一部分"},
        _body_rows()[0],
        {"index": "subtitle-b", "type": "subtitle", "text": "## 第二部分"},
        _body_rows()[1],
        {"index": "comment-a", "type": "comment", "text": "不进入模型"},
        _body_rows()[2],
    ])
    source = {"script": list(projection.body_rows)}
    plan = plan_sections(
        [row["text"] for row in projection.body_rows],
        headings=projection.headings,
        segment_indexes=[row["index"] for row in projection.body_rows],
    )

    assert [(row.start, row.end, row.title) for row in plan.sections] == [
        (0, 1, "第一部分"),
        (1, 3, "第二部分"),
    ]
    sentences = [
        sentence
        for section in plan.sections
        for sentence in section_sentences(source, section)
    ]
    assert [row.segment_index for row in sentences] == ["S0001", "S0002", "S0003"]
    assert [row.text for row in sentences] == ["第一句。", "第二句。", "第三句。"]


def test_extraction_prompt_omits_titles_and_anchor_binding_metadata() -> None:
    projection = project_script([
        {"index": "subtitle-a", "type": "subtitle", "text": "## 编辑标题"},
        _body_rows()[0],
    ])
    source = {"script": list(projection.body_rows)}
    section = Section(index=1, start=0, end=1, title="编辑标题")
    prompt = _section_prompt_body(
        source,
        section,
        section_sentences(source, section),
    )

    assert "编辑标题" not in prompt
    assert "source_index=subtitle-a" not in prompt
    assert "source_index=10" not in prompt
    assert "; 0-5]" not in prompt
    assert "[segment S0001]" in prompt

    renamed = _section_prompt_body(
        source,
        Section(index=1, start=0, end=1, title="另一标题"),
        section_sentences(source, section),
    )
    assert renamed == prompt


def test_default_prompt_projection_never_turns_editorial_rows_into_segments() -> None:
    from backend.pipeline.corpus_survey_runner import _transcript_for_prompt

    prompt = _transcript_for_prompt({
        "script": [
            {"index": "subtitle-a", "type": "subtitle", "text": "## 编辑标题"},
            {"index": "comment-a", "type": "comment", "text": "内部备注"},
            _body_rows()[0],
        ]
    })

    assert "[位于 S0001 之前；H2] 编辑标题" in prompt
    assert "source_index=subtitle-a" not in prompt
    assert "内部备注" not in prompt
    assert "[segment S0001; source_index=10" in prompt
