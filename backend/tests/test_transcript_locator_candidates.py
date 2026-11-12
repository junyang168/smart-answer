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


def test_overlong_model_selection_trims_only_a_unique_literal_acknowledgment_prefix():
    from backend.pipeline.transcript_locator_candidates import narrow_overlong_quote

    quote = "那個是另外\n一個situation，對。"
    text = "Yeah, yeah, yeah. \n" + quote
    segment = {"paragraph_key": "S0119", "verbatim_excerpt": text}
    new = {"paragraphs": {"S0119": {"text": text}}}
    assert narrow_overlong_quote([segment], new, 24) == [
        {"paragraph_key": "S0119", "verbatim_excerpt": quote}
    ]
    assert narrow_overlong_quote([segment], {"paragraphs": {"S0119": {"text": text + quote}}}, 24) is None
    assert narrow_overlong_quote(
        [{**segment, "verbatim_excerpt": "對他來說，這是另外一個情況。"}], new, 24,
    ) is None


def test_short_fragment_ranks_the_complete_question_and_sibling_citation():
    clock = {"media_time": 0, "media_end_time": 100}
    paragraphs = {
        f"S{i:04}": {"text": "中文叫什麼？這是主動語態。", "start_time": i, "end_time": i + 1}
        for i in range(1, 10)
    }
    paragraphs["S0010"] = {"text": "這種募款，raise money。", "start_time": 10, "end_time": 11}
    row = {"record_key": "source_fragments/f", "quote": "中文叫什麼？", "old_paragraph": "S0001"}
    consumers = [{"statement": "是否明白「募款」，其中文所指為何？",
                  "citations": [{"target": False, "old_quote": "募款聽懂了？"}]}]
    packet = candidate_packet(row, {}, paragraphs, clock, consumers=consumers)
    keys = {p["paragraph_key"] for p in packet["candidates"]}
    assert "S0010" in keys
    assert len(keys) == 8


def test_actual_hypotasso_question_trims_to_the_unique_literal_prompt():
    from backend.pipeline.transcript_locator_candidates import narrow_overlong_quote

    question = "hypotasso是什麼？"
    text = question + "\n把自己放在對面的下面。把自己啊放在對方的下面。"
    new = {"paragraphs": {"S0161": {"text": text}}}
    assert narrow_overlong_quote(
        [{"paragraph_key": "S0161", "verbatim_excerpt": text}], new, 26,
        old_quote="Hypotasso 是什麼？",
    ) == [{"paragraph_key": "S0161", "verbatim_excerpt": question}]


def test_overlong_location_only_rechecks_the_selected_span_and_reuses_the_reply(tmp_path):
    from backend.pipeline.transcript_claim_migration import read
    from backend.pipeline.transcript_locator_resolution import resolve_packets

    key = "source_fragments/f"
    paragraph = "S0001"
    quote = "我那時候還不能夠回去。"
    broad = quote + "後面就講了完全不同的事情，跟這次引文沒有任何關係。"
    other = "後來我就回去了。"
    clock = {"media_time": 5, "media_end_time": 20}
    paragraphs = {paragraph: {"text": broad + other, "start_time": 10, "end_time": 15}}
    new = {"paragraphs": paragraphs, "body_sha256": "new"}
    records = {key: {"revision": 1, "content_sha256": "old", "payload": clock}}
    packet = candidate_packet({"record_key": key, "quote": "我現在不能回去。", "old_paragraph": paragraph},
                              {}, paragraphs, clock)
    bundle = {"packets": [packet], "clock_proofs": {}, "unscoped": [], "consumer_exceptions": []}
    for label, second, passes in [("valid", quote, True), ("outside", other, False)]:
        calls = []
        def respond(batch):
            calls.append(batch)
            if len(calls) == 2:
                assert batch[0]["boundary_review"]["previous_spans"] == [
                    {"paragraph_key": paragraph, "verbatim_excerpt": broad},
                ]
            return {}, {"decisions": [{"record_key": key,
                                       "spans": [span(paragraph, second if len(calls) == 2 else broad)],
                                       "reason": "同一次發言"}]}
        output = resolve_packets(tmp_path / label, bundle, records, new, {}, call=respond)
        assert (key in read(output)["fragments"]) is passes
        assert resolve_packets(tmp_path / label, bundle, records, new, {}, call=respond) == output
        assert len(calls) == 2


