"""按章页面的投影片：模型在句子上分张，读到的经文要在上面。"""

import json

from backend.pipeline import chapter_exposition, chapter_slides
from backend.pipeline.chapter_material import Chapter

CHAPTER = Chapter(
    passage="mat-5-1-48",
    series="S",
    lectures=("L",),
    sections=("mat-5-1-12", "mat-5-13-16", "mat-5-17-20"),
)

# 两个片段、四句话：S0001 S0002 在 U001，S0003 S0004 在 U002。
KEPT = [
    {"id": "U001", "start": 0.0, "end": 100.0, "text": "耶穌上了山。這是問題嗎？"},
    {"id": "U002", "start": 300.0, "end": 400.0, "text": "答案在這裡。我們看以賽亞書六十一章一節。"},
]


def slide(first, verses="5:3-5", scriptures=(), points=("一", "二"), title=None):
    points = [p if isinstance(p, dict) else {"sentence": first, "text": p} for p in points]
    return {"first": first, "title": title or first, "points": points,
            "verses": verses, "scriptures": list(scriptures)}


def test_sentences_are_numbered_and_timed_by_position():
    lines = chapter_slides.sentences(KEPT)
    assert [(s["id"], s["piece"]) for s in lines] == [
        ("S0001", "U001"), ("S0002", "U001"), ("S0003", "U002"), ("S0004", "U002"),
    ]
    assert lines[0]["start"] == 0 and 0 < lines[1]["start"] < 100
    assert lines[2]["start"] == 300


def test_a_slide_may_start_mid_piece_so_a_question_stays_with_its_answer():
    problems, _, slides = chapter_slides.check(
        [slide("S0001", "5:1-48"), slide("S0002", "5:17-18",
                                       scriptures=[{"sentence": "S0004", "ref": "以賽亞書 61:1"}])],
        KEPT, CHAPTER,
    )
    assert problems == []
    assert [(s["first"], s["last"]) for s in slides] == [("S0001", "S0001"), ("S0002", "S0004")]


def test_slides_must_start_at_the_first_sentence_and_go_forward():
    problems, _, _ = chapter_slides.check([slide("S0002")], KEPT, CHAPTER)
    assert "第一張應從 S0001 開始" in problems
    problems, _, _ = chapter_slides.check([slide("S0001"), slide("S0003"), slide("S0002")], KEPT, CHAPTER)
    assert "投影片的第一句沒有按次序遞增" in problems


def test_points_verses_and_scripture_references_are_checked():
    problems, _, _ = chapter_slides.check([
        slide("S0001", points=("只有一條",)),
        slide("S0002", verses="6:1", scriptures=[{"sentence": "S0001", "ref": "以賽亞書 61:1"}]),
        slide("S0004", "5:18", scriptures=[{"sentence": "S0004", "ref": "某書 1:1"}]),
    ], KEPT, CHAPTER)
    joined = "\n".join(problems)
    assert "要點 1 條" in joined
    assert "經節讀不出來或不在這一章" in joined
    assert "句子 S0001 不在這一張" in joined
    assert "經文出處讀不出來" in joined


def test_points_appear_when_he_reaches_them_in_his_order():
    # 「八福都是天國的祝福」：四條一起出來，他還在讀經文，聽眾已讀到兩分鐘後的結論。
    problems, _, _ = chapter_slides.check([slide("S0001", points=(
        {"sentence": "S0002", "text": "後"}, {"sentence": "S0001", "text": "先"}))], KEPT, CHAPTER)
    assert any("沒有按他講的次序" in p for p in problems)
    problems, _, _ = chapter_slides.check([slide("S0001"), slide("S0003", points=(
        {"sentence": "S0001", "text": "別張的句子"}, {"sentence": "S0004", "text": "二"}))], KEPT, CHAPTER)
    assert any("別張的句子" in p and "不在這一張" in p for p in problems)
    decisions = {"pieces": KEPT, "decisions": [{"id": u["id"], "keep": True, "verses": ["mat-5-3"], "kind": "釋經"} for u in KEPT]}
    _, _, slides = chapter_slides.check([slide("S0001", points=(
        {"sentence": "S0001", "text": "一"}, {"sentence": "S0004", "text": "二"}))], KEPT, CHAPTER)
    _, timed = chapter_slides.placed(decisions, {"slides": slides})
    assert [p["at"] for p in timed[0]["points"]][0] == 0 and timed[0]["points"][1]["at"] > 300


