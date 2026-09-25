from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _run(
    *args: str | Path,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=env,
        check=check,
        text=True,
        capture_output=True,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run("git", *args, cwd=repo)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture()
def deploy_fixture(tmp_path: Path) -> dict[str, object]:
    remote = tmp_path / "origin.git"
    source = tmp_path / "source"
    deploy_root = tmp_path / "deploy"
    runtime_data = tmp_path / "runtime-data"
    legacy = tmp_path / "legacy"
    bin_dir = tmp_path / "bin"
    logs = tmp_path / "logs"
    backend_plist = tmp_path / "backend.plist"
    reminder_plist = tmp_path / "reminder.plist"
    inbox_plist = tmp_path / "inbox.plist"

    for path in (source, deploy_root / "releases", runtime_data / "config", legacy, bin_dir, logs):
        path.mkdir(parents=True)

    # The web's own configuration: every release links web/.env.local here.
    web_data = tmp_path / "web-data"
    (web_data / "fellowship" / "docs" / "2026-09-25").mkdir(parents=True)
    (web_data / "fellowship" / "docs" / "2026-09-25" / "study.pptx").write_bytes(b"deck")
    (legacy / "web").mkdir()
    web_env = legacy / "web" / ".env.local"
    web_env.write_text(f"NEXTAUTH_URL=https://example.test\nDATA_BASE_DIR={web_data}\n", encoding="utf-8")

    _run("git", "init", "--bare", "--initial-branch=main", remote, cwd=tmp_path)
    _run("git", "init", "--initial-branch=main", source, cwd=tmp_path)
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test User")

    python_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    (source / ".python-version").write_text(f"{python_minor}\n", encoding="utf-8")
    scripts = source / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts" / "deploy.sh", scripts / "deploy.sh")
    (scripts / "pm2.production.config.cjs").write_text("module.exports = {};\n", encoding="utf-8")
    _write_executable(scripts / "prune-releases.sh", "#!/usr/bin/env bash\nexit 0\n")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "push", "-u", "origin", "main")
    target_sha = _git(source, "rev-parse", "HEAD").stdout.strip()
    previous_sha = "a" * 40

    def make_release(sha: str, *, with_inbox: bool = True) -> Path:
        release = deploy_root / "releases" / sha
        (release / "backend" / ".venv" / "bin").mkdir(parents=True)
        (release / "web" / ".next").mkdir(parents=True)
        _write_executable(
            release / "backend" / ".venv" / "bin" / "python3",
            "#!/usr/bin/env bash\nexit 99\n",
        )
        (release / "backend" / "fellowship_reminder_job.py").write_text(
            "raise AssertionError('deployment must not run the reminder job')\n",
            encoding="utf-8",
        )
        if with_inbox:
            (release / "backend" / "reference_commentary_inbox_job.py").write_text(
                "raise AssertionError('deployment must not run the inbox job')\n",
                encoding="utf-8",
            )
        (release / ".deploy-complete").touch()
        return release

    target_release = make_release(target_sha)
    previous_release = make_release(previous_sha)
    (deploy_root / "active-release").write_text(f"{previous_release}\n", encoding="utf-8")
    (deploy_root / "deployments.log").write_text(
        f"2026-09-01T00:00:00Z {previous_sha} previous=none\n",
        encoding="utf-8",
    )
    (runtime_data / "config" / "config.json").write_text("{}\n", encoding="utf-8")

    backend_data = {
        "Label": "test.smart-answer.backend",
        "ProgramArguments": ["/old/python", "-m", "backend.api.main:app"],
        "WorkingDirectory": "/old/release",
    }
    reminder_data = {
        "Label": "com.smartanswer.fellowshipreminder",
        "ProgramArguments": ["/old/python", "/old/fellowship_reminder_job.py"],
        "WorkingDirectory": "/old/backend",
        "StartCalendarInterval": {"Hour": 10, "Minute": 0},
        "EnvironmentVariables": {"SMTP_PASSWORD": "preserve-this-secret"},
    }
    with backend_plist.open("wb") as handle:
        plistlib.dump(backend_data, handle)
    with reminder_plist.open("wb") as handle:
        plistlib.dump(reminder_data, handle)
    inbox_data = {
        "Label": "com.smart_answer.referencecommentaryinbox",
        "ProgramArguments": ["/old/python", "/old/reference_commentary_inbox_job.py"],
        "WorkingDirectory": "/old/release",
        "StartInterval": 180,
    }
    with inbox_plist.open("wb") as handle:
        plistlib.dump(inbox_data, handle)

    _write_executable(
        bin_dir / "plist-buddy",
        content="""#!/usr/bin/env python3
import plistlib
import sys
from pathlib import Path

if len(sys.argv) != 4 or sys.argv[1] != "-c":
    raise SystemExit(64)
command = sys.argv[2]
path = Path(sys.argv[3])
with path.open("rb") as handle:
    data = plistlib.load(handle)
verb, remainder = command.split(" ", 1)
parts = remainder.split(" ", 1)
keys = parts[0].strip(":").split(":")
current = data
for key in keys[:-1]:
    current = current[int(key)] if isinstance(current, list) else current[key]
last = keys[-1]
if verb == "Print":
    value = current[int(last)] if isinstance(current, list) else current[last]
    print(value)
elif verb == "Set" and len(parts) == 2:
    if isinstance(current, list):
        current[int(last)] = parts[1]
    else:
        current[last] = parts[1]
    with path.open("wb") as handle:
        plistlib.dump(data, handle)
else:
    raise SystemExit(64)
""",
    )
    _write_executable(
        bin_dir / "npm",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *' audit '* ]]; then\n"
        "  printf '%s\\n' '{\"metadata\":{\"vulnerabilities\":{\"critical\":0}}}'\n"
        "fi\n",
    )
    _write_executable(
        bin_dir / "pm2",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf 'pm2 %s root=%s data=%s\\n' \"$*\" \"${SMART_ANSWER_WEB_ROOT:-}\" \"${DATA_BASE_DIR:-}\" >> \"$TEST_DEPLOY_EVENT_LOG\"\n"
        "if [[ \"${1:-}\" == 'start' ]]; then\n"
        "  printf '%s\\n' \"$SMART_ANSWER_WEB_ROOT\" > \"$TEST_PM2_WEB_ROOT\"\n"
        "fi\n"
        "if [[ \"${1:-}\" == 'save' && -n \"${TEST_FAIL_PM2_SAVE_ONCE:-}\" && ! -e \"$TEST_PM2_SAVE_FAIL_MARKER\" ]]; then\n"
        "  touch \"$TEST_PM2_SAVE_FAIL_MARKER\"\n"
        "  exit 1\n"
        "fi\n",
    )
    _write_executable(
        bin_dir / "curl",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "url=''\n"
        "for argument in \"$@\"; do url=\"$argument\"; done\n"
        "if [[ \"$url\" == \"$SMART_ANSWER_FRONTEND_HEALTH\" ]]; then\n"
        "  web_root=''\n"
        "  [[ ! -f \"$TEST_PM2_WEB_ROOT\" ]] || web_root=$(<\"$TEST_PM2_WEB_ROOT\")\n"
        "  printf 'frontend-health root=%s\\n' \"$web_root\" >> \"$TEST_DEPLOY_EVENT_LOG\"\n"
        "  if [[ -n \"${TEST_FAIL_TARGET_FRONTEND:-}\" && \"$web_root\" == *\"/$TEST_TARGET_SHA/web\" ]]; then\n"
        "    exit 1\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$url\" == */api/fellowship-documents/* ]]; then\n"
        "  printf 'fellowship-document %s\\n' \"$url\" >> \"$TEST_DEPLOY_EVENT_LOG\"\n"
        "  web_root=''\n"
        "  [[ ! -f \"$TEST_PM2_WEB_ROOT\" ]] || web_root=$(<\"$TEST_PM2_WEB_ROOT\")\n"
        "  if [[ -n \"${TEST_FAIL_FELLOWSHIP_DOCUMENT:-}\" && \"$web_root\" == *\"/$TEST_TARGET_SHA/web\" ]]; then exit 22; fi\n"
        "  exit 0\n"
        "fi\n"
        "backend_program=$(\"$SMART_ANSWER_PLIST_BUDDY\" -c 'Print :ProgramArguments:0' \"$TEST_BACKEND_PLIST\")\n"
        "release=${backend_program%/backend/.venv/bin/python3}\n"
        "printf '{\"status\":\"ok\",\"release\":\"%s\"}\\n' \"${release##*/}\"\n",
    )
    _write_executable(bin_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        bin_dir / "launchctl",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$TEST_LAUNCHCTL_LOG\"\n"
        "case \"${1:-}\" in\n"
        "  unload) exit 0 ;;\n"
        "  load)\n"
        "    if [[ \"${2:-}\" == \"$TEST_REMINDER_PLIST\" && -n \"${TEST_FAIL_TARGET_REMINDER_LOAD:-}\" ]]; then\n"
        "      program=$(\"$SMART_ANSWER_PLIST_BUDDY\" -c 'Print :ProgramArguments:0' \"$TEST_REMINDER_PLIST\")\n"
        "      if [[ \"$program\" == *\"/$TEST_TARGET_SHA/\"* && ! -e \"$TEST_FAIL_MARKER\" ]]; then\n"
        "        touch \"$TEST_FAIL_MARKER\"\n"
        "        exit 1\n"
        "      fi\n"
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        "  print)\n"
        "    plist=\"$TEST_REMINDER_PLIST\"\n"
        "    [[ \"${2:-}\" != *referencecommentaryinbox ]] || plist=\"$TEST_INBOX_PLIST\"\n"
        "    program=$(\"$SMART_ANSWER_PLIST_BUDDY\" -c 'Print :ProgramArguments:0' \"$plist\")\n"
        "    script=$(\"$SMART_ANSWER_PLIST_BUDDY\" -c 'Print :ProgramArguments:1' \"$plist\")\n"
        "    workdir=$(\"$SMART_ANSWER_PLIST_BUDDY\" -c 'Print :WorkingDirectory' \"$plist\")\n"
        "    printf 'program = %s\\narguments = {\\n%s\\n}\\nworking directory = %s\\n' \"$program\" \"$script\" \"$workdir\"\n"
        "    ;;\n"
        "esac\n",
    )

    env = {
        **{k: v for k, v in os.environ.items() if k != "DATA_BASE_DIR"},
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SMART_ANSWER_DEPLOY_ROOT": str(deploy_root),
        "SMART_ANSWER_LEGACY_ROOT": str(legacy),
        "SMART_ANSWER_WEB_DATA_DIR": str(runtime_data),
        "SMART_ANSWER_BACKEND_PLIST": str(backend_plist),
        "SMART_ANSWER_FELLOWSHIP_REMINDER_PLIST": str(reminder_plist),
        "SMART_ANSWER_REFERENCE_INBOX_PLIST": str(inbox_plist),
        "SMART_ANSWER_PLIST_BUDDY": str(bin_dir / "plist-buddy"),
        "SMART_ANSWER_MIN_FREE_MB": "1",
        "SMART_ANSWER_BACKEND_HEALTH": "http://backend.test/healthz",
        "SMART_ANSWER_FRONTEND_HEALTH": "http://frontend.test/",
        "TEST_BACKEND_PLIST": str(backend_plist),
        "TEST_REMINDER_PLIST": str(reminder_plist),
        "TEST_INBOX_PLIST": str(inbox_plist),
        "TEST_LAUNCHCTL_LOG": str(logs / "launchctl.log"),
        "TEST_DEPLOY_EVENT_LOG": str(logs / "deploy-events.log"),
        "TEST_PM2_WEB_ROOT": str(logs / "pm2-web-root"),
        "TEST_PM2_SAVE_FAIL_MARKER": str(logs / "pm2-save-failed"),
        "TEST_TARGET_SHA": target_sha,
        "TEST_FAIL_MARKER": str(logs / "failed-reminder-load"),
    }
    return {
        "source": source,
        "deploy": scripts / "deploy.sh",
        "deploy_root": deploy_root,
        "backend_plist": backend_plist,
        "reminder_plist": reminder_plist,
        "inbox_plist": inbox_plist,
        "make_release": make_release,
        "launchctl_log": logs / "launchctl.log",
        "deploy_event_log": logs / "deploy-events.log",
        "pm2_web_root": logs / "pm2-web-root",
        "target_sha": target_sha,
        "target_release": target_release,
        "previous_release": previous_release,
        "env": env,
        "web_env": web_env,
        "web_data": web_data,
    }


