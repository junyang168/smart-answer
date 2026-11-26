from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.pipeline import claim_creation_flash as creation
from backend.pipeline import claim_unit_argument as argument


def read_rows():
    return SimpleNamespace(sentences=[dict(source_id=source, paragraph="S0001", start=0,
        end=3, modality="spoken", text=text) for source, text in [("A","怎樣？"),("B","他說。"),("C","如此。")]])


def test_unit_connections_keep_the_question_and_distinct_source_identities():
    claims = {"C1": dict(statement="回答", at=[3])}
    questions = {"Q1": dict(kind="question", statement="怎樣？", at=[1])}
    reply = dict(new_nodes=[dict(id="N1", kind="evidence", statement="理由", at=[2])],
                 edges=[dict(from_id="C1", to_id="Q1", relation="answers", reason="解答", at=[1,3], attribution="explicit"),
                        dict(from_id="N1", to_id="C1", relation="supports", reason="支持", at=[2,3], attribution="explicit")],
                 question_states=[dict(question_id="Q1", state="answered", remaining="")], gaps=[])
    nodes = argument.bind_graph(read_rows(), claims, questions, reply)
    assert nodes["Q1"]["at"] == [1] and nodes["C1"]["at"] == [3]
    separate_grounds = deepcopy(reply)
    separate_grounds["edges"].append(dict(reply["edges"][0], reason="另一处交代怎样作答", at=[2,3]))
    assert argument.bind_graph(read_rows(), claims, questions, separate_grounds) == nodes
    for change in ["foreign", "unbound", "duplicate", "imagined_answer", "cross_source_claim"]:
        bad = deepcopy(reply)
        if change == "foreign":
            bad["edges"][0]["to_id"] = "OTHER"
        elif change == "unbound":
            bad["edges"][0]["at"] = [0]
        elif change == "duplicate":
            bad["edges"] *= 2
        elif change == "imagined_answer":
            bad["edges"][0]["attribution"] = "editorial"
        else:
            bad["new_nodes"][0].update(kind="claim", at=[1,2])
        with pytest.raises(ValueError):
            argument.bind_graph(read_rows(), claims, questions, bad)


def test_topic_can_resume_without_splicing_its_quotations():
    ctx = SimpleNamespace(reading=read_rows(), plan={"runs":[
        {"first":1,"group":"长题"}, {"first":2,"group":"插问"}, {"first":3,"group":"长题"}]})
    result = argument.group_passages(ctx)
    assert [p["positions"] for p in result["长题"]] == [[1],[3]]
    assert result["插问"][0]["source_id"] == "B"


def test_sentence_range_projection_rejects_unknown_or_reversed_identifiers():
    sents = [SimpleNamespace(sentence_id=f"S0001#{i:03d}", segment_index="S0001") for i in range(1, 5)]
    rows = [dict(first="S0001#001", last="S0001#002"),dict(first="S0001#003",last="S0001#004")]
    assert creation.project_unit_claim_ranges(rows, ["S0001"], sents) == [dict(first="S0001",last="S0001")]
    for end in ["S0001#999", "S0001#001"]:
        with pytest.raises(ValueError):
            creation.project_unit_claim_ranges([dict(first="S0001#003", last=end)], ["S0001"], sents)


