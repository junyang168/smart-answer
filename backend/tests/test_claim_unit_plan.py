import hashlib
import json

import pytest

from backend.pipeline import claim_creation_flash as creation
from backend.pipeline import claim_unit_plan as unit
from backend.tests.test_claim_creation_flash import StepsPorts, groups
from backend.tests.test_detailed_knowledge_extraction import _transcript


def write_source(path, doc, source_id, first, last):
    path.write_text(json.dumps(doc, ensure_ascii=False))
    return dict(source_id=source_id, file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                ranges=[dict(first=first, last=last)])


def example(tmp_path):
    earlier = {"script": [{"text": "為什麼？明天再回答。", "index": 1}]}
    sources = [write_source(tmp_path / "previous.json", earlier, "previous", "S0001#001", "S0001#001"),
               write_source(tmp_path / "current.json", _transcript(), "T", "S0001", "S0003")]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"sources": sources}))
    return path


class Planner:
    identity = StepsPorts.identity

    def __init__(self):
        self.calls = []

    def repair(self, request):
        self.calls.append(request)
        first = request["input"]["sentences"][1][0]
        return {"runs": [{"first": first, "group": "共题", "question": "為什麼？"}],
                "questions": [{"at": first, "question": "為什麼？"}]}


def test_context_reaches_claims_and_steps_and_preserves_local_fragments(tmp_path):
    manifest = example(tmp_path)
    folder = tmp_path / "plan"
    planner = Planner()
    result = unit.run(manifest, folder, planner)
    assert unit.run(manifest, folder, planner) == result
    assert len(planner.calls) == 1
    assert "明天再回答" not in json.dumps(planner.calls, ensure_ascii=False)
    seen = []

    class Ports(StepsPorts):
        def repair(self, request):
            content = request["input"]
            if content["task"] in ("creation_claims", "creation_argument_steps"):
                context = content["unit_context"]
                assert context["questions"][0]["sentence_id"] == "S0001#001"
                assert context["context_passages"][0]["text"] == "為什麼？"
                assert context["context_passages"][0]["source_id"] == "previous"
                seen.append(content["task"])
            return super().repair(request)

    source = tmp_path / "current.json"
    run = tmp_path / "run"
    ports = Ports(_transcript())
    creation.run("T", source, run, ports, groups=groups(), steps_first=True,
                 grouped_package=True, unit_context_dir=folder)
    assert seen == ["creation_claims", "creation_argument_steps"]
    package = json.loads((run / "candidate.json").read_text())
    assert package["extraction"]["section_plan"]["unit_plan_sha256"] == unit.artifacts.digest(result)
    assert all(f["verbatim_excerpt"] != "為什麼？" for f in package["source_fragments"])
    (tmp_path / "previous.json").write_text("{}")
    with pytest.raises(ValueError, match="unit source changed"):
        creation.run("T", source, run, ports, groups=groups(), steps_first=True,
                     grouped_package=True, unit_context_dir=folder)


def test_ranges_cannot_silently_duplicate_or_drop_unknown_sentences(tmp_path):
    manifest = example(tmp_path)
    data = json.loads(manifest.read_bytes())
    span = data["sources"][0]["ranges"][0]
    data["sources"][0]["ranges"].append(dict(span))
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="ordered and disjoint"):
        unit.read_unit(manifest)
    span["first"] = "S0001#999"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unknown source range"):
        unit.read_unit(manifest)


def test_budget_batches_share_open_questions_and_preserve_all_sentences(tmp_path, monkeypatch):
    reading = unit.read_unit(example(tmp_path))
    planner = Planner()
    # Make a realistic size boundary without splitting an individual sentence.
    reading.sentences = [dict(row, text="原文。" * 350) for row in reading.sentences]
    reading.sentences[0]["text"] = "為什麼？"
    monkeypatch.setattr(creation.f, "GPT_BYTES", 11500)
    run_dir = tmp_path / "run"
    plan = unit.plan_unit(reading, run_dir, planner)
    assert len(planner.calls) > 1 and plan["runs"][0]["first"] == 1
    assert planner.calls[1]["input"]["earlier_questions"][0]["quote"] == reading.sentences[0]["text"]
    assert [line[0] for request in planner.calls for line in request["input"]["sentences"]
            if isinstance(line[0], int)] == list(range(1, len(reading.sentences) + 1))
    # Each batch must write to its own isolated directory and replay from cache without extra model calls.
    assert (run_dir / "plan-01").is_dir() and (run_dir / "plan-02").is_dir()
    prev_calls = len(planner.calls)
    assert unit.plan_unit(reading, run_dir, planner) == plan
    assert len(planner.calls) == prev_calls


def test_modified_plan_does_not_reach_extraction(tmp_path):
    manifest = example(tmp_path)
    folder = tmp_path / "plan"
    plan = unit.run(manifest, folder, Planner())
    plan["questions"][0]["question"] = "Changed after generation"
    (folder / "unit-plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="saved unit plan changed"):
        unit.UnitContext.load(folder, "T", tmp_path / "current.json")


def test_discontinuous_ranges_never_join_two_quotes(tmp_path):
    earlier = {"script": [{"text": "第一句。跳過這句。第三句。", "index": 1}]}
    manifest = example(tmp_path)
    data = json.loads(manifest.read_bytes())
    source = write_source(tmp_path / "previous.json", earlier, "previous", "S0001#001", "S0001#001")
    source["ranges"].append(dict(first="S0001#003", last="S0001#003"))
    data["sources"][0] = source
    manifest.write_text(json.dumps(data))
    folder = tmp_path / "plan"
    unit.run(manifest, folder, Planner())
    context = unit.UnitContext.load(folder, "T", tmp_path / "current.json")
    passages = context.for_paragraphs(["S0001"])["unit_context"]["context_passages"]
    assert [row["text"] for row in passages if row["source_id"] == "previous"] == ["第一句。", "第三句。"]


def test_scoped_trial_exports_the_local_edges_needed_for_unit_review(tmp_path):
    manifest = example(tmp_path)
    folder = tmp_path / "plan"
    unit.run(manifest, folder, Planner())
    trial = creation.run("T", tmp_path / "current.json", tmp_path / "local", StepsPorts(_transcript()),
                         groups=groups(), steps_first=True, grouped_package=True,
                         only_paragraphs=["S0001"], unit_context_dir=folder)
    assert trial["status"] == "partial_test"
    assert trial["source_id"] == "T"
    assert any(e["relation_type"] == "supports" and e["to_id"] == "CL0001" for e in trial["local_connections"])
    assert not (tmp_path / "local" / "candidate.json").exists()
