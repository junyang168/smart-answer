
import pytest

from backend.pipeline import transcript_gemini_proofreading as flow


def test_start_stage_reuses_only_its_verified_upstream_checkpoints(tmp_path, monkeypatch):
    media = tmp_path / "source.mp3"
    media.write_bytes(b"test media")
    cues = [
        {"id": "0-1", "start": 0, "end": 4, "speaker": "w0:0", "text": "自己要要要這樣做。"},
        {"id": "0-2", "start": 4, "end": 8, "speaker": "w0:1", "text": "可以問一下嗎？"},
    ]
    calls = []

    def roles(*_):
        calls.append("speakers")
        return {"w0:0": "professor", "w0:1": "audience"}

    def repeats(*_):
        calls.append("repetitions")
        return [{"id": "0-1", "quote": "要要要這樣", "replace": "要這樣"}]

    monkeypatch.setattr(flow, "speaker_roles", roles)
    monkeypatch.setattr(flow, "repetition_proposals", repeats)
    first = tmp_path / "first"
    original = flow.run(cues, media, first)
    assert calls == ["speakers", "repetitions"]
    assert original["paragraphs"][0]["text"] == "**教授**：自己~~要要~~要這樣做。\n\n**聽眾**：可以問一下嗎？"
    calls.clear()
    assert flow.run(cues, media, tmp_path / "second", start="layout", prior=first) == original
    assert calls == []
    assert flow.run(cues, media, tmp_path / "third", start="repetitions", prior=first) == original
    assert calls == ["repetitions"]
    media.write_bytes(b"other source")
    with pytest.raises(ValueError, match="different audio"):
        flow.run(cues, media, tmp_path / "wrong", start="layout", prior=first)


def test_layout_preserves_preexisting_human_row():
    protected = {"1": {"index": "1", "text": "已校對原文", "review": "confirmed"}}
    cues = [{"id": "1", "start": 0, "end": 4, "speaker": "spk", "text": "這是底稿。"}]
    result = flow.editor_layout(flow.proofreading_blocks(cues), {}, [], protected)
    assert result["paragraphs"] == list(protected.values())


def test_full_runner_can_restart_at_flash_without_transcribing_again(tmp_path, monkeypatch):
    media = tmp_path / "audio.mp3"
    media.write_bytes(b"source")
    cue = {"id": "w0-1", "start": 0, "end": 8, "speaker": "w0:spk:0", "text": "要要要說明。"}
    calls = []

    def transcribe(*_):
        calls.append("transcribe")
        return {"words": [{"text": "要要要說明。", "speaker": "w0:spk:0", "start": 0, "end": 8}]}

    def flash(_, transcript, *args):
        assert transcript["words"][0]["text"] == "要要要說明。"
        calls.append("flash")
        return {"cues": [cue], "coverage": [], "omissions_for_review": []}

    def timing(_, refined, _out, _aligner, starts):
        calls.append("timing")
        assert len(refined["blocks"]) == 1
        assert starts == [{"start": 0, "method": "original_word"}]
        return {**refined, "blocks": [{**refined["blocks"][0], "start": 0.25, "timing_review": True}]}

    from backend.pipeline import transcript_semantic_segmentation as semantic

    def segment(turns, _):
        calls.append("segment")
        text, positions, _ = semantic.segmentation_input(turns)
        return semantic.apply_ends(text, positions, [len(positions)])

    monkeypatch.setattr(semantic, "segment", segment)

    monkeypatch.setattr(flow.audio, "transcribe", transcribe)
    monkeypatch.setattr(flow.audio, "refine", flash)
    monkeypatch.setattr(flow.audio, "align_starts", timing)
    monkeypatch.setattr(flow, "speaker_roles", lambda *_: {"w0:spk:0": "professor"})
    monkeypatch.setattr(flow, "repetition_proposals", lambda *_: [
        {"id": "p1-t1", "quote": "要要要說明", "replace": "要說明"},
    ])
    original = tmp_path / "complete"
    aligner = tmp_path / "aligner"
    expected = flow.run(None, media, original, aligner=aligner)
    assert expected["paragraphs"][0]["start"] == 0.25
    assert expected["paragraphs"][0]["timing_review"] is True
    assert calls == ["transcribe", "flash", "segment", "timing"]
    calls.clear()
    assert flow.run(None, media, tmp_path / "retimed", start="timing", prior=original, aligner=aligner) == expected
    assert calls == ["segment", "timing"]
    calls.clear()
    assert flow.run(None, media, tmp_path / "new", start="flash", prior=original, aligner=aligner) == expected
    assert calls == ["flash", "segment", "timing"]
    calls.clear()
    assert flow.run(None, media, tmp_path / "layout", start="layout", prior=original, aligner=aligner) == expected
    assert calls == []
    checkpoint = original / "transcribe.json"
    checkpoint.write_text(checkpoint.read_text().replace("w0:spk:0", "tampered"))
    with pytest.raises(ValueError, match="Invalid transcribe checkpoint"):
        flow.run(None, media, tmp_path / "bad", start="flash", prior=original, aligner=aligner)


