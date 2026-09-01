"""Shared artifact and subscription runtime for CVP and Route resolution."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
# 900s proved too tight for opus reviewing a 20-claim effective proposal at
# high effort (#315: final review timed out with every earlier stage done).
CALL_TIMEOUT_SECONDS = 1800.0


def read_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read_artifact(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def write_derived(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def recorded_model_executions(
    output_dir: Path, *, raw_artifacts: dict[str, str]
) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    for stage, filename in raw_artifacts.items():
        path = output_dir / filename
        if not path.exists():
            stages[stage] = {"calls_recorded": 0, "wall_seconds_recorded": 0.0}
            continue
        artifact = read_artifact(path)
        body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        if artifact.get("artifact_sha256") != sha256_json(body):
            raise ValueError(f"raw response artifact SHA mismatch: {path}")
        stages[stage] = {
            "calls_recorded": int(artifact.get("calls_recorded") or 1),
            "wall_seconds_recorded": round(float(artifact["wall_seconds"]), 3),
            "backend": artifact.get("backend"),
            "model_id": artifact.get("model_id"),
            "artifact_sha256": artifact["artifact_sha256"],
        }
    return {
        "stages": stages,
        "calls_recorded_total": sum(item["calls_recorded"] for item in stages.values()),
        "wall_seconds_recorded_total": round(
            sum(item["wall_seconds_recorded"] for item in stages.values()), 3
        ),
    }


def write_current_state(
    output_dir: Path,
    *,
    schema_version: str,
    identity: dict[str, str],
    status: str,
    authoritative_artifact: str,
    authoritative_artifact_sha256: str,
    superseded_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    state = {
        "schema_version": schema_version,
        **identity,
        "status": status,
        "authoritative_artifact": authoritative_artifact,
        "authoritative_artifact_sha256": authoritative_artifact_sha256,
        "superseded_artifacts": sorted(set(superseded_artifacts or [])),
    }
    state["artifact_sha256"] = sha256_json(state)
    write_derived(output_dir / "current-state.json", state)
    return state


def stable_decided_at(batch_dir: Path) -> str:
    path = batch_dir / "decision-time.json"
    if path.exists():
        payload = read_artifact(path)
        body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
        if payload.get("artifact_sha256") != sha256_json(body):
            raise ValueError(f"decision time artifact SHA mismatch: {path}")
        return str(payload["decided_at"])
    body = {
        "schema_version": "wang_cvp_batch_decision_time_v1",
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    write_immutable(path, body | {"artifact_sha256": sha256_json(body)})
    return str(body["decided_at"])


def subscription_client(
    provider: str,
    model: str,
    reasoning_effort: str,
    *,
    timeout_seconds: float = CALL_TIMEOUT_SECONDS,
) -> ClaudeSubscriptionClient | CodexSubscriptionClient:
    client_type = {
        "claude": ClaudeSubscriptionClient,
        "codex": CodexSubscriptionClient,
    }.get(provider)
    if client_type is None:
        raise ValueError(f"unsupported subscription provider: {provider}")
    return client_type(
        model=model,
        reasoning_effort=reasoning_effort,
        timeout_seconds=timeout_seconds,
    )


def call_model(
    adapter: Any, payload: dict[str, Any], cache: Path
) -> tuple[dict[str, Any], int, float]:
    request_sha = sha256_json(payload)
    if cache.exists():
        artifact = read_artifact(cache)
        if artifact.get("request_payload_sha256") != request_sha:
            raise ValueError(f"cached response belongs to another request payload: {cache}")
        expected_generation = {
            "model_id": adapter.model_id,
            "backend": adapter.backend,
            "prompt_sha256": adapter.prompt_sha256,
            "generation_config_sha256": adapter.generation_config_sha256,
        }
        mismatched = [
            key
            for key, expected in expected_generation.items()
            if artifact.get(key) != expected
        ]
        if mismatched:
            raise ValueError(
                "cached response belongs to another generation config "
                f"({', '.join(mismatched)}): {cache}"
            )
        response = dict(artifact.get("response") or {})
        if artifact.get("response_sha256") != sha256_json(response):
            raise ValueError(f"cached response SHA mismatch: {cache}")
        body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        if artifact.get("artifact_sha256") != sha256_json(body):
            raise ValueError(f"cached response artifact SHA mismatch: {cache}")
        return response, 0, 0.0
    started = time.monotonic()
    raw = dict(adapter.generate(payload))
    elapsed = round(time.monotonic() - started, 3)
    artifact = {
        "schema_version": "wang_canonical_viewpoint_batch_raw_response_v1",
        "model_id": adapter.model_id,
        "backend": adapter.backend,
        "prompt_sha256": adapter.prompt_sha256,
        "generation_config_sha256": adapter.generation_config_sha256,
        "request_payload_sha256": request_sha,
        "wall_seconds": elapsed,
        "response_sha256": sha256_json(raw),
        "response": raw,
    }
    artifact["artifact_sha256"] = sha256_json(artifact)
    write_immutable(cache, artifact)
    return raw, 1, elapsed