def test_deploy_binds_loaded_fellowship_reminder_to_new_release(
    deploy_fixture: dict[str, object],
) -> None:
    source = deploy_fixture["source"]
    deploy = deploy_fixture["deploy"]
    env = deploy_fixture["env"]
    assert isinstance(source, Path)
    assert isinstance(deploy, Path)
    assert isinstance(env, dict)

    result = _run("bash", deploy, cwd=source, env=env)

    reminder_plist = deploy_fixture["reminder_plist"]
    target_release = deploy_fixture["target_release"]
    deploy_root = deploy_fixture["deploy_root"]
    launchctl_log = deploy_fixture["launchctl_log"]
    deploy_event_log = deploy_fixture["deploy_event_log"]
    assert isinstance(reminder_plist, Path)
    assert isinstance(target_release, Path)
    assert isinstance(deploy_root, Path)
    assert isinstance(launchctl_log, Path)
    assert isinstance(deploy_event_log, Path)

    with reminder_plist.open("rb") as handle:
        reminder = plistlib.load(handle)
    assert reminder["ProgramArguments"] == [
        str(target_release / "backend" / ".venv" / "bin" / "python3"),
        str(target_release / "backend" / "fellowship_reminder_job.py"),
    ]
    assert reminder["WorkingDirectory"] == str(target_release)
    assert reminder["StartCalendarInterval"] == {"Hour": 10, "Minute": 0}
    assert reminder["EnvironmentVariables"]["SMTP_PASSWORD"] == "preserve-this-secret"
    assert stat.S_IMODE(reminder_plist.stat().st_mode) == 0o600
    assert (deploy_root / "active-release").read_text(encoding="utf-8").strip() == str(target_release)
    launchctl_calls = launchctl_log.read_text(encoding="utf-8")
    assert f"load {reminder_plist}" in launchctl_calls
    assert "kickstart" not in launchctl_calls
    assert "Fellowship reminder is bound to" in result.stdout
    events = deploy_event_log.read_text(encoding="utf-8").splitlines()
    frontend_start = next(
        index
        for index, event in enumerate(events)
        if event.startswith("pm2 start ") and f"root={target_release / 'web'}" in event
    )
    frontend_health = next(
        index
        for index, event in enumerate(events)
        if event == f"frontend-health root={target_release / 'web'}"
    )
    pm2_save = next(index for index, event in enumerate(events) if event.startswith("pm2 save"))
    assert frontend_start < frontend_health < pm2_save
    assert sum(event.startswith("pm2 save") for event in events) == 1


