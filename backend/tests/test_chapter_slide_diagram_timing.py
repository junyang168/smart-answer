import json

import pytest

from backend.pipeline import chapter_exposition, chapter_slides, claim_flash_common
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
