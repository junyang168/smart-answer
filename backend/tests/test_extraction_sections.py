from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.detailed_knowledge_extraction import (
    AuditedSentence,
    DetailedExtractionValidationError,
    validate_sentence_audit,
)
from backend.pipeline.detailed_knowledge_extraction_runner import (
    _section_prompt_body,
    section_sentences,
)
from backend.pipeline.extraction_sections import (
    FROM_GENERATOR,
    FROM_SOURCE,
    Section,
    SectionPlan,
    breadcrumb_for,
    load_cached_plan,
    plan_sections,
    save_plan,
    sections_from_headings,
)
from backend.pipeline.knowledge_source import markdown_blocks
from backend.pipeline.source_projection import (
    LOCATOR_SPACE,
    EditorialHeading,
    project_script,
    script_from_markdown_blocks,
)


def _segments(count: int) -> list[str]:
    return [f"第{index}段的正文内容，这里写了一句足够长的话。" for index in range(count)]


# --------------------------------------------------------------------------
# Sectioning
# --------------------------------------------------------------------------


def test_sections_split_at_the_units_the_source_was_written_in() -> None:
    segments = _segments(20)
    segments[0] = "## 一、彌賽亞秘密理論"
    segments[8] = "## 二、從馬可福音現象回應"
    segments[15] = "## 三、捨己與背十字架"
    sections = sections_from_headings(segments)
    assert [(s.start, s.end) for s in sections] == [(0, 7), (7, 13), (13, 17)]
    assert [s.title for s in sections] == [
        "一、彌賽亞秘密理論", "二、從馬可福音現象回應", "三、捨己與背十字架",
    ]


def test_subheadings_do_not_start_a_section() -> None:
    """`###` is the skeleton inside a unit; every long relation crosses one."""

    segments = _segments(12)
    segments[0] = "## 一、大標題"
    segments[4] = "### 釋經"
    segments[8] = "### 神學意義"
    assert len(sections_from_headings(segments)) == 1


def test_oversized_section_uses_subheadings_to_make_two_balanced_chunks() -> None:
    segments = _segments(12)
    headings = [EditorialHeading(0, 2, "第一部分")]
    headings.extend(EditorialHeading(position, 3, f"子题 {position}") for position in (2, 4, 6, 8, 10))
    plan = plan_sections(
        segments, headings=headings,
        sentence_counts=[30] * len(segments), max_section_sentences=180,
    )
    assert [(row.start, row.end) for row in plan.sections] == [(0, 6), (6, 12)]
    assert [sum([30] * (row.end - row.start)) for row in plan.sections] == [180, 180]
    assert plan.sections[1].title == "第一部分 > 子题 6"
    assert {row["boundary_kind"] for row in plan.split_lineage} == {"h3"}


def test_adaptive_sectioning_keeps_normal_h2_section_whole() -> None:
    segments = _segments(6)
    headings = [EditorialHeading(0, 2, "第一部分"), EditorialHeading(3, 3, "子题")]
    plan = plan_sections(
        segments, headings=headings,
        sentence_counts=[20] * len(segments), max_section_sentences=180,
    )
    assert [(row.start, row.end) for row in plan.sections] == [(0, 6)]


def test_adaptive_sectioning_uses_three_chunks_only_when_two_cannot_fit() -> None:
    segments = _segments(9)
    headings = [
        EditorialHeading(0, 2, "第一部分"),
        EditorialHeading(3, 3, "子题二"),
        EditorialHeading(6, 3, "子题三"),
    ]
    plan = plan_sections(
        segments, headings=headings,
        sentence_counts=[50] * len(segments), max_section_sentences=180,
    )
    assert [(row.start, row.end) for row in plan.sections] == [(0, 3), (3, 6), (6, 9)]


