"""Structured-output client backed by a local Claude Code subscription."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Mapping


class ClaudeSubscriptionError(RuntimeError):
    pass


API_BILLING_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
})


def subscription_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    values = dict(os.environ if source is None else source)
    for name in API_BILLING_ENV_VARS:
        values.pop(name, None)
    return values


class ClaudeSubscriptionClient:
    backend = "claude_subscription"

    def __init__(
        self, *, model: str, reasoning_effort: str = "medium",
        timeout_seconds: float = 900.0, max_output_tokens: int = 12000,
        executable: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.executable = executable or shutil.which("claude") or "claude"
        self.environment = subscription_environment(environment)
        self.last_usage: Any = None
        self._authenticated = False

    def _verify_subscription_login(self) -> None:
        if self._authenticated:
            return
        completed = subprocess.run(
            [self.executable, "auth", "status"], capture_output=True, text=True,
            env=self.environment, timeout=min(self.timeout_seconds, 30), check=False,
        )
        try:
            status = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeSubscriptionError("unable to parse Claude auth status") from exc
        if (
            completed.returncode != 0
            or not status.get("loggedIn")
            or status.get("authMethod") != "claude.ai"
            or not status.get("subscriptionType")
        ):
            raise ClaudeSubscriptionError(
                "claude-subscription requires a logged-in claude.ai subscription"
            )
        self._authenticated = True

    def generate_json(
        self, system_prompt: str, user_prompt: str,
        json_schema: dict[str, Any], temperature: float = 0.0,
        timeout_seconds: float | None = None, cache_prefix: str | None = None,
    ) -> dict[str, Any]:
        del temperature
        self._verify_subscription_login()
        schema = json_schema.get("schema", json_schema)
        prompt = (
            system_prompt + "\n\n===== INPUT =====\n"
            + (cache_prefix or "") + user_prompt
            + "\n\nReturn only the JSON object required by the schema."
        )
        command = [
            self.executable, "--safe-mode", "--tools", "",
            "--permission-mode", "dontAsk", "--no-session-persistence",
            "--model", self.model, "--effort", self.reasoning_effort,
            "--print", "--output-format", "json",
            "--json-schema", json.dumps(schema, ensure_ascii=False, sort_keys=True),
        ]
        try:
            completed = subprocess.run(
                command, input=prompt, capture_output=True, text=True,
                env=self.environment,
                timeout=timeout_seconds or self.timeout_seconds, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ClaudeSubscriptionError(
                f"Claude subscription transport failed: {type(exc).__name__}: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no output")[-4000:]
            raise ClaudeSubscriptionError(
                f"Claude subscription generation failed: {detail}"
            )
        try:
            envelope = json.loads(completed.stdout)
            result = envelope.get("structured_output")
            if not isinstance(result, dict):
                result = json.loads(str(envelope.get("result") or ""))
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise ClaudeSubscriptionError(
                "Claude subscription returned invalid structured output"
            ) from exc
        self.last_usage = envelope.get("usage")
        return result
