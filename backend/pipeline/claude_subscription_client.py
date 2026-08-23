"""Structured-output client backed by a Claude.ai/Claude Code subscription."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Mapping


class ClaudeSubscriptionError(RuntimeError):
    """Claude Code could not produce a subscription-backed structured response."""


# Claude Code gives these sources precedence over the OAuth login stored by
# `claude auth login`. Remove them from both the auth check and generation.
API_BILLING_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
})


def subscription_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    values = dict(os.environ if source is None else source)
    for name in API_BILLING_ENV_VARS:
        values.pop(name, None)
    return values


def _diagnostic(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
    return detail[-4000:]


class ClaudeSubscriptionClient:
    """Adapt ``claude -p`` to the pipeline's ``generate_json`` contract."""

    backend = "claude_subscription"

    def __init__(
        self,
        *,
        model: str,
        reasoning_effort: str = "high",
        timeout_seconds: float = 900.0,
        max_output_tokens: int = 64000,
        executable: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        # Kept for the shared reviewer fingerprint. Claude Code does not expose
        # an output-token ceiling flag; its schema-constrained turn owns that.
        self.max_output_tokens = max_output_tokens
        self.executable = executable or shutil.which("claude") or "claude"
        self.environment = subscription_environment(environment)
        # Subscription calls are not API-billed per invocation. Feeding their
        # token metadata to API pricing would invent a dollar charge.
        self.last_usage: Any = None
        self.last_metadata: dict[str, Any] = {}
        self._authenticated = False

    def _verify_subscription_login(self) -> None:
        if self._authenticated:
            return
        try:
            completed = subprocess.run(
                [self.executable, "auth", "status"],
                capture_output=True,
                text=True,
                env=self.environment,
                timeout=min(self.timeout_seconds, 30.0),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ClaudeSubscriptionError(
                f"unable to verify Claude subscription login: {type(exc).__name__}: {exc}"
            ) from exc
        try:
            status = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ClaudeSubscriptionError(
                f"Claude auth status was not JSON: {_diagnostic(completed)}"
            ) from exc
        subscription = str(status.get("subscriptionType") or "").casefold()
        if (
            completed.returncode != 0
            or status.get("loggedIn") is not True
            or status.get("authMethod") != "claude.ai"
            or subscription not in {"pro", "max", "team", "enterprise"}
        ):
            raise ClaudeSubscriptionError(
                "claude-subscription requires an active claude.ai Pro, Max, Team, "
                f"or Enterprise login; received: {_diagnostic(completed)}"
            )
        self._authenticated = True

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
        cache_prefix: str | None = None,
    ) -> dict[str, Any]:
        del temperature
        self._verify_subscription_login()
        schema = json_schema.get("schema", json_schema)
        prompt = "".join((cache_prefix or "", user_prompt))
        command = [
            self.executable,
            "--print",
            "--safe-mode",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--model",
            self.model,
            "--effort",
            self.reasoning_effort,
            "--system-prompt",
            system_prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, ensure_ascii=False, sort_keys=True),
        ]
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                env=self.environment,
                timeout=timeout_seconds or self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ClaudeSubscriptionError(
                f"Claude subscription transport failed: {type(exc).__name__}: {exc}"
            ) from exc
        if completed.returncode != 0:
            raise ClaudeSubscriptionError(
                f"Claude subscription generation failed (exit {completed.returncode}): "
                f"{_diagnostic(completed)}"
            )
        try:
            wrapper = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeSubscriptionError(
                "Claude subscription returned no valid JSON envelope: "
                f"{exc}; diagnostic: {_diagnostic(completed)}"
            ) from exc
        if wrapper.get("is_error"):
            raise ClaudeSubscriptionError(
                f"Claude subscription returned an error result: {wrapper.get('result') or wrapper}"
            )
        response = wrapper.get("structured_output")
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError as exc:
                raise ClaudeSubscriptionError(
                    f"Claude structured_output was not valid JSON: {exc}"
                ) from exc
        if not isinstance(response, dict):
            raise ClaudeSubscriptionError(
                "Claude subscription response must contain an object in structured_output"
            )
        self.last_metadata = {
            key: wrapper.get(key)
            for key in ("session_id", "duration_ms", "duration_api_ms", "num_turns")
            if wrapper.get(key) is not None
        }
        return response
