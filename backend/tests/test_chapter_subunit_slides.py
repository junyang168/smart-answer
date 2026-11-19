"""Slides per listening sub-unit (owner 2026-11-18: 「do it in sub unit」)."""
import pytest

from backend.pipeline import chapter_slides
from backend.pipeline.chapter_slide_cases import check_cases, resolve_cases
from backend.pipeline.chapter_subunit_slides import subunit_selection


def plan():
    rng = lambda key, start, text: {"paragraph_key": key, "start": start, "end": start + 10, "text": text}
    return {
        "inputs": [{"lecture": "L1", "source_id": "S1", "source_sha256": "t1", "changeset": "c1"}],
        "units": {"mat-5-1-12": {
            "groups": [
                {"key": "S1:G001", "source_id": "S1", "source_ranges": [rng("P2", 20, "第二段。"), rng("P1", 0, "第一段。")]},
                {"key": "S1:G002", "source_id": "S1", "source_ranges": [rng("P3", 40, "第三段。")]},
            ],
            "sub_units": [{"id": "1.1", "title": "結構", "group_keys": ["S1:G001"]},
                          {"id": "1.2", "title": "八福", "group_keys": ["S1:G002"]}],
        }},
    }


def test_subunit_selection_is_exactly_its_source_ranges_in_order():
    lecture, selection = subunit_selection(plan(), "mat-5-1-48", "1.1")
    assert lecture == "L1" and selection["transcript_sha256"] == "t1"
    assert [(p["source_paragraph_key"], p["text"]) for p in selection["pieces"]] == [("P1", "第一段。"), ("P2", "第二段。")]
    assert all(d["keep"] for d in selection["decisions"])
    assert [line["id"] for line in chapter_slides.sentences(chapter_slides.kept_pieces(selection))] == ["S0001", "S0002"]


def test_owner_cases_follow_his_words_into_a_subunit():
    lines = [{"id": "S0001", "text": "另一句。"}, {"id": "S0002", "text": "門徒的使命。"}]
    case = {"kind": "point", "at": "S0015", "source_text": "門徒的使命。", "pattern": "門徒", "why": "w"}
    elsewhere = {"kind": "point", "at": "S0099", "source_text": "不在這一段。", "pattern": "x", "why": "w"}
    assert [c["at"] for c in resolve_cases([case, elsewhere], lines, partial=True)] == ["S0002"]
    with pytest.raises(ValueError):
        resolve_cases([elsewhere], lines)          # the whole lecture must contain every case
    slides = [{"first": "S0001", "title": "t", "points": [{"sentence": "S0001", "text": "別的"}]}]
    assert check_cases(slides, [case], lines, partial=True)


def test_a_revision_may_not_change_slides_nobody_flagged():
    a = {"first": "S0001", "title": "約", "diagram_ref": "a" * 64, "points": []}
    b = {"first": "S0010", "title": "律法", "points": []}
    dropped = [dict(a, diagram_ref=""), dict(b, title="律法（改）")]
    assert chapter_slides.untouched_problems([a, b], dropped, {"S0010"}) == [
        "約（S0001）沒有被指出問題，修正稿卻改了它；請原樣交回"]
    assert chapter_slides.untouched_problems([a, b], [a, dict(b, title="新")], {"S0010"}) == []


def test_owner_one_slide_case_outranks_the_four_point_limit():
    from backend.tests.test_chapter_slides import CHAPTER
    kept = [{"id": "U001", "start": 0.0, "end": 60.0, "text": "一。二。三。四。五。六。"}]
    slide = {"first": "S0001", "title": "結構", "verses": "5:3-5", "scriptures": [],
             "points": [{"sentence": f"S000{i}", "text": f"第{i}點"} for i in range(1, 7)]}
    assert any("要點 6 條" in p for p in chapter_slides.check([slide], kept, CHAPTER)[0])
    assert not any("要點 6 條" in p for p in chapter_slides.check([slide], kept, CHAPTER, one_slide=[("S0001", "S0006")])[0])


def test_his_diagram_is_placed_by_the_program_at_the_argument_start(monkeypatch):
    from backend.pipeline import chapter_slide_cases
    monkeypatch.setattr(chapter_slide_cases, "diagrams_for_lecture", lambda lecture: [{"claim": "CL0001"}])
    context = {"lecture": "L1", "claims": [{"claim_id": "G01-CL0001", "stated": "S0018", "diagrams": [{"sha256": "d" * 64}],
                                            "evidence_steps": [{"anchors": [{"sentences": ["S0008"]}]}]}]}
    raw = [{"first": "S0001", "diagram_ref": ""}, {"first": "S0007", "diagram_ref": ""}, {"first": "S0017"}]
    placed = chapter_slides.place_case_diagrams(raw, context)
    assert [x.get("diagram_ref") for x in placed] == ["", "d" * 64, None]
    assert raw[1]["diagram_ref"] == ""             # the model's answer is not edited in place


def test_verse_spacing_is_not_a_change():
    a = {"first": "S0066", "title": "t", "verses": "5:21-22,5:27-28", "points": []}
    assert chapter_slides.untouched_problems([a], [dict(a, verses="5:21-22, 5:27-28")], {"S0001"}) == []