def test_unhealthy_frontend_is_not_saved_and_rollback_is_persisted(
    deploy_fixture: dict[str, object],
) -> None:
    source = deploy_fixture["source"]
    deploy = deploy_fixture["deploy"]
    env = deploy_fixture["env"]
    previous_release = deploy_fixture["previous_release"]
    deploy_root = deploy_fixture["deploy_root"]
    deploy_event_log = deploy_fixture["deploy_event_log"]
    assert isinstance(source, Path)
    assert isinstance(deploy, Path)
    assert isinstance(env, dict)
    assert isinstance(previous_release, Path)
    assert isinstance(deploy_root, Path)
    assert isinstance(deploy_event_log, Path)

    result = _run(
        "bash",
        deploy,
        cwd=source,
        env={**env, "TEST_FAIL_TARGET_FRONTEND": "1"},
        check=False,
    )

    assert result.returncode == 1
    assert (deploy_root / "active-release").read_text(encoding="utf-8").strip() == str(
        previous_release
    )
    events = deploy_event_log.read_text(encoding="utf-8").splitlines()
    rollback_start = next(
        index
        for index, event in enumerate(events)
        if event.startswith("pm2 start ") and f"root={previous_release / 'web'}" in event
    )
    rollback_health = next(
        index
        for index, event in enumerate(events)
        if event == f"frontend-health root={previous_release / 'web'}"
    )
    save_indexes = [index for index, event in enumerate(events) if event.startswith("pm2 save")]
    assert len(save_indexes) == 1
    assert rollback_start < rollback_health < save_indexes[0]
    assert not any(event.startswith("pm2 save") for event in events[:rollback_start])


