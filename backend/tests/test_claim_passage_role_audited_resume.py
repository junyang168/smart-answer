"""No-model tests for #409 rejected-answer retention and bounded retry."""

from __future__ import annotations

import pytest

from backend.pipeline import claim_passage_role_audited_resume as audited
from backend.pipeline import claim_passage_role_runner as base


ROW = {
    "claim_id": "CL-1", "statement": "此处的词是什么意思",
    "scripture_refs": ["太 1:1"],
    "evidence_steps": [{"statement": "解释该词", "scripture_refs": ["太 1:1"],
                        "fragments": [{"verbatim_excerpt": "教授原话"}]}],
}


def answer(evidence_refs: list[str]) -> dict:
    return {"schema_version": base.RESPONSE_VERSION, "decisions": {"CL-1": {
        "role": "passage_exegesis", "interpreted_ref_indices": [],
        "interpreted_evidence_refs": evidence_refs,
        "reason": "解释这处经文的词义。",
    }}}


class FakeClient:
    model = "claude-opus-5-5"

    def __init__(self, responses: list[dict]):
        self.responses = iter(responses)
        self.calls = 0

    def generate_json(self, prompt, payload, schema):
        assert "CL-1" in payload and "CL-1" in schema["schema"]["properties"]["decisions"]["required"]
        self.calls += 1
        return next(self.responses)


def test_rejected_structured_responses_are_retained_before_failure(tmp_path):
    client = FakeClient([answer(["Matthew 1:1"]), answer(["Matt 1:1"])])
    with pytest.raises(ValueError, match="raw answers retained"):
        audited._response_for_batch(
            "independent", [ROW], client=client, packet_sha="packet",
            manifest_sha="manifest", batch_id=69, output_root=tmp_path,
        )
    assert client.calls == 2
    assert not (tmp_path / "independent-00069.json").exists()
    for number in (1, 2):
        raw = base._read_json(tmp_path / "attempts" / f"independent-00069-attempt-{number}.json")
        base._check_artifact(raw)
        assert raw["response"]["decisions"]["CL-1"]["interpreted_evidence_refs"]
        event = base._read_json(tmp_path / "attempts" / f"independent-00069-attempt-{number}-validation.json")
        base._check_artifact(event)
        assert event["status"] == "rejected"
        assert event["raw_attempt_sha256"] == raw["artifact_sha256"]


def test_invalid_then_valid_never_promotes_rejected_answer(tmp_path):
    client = FakeClient([answer(["Matthew 1:1"]), answer(["太 1:1"])])
    decisions = audited._response_for_batch(
        "independent", [ROW], client=client, packet_sha="packet",
        manifest_sha="manifest", batch_id=69, output_root=tmp_path,
    )
    assert decisions[0]["interpreted_evidence_refs"] == ["太 1:1"]
    accepted = base._read_json(tmp_path / "independent-00069.json")
    base._check_artifact(accepted)
    assert accepted["decisions"] == decisions
    rejected = base._read_json(tmp_path / "attempts" / "independent-00069-attempt-1.json")
    assert rejected["response"]["decisions"]["CL-1"]["interpreted_evidence_refs"] == ["Matthew 1:1"]


def test_persisted_raw_response_recovers_without_another_model_call(tmp_path, monkeypatch):
    client = FakeClient([answer(["太 1:1"])])
    original_write = base._write_immutable

    def crash_before_promotion(path, value):
        if path.name == "independent-00069.json":
            raise RuntimeError("crash after raw answer")
        original_write(path, value)

    monkeypatch.setattr(base, "_write_immutable", crash_before_promotion)
    with pytest.raises(RuntimeError, match="crash after raw"):
        audited._response_for_batch(
            "independent", [ROW], client=client, packet_sha="packet",
            manifest_sha="manifest", batch_id=69, output_root=tmp_path,
        )
    monkeypatch.setattr(base, "_write_immutable", original_write)
    result = audited._response_for_batch(
        "independent", [ROW], client=client, packet_sha="packet",
        manifest_sha="manifest", batch_id=69, output_root=tmp_path,
    )
    assert result[0]["role"] == "passage_exegesis"
    assert client.calls == 1
