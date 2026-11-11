import pytest

from backend.pipeline.transcript_locator_candidates import calibration_rules, candidate_packet


def test_model_can_select_changed_passage_but_cannot_supply_its_own_location_or_text():
    row = dict(record_key="fragment", old_paragraph="S0001", quote="馬太五章三十二節",
               target_paragraphs=["S0002"])
    old = {"S0001": {"text": "馬太五章三十二節，指出婚姻的標準。"}}
    live = {"S0002": {"text": "馬太五章二十節，指出婚姻的標準。", "start_time": 20, "end_time": 29},
            "S0003": {"text": "另一處重複引文", "start_time": 100, "end_time": 110}}
    clock = {"media_time": 19, "media_end_time": 30}
    packet = candidate_packet(row, old, live, clock)
    assert [p["paragraph_key"] for p in packet["candidates"]] == ["S0002"]
    records = {"fragment": {"revision": 1, "content_sha256": "old", "payload": clock}}
    new = {"paragraphs": live, "body_sha256": "new"}

    def decide(keys):
        return {"decisions": [{"record_key": "fragment", "paragraph_keys": keys, "reason": "同一處發言"}]}

    rules, pending = calibration_rules([packet], decide(["S0002"]), records, new, {})
    assert not pending
    assert rules["fragment"]["segments"][0]["verbatim_excerpt"] == live["S0002"]["text"]
    assert rules["fragment"]["before_pin"] == [1, "old"]
    rules, pending = calibration_rules([packet], decide(["S0003"]), records, new, {})
    assert not rules and pending == {"fragment": "no_valid_candidate_selection"}
    with pytest.raises(ValueError, match="exact candidate packet"):
        calibration_rules([packet], {"decisions": []}, records, new, {})


def test_approximate_candidate_without_verified_clock_stays_unresolved():
    row = dict(record_key="fragment", old_paragraph="S0001", quote="舊稿",
               target_paragraphs=["S0002"])
    live = {"S0002": {"text": "新版", "start_time": 20, "end_time": 29}}
    packet = candidate_packet(row, {}, live, {})
    assert packet["candidates"]
    records = {"fragment": {"revision": 1, "content_sha256": "old", "payload": {}}}
    answer = {"decisions": [{"record_key": "fragment", "paragraph_keys": ["S0002"], "reason": "候選"}]}
    rules, pending = calibration_rules([packet], answer, records, {"paragraphs": live}, {})
    assert not rules and pending == {"fragment": "no_verified_clock_match"}