def test_pm2_save_failure_rolls_back_and_persists_previous_release(
    deploy_fixture: dict[str, object],
) -> None:
    source = deploy_fixture["source"]
    deploy = deploy_fixture["deploy"]
    env = deploy_fixture["env"]
    previous_release = deploy_fixture["previous_release"]
    deploy_root = deploy_fixture["deploy_root"]
    deploy_event_log = deploy_fixture["deploy_event_log"]
    assert isinstance(source, Path)
    assert isinstance(deploy, Path)
    assert isinstance(env, dict)
    assert isinstance(previous_release, Path)
    assert isinstance(deploy_root, Path)
    assert isinstance(deploy_event_log, Path)

    result = _run(
        "bash",
        deploy,
        cwd=source,
        env={**env, "TEST_FAIL_PM2_SAVE_ONCE": "1"},
        check=False,
    )

    assert result.returncode == 1
    assert "failed to save PM2 process list" in result.stderr
    assert (deploy_root / "active-release").read_text(encoding="utf-8").strip() == str(
        previous_release
    )
    events = deploy_event_log.read_text(encoding="utf-8").splitlines()
    save_indexes = [index for index, event in enumerate(events) if event.startswith("pm2 save")]
    assert len(save_indexes) == 2
    rollback_start = next(
        index
        for index, event in enumerate(events)
        if event.startswith("pm2 start ") and f"root={previous_release / 'web'}" in event
    )
    rollback_health = next(
        index
        for index, event in enumerate(events)
        if event == f"frontend-health root={previous_release / 'web'}"
    )
    assert save_indexes[0] < rollback_start < rollback_health < save_indexes[1]


