import json
from pathlib import Path

import pytest

from backend.api.scripture import slide_reference_slugs
from backend.pipeline import chapter_exposition, chapter_slide_eval as ev, chapter_slides
from backend.pipeline.chapter_material import CHAPTERS


GOLD = Path(__file__).parent / "fixtures" / "chapter_slides" / "actual_candidate_eval_gold.json"


def test_gold_candidate_set_preserves_actual_source_and_distinguishes_four_criteria():
    cases = json.loads(GOLD.read_text())
    assert {c["criterion"] for c in cases} == ev.SEMANTIC_CRITERIA.keys()
    for case in cases:
        assert case["known_good"] != case["known_bad"]
        assert case["support_sentence"] in {line["id"] for line in case["source"]}


def test_coverage_checks_the_inference_in_both_authoring_and_eval():
    gold = next(c for c in json.loads(GOLD.read_text()) if c["id"] == "inclusio-links-prayer-to-conduct")
    selection = {"pieces": [{"id": "P1", "start": 0, "end": 60,
                             "text": "".join(row["text"] for row in gold["source"])}],
                 "decisions": [{"id": "P1", "keep": True}]}
    context = {"schema_version": "chapter-slide-outline-context.v1", "scope": "subunit", "claims": []}
    gen = chapter_slides.user_prompt("Lecture", selection, context)
    packet = ev.semantic_packet({"lecture": "Lecture", "claim_context": context, "slides": []}, selection)
    coverage = packet["criteria"]["coverage"]
    for text in (gen, coverage):
        assert "未交代理由怎樣支持結論，仍是漏步驟" in text
        assert "相鄰張的要點合起來要能讀出兩者的關係" in text
    assert "通過要指出實際表達該推論的要點" in coverage
    assert len(gold["across_slides_good"]) == 2


def test_hebrews_regression_detects_multiple_readings_in_one_composite_sentence(tmp_path):
    case = next(c for c in json.loads(GOLD.read_text()) if c["id"] == "hebrews-disjoint-readings")
    line = next(line["text"] for line in case["source"] if line["id"] == case["support_sentence"])
    kept = [{"id": "U1", "start": 0, "end": 15, "text": line}]
    bad = [{"first": "S0001", "title": "一次獻祭", "scriptures": [{"sentence": "S0001", "slug": "heb-10-12"}],
            "points": []}]
    ref = "希伯來書 10:12，14"
    assert slide_reference_slugs(ref) == ["heb-10-12", "heb-10-14"]
    assert slide_reference_slugs("希伯來書 10:12，14:2") == []
    assert ev.evaluate(bad, kept, candidates=["heb-10-12-14"])["problems"] == [
        "一次獻祭（S0001）: 他在念 heb-10-14，畫面沒有顯示這節經文"]
    raw = [{"first": "S0001", "title": "一次獻祭", "verses": "太 5:17–20",
            "points": [{"sentence": "S0001", "text": "一次贖罪祭"}, {"sentence": "S0001", "text": "經文對照"}],
            "scriptures": [{"sentence": "S0001", "ref": ref}]}]
    errors, _, good = chapter_slides.check(raw, kept, CHAPTERS["mat-5-1-48"])
    assert errors == []
    assert [item["slug"] for item in good[0]["scriptures"]] == ["heb-10-12", "heb-10-14"]
    assert ev.evaluate(good, kept, candidates=["heb-10-12-14"])["problems"] == []


