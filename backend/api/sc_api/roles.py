"""Shared role semantics. Beta testing grants editor capabilities, not admin ones."""
from pathlib import Path
import json

BETA_TESTER_ROLE = "beta_tester"


def user_roles(config, actor):
    identity = actor.strip().lower()
    if not any(u["id"].strip().lower() == identity for u in config.get("users", [])):
        return set()
    return {
        r["role"] for r in config.get("user_roles", [])
        if r["user"].strip().lower() == identity
    } or {"reader"}


def role_permissions(config, actor):
    roles = {"editor" if r == BETA_TESTER_ROLE else r for r in user_roles(config, actor)}
    return {r["permission"] for r in config.get("role_permissions", []) if r["role"] in roles}


def current_beta_access(root: Path, actor: str) -> bool:
    # Read the authoritative file on each request, including media and history.
    config = json.loads((Path(root) / "config/config.json").read_text())
    return BETA_TESTER_ROLE in user_roles(config, actor)