def test_already_active_release_refreshes_pm2_state_without_restart(
    deploy_fixture: dict[str, object],
) -> None:
    source = deploy_fixture["source"]
    deploy = deploy_fixture["deploy"]
    env = deploy_fixture["env"]
    deploy_root = deploy_fixture["deploy_root"]
    target_release = deploy_fixture["target_release"]
    deploy_event_log = deploy_fixture["deploy_event_log"]
    assert isinstance(source, Path)
    assert isinstance(deploy, Path)
    assert isinstance(env, dict)
    assert isinstance(deploy_root, Path)
    assert isinstance(target_release, Path)
    assert isinstance(deploy_event_log, Path)
    (deploy_root / "active-release").write_text(f"{target_release}\n", encoding="utf-8")

    result = _run("bash", deploy, cwd=source, env=env)

    events = deploy_event_log.read_text(encoding="utf-8").splitlines()
    assert not any(event.startswith(("pm2 delete", "pm2 start")) for event in events)
    assert events[-1].startswith("pm2 save")
    assert "PM2 resurrection state saved" in result.stdout


def test_already_active_unhealthy_frontend_is_not_saved(
    deploy_fixture: dict[str, object],
) -> None:
    source = deploy_fixture["source"]
    deploy = deploy_fixture["deploy"]
    env = deploy_fixture["env"]
    deploy_root = deploy_fixture["deploy_root"]
    target_release = deploy_fixture["target_release"]
    deploy_event_log = deploy_fixture["deploy_event_log"]
    pm2_web_root = deploy_fixture["pm2_web_root"]
    assert isinstance(source, Path)
    assert isinstance(deploy, Path)
    assert isinstance(env, dict)
    assert isinstance(deploy_root, Path)
    assert isinstance(target_release, Path)
    assert isinstance(deploy_event_log, Path)
    assert isinstance(pm2_web_root, Path)
    (deploy_root / "active-release").write_text(f"{target_release}\n", encoding="utf-8")
    pm2_web_root.write_text(f"{target_release / 'web'}\n", encoding="utf-8")

    result = _run(
        "bash",
        deploy,
        cwd=source,
        env={**env, "TEST_FAIL_TARGET_FRONTEND": "1"},
        check=False,
    )

    assert result.returncode == 1
    events = deploy_event_log.read_text(encoding="utf-8").splitlines()
    assert not any(event.startswith("pm2 save") for event in events)