def test_consumer_keeps_adjacent_question_and_answer_with_one_verified_witness():
    from backend.pipeline.transcript_locator_resolution import consumer_rule

    key = "questions/q"
    parent = "source_fragments/p"
    clock = {"media_time": 50, "media_end_time": 100}
    question = "保羅用三節講妻子的責任，用更多篇幅講丈夫的責任。什麼意思？"
    answer = "丈夫要愛妻子，愛到妻子願意甘心順服。"
    paragraphs = {
        "S0207": {"text": question, "start_time": 60, "end_time": 70},
        "S0208": {"text": answer, "start_time": 70, "end_time": 80},
        "S0209": {"text": "另一個問題。", "start_time": 80, "end_time": 90},
    }
    new = {"paragraphs": paragraphs, "body_sha256": "new"}
    records = {
        parent: {"revision": 1, "content_sha256": "p", "payload": {**clock, "verbatim_excerpt": "什麼意思？"}},
        key: {"revision": 2, "content_sha256": "q", "payload": {}},
    }
    packet = candidate_packet({"record_key": key, "quote": "夫妻責任篇幅的差異是什麼意思？",
                               "old_paragraph": "S0207"}, {}, paragraphs, clock)
    packet["consumer"] = {"context_fragment_ids": ["p"], "citations": []}
    decision = {"record_key": key, "spans": [span("S0207", question), span("S0208", answer)], "reason": "完整問答"}
    rule, parents = consumer_rule(packet, decision, records, new)
    assert [s["paragraph_key"] for s in rule["segments"]] == ["S0207", "S0208"]
    assert parents[parent]["segments"] == [{"paragraph_key": "S0207", "verbatim_excerpt": "什麼意思？"}]
    assert consumer_rule(packet, {**decision, "spans": [span("S0207", question),
                                                         span("S0209", "另一個問題。")]}, records, new) is None


def test_blank_paragraph_does_not_hide_the_adjacent_spoken_context():
    clock = {"media_time": 0, "media_end_time": 100}
    paragraphs = {
        "S0201": {"text": "其實什麼東西啊？", "start_time": 10, "end_time": 20},
        "S0202": {"text": "\n", "start_time": 20, "end_time": 21},
        "S0203": {"text": "保羅用幾節聖經來談到妻子的責任？", "start_time": 21, "end_time": 30},
    }
    row = {"record_key": "source_fragments/f", "quote": "其實什麼東西啊？", "old_paragraph": "S0001"}
    packet = candidate_packet(row, {}, paragraphs, clock, limit=2)
    assert [p["paragraph_key"] for p in packet["candidates"]] == ["S0201", "S0203"]


def test_statement_first_keeps_question_context_separate_from_unsupported_evidence(tmp_path):
    from backend.pipeline.transcript_claim_migration import read
    from backend.pipeline.transcript_evidence_apply import calibrate, validate_fragment_locations
    from backend.pipeline.transcript_locator_resolution import resolve_packets

    question = "source_fragments/question"
    evidence = "source_fragments/evidence"
    clock = {"media_time": 5, "media_end_time": 20}
    text = "**聽眾**：……\n\n**教授**：對，不可離開。"
    paragraphs = {"S0145": {"index": "145", "text": text, "start_time": 10, "end_time": 15}}
    new = {"paragraphs": paragraphs, "body_sha256": "new"}
    records = {
        question: {"revision": 1, "content_sha256": "q", "payload": {
            **clock, "source_id": "s", "verbatim_excerpt": "聽眾舊問句",
        }},
        evidence: {"revision": 1, "content_sha256": "e", "payload": {
            **clock, "source_id": "s", "verbatim_excerpt": "安於不嫁",
        }},
    }
    packets = [candidate_packet({"record_key": key, "quote": row["payload"]["verbatim_excerpt"],
                                 "old_paragraph": "S0001"}, {}, paragraphs, clock)
               for key, row in records.items()]
    packets[0]["consumers"] = [{"record_key": "questions/q"}]
    packets[1]["consumers"] = [{"record_key": "evidence_steps/e"}]
    answer = {"decisions": [
        {"record_key": question, "spans": [span("S0145", "對，不可離開。")],
         "disposition": "question_context_only", "reason": "原問句省略，回答可定位"},
        {"record_key": evidence, "spans": [], "disposition": "no_match", "reason": "新版沒有支持"},
    ]}
    bundle = {"packets": packets, "clock_proofs": {}, "unscoped": [], "consumer_exceptions": []}
    out = resolve_packets(tmp_path, bundle, records, new, {}, statement_first=True,
                          call=lambda _: ({}, answer))
    rule = read(out)["fragments"][question]
    row = {"record_key": question, "status": "exception", "direct_consumers": ["questions/q"]}
    result = calibrate(row, records[question], rule, new, {})
    post = {**records[question]["payload"], **result["proposed_fields"]}
    assert post["verbatim_excerpt"] == "聽眾舊問句"
    assert post["anchor_state"] == "unresolved"
    assert post["question_context"][0]["verbatim_excerpt"] == "對，不可離開。"
    validate_fragment_locations({question: post}, new)
    assert read(tmp_path / "unresolved.json") == {evidence: "no_match"}
    assert evidence not in read(out)["fragments"]


