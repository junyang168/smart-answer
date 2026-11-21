"""Regression cases from 2011 Matthew lecture 4, subunits 6.1 and 6.2."""
import copy
import json
import os
from pathlib import Path

import pytest

from backend.pipeline import chapter_slide_arguments as arg
from backend.pipeline import chapter_slide_eval as ev
from backend.pipeline import chapter_slides


FIXTURE = Path(__file__).parent / "fixtures/chapter_slides/subunit_argument_regressions.json"


def cases():
    return json.loads(FIXTURE.read_text())


def packet_for(case, variant):
    pieces = [
        {"id": f"P{i}", "start": i * 10, "end": i * 10 + 8, "text": text}
        for i, text in enumerate(case["source"], 1)
    ]
    selection = {"pieces": pieces, "decisions": [{"id": p["id"], "keep": True} for p in pieces]}
    source = chapter_slides.sentences(pieces)
    assert [line["text"] for line in source] == case["source"]
    route, = arg.validate([case["requirement"]], source)
    context = {"scope": "subunit", "lecture": "L1", "claims": [], arg.PLAN_KEY: [route]}
    record = {"lecture": "L1", "model": "writer", "claim_context": context, "slides": variant["slides"]}
    return record, selection


def test_planner_receives_source_and_claims_without_golden_answers():
    case = cases()[0]
    _, selection = packet_for(case, case["variants"][0])
    source = chapter_slides.sentences(selection["pieces"])

    class Planner:
        def generate_json(self, system, prompt, schema):
            assert json.loads(prompt) == {"source": [{"id": s["id"], "text": s["text"]} for s in source],
                                          "claims": [{"claim_id": "C1", "statement": "可選較小的惡"}]}
            assert schema == arg.PLAN_SCHEMA
            assert "例子的非必要細節不用列" in system
            return {arg.PLAN_KEY: [case["requirement"]]}

    routes = arg.plan({"claims": [{"claim_id": "C1", "statement": "可選較小的惡", "diagrams": []}]},
                      source, Planner())
    assert routes[0]["id"] == "argument-1"


@pytest.mark.parametrize("field,value", [("conclusion_sentence", "S0999"), ("source_sentences", ["S0001", "S0999"]),
                                         ("inference", " "), ("id", "argument-2")])
def test_plan_cannot_require_unbound_or_incomplete_inference(field, value):
    case = cases()[0]
    _, selection = packet_for(case, case["variants"][0])
    route = {**case["requirement"], field: value}
    with pytest.raises(ValueError):
        arg.validate([route], chapter_slides.sentences(selection["pieces"]))


def test_empty_speaker_label_cannot_anchor_a_required_conclusion():
    route = {"reason": "理由", "inference": "關係", "conclusion": "結論",
             "conclusion_sentence": "S0002", "source_sentences": ["S0001", "S0002"]}
    source = [{"id": "S0001", "text": "古代背景。"}, {"id": "S0002", "text": "答： "}]
    with pytest.raises(ValueError, match="outside its source"):
        arg.validate([route], source)


def test_generation_and_eval_share_the_source_boundary_and_the_frozen_inference():
    case = cases()[0]
    record, selection = packet_for(case, case["variants"][1])
    context = {**record["claim_context"], "schema_version": "chapter-slide-outline-context.v1"}
    prompt = chapter_slides.user_prompt("L1", selection, context)
    packet = ev.semantic_packet(record, selection)
    assert arg.BOUNDARY in prompt
    assert arg.BOUNDARY in packet["criteria"]["faithfulness"]
    requirement, = packet["argument_requirements"]
    assert requirement["source_slides"]["S0001"] == "S0001"
    assert requirement["conclusion_slide"] == "S0011"
    assert requirement["inference"] in prompt
    assert arg.CHAIN_RULE in prompt
    assert "known_bad" not in prompt and "expected" not in prompt


