"""教授釋經筆記按章页：母本切小节、朗读稿、归段、写成讲座按章页的存档。"""

from __future__ import annotations

from backend.pipeline import chapter_exposition, chapter_slides, notes_chapter as notes
from backend.pipeline.chapter_material import Chapter

NOTES = """## 一、八福的結構

### 釋經

#### 八福的動詞結構

太 5:3–10 共包含八個「有福了」的宣告。第1福使用 **εστιν**，即 be 動詞的現在式。

> 5:4（第2福）：παρακληθήσονται（將來式）——必得安慰

| 福 | 時態 |
| --- | --- |
| 1 | 現在 |

### 生活應用

#### 當下享受

我們今天就可以享受天國的福。

### 附錄

#### 課堂例子

王老師的轉機比喻。

## 二、山上變貌

#### 太 16:28 與 17:1

六天以後，耶穌帶著彼得上了高山。
"""


def test_parse_keeps_every_word_and_reads_categories() -> None:
    pieces = notes.parse(NOTES)
    assert notes.check_parse(NOTES, pieces) == []
    assert [(p["heading"], p["category"]) for p in pieces] == [
        ("一、八福的結構", "釋經"),
        ("八福的動詞結構", "釋經"),
        ("當下享受", "生活應用"),
        ("課堂例子", "附錄"),
        ("二、山上變貌", "釋經"),  # 新的大节重新从釋經算起
        ("太 16:28 與 17:1", "釋經"),
    ]
    text = "".join(pieces[1]["sentences"])
    assert "現在" not in text.replace("現在式", "")  # 表格不念
    assert "**" not in text and ">" not in text


def test_check_parse_catches_a_dropped_sentence() -> None:
    pieces = notes.parse(NOTES)
    pieces[2]["sentences"] = pieces[2]["sentences"][:1]
    assert notes.check_parse(NOTES, pieces)


def test_reading_script_says_references_and_switches_voice_for_greek() -> None:
    assert notes.speak("太 5:3–10 共包含") == "馬太福音五章三節到十節 共包含"
    assert notes.speak("（5:13–7:29）") == "（五章十三節到七章二十九節）"
    assert notes.speak("詩篇 119:105") == "詩篇一百一十九章一百零五節"
    assert notes.voice_parts("均使用 εστιν，即現在式") == [
        ("zh", "均使用 "), ("el", "εστιν"), ("zh", "，即現在式"),
    ]


def test_ssml_stays_under_the_voice_limit() -> None:
    sentence = {"id": "S0001", "piece": "N001", "text": "x", "voice": [("zh", "甲"), ("el", "λόγος"), ("zh", "乙")]}
    script = [{**sentence, "id": f"S{i:04d}", "piece": f"N{i // 3:03d}"} for i in range(1, 200)]
    for chunk in notes._chunks(script):
        assert notes.ssml(chunk, set()).count("<voice ") <= 50
    # 接连的普通话并成一个 <voice>，书签都在
    two = notes.ssml([{**sentence, "voice": [("zh", "甲")]}, {**sentence, "id": "S0002", "voice": [("zh", "乙")]}], set())
    assert two.count("<voice ") == 1 and 'mark="S0002"' in two


def test_verses_cross_chapters_and_are_checked() -> None:
    pieces = [{"id": "N001", "sentences": ["太 16:28"], "heading": ""}, {"id": "N002", "sentences": ["x"], "heading": ""}]
    problems, verses = notes.check_verses([{"id": "N001", "verses": "16:28-17:8"}, {"id": "N002", "verses": "5:49"}], pieces)
    assert verses == {"N001": [[16, 28, 17, 8]]}
    assert any("N002" in p for p in problems)
    assert notes._in_chapter(verses["N001"], 16) == "mat-16-28"
    assert notes._in_chapter(verses["N001"], 17) == "mat-17-1-8"
    assert notes._in_chapter(verses["N001"], 15) is None
    flagged = notes.neighbour_chapters([{"id": "N003", "heading": "h", "sentences": ["見太 17:1"]}], {"N003": [[16, 28, 16, 28]]})
    assert flagged and "17" in flagged[0]


def test_record_works_with_the_lecture_page_rows(monkeypatch, tmp_path) -> None:
    """生活應用、附錄是灰色片段，跟在前一段释经后面；幻灯的句子用合成的书签时间。"""

    pieces = notes.parse(NOTES)[:4]
    verses = {"pieces": pieces, "title": "八福", "verses": {p["id"]: [[5, 3, 5, 10]] for p in pieces}}
    t = 0.0
    sentences = []
    for piece in pieces:
        for text in piece["sentences"]:
            sentences.append({"id": f"S{len(sentences) + 1:04d}", "piece": piece["id"], "start": t, "end": t + 30, "text": text})
            t += 30
    monkeypatch.setattr(notes, "load_verses", lambda *_: verses)
    monkeypatch.setattr(notes, "load_audio", lambda *_: {"sentences": sentences, "duration": t})
    (tmp_path / notes.WORK_DIR / "八福").mkdir(parents=True)
    (tmp_path / notes.WORK_DIR / "八福" / "audio.json").write_text("{}", encoding="utf-8")
    chapter = Chapter(passage="mat-5-1-48", series="", lectures=("notes_manuscript:八福",),
                      sections=("mat-5-1-12", "mat-5-13-48"))
    record = notes.chapter_record(chapter, "八福", tmp_path)
    assert [d["keep"] for d in record["decisions"]] == [True, True, False, False]
    assert [d.get("reason") for d in record["decisions"][2:]] == ["生活應用", "附錄"]
    rows = chapter_exposition.lecture_rows(chapter, record)
    assert list(rows) == ["mat-5-1-12"]
    grey = [s for s in rows["mat-5-1-12"]["stretches"] if s.get("left_out")]
    assert grey and grey[0]["reasons"] == ["生活應用", "附錄"]
    lines = chapter_slides.sentences(chapter_slides.kept_pieces(record))
    assert [line["start"] for line in lines][:3] == [0.0, 30.0, 60.0]


