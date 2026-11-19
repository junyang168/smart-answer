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


def test_matthew_seven_requires_both_sides_of_the_contrast_and_the_missing_inference():
    from backend.pipeline.chapter_slide_cases import cases_for

    rules = cases_for("2011 NYSC 專題：馬太福音釋經（二）6")
    source = [
        {"id": "S0001", "text": "談到我們要怎麼對待人。"},
        {"id": "S0002", "text": "所以你認出彌賽亞，下面是門徒的使命。"},
    ]
    def slide(sid, *points):
        return {"first": sid, "title": "結語",
                "points": [{"sentence": sid, "text": text} for text in points]}
    original = [
        slide("S0001", "七章七至十一節談向神禱告", "兩段無因果關係"),
        slide("S0002", "認出祂是神就有責任", "認出祂是神就知道責任"),
    ]
    findings = check_cases(original, rules, source, partial=True)
    assert len(findings) == 2
    assert "怎樣待人" in findings[0] and "到門徒使命" in findings[1]
    assert check_cases([slide("S0001", "太七章十二節講如何待人"), original[1]],
                       rules, source, partial=True)[0].endswith(rules[1]["why"])

    corrected = [slide("S0001", "向神禱告", "怎樣待人"),
                 slide("S0002", "認出彌賽亞就有門徒的使命")]
    assert check_cases(corrected, rules, source, partial=True) == []
    assert len(resolve_cases(rules, source[:1], partial=True)) == 2


def test_subunit_writer_stops_at_first_program_rejection(tmp_path, monkeypatch):
    import json
    from backend.pipeline import chapter_exposition
    from backend.tests.test_chapter_slides import CHAPTER

    record = chapter_exposition.record_path(tmp_path, CHAPTER.passage, "L1")
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"pieces": [{"id": "P1", "text": "第一句。", "start": 0, "end": 1}],
                                  "decisions": [{"id": "P1", "keep": True}]}))
    context = {"scope": "subunit", "schema_version": "chapter-slide-outline-context.v1",
               "passage": CHAPTER.passage, "lecture": "L1", "claims": [],
               "decisions_sha256": chapter_exposition.file_sha256(record)}

    class Writer:
        calls = 0

        def generate_json(self, system, packet, schema):
            self.calls += 1
            assert "本子單元承重推論及原文位置" in packet
            return {"slides": [{"first": "S0099", "title": "錯了"}]}

    writer = Writer()
    assert chapter_slides.decide(CHAPTER, "L1", data_base_dir=tmp_path, client=writer,
                                 claim_context=context)["status"] == "rejected"
    assert writer.calls == 1


def test_same_one_shot_request_can_replay_but_cannot_dispatch_again(tmp_path):
    from backend.pipeline.chapter_slide_claims import RecordedClient

    class Writer:
        model = "writer"
        calls = 0

        def generate_json(self, system, prompt, schema):
            self.calls += 1
            if prompt == "unfinished":
                raise RuntimeError("interrupted")
            return {"slides": []}

    writer = Writer()
    client = RecordedClient(writer, tmp_path / "complete", once=True)
    assert client.generate_json("system", "packet", {}) == {"slides": []}
    assert client.generate_json("system", "packet", {}) == {"slides": []}
    assert writer.calls == 1
    with pytest.raises(ValueError, match="different one-shot"):
        client.generate_json("system", "changed", {})

    unfinished = RecordedClient(writer, tmp_path / "unfinished", once=True)
    with pytest.raises(RuntimeError, match="interrupted"):
        unfinished.generate_json("system", "unfinished", {})
    with pytest.raises(ValueError, match="no recorded answer"):
        unfinished.generate_json("system", "unfinished", {})
    assert writer.calls == 2