def test_reminder_activation_failure_rolls_back_its_release_binding(
    deploy_fixture: dict[str, object],
) -> None:
    source = deploy_fixture["source"]
    deploy = deploy_fixture["deploy"]
    env = deploy_fixture["env"]
    assert isinstance(source, Path)
    assert isinstance(deploy, Path)
    assert isinstance(env, dict)
    env = {**env, "TEST_FAIL_TARGET_REMINDER_LOAD": "1"}

    result = _run("bash", deploy, cwd=source, env=env, check=False)

    reminder_plist = deploy_fixture["reminder_plist"]
    previous_release = deploy_fixture["previous_release"]
    deploy_root = deploy_fixture["deploy_root"]
    assert isinstance(reminder_plist, Path)
    assert isinstance(previous_release, Path)
    assert isinstance(deploy_root, Path)

    with reminder_plist.open("rb") as handle:
        reminder = plistlib.load(handle)
    assert result.returncode == 1
    assert reminder["ProgramArguments"][0] == str(
        previous_release / "backend" / ".venv" / "bin" / "python3"
    )
    assert reminder["ProgramArguments"][1] == str(
        previous_release / "backend" / "fellowship_reminder_job.py"
    )
    assert reminder["WorkingDirectory"] == str(previous_release)
    assert (deploy_root / "active-release").read_text(encoding="utf-8").strip() == str(previous_release)
    assert "Rollback complete" in result.stdout


def test_deploy_fails_closed_when_reminder_launchagent_is_missing(
    deploy_fixture: dict[str, object],
) -> None:
    source = deploy_fixture["source"]
    deploy = deploy_fixture["deploy"]
    env = deploy_fixture["env"]
    reminder_plist = deploy_fixture["reminder_plist"]
    assert isinstance(source, Path)
    assert isinstance(deploy, Path)
    assert isinstance(env, dict)
    assert isinstance(reminder_plist, Path)
    reminder_plist.unlink()

    result = _run("bash", deploy, "--dry-run", cwd=source, env=env, check=False)

    assert result.returncode == 1
    assert "fellowship reminder LaunchAgent not found" in result.stderr


def test_deploy_binds_reference_inbox_to_new_release(deploy_fixture: dict[str, object]) -> None:
    env = deploy_fixture["env"]
    result = _run("bash", deploy_fixture["deploy"], cwd=deploy_fixture["source"], env=env)

    target_release = deploy_fixture["target_release"]
    with deploy_fixture["inbox_plist"].open("rb") as handle:
        inbox = plistlib.load(handle)
    assert inbox["ProgramArguments"] == [
        str(target_release / "backend" / ".venv" / "bin" / "python3"),
        str(target_release / "backend" / "reference_commentary_inbox_job.py"),
    ]
    assert inbox["WorkingDirectory"] == str(target_release)
    assert inbox["StartInterval"] == 180
    assert f"load {deploy_fixture['inbox_plist']}" in deploy_fixture["launchctl_log"].read_text(encoding="utf-8")
    assert "Reference commentary inbox is bound to" in result.stdout