@pytest.mark.parametrize("invalid", ["missing", "incomplete", "repeated", "failed", "stale", "published", "none"])
def test_saved_semantic_eval_requires_exact_complete_passing_candidate(tmp_path, invalid):
    selection = tmp_path / "selection.json"
    selection.write_text("{}")
    candidate = tmp_path / "slides.json"
    candidate.write_text(json.dumps({"slides": [{"first": "S0001"}]}))
    receipt = tmp_path / "semantic-eval.json"
    rows = [{"slide": "S0001", "criterion": k, "passed": True, "evidence": "test evidence"}
            for k in ev.SEMANTIC_CRITERIA]
    if invalid == "incomplete":
        rows.pop()
    if invalid == "repeated":
        rows.append(rows[0])
    if invalid == "failed":
        rows[0]["passed"] = False
    if invalid != "missing":
        receipt.write_text(json.dumps(ev.with_run({
            "schema_version": ev.SEMANTIC_SCHEMA_VERSION,
            "slides_sha256": ev.semantic_slide_sha256(candidate),
            "selection_sha256": chapter_exposition.file_sha256(selection),
            "checks": rows,
        })))
    if invalid == "stale":
        candidate.write_text(json.dumps({"slides": [{"first": "S0002"}]}))
    if invalid == "published":
        candidate.write_text(json.dumps({"slides": [{"first": "S0001"}], "publication": {"published_at": "now"}}))
    assert bool(ev.semantic_eval_problems(candidate, selection, receipt)) == (invalid not in {"published", "none"})


def test_unit_reader_uses_the_exact_semantic_eval_without_a_second_review(tmp_path):
    from backend.pipeline.chapter_listening_units import reviewed_unit_recording

    title = "Lecture"
    text = "第一個論點。第二個論點。"
    selection = tmp_path / f"{title}.selection.json"
    selection.write_text(json.dumps({
        "source_id": "source", "lecture": title, "transcript_sha256": "transcript",
        "pieces": [{"id": "P1", "source_paragraph_key": "K1", "start": 0, "end": 10, "text": text}],
        "decisions": [{"id": "P1", "keep": True}],
    }))
    edition = tmp_path / f"{title}.slides.json"
    edition.write_text(json.dumps({
        "semantic_eval": ev.SEMANTIC_SCHEMA_VERSION,
        "decisions_sha256": chapter_exposition.file_sha256(selection),
        "claim_context": {"instance": {"change_set_id": "current"}},
        "slides": [{"first": "S0001", "last": "S0002", "title": "論點", "verses": ["mat-5-1-12"],
                    "scriptures": [], "points": []}],
    }))
    unit = {"groups": [{"source_id": "source", "source_ranges": [
        {"paragraph_key": "K1", "start": 0, "end": 10, "text": text}]}]}
    entry = {"lecture": title, "source_id": "source", "changeset": "current", "source_sha256": "transcript"}
    with pytest.raises(ValueError, match="Missing or unreadable semantic Eval"):
        reviewed_unit_recording(tmp_path, unit, entry, {})
    (tmp_path / f"{title}.semantic-eval.json").write_text(json.dumps(ev.with_run({
        "schema_version": ev.SEMANTIC_SCHEMA_VERSION,
        "slides_sha256": ev.semantic_slide_sha256(edition),
        "selection_sha256": chapter_exposition.file_sha256(selection),
        "checks": [{"slide": "S0001", "criterion": k, "passed": True, "evidence": text}
                   for k in ev.SEMANTIC_CRITERIA],
    })))
    assert reviewed_unit_recording(tmp_path, unit, entry, {})["slides"][0]["title"] == "論點"


def test_saved_eval_can_require_title_to_cover_the_judicial_conclusion(tmp_path):
    gold = next(c for c in json.loads(GOLD.read_text()) if c['id'] == 'title-covers-judicial-conclusion')
    candidate = tmp_path / "slides.json"
    source = tmp_path / "selection.json"
    source.write_text(json.dumps({"source": gold["source"]}))
    bad, good, prior = gold['known_bad'], gold['known_good'], gold['preceding']
    assert prior['title'] == '「以眼還眼」是甚麼意思？'
    assert gold['support_sentence'] == 'S0058'
    assert '審判官' in next(line['text'] for line in gold['source'] if line['id'] == 'S0058')
    for slide, expected in ((bad, False), (good, True)):
        candidate.write_text(json.dumps({"slides": [prior, slide]}))
        receipt = tmp_path / "semantic-eval.json"
        receipt.write_text(json.dumps(ev.with_run({
            "schema_version": ev.SEMANTIC_SCHEMA_VERSION,
            "slides_sha256": ev.semantic_slide_sha256(candidate),
            "selection_sha256": chapter_exposition.file_sha256(source),
            "checks": [{"slide": s["first"], "criterion": k,
                        "passed": expected or s is prior or k != "coverage",
                        "evidence": "標題須包含 S0058 的司法原則。"}
                       for s in (prior, slide) for k in ev.SEMANTIC_CRITERIA],
        })))
        problems = ev.semantic_eval_problems(candidate, source, receipt)
        assert problems == ([] if expected else ["S0020 coverage: 標題須包含 S0058 的司法原則。"])