def test_subunit_semantic_rejection_is_reported_once(tmp_path, monkeypatch):
    import json
    from backend.pipeline import chapter_slide_claims, chapter_subunit_slides as sub
    from backend.tests.test_chapter_slides import CHAPTER

    plan_path = sub.plan_path(tmp_path, CHAPTER.passage)
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(json.dumps(plan()))
    out = tmp_path / "run"
    monkeypatch.setitem(sub.CHAPTERS, CHAPTER.passage, CHAPTER)
    monkeypatch.setattr(chapter_slide_claims, "compile_instance_context", lambda *a, **kw: {})
    monkeypatch.setattr(sub, "_client", lambda *a: object())
    calls = []

    def plan_arguments(context, source, client):
        calls.append("plan")
        assert context["scope"] == "subunit" and source
        return []

    monkeypatch.setattr(sub.chapter_slide_arguments, "plan", plan_arguments)

    def decide(chapter, lecture, *, data_base_dir, **kwargs):
        calls.append("generate")
        path = chapter_slides.record_path(data_base_dir, chapter.passage, lecture)
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        return {"status": "saved"}

    def prepare_diagram(deck, selection, artifacts):
        calls.append("diagram")
        return []

    def prepare(deck, selection, artifacts):
        calls.append("timing")

    def evaluate(deck, selection, receipt, model, **kwargs):
        calls.append("eval")
        receipt.write_text("{}")

    monkeypatch.setattr(chapter_slides, "decide", decide)
    monkeypatch.setattr(sub.chapter_slide_diagram_timing, "check_record", prepare_diagram)
    monkeypatch.setattr(sub.chapter_slide_point_timing, "prepare_record", prepare)
    monkeypatch.setattr(sub.chapter_slide_eval, "run_semantic_eval", evaluate)
    monkeypatch.setattr(sub.chapter_slide_eval, "semantic_eval_problems", lambda *a: ["missing coverage"])
    for _ in range(2):
        result = sub.run(tmp_path, CHAPTER.passage, "1.1", out, conn=None)
        assert result["status"] == "unresolved" and result["problems"] == ["missing coverage"]
    assert calls == ["plan", "generate", "diagram", "timing", "eval"]


def test_matthew_seven_keeps_the_structure_diagram_only_at_the_approved_introduction():
    from backend.pipeline.chapter_slide_cases import diagrams_for_lecture

    lecture = "2011 NYSC 專題：馬太福音釋經（二）6"
    case, = diagrams_for_lecture(lecture)
    old = case["replace_refs"][1]
    right = case["sha256"]
    context = {"lecture": lecture, "claims": [
        {"claim_id": "G05-CL0014", "diagrams": [{"sha256": right}, {"sha256": old}]}]}
    raw = [{"first": "S0001", "diagram_ref": old}, {"first": "S0008", "diagram_ref": old}]
    lines = [{"id": "S0002", "text": case["source_text"]}]
    assert [row["diagram_ref"] for row in chapter_slides.place_case_diagrams(raw, context, lines)] == [right, ""]
    assert [row["diagram_ref"] for row in chapter_slides.place_case_diagrams(raw, context)] == ["", ""]
    assert [row["diagram_ref"] for row in raw] == [old, old]


def test_both_sides_are_reviewed_on_the_slide_as_separate_points():
    from backend.pipeline.chapter_slide_eval import semantic_packet

    lecture = "2011 NYSC 專題：馬太福音釋經（二）6"
    selection = {"pieces": [{"id": "P", "start": 0, "end": 20,
                            "text": "向神禱告。談到我們要怎麼對待人。"}],
                 "decisions": [{"id": "P", "keep": True}]}
    packet = semantic_packet({"lecture": lecture, "claim_context": {"scope": "subunit"}, "slides": []}, selection)
    assert packet["owner_rulings"] == []
    assert len(packet["required_slide_points"]) == 2
    assert all(row["slide_containing_sentence"] == "S0002" for row in packet["required_slide_points"])
    assert any("可以用不同要點分別呈現對照兩端" in rule for rule in packet["owner_rules"])


def test_subunit_prompt_allows_complete_answers_before_they_are_spoken():
    from backend.tests.test_chapter_slides import CHAPTER

    prompt = chapter_slides.system_prompt(CHAPTER, subunit=True)
    assert "可以概括連續幾句" in prompt
    assert "可在教授開始講這個問題時顯示該問題的完整答案" in prompt
    assert "不得借用前面的提問" not in prompt
    assert "一條要點只寫他在一處講出" not in prompt
