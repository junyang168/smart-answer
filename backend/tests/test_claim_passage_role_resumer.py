"""No-model tests for bounded #409 resume supervision."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from backend.pipeline import claim_passage_role_runner as role_runner
from backend.pipeline import claim_passage_role_resumer as resumer


def test_only_fresh_model_validation_errors_are_retryable():
    assert resumer.retryable_validation_error(
        "Traceback\nValueError: interpreted references disagree with role: CL-1\n"
    ) == "ValueError: interpreted references disagree with role: CL-1"
    assert resumer.retryable_validation_error(
        "Traceback\nValueError: role packet changed during model review\n"
    ) is None
    assert resumer.retryable_validation_error(
        "ClaudeSubscriptionError: subscription quota exhausted\n"
    ) is None


def test_supervisor_retries_one_invalid_answer_without_changing_packet(tmp_path, monkeypatch):
    packet = role_runner._artifact({
        "mode": "all_eligible", "claims": [{"claim_id": "CL-1"}],
        "runner_code_sha256": hashlib.sha256(Path(role_runner.__file__).read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(role_runner.PROMPT.read_bytes()).hexdigest(),
    })
    role_runner._write_immutable(tmp_path / "role-packet.json", packet)
    calls = []

    def fake_run(command, *, cwd, stdout, stderr, check):
        del command, cwd, stderr, check
        calls.append(1)
        if len(calls) == 1:
            stdout.write("ValueError: interpreted references disagree with role: CL-1\n")
            return SimpleNamespace(returncode=1)
        (tmp_path / "primary-00001.json").write_text("{}", encoding="utf-8")
        (tmp_path / "independent-00001.json").write_text("{}", encoding="utf-8")
        role_runner._write_immutable(
            tmp_path / "role-ledger-v4.json", role_runner._artifact({"decisions": []})
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(resumer.subprocess, "run", fake_run)
    assert resumer.supervise(
        tmp_path, batch_size=16, max_invalid_retries=1,
        primary_model="gpt-6-sol", independent_model="claude-fable-5-1",
    ) == 0
    assert len(calls) == 2
    events = [json.loads(line) for line in (tmp_path / "resume-events.jsonl").read_text().splitlines()]
    assert [event["status"] for event in events] == [
        "retrying_invalid_model_response", "complete",
    ]
    assert events[0]["retry_number"] == 1
    assert role_runner._read_json(tmp_path / "role-packet.json") == packet
