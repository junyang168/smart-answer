"""Structured-output client backed by the Antigravity (agy) subscription CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Mapping


class AgySubscriptionError(RuntimeError):
    """Antigravity CLI could not produce a subscription-backed structured response."""


# Strip API-key environment variables so subscription invocations never fall back to paid APIs.
API_BILLING_ENV_VARS = frozenset({
    "GEMINI_API_KEY",
    "GEMINI_API_KEY1",
    "GEMINI_API_KEY2",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
})

TRANSIENT_STARTUP_MARKERS = (
    "empty conversation id",
    "failed to start conversation",
)


def subscription_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    values = dict(os.environ if source is None else source)
    for name in API_BILLING_ENV_VARS:
        values.pop(name, None)
    return values


def _resolve_executable(override: str | None = None) -> str:
    if override:
        return override
    configured = os.environ.get("AGY_EXECUTABLE", "").strip()
    if configured:
        return configured
    found = shutil.which("agy")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "agy"
    if fallback.exists():
        return str(fallback)
    return "agy"


def _diagnostic(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
    return detail[-4000:]


class AgySubscriptionClient:
    """Adapt ``agy --print --output-format json --json-schema`` to ``generate_json``."""

    backend = "agy_subscription"

    def __init__(
        self,
        *,
        model: str = "gemini-4-neon-high",
        timeout_seconds: float = 600.0,
        executable: str | None = None,
        environment: Mapping[str, str] | None = None,
        startup_retries: int = 2,
        retry_backoff_seconds: float = 3.0,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.executable = _resolve_executable(executable)
        self.environment = subscription_environment(environment)
        self.startup_retries = max(0, int(startup_retries))
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.call_artifacts: Path | None = None
        self.last_usage: Any = None
        self.last_raw_response: Any = None
        self.last_metadata: dict[str, Any] = {}

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        full_prompt = (
            "Ignore any plugin setup notices. Do not call any tools or read any files. "
            "Respond strictly with valid JSON matching the provided JSON schema.\n\n"
            f"=== SYSTEM INSTRUCTIONS ===\n{system_prompt}\n\n"
            f"=== USER INPUT ===\n{user_prompt}"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as sf:
            json.dump(json_schema, sf, ensure_ascii=False)
            schema_path = sf.name

        try:
            attempt = 0
            while True:
                attempt += 1
                cmd = [
                    self.executable,
                    "--model",
                    self.model,
                    "--output-format",
                    "json",
                    "--disable-slash-commands",
                    "--json-schema",
                    schema_path,
                    f"--print={full_prompt}",
                ]
                try:
                    completed = subprocess.run(
                        cmd,
                        cwd="/tmp",
                        capture_output=True,
                        text=True,
                        env=self.environment,
                        timeout=self.timeout_seconds,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise AgySubscriptionError(
                        f"agy subscription call failed: {type(exc).__name__}: {exc}"
                    ) from exc

                diag = _diagnostic(completed)
                if completed.returncode != 0:
                    if attempt <= self.startup_retries and any(m in diag.lower() for m in TRANSIENT_STARTUP_MARKERS):
                        time.sleep(self.retry_backoff_seconds * attempt)
                        continue
                    raise AgySubscriptionError(
                        f"agy subscription generation failed (exit {completed.returncode}): {diag}"
                    )

                try:
                    envelope = json.loads(completed.stdout)
                except json.JSONDecodeError as exc:
                    raise AgySubscriptionError(
                        f"agy stdout was not valid JSON: {diag}"
                    ) from exc

                structured = envelope.get("structured_output")
                if envelope.get("status") != "SUCCESS" or not isinstance(structured, dict):
                    raise AgySubscriptionError(
                        f"agy returned non-success or missing structured_output: {json.dumps(envelope, ensure_ascii=False)[:1000]}"
                    )

                self.last_raw_response = envelope
                self.last_usage = None
                self.last_metadata = {
                    "backend": self.backend,
                    "model": self.model,
                    "conversation_id": envelope.get("conversation_id"),
                    "duration_seconds": envelope.get("duration_seconds"),
                    "usage": envelope.get("usage"),
                }
                if self.call_artifacts is not None:
                    art_dir = Path(self.call_artifacts)
                    art_dir.mkdir(parents=True, exist_ok=True)
                    (art_dir / "envelope.json").write_text(
                        json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    (art_dir / "structured_output.json").write_text(
                        json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                return structured
        finally:
            Path(schema_path).unlink(missing_ok=True)