def test_adaptive_sectioning_falls_back_to_spoken_row_boundaries() -> None:
    segments = _segments(8)
    plan = plan_sections(
        segments, headings=[EditorialHeading(0, 2, "第一部分")],
        sentence_counts=[30] * len(segments), max_section_sentences=180,
    )
    assert [(row.start, row.end) for row in plan.sections] == [(0, 4), (4, 8)]
    assert {row["boundary_kind"] for row in plan.split_lineage} == {"spoken_row"}


def test_adaptive_sectioning_uses_disjoint_sentence_ranges_for_one_large_row() -> None:
    plan = plan_sections(
        _segments(3), headings=[EditorialHeading(0, 2, "第一部分")],
        sentence_counts=[30, 181, 30], max_section_sentences=180,
    )
    assert [(row.sentence_start, row.sentence_end) for row in plan.sections] == [
        (0, 120),
        (120, 241),
    ]
    assert {row["boundary_kind"] for row in plan.split_lineage} == {"sentence"}


def test_sentence_range_chunks_keep_disjoint_parent_audit_ids() -> None:
    source = {
        "script": [{"index": 1, "text": "第一句。  \n第二句。第三句。第四句。"}]
    }
    plan = plan_sections(
        [source["script"][0]["text"]],
        headings=[EditorialHeading(0, 2, "第一部分")],
        sentence_counts=[4],
        max_section_sentences=2,
    )
    assert [
        [sentence.sentence_id for sentence in section_sentences(source, section)]
        for section in plan.sections
    ] == [["S0001#001", "S0001#002"], ["S0001#003", "S0001#004"]]
    first_sentences = section_sentences(source, plan.sections[0])
    prompt = _section_prompt_body(
        source,
        plan.sections[0],
        first_sentences,
    )
    assert "第一句。  \n第二句。" in prompt
    assert "第三句。" not in prompt
    assert "内部连续分片" in prompt


def test_unchanged_cap_does_not_change_section_identity() -> None:
    segments = _segments(3)
    uncapped = plan_sections(
        segments, headings=[EditorialHeading(0, 2, "第一部分")],
        sentence_counts=[20, 20, 20],
    )
    capped = plan_sections(
        segments, headings=[EditorialHeading(0, 2, "第一部分")],
        sentence_counts=[20, 20, 20], max_section_sentences=125,
    )
    assert capped is uncapped or capped == uncapped
    assert capped.identity() == uncapped.identity()


def test_title_and_origin_do_not_change_model_section_identity() -> None:
    first = SectionPlan(
        sections=(Section(index=1, start=0, end=3, title="旧标题"),),
        origin=FROM_SOURCE,
    )
    renamed = SectionPlan(
        sections=(Section(index=1, start=0, end=3, title="新标题"),),
        origin=FROM_GENERATOR,
    )
    moved = SectionPlan(
        sections=(
            Section(index=1, start=0, end=2, title="第一节"),
            Section(index=2, start=2, end=3, title="第二节"),
        ),
        origin=FROM_SOURCE,
    )

    assert first.generation_identity() == renamed.generation_identity()
    assert first.identity() != renamed.identity()
    assert first.generation_identity() != moved.generation_identity()


def test_same_boundary_keeps_legacy_deepest_title_identity() -> None:
    plan = plan_sections(
        _segments(3),
        headings=[
            EditorialHeading(0, 2, "父层标题"),
            EditorialHeading(0, 3, "内部标题"),
        ],
        level=3,
    )
    assert plan.sections[0].title == "内部标题"


def test_sections_cover_every_segment_exactly_once() -> None:
    segments = _segments(30)
    for position in (0, 7, 7, 19):
        segments[position] = f"## 標題 {position}"
    covered = [p for s in sections_from_headings(segments) for p in range(s.start, s.end)]
    assert covered == list(range(27))


