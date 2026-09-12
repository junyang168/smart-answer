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
    VisualSourceAttestationError,
    assert_locator_space_compatible,
    excerpt_overlaps_inline_markup,
    inline_markup_spans,
    live_script,
    project_script,
    provably_nonspoken_inline_markup,
    source_uses_body_locator_space,
    validate_visual_source_attestations,
    validate_visual_fragment_against_block,
    visual_fragment_display_text,
    visual_source_blocks,
)
from backend.pipeline.source_projection import script_from_markdown_blocks


def _body_rows() -> list[dict]:
    return [
        {"index": 10, "text": "第一句。", "start_time": 0, "end_time": 5},
        {"index": 20, "text": "第二句。", "start_time": 5, "end_time": 10},
        {"index": 30, "text": "第三句。", "start_time": 10, "end_time": 15},
    ]


def test_inline_markup_spans_separate_proven_editor_payload_from_ambiguous_quotes() -> None:
    text = (
        "教授正文。\n"
        "> ### 编辑提纲\n"
        "> 可能是朗读经文，也可能是投影片。\n"
        "<svg><rect width=\"2\"/></svg>\n"
        "<!-- editor note -->\n"
        "后续正文。"
    )

    assert {span.kind for span in inline_markup_spans(text)} == {
        "blockquote",
        "svg",
        "html_comment",
    }
    assert {span.kind for span in provably_nonspoken_inline_markup(text)} == {
        "svg",
        "html_comment",
    }
    assert excerpt_overlaps_inline_markup(text, "编辑提纲") is True
    assert excerpt_overlaps_inline_markup(text, "rect width") is True
    assert excerpt_overlaps_inline_markup(text, "教授正文") is False
    assert excerpt_overlaps_inline_markup(text, "后续正文") is False


def test_unclosed_editor_payload_fails_closed_to_the_end_of_the_row() -> None:
    text = "教授正文。\n<svg><text>编辑图形"
    spans = provably_nonspoken_inline_markup(text)
    assert [(span.kind, span.end) for span in spans] == [("svg", len(text))]


def test_svg_is_visual_source_and_is_removed_only_from_spoken_projection() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100">'
        '<ellipse cx="50" cy="50" rx="40" ry="20" stroke="#82E047"/>'
        '<text x="50" y="50">重疊</text>'
        '</svg>'
    )
    row = {"index": 17, "text": f"前一句。\n{svg}\n後一句。"}
    projection = project_script([row])

    assert projection.body_rows[0]["text"] == row["text"]
    assert svg not in projection.spoken_rows[0]["text"]
    assert "前一句。" in projection.spoken_rows[0]["text"]
    assert "後一句。" in projection.spoken_rows[0]["text"]
    assert len(projection.visual_blocks) == 1
    visual = projection.visual_blocks[0]
    assert visual.locator == "S0001/V01"
    assert visual.source_segment_index == 17
    assert visual.readable is True
    assert [fact["tag"] for fact in visual.facts] == ["svg", "ellipse", "text"]
    assert visual.facts[-1]["text"] == "重疊"
    assert projection.visual_content_sha256 is not None


def test_svg_literal_facts_preserve_text_after_a_child_element() -> None:
    visual = visual_source_blocks(
        "<svg><text>A<tspan>B</tspan>C</text></svg>",
        segment_index="S0001",
    )[0]

    assert visual.facts[1]["text"] == "A"
    assert visual.facts[2]["text"] == "B"
    assert visual.facts[2]["tail"] == "C"


def test_visual_display_label_uses_only_visible_text_and_strips_zero_width() -> None:
    fragment = {
        "source_modality": "visual",
        "visual_locator": "S0001/V01",
        "visual_facts": [
            {"tag": "style", "text": ".label { fill: red; }"},
            {"tag": "text", "text": "\u200b盟约", "tail": None},
            {"tag": "tspan", "text": "结构", "tail": "关系"},
        ],
    }

    rendered = visual_fragment_display_text(fragment)
    assert rendered == "视觉来源（非口述，S0001/V01）：盟约；结构；关系"
    assert "fill" not in rendered


def test_malformed_visual_source_is_preserved_and_marked_invalid() -> None:
    svg = "<svg><text><tspan>圖</tspan></tspan></text></svg>"
    blocks = visual_source_blocks(svg, segment_index="S0007")

    assert len(blocks) == 1
    assert blocks[0].locator == "S0007/V01"
    assert blocks[0].raw_svg == svg
    assert blocks[0].readable is False
    assert "mismatched tag" in str(blocks[0].parse_error)