def test_subunit_writer_uses_the_current_answer_when_the_earlier_reason_differs():
    from backend.pipeline.chapter_material import CHAPTERS

    case = next(row for row in cases() if row["id"] == "remarriage-earlier-idea-in-later-slide")
    record, selection = packet_for(case, case["variants"][0])
    context = {**record["claim_context"], "schema_version": "chapter-slide-outline-context.v1"}
    chapter = CHAPTERS["mat-5-1-48"]
    system = chapter_slides.system_prompt(chapter, subunit=True)
    prompt = chapter_slides.user_prompt(record["lecture"], selection, context)
    assert chapter_slides.SUBUNIT_LOCAL_ANSWER_RULE in system
    assert "不可拿前一段的理由替換這次的理由" in system
    assert "不要替「可以」「明說」等補上一個未能確定的對象" in system
    assert "即使照抄原話，也不要把它列成該問題的答案" in system
    packet = ev.semantic_packet(record, selection)
    assert "不要求作者改成相反的定論" in packet["criteria"]["faithfulness"]
    assert chapter_slides.SUBUNIT_LOCAL_ANSWER_RULE not in chapter_slides.system_prompt(chapter)
    assert "保羅就沒有提說" in prompt and "保羅明講，是可以" in prompt
    assert "不能據未明講就禁止" not in prompt
    assert "known_good" not in prompt and "known_bad" not in prompt


def test_subunit_prompt_preserves_the_local_object_of_a_negation():
    from backend.pipeline.chapter_material import CHAPTERS

    case = next(row for row in cases() if row["id"] == "same-claim-two-different-local-distinctions")
    record, selection = packet_for(case, case["variants"][0])
    context = {**record["claim_context"], "schema_version": "chapter-slide-outline-context.v1"}
    chapter = CHAPTERS["mat-5-1-48"]
    system = chapter_slides.system_prompt(chapter, subunit=True)
    assert chapter_slides.SUBUNIT_LOCAL_DISTINCTION_RULE in system
    assert "不能用另一張的對比替換本張" in system
    assert chapter_slides.SUBUNIT_LOCAL_DISTINCTION_RULE not in chapter_slides.system_prompt(chapter)
    prompt = chapter_slides.user_prompt(record["lecture"], selection, context)
    assert "凡看見婦女就動起淫念" in prompt and "就覺得她很漂亮" in prompt
    assert "因此不是看見便犯罪" not in prompt


def test_subunit_writer_planner_and_eval_share_explicit_premise_rule():
    from backend.pipeline.chapter_material import CHAPTERS

    case = next(row for row in cases() if row["id"] == "ten-commandments-omitted-premise")
    record, selection = packet_for(case, case["variants"][0])
    chapter = CHAPTERS["mat-5-1-48"]
    system = chapter_slides.system_prompt(chapter, subunit=True)
    coverage = ev.semantic_packet(record, selection)["criteria"]["coverage"]

    for prompt in (arg.PLAN_SYSTEM, system, coverage):
        assert "不可用正面描述代替原文明說的否定" in prompt
    assert "合寫成一條要點" in system
    assert "省略重述和不影響推論的細節" in system
    assert "已不在摩西律法之下，乃在基督律法之下" not in system


def test_illustration_coverage_considers_the_preceding_slide():
    case = next(row for row in cases() if row["id"] == "teaching-analogy-after-complete-argument")
    record, selection = packet_for(case, case["variants"][0])
    packet = ev.semantic_packet(record, selection)
    assert len(packet["slides"]) == 2
    assert ev.ILLUSTRATION_COVERAGE in packet["criteria"]["coverage"]
    assert ev.ILLUSTRATION_COVERAGE in ev.ARGUMENT_REVIEW_SYSTEM


def test_writer_must_map_every_inference_to_real_points_within_its_original_slides(tmp_path, monkeypatch):
    from backend.pipeline import chapter_exposition
    from backend.tests.test_chapter_slides import CHAPTER

    case = cases()[0]
    record, selection = packet_for(case, case["variants"][1])
    context = {**record["claim_context"], "schema_version": "chapter-slide-outline-context.v1"}
    schema = chapter_slides.response_schema(context)
    assert arg.BINDINGS_KEY in schema["required"]
    selected = chapter_exposition.record_path(tmp_path, CHAPTER.passage, record["lecture"])
    selected.parent.mkdir(parents=True)
    selected.write_text(json.dumps(selection))
    monkeypatch.setattr(chapter_slides, "checked", lambda *a: ([], [], record["slides"]))
    edited = {"slides": record["slides"]}
    prompt = chapter_slides.user_prompt(record["lecture"], selection, context)

    def save(bindings):
        return chapter_slides.save(CHAPTER, record["lecture"], tmp_path,
                                   {**edited, arg.BINDINGS_KEY: bindings}, prompt,
                                   selection["pieces"], "writer", context)

    for anchors in ([], ["S0005"], ["S0006", "S0011"], ["S0011", "S0999"]):
        assert save([{"id": "argument-1", "point_sentences": anchors}])["status"] == "rejected"
    accepted = save([{"id": "argument-1", "point_sentences": ["S0005", "S0011"]}])
    assert accepted["status"] == "saved"
    packet = ev.semantic_packet(json.loads(Path(accepted["file"]).read_text()), selection)
    assert packet["argument_requirements"][0]["writer_points"] == ["S0005", "S0011"]
    for legacy in (chapter_slides.review, chapter_slides.revise):
        outcome = legacy(CHAPTER, record["lecture"], data_base_dir=tmp_path, client=None)
        assert outcome["status"] == "subunit_uses_semantic_eval"