def test_breadcrumb_reports_the_enclosing_heading_chain() -> None:
    segments = _segments(8)
    segments[0] = "## 二、從馬可福音現象回應"
    segments[2] = "### 釋經"
    segments[5] = "### 附錄"
    headings = project_script([{"text": text} for text in segments]).headings
    assert breadcrumb_for(headings, 2) == "二、從馬可福音現象回應 > 釋經"
    assert breadcrumb_for(headings, 3) == "二、從馬可福音現象回應 > 附錄"


# --------------------------------------------------------------------------
# Generated boundaries (the 90 transcripts with no headings)
# --------------------------------------------------------------------------


def _provider(insertions: list[dict]):
    def call(paragraphs: list[dict]) -> list[dict]:
        call.seen = paragraphs
        return insertions
    return call


def test_a_source_with_no_headings_gets_boundaries_from_the_generator() -> None:
    provider = _provider([
        {"after_index": "START", "text": "## 導論", "level": 1},
        {"after_index": "5", "text": "## 第一部分：八福", "level": 1},
        {"after_index": "9", "text": "### 時態的奧秘", "level": 2},
    ])
    plan = plan_sections(_segments(14), provider=provider)
    assert plan.origin == FROM_GENERATOR
    # level 2 is a subheading and must not open a section
    assert [(s.start, s.end) for s in plan.sections] == [(0, 6), (6, 14)]
    assert plan.sections[1].title == "第一部分：八福"


def test_a_trailing_empty_heading_does_not_suppress_the_generator() -> None:
    provider = _provider([
        {"after_index": "START", "text": "## 正确标题", "level": 1},
    ])
    segments = _segments(3)

    plan = plan_sections(
        segments,
        headings=[EditorialHeading(boundary=3, level=2, title="空标题")],
        provider=provider,
    )

    assert plan.origin == FROM_GENERATOR
    assert [section.title for section in plan.sections] == ["正确标题"]
    assert provider.seen == [
        {"index": index, "text": text} for index, text in enumerate(segments)
    ]


def test_historical_overall_and_section_titles_can_share_one_boundary() -> None:
    plan = plan_sections(
        _segments(3),
        headings=[
            EditorialHeading(boundary=0, level=2, title="旧总标题"),
            EditorialHeading(boundary=0, level=2, title="第一节标题"),
        ],
    )
    assert plan.sections[0].title == "第一节标题"


def test_a_source_that_already_has_headings_never_calls_the_generator() -> None:
    """Where the author broke the text beats a model's guess at where they would."""

    segments = _segments(12)
    segments[0] = "## 一、標題"
    segments[6] = "## 二、標題"
    called = []
    projection = project_script([{"text": text} for text in segments])
    plan = plan_sections(
        [row["text"] for row in projection.body_rows],
        headings=projection.headings,
        provider=lambda rows: called.append(rows) or [],
    )
    assert plan.origin == FROM_SOURCE
    assert called == []
    assert len(plan.sections) == 2


def test_generated_titles_are_not_written_back_into_the_source() -> None:
    """Inserting them would shift every later S-number, and only edges are needed."""

    segments = _segments(10)
    before = list(segments)
    plan_sections(segments, provider=_provider(
        [{"after_index": "4", "text": "## 新一節", "level": 1}]))
    assert segments == before


def test_plan_enters_the_fingerprint_and_survives_a_round_trip(tmp_path: Path) -> None:
    plan = plan_sections(_segments(12), provider=_provider(
        [{"after_index": "5", "text": "## 第二節", "level": 1}]))
    other = plan_sections(_segments(12), provider=_provider(
        [{"after_index": "7", "text": "## 第二節", "level": 1}]))
    assert plan.identity() != other.identity(), "different boundaries must not share a fingerprint"

    path = tmp_path / "plan.json"
    save_plan(path, plan, source_sha256="abc")
    assert load_cached_plan(path, "abc") == plan
    assert load_cached_plan(path, "different-source") is None, "a plan is bound to its source"


