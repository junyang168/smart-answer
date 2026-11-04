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
    return {"first": first, "title": title or first, "points": list(points),
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