def test_semantic_eval_must_be_a_recorded_run_by_another_model(tmp_path):
    """Owner 2026-11-18: lecture 1's receipt was written by the slide builder and reused
    an older verdict (「沿用…該項未變」); it passed a point 12 s late."""
    selection = tmp_path / "selection.json"
    selection.write_text("{}")
    candidate = tmp_path / "slides.json"
    candidate.write_text(json.dumps({"model": "gpt-slides", "slides": [{"first": "S0001"}]}))
    receipt = tmp_path / "semantic-eval.json"
    base = {"schema_version": ev.SEMANTIC_SCHEMA_VERSION, "slides_sha256": ev.semantic_slide_sha256(candidate),
            "selection_sha256": chapter_exposition.file_sha256(selection),
            "checks": [{"slide": "S0001", "criterion": k, "passed": True, "evidence": "e"} for k in ev.SEMANTIC_CRITERIA]}
    for written, ok in ((base, False), (ev.with_run(base, "gpt-slides"), False),
                        ({**ev.with_run(base, "claude-opus-5-5"), "baseline_eval_sha256": "x"}, False),
                        (ev.with_run(base, "claude-opus-5-5"), True)):
        receipt.write_text(json.dumps(written))
        assert (ev.semantic_eval_problems(candidate, selection, receipt) == []) == ok


@pytest.mark.parametrize("changed_criterion", ["coverage", "timing"])
def test_final_verdict_must_match_opus_and_jev_evidence(tmp_path, changed_criterion):
    selection = tmp_path / "selection.json"
    selection.write_text("{}")
    candidate = tmp_path / "slides.json"
    slide_row = {"first": "S0001", "points": [{"sentence": "S0001"}]}
    candidate.write_text(json.dumps({"claim_context": {"scope": "subunit"}, "slides": [slide_row]}))
    receipt = tmp_path / "semantic-eval.json"
    checks = [{"slide": "S0001", "criterion": key, "passed": key != "coverage", "evidence": "原答"}
              for key in ev.SEMANTIC_CRITERIA if key != "timing"]
    timing = {"points": [{"slide": "S0001", "point": 0, "sentence": "S0001",
                           "status": "unresolved", "evidence": "找不到要點的講述位置"}]}
    final = [*checks, {"slide": "S0001", "criterion": "timing", "passed": False,
                       "evidence": "找不到要點的講述位置"}]
    raw = {"stdout": json.dumps({"structured_output": {"checks": checks}}), "stderr": ""}
    result = ev.with_run({"schema_version": ev.SEMANTIC_SCHEMA_VERSION,
                          "slides_sha256": ev.semantic_slide_sha256(candidate),
                          "selection_sha256": chapter_exposition.file_sha256(selection),
                          "point_timing": timing, "checks": final}, raw=raw)
    receipt.write_text(json.dumps(result))
    assert ev.semantic_eval_problems(candidate, selection, receipt) == [
        "S0001 coverage: 原答", "S0001 timing: 找不到要點的講述位置"]
    result["checks"] = [{**row, "passed": True} if row["criterion"] == changed_criterion else row
                        for row in result["checks"]]
    receipt.write_text(json.dumps(result))
    assert ev.semantic_eval_problems(candidate, selection, receipt) == [
        "Semantic Eval verdicts differ from the recorded review and point timing evidence"]