def test_cached_plan_is_bound_to_adaptive_policy(tmp_path: Path) -> None:
    segments = _segments(10)
    plan = plan_sections(
        segments,
        headings=[
            EditorialHeading(0, 2, "第一部分"),
            EditorialHeading(5, 3, "第二小段"),
        ],
        sentence_counts=[30] * len(segments), max_section_sentences=180,
    )
    path = tmp_path / "plan.json"
    save_plan(path, plan, source_sha256="abc")
    assert load_cached_plan(path, "abc", max_section_sentences=180) == plan
    assert load_cached_plan(path, "abc") is None
    assert load_cached_plan(path, "abc", max_section_sentences=200) is None


def test_saving_an_identical_section_plan_does_not_rewrite_the_artifact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "plan.json"
    plan = SectionPlan(
        sections=(Section(index=1, start=0, end=2, title="第一部分"),),
        origin=FROM_SOURCE,
    )
    save_plan(
        path,
        plan,
        source_sha256="abc",
        source_file_sha256="physical",
        editorial_structure_sha256="structure",
    )
    first_inode = path.stat().st_ino

    save_plan(
        path,
        plan,
        source_sha256="abc",
        source_file_sha256="physical",
        editorial_structure_sha256="structure",
    )

    assert path.stat().st_ino == first_inode


def test_cached_plan_is_invalidated_when_editorial_structure_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "plan.json"
    plan = SectionPlan(
        sections=(Section(index=1, start=0, end=2, title="旧标题"),),
        origin=FROM_SOURCE,
    )
    save_plan(
        path,
        plan,
        source_sha256="same-body",
        editorial_structure_sha256="old-structure",
    )

    assert load_cached_plan(
        path,
        "same-body",
        editorial_structure_sha256="new-structure",
    ) is None


def test_legacy_default_cache_remains_compatible_but_not_with_new_policy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-plan.json"
    path.write_text(json.dumps({
        "source_sha256": "abc",
        "origin": FROM_SOURCE,
        "sections": [vars(Section(index=1, start=0, end=4, title="第一部分"))],
    }, ensure_ascii=False), encoding="utf-8")
    assert load_cached_plan(path, "abc") is not None
    assert load_cached_plan(path, "abc", level=3) is None
    assert load_cached_plan(path, "abc", max_section_sentences=180) is None


# --------------------------------------------------------------------------
# Sentence audit
# --------------------------------------------------------------------------


def _transcript() -> dict:
    return {
        "metadata": {"title": "t"},
        "script": [
            {"index": 1, "text": "彼得宣認耶穌是基督。門徒卻不明白祂要受苦。"},
        ],
    }


def _sentences() -> list[AuditedSentence]:
    return [
        AuditedSentence("S0001#001", "S0001", "彼得宣認耶穌是基督。"),
        AuditedSentence("S0001#002", "S0001", "門徒卻不明白祂要受苦。"),
    ]


def _response(audit: list[dict], excerpt: str = "彼得宣認耶穌是基督") -> dict:
    return {
        "questions": [], "positions": [], "evidence_steps": [], "claims": [],
        "evidence_relations": [], "claim_relations": [],
        "observations": [{
            "observation_id": "OBS001", "statement": "彼得的宣認",
            "observation_type": "scripture_text", "argument_role": "background",
            "scripture_refs": [],
            "anchors": [{"segment_index": "S0001", "start_time": None,
                         "end_time": None, "verbatim_excerpt": excerpt}],
        }],
        "sentence_audit": audit,
    }


def test_audit_accepts_a_truthful_report() -> None:
    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason_code": None, "reason": ""},
        {"sentence_id": "S0001#002", "status": "not_extracted",
         "covered_by": [], "reason_code": "not_exegesis", "reason": "純過渡句"},
    ])
    validate_sentence_audit(response, _transcript(), _sentences())


