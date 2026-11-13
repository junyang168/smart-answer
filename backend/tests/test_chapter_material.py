"""按章页面：逐字稿切片、模型决定的核对、页面上的行。"""

import json

from backend.pipeline import chapter_exposition, chapter_material
from backend.pipeline.chapter_exposition import check, cut, lecture_rows, parse_verses
from backend.pipeline.chapter_material import Chapter

CHAPTER = Chapter(
    passage="mat-5-1-48",
    series="2011 NYSC 專題：馬太福音釋經（二）",
    lectures=("L",),
    sections=("mat-5-1-12", "mat-5-27-30", "mat-5-31-32"),
)


FIVE = Chapter(
    passage="mat-5-1-48",
    series="2011 NYSC 專題：馬太福音釋經（二）",
    lectures=tuple(f"2011 NYSC 專題：馬太福音釋經（二）{n}" for n in range(1, 7)),
    sections=("mat-5-1-12", "mat-5-13-16", "mat-5-17-20", "mat-5-21-26", "mat-5-27-30",
              "mat-5-31-32", "mat-5-33-37", "mat-5-38-42", "mat-5-43-48"),
)


def _write(path, chapters):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": chapter_material.SCHEMA_VERSION, "chapters": [
        {"passage": c.passage, "series": c.series, "source": c.source,
         "lectures": list(c.lectures), "sections": list(c.sections)} for c in chapters
    ]}, ensure_ascii=False))


def test_the_chapter_list_is_read_from_the_data_folder_and_reread_when_it_changes(tmp_path, monkeypatch):
    path = chapter_material.chapters_path(tmp_path)
    monkeypatch.setattr(chapter_material, "chapters_path", lambda data_base_dir=None: path)
    chapters = chapter_material._Chapters()
    assert dict(chapters) == {}
    _write(path, [FIVE])
    assert chapters["mat-5-1-48"] == FIVE
    seven = Chapter(passage="mat-7-1-29", series="S", lectures=("L",),
                    sections=("mat-7-1-6", "mat-7-7-29"))
    _write(path, [FIVE, seven])
    import os
    os.utime(path, ns=(1, path.stat().st_mtime_ns + 1_000_000))
    assert list(chapters) == ["mat-5-1-48", "mat-7-1-29"]


def test_a_chapter_whose_sections_do_not_cover_it_is_skipped_not_served(tmp_path):
    gap = Chapter(passage="mat-7-1-29", series="S", lectures=("L",), sections=("mat-7-1-6", "mat-7-13-29"))
    no_lecture = Chapter(passage="mat-6-1-34", series="S", lectures=(), sections=("mat-6-1-34",))
    path = chapter_material.chapters_path(tmp_path)
    _write(path, [FIVE, gap, no_lecture])
    assert list(chapter_material.load_chapters(path)) == ["mat-5-1-48"]
    assert chapter_material.problems(gap) and chapter_material.problems(no_lecture)
    assert chapter_material.problems(FIVE) == []


def test_a_verse_belongs_to_the_section_where_it_starts():
    chapter = FIVE
    assert chapter_material.section_of(chapter, "mat-5-28") == "mat-5-27-30"
    assert chapter_material.section_of(chapter, "mat-5-39-44") == "mat-5-38-42"
    assert chapter_material.section_of(chapter, "mat-5-17-48") == "mat-5-17-20"


def test_long_paragraphs_are_cut_at_sentence_ends_with_times_by_position():
    # （二）1 一个段落 13.7 分钟，前半释经、后半见证；要能分开决定。
    sentence = "這是一句話。" * 10  # 60 字
    sermon = {"segments": [
        {"text": sentence * 6, "start_time": 0},  # 360 字
        {"text": "短。", "start_time": 360},
    ]}
    pieces = cut(sermon)
    assert [p["id"] for p in pieces] == ["U001", "U002"]
    assert all(p["text"].endswith("。") for p in pieces)
    assert len(pieces[0]["text"]) <= chapter_exposition.PIECE_CHARS
    # 360 字 360 秒：时间按字的位置推算，模型不给时间。
    assert pieces[0]["start"] == 0 and pieces[1]["end"] == 360
    assert pieces[0]["end"] == pieces[1]["start"]