def test_rollback_to_release_without_inbox_stops_the_agent(deploy_fixture: dict[str, object]) -> None:
    previous_release = deploy_fixture["previous_release"]
    (previous_release / "backend" / "reference_commentary_inbox_job.py").unlink()
    env = {**deploy_fixture["env"], "TEST_FAIL_TARGET_REMINDER_LOAD": "1"}

    result = _run("bash", deploy_fixture["deploy"], cwd=deploy_fixture["source"], env=env, check=False)

    assert result.returncode == 1
    assert "Rollback complete" in result.stdout
    assert "Reference commentary inbox is not in" in result.stdout
    calls = deploy_fixture["launchctl_log"].read_text(encoding="utf-8").splitlines()
    assert calls[-1] == f"unload {deploy_fixture['inbox_plist']}"


def test_deploy_fails_closed_when_inbox_launchagent_is_missing(deploy_fixture: dict[str, object]) -> None:
    deploy_fixture["inbox_plist"].unlink()

    result = _run(
        "bash", deploy_fixture["deploy"], "--dry-run", cwd=deploy_fixture["source"], env=deploy_fixture["env"], check=False
    )

    assert result.returncode == 1
    assert "reference commentary inbox LaunchAgent not found" in result.stderr
    assert "install-reference-inbox-agent.sh" in result.stderr


def test_web_gets_data_base_dir_from_its_config_not_the_deploying_shell(deploy_fixture: dict[str, object]) -> None:
    result = _run("bash", deploy_fixture["deploy"], cwd=deploy_fixture["source"], env=deploy_fixture["env"])

    events = deploy_fixture["deploy_event_log"].read_text(encoding="utf-8").splitlines()
    start = next(e for e in events if e.startswith("pm2 start "))
    assert start.endswith(f"data={deploy_fixture['web_data']}")
    assert any(e.startswith("fellowship-document ") and e.endswith("/api/fellowship-documents/2026-09-25/study.pptx") for e in events)
    assert "Web serves fellowship documents (2026-09-25/study.pptx)" in result.stdout


def test_deploy_fails_closed_when_web_config_lacks_data_base_dir(deploy_fixture: dict[str, object]) -> None:
    deploy_fixture["web_env"].write_text("NEXTAUTH_URL=https://example.test\n", encoding="utf-8")

    result = _run("bash", deploy_fixture["deploy"], "--dry-run", cwd=deploy_fixture["source"], env=deploy_fixture["env"], check=False)

    assert result.returncode == 1
    assert "DATA_BASE_DIR is not set in" in result.stderr


def test_deploy_fails_closed_when_web_data_dir_is_missing(deploy_fixture: dict[str, object]) -> None:
    deploy_fixture["web_env"].write_text("DATA_BASE_DIR=/nonexistent/web-data\n", encoding="utf-8")

    result = _run("bash", deploy_fixture["deploy"], "--dry-run", cwd=deploy_fixture["source"], env=deploy_fixture["env"], check=False)

    assert result.returncode == 1
    assert "is not a directory: /nonexistent/web-data" in result.stderr


def test_unservable_fellowship_document_rolls_back(deploy_fixture: dict[str, object]) -> None:
    env = {**deploy_fixture["env"], "TEST_FAIL_FELLOWSHIP_DOCUMENT": "1"}

    result = _run("bash", deploy_fixture["deploy"], cwd=deploy_fixture["source"], env=env, check=False)

    assert result.returncode == 1
    assert "web cannot serve fellowship document 2026-09-25/study.pptx" in result.stderr
    assert "Rollback complete" in result.stdout
    assert (deploy_fixture["deploy_root"] / "active-release").read_text(encoding="utf-8").strip() == str(deploy_fixture["previous_release"])
