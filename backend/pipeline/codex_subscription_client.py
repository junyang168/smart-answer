"""Structured-output client backed by a local ChatGPT/Codex subscription."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping


class CodexSubscriptionError(RuntimeError):
    """The subscription backend could not produce a structured response."""


# These variables can make Codex use separately billed API credentials instead
# of the local ChatGPT login. Keep the list explicit: the child still needs its
# ordinary environment (notably PATH and CODEX_HOME) to find the CLI and OAuth
# state, while none of these values is necessary for ChatGPT authentication.
API_BILLING_ENV_VARS = frozenset({
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "OPENAI_PROJECT_ID",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "CODEX_API_KEY",
})


def subscription_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child environment that cannot select API-key billing."""

    values = dict(os.environ if source is None else source)
    for name in API_BILLING_ENV_VARS:
        values.pop(name, None)
    return values


def _resolve_executable(override: str | None = None) -> str:
    """Find the codex CLI: explicit override, then CODEX_EXECUTABLE, then PATH.

    The CLI ships inside application bundles that are not on PATH, so a
    machine can have it installed and still fail `shutil.which`. CODEX_EXECUTABLE
    lets a checkout record where this machine keeps it instead of every caller
    reconstructing a PATH.
    """

    if override:
        return override
    configured = os.environ.get("CODEX_EXECUTABLE", "").strip()
    if configured:
        return configured
    return shutil.which("codex") or "codex"


def _diagnostic(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
    return detail[-4000:]


class CodexSubscriptionClient:
    """Adapt ``codex exec`` to the extraction runner's ``generate_json`` contract.

    Authentication is intentionally lazy. A generation-fingerprint or section
    cache hit must not launch Codex merely to confirm a login that it will not
    use. The first real call verifies that the sanitized child environment is
    logged in specifically through ChatGPT, then all calls fail closed on any
    CLI, quota, authentication, transport, or output error.
    """

    backend = "codex_subscription"

    def __init__(
        self,
        *,
        model: str,
        reasoning_effort: str = "medium",
        timeout_seconds: float = 900.0,
        max_output_tokens: int = 64000,
        executable: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.executable = _resolve_executable(executable)
        self.environment = subscription_environment(environment)
        self.last_usage: Any = None
        self._authenticated = False

    def _verify_chatgpt_login(self) -> None:
        if self._authenticated:
            return
        try:
            completed = subprocess.run(
                [self.executable, "login", "status"],
                capture_output=True,
                text=True,
                env=self.environment,
                timeout=min(self.timeout_seconds, 30.0),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexSubscriptionError(
                f"unable to verify Codex ChatGPT login: {type(exc).__name__}: {exc}"
            ) from exc
        status = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        if completed.returncode != 0 or status.casefold() != "logged in using chatgpt":
            raise CodexSubscriptionError(
                "codex-subscription requires `codex login status` to report a ChatGPT login; "
                f"received: {status or f'exit {completed.returncode} with no output'}"
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
        del temperature  # Codex uses the selected model's supported controls.
        self._verify_chatgpt_login()
        effective_timeout = timeout_seconds or self.timeout_seconds
        schema = json_schema.get("schema", json_schema)
        prompt_parts = [
            "Perform this structured extraction without using tools or modifying files. "
            "Return only the JSON object required by the supplied output schema.",
            "\n===== SYSTEM INSTRUCTIONS =====\n",
            system_prompt,
            "\n===== USER INPUT =====\n",
            cache_prefix or "",
            user_prompt,
        ]
        prompt = "".join(prompt_parts)

        with tempfile.TemporaryDirectory(prefix="codex-subscription-extraction-") as temp_dir:
            root = Path(temp_dir)
            schema_path = root / "response-schema.json"
            output_path = root / "response.json"
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            command = [
                self.executable,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--model",
                self.model,
                "--config",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=root,
                    env=self.environment,
                    timeout=effective_timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CodexSubscriptionError(
                    f"Codex subscription transport failed: {type(exc).__name__}: {exc}"
                ) from exc
            if completed.returncode != 0:
                raise CodexSubscriptionError(
                    f"Codex subscription generation failed (exit {completed.returncode}): "
                    f"{_diagnostic(completed)}"
                )
            try:
                content = output_path.read_text(encoding="utf-8")
                response = json.loads(content)
            except (OSError, json.JSONDecodeError) as exc:
                raise CodexSubscriptionError(
                    "Codex subscription returned no valid structured output: "
                    f"{type(exc).__name__}: {exc}; CLI diagnostic: {_diagnostic(completed)}"
                ) from exc
            if not isinstance(response, dict):
                raise CodexSubscriptionError(
                    f"Codex subscription response must be a JSON object, got {type(response).__name__}"
                )
            return response