def test_subunit_semantic_eval_checks_a_complete_summary_without_asking_for_splits(tmp_path, monkeypatch):
    from backend.pipeline import claim_flash_common

    gold = next(c for c in json.loads(GOLD.read_text()) if c["id"] == "title-covers-temptation-responsibility")
    source = tmp_path / "selection.json"
    source.write_text(json.dumps({
        "pieces": [{"id": "P1", "start": 0, "end": 10,
                    "text": "".join(line["text"] for line in gold["source"])}],
        "decisions": [{"id": "P1", "keep": True}],
    }))
    slide_path = tmp_path / "slides.json"
    slide_path.write_text(json.dumps({
        "lecture": "Lecture", "model": "writer", "decisions_sha256": chapter_exposition.file_sha256(source),
        "claim_context": {"scope": "subunit"},
        "slides": [{"first": "S0001", "last": "S0003", "title": gold["known_good"]["title"], "points": [
            {"sentence": "S0001", "text": "神允許的試探由神開出路，自己招來的試探要自己負責"}]}],
    }))

    class Reviewer:
        model = "reviewer"

        def generate_json(self, system, packet, schema):
            assert schema["required"] == ["checks"]
            request = json.loads(packet)
            criteria = request["criteria"]
            assert set(criteria) == {"faithfulness", "coverage", "scripture"}
            assert set(schema["properties"]["checks"]["items"]["properties"]["criterion"]["enum"]) == set(criteria)
            assert "標題涵蓋本張共同主題" in criteria["coverage"]
            assert request["slides"][0]["title"] == "警告中的責任分別"
            assert request["source"][0]["text"] == "那警告是什麼意思？"
            return {"checks": [{"slide": "S0001", "criterion": criterion, "passed": True, "evidence": "原審閱"}
                               for criterion in criteria]}

    def jev(root, port, state, questions):
        assert set(questions) == {"0:whole:S0001", "0:whole:S0002", "0:whole:S0003"}
        return {key: "full" for key in questions}

    monkeypatch.setattr(chapter_exposition, "chapter_client", lambda model: Reviewer())
    monkeypatch.setattr(claim_flash_common, "jev", jev)
    path = tmp_path / "eval.json"
    receipt = ev.run_semantic_eval(slide_path, source, path, "reviewer")
    assert ev.semantic_eval_problems(slide_path, source, path) == []
    assert receipt["point_timing"]["points"][0]["status"] == "correct"
    assert all(row["criterion"] != "timing" for row in receipt["run"]["raw_response"]["checks"])


def test_semantic_eval_can_use_billed_opus_without_subscription(tmp_path, monkeypatch):
    from backend.pipeline import claim_anthropic_api

    source = tmp_path / "selection.json"
    source.write_text(json.dumps({"pieces": [], "decisions": []}))
    slide_path = tmp_path / "slides.json"
    slide_path.write_text(json.dumps({
        "lecture": "Lecture", "model": "writer", "decisions_sha256": chapter_exposition.file_sha256(source),
        "slides": [{"first": "S0001"}],
    }))
    spend = tmp_path / "spend"

    class FakeAPI:
        backend = "anthropic_api"

        def __init__(self, *, model, max_output_tokens, spend_root):
            assert spend_root == spend
            self.model = model

        def generate_json(self, system, prompt, schema):
            assert self.call_artifacts.name == "transport"
            assert not (self.call_artifacts / "request.json").exists()
            answer = {"checks": [{"slide": "S0001", "criterion": kind, "passed": True, "evidence": "test"}
                                 for kind in ev.SEMANTIC_CRITERIA]}
            self.last_raw_response = {"stop_reason": "end_turn",
                                      "content": [{"type": "text", "text": json.dumps(answer)}],
                                      "usage": {"input_tokens": 13, "output_tokens": 8}}
            return answer

    def forbidden(*args, **kwargs):
        pytest.fail("An Opus API review must not open the subscription transport.")

    monkeypatch.setattr(claim_anthropic_api, "ClaimAnthropicAPI", FakeAPI)
    monkeypatch.setattr(chapter_exposition, "chapter_client", forbidden)
    receipt_path = tmp_path / "eval.json"
    receipt = ev.run_semantic_eval(slide_path, source, receipt_path, "claude-opus-5-5",
                                   opus_api_spend_root=spend)
    assert receipt["run"]["backend"] == "anthropic_api"
    assert receipt["run"]["usage"] == {"input_tokens": 13, "output_tokens": 8}
    assert ev.semantic_eval_problems(slide_path, source, receipt_path) == []


