"""Versioned executable policy for asynchronous ArgumentRoute resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from backend.api.canonical_repository.viewpoint_foundation import sha256_json

ROUTE_POLICY_VERSION = "wang_route_resolution_policy_v1"
DEFAULT_ROUTE_POLICY_PATH = (
    Path(__file__).resolve().parent
    / "policies"
    / "wang_route_resolution_policy_v1.json"
)


def load_route_policy(path: Path) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != ROUTE_POLICY_VERSION:
        raise ValueError(f"{path} is not a {ROUTE_POLICY_VERSION}")
    required_top = {
        "schema_version",
        "policy_id",
        "proposal",
        "review",
        "correction",
        "prompts",
        "validator_version",
        "call_timeout_seconds",
    }
    if set(policy) != required_top:
        raise ValueError(
            "Route policy fields differ: "
            f"missing={sorted(required_top - set(policy))}, "
            f"extra={sorted(set(policy) - required_top)}"
        )
    for role in ("proposal", "correction"):
        if set(policy[role]) != {"provider", "model", "effort"}:
            raise ValueError(f"Route policy {role} fields are invalid")
    if set(policy["review"]) != {
        "provider",
        "model",
        "effort",
        "targets_per_batch",
    }:
        raise ValueError("Route policy review fields are invalid")
    if set(policy["prompts"]) != {"proposal", "review", "correction"}:
        raise ValueError("Route policy prompt roles are invalid")
    if policy["proposal"]["provider"] != "codex" or policy["correction"]["provider"] != "codex":
        raise ValueError("Route proposal/correction policy must use the Codex subscription")
    if policy["review"]["provider"] != "claude":
        raise ValueError("Route review policy must use the Claude subscription")
    if int(policy["review"]["targets_per_batch"]) < 1:
        raise ValueError("Route review batch size must be positive")
    if int(policy["call_timeout_seconds"]) < 1:
        raise ValueError("Route call timeout must be positive")
    return policy


def route_policy_fingerprint(
    policy: Mapping[str, Any], *, prompt_sha256s: Mapping[str, str]
) -> str:
    """Bind executable policy and the exact prompt content it names."""

    return sha256_json(
        {
            "policy": dict(policy),
            "prompt_sha256s": dict(sorted(prompt_sha256s.items())),
        }
    )


def route_policy_prompt_sha256s(
    policy: Mapping[str, Any], *, prompt_dir: Path
) -> dict[str, str]:
    return {
        role: sha256_json((prompt_dir / str(filename)).read_text(encoding="utf-8"))
        for role, filename in sorted(dict(policy["prompts"]).items())
    }