def test_a_verse_read_but_missing_from_the_slide_is_listed_not_rejected():
    # 不作废（程序认出处会认错），但要列出来给负责人看。
    problems, unshown, _ = chapter_slides.check([slide("S0001", "5:1-12")], KEPT, CHAPTER)
    assert problems == []
    assert any("isa-61-1" in item for item in unshown)


def test_quoted_titles_lose_their_outer_quotes():
    _, _, slides = chapter_slides.check([slide("S0001", title="「Pat, how are you?」")], KEPT, CHAPTER)
    assert slides[0]["title"] == "Pat, how are you?"


def test_pieces_follow_their_first_sentences_slide_and_verses_appear_when_read():
    decisions = {
        "pieces": KEPT,
        "decisions": [{"id": "U001", "keep": True, "verses": ["mat-5-3"], "kind": "釋經"},
                      {"id": "U002", "keep": True, "verses": ["mat-5-13"], "kind": "釋經"}],
    }
    _, _, slides = chapter_slides.check(
        [slide("S0001", "5:1-48"), slide("S0003", "5:17-18",
                                       scriptures=[{"sentence": "S0004", "ref": "以賽亞書 61:1"}])],
        KEPT, CHAPTER,
    )
    verses, timed = chapter_slides.placed(decisions, {"slides": slides})
    assert verses == {"U001": ["mat-5-1-48"], "U002": ["mat-5-17-18"]}
    assert timed[1]["at"] == 300 and timed[1]["scriptures"][0]["at"] > 300


def test_slides_are_dropped_once_the_keep_decisions_are_rerun(tmp_path):
    decisions = chapter_exposition.record_path(tmp_path, "mat-5-1-48", "L")
    decisions.parent.mkdir(parents=True)
    decisions.write_text("{}")
    target = chapter_slides.record_path(tmp_path, "mat-5-1-48", "L")
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "schema_version": chapter_slides.SCHEMA_VERSION,
        "decisions_sha256": chapter_slides.decisions_sha(tmp_path, "mat-5-1-48", "L"),
        "slides": [],
    }))
    assert chapter_slides.load_record(tmp_path, "mat-5-1-48", "L") is not None
    decisions.write_text('{"rerun": true}')
    assert chapter_slides.load_record(tmp_path, "mat-5-1-48", "L") is None


class FakeClient:
    model = "fake"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def generate_json(self, system, user, schema):
        self.prompts.append(user)
        return self.answers.pop(0)


def _decided(tmp_path):
    record = {"pieces": KEPT, "decisions": [
        {"id": u["id"], "keep": True, "verses": ["mat-5-3"], "kind": "釋經"} for u in KEPT]}
    path = chapter_exposition.record_path(tmp_path, "mat-5-1-48", "L")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record, ensure_ascii=False))
    first = {"slides": [slide("S0001", "5:1-48", points=("重複", "重複的另一說法"))]}
    client = FakeClient([first])
    assert chapter_slides.decide(CHAPTER, "L", data_base_dir=tmp_path, client=client)["status"] == "saved"


