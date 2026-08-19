from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.detailed_knowledge_extraction import (
    AuditedSentence,
    DetailedExtractionValidationError,
    validate_sentence_audit,
)
from backend.pipeline.extraction_sections import (
    FROM_GENERATOR,
    FROM_SOURCE,
    Section,
    breadcrumb_for,
    load_cached_plan,
    plan_sections,
    save_plan,
    sections_from_headings,
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
    assert [(s.start, s.end) for s in sections] == [(0, 8), (8, 15), (15, 20)]
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


def test_sections_cover_every_segment_exactly_once() -> None:
    segments = _segments(30)
    for position in (0, 7, 7, 19):
        segments[position] = f"## 標題 {position}"
    covered = [p for s in sections_from_headings(segments) for p in range(s.start, s.end)]
    assert covered == list(range(30))


def test_breadcrumb_reports_the_enclosing_heading_chain() -> None:
    segments = _segments(8)
    segments[0] = "## 二、從馬可福音現象回應"
    segments[2] = "### 釋經"
    segments[5] = "### 附錄"
    assert breadcrumb_for(segments, 4) == "二、從馬可福音現象回應 > 釋經"
    assert breadcrumb_for(segments, 6) == "二、從馬可福音現象回應 > 附錄"


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


def test_a_source_that_already_has_headings_never_calls_the_generator() -> None:
    """Where the author broke the text beats a model's guess at where they would."""

    segments = _segments(12)
    segments[0] = "## 一、標題"
    segments[6] = "## 二、標題"
    called = []
    plan = plan_sections(segments, provider=lambda rows: called.append(rows) or [])
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
         "covered_by": ["OBS001"], "reason": ""},
        {"sentence_id": "S0001#002", "status": "not_extracted",
         "covered_by": [], "reason": "純過渡句"},
    ])
    validate_sentence_audit(response, _transcript(), _sentences())


def test_audit_rejects_the_semantic_dodge() -> None:
    """"已由 O7/E4 涵蓋" was Opus's real answer; the ledger cannot see meaning."""

    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason": ""},
        {"sentence_id": "S0001#002", "status": "extracted",
         "covered_by": ["OBS001"], "reason": "與前句同義，已被涵蓋"},
    ])
    with pytest.raises(DetailedExtractionValidationError, match="no anchor lands on it"):
        validate_sentence_audit(response, _transcript(), _sentences())


def test_audit_rejects_silence() -> None:
    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason": ""},
    ])
    with pytest.raises(DetailedExtractionValidationError, match="no verdict"):
        validate_sentence_audit(response, _transcript(), _sentences())


def test_audit_rejects_an_unexplained_omission() -> None:
    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason": ""},
        {"sentence_id": "S0001#002", "status": "not_extracted",
         "covered_by": [], "reason": "   "},
    ])
    with pytest.raises(DetailedExtractionValidationError, match="without a reason"):
        validate_sentence_audit(response, _transcript(), _sentences())


def test_audit_rejects_a_sentence_from_outside_the_section() -> None:
    response = _response([
        {"sentence_id": "S0001#001", "status": "extracted",
         "covered_by": ["OBS001"], "reason": ""},
        {"sentence_id": "S0001#002", "status": "not_extracted",
         "covered_by": [], "reason": "純過渡句"},
        {"sentence_id": "S0099#001", "status": "not_extracted",
         "covered_by": [], "reason": "不存在"},
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
    package_path = tmp_path / "pkg.json"
    package_path.write_text(json.dumps({
        "source_documents": [{"source_id": "SRC"}],
        "source_fragments": [{
            "fragment_id": "FR-1", "paragraph_key": "S0002",
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