def test_empty_transcript_rows_give_no_piece():
    # （二）6 逐字稿有空洞，没有文字就没有片段可判断。
    sermon = {"segments": [{"text": "", "start_time": 0}, {"text": "有內容的一句話。", "start_time": 60}]}
    assert len(cut(sermon)) == 1


def test_verses_parse_within_the_chapter_only():
    assert parse_verses("5:27-28", CHAPTER) == ["mat-5-27-28"]
    assert parse_verses("5:3, 5:10", CHAPTER) == ["mat-5-3", "mat-5-10"]
    # （二）2 的模型用了分号；格式的分隔符不该让整堂作废。
    assert parse_verses("5:21-22; 5:27-28", CHAPTER) == ["mat-5-21-22", "mat-5-27-28"]
    assert parse_verses("6:13", CHAPTER) is None
    assert parse_verses("5:47-49", CHAPTER) is None
    assert parse_verses("無", CHAPTER) is None


PIECES = [{"id": f"U00{n}", "start": n * 60.0, "end": n * 60.0 + 60, "text": f"第{n}段。"} for n in range(1, 6)]


def keep(piece_id, verses="5:28", kind="釋經"):
    return {"id": piece_id, "keep": True, "verses": verses, "kind": kind}


def drop(piece_id, reason="見證，與第五章無關"):
    return {"id": piece_id, "keep": False, "reason": reason}


def test_every_piece_must_be_decided_or_the_whole_output_is_void():
    problems, _ = check([keep("U001"), drop("U002"), keep("U003"), keep("U004")], PIECES, CHAPTER)
    assert any("漏了 1 個片段" in p for p in problems)


def test_duplicates_unknown_pieces_and_bad_verses_are_rejected():
    decisions = [keep("U001"), keep("U001"), keep("U002", verses="6:13"), drop("U003", reason=""),
                 keep("U004", kind="其他"), drop("U005"), drop("U009")]
    problems, _ = check(decisions, PIECES, CHAPTER)
    joined = "\n".join(problems)
    assert "U001: 決定了兩次" in joined
    assert "U002: 經節讀不出來或不在這一章" in joined
    assert "U003: 不保留卻沒有理由" in joined
    assert "U004: 保留的片段沒有標釋經、神學或應用" in joined
    assert "U009: 不是這一堂的片段" in joined


def test_a_complete_answer_passes_and_is_normalised():
    decisions = [keep("U001"), drop("U002"), keep("U003", verses="5:28"), keep("U004", "5:32", "應用"), drop("U005")]
    problems, normalised = check(decisions, PIECES, CHAPTER)
    assert problems == []
    assert normalised[0] == {"id": "U001", "keep": True, "verses": ["mat-5-28"], "kind": "釋經"}
    assert normalised[1] == {"id": "U002", "keep": False, "reason": "見證，與第五章無關"}


def test_left_out_pieces_stay_inline_after_the_kept_piece_they_follow():
    # （二）3：5:28 的释经、中间三段见证、再回到 5:28；见证留在原处、标灰。
    record = {"pieces": PIECES, "decisions": check(
        [keep("U001"), drop("U002"), drop("U003", "另一個理由"), keep("U004"), keep("U005", "5:32")],
        PIECES, CHAPTER,
    )[1]}
    rows = lecture_rows(CHAPTER, record)
    assert set(rows) == {"mat-5-27-30", "mat-5-31-32"}
    stretches = rows["mat-5-27-30"]["stretches"]
    assert [(s["start"], s["end"], bool(s.get("left_out"))) for s in stretches] == [
        (60, 120, False), (120, 240, True), (240, 300, False),
    ]
    assert stretches[1]["reasons"] == ["見證，與第五章無關", "另一個理由"]
    assert stretches[1]["text"] == "第2段。\n\n第3段。"
    # 同一段里接连的保留片段并成一段录音。
    assert rows["mat-5-31-32"]["stretches"] == [{"start": 300, "end": 360}]


def test_left_out_pieces_before_any_kept_piece_join_the_first_kept_section():
    # （二）6：前一个多小时讲第六、七章，末尾才回到太 5。
    record = {"pieces": PIECES, "decisions": check(
        [drop("U001"), drop("U002"), drop("U003"), keep("U004", "5:3"), drop("U005")],
        PIECES, CHAPTER,
    )[1]}
    rows = lecture_rows(CHAPTER, record)
    assert list(rows) == ["mat-5-1-12"]
    assert [bool(s.get("left_out")) for s in rows["mat-5-1-12"]["stretches"]] == [True, False, True]


