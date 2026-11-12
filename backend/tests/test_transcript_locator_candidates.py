import pytest

from backend.pipeline.transcript_locator_candidates import calibration_rules, candidate_packet


def span(key, quote):
    return {"paragraph_key": key, "start_marker": quote, "end_marker": quote}


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
        return {"decisions": [{"record_key": "fragment", "spans": [span(key, live[key]["text"]) for key in keys], "reason": "同一處發言"}]}

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
    answer = {"decisions": [{"record_key": "fragment", "spans": [span("S0002", "新版")], "reason": "候選"}]}
    rules, pending = calibration_rules([packet], answer, records, {"paragraphs": live}, {})
    assert not rules and pending == {"fragment": "no_verified_clock_match"}


def test_repeated_reference_uses_consumer_slots_and_rejects_wrong_order():
    from backend.pipeline.transcript_locator_candidates import consumer_context

    key = "source_fragments/f"
    clock = {"media_time": 0, "media_end_time": 70}
    records = {
        key: {"revision": 1, "content_sha256": "old",
              "payload": {"verbatim_excerpt": "對啊。", **clock}},
        "source_fragments/m": {"payload": {"verbatim_excerpt": "中間的引文"}},
        "evidence_steps/e": {"payload": {
            "statement": "教授確認兩個問題。", "source_fragment_ids": ["f", "m", "f"]}},
    }
    row = {"record_key": key, "old_paragraph": "S0001", "quote": "對啊。",
           "direct_consumers": ["evidence_steps/e"]}
    live = {
        f"S{i:04d}": {"text": text, "start_time": i * 10, "end_time": i * 10 + 5}
        for i, text in enumerate(("問題一？對啊。", "中間的引文", "問題二？對啊。", "別句。"), 1)
    }
    packet = candidate_packet(row, {}, live, clock)
    packet["consumers"] = consumer_context(row, records, {
        "source_fragments/m": {"target_paragraph": "S0002"},
    })
    assert [c["target"] for c in packet["consumers"][0]["citations"]] == [True, False, True]
    new = {"paragraphs": live, "body_sha256": "new"}
    for selected, expected in [(["S0001", "S0003"], True), (["S0003", "S0001"], False),
                               (["S0001"], False), (["S0001", "S0004"], False)]:
        answer = {"decisions": [{"record_key": key, "spans": [span(pk, "對啊。" if pk in {"S0001", "S0003"} else live[pk]["text"]) for pk in selected], "reason": "問答"}]}
        rules, pending = calibration_rules([packet], answer, records, new, {})
        assert bool(rules) == expected and bool(pending) != expected
        if expected:
            assert rules[key]["consumer_occurrence_segments"] == {"evidence_steps/e": [0, 1]}


def test_candidate_handoff_keeps_context_only_consumers_out_of_fragment_packets():
    from backend.pipeline.transcript_locator_candidates import prepare_packets

    fragment_key = "source_fragments/f"
    consumer_key = "questions/q"
    clock = {"media_time": 10, "media_end_time": 20}
    row = {"record_key": fragment_key, "old_paragraph": "S0001",
           "quote": "什麼意思？", "target_paragraphs": ["S0002"]}
    records = {
        fragment_key: {"payload": {**clock, "verbatim_excerpt": "什麼意思？"}},
        consumer_key: {"payload": {"text": "五個丈夫是什麼意思？", "source_fragment_ids": ["f"]}},
    }
    exception = {"record_key": consumer_key, "reason": "consumer_requires_substantive_source",
                 "context_fragment_ids": ["f"]}
    old = {"paragraphs": {"S0001": {"text": "五個丈夫。什麼意思？"}}}
    new = {"paragraphs": {"S0002": {"text": "五個丈夫是什麼意思？",
                                  "start_time": 12, "end_time": 18}}}
    result = prepare_packets({"exceptions": [row, exception]}, records, old, new, {},
                             comparison={"fragments": [{**row, "status": "context_only"}]})
    assert [packet["record_key"] for packet in result["packets"]] == [fragment_key]
    assert result["consumer_exceptions"] == [{
        **exception, "statement": "五個丈夫是什麼意思？",
        "citations": [{"record_key": fragment_key, "old_quote": "什麼意思？",
                       "target_paragraphs": ["S0002"], "bound_paragraph": None}],
    }]