def test_argument_binding_rejects_a_reason_separated_from_the_conclusion_by_another_slide():
    # Actual 6.2 shape: S0103 is on S0102, then S0114 intervenes before the recap at S0136.
    slides = [
        {"first": "S0102", "points": [{"sentence": "S0103"}]},
        {"first": "S0114", "points": [{"sentence": "S0114"}]},
        {"first": "S0136", "points": [{"sentence": "S0136"}, {"sentence": "S0142"}]},
    ]
    route = {"id": "argument-11", "conclusion_slide": "S0136",
             "source_slides": {"S0104": "S0102", "S0136": "S0136", "S0142": "S0136"}}
    binding = {"id": "argument-11", "point_sentences": ["S0103", "S0136"]}
    with pytest.raises(ValueError, match="two adjacent slides"):
        arg.bind_points([route], slides, [binding])
    assert arg.bind_points([route], [slides[0], slides[2]], [binding])[0]["writer_points"] == ["S0103", "S0136"]
    local_binding = {**binding, "point_sentences": ["S0136", "S0142"]}
    assert arg.bind_points([route], slides, [local_binding])[0]["writer_points"] == ["S0136", "S0142"]


def test_writer_can_recover_a_nearby_repeat_of_the_same_anchored_evidence():
    slides = [
        {"first": "S0109", "last": "S0119", "points": [{"sentence": "S0113", "source": "E1"}]},
        {"first": "S0120", "last": "S0138", "points": [{"sentence": "S0122", "source": "E1"}]},
        {"first": "S0139", "last": "S0141", "points": [{"sentence": "S0141", "source": "C1"}]},
    ]
    source = [{"id": line, "text": "逐字稿。"} for line in ("S0113", "S0122", "S0123", "S0141")]
    step = {"evidence_step_id": "E1", "anchors": [
        {"fragment_id": "old", "sentences": ["S0113"]},
        {"fragment_id": "repeat", "sentences": ["S0122", "S0123"]},
    ]}
    context = {"claims": [{"evidence_steps": [step]}], arg.PLAN_KEY: [{
        "reason": "相同用詞比較", "inference": "推論", "conclusion": "結論",
        "conclusion_sentence": "S0141", "source_sentences": ["S0113", "S0141"],
    }]}
    original = {"id": "argument-1", "point_sentences": ["S0113", "S0122", "S0141"]}
    requirements = arg.review_requirements(context, source, slides)
    assert requirements[0]["repeated_evidence_points"] == ["S0122"]
    assert requirements[0]["source_slides"]["S0122"] == "S0120"
    with pytest.raises(ValueError, match="two adjacent slides"):
        arg.bind_points(requirements, slides, [original])
    resolved, = arg.bind_points(requirements, slides, [original], recover_repeats=True)
    assert resolved["writer_points"] == ["S0122", "S0141"]
    assert original["point_sentences"] == ["S0113", "S0122", "S0141"]

    wrong = copy.deepcopy(slides)
    wrong[1]["points"][0]["source"] = "C1"  # Same topic alone is insufficient.
    requirements = arg.review_requirements(context, source, wrong)
    assert "S0122" not in requirements[0]["source_slides"]
    with pytest.raises(ValueError, match="must cite existing points"):
        arg.bind_points(requirements, wrong, [original], recover_repeats=True)