def test_audit_rejects_the_semantic_dodge() -> None:
    """"已由 O7/E4 涵蓋" was Opus's real answer; the ledger cannot see meaning."""

    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason_code": None, "reason": ""},
        {"sentence_id": "S0001#002", "status": "extracted",
         "covered_by": ["OBS001"], "reason_code": None, "reason": "與前句同義，已被涵蓋"},
    ])
    with pytest.raises(DetailedExtractionValidationError, match="no anchor lands on it"):
        validate_sentence_audit(response, _transcript(), _sentences())


def test_audit_rejects_silence() -> None:
    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason_code": None, "reason": ""},
    ])
    with pytest.raises(DetailedExtractionValidationError, match="no verdict"):
        validate_sentence_audit(response, _transcript(), _sentences())


def test_audit_rejects_an_unexplained_omission() -> None:
    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason_code": None, "reason": ""},
        {"sentence_id": "S0001#002", "status": "not_extracted",
         "covered_by": [], "reason_code": "not_exegesis", "reason": "   "},
    ])
    with pytest.raises(DetailedExtractionValidationError, match="without a reason"):
        validate_sentence_audit(response, _transcript(), _sentences())


def test_audit_rejects_a_sentence_from_outside_the_section() -> None:
    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason_code": None, "reason": ""},
        {"sentence_id": "S0001#002", "status": "not_extracted",
         "covered_by": [], "reason_code": "not_exegesis", "reason": "純過渡句"},
        {"sentence_id": "S0099#001", "status": "not_extracted",
         "covered_by": [], "reason_code": "not_exegesis", "reason": "不存在"},
    ])
    with pytest.raises(DetailedExtractionValidationError, match="not in this section"):
        validate_sentence_audit(response, _transcript(), _sentences())


# --------------------------------------------------------------------------
# The ledger rides along with the extraction (#88)
# --------------------------------------------------------------------------


def test_coverage_is_recorded_on_the_package_it_scores(tmp_path: Path) -> None:
    """The scoreboard belongs in the package, not in someone's shell history."""

    from backend.pipeline.detailed_knowledge_extraction_runner import _coverage

    source = tmp_path / "final.md"
    source.write_text(
        "## 一、標題\n\n彼得宣認耶穌是基督，這一認信本身是正確的。\n", encoding="utf-8"
    )
    projection = project_script(
        script_from_markdown_blocks(markdown_blocks(source.read_text(encoding="utf-8")))
    )
    package_path = tmp_path / "pkg.json"
    package_path.write_text(json.dumps({
        "source_documents": [{
            "source_id": "SRC",
            "source_type": "notes_manuscript",
            "source_sha256": projection.body_sha256,
            "source_body_sha256": projection.body_sha256,
            "locator_space": LOCATOR_SPACE,
        }],
        "source_fragments": [{
            "fragment_id": "FR-1", "paragraph_key": "S0001",
            "verbatim_excerpt": "彼得宣認耶穌是基督",
        }],
        "evidence_steps": [{"evidence_step_id": "E001", "source_fragment_ids": ["FR-1"]}],
        "observations": [], "questions": [], "position_nodes": [],
    }, ensure_ascii=False), encoding="utf-8")

    coverage = _coverage(source, package_path)
    assert coverage["available"] is True
    assert coverage["by_category"]["prose"]["represented"] == 1
    assert coverage["by_category"]["heading"]["represented"] == 0


def test_a_broken_scoreboard_does_not_take_the_extraction_down(tmp_path: Path) -> None:
    """The package is already valid and on disk; the score is the optional part."""

    from backend.pipeline.detailed_knowledge_extraction_runner import _coverage

    source = tmp_path / "final.md"
    source.write_text("## 一、標題\n\n正文。\n", encoding="utf-8")
    broken = tmp_path / "pkg.json"
    broken.write_text(json.dumps({"source_documents": []}), encoding="utf-8")

    coverage = _coverage(source, broken)
    assert coverage["available"] is False
    assert "IndexError" in coverage["reason"] or "KeyError" in coverage["reason"]
