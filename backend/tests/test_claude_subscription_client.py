from __future__ import annotations

import json
import subprocess

import pytest

from backend.pipeline.claude_subscription_client import (
    API_BILLING_ENV_VARS,
    ClaudeSubscriptionClient,
    ClaudeSubscriptionError,
    subscription_environment,
)


def _completed(args, *, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_claude_subscription_environment_removes_api_credentials():
    source = {name: "secret" for name in API_BILLING_ENV_VARS}
    source["PATH"] = "/bin"
    result = subscription_environment(source)
    assert not API_BILLING_ENV_VARS.intersection(result)
    assert result["PATH"] == "/bin"


def test_claude_api_key_auth_fails_closed(monkeypatch: pytest.MonkeyPatch):
    def fake_run(command, **_kwargs):
        return _completed(command, stdout=json.dumps({
            "loggedIn": True, "authMethod": "api_key", "subscriptionType": None,
        }))

    monkeypatch.setattr(
        "backend.pipeline.claude_subscription_client.subprocess.run", fake_run
    )
    client = ClaudeSubscriptionClient(model="claude-sonnet-5", executable="claude")
    with pytest.raises(ClaudeSubscriptionError, match="claude.ai subscription"):
        client.generate_json("system", "user", {"type": "object"})


def test_claude_subscription_returns_structured_output(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["auth", "status"]:
            return _completed(command, stdout=json.dumps({
                "loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro",
            }))
        return _completed(command, stdout=json.dumps({
            "structured_output": {"ok": True}, "usage": {"input_tokens": 1},
        }))

    monkeypatch.setattr(
        "backend.pipeline.claude_subscription_client.subprocess.run", fake_run
    )
    client = ClaudeSubscriptionClient(
        model="claude-sonnet-5", executable="claude",
        environment={"PATH": "/bin", "ANTHROPIC_API_KEY": "must-not-leak"},
    )
    assert client.generate_json(
        "system", "user",
        {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    ) == {"ok": True}
    assert len(calls) == 2
    assert all("ANTHROPIC_API_KEY" not in call[1]["env"] for call in calls)
    generation_command = calls[1][0]
    assert "--safe-mode" in generation_command
    assert generation_command[generation_command.index("--tools") + 1] == ""
