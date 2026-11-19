import json

from backend.pipeline import chapter_slide_eval as ev

KEPT = [{"id": "U1", "start": 0.0, "end": 60.0,
         "text": "我們看五章三節。虛心的人有福了，因為天國是他們的。那個是字原文是現在式。"}]


def bible(tmp_path):
    root = tmp_path / "cuv" / "MAT"
    root.mkdir(parents=True, exist_ok=True)
    (root / "5.json").write_text(json.dumps({"3": "虛心的人有福了！因為天國是他們的。", "4": "哀慟的人有福了！因為他們必得安慰。"},
                                            ensure_ascii=False))
    return ev.Bible(tmp_path / "cuv")


def one_slide(scriptures, notes=(), points=("一", "二")):
    return [{"first": "S0001", "last": "S0003", "title": "八福", "scriptures": list(scriptures),
             "inline_notes": list(notes), "points": [{"sentence": "S0001", "text": t} for t in points]}]


def test_a_verse_he_reads_must_be_on_screen_when_he_reads_it(tmp_path):
    missing = ev.evaluate(one_slide([]), KEPT, candidates=["mat-5-1-200"], bible=bible(tmp_path))["problems"]
    assert any("他在念 mat-5-3" in p for p in missing)
    shown = ev.evaluate(one_slide([{"sentence": "S0001", "slug": "mat-5-3-4"}],
                                  notes=[{"original": "ἐστιν", "explanation": "現在式"}]),
                        KEPT, candidates=["mat-5-1-200"], bible=bible(tmp_path))["problems"]
    assert shown == []


def test_original_language_he_explains_must_be_on_the_slide(tmp_path):
    problems = ev.evaluate(one_slide([{"sentence": "S0001", "slug": "mat-5-3"}]), KEPT,
                           candidates=["mat-5-1-200"], bible=bible(tmp_path))["problems"]
    assert problems == ["八福（S0003）: 他在講原文或文法，這一張沒有原文註解或相應要點"]
    noted = one_slide([{"sentence": "S0001", "slug": "mat-5-3"}], points=("一", "「是」是現在式"))
    assert ev.evaluate(noted, KEPT, candidates=["mat-5-1-200"], bible=bible(tmp_path))["problems"] == []


def test_reading_exemption_requires_the_verse_to_be_displayed_already(tmp_path):
    from backend.pipeline.chapter_slide_eval import Bible,displayed_reading_sentences
    book=tmp_path/'MAT';book.mkdir()
    text='虛心的人有福了因為天國是他們的'
    (book/'5.json').write_text(json.dumps({'3':text}))
    lines=[{'id':'S0001','text':text},{'id':'S0002','text':text},
           {'id':'S0003','text':'這一節的解釋需要特別留意上下文和神學觀念，不能讀進自己的意思。'}]
    slides=[{'first':'S0001','scriptures':[{'sentence':'S0002','slug':'mat-5-3'}]}]
    assert displayed_reading_sentences(slides,lines,bible=Bible(tmp_path))=={'S0002'}


def test_story_phrase_is_not_a_scripture_reading(tmp_path):
    book = tmp_path / "JOS"
    book.mkdir()
    verse = "那些人就往約但河的渡口追趕他們去了。追趕他們的人一出去，城門就關了。"
    (book / "2.json").write_text(json.dumps({"7": verse}, ensure_ascii=False))
    bible = ev.Bible(tmp_path)
    passage = ["jos-2-7"]
    kept = [{"id": "U1", "start": 0, "end": 40, "text": "可是他們出去，城門就關了。"}]
    assert ev.evaluate(one_slide([]), kept, candidates=passage, bible=bible)["problems"] == []
    kept[0]["text"] = verse
    assert ev.evaluate(one_slide([]), kept, candidates=passage, bible=bible)["problems"]


def test_slide_eval_checks_the_required_diagram_in_its_retained_claim_scope(tmp_path):
    lecture = "2011 NYSC 專題：馬太福音釋經（二）1"
    figure = {"sha256": "d" * 64, "title": "結構", "layout": "overview",
              "kind": "professor", "origin": "原圖", "svg": "<svg/>", "labels": ["結構"]}
    context = {"lecture": lecture, "diagram_contract": "claim-bound.v1", "claims": [
        {"claim_id": "G01-CL0001", "stated": "S0002", "diagrams": [figure]},
    ]}
    slides = one_slide([], points=("原文是現在式", "二"))
    library = bible(tmp_path)
    assert any("論證圖缺漏" in p for p in ev.evaluate(
        slides, KEPT, candidates=[], bible=library, context=context)["problems"])
    slides[0]["diagram_ref"] = figure["sha256"]
    assert ev.evaluate(slides, KEPT, candidates=[], bible=library, context=context)["problems"] == []
    context["claims"][0]["stated"] = "S0100"
    assert any("沒有連到本張" in p for p in ev.evaluate(
        slides, KEPT, candidates=[], bible=library, context=context)["problems"])
    slides[0].pop("diagram_ref")
    assert ev.evaluate(slides, KEPT, candidates=[], bible=library, context=context)["problems"] == []
