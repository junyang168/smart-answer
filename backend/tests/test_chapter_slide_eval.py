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