def test_slide_prompts_say_ai_reading_for_notes() -> None:
    chapter = Chapter(passage="mat-5-1-48", series="", lectures=(), sections=("mat-5-1-48",))
    assert "AI 朗讀" in chapter_slides.system_prompt(chapter, "notes_manuscript:八福")
    assert "講座的錄音" in chapter_slides.system_prompt(chapter, "2011 NYSC 專題：馬太福音釋經（二）1")
    assert "筆記原文" in chapter_slides.for_lecture(chapter_slides.REVIEW_PROMPT, "notes_manuscript:八福")


def test_plain_drops_single_asterisk_italics_but_not_list_markers() -> None:
    assert notes.plain("成全（*fulfill*）律法") == "成全（fulfill）律法"
    assert notes.plain("* 第一點") == "* 第一點"


def test_review_runs_once_per_slide_version(monkeypatch, tmp_path) -> None:
    chapter = Chapter(passage="mat-5-1-48", series="", lectures=(), sections=("mat-5-1-48",))
    path = chapter_slides.record_path(tmp_path, chapter.passage, "L")
    path.parent.mkdir(parents=True)
    path.write_text('{"slides": [], "revision": 1}', encoding="utf-8")
    decisions = chapter_exposition.record_path(tmp_path, chapter.passage, "L")
    decisions.parent.mkdir(parents=True, exist_ok=True)
    decisions.write_text('{"pieces": [], "decisions": []}', encoding="utf-8")
    monkeypatch.setattr(chapter_slides, "user_prompt", lambda *_: "")
    monkeypatch.setattr(chapter_slides, "load_record", lambda *_: {"slides": [], "revision": 1})
    calls = []

    class Client:
        def generate_json(self, *_):
            calls.append(1)
            return {"grade": "A", "blocking": False, "findings": []}

    assert chapter_slides.review(chapter, "L", data_base_dir=tmp_path, client=Client())["status"] == "reviewed"
    assert chapter_slides.review(chapter, "L", data_base_dir=tmp_path, client=Client())["status"] == "unchanged"
    assert len(calls) == 1


def test_tables_are_kept_for_the_slide_and_paused_at_not_read() -> None:
    pieces = notes.parse(NOTES)
    assert notes.check_parse(NOTES, pieces) == []
    assert pieces[1]["tables"] == [{"after": 4, "rows": [["福", "時態"], ["1", "現在"]]}]
    script = notes.script_of(pieces)
    paused = [s for s in script if s.get("table")]
    assert len(paused) == 1 and paused[0]["table"]["pause_ms"] == 4200
    out = notes.ssml(script, set())
    assert f'<bookmark mark="T{paused[0]["id"]}"/><break time="4200ms"/>' in out
    assert "現在</" not in out and "時態" not in out  # 表格不念
    # 一张大表停顿超过 5 秒，接几个 <break>
    assert notes.table_pause_ms(30) == 20000
    big = [{**paused[0], "table": {"rows": [], "pause_ms": 12000}}]
    assert notes.ssml(big, set()).count("<break ") == 3


def test_a_dropped_table_row_fails_the_parse_check() -> None:
    pieces = notes.parse(NOTES)
    pieces[1]["tables"][0]["rows"].pop()
    assert any("表格" in p for p in notes.check_parse(NOTES, pieces))


def test_script_sha_input_is_unchanged_for_notes_without_tables() -> None:
    text = NOTES.replace("| 福 | 時態 |\n| --- | --- |\n| 1 | 現在 |\n", "")
    assert not any(s.get("table") for s in notes.script_of(notes.parse(text)))


def test_a_piece_goes_to_the_chapter_of_its_main_passage() -> None:
    # 交叉参照不拉进别章
    assert notes._in_chapter([[5, 31, 5, 32], [19, 3, 19, 9]], 19) is None
    assert notes._in_chapter([[5, 31, 5, 32], [19, 3, 19, 9]], 5) == "mat-5-31-32"
    # 整段结构归第一章，不铺到每一章
    assert notes._in_chapter([[5, 1, 7, 29]], 5) == "mat-5-1-48"
    assert notes._in_chapter([[5, 1, 7, 29]], 6) is None
    assert notes._in_chapter([[1, 1, 28, 20]], 14) is None


def test_the_bookmark_sits_on_the_first_part_with_words() -> None:
    sentence = {"id": "S0001", "piece": "N001", "text": "（λόγος.）", "voice": [("zh", "（"), ("el", "λόγος"), ("zh", ".）")]}
    out = notes.ssml([sentence], set())
    assert 'xml:lang="zh-CN"><voice name="zh-CN-YunzeNeural"><prosody rate="-10%" pitch="-2%">（</prosody>' in out
    assert '<bookmark mark="S0001"/>λόγος' in out
    long = [{"id": f"S{i:04d}", "piece": "N001", "text": "字" * 100, "voice": [("zh", "字" * 100)]} for i in range(1, 60)]
    assert max(sum(len(s["text"]) for s in chunk) for chunk in notes._chunks(long)) <= notes.HARD_CHUNK_CHARS