def test_complete_unit_replays_and_preserves_local_claim_identity(tmp_path):
    from backend.tests.test_claim_creation_flash import Ports
    from backend.tests.test_claim_unit_plan import Planner, example
    from backend.pipeline import claim_unit_plan as plan

    folder = tmp_path / "plan"
    plan.run(example(tmp_path), folder, Planner())

    class UnitPorts(Ports):
        def repair(self, request):
            if request["input"]["task"] == "creation_unit_connections":
                self.calls.append("creation_unit_connections")
                assert list(request["input"]["claims"]) == ["C001"]
                return dict(new_nodes=[], edges=[dict(from_id="C001", to_id="Q001", relation="answers",
                    reason="後一講作答", at=[1,2], attribution="explicit")],
                    question_states=[dict(question_id="Q001", state="answered", remaining="")], gaps=[])
            reply = super().repair(request)
            supplied = request["input"]["owned_paragraphs"]
            key = list(supplied)[1]
            reply["candidates"][0].update(related_ranges=[dict(first=key,last=key)],
                conclusion_anchor=dict(segment_index=key, start_time=None, end_time=None, verbatim_excerpt=supplied[key]))
            return reply

    ports = UnitPorts(None)
    run = tmp_path / "argument"
    receipt = argument.run(folder, run, ports)
    assert receipt["claims"] == receipt["edges"] == 1
    assert argument.run(folder, run, ports) == receipt
    assert ports.calls == ["creation_claims", "creation_unit_connections"]
    candidate = argument.json.loads((run / "unit-argument.json").read_bytes())
    assert candidate["claims"]["C001"]["source_id"] == "T"
    assert candidate["sentences"][0]["source_id"] == "previous"
    assert "C001 → Q001" in (run / "unit-argument.md").read_text()

    packet = argument.blind_reading(candidate)
    assert argument.json.loads((run / "unit-argument-reading.json").read_bytes()) == packet
    assert packet["nodes"]["C001"] == {"kind": "claim", "statement": candidate["claims"]["C001"]["statement"]}
    assert packet["edges"]["E1"] == dict(from_id="C001", to_id="Q001", relation="answers",
        reason="後一講作答", at=[1,2], attribution="explicit") if False else dict(
        from_id="C001", to_id="Q001", relation="answers", reason="後一講作答", attribution="explicit")
    assert set(packet) == {"nodes", "edges", "question_states", "gaps"}


def test_connect_batches_when_unit_exceeds_payload_limit(tmp_path, monkeypatch):
    from backend.tests.test_claim_creation_flash import Ports

    rows = [
        dict(source_id="A", paragraph="S0001", time="00:00", sentence_id="S0001#001",
             start=0, end=10, modality="spoken", text="為什麼？" + "甲" * 1200),
        dict(source_id="B", paragraph="S0001", time="00:00", sentence_id="S0001#001",
             start=0, end=10, modality="spoken", text="理由。" + "乙" * 1200),
        dict(source_id="C", paragraph="S0001", time="00:00", sentence_id="S0001#001",
             start=0, end=10, modality="spoken", text="結論。" + "丙" * 1200),
    ]
    reading = SimpleNamespace(sentences=rows, sources=[{"source_id": s} for s in ("A", "B", "C")], verify=lambda: None)
    ctx = SimpleNamespace(reading=reading, plan={"runs": [{"first": 1, "group": "g1"}],
                                                 "questions": [{"at": 1, "question": "為什麼？"}]})
    claims = {"C001": dict(statement="回答", group="g1", at=[3], answers_question="為什麼？", support_hint="")}

    class BatchPorts(Ports):
        def repair(self, request):
            self.calls.append(request["input"])
            sent_nums = [x[0] for x in request["input"]["sentences"] if isinstance(x[0], int)]
            if 2 in sent_nums and 3 not in sent_nums:
                return dict(
                    new_nodes=[dict(id="N1", kind="evidence", statement="理由", at=[2])],
                    edges=[dict(from_id="N1", to_id="C001", relation="supports", reason="支持", at=[2, 3], attribution="explicit")],
                    question_states=[dict(question_id="Q001", state="open", remaining="待后批回答")],
                    gaps=[],
                )
            if 3 in sent_nums:
                return dict(
                    new_nodes=[],
                    edges=[dict(from_id="C001", to_id="Q001", relation="answers", reason="後一講作答", at=[1, 3], attribution="explicit")],
                    question_states=[dict(question_id="Q001", state="answered", remaining="")],
                    gaps=[],
                )
            return dict(
                new_nodes=[],
                edges=[],
                question_states=[dict(question_id="Q001", state="open", remaining="待后批回答")],
                gaps=[],
            )

    monkeypatch.setattr(creation.f, "GPT_BYTES", 10500)
    ports = BatchPorts(None)
    run = tmp_path / "batched_arg"
    run.mkdir()
    receipt = argument.connect(run, ctx, claims, ports)
    assert len(ports.calls) >= 2
    assert (run / "connections-01").is_dir() and (run / "connections-02").is_dir()
    assert receipt["edges"] == 2
    # Replay from cache should make zero new calls.
    prev_calls = len(ports.calls)
    assert argument.connect(run, ctx, claims, ports) == receipt
    assert len(ports.calls) == prev_calls