@pytest.mark.parametrize("id", [case["id"] for case in cases()])
def test_saved_counterexamples_and_positive_controls_follow_the_production_eval_contract(tmp_path, id):
    case = next(row for row in cases() if row["id"] == id)
    for variant in case["variants"]:
        record, selection = packet_for(case, variant)
        selection_path, slide_path, receipt_path = (tmp_path / name for name in ("source", "deck", "eval"))
        selection_path.write_text(json.dumps(selection))
        slide_path.write_text(json.dumps(record))
        packet = ev.semantic_packet(record, selection)
        req, = packet["argument_requirements"]
        assert req["conclusion_slide"] == variant["expected"]["conclusion_slide"]
        # Simulate provider responses to test receipt replay and the mandatory ruling.
        checks = [{"slide": slide["first"], "criterion": criterion,
                   "passed": not ((criterion == "faithfulness" and slide["first"] == variant["expected"].get("unfaithful_slide"))
                                  or (criterion == "coverage" and slide["first"] == variant.get("coverage_slide")
                                      and not variant["expected"].get("coverage", True))),
                   "evidence": "Fixture evidence"}
                  for slide in record["slides"] for criterion in packet["criteria"]]
        checks.append({"slide": req["conclusion_slide"], "criterion": req["id"],
                       "passed": variant["expected"]["passed"], "evidence": "Fixture inference verdict"})
        timing = {"points": [{"slide": s["first"], "point": i, "sentence": p["sentence"],
                              "status": "correct", "evidence": "Fixture timing"}
                             for s in record["slides"] for i, p in enumerate(s["points"])]}
        assembled = ev._assemble_checks(record["slides"], checks, timing)
        receipt = ev.with_run({"schema_version": ev.SEMANTIC_SCHEMA_VERSION,
                               "slides_sha256": ev.semantic_slide_sha256(slide_path),
                               "selection_sha256": ev.chapter_slides.chapter_exposition.file_sha256(selection_path),
                               "point_timing": timing, "checks": assembled}, raw={"checks": checks})
        receipt_path.write_text(json.dumps(receipt))
        problems = ev.semantic_eval_problems(slide_path, selection_path, receipt_path)
        should_pass = variant["expected"]["passed"] and "unfaithful_slide" not in variant["expected"]
        assert (not problems) == should_pass, variant["id"]
        missing = copy.deepcopy(receipt)
        missing["checks"] = [row for row in assembled if row["criterion"] != req["id"]]
        missing = ev.with_run(missing, raw={"checks": checks[:-1]})
        receipt_path.write_text(json.dumps(missing))
        assert any("each planned inference" in error
                   for error in ev.semantic_eval_problems(slide_path, selection_path, receipt_path))
        if len(record["slides"]) > 1:
            other_slide = next(s["first"] for s in record["slides"] if s["first"] != req["conclusion_slide"])
            misplaced = [*checks[:-1], {**checks[-1], "slide": other_slide}]
            bad = ev.with_run({**receipt, "checks": ev._assemble_checks(record["slides"], misplaced, timing)},
                              raw={"checks": misplaced})
            receipt_path.write_text(json.dumps(bad))
            assert ev.semantic_eval_problems(slide_path, selection_path, receipt_path) == [
                "Semantic Eval has a duplicate, unknown or incomplete check"]



