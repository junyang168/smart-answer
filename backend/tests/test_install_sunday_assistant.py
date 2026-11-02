from pathlib import Path
import os
import plistlib
import subprocess
import sys

from dotenv import dotenv_values


def test_production_setup_keeps_one_secret_outside_release_and_does_not_start(tmp_path):
    config = tmp_path / "config"
    deploy = tmp_path / "deploy"
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    deploy.mkdir()
    (deploy / "active-release").write_text("/immutable/current-release\n")
    for relative in (".env", "backend/.env", "web/.env.local"):
        path = config / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("EXISTING_SETTING=preserved\n")
    backend = agents / "com.smart_answer.fullarticleservice.plist"
    backend.write_bytes(plistlib.dumps({"EnvironmentVariables": {"DATA_BASE_DIR": str(tmp_path / "data"), "OPENAI_API_KEY": "test-private-credential"}}))
    script = Path(__file__).resolve().parents[2] / "scripts/install-sunday-assistant-agent.py"
    env = {**os.environ, "HOME": str(home), "SMART_ANSWER_CONFIG_ROOT": str(config), "SMART_ANSWER_DEPLOY_ROOT": str(deploy)}
    first = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=True)
    settings = dotenv_values(config / "sunday-assistant.env")
    secret = settings["SUNDAY_ASSISTANT_SECRET"]
    subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=True)
    assert secret and len(secret) >= 24
    assert dotenv_values(config / "sunday-assistant.env")["SUNDAY_ASSISTANT_SECRET"] == secret
    for relative in (".env", "backend/.env", "web/.env.local"):
        values = dotenv_values(config / relative)
        assert values["SUNDAY_ASSISTANT_SECRET"] == secret
        assert values["EXISTING_SETTING"] == "preserved"
    plist = plistlib.loads((agents / "com.smart_answer.sundayassistant.plist").read_bytes())
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    assert plist["ProgramArguments"][2:] == ["--config", str(config / "sunday-assistant.env")]
    assert "test-private-credential" not in str(plist)
    assert settings["OPENAI_API_KEY"] == "test-private-credential"
    assert not (tmp_path / "data").exists()
    assert "not loaded" in first.stdout
    assert secret not in first.stdout
    assert (config / "sunday-assistant.env").stat().st_mode & 0o777 == 0o600