def test_asr_rejects_wrong_model_and_marks_inverted_clock():
    result = {"model": "gemini-3.5-transcribe", "status": "completed",
              "steps": [{"content": [{"annotations": [
                  {"type": "word_info", "text": "甲", "speaker": "spk:0",
                   "start_offset": "2s", "end_offset": "1s"},
                  {"type": "word_info", "text": "乙", "speaker": "spk:0",
                   "start_offset": "2.1s", "end_offset": "2.5s"},
              ]}]}]}
    words = flow.audio.words(result, "0", 0, 5, 0, 5)
    assert "".join(row["text"] for row in words) == "甲乙"
    assert words[0]["timing_review"] and words[0]["start"] <= words[0]["end"]
    with pytest.raises(ValueError, match="did not complete"):
        flow.audio.words({**result, "model": "wrong"}, "0", 0, 5, 0, 5)


def test_flash_input_preserves_turns_without_cutting_at_punctuation():
    words = [
        {"text": "教授說。", "speaker": "professor", "start": 0, "end": 15},
        {"text": "再說一句。", "speaker": "professor", "start": 15, "end": 35},
        {"text": "怎麼理解？", "speaker": "audience", "start": 35, "end": 37},
        {"text": "可以這樣看。", "speaker": "professor", "start": 37, "end": 42},
    ]
    turns = flow.audio.refinement_turns(words)
    assert [turn["text"] for turn in turns] == ["教授說。再說一句。", "怎麼理解？", "可以這樣看。"]
    assert [turn["speaker"] for turn in turns] == ["professor", "audience", "professor"]


def test_semantic_segmentation_accepts_only_punctuation_offsets_and_keeps_text():
    from backend.pipeline import transcript_semantic_segmentation as semantic

    turns = [
        {"speaker": "聽眾", "text": "這是 3.14 嗎？"},
        {"speaker": "教授", "text": "對，可以這樣看。下一題。"},
    ]
    text, positions, marked = semantic.segmentation_input(turns)
    assert len(positions) == 3
    assert "3.14" in marked[0]["text"]
    assert marked[1]["text"].endswith("。〈3〉")
    paragraphs = semantic.apply_ends(text, positions, [2, 3])
    assert paragraphs[0]["text"] == "這是 3.14 嗎？對，可以這樣看。"
    assert "".join(p["text"] for p in paragraphs) == text
    for invalid in ([3, 2], [2, 2, 3], [0, 3], [2], [2.0, 3], [True, 3]):
        with pytest.raises(ValueError, match="ordered punctuation IDs"):
            semantic.apply_ends(text, positions, invalid)



def test_semantic_segmentation_offers_commas_only_inside_long_sentences():
    from backend.pipeline.transcript_semantic_segmentation import segmentation_input

    short = "你要聽我的話，人家馬上把你踢出去。"
    long = "前" * 75 + "，" + "後" * 75 + "。"
    text, positions, _ = segmentation_input([{"speaker": "教授", "text": short + long}])
    assert positions == [len(short), len(short) + 76, len(text)]