@pytest.mark.skipif(not os.getenv("WANG_SLIDE_REGRESSION_ARTIFACTS"), reason="Requires recorded Opus subscription")
def test_actual_model_distinguishes_the_saved_argument_regressions():
    """Run the production content policy on both good and bad examples in one call."""
    from dotenv import load_dotenv

    from backend.pipeline import chapter_exposition
    from backend.pipeline.chapter_slide_claims import RecordedClient
    from backend.pipeline.chapter_subunit_slides import REVIEW_MODEL

    load_dotenv()
    items = []
    expected = {}
    labels = {}
    for case in cases():
        for variant in case["variants"]:
            case_id = f"case-{len(items) + 1}"
            labels[case_id] = "/".join((case["id"], variant["id"]))
            record, selection = packet_for(case, variant)
            actual_slides = record["slides"]
            initial_packet = ev.semantic_packet(record, selection)
            target = initial_packet["argument_requirements"][0]["conclusion_slide"]
            conclusion_slide = next(s for s in actual_slides if s["first"] == target)
            if conclusion_slide["points"]:
                allowed = set(initial_packet["argument_requirements"][0]["source_slides"].values())
                record[arg.BINDINGS_KEY] = [{"id": "argument-1", "point_sentences": [
                    p["sentence"] for s in actual_slides if s["first"] in allowed for p in s["points"]]}]
            items.append({"case_id": case_id, "packet": ev.semantic_packet(record, selection),
                          **({"coverage_slide": variant["coverage_slide"]} if "coverage_slide" in variant else {})})
            expected[case_id] = variant["expected"]
    checks = {f"{item['case_id']}-{kind}": (item["case_id"], kind) for item in items
              for kind in (("argument", "faithfulness", "coverage") if "coverage_slide" in item
                           else ("argument", "faithfulness"))}
    row = {"type": "object", "additionalProperties": False,
           "required": ["check_id", "passed", "evidence"],
           "properties": {"check_id": {"type": "string", "enum": list(checks)},
                          "passed": {"type": "boolean"}, "evidence": {"type": "string"}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["results"],
              "properties": {"results": {"type": "array", "items": row}}}
    system = (ev.SEMANTIC_SYSTEM + "\n" + ev.ARGUMENT_REVIEW_SYSTEM
              + "\n這次是批量試題。對每案回獨立判定，check_id 為 case_id 加 -argument 和 -faithfulness。"
                "有 coverage_slide 時另外回 -coverage，按 coverage 只判這張是否漏掉必要內容，並核對相鄰張。"
                "-argument 只判定唯一 argument_requirements 指明的推論，以 conclusion_slide 與來源為範圍。"
                "-faithfulness 只判定所有呈現內容有沒有本張原文依據，有不忠實之處才不通過。"
                "一筆的判定不能影響同個案例的另一筆；evidence 指出具體原話和投影片內容。")
    client = RecordedClient(chapter_exposition.chapter_client(REVIEW_MODEL),
                            Path(os.environ["WANG_SLIDE_REGRESSION_ARTIFACTS"]), once=True)
    results = client.generate_json(system, json.dumps(items, ensure_ascii=False), schema)["results"]
    assert len(results) == len(checks) and {row["check_id"] for row in results} == set(checks)
    mismatches = []
    for verdict in results:
        case_id, kind = checks[verdict["check_id"]]
        want = expected[case_id]
        passes = (want["passed"] if kind == "argument" else want["coverage"] if kind == "coverage"
                  else "unfaithful_slide" not in want)
        # Some true-but-weaker statements are also called unfaithful in a full review.
        # Their missing distinction must still independently fail the argument check.
        accepted = {passes}
        if kind == "faithfulness" and not want["passed"] and want.get("faithfulness_may_pass"):
            accepted.add(True)
        if verdict["passed"] not in accepted or not verdict["evidence"].strip():
            mismatches.append({**verdict, "fixture": labels[case_id], "expected": sorted(accepted)})
    assert not mismatches, mismatches


REPEAT_CASES = json.loads(
    (FIXTURE.parent / "subunit_argument_repeat_regressions.json").read_text()
)


@pytest.mark.parametrize("case", REPEAT_CASES, ids=lambda case: case["id"])
def test_jev_recovers_only_the_confirmed_adjacent_repeat(case):
    from backend.pipeline.evidence_refresh import MODEL, digest

    source, slides = case["source"], case["slides"]
    route = case["requirement"]
    route_id = route["id"]
    requirements = [route]
    original = [case["original_binding"]]
    control = case["same_topic_control"]
    with_control = [{**original[0], "point_sentences": [*original[0]["point_sentences"], control]}]
    with pytest.raises(ValueError, match="source slides"):
        arg.bind_points(requirements, slides, original, recover_repeats=True)

    def ask(state, questions):
        assert "不能把別句說過的意思算在本句" in next(iter(questions.values()))["instructions"]
        saved = case["recorded_jev"][state["sentence"]]
        assert digest({"model": MODEL, "state": state, "questions": questions}) == saved["sha256"]
        return dict.fromkeys(questions, saved["answer"])

    confirmed = arg.find_adjacent_repeats(requirements, slides, source, with_control, ask)
    assert confirmed == {route_id: [case["expected_repeat"]]}
    repaired = arg.apply_adjacent_repeats(requirements, slides, confirmed)
    [bound] = arg.bind_points(repaired, slides, with_control, recover_repeats=True)
    assert bound["writer_points"] == case["expected_points"]
    with pytest.raises(ValueError, match="unconfirmed point"):
        arg.bind_points(repaired, slides, [{"id": route_id, "point_sentences": [control, route["conclusion_sentence"]]}])
    with pytest.raises(ValueError, match="preceding slide"):
        arg.apply_adjacent_repeats(requirements, slides, {route_id: [route["conclusion_sentence"]]})
