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
    assert "known_bad" not in prompt and "expected" not in prompt


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
                   "passed": not (criterion == "faithfulness" and slide["first"] == variant["expected"].get("unfaithful_slide")),
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
            items.append({"case_id": case_id, "packet": ev.semantic_packet(record, selection)})
            expected[case_id] = variant["expected"]
    row = {"type": "object", "additionalProperties": False,
           "required": ["case_id", "argument_passed", "unfaithful_slides", "evidence"],
           "properties": {"case_id": {"type": "string", "enum": list(expected)},
                          "argument_passed": {"type": "boolean"},
                          "unfaithful_slides": {"type": "array", "items": {"type": "string"}},
                          "evidence": {"type": "string"}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["results"],
              "properties": {"results": {"type": "array", "items": row}}}
    system = (ev.SEMANTIC_SYSTEM + "\n" + ev.ARGUMENT_REVIEW_SYSTEM
              + "\n這次是批量試題，每筆獨立判斷。回一筆 results 給每個 case_id。"
                "argument_passed 是唯一 argument_requirements 的判定；"
                "unfaithful_slides 列有實質意思不受本張原文支持的張號，含把別張才說的內容搬來或與本張原話相反；沒有則空。"
                "evidence 引原文句號與具體文字，其他逐張判定省略。")
    client = RecordedClient(chapter_exposition.chapter_client(REVIEW_MODEL),
                            Path(os.environ["WANG_SLIDE_REGRESSION_ARTIFACTS"]), once=True)
    results = client.generate_json(system, json.dumps(items, ensure_ascii=False), schema)["results"]
    assert len(results) == len(expected) and {row["case_id"] for row in results} == set(expected)
    mismatches = []
    for verdict in results:
        want = expected[verdict["case_id"]]
        unfaithful = [want["unfaithful_slide"]] if "unfaithful_slide" in want else []
        if (verdict["argument_passed"] != want["passed"] or verdict["unfaithful_slides"] != unfaithful
                or not verdict["evidence"].strip()):
            mismatches.append({**verdict, "fixture": labels[verdict["case_id"]]})
    assert not mismatches, mismatches