def test_a_revision_replaces_the_slides_only_after_review_and_the_same_checks(tmp_path):
    _decided(tmp_path)
    finding = {"grade": "B", "blocking": False, "findings": [
        {"slide": "S0001", "kind": "跟得上", "problem": "兩條說同一件事", "fix": "刪一條，加他的結論"}]}
    bad = {"slides": [slide("S0002")]}  # 不從第一句開始：程序核對不過，原稿不動
    client = FakeClient([finding, bad])
    assert chapter_slides.review(CHAPTER, "L", data_base_dir=tmp_path, client=client)["findings"] == 1
    assert chapter_slides.revise(CHAPTER, "L", data_base_dir=tmp_path, client=client)["status"] == "rejected"
    assert "兩條說同一件事" in client.prompts[-1] and "重複的另一說法" in client.prompts[-1]
    assert chapter_slides.load_record(tmp_path, "mat-5-1-48", "L")["slides"][0]["points"][1]["text"] == "重複的另一說法"

    good = {"slides": [slide("S0001", "5:1-48", points=("一", "他的結論"))]}
    client = FakeClient([good])
    assert chapter_slides.revise(CHAPTER, "L", data_base_dir=tmp_path, client=client)["status"] == "revised"
    record = chapter_slides.load_record(tmp_path, "mat-5-1-48", "L")
    assert record["revision"] == 1 and record["slides"][0]["points"][1]["text"] == "他的結論"
    # 修正后的稿子还没审过，不能再按旧审阅修。
    assert chapter_slides.revise(CHAPTER, "L", data_base_dir=tmp_path, client=FakeClient([]))["status"] == "not_reviewed"


def test_a_slide_shows_in_every_section_its_time_reaches():
    # （二）6：投影片在 5:17-20 的片段裡開始，講到下一個片段歸了 5:1-12；那一行原來沒有投影片。
    from backend.pipeline.original_audio_index import _chapter_slides

    rows = {
        "mat-5-17-20": {"pieces": [{"id": "U001", "start": 0.0, "end": 100.0}]},
        "mat-5-1-12": {"pieces": [{"id": "U002", "start": 100.0, "end": 180.0}]},
    }
    timed = [{"at": 50.0, "seconds": 120.0, "title": "t", "points": [], "verses": ["mat-5-3"], "scriptures": []}]
    out = _chapter_slides(rows, timed)
    assert out["mat-5-17-20"][0]["at"] == 50.0
    assert out["mat-5-1-12"][0]["at"] == 100.0 and out["mat-5-1-12"][0]["seconds"] == 70.0


def test_each_row_gets_its_own_timed_transcript_with_grey_lines_marked():
    from backend.pipeline.original_audio_index import _transcript

    pieces = [
        {"id": "U001", "start": 0.0, "end": 10.0, "text": "第一句。第二句。"},
        {"id": "U002", "start": 10.0, "end": 20.0, "text": "見證一句。"},
        {"id": "U003", "start": 30.0, "end": 40.0, "text": "別的段。"},
    ]
    lines = chapter_slides.sentences(pieces)
    stretches = [{"start": 0.0, "end": 10.0}, {"start": 10.0, "end": 20.0, "left_out": True}]

    rows = _transcript(lines, stretches)

    assert [row["text"] for row in rows] == ["第一句。", "第二句。", "見證一句。"]
    assert [row["at"] for row in rows] == [0.0, 5.0, 10.0]
    assert [row.get("left_out", False) for row in rows] == [False, False, True]


def test_times_follow_the_speech_recognition_not_an_even_pace_through_the_paragraph(tmp_path):
    """段落只有段首有时间；他前半段讲得慢，按字数平均会把后半段的句子放早了。"""

    from backend.pipeline.original_audio_index import retimer

    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "L1.json").write_text(json.dumps({"entries": [
        {"index": 1, "start_ms": 0, "end_ms": 30000, "text": "甲乙丙丁戊己"},
        {"index": 2, "start_ms": 30000, "end_ms": 36000, "text": "庚辛壬癸子丑"},
        {"index": 3, "start_ms": 100000, "end_ms": 106000, "text": "寅卯辰巳"},
    ]}, ensure_ascii=False), encoding="utf-8")
    sermon = {"transcript_id": "L1", "segments": [
        {"text": "甲乙丙丁戊己，庚辛壬癸子丑。", "start_index": 1, "end_index": 2, "start_time": 0},
        {"text": "寅卯辰巳。", "start_index": 3, "end_index": 3, "start_time": 100},
    ]}

    convert = retimer(sermon, tmp_path)

    # 「庚」是第 7 个字：平均语速算出来约 50 秒，录音里是 30 秒。
    coarse = 100 * 7 / 14
    assert abs(convert(coarse) - 30.0) < 0.5
    assert abs(convert(100.0) - 100.0) < 0.5


