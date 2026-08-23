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
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_subscription_environment_removes_higher_precedence_credentials() -> None:
    source = {name: "secret" for name in API_BILLING_ENV_VARS}
    source.update({"PATH": "/bin", "HOME": "/oauth-keychain"})
    result = subscription_environment(source)
    assert not API_BILLING_ENV_VARS.intersection(result)
    assert result["PATH"] == "/bin"
    assert result["HOME"] == "/oauth-keychain"


def test_non_subscription_login_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command, **_kwargs):
        return _completed(command, stdout=json.dumps({
            "loggedIn": True, "authMethod": "console", "subscriptionType": None,
        }))

    monkeypatch.setattr("backend.pipeline.claude_subscription_client.subprocess.run", fake_run)
    client = ClaudeSubscriptionClient(model="claude-sonnet-5", executable="claude")
    with pytest.raises(ClaudeSubscriptionError, match="active claude.ai"):
        client.generate_json("system", "user", {"type": "object"})


def test_structured_output_uses_subscription_safe_cli_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["auth", "status"]:
            return _completed(command, stdout=json.dumps({
                "loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro",
            }))
        return _completed(command, stdout=json.dumps({
            "is_error": False,
            "session_id": "session",
            "structured_output": {"answer": "ok"},
        }))

    monkeypatch.setattr("backend.pipeline.claude_subscription_client.subprocess.run", fake_run)
    environment = {
        "PATH": "/bin", "HOME": "/oauth-keychain",
        "ANTHROPIC_API_KEY": "must-not-leak", "ANTHROPIC_AUTH_TOKEN": "nor-this",
    }
    client = ClaudeSubscriptionClient(
        model="claude-sonnet-5", executable="claude", environment=environment,
    )
    schema = {"name": "answer", "strict": True, "schema": {
        "type": "object", "additionalProperties": False,
        "properties": {"answer": {"type": "string"}}, "required": ["answer"],
    }}
    assert client.generate_json("system", "tail", schema, cache_prefix="stable") == {
        "answer": "ok"
    }
    command, kwargs = calls[1]
    assert "--bare" not in command
    assert "--safe-mode" in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--output-format") + 1] == "json"
    assert json.loads(command[command.index("--json-schema") + 1]) == schema["schema"]
    assert kwargs["input"] == "stabletail"
    assert not API_BILLING_ENV_VARS.intersection(kwargs["env"])
    assert client.last_usage is None
    assert client.last_metadata["session_id"] == "session"


def test_cli_failure_has_no_api_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        if command[1:3] == ["auth", "status"]:
            return _completed(command, stdout=json.dumps({
                "loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro",
            }))
        return _completed(command, stderr="subscription limit reached", returncode=1)

    monkeypatch.setattr("backend.pipeline.claude_subscription_client.subprocess.run", fake_run)
    client = ClaudeSubscriptionClient(model="claude-sonnet-5", executable="claude")
    with pytest.raises(ClaudeSubscriptionError, match="subscription limit reached"):
        client.generate_json("system", "user", {"type": "object"})
    assert calls == 2