def test_subunit_eval_applies_only_confirmed_moves_without_erasing_review(tmp_path, monkeypatch):
    from backend.pipeline import claim_flash_common, chapter_slides

    source = tmp_path / "selection.json"
    selection = {"pieces": [{"id": "P1", "start": 0, "end": 60,
        "text": "七章十二節的結論是什麼？下面又是另一個結論。跟門徒的使命有關係。"
                "這是門徒與舊約關係的結論。是整個門徒使命的結論。"}],
        "decisions": [{"id": "P1", "keep": True}]}
    source.write_text(json.dumps(selection))
    first = {"first": "S0001", "last": "S0005", "title": "結論的範圍", "verses": [], "scriptures": [],
             "points": [{"sentence": "S0001", "text": "太 7:12 總結門徒與舊約的關係"},
                        {"sentence": "S0002", "text": "還有另一個結論"},
                        {"sentence": "S0003", "text": "太 7:13–27 是門徒使命的結論"}]}
    deck = {"lecture": "Lecture", "model": "writer", "decisions_sha256": chapter_exposition.file_sha256(source),
            "claim_context": {"scope": "subunit"}, "slides": [first], "raw_slides": [first]}
    slide_path = tmp_path / "slides.json"
    slide_path.write_text(json.dumps(deck))

    class Reviewer:
        model = "reviewer"

        def generate_json(self, system, packet, schema):
            return {"checks": [
                {"slide": "S0001", "criterion": criterion, "passed": criterion != "coverage", "evidence": "原審閱"}
                for criterion in json.loads(packet)["criteria"]]}

    def jev(root, port, state, questions):
        full = {"0:whole:S0004", "1:whole:S0002", "2:whole:S0005"}
        return {key: "full" if key in full else "partial" for key in questions}

    monkeypatch.setattr(chapter_exposition, "chapter_client", lambda model: Reviewer())
    monkeypatch.setattr(claim_flash_common, "jev", jev)
    path = tmp_path / "eval.json"
    receipt = ev.run_semantic_eval(slide_path, source, path, "reviewer")
    written = json.loads(slide_path.read_text())
    assert written["slides"] == written["raw_slides"] == [first]
    assert [row["status"] for row in receipt["point_timing"]["points"]] == ["early", "correct", "early"]
    _, placed = chapter_slides.placed(selection, written)
    assert [(point["text"], point["sentence"]) for point in placed[0]["points"]] == [
        (first["points"][i]["text"], sid) for i, sid in [(1, "S0002"), (0, "S0004"), (2, "S0005")]]
    assert ev.semantic_eval_problems(slide_path, source, path) == ["S0001 coverage: 原審閱"]
    receipt["point_timing"]["points"][0]["expected_sentence"] = "S0002"
    path.write_text(json.dumps(receipt))
    assert ev.semantic_eval_problems(slide_path, source, path) == [
        "Point timing adjustments do not match the recorded results"]