def test_without_speech_recognition_times_stay_as_they_were(tmp_path):
    from backend.pipeline.original_audio_index import retimer

    convert = retimer({"transcript_id": "none", "segments": []}, tmp_path)
    assert convert(42.0) == 42.0


def test_the_older_cuv_spelling_of_the_johannine_letters_is_read():
    from backend.api.scripture import reference_slugs

    assert reference_slugs("約翰壹書 1:9") == ["1jn-1-9"]
    assert reference_slugs("約翰貳書 1:3") == ["2jn-1-3"]
    assert reference_slugs("約翰參書 1:2") == ["3jn-1-2"]


def test_the_cuv_spelling_of_kings_is_read():
    from backend.api.scripture import reference_slugs

    assert reference_slugs("列王紀上 1:32-33") == ["1ki-1-32-33"]
    assert reference_slugs("列王紀下 2:11") == ["2ki-2-11"]


def test_a_rejected_answer_is_rechecked_without_calling_the_model(tmp_path, monkeypatch):
    record = {"pieces": KEPT, "decisions": [
        {"id": u["id"], "keep": True, "verses": ["mat-5-3"], "kind": "釋經"} for u in KEPT]}
    path = chapter_exposition.record_path(tmp_path, "mat-5-1-48", "L")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record, ensure_ascii=False))
    answer = {"slides": [slide("S0001", "5:1-48", points=("一", "二"))]}
    # 程序先拒收（像书名表不认得的写法），修好后同一份回答重核就过。
    real_check = chapter_slides.check
    monkeypatch.setattr(chapter_slides, "check", lambda *a: (["書名讀不出來"], [], []))
    assert chapter_slides.decide(CHAPTER, "L", data_base_dir=tmp_path, client=FakeClient([answer]))["status"] == "rejected"
    monkeypatch.setattr(chapter_slides, "check", real_check)
    assert chapter_slides.recheck(CHAPTER, "L", data_base_dir=tmp_path)["status"] == "saved"
    assert chapter_slides.load_record(tmp_path, "mat-5-1-48", "L")["slides"][0]["points"][1]["text"] == "二"
    # 存过之后，旧的拒收稿不再重核，免得盖掉审过、修过的那一份。
    assert chapter_slides.recheck(CHAPTER, "L", data_base_dir=tmp_path)["status"] == "already_saved"


def test_a_rejected_answer_is_not_rechecked_after_the_keep_decisions_change(tmp_path, monkeypatch):
    record = {"pieces": KEPT, "decisions": [
        {"id": u["id"], "keep": True, "verses": ["mat-5-3"], "kind": "釋經"} for u in KEPT]}
    path = chapter_exposition.record_path(tmp_path, "mat-5-1-48", "L")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record, ensure_ascii=False))
    monkeypatch.setattr(chapter_slides, "check", lambda *a: (["x"], [], []))
    chapter_slides.decide(CHAPTER, "L", data_base_dir=tmp_path,
                          client=FakeClient([{"slides": [slide("S0001", "5:1-48")]}]))
    record["decisions"][0]["keep"] = False
    path.write_text(json.dumps(record, ensure_ascii=False))
    assert chapter_slides.recheck(CHAPTER, "L", data_base_dir=tmp_path)["status"] == "decisions_changed"


