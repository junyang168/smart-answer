import json

from backend.pipeline import chapter_slide_cases as c


def slides():
    return [{"first": "S0001", "title": "結構", "points": [{"sentence": "S0001", "text": "兩大段"}], "inline_notes": []},
            {"first": "S0004", "title": "八福", "points": [{"sentence": "S0004", "text": "現在式"}, {"sentence": "S0006", "text": "將來式"}],
             "inline_notes": [{"sentence": "S0005", "span": "天國是他們的", "verse": 3}]}]


def test_each_kind_of_owner_case_is_checked():
    cases = [{"kind": "no_title", "pattern": "地圖", "why": "a"},
             {"kind": "one_slide", "from": "S0001", "to": "S0005", "why": "b"},
             {"kind": "point", "at": "S0007", "pattern": "首.*現在", "why": "c"},
             {"kind": "not_before", "at": "S0006", "pattern": "將來", "why": "d"},
             {"kind": "note", "at": "S0006", "span": "天國是他們的", "verses": [3, 10], "why": "e"}]
    problems = c.check_cases(slides(), cases)
    assert [p.split("：")[1][0] for p in problems] == ["b", "c", "e"]


def test_the_case_file_reads_and_every_case_is_a_known_kind():
    data = json.loads(c.CASES.read_text(encoding="utf-8"))
    cases = [case for lecture in data["lectures"].values() for case in lecture]
    assert cases and all(case["why"] and case["owner"] for case in cases)
    c.check_cases([{"first": "S0001", "title": "t", "points": [], "inline_notes": []}], cases)
