"""原声同步 slide 的来源绑定与可播放边界。"""

from __future__ import annotations

from backend.pipeline.original_audio_index import (
    judgement_during,
    original_language_during,
    stretch,
)


def _sermon(text: str) -> dict:
    return {
        "segments": [
            {"index": "1", "text": text, "start_time": 0},
            {"index": "2", "text": "收尾。", "start_time": 120},
        ]
    }


def test_original_language_event_keeps_exact_transcript_source() -> None:
    text = (
        "耶穌說你是彼得（Petrus），我要把我的教會建造在這磐石（petra）上。"
        "這裡是在比較兩個希臘文詞。"
    )

    events = original_language_during([(0, 120, "")], _sermon(text))

    assert [event["original"] for event in events] == ["Petrus", "petra"]
    transcript = text + "收尾。"
    for event in events:
        span = event["transcript_span"]
        assert transcript[span["start"]:span["end"]] == event["transcript_excerpt"]
        assert event["source_kind"] == "transcript_explicit"


def test_grammar_event_keeps_the_greek_form_with_professors_label() -> None:
    text = (
        "這個標準天上已經決定好了。"
        "ἔσται δεδεμένον就是未來完成時態（Future Perfect Passive）。"
        "中文要用很多字才能翻。"
    )

    [event] = original_language_during([(0, 120, "")], _sermon(text))

    assert event["greek"] == "ἔσται δεδεμένον"
    assert event["original"] == "Future Perfect Passive"
    assert "就是未來完成時態" in event["transcript_excerpt"]


def test_ordinary_english_parenthesis_is_not_mislabeled_as_original_language() -> None:
    sermon = _sermon("你要完全了解信心的內涵（implication），才能明白這個問題。")

    assert original_language_during([(0, 120, "")], sermon) == []


def test_playback_stretches_never_run_past_media_duration() -> None:
    intervals = [
        (4000, 4010, "仍在录音内"),
        (4200, 4250, "估算落在 EOF 后"),
    ]

    result = stretch(intervals, media_duration=4135.2)

    assert result == [(3992, 4082, "仍在录音内")]
    assert all(0 <= start < end <= 4135.2 for start, end, _ in result)


def test_minimum_slide_duration_is_clamped_at_eof() -> None:
    result = stretch([(4120, 4121, "最后一句")], media_duration=4135.2)

    assert result == [(4112, 4135.2, "最后一句")]


def test_normalized_slide_title_keeps_claim_and_evidence_provenance() -> None:
    provenance = {
        "讲论要点": {
            "claim_ids": ["CL-1"],
            "evidence_step_ids": ["EV-1"],
            "source_fragment_ids": ["FR-1"],
        }
    }

    [mark] = judgement_during(
        [(0, 120, "讲论要点")],
        [(0, 120, "")],
        {"讲论要点": "mat-16-18"},
        provenance,
    )

    assert mark["provenance"] == provenance["讲论要点"]