def test_owner_title_scope_case_reaches_generation_review_and_revision(tmp_path):
    """Regression for the shared policy and normal correction path, not a model eval."""
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/chapter_slides/title_scope_owner_case.json').read_text())
    pieces = [{'id': 'U001', 'start': fixture['sentences'][0]['start'],
               'end': fixture['sentences'][-1]['end'],
               'text': ''.join(s['text'] for s in fixture['sentences'])}]
    decisions = {'pieces': pieces, 'decisions': [
        {'id': 'U001', 'keep': True, 'verses': ['mat-5-1-48'], 'kind': '釋經'}]}
    path = chapter_exposition.record_path(tmp_path, CHAPTER.passage, 'L')
    path.parent.mkdir(parents=True); path.write_text(json.dumps(decisions, ensure_ascii=False))
    wrong = {'slides': [slide('S0001', title=fixture['rejected_title'])]}
    finding = {'grade': 'B', 'blocking': False, 'findings': [
        {'slide': 'S0001', 'kind': '跟得上', 'problem': fixture['reason'],
         'fix': '標題改為「' + fixture['reference_title'] + '」，保留內容與分張。'}]}
    corrected = {'slides': [slide('S0001', title=fixture['reference_title'])]}

    class Capture(FakeClient):
        def __init__(self, answers):
            super().__init__(answers)
            self.systems = []

        def generate_json(self, system, user, schema):
            self.systems.append(system)
            return super().generate_json(system, user, schema)

    author, reviewer, reviser = Capture([wrong]), Capture([finding]), Capture([corrected])
    chapter_slides.decide(CHAPTER, 'L', data_base_dir=tmp_path, client=author)
    chapter_slides.review(CHAPTER, 'L', data_base_dir=tmp_path, client=reviewer)
    chapter_slides.revise(CHAPTER, 'L', data_base_dir=tmp_path, client=reviser)
    # All three real pipeline calls receive the same range rule, including revisions.
    for client in (author, reviewer, reviser):
        assert chapter_slides.SLIDE_TITLE_RULES in client.systems[0]
        assert chapter_slides.POINT_TIMING_RULES in client.systems[0]
        assert "〈要點時間規則〉" not in client.systems[0]
        assert '〈標題規則〉' not in client.systems[0]
    assert '原文中標題沒有涵蓋的部分' in reviewer.systems[0]
    assert fixture['reason'] in reviser.prompts[0]
    assert fixture['reference_title'] in reviser.prompts[0]
    record = chapter_slides.load_record(tmp_path, CHAPTER.passage, 'L')
    assert record['slides'][0]['title'] == fixture['reference_title']
    assert record['revision'] == 1
    previous = json.loads(chapter_slides.record_path(tmp_path, CHAPTER.passage, 'L').with_suffix('.revision-0.json').read_text())
    assert previous['slides'][0]['title'] == fixture['rejected_title']


def test_a_named_term_is_anchored_to_its_introduction_before_the_definition():
    kept = [
        {"id": "U004", "start": 324.722, "end": 344.95, "text": (
            "這裡啊，大概各位可以看到，聖經的這些結構裡面啊，常常有一種結構，"
            "英文叫做 inclusio，中文我還沒有看到人家怎麼翻呢，inclusio。其實是什麼東西呢？"
        )},
        {"id": "U005", "start": 344.95, "end": 365.0, "text": (
            "就是一段聖經啊，它的開頭跟它的最後，或者是字完全一樣，或者是觀念完全一樣。"
        )},
    ]
    point = {"sentence": "S0003", "text": "inclusio：一段經文開頭和結尾字或觀念相同"}
    answer = [slide("S0001", points=("八福的結構", point))]
    problems, _, _ = chapter_slides.check(answer, kept, CHAPTER)
    assert len(problems) == 1
    assert "S0001 已明確引入術語" in problems[0]
    assert "S0003 的定義句" in problems[0]

    for earlier in (
        [dict(kept[0], text=kept[0]["text"].replace("英文叫做", "也有人提到")), kept[1]],
        [kept[0], dict(kept[1], start=350.0)],
    ):
        assert chapter_slides.check(answer, earlier, CHAPTER)[0] == []

    point["sentence"] = "S0001"
    assert chapter_slides.check(answer, kept, CHAPTER)[0] == []
