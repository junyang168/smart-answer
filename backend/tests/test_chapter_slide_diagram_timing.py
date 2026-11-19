import json

import pytest

from backend.pipeline import chapter_exposition, chapter_slide_eval, chapter_slides, claim_flash_common
from backend.pipeline import chapter_slide_diagram_timing as timing


def source():
    return [{"id": "S0001", "start": 0, "text": "登山寶訓的背景。"},
            {"id": "S0002", "start": 7, "text": "登山寶訓可以分為兩大段。"},
            {"id": "S0003", "start": 12, "text": "第一段談八福。"},
            {"id": "S0004", "start": 19, "text": "下一段只講禱告。"}]


def candidate():
    chart = {"sha256": "C", "title": "登山寶訓的文學結構", "origin": "整理", "kind": "editorial",
             "svg": "<svg><text>八福</text><text>門徒使命</text></svg>"}
    return {"slides": [{"first": "S0001", "last": "S0003", "title": "總結", "points": [], "diagram_ref": "C"},
                       {"first": "S0004", "last": "S0004", "title": "禱告", "points": [], "diagram_ref": "C"}],
            "claim_context": {"claims": [{"claim_id": "CL1", "diagrams": [chart], "stated": "S0003"},
                                         {"claim_id": "CL2", "diagrams": [chart], "stated": "S0004"}]}}


def test_diagram_starts_with_exposition_and_is_hidden_when_slide_explains_only_part():
    record, lines = candidate(), source()
    calls = []

    def ask(state, questions):
        ids = list(state["這一張投影片的依序原文"])
        calls.append(ids)
        assert state["圖中文字"] == ["八福", "門徒使命"]
        assert set(questions) == set(ids) | {"scope"}
        return {"scope": "whole" if "S0002" in ids else "part",
                **{sid: "explains" if sid in {"S0002", "S0003"} else "other" for sid in ids}}

    timings, issues = timing.resolve(record, lines, ask)
    assert issues == []
    assert calls == [["S0001", "S0002", "S0003"], ["S0004"]]
    assert timings == {"S0001": {"sha256": "C", "sentence": "S0002"},
                       "S0004": {"sha256": "C", "sentence": None}}

    assert chapter_slides.timed_diagrams(record["slides"], lines, record["claim_context"])["S0001"]["at"] == 12
    projected = chapter_slides.timed_diagrams(record["slides"], lines, record["claim_context"], timings)
    assert list(projected) == ["S0001"]
    assert projected["S0001"]["at"] == 7
    timings["S0001"]["sentence"] = "S0004"
    with pytest.raises(ValueError, match="與本張原文不符"):
        chapter_slides.timed_diagrams(record["slides"], lines, record["claim_context"], timings)


def test_uncertain_diagram_start_does_not_use_the_claim_anchor():
    record = candidate()
    record["slides"].pop()

    def ask(state, questions):
        return {key: "uncertain" for key in questions}

    timings, issues = timing.resolve(record, source(), ask)
    assert timings == {}
    assert len(issues) == 1 and "無法確認" in issues[0]


def test_diagram_timing_updates_existing_record_and_clears_stale_assignments(tmp_path, monkeypatch):
    src = tmp_path / "selection.json"
    src.write_text(json.dumps({
        "pieces": [{"id": "U", "start": 0, "end": 40, "text": "".join(line["text"] for line in source())}],
        "decisions": [{"id": "U", "keep": True}],
    }))
    record = {**candidate(), "decisions_sha256": chapter_exposition.file_sha256(src)}
    dest = tmp_path / "slides.json"
    dest.write_text(json.dumps(record))

    def jev(root, port, state, questions):
        return {"scope": "whole", **{sid: "explains" for sid in questions if sid != "scope"}}

    monkeypatch.setattr(claim_flash_common, "jev", jev)
    assert timing.check_record(dest, src, tmp_path / "raw-calls") == []
    saved = json.loads(dest.read_text())
    assert saved["slides"] == record["slides"]
    assert saved["diagram_timing"]["S0001"]["sentence"] == "S0001"

    for slide in saved["slides"]:
        slide.pop("diagram_ref")
    dest.write_text(json.dumps(saved))
    assert timing.check_record(dest, src, tmp_path / "raw-calls") == []
    assert json.loads(dest.read_text())["diagram_timing"] == {}


def test_diagram_span_is_planned_before_the_writer_splits_slides():
    lines = source()
    calls = []

    def ask(state, questions):
        ids = list(state["相關論證的依序原文"])
        calls.append(ids)
        return {"scope": "whole", "S0001": "outside", "S0002": "start",
                "S0003": "inside", "S0004": "outside"}

    context = candidate()["claim_context"]
    spans, problems = timing.plan_spans(context, lines, ask)
    assert problems == [] and calls == [[line["id"] for line in lines]]
    assert spans == [{"sha256": "C", "first": "S0002", "last": "S0003"}]

    split = [{"first": "S0001", "last": "S0002", "diagram_ref": "C"},
             {"first": "S0003", "last": "S0003", "diagram_ref": "C"}]
    assert timing.project_plan(split, spans)[1]
    assert chapter_slide_eval.diagram_problems(split, lines, {**context, "diagram_spans": spans})
    missing = [{"first": "S0001", "last": "S0003"}]
    assert timing.project_plan(missing, spans)[1]
    extended = [{"first": "S0001", "last": "S0004", "diagram_ref": "C"}]
    assert timing.project_plan(extended, spans)[1]

    grouped = [{"first": "S0001", "last": "S0003", "diagram_ref": "C", "points": []},
               {"first": "S0004", "last": "S0004", "points": []}]
    assert timing.project_plan(grouped, spans) == ({"S0001": {"sha256": "C", "sentence": "S0002"}}, [])
    assert chapter_slide_eval.diagram_problems(grouped, lines, {**context, "diagram_spans": spans}) == []
    grouped[1]["diagram_ref"] = "C"
    assert timing.project_plan(grouped, spans)[1]


def test_local_part_of_diagram_does_not_create_a_diagram_span():
    result = timing.plan_spans(candidate()["claim_context"], source(),
                              lambda state, questions: {"scope": "part"})
    assert result == ([], [])


def test_diagram_span_limits_writer_schema_without_reducing_ordinary_slide_limits():
    from backend.tests.test_chapter_slides import CHAPTER

    context = {**candidate()["claim_context"], "schema_version": "chapter-slide-outline-context.v1",
               "diagram_spans": [{"sha256": "C", "first": "S0001", "last": "S0007"}]}
    schema = chapter_slides.response_schema(context)
    props = schema["properties"]["slides"]["items"]["properties"]
    assert "diagram_ref" not in props and "maxItems" not in props["points"]

    kept = [{"id": "P", "start": 0, "end": 70, "text": "".join(f"句{i}。" for i in range(1, 8))}]
    draft = {"first": "S0001", "title": "結構", "verses": "5:1-12", "scriptures": [],
             "points": [{"sentence": f"S{i:04d}", "text": f"要點{i}"} for i in range(1, 8)]}
    assert any("要點 7 條" in problem for problem in chapter_slides.check([draft], kept, CHAPTER)[0])
    assert chapter_slides.check([draft], kept, CHAPTER, diagram_spans=context["diagram_spans"])[0] == []
