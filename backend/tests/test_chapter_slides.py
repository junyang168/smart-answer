"""按章页面的投影片：模型在句子上分张，读到的经文要在上面。"""

import json
import pytest

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


def test_subunit_slide_can_cover_a_passage_elsewhere_in_scripture():
    raw = slide("S0001", verses="", scriptures=[{"sentence": "S0001", "ref": "使徒行傳15:23"}])
    context = {"scope": "subunit"}
    assert chapter_slides.check([raw], KEPT, CHAPTER)[0]
    problems, _, slides = chapter_slides.checked([raw], KEPT, CHAPTER, context)
    assert not problems
    assert slides[0]["verses"] == []
    assert slides[0]["scriptures"] == [{"sentence": "S0001", "slug": "act-15-23"}]
    raw["verses"] = "6:1"
    assert chapter_slides.checked([raw], KEPT, CHAPTER, context)[0]


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
    saved_path = chapter_slides.record_path(tmp_path, CHAPTER.passage, 'L')
    previous_review = saved_path.with_suffix('.review-adopted.json')
    previous_review.write_text(json.dumps(finding, ensure_ascii=False))
    saved = json.loads(saved_path.read_text())
    adopted = '已採納：標題須涵蓋整段，不能只涵蓋其中一個例子。'
    saved['accepted_review_resolutions'] = [{
        'decision': adopted,
        'source_review_sha256': chapter_exposition.file_sha256(previous_review),
        'decisions_sha256': chapter_slides.decisions_sha(tmp_path, CHAPTER.passage, 'L')}]
    saved_path.write_text(json.dumps(saved, ensure_ascii=False))
    chapter_slides.review(CHAPTER, 'L', data_base_dir=tmp_path, client=reviewer)
    chapter_slides.revise(CHAPTER, 'L', data_base_dir=tmp_path, client=reviser)
    # All three real pipeline calls receive the same range rule, including revisions.
    for client in (author, reviewer, reviser):
        assert chapter_slides.SLIDE_TITLE_RULES in client.systems[0]
        assert chapter_slides.POINT_TIMING_RULES in client.systems[0]
        assert "〈要點時間規則〉" not in client.systems[0]
        assert '〈標題規則〉' not in client.systems[0]
    assert reviewer.systems[0] == chapter_slides.review_system_prompt('L')
    assert chapter_slides.REVIEW_COMPLETENESS_RULES in reviewer.systems[0]
    assert '原文中標題沒有涵蓋的部分' in reviewer.systems[0]
    assert adopted in reviewer.prompts[0] and adopted in reviser.prompts[0]
    assert fixture['reason'] in reviser.prompts[0]
    assert fixture['reference_title'] in reviser.prompts[0]
    record = chapter_slides.load_record(tmp_path, CHAPTER.passage, 'L')
    assert record['slides'][0]['title'] == fixture['reference_title']
    assert record['revision'] == 1
    previous = json.loads(chapter_slides.record_path(tmp_path, CHAPTER.passage, 'L').with_suffix('.revision-0.json').read_text())
    assert previous['slides'][0]['title'] == fixture['rejected_title']


def test_term_question_can_start_at_naming_but_substantive_definition_waits():
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
    assert chapter_slides.check(answer, kept, CHAPTER)[0] == []
    # Only an introductory label/question is incorrectly late at the definition.
    point["text"] = "inclusio：是什麼？"
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


def test_argument_evidence_spans_adjacent_selection_pieces():
    pieces = [
        {"id": "U001", "text": "這是問題嗎？"},
        {"id": "U002", "text": "答案在這裡。"},
        {"id": "U003", "text": "轉到另一個問題。"},
    ]
    claims = {"C1": {"occurrences": [{"transcript_id": "L"}], "evidence_step_ids": ["E1"]}}
    steps = {"E1": {"step_type": "reasoning", "source_fragment_ids": ["F1", "F2"]}}
    fragments = {
        "F1": {"source_id": "SRC", "verbatim_excerpt": "這是問題嗎？答案在這裡。"},
        "F2": {"source_id": "OTHER", "verbatim_excerpt": "轉到另一個問題。"},
    }
    evidence = chapter_exposition.piece_evidence(pieces, claims, steps, fragments, "SRC", "L")
    assert list(evidence) == ["U001", "U002"]
    assert evidence["U001"] == evidence["U002"]
    assert evidence["U001"][0]["role"] == "reasoning"
    assert evidence["U001"][0]["fragment_id"] == "F1"


