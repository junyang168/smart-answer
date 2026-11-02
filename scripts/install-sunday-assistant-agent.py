#!/usr/bin/env python3
"""Prepare protected Sunday config and LaunchAgent; deploy.sh alone loads it.

Run with backend/.venv/bin/python. Does not enable collection or copy test data.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import secrets
import shutil

from dotenv import dotenv_values, set_key


def main():
    config = Path(os.getenv("SMART_ANSWER_CONFIG_ROOT", "/opt/homebrew/var/www/smart-answer-config"))
    deploy = Path(os.getenv("SMART_ANSWER_DEPLOY_ROOT", "/opt/homebrew/var/www/smart-answer-deploy"))
    agents = Path.home() / "Library/LaunchAgents"
    backend_plist = agents / "com.smart_answer.fullarticleservice.plist"
    backend = plistlib.loads(backend_plist.read_bytes())
    backend_env = backend.setdefault("EnvironmentVariables", {})
    data = Path(backend_env["DATA_BASE_DIR"])
    env_files = [config / relative for relative in (".env", "backend/.env", "web/.env.local")]
    if not all(p.is_file() for p in env_files):
        raise SystemExit("Production configuration files must already exist")
    assistant_env = config / "sunday-assistant.env"
    existing = dotenv_values(assistant_env) if assistant_env.exists() else {}
    secret = existing.get("SUNDAY_ASSISTANT_SECRET") or backend_env.get("SUNDAY_ASSISTANT_SECRET") or secrets.token_urlsafe(48)
    if len(secret) < 24:
        raise SystemExit("Existing secret is too short; reconcile it before installation")
    # Back up once, without ever printing secret values.
    for path in [*env_files, backend_plist]:
        backup = path.with_name(path.name + ".before-sunday-assistant")
        if not backup.exists():
            shutil.copy2(path, backup)
            backup.chmod(0o600)
    for path in env_files:
        set_key(str(path), "SUNDAY_ASSISTANT_SECRET", secret)
        path.chmod(0o600)
    backend_env["SUNDAY_ASSISTANT_SECRET"] = secret
    backend_plist.write_bytes(plistlib.dumps(backend))
    backend_plist.chmod(0o600)
    values = {
        "DATA_BASE_DIR": str(data),
        "FULL_ARTICLE_SERVICE_URL": "http://127.0.0.1:8555",
        "SUNDAY_ASSISTANT_SECRET": secret,
        "SUNDAY_WHATSAPP_STATE_DIR": str(data / "sunday-assistant/whatsapp"),
        "SUNDAY_WHATSAPP_PORT": "8766",
        "SUNDAY_WHATSAPP_BRIDGE_URL": "http://127.0.0.1:8766",
        "SUNDAY_WHATSAPP_AUTOCONNECT": "1",
        "SUNDAY_WHATSAPP_CHROME": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    }
    # Same model credentials as the backend, without putting them in the plist.
    values.update({k: v for k, v in backend_env.items() if k.startswith(("OPENAI_", "SUNDAY_ASSISTANT_MODEL"))})
    assistant_env.touch(mode=0o600, exist_ok=True)
    assistant_env.chmod(0o600)
    for key, value in values.items():
        set_key(str(assistant_env), key, str(value))
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node must be installed before preparing the LaunchAgent")
    release = Path((deploy / "active-release").read_text().strip())
    logs = Path.home() / "Library/Logs/smart-answer"
    logs.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": "com.smart_answer.sundayassistant",
        "ProgramArguments": [str(release / "backend/.venv/bin/python3"), str(release / "scripts/run-sunday-assistant.py"), "--config", str(assistant_env)],
        "WorkingDirectory": str(release),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "EnvironmentVariables": {"PATH": f"{Path(node).parent}:/opt/homebrew/bin:/usr/bin:/bin", "PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(logs / "sunday-assistant.log"),
        "StandardErrorPath": str(logs / "sunday-assistant.log"),
    }
    target = agents / "com.smart_answer.sundayassistant.plist"
    target.write_bytes(plistlib.dumps(plist))
    target.chmod(0o600)
    print("Protected shared configuration and Sunday LaunchAgent prepared; not loaded. Deploy using scripts/deploy.sh.")


if __name__ == "__main__":
    main()