def test_point_timing_refuses_collisions_and_moves_across_slides():
    from backend.pipeline.chapter_slide_point_timing import timing_adjustments

    source = [{"id": f"S{i:04d}"} for i in range(1, 5)]
    slides = [{"first": "S0001", "last": "S0003", "points": [{"sentence": "S0001"}, {"sentence": "S0002"}]}]
    unchanged = {"slide": "S0001", "point": 1, "sentence": "S0002", "status": "correct"}
    early = {"slide": "S0001", "point": 0, "sentence": "S0001", "status": "early"}
    for target in ("S0002", "S0004", "S0099"):
        assert timing_adjustments(slides, source, [{**early, "expected_sentence": target}, unchanged]) == []


def test_automatic_timing_respects_the_owners_saved_word_sequence(monkeypatch):
    from backend.pipeline import chapter_slide_cases

    source = [{"id": "S0001", "text": "門徒要發光。"}, {"id": "S0002", "text": "再談一次發光。"}]
    record = {"lecture": "Lecture", "slides": [{"first": "S0001", "last": "S0002", "title": "發光",
        "points": [{"sentence": "S0001", "text": "門徒要發光"}]}]}
    monkeypatch.setattr(chapter_slide_cases, "cases_for", lambda _: [{
        "kind": "point", "source_text": source[0]["text"], "pattern": "發光", "why": "原聲開始時顯示"}])
    finding = {"slide": "S0001", "point": 0, "sentence": "S0001", "status": "early", "expected_sentence": "S0002"}
    assert ev._checked_point_adjustments(record, source, [finding]) == []


@pytest.mark.parametrize(("sentence", "shown", "required", "passes"), [
    ("S0065", "1pe-2-13-14", "1pe-2-13", True),
    ("S0064", "1pe-2-13-14", "1pe-2-13", False),
    ("S0065", "1pe-2-13", "1pe-2-13-14", False),
])
def test_verified_reading_requires_full_coverage_at_its_original_sentence(tmp_path, sentence, shown, required, passes):
    selection = tmp_path / "source.json"
    selection.write_text("{}")
    candidate = tmp_path / "deck.json"
    candidate.write_text(json.dumps({
        "claim_context": {"verified_scriptures": [{"sentence": "S0065", "slug": required}]},
        "slides": [{"first": "S0064", "scriptures": [{"sentence": sentence, "slug": shown}]}],
    }))
    receipt = tmp_path / "eval.json"
    receipt.write_text(json.dumps(ev.with_run({
        "schema_version": ev.SEMANTIC_SCHEMA_VERSION,
        "slides_sha256": ev.semantic_slide_sha256(candidate),
        "selection_sha256": chapter_exposition.file_sha256(selection),
        "checks": [{"slide": "S0064", "criterion": key, "passed": True, "evidence": "原答"}
                   for key in ev.SEMANTIC_CRITERIA],
    })))
    assert ev.semantic_eval_problems(candidate, selection, receipt) == (
        [] if passes else ["Verified scripture is missing from its recorded source sentence"])


def test_gold_overlapping_scripture_replays_the_original_merge():
    case = next(c for c in json.loads(GOLD.read_text())
                if c["id"] == "first-peter-overlapping-reading-at-same-sentence")
    source, = case["source"]
    at = source["id"]
    assert at == case["support_sentence"] == "S0065"
    kept = [{"id": "U1", "start": source["start"], "end": source["end"], "text": source["text"]}]
    # The selected sentence is numbered S0001 when replayed on its own.
    def on_fixture(items):
        return [{**item, "sentence": "S0001"} for item in items]

    raw = {"first": "S0001", "title": case["known_bad"]["title"], "verses": "",
           "points": on_fixture([{"text": "為主順服人的制度"}, {"text": "包括君王和他所派的官員"}]),
           "scriptures": on_fixture(case["model_scriptures"])}
    problems, _, slides = chapter_slides.check(
        [raw], kept, CHAPTERS[case["scope"]["passage"]], allow_no_chapter_verses=True,
        verified_scriptures=on_fixture(case["verified_scriptures"]))
    assert not problems
    assert slides[0]["scriptures"] == on_fixture(case["known_good"]["scriptures"])
    assert slides[0]["scriptures"] != on_fixture(case["known_bad"]["scriptures"])