def test_inline_notes_follow_the_cited_verse_and_sentence(tmp_path):
    book = tmp_path / "ISA"
    book.mkdir()
    (book / "61.json").write_text(json.dumps({"1": "叫我傳好信息給謙卑的人"}))
    draft = slide("S0001", scriptures=[{"sentence": "S0003", "ref": "以賽亞書 61:1"}])
    note = {"sentence": "S0004", "scripture": "isa-61-1", "verse": 1, "span": "謙卑",
            "original": "anawim", "reading": "", "explanation": "教授指出以賽亞所用的詞。"}
    draft["inline_notes"] = [note]
    problems, _, slides = chapter_slides.check([draft], KEPT, CHAPTER, cuv_root=tmp_path)
    assert not problems
    decisions = {"pieces": KEPT, "decisions": [{"id": p["id"], "keep": True} for p in KEPT]}
    _, timed = chapter_slides.placed(decisions, {"slides": slides})
    assert timed[0]["inline_notes"] == [{**note, "at": chapter_slides.sentences(KEPT)[3]["start"]}]
    for change in ({"span": "虛心"}, {"verse": 2}):
        draft["inline_notes"] = [{**note, **change}]
        problems, _, _ = chapter_slides.check([draft], KEPT, CHAPTER, cuv_root=tmp_path)
        assert problems
    # 綁在經文讀出之前的註解：只把時間移到讀出那一句，文字不變。
    draft["inline_notes"] = [{**note, "sentence": "S0002"}]
    problems, _, slides = chapter_slides.check([draft], KEPT, CHAPTER, cuv_root=tmp_path)
    assert not problems and slides[0]["inline_notes"] == [{**note, "sentence": "S0003"}]


def test_a_verse_read_again_later_on_the_slide_appears_again():
    kept = [{"id": "U001", "start": 0.0, "end": 100.0,
             "text": "以賽亞書六十一章七節。接著講別的。馬太五章五節。又是以賽亞六十一章第七節所預言的。"}]
    decisions = {"pieces": kept, "decisions": [{"id": "U001", "keep": True}]}
    record = {"slides": [dict(slide("S0001", points=({"sentence": "S0001", "text": "一"},
                                                     {"sentence": "S0003", "text": "二"})),
                              last="S0004", scriptures=[{"sentence": "S0001", "slug": "isa-61-7"},
                                                        {"sentence": "S0003", "slug": "mat-5-5"}])]}
    _, timed = chapter_slides.placed(decisions, record)
    assert [(s["sentence"], s["slug"]) for s in timed[0]["scriptures"]] == [
        ("S0001", "isa-61-7"), ("S0003", "mat-5-5"), ("S0004", "isa-61-7")]


def test_note_span_written_with_a_variant_character_is_located_and_saved_in_cuv_spelling(tmp_path):
    cuv = tmp_path / "cuv" / "MAT"
    cuv.mkdir(parents=True)
    (cuv / "5.json").write_text(json.dumps({"3": "天國是努力進入的，努力的人就得着了。"}, ensure_ascii=False))
    raw = [slide("S0001", "5:1-48", points=("一", "二"))]
    raw[0]["scriptures"] = [{"sentence": "S0001", "ref": "太5:3"}]
    raw[0]["inline_notes"] = [{"sentence": "S0001", "scripture": "mat-5-3", "verse": 3, "span": "努力的人就得著了",
                               "original": "", "reading": "", "explanation": ""}]
    problems, _, slides = chapter_slides.check(raw, KEPT, CHAPTER, cuv_root=tmp_path / "cuv")
    assert problems == []
    assert slides[0]["inline_notes"][0]["span"] == "努力的人就得着了"


def test_timing_findings_block_only_when_the_recording_shows_a_real_gap():
    lines = [{"id": "S0001", "start": 0.0}, {"id": "S0002", "start": 12.0}, {"id": "S0003", "start": 47.0},
             {"id": "S0004", "start": 115.0}]
    def timing(now, proposed):
        return {"slide": "S0001", "kind": "出現時間", "problem": "p", "fix": "f",
                "current_sentence": now, "proposed_sentence": proposed}
    # The lecture 2 rounds that went back and forth: 12 s early, 47 s late. Neither blocks.
    small = [timing("S0001", "S0002"), timing("S0003", "S0001")]
    chapter_slides.measure_timing(small, lines)
    review = {"grade": "B", "blocking": True, "findings": small}
    assert [f["measured_seconds"] for f in small] == [12.0, -47.0]
    assert not chapter_slides.review_blocks_publication(review)
    assert chapter_slides.actionable(review) == []
    # A point that gives away what he says over a minute later blocks; so does one placed nowhere.
    real = [timing("S0003", "S0004"), timing("S0001", "S9999")]
    chapter_slides.measure_timing(real, lines)
    assert [f["timing_blocks"] for f in real] == [True, True]
    assert chapter_slides.review_blocks_publication({"blocking": False, "findings": real})
    # Another kind of finding still follows the reviewer's flag.
    other = {"slide": "S0001", "kind": "完整", "problem": "p", "fix": "f", "current_sentence": "", "proposed_sentence": ""}
    assert chapter_slides.review_blocks_publication({"blocking": True, "findings": small + [other]})
    assert not chapter_slides.review_blocks_publication({"blocking": False, "findings": [other]})