def test_a_lecture_with_nothing_kept_has_no_row():
    record = {"pieces": PIECES[:2], "decisions": check([drop("U001"), drop("U002")], PIECES[:2], CHAPTER)[1]}
    assert lecture_rows(CHAPTER, record) == {}


def test_saved_decisions_are_ignored_once_the_transcript_changes(tmp_path):
    target = chapter_exposition.record_path(tmp_path, "mat-5-1-48", "L")
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "schema_version": chapter_exposition.SCHEMA_VERSION,
        "transcript_sha256": "aaa",
        "pieces": [],
        "decisions": [],
    }))
    assert chapter_exposition.load_record(tmp_path, "mat-5-1-48", "L", "aaa") is not None
    assert chapter_exposition.load_record(tmp_path, "mat-5-1-48", "L", "bbb") is None
    assert chapter_exposition.load_record(tmp_path, "mat-5-1-48", "M", "aaa") is None


def test_slide_points_are_the_claims_own_evidence_steps_with_his_words():
    from backend.pipeline.original_audio_index import claim_points

    claims = {"c": {"claim_id": "c", "statement": "主張 c", "claim_type": "application",
                    "evidence_step_ids": ["q", "s1", "s2", "s3", "s4"]}}
    steps = {
        "q": {"statement": "問題本身", "step_type": "question"},
        "s1": {"statement": "第一步", "step_type": "literary_context", "source_fragment_ids": ["f1"]},
        "s2": {"statement": "第二步", "step_type": "original_language"},
        "s3": {"statement": "主張 c", "step_type": "reasoning"},
        "s4": {"statement": "第三步", "step_type": "reasoning"},
    }
    fragments = {"f1": {"fragment_id": "f1", "verbatim_excerpt": "他的原話"}}
    out = claim_points({"provenance": {"claim_ids": ["c"]}}, claims, steps, fragments)
    assert [p["text"] for p in out["points"]] == ["第一步", "第二步", "第三步"]
    assert out["points"][0]["quote"] == "他的原話"


def test_a_sentence_long_kept_piece_stays_with_the_argument_around_it():
    # （二）5：5:38-39 論證中間的「我們要愛仇敵。」3 秒，標了 5:44，5:43-48 多出 0 分鐘的一行。
    pieces = [
        {"id": "U001", "start": 0.0, "end": 100.0, "text": "以眼還眼。"},
        {"id": "U002", "start": 100.0, "end": 103.0, "text": "我們要愛仇敵。"},
        {"id": "U003", "start": 103.0, "end": 200.0, "text": "右臉左臉。"},
    ]
    chapter = Chapter(passage="mat-5-1-48", series="S", lectures=("L",), sections=("mat-5-38-42", "mat-5-43-48"))
    decisions = check([keep("U001", "5:38"), keep("U002", "5:44", "應用"), keep("U003", "5:39")], pieces, chapter)[1]
    rows = lecture_rows(chapter, {"pieces": pieces, "decisions": decisions})
    assert list(rows) == ["mat-5-38-42"]
    assert rows["mat-5-38-42"]["stretches"] == [{"start": 0.0, "end": 200.0}]


def test_the_slide_prompt_names_the_chapter_it_is_for():
    from backend.pipeline import chapter_material, chapter_slides

    thirteen = Chapter(passage="mat-13-1-58", series="S", lectures=("L",), sections=("mat-13-1-58",))
    prompt = chapter_slides.system_prompt(thirteen)
    assert "第 13 章" in prompt and "13:1-58" in prompt
    assert "第五章" not in prompt and "5:1-48" not in prompt


def test_chapter_16_retires_the_three_prototype_audio_entries(monkeypatch):
    from backend.pipeline import original_audio_index

    monkeypatch.setattr(original_audio_index.chapter_material, "CHAPTERS", {})
    assert dict(original_audio_index.passages())["mat-16-24-27"]
    monkeypatch.setattr(original_audio_index.chapter_material, "CHAPTERS", {"mat-16-1-28": None})
    assert original_audio_index.passages() == (("mat-16-1-28", "太16:1–28"),)
