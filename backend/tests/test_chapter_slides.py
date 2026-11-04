"""按章页面的投影片：模型分的投影片要覆盖每个保留片段，读到的经文要在上面。"""

from backend.pipeline import chapter_slides
from backend.pipeline.chapter_material import Chapter

CHAPTER = Chapter(
    passage="mat-5-1-48",
    series="S",
    lectures=("L",),
    sections=("mat-5-1-12", "mat-5-13-16", "mat-5-17-20"),
)

KEPT = [
    {"id": "U001", "start": 0.0, "end": 100.0, "text": "耶穌上了山。這是結構。"},
    {"id": "U002", "start": 100.0, "end": 200.0, "text": "我們看以賽亞書六十一章一節。"},
    {"id": "U003", "start": 300.0, "end": 400.0, "text": "律法的一點一畫。"},
]


def slide(first, last, verses="5:3-5", scriptures=(), points=("一", "二")):
    return {"first": first, "last": last, "title": f"{first}-{last}", "points": list(points),
            "verses": verses, "scriptures": list(scriptures)}


def test_slides_must_cover_every_kept_piece_in_order():
    problems, _, _ = chapter_slides.check([slide("U001", "U001"), slide("U003", "U003", "5:18")], KEPT, CHAPTER)
    assert any("應從 U002" in p for p in problems)
    problems, _, _ = chapter_slides.check([slide("U001", "U002")], KEPT, CHAPTER)
    assert "漏了 1 個片段" in problems


def test_points_verses_and_scripture_references_are_checked():
    problems, _, _ = chapter_slides.check([
        slide("U001", "U001", points=("只有一條",)),
        slide("U002", "U002", verses="6:1", scriptures=[{"piece": "U001", "ref": "以賽亞書 61:1"}]),
        slide("U003", "U003", "5:18", scriptures=[{"piece": "U003", "ref": "某書 1:1"}]),
    ], KEPT, CHAPTER)
    joined = "\n".join(problems)
    assert "要點 1 條" in joined
    assert "經節讀不出來或不在這一章" in joined
    assert "片段 U001 不在這一張" in joined
    assert "經文出處讀不出來" in joined


def test_a_verse_read_in_the_transcript_but_missing_from_the_slide_is_listed():
    # 不作废（程序认出处会认错），但要列出来给负责人看。
    problems, unshown, slides = chapter_slides.check([
        slide("U001", "U002", "5:1-12"),
        slide("U003", "U003", "5:18"),
    ], KEPT, CHAPTER)
    assert problems == []
    assert any("isa-61-1" in item for item in unshown)
    problems, unshown, slides = chapter_slides.check([
        slide("U001", "U002", "5:1-12", scriptures=[{"piece": "U002", "ref": "以賽亞書 61:1-2"}]),
        slide("U003", "U003", "5:18"),
    ], KEPT, CHAPTER)
    assert unshown == []
    assert slides[0] == {"pieces": ["U001", "U002"], "title": "U001-U002", "points": ["一", "二"],
                         "verses": ["mat-5-1-12"], "scriptures": [{"piece": "U002", "slug": "isa-61-1-2"}]}


def test_a_verse_shows_when_he_reads_it_not_when_the_slide_starts():
    piece = {"id": "U002", "start": 100.0, "end": 200.0, "text": "前面說了很多很多話。我們看以賽亞書六十一章一節。"}
    assert chapter_slides.at_of(piece, "isa-61-1-2") > 100.0
    # 认不出出处的，从片段开头。
    assert chapter_slides.at_of(piece, "jas-1-21") == 100.0


def test_slides_are_dropped_once_the_keep_decisions_are_rerun(tmp_path):
    import json
    from backend.pipeline import chapter_exposition

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
