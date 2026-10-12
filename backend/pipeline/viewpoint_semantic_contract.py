"""Scoped semantic instructions, never the complete CVP design document.

Historic prompts remain byte-for-byte available. Composition changes the
adapter prompt/config hash and hence cannot masquerade as a cached old review.
The independent auditor reads the same *text* with its own loader, not this code.
"""
from pathlib import Path

LEGACY_SEMANTICS = "legacy_v1"
CONTEXTUAL_SEMANTICS = "wang_cvp_contextual_semantics_v2"
CONTRACT_PATH = Path(__file__).resolve().parents[2] / "config" / "wang_cvp_contextual_semantics_v2.md"
COMPONENT_ROLES = "wang_cvp_component_roles_v1"
COMPONENT_PATH = CONTRACT_PATH.with_name(COMPONENT_ROLES + ".md")
RELATION_ROLES = "wang_cvp_relation_roles_v1"
RELATION_PATH = CONTRACT_PATH.with_name(RELATION_ROLES + ".md")


def relation_rules():
    text = RELATION_PATH.read_text(encoding="utf-8")
    if not text.startswith("## 连接作用规则：" + RELATION_ROLES + "\n"):
        raise ValueError("CVP relation role contract identity drift")
    return text


def component_rules():
    text = COMPONENT_PATH.read_text(encoding="utf-8")
    if not text.startswith("## 成分角色規則：" + COMPONENT_ROLES + "\n"):
        raise ValueError("CVP component role contract identity drift")
    return text


def semantic_prompt(role_prompt: str, version: str = CONTEXTUAL_SEMANTICS) -> str:
    if version == LEGACY_SEMANTICS:
        return role_prompt
    if version != CONTEXTUAL_SEMANTICS:
        raise ValueError("unknown CVP semantic contract version")
    rules = CONTRACT_PATH.read_text(encoding="utf-8")
    if not rules.startswith("## 统一语义规则：" + CONTEXTUAL_SEMANTICS + "\n"):
        raise ValueError("CVP semantic contract identity drift")
    return rules + "\n\n## 本阶段任务与输出约束\n\n" + role_prompt


def policy_prompt(policy, role, prompt_dir):
    prompt = semantic_prompt(
        (prompt_dir / policy["prompts"][role]).read_text(encoding="utf-8"),
        policy.get("semantic_contract_version", LEGACY_SEMANTICS),
    )
    from backend.pipeline.viewpoint_cvp_coverage_contract import enabled, PROMPT
    return prompt + "\n\n" + PROMPT if enabled(policy) else prompt
