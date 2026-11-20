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


def test_owner_timing_uses_unique_source_quote_not_stale_sentence_number():
    source = [{'id': 'S0005', 'text': '教授現在開始說結論。'}]
    case = {'kind': 'point', 'at': 'S0002', 'source_text': '教授現在開始說結論。',
            'pattern': '結論', 'point_text': '這是結論', 'why': '結論須在教授講到時出現'}
    before = [{'first': 'S0001', 'last': 'S0009', 'points': [
        {'sentence': 'S0001', 'text': '背景'}, {'sentence': 'S0008', 'text': '這是結論'}]}]
    assert c.check_cases(before, [case], source)
    changes = c.timing_corrections(before, [case], source)
    assert changes[0]['current_sentence'] == 'S0008'
    assert changes[0]['proposed_sentence'] == 'S0005'
    assert case['at'] == 'S0002'  # Preserve the historical reference.
    before[0]['points'][1]['sentence'] = 'S0005'
    assert c.check_cases(before, [case], source) == []
    assert c.timing_corrections(before, [case], source) == []


def test_missing_or_duplicated_owner_source_quote_cannot_fall_back_to_old_id():
    import pytest
    case = {'kind': 'point', 'at': 'S0002', 'source_text': '原話', 'pattern': '結論', 'why': 'why'}
    for source in [[], [{'id': 'S0001', 'text': '原話'}, {'id': 'S0002', 'text': '原話'}]]:
        assert c.check_cases([], [case], source)
        with pytest.raises(ValueError): c.timing_corrections([], [case], source)


def test_structure_keeps_mission_and_response_on_one_slide_at_separate_anchors():
    lecture = "2011 NYSC 專題：馬太福音釋經（二）1"
    cases = [case for case in c.cases_for(lecture)
             if case.get("at") in {"S0015", "S0017"} or case["kind"] == "one_slide"]
    source = [{"id": "S0105", "text": cases[0]["from_text"]},
              {"id": "S0115", "text": cases[1]["source_text"]},
              {"id": "S0117", "text": cases[2]["source_text"]},
              {"id": "S0120", "text": cases[0]["to_text"]}]
    structure = {"first": "S0105", "title": "登山寶訓的結構"}
    mission = {"sentence": "S0115", "text": "太5:13–7:29 談門徒的使命"}
    response = {"sentence": "S0117", "text": "認識神後有行為或生命上的反應"}
    assert c.check_cases([{**structure, "points": [mission, response]}], cases, source) == []
    for sentence in ("S0115", "S0117"):
        combined = {"sentence": sentence, "text": mission["text"] + "；" + response["text"]}
        assert c.check_cases([{**structure, "points": [combined]}], cases, source)
    assert c.check_cases([{**structure, "points": [mission]},
                          {"first": "S0117", "title": "門徒", "points": [response]}], cases, source)