def test_semantic_playback_spans_keep_source_turns_across_punctuation_cut():
    from backend.pipeline import transcript_semantic_segmentation as semantic

    turns = [
        {"id": "t1", "speaker": "聽眾", "text": "為何？", "start": 2, "end": 4},
        {"id": "t2", "speaker": "教授", "text": "可以。下題。", "start": 4, "end": 10},
    ]
    text, positions, _ = semantic.segmentation_input(turns)
    blocks = semantic.playback_blocks(semantic.apply_ends(text, positions, [2, 3]), turns)
    assert [(b["start"], b["end"]) for b in blocks] == [(2, 7), (7, 10)]
    assert [[(c["speaker"], c["text"]) for c in b["cues"]] for b in blocks] == [
        [("聽眾", "為何？"), ("教授", "可以。")], [("教授", "下題。")],
    ]


def test_semantic_segmentation_preserves_unpunctuated_tail():
    from backend.pipeline import transcript_semantic_segmentation as semantic

    turns = [{"speaker": "教授", "text": "第一個觀點。第二個觀點。最後幾個字 "}]
    text, positions, _ = semantic.segmentation_input(turns)
    paragraphs = semantic.apply_ends(text, positions, [1, 2])
    assert [p["text"] for p in paragraphs] == ["第一個觀點。", "第二個觀點。最後幾個字 "]


def test_word_timing_uses_word_edges_and_rejects_uncertain_clock():
    from backend.pipeline.transcript_word_timing import locate_paragraph_edges

    text = "這是第一句。這是第二句。"
    turns = [{"text": text, "speaker": "w0:0", "start": 0, "end": 8}]
    words = [{"text": "这是第一句", "speaker": "w0:0", "start": 0.2, "end": 2.1},
             {"text": "这是第二句", "speaker": "w0:0", "start": 3.0, "end": 7.5}]
    paragraphs = [{"start": 0, "end": 6}, {"start": 6, "end": len(text)}]
    assert locate_paragraph_edges(paragraphs, turns, words) == [
        {"start": 0.2, "end": 2.1}, {"start": 3.0, "end": 7.5},
    ]
    words[1]["timing_review"] = True
    assert locate_paragraph_edges(paragraphs, turns, words)[1] == {"start": None, "end": None}


def test_word_timing_projects_flash_corrections_through_original_turn():
    from backend.pipeline.transcript_word_timing import locate_starts_from_base

    turn = {"id": "t1", "speaker": "w0:0", "start": 1.0, "end": 4.0}
    base = [{**turn, "text": "耶蘇說。"}]
    words = [{**turn, "text": "耶苏", "end": 2.0},
             {**turn, "text": "说", "start": 2.0}]
    paragraphs = [{"start": 0, "end": 4}]
    assert locate_starts_from_base(paragraphs, [{**turn, "text": "耶穌說。"}], words, base) == [
        {"start": 1.0, "method": "original_word"},
    ]
    assert locate_starts_from_base(paragraphs, [{**turn, "text": "所以耶穌說。"}], words, base) == [None]


def test_start_only_timing_reuses_gemini_and_ends_at_next_start(tmp_path):
    refined = {"duration": 20, "blocks": [{"start": 0, "end": 3}, {"start": 7, "end": 10}]}
    starts = [{"start": 0.2, "method": "original_word"}, {"start": 6.8, "method": "exact_word_match"}]
    result = flow.audio.align_starts(tmp_path / "unused.mp3", refined, tmp_path, None, starts)
    assert [(b["start"], b["end"], b["timing_review"]) for b in result["blocks"]] == [
        (0.2, 6.8, False), (6.8, 20, False),
    ]
    assert not (tmp_path / "paragraph-start-timing").exists()


def test_punctuation_cannot_rewrite_transcript_or_protected_markup():
    from backend.pipeline.transcript_punctuation import validate_punctuation

    original = "**教授**：對對~~要要~~要這樣做\n\n**聽眾**：是嗎？"
    corrected = "**教授**：對，對，~~要要~~要這樣做。\n\n**聽眾**：是嗎？"
    assert validate_punctuation(original, corrected) == corrected
    with pytest.raises(ValueError, match="content"):
        validate_punctuation(original, corrected.replace("做", "說"))
    with pytest.raises(ValueError, match="markup"):
        validate_punctuation(original, corrected.replace("~~要要~~", "~~要，要~~"))