def test_locator_capacity_wait_does_not_create_unknown_model_outcome(tmp_path):
    import pytest
    from backend.pipeline.media_batch.runtime import WaitingCapacity
    from backend.pipeline.transcript_locator_resolution import review_batch

    packet = {"record_key": "source_fragments/f"}
    def no_capacity(_):
        raise WaitingCapacity("no local slot")
    with pytest.raises(WaitingCapacity):
        review_batch(tmp_path, [packet], no_capacity)
    assert (tmp_path / "capacity-wait.json").exists()
    assert not (tmp_path / "started.json").exists()
    answer = {"decisions": [{"record_key": packet["record_key"], "spans": [], "reason": "不確定"}]}
    assert review_batch(tmp_path, [packet], lambda _: ({}, answer)) == {
        packet["record_key"]: answer["decisions"][0]
    }


def test_owner_confirmation_must_match_fragment_source_and_evidence():
    import pytest
    from backend.pipeline.transcript_evidence_apply import calibrate

    key = "source_fragments/f"
    row = {"record_key": key, "status": "exception", "direct_consumers": ["evidence_steps/e"]}
    state = {"revision": 4, "content_sha256": "old",
             "payload": {"source_id": "sermon", "verbatim_excerpt": "歷史文字"}}
    rule = {
        "before_pin": [4, "old"], "target_source_sha256": "new", "mode": "source_text_missing",
        "reason": "人工核實歸屬錯誤", "owner_confirmation": {
            "source_id": "sermon", "fragment_key": key, "evidence_key": "evidence_steps/e",
            "owner_response": "不是教授說的",
        },
    }
    result = calibrate(row, state, rule, {"body_sha256": "new"}, {})
    assert result["status"] == "source_text_missing"
    assert "verbatim_excerpt" not in result["proposed_fields"]
    rule["owner_confirmation"]["evidence_key"] = "evidence_steps/other"
    with pytest.raises(ValueError, match="owner confirmation"):
        calibrate(row, state, rule, {"body_sha256": "new"}, {})


def test_spoken_continuity_allows_blank_rows_but_not_skipped_speech():
    from backend.pipeline.transcript_locator_candidates import consecutive_spoken

    new = {"paragraphs": {
        "S0049": {"text": "第八節開始說了。"},
        "S0050": {"text": " \n"},
        "S0051": {"text": "我對著沒有嫁娶的和寡婦。"},
        "S0052": {"text": "這是另一句話。"},
    }}
    assert consecutive_spoken(["S0049", "S0051"], new)
    assert not consecutive_spoken(["S0049", "S0052"], new)
    assert not consecutive_spoken(["S0051", "S0049"], new)


def test_locator_live_transport_uses_codex_subscription(tmp_path, monkeypatch):
    from backend.pipeline import codex_subscription_client, transcript_locator_candidates

    packet = {"record_key": "source_fragments/f"}
    answer = {"decisions": [{"record_key": packet["record_key"], "spans": [], "reason": "不確定"}]}

    class Subscription:
        backend = "codex_subscription"
        last_usage = {"input_tokens": 100}
        last_raw_response = {"last_message": "saved"}

        def __init__(self, **kwargs):
            assert kwargs["model"] == "gpt-6.1-sol"
            self.authenticated = False

        def _verify_chatgpt_login(self):
            self.authenticated = True

        def generate_json(self, prompt, payload, schema):
            import json

            assert self.authenticated
            assert json.loads(payload) == [packet]
            assert schema == transcript_locator_candidates.SCHEMA
            return answer

    monkeypatch.setenv("MEDIA_BATCH_CAPACITY_ROOT", str(tmp_path))
    monkeypatch.setattr(codex_subscription_client, "CodexSubscriptionClient", Subscription)
    raw, result = transcript_locator_candidates.review_packets([packet])
    assert result == answer
    assert raw["backend"] == "codex_subscription"
    assert raw["usage"] == {"input_tokens": 100}
