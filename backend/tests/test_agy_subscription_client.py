import json
from pathlib import Path
import subprocess

import pytest

from backend.pipeline.agy_subscription_client import (
    AgySubscriptionClient,
    AgySubscriptionError,
    subscription_environment,
)


def test_subscription_environment_strips_api_keys():
    env = subscription_environment({
        "PATH": "/usr/bin",
        "GEMINI_API_KEY": "secret1",
        "GEMINI_API_KEY1": "secret2",
        "GOOGLE_API_KEY": "secret3",
        "ANTHROPIC_API_KEY": "secret4",
        "OPENAI_API_KEY": "secret5",
    })
    assert env == {"PATH": "/usr/bin"}


def test_agy_client_retries_transient_empty_conversation_id(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="error: failed to start conversation: backend returned an empty conversation id"
            )
        envelope = {
            "conversation_id": "conv-123",
            "status": "SUCCESS",
            "structured_output": {"ok": True},
            "duration_seconds": 1.2,
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(envelope), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = AgySubscriptionClient(
        model="gemini-4-neon-high",
        executable="/usr/local/bin/agy",
        startup_retries=2,
        retry_backoff_seconds=0.0,
    )
    client.call_artifacts = tmp_path / "artifacts"
    out = client.generate_json("sys", "usr", {"type": "object"})
    assert out == {"ok": True}
    assert len(calls) == 2
    assert (tmp_path / "artifacts" / "structured_output.json").exists()


def test_agy_client_raises_on_non_transient_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fatal model error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = AgySubscriptionClient(
        model="gemini-4-neon-high",
        executable="/usr/local/bin/agy",
        startup_retries=2,
        retry_backoff_seconds=0.0,
    )
    with pytest.raises(AgySubscriptionError, match="fatal model error"):
        client.generate_json("sys", "usr", {"type": "object"})