def test_rejected_revision_can_be_rechecked_after_code_fix_without_model_or_budget_reset(tmp_path, monkeypatch):
    _decided(tmp_path)
    finding={'grade':'B','blocking':True,'findings':[{'slide':'S0001','kind':'完整','problem':'缺結論','fix':'補結論'}]}
    chapter_slides.review(CHAPTER,'L',data_base_dir=tmp_path,client=FakeClient([finding]))
    good={'slides':[slide('S0001','5:1-48',points=('理由','結論'))]}
    original_check=chapter_slides.checked
    monkeypatch.setattr(chapter_slides,'checked',lambda *a:(['proven input/check defect'],[],[]))
    assert chapter_slides.revise(CHAPTER,'L',data_base_dir=tmp_path,client=FakeClient([good]))['status']=='rejected'
    path=chapter_slides.record_path(tmp_path,'mat-5-1-48','L')
    rejection=path.with_suffix('.revision-1.rejected.json')
    rejected_bytes=rejection.read_bytes()
    monkeypatch.setattr(chapter_slides,'checked',original_check)
    assert chapter_slides.revise(CHAPTER,'L',data_base_dir=tmp_path,client=None,recheck_only=True)['status']=='revised'
    record=json.loads(path.read_text())
    assert record['revision']==1
    assert record['raw_slides']==good['slides']
    assert record['rechecked_revision_answer_sha256']==chapter_exposition.file_sha256(rejection)
    assert rejection.read_bytes()==rejected_bytes
    assert path.with_suffix('.revision-0.json').exists()


def test_rechecking_a_still_invalid_revision_preserves_original_rejection_and_saved_slides(tmp_path):
    _decided(tmp_path)
    finding={'grade':'B','blocking':True,'findings':[{'slide':'S0001','kind':'完整','problem':'缺結論','fix':'補結論'}]}
    chapter_slides.review(CHAPTER,'L',data_base_dir=tmp_path,client=FakeClient([finding]))
    chapter_slides.revise(CHAPTER,'L',data_base_dir=tmp_path,client=FakeClient([{'slides':[slide('S0002')]}]))
    path=chapter_slides.record_path(tmp_path,'mat-5-1-48','L');rejected=path.with_suffix('.revision-1.rejected.json')
    before=(path.read_bytes(),rejected.read_bytes())
    assert chapter_slides.revise(CHAPTER,'L',data_base_dir=tmp_path,client=None,recheck_only=True)['status']=='rejected'
    assert before==(path.read_bytes(),rejected.read_bytes())


def test_review_material_preserves_existing_argument_source_ids():
    raw=[slide('S0001','5:1-48',points=({'sentence':'S0001','text':'理由','source':'E1'}, {'sentence':'S0002','text':'結論','source':'C1'}))]
    problems,_,normalized=chapter_slides.check(raw,KEPT,CHAPTER)
    assert not problems
    packet=chapter_slides.show_slides(normalized)
    assert '(source: E1)' in packet and '(source: C1)' in packet


def test_inline_note_tracks_its_verse_when_displayed_range_is_split(tmp_path):
    book=tmp_path/'ISA';book.mkdir()
    (book/'61.json').write_text(json.dumps({'1':'叫我傳好信息給謙卑的人','2':'報告耶和華的恩年'}))
    draft=slide('S0001',scriptures=[{'sentence':'S0001','ref':'以賽亞書 61:1'},{'sentence':'S0003','ref':'以賽亞書 61:2'}])
    note={'sentence':'S0002','scripture':'isa-61-1-2','verse':2,'span':'恩年','original':'','reading':'','explanation':'恩典的年'}
    draft['inline_notes']=[note]
    problems,_,result=chapter_slides.check([draft],KEPT,CHAPTER,cuv_root=tmp_path)
    assert not problems
    assert result[0]['inline_notes']==[{**note,'scripture':'isa-61-2','sentence':'S0003'}]
    for wrong in ('mat-61-1-2','isa-60-1-2','isa-61-1'):
        draft['inline_notes']=[{**note,'scripture':wrong}]
        assert chapter_slides.check([draft],KEPT,CHAPTER,cuv_root=tmp_path)[0]


