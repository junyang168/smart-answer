from backend.pipeline.transcript_proofreading_layout import proofreading_blocks


def test_reference_and_answer_can_cross_short_pauses_without_changing_cues():
    cues = [
        dict(id="0-0", start=1, end=2, speaker="a", text="以弗所書"),
        dict(id="0-1", start=4.4, end=10, speaker="a", text="第五章，講的是什麼？"),
        dict(id="0-2", start=12.3, end=14, speaker="b", text="~~哦。~~彼此順服。"),
    ]
    blocks = proofreading_blocks(cues)
    assert len(blocks) == 1
    assert blocks[0] == {"start": 1, "end": 14, "cues": cues}


def test_does_not_join_across_long_pause_or_create_overlong_paragraph():
    cues = [
        dict(id="0-0", start=0, end=23, text="這是一句完整而且足夠長的獨立內容，現在講完。"),
        dict(id="0-1", start=26, end=34, text="接著。"),
        dict(id="1-0", start=42, end=44, text="另外。"),
    ]
    assert [block["cues"] for block in proofreading_blocks(cues)] == [[cue] for cue in cues]


def test_marks_clear_repetitions_without_changing_source_or_negation():
    from backend.pipeline.transcript_proofreading_layout import mark_repetition

    text = "他不是自己要要要這麼做。"
    result = mark_repetition(text, "自己要要要這麼做", "自己要這麼做")
    assert result == "他不是自己~~要要~~要這麼做。"
    assert result.replace("~~", "") == text
    assert mark_repetition("不是不是。", "不是不是", "不是") == "不是不是。"
    assert mark_repetition("要~~要要~~這樣。", "要要", "要") == "要~~要要~~這樣。"


def test_editor_uses_roles_and_keeps_unverified_speech_without_a_model_label():
    from backend.pipeline.transcript_proofreading_layout import proofreading_speaker_text

    cues = [{"speaker": "w0:spk:0", "text": "先說。"},
            {"speaker": "w0:spk:1", "text": "請問？"},
            {"speaker": "w1:spk:0", "text": "還有。"}]
    text, turns = proofreading_speaker_text(cues, {"w0:spk:0": "professor", "w0:spk:1": "audience"})
    assert text == "**教授**：先說。\n\n**聽眾**：請問？\n\n還有。"
    assert "".join(t["text"] for t in turns) == "".join(c["text"] for c in cues)


def test_professor_only_paragraph_omits_tags_across_asr_window_boundaries():
    from backend.pipeline.transcript_proofreading_layout import proofreading_speaker_text

    cues = [{"speaker": "w15:spk:1", "text": "先說。"},
            {"speaker": "w16:spk:0", "text": "接著說。"}]
    text, turns = proofreading_speaker_text(cues, {cue["speaker"]: "professor" for cue in cues})
    assert text == "先說。\n\n接著說。"
    assert [turn["role"] for turn in turns] == ["professor", "professor"]


def test_transcript_gpt_uses_subscription_client(monkeypatch):
    from backend.pipeline import transcript_subscription_reviewer as module

    class Subscription:
        last_usage = None

        def __init__(self, **options):
            assert options["model"] == "gpt-6.1-sol"

        def generate_json(self, prompt, text, schema):
            assert (prompt, text, schema) == ("instructions", "transcript", {})
            return {"edits": []}

    monkeypatch.setattr(module, "CodexSubscriptionClient", Subscription)
    reviewer = module.TranscriptSubscriptionReviewer("instructions", {})
    raw = reviewer.generate([{"role": "user", "content": "transcript"}])
    assert raw["backend"] == "codex_subscription"
    assert reviewer.text(raw) == '{"edits": []}'
