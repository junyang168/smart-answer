from copy import deepcopy

import pytest

from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline import other_claim_partition_arbitration as runner


def packet():
    rows = [{"claim_id": cid, "statement": "神奇妙的安排与兴起。",
             "claim_content_sha256": cid * 32, "source_file_sha256": "a" * 64,
             "context": [{"paragraph_key": "S0001", "text": "原文\nδίκαιος 與興起，字元不可改变。"}],
             "prior_primary": {"decision": {"partition": "god_trinity", "reason": "神的护理"}},
             "prior_independent": {"decision": {"partition": "other", "reason": "个人经历"}}}
            for cid in ("C1", "C2")]
    return base._artifact({"rows": rows, "model": runner.MODEL})


def response(partition="god_trinity", disposition="resolved", selection="E0001"):
    return {"decisions": {cid: {"partition": partition, "disposition": disposition,
        "source_selection": selection, "reason": "上下文明确是护理判断，不是仅叙述经历；解释双方意见。"}
        for cid in ("C1", "C2")}}


def test_context_source_selection_preserves_unicode_and_pins_both_prior_reasons():
    p = packet()
    pin, payload, schema, size = runner.binding(p, p["rows"])
    assert pin["model"] == "gpt-6.1-sol"
    assert "神的护理" in payload and "个人经历" in payload and "δίκαιος" in payload
    assert size < 500000
    assert schema["schema"]["properties"]["decisions"]["properties"]["C1"]["properties"]["source_selection"]["enum"] == ["NONE", "E0001"]
    decisions = runner.validate(response(), p["rows"])
    assert decisions["C1"]["source_quote"] == p["rows"][0]["context"][0]["text"]
    assert decisions["C1"]["source_file_sha256"] == "a" * 64


@pytest.mark.parametrize("disposition", ["needs_human", "repair_required"])
def test_uncertainty_or_data_repair_never_becomes_a_partition(disposition):
    p = packet()
    resolved = runner.validate(response("unresolved", disposition, "NONE"), p["rows"])
    assert all(d["partition"] == "unresolved" and not d["source_quote"] for d in resolved.values())
    with pytest.raises(ValueError, match="unresolved disposition"):
        runner.validate(response("other", disposition, "NONE"), p["rows"])


@pytest.mark.parametrize("bad", ["missing", "foreign", "quote_id", "no_source", "unresolved_resolved", "empty_reason"])
def test_arbitration_rejects_ungrounded_and_invalid_decisions(bad):
    p = packet()
    a = response()
    if bad == "missing":
        a["decisions"].pop("C2")
    elif bad == "foreign":
        a["decisions"]["C3"] = a["decisions"]["C1"]
    elif bad == "quote_id":
        a["decisions"]["C1"]["source_selection"] = "E9999"
    elif bad == "no_source":
        a["decisions"]["C1"]["source_selection"] = "NONE"
    elif bad == "unresolved_resolved":
        a["decisions"]["C1"]["partition"] = "unresolved"
    else:
        a["decisions"]["C1"]["reason"] = " "
    with pytest.raises(ValueError):
        runner.validate(a, p["rows"])


def test_raw_answer_saved_before_validation_one_retry_then_cached_no_call(tmp_path):
    p = packet()
    class Client:
        calls = 0
        def generate_json(self, *args):
            self.calls += 1
            return response(selection="E9999") if self.calls == 1 else response()
    client = Client()
    result = runner.run_batch(p, p["rows"], tmp_path, client)
    assert len(result["decisions"]) == 2 and result["human_approval"] is False
    assert result["database_mutations"] == 0 and not result["grouping_authorization"]
    assert client.calls == 2
    assert len(list(tmp_path.glob("*.validation-failure.json"))) == 1
    assert base._read_json(next(tmp_path.glob("*.attempt-1.json")))["response"]["decisions"]["C1"]["source_selection"] == "E9999"
    assert runner.run_batch(p, p["rows"], tmp_path, client) == result
    assert client.calls == 2


def test_two_invalid_answers_stop_and_transport_failure_is_never_auto_restarted(tmp_path):
    p = packet()
    class Invalid:
        calls = 0
        def generate_json(self, *args):
            self.calls += 1
            return response(selection="E9999")
    client = Invalid()
    for _ in range(2):
        with pytest.raises(ValueError):
            runner.run_batch(p, p["rows"], tmp_path, client)
    assert client.calls == 2
    class Transport:
        calls = 0
        last_raw_response = {"stdout": "retained failure", "stderr": "quota"}
        def generate_json(self, *args):
            self.calls += 1
            raise RuntimeError("quota")
    client = Transport()
    with pytest.raises(RuntimeError):
        runner.run_batch(p, p["rows"], tmp_path / "transport", client)
    with pytest.raises(ValueError, match="no auto restart"):
        runner.run_batch(p, p["rows"], tmp_path / "transport", client)
    assert client.calls == 1
    failure = base._read_json(next((tmp_path / "transport").glob("*.failure.json")))
    assert failure["raw_response"] == client.last_raw_response


def test_stale_or_oversized_context_fails_before_model_call():
    p = packet()
    changed = deepcopy(p["rows"])
    changed[0]["context"][0]["text"] += "drift"
    with pytest.raises(ValueError, match="freeze"):
        runner.binding(p, changed)
    p["rows"][0]["context"][0]["text"] = "字" * 200000
    p = base._artifact({k: v for k, v in p.items() if k != "artifact_sha256"})
    with pytest.raises(ValueError, match="byte ceiling"):
        runner.binding(p, p["rows"])


def test_physical_source_pin_checked_before_run(tmp_path):
    import hashlib
    source = tmp_path / "source.txt"
    source.write_text("原文\nδίκαιος")
    row = {"claim_id": "C1", "source_path": str(source),
           "source_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    runner.check_sources([row, row])
    with pytest.raises(ValueError, match="conflicting"):
        runner.check_sources([row, row | {"source_file_sha256": "bad"}])
    source.write_text("changed")
    with pytest.raises(ValueError, match="source file changed"):
        runner.check_sources([row])
