"""Versioned executable policy for production CanonicalViewpoint resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    VALIDATION_VERSION,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


CVP_POLICY_VERSION = "wang_cvp_resolution_policy_v3"
SUPPORTED_CVP_POLICY_VERSIONS = frozenset(
    {"wang_cvp_resolution_policy_v1", "wang_cvp_resolution_policy_v2", CVP_POLICY_VERSION,
     "wang_cvp_resolution_policy_v4"}
)
DEFAULT_CVP_POLICY_PATH = (
    Path(__file__).resolve().parent
    / "policies"
    / "wang_cvp_resolution_policy_v4.json"
)


def load_cvp_policy(path: Path) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema_version") not in SUPPORTED_CVP_POLICY_VERSIONS:
        raise ValueError(
            f"{path} is not a supported CVP policy version: "
            f"{sorted(SUPPORTED_CVP_POLICY_VERSIONS)}"
        )
    required = {
        "schema_version",
        "policy_id",
        "grouping",
        "proposal",
        "review",
        "correction",
        "consolidation",
        "prompts",
        "validator_version",
        "batch_size",
        "max_request_bytes",
        "call_timeout_seconds",
    }
    if policy["schema_version"] in {CVP_POLICY_VERSION, "wang_cvp_resolution_policy_v4"}:
        from backend.pipeline.viewpoint_semantic_contract import CONTEXTUAL_SEMANTICS
        required.add("semantic_contract_version")
        if policy.get("semantic_contract_version") != CONTEXTUAL_SEMANTICS:
            raise ValueError("CVP v3 requires the contextual semantic contract")
    if policy["schema_version"] == "wang_cvp_resolution_policy_v4":
        from backend.pipeline.viewpoint_cvp_coverage_contract import VERSION
        required.add("coverage_contract_version")
        if policy.get("coverage_contract_version") != VERSION:
            raise ValueError("CVP v4 requires mandatory evidence-sufficient Claim coverage")
    if set(policy) != required:
        raise ValueError(
            "CVP policy fields differ: "
            f"missing={sorted(required - set(policy))}, "
            f"extra={sorted(set(policy) - required)}"
        )
    if policy["validator_version"] != VALIDATION_VERSION:
        raise ValueError("CVP policy validator_version must be " + VALIDATION_VERSION)
    for role in ("grouping", "proposal", "review", "correction", "consolidation"):
        if set(policy[role]) != {"provider", "model", "effort"}:
            raise ValueError(f"CVP policy {role} fields are invalid")
    if policy["grouping"]["provider"] != "claude":
        raise ValueError("CVP grouping policy must use the Claude subscription")
    if policy["proposal"]["provider"] != "codex" or policy["correction"]["provider"] != "codex":
        raise ValueError("CVP proposal/correction policy must use the Codex subscription")
    if policy["review"]["provider"] != "claude" or policy["consolidation"]["provider"] != "claude":
        raise ValueError("CVP review/consolidation policy must use the Claude subscription")
    expected_prompts = {"grouping", "proposal", "review", "correction", "consolidation"}
    if set(policy["prompts"]) != expected_prompts:
        raise ValueError("CVP policy prompt roles are invalid")
    if int(policy["batch_size"]) < 1:
        raise ValueError("CVP policy batch size must be positive")
    if int(policy["max_request_bytes"]) < 1:
        raise ValueError("CVP policy request byte ceiling must be positive")
    if int(policy["call_timeout_seconds"]) < 1:
        raise ValueError("CVP policy call timeout must be positive")
    return policy


def cvp_policy_prompt_sha256s(
    policy: Mapping[str, Any], *, prompt_dir: Path
) -> dict[str, str]:
    from backend.pipeline.viewpoint_semantic_contract import policy_prompt
    return {role: sha256_json(policy_prompt(policy, role, prompt_dir))
            for role in sorted(policy["prompts"])}


def cvp_policy_fingerprint(
    policy: Mapping[str, Any], *, prompt_sha256s: Mapping[str, str]
) -> str:
    return sha256_json(
        {
            "policy": dict(policy),
            "prompt_sha256s": dict(sorted(prompt_sha256s.items())),
        }
    )