def test_verified_cross_paragraph_rule_requires_both_edges_and_matching_clock():
    from backend.pipeline.transcript_evidence_apply import verified_cross_paragraph_rule

    first = "保羅曾經結過婚嘛。"
    second = "可能是他的太太早死，他就沒有再婚"
    row = {
        "reason": "source_located_requires_refresh",
        "quote": "保羅曾經結過婚，可能是他的太太早死，他就沒有再婚。",
        "alignment": {"both_endpoints_matched": True, "left_anchor": {"literal": "前文"},
                      "right_anchor": {"literal": "後文"}},
        "candidate_segments": [
            {"paragraph_key": "S0002", "verbatim_excerpt": first, "media_time": 12, "media_end_time": 16},
            {"paragraph_key": "S0003", "verbatim_excerpt": second, "media_time": 16, "media_end_time": 20},
        ],
    }
    state = {"revision": 3, "content_sha256": "old", "payload": {"media_time": 10, "media_end_time": 22}}
    new = {"body_sha256": "new", "paragraphs": {"S0002": {"text": first}, "S0003": {"text": second}}}

    rule = verified_cross_paragraph_rule(row, state, new)
    assert rule["before_pin"] == [3, "old"]
    assert [segment["verbatim_excerpt"] for segment in rule["segments"]] == [first, second]
    assert verified_cross_paragraph_rule(
        {**row, "alignment": {**row["alignment"], "both_endpoints_matched": False}}, state, new,
    ) is None
    assert verified_cross_paragraph_rule(
        row, {**state, "payload": {"media_time": 100, "media_end_time": 200}}, new,
    ) is None


def test_resolution_calibrates_fragment_and_context_consumer_and_reuses_saved_reply(tmp_path):
    from backend.pipeline.transcript_locator_resolution import resolve_packets
    from backend.pipeline.transcript_claim_migration import read

    fragment = "source_fragments/f"
    parent = "source_fragments/p"
    question = "questions/q"
    clock = {"media_time": 5, "media_end_time": 20}
    records = {
        fragment: {"revision": 1, "content_sha256": "f", "payload": clock},
        parent: {"revision": 1, "content_sha256": "p",
                 "payload": {**clock, "verbatim_excerpt": "什麼意思？"}},
        question: {"revision": 2, "content_sha256": "q", "payload": {}},
    }
    paragraphs = {"S0001": {"text": "你已經有五個丈夫，這是什麼意思？",
                            "start_time": 10, "end_time": 15}}
    new = {"paragraphs": paragraphs, "body_sha256": "new"}
    packet = candidate_packet(
        {"record_key": fragment, "quote": "五個丈夫", "old_paragraph": "S0001"},
        {}, paragraphs, clock,
    )
    bundle = {"packets": [packet], "clock_proofs": {}, "unscoped": [],
              "consumer_exceptions": [{"record_key": question, "statement": "五個丈夫是什麼意思？",
                                       "context_fragment_ids": ["p"], "citations": []}]}
    calls = []

    def respond(batch):
        calls.append(batch)
        return {"id": "saved"}, {"decisions": [
            {"record_key": p["record_key"], "spans": [span("S0001", paragraphs["S0001"]["text"])], "reason": "原聲中的問題"}
            for p in batch
        ]}

    output = resolve_packets(tmp_path, bundle, records, new, {}, call=respond)
    calibration = read(output)
    assert set(calibration["fragments"]) == {fragment, parent}
    assert set(calibration["consumers"]) == {question}
    assert calibration["fragments"][parent]["segments"][0]["verbatim_excerpt"] == "什麼意思？"
    assert read(tmp_path / "unresolved.json") == {}
    assert resolve_packets(tmp_path, bundle, records, new, {}, call=respond) == output
    assert len(calls) == 1


def test_live_quote_uses_exact_unique_boundaries_and_rejects_whole_paragraph_overreach():
    text = "對啊。聽眾又提出另一個完全不同的問題，然後教授花了很長的時間詳細解答這個新的問題。"
    key = "source_fragments/f"
    paragraph = "S0001"
    new = {"paragraphs": {paragraph: {"text": text, "start_time": 10, "end_time": 15}},
           "body_sha256": "new"}
    clock = {"media_time": 5, "media_end_time": 20}
    records = {key: {"revision": 1, "content_sha256": "old", "payload": clock}}
    packet = candidate_packet({"record_key": key, "quote": "對啊。", "old_paragraph": paragraph},
                              {}, new["paragraphs"], clock)

    def resolve(quote):
        return calibration_rules([packet], {"decisions": [{
            "record_key": key, "spans": [span(paragraph, quote)], "reason": "同一處",
        }]}, records, new, {})

    rules, pending = resolve("對啊。")
    assert not pending
    assert rules[key]["segments"] == [{"paragraph_key": paragraph, "verbatim_excerpt": "對啊。"}]
    rules, pending = resolve(text)
    assert not rules and pending[key] == "selected_quote_is_too_broad"
    rules, pending = resolve("不存在的文字")
    assert not rules and pending[key] == "no_valid_candidate_selection"