def test_runtime_model_packet_contains_resolved_owner_golden_with_source(monkeypatch):
    from backend.pipeline import chapter_slide_cases as cases
    owner={'kind':'not_before','at':'S9999','pattern':'答案','source_text':chapter_slides.sentences(KEPT)[1]['text'],'why':'答案不能跟問題一起提前出現'}
    monkeypatch.setattr(cases,'cases_for',lambda lecture:[owner])
    decisions={'pieces':KEPT,'decisions':[{'id':p['id'],'keep':True} for p in KEPT]}
    context={'schema_version':'chapter-slide-outline-context.v1','groups':[],'claims':[]}
    packet=chapter_slides.user_prompt('L',decisions,context)
    assert '本講已確認的 golden 約束' in packet
    assert '答案不能跟問題一起提前出現' in packet
    assert '"at": "S0002"' in packet and '"at": "S9999"' not in packet
    assert owner['source_text'] in packet
    assert '優先於一般的提早引入規則' in packet
    assert '不得把已合規的答案時間判為過晚' in packet


def test_owner_packet_does_not_silently_reuse_missing_source_anchor(monkeypatch):
    from backend.pipeline import chapter_slide_cases as cases
    import pytest
    monkeypatch.setattr(cases,'cases_for',lambda lecture:[{'kind':'point','at':'S0001','source_text':'本講沒有這句','pattern':'答案','why':'old case'}])
    decisions={'pieces':KEPT,'decisions':[{'id':p['id'],'keep':True} for p in KEPT]}
    with pytest.raises(ValueError,match='missing or ambiguous'):
        chapter_slides.user_prompt('L',decisions,{'schema_version':'chapter-slide-outline-context.v1','groups':[],'claims':[]})


def test_chapter_gpt_entry_uses_owner_ten_minute_timeout(monkeypatch):
    from backend.pipeline import codex_subscription_client as codex
    seen={}
    def capture(**kwargs):seen.update(kwargs);return object()
    monkeypatch.setattr(codex,'CodexSubscriptionClient',capture)
    chapter_exposition.chapter_client('gpt-6.1-sol')
    assert seen['timeout_seconds']==600


def test_adopted_review_decisions_require_saved_review_and_same_source(tmp_path):
    old = tmp_path / 'L.review-3.json'
    old.write_text('{"findings": ["original verdict stays immutable"]}')
    before = old.read_bytes()
    decision = {'decisions_sha256': 'source-sha',
                'source_review_sha256': chapter_exposition.file_sha256(old),
                'decision': '通常譯詞比較留在要點，不作本節希臘原文短註。'}
    record = {'accepted_review_resolutions': [decision]}
    prompt = chapter_slides.accepted_review_context(record, decisions_sha256='source-sha', review_directory=tmp_path)
    assert decision['decision'] in prompt
    assert old.read_bytes() == before
    with pytest.raises(ValueError, match='stale source'):
        chapter_slides.accepted_review_context(record, decisions_sha256='changed-source', review_directory=tmp_path)
    old.unlink()
    with pytest.raises(ValueError, match='missing original review'):
        chapter_slides.accepted_review_context(record, decisions_sha256='source-sha', review_directory=tmp_path)
    assert chapter_slides.accepted_review_context({}, decisions_sha256='x', review_directory=tmp_path) == ''


def test_prayer_unit_allows_its_matthew_six_and_seven_verses():
    from dataclasses import replace

    from backend.pipeline.chapter_material import Chapter
    from backend.pipeline.chapter_slides import parse_slide_verses, system_prompt

    chapter = Chapter('mat-6-1-34', 'NYSC', ())
    scoped = replace(chapter, slide_scope=('mat-6-8-34', 'mat-7-1-11'))
    assert parse_slide_verses('6:12; 7:1–6', scoped) == ['mat-6-12', 'mat-7-1-6']
    assert parse_slide_verses('7:7–12', scoped) is None
    assert parse_slide_verses('7:1–6', chapter) is None
    assert '6:8–34、7:1–11' in system_prompt(scoped)


def test_overlap_merges_only_within_the_same_sentence_book_and_chapter():
    inputs = [{"sentence": at, "slug": slug} for at, slug in (
        ("S0001", "1pe-2-13-14"), ("S0001", "1pe-2-14-15"), ("S0001", "1pe-2-16"),
        ("S0001", "1pe-3-13"), ("S0001", "2pe-2-13"), ("S0002", "1pe-2-14"),
    )]
    assert chapter_slides.merge_scripture_ranges(inputs, ["S0001", "S0002"]) == [
        {"sentence": at, "slug": slug} for at, slug in (
            ("S0001", "1pe-2-13-15"), ("S0001", "1pe-2-16"),
            ("S0001", "1pe-3-13"), ("S0001", "2pe-2-13"), ("S0002", "1pe-2-14"),
        )
    ]