@pytest.mark.parametrize(
    "svg, reason",
    [
        ("<svg><FoReIgNoBjEcT/></svg>", "unsafe SVG element"),
        ("<svg><animate attributeName=\"x\"/></svg>", "unsafe SVG element"),
        ("<svg><rect onLoad=\"alert(1)\"/></svg>", "event handler attribute"),
        ("<svg><style>@IMPORT url(https://example.test/a.css)</style></svg>", "external CSS import"),
        ("<svg><rect style=\"fill:url('https://example.test/a.svg')\"/></svg>", "external CSS url"),
    ],
)
def test_visual_source_rejects_case_insensitive_active_or_external_content(
    svg: str, reason: str
) -> None:
    block = visual_source_blocks(svg, segment_index="S0001")[0]

    assert block.readable is False
    assert reason in str(block.parse_error)


def test_visual_fragment_facts_must_be_exact_source_facts() -> None:
    block = visual_source_blocks(
        '<svg><text x="1">图</text></svg>', segment_index="S0001"
    )[0]
    fragment = {
        "source_modality": "visual",
        "visual_locator": block.locator,
        "visual_block_sha256": block.raw_sha256,
        "visual_canonical_sha256": block.canonical_sha256,
        "visual_renderer_version": "svg_literal_facts_v3_cjk_white",
        "visual_facts": [dict(block.facts[-1])],
        "verbatim_excerpt": block.raw_svg,
    }

    validate_visual_fragment_against_block(fragment, block)
    fragment["visual_facts"][0]["text"] = "被改写的图"
    with pytest.raises(ValueError, match="does not match"):
        validate_visual_fragment_against_block(fragment, block)


def test_source_without_svg_keeps_existing_body_and_spoken_identity_shape() -> None:
    projection = project_script(_body_rows())

    assert projection.spoken_rows == projection.body_rows
    assert projection.visual_blocks == ()
    assert projection.visual_content_sha256 is None


def test_inline_markup_overlap_uses_the_same_first_match_as_anchor_compilation() -> None:
    assert excerpt_overlaps_inline_markup(
        "相同句。\n> 相同句。", "相同句"
    ) is False
    assert excerpt_overlaps_inline_markup(
        "> 相同句。\n相同句。", "相同句"
    ) is True


def test_heading_marker_without_a_horizontal_title_does_not_swallow_next_line() -> None:
    text = "##\n教授正文。"
    assert excerpt_overlaps_inline_markup(text, "教授正文") is False


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


def test_shared_transcript_prompt_requires_prior_visual_attestation() -> None:
    from backend.pipeline.corpus_survey_runner import _transcript_for_prompt

    payload = {
        "script": [
            {
                "index": 1,
                "text": '教授正文。<svg><text x="1">图</text></svg>',
            }
        ]
    }
    with pytest.raises(VisualSourceAttestationError, match="without an external"):
        _transcript_for_prompt(payload)

    prompt = _transcript_for_prompt(payload, visual_source_attested=True)
    assert "教授展示或画出的视觉来源" in prompt
    assert "[visual source S0001/V01]" in prompt


def test_readable_svg_still_requires_exact_source_attestation() -> None:
    projection = project_script([
        {"index": 1, "text": '教授正文。<svg><text x="1">图</text></svg>'}
    ])
    visual = projection.visual_blocks[0]

    with pytest.raises(VisualSourceAttestationError, match="not attested"):
        validate_visual_source_attestations(projection, {})
    with pytest.raises(VisualSourceAttestationError, match="does not match"):
        validate_visual_source_attestations(
            projection, {visual.locator: "0" * 64}
        )

    validate_visual_source_attestations(
        projection, {visual.locator: visual.raw_sha256}
    )


def test_visual_source_transport_split_contains_only_its_target_units() -> None:
    source = {
        "script": [{
            "index": 1,
            "text": "第一句。<svg><text>重要图</text></svg>第二句。第三句。",
        }]
    }
    first = Section(
        index=1,
        start=0,
        end=1,
        title="",
        parent_start=0,
        parent_end=1,
        sentence_start=0,
        sentence_end=2,
    )
    second = Section(
        index=2,
        start=0,
        end=1,
        title="",
        parent_start=0,
        parent_end=1,
        sentence_start=2,
        sentence_end=4,
    )

    first_prompt = _section_prompt_body(
        source, first, section_sentences(source, first)
    )
    second_prompt = _section_prompt_body(
        source, second, section_sentences(source, second)
    )

    assert "第一句。" in first_prompt
    assert "重要图" in first_prompt
    assert "第二句。" not in first_prompt
    assert "第三句。" not in first_prompt
    assert "第一句。" not in second_prompt
    assert "<svg" not in second_prompt
    assert "重要图" not in second_prompt
    assert "第二句。第三句。" in second_prompt


@pytest.mark.parametrize(
    "svg",
    [
        "<svg><style>@import url(https://example.invalid/a.css)</style></svg>",
        "<svg><style>.a{fill:url(https://example.invalid/a.svg)}</style></svg>",
    ],
)
def test_visual_source_rejects_external_css(svg: str) -> None:
    visual = visual_source_blocks(svg, segment_index="S0001")[0]
    assert visual.readable is False
    assert "external CSS" in str(visual.parse_error)
