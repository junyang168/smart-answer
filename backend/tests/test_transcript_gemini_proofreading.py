
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
        return {"words": ["original"]}

    def flash(_, transcript, *args):
        assert transcript["words"] == ["original"]
        calls.append("flash")
        return {"cues": [cue], "coverage": [], "omissions_for_review": []}

    def timing(_, refined, *args):
        calls.append("timing")
        return refined

    monkeypatch.setattr(flow.audio, "transcribe", transcribe)
    monkeypatch.setattr(flow.audio, "refine", flash)
    monkeypatch.setattr(flow.audio, "align", timing)
    monkeypatch.setattr(flow, "speaker_roles", lambda *_: {"w0:spk:0": "professor"})
    monkeypatch.setattr(flow, "repetition_proposals", lambda *_: [
        {"id": "w0-1", "quote": "要要要說明", "replace": "要說明"},
    ])
    original = tmp_path / "complete"
    expected = flow.run(None, media, original)
    assert calls == ["transcribe", "flash", "timing"]
    calls.clear()
    assert flow.run(None, media, tmp_path / "new", start="flash", prior=original) == expected
    assert calls == ["flash", "timing"]
    calls.clear()
    assert flow.run(None, media, tmp_path / "layout", start="layout", prior=original) == expected
    assert calls == []
    checkpoint = original / "transcribe.json"
    checkpoint.write_text(checkpoint.read_text().replace("original", "tampered"))
    with pytest.raises(ValueError, match="Invalid transcribe checkpoint"):
        flow.run(None, media, tmp_path / "bad", start="flash", prior=original)


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
