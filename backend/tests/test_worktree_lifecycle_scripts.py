from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RECONCILE = ROOT / "scripts" / "reconcile-closed-worktrees.sh"
CLOSE_TICKET = ROOT / "scripts" / "close-ticket.sh"
DEV_SCRIPT = ROOT / "scripts" / "dev.sh"


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


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run("git", *args, cwd=repo, check=check)


@pytest.fixture()
def lifecycle_repo(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    remote = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    issue_dir = tmp_path / "issues"
    bin_dir = tmp_path / "bin"
    dev_log = tmp_path / "dev.log"
    issue_dir.mkdir()
    bin_dir.mkdir()

    _run("git", "init", "--bare", "--initial-branch=main", remote, cwd=tmp_path)
    _run("git", "init", "--initial-branch=main", repo, cwd=tmp_path)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    scripts = repo / "scripts"
    scripts.mkdir()
    dev = scripts / "dev.sh"
    dev.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$DEV_LOG\"\n",
        encoding="utf-8",
    )
    dev.chmod(0o755)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")

    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "number=${3:?issue number missing}\n"
        "case \"$1:$2\" in\n"
        "  issue:view) cat \"$ISSUE_DIR/$number\" ;;\n"
        "  issue:close) printf 'CLOSED\\n' > \"$ISSUE_DIR/$number\" ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    env = {
        **os.environ,
        "SMART_ANSWER_SOURCE_REPO": str(repo),
        "SMART_ANSWER_GH": str(fake_gh),
        "SMART_ANSWER_DEV_SCRIPT": str(dev),
        "ISSUE_DIR": str(issue_dir),
        "DEV_LOG": str(dev_log),
    }
    return repo, issue_dir, dev_log, env


def _card_worktree(repo: Path, tmp_path: Path, number: int) -> tuple[Path, str]:
    branch = f"wkp-{number}-fixture"
    worktree = tmp_path / branch
    _git(repo, "worktree", "add", "-b", branch, str(worktree), "origin/main")
    _git(worktree, "config", "user.email", "test@example.com")
    _git(worktree, "config", "user.name", "Test User")
    return worktree, branch


def _commit_on_card(worktree: Path, text: str) -> None:
    (worktree / "card.txt").write_text(text, encoding="utf-8")
    _git(worktree, "add", "card.txt")
    _git(worktree, "commit", "-m", text)


def test_closed_clean_merged_card_removes_worktree_and_branch(
    lifecycle_repo: tuple[Path, Path, Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, issue_dir, dev_log, env = lifecycle_repo
    worktree, branch = _card_worktree(repo, tmp_path, 101)
    _commit_on_card(worktree, "merged")
    _git(repo, "merge", "--ff-only", branch)
    _git(repo, "push", "origin", "main")
    (issue_dir / "101").write_text("CLOSED\n", encoding="utf-8")

    result = _run("bash", RECONCILE, "--issue", "101", cwd=repo, env=env)

    assert not worktree.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode != 0
    assert dev_log.read_text(encoding="utf-8") == "stop\n"
    assert "deleted merged local branch" in result.stdout


def test_closed_clean_unmerged_card_preserves_branch(
    lifecycle_repo: tuple[Path, Path, Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, issue_dir, _, env = lifecycle_repo
    worktree, branch = _card_worktree(repo, tmp_path, 102)
    _commit_on_card(worktree, "unmerged")
    (issue_dir / "102").write_text("CLOSED\n", encoding="utf-8")

    result = _run("bash", RECONCILE, "--issue", "102", cwd=repo, env=env)

    assert not worktree.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0
    assert "preserved unmerged local branch" in result.stdout


def test_closed_dirty_card_is_blocked_without_stopping_server(
    lifecycle_repo: tuple[Path, Path, Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, issue_dir, dev_log, env = lifecycle_repo
    worktree, branch = _card_worktree(repo, tmp_path, 103)
    (worktree / "unsaved.txt").write_text("do not lose me\n", encoding="utf-8")
    (issue_dir / "103").write_text("CLOSED\n", encoding="utf-8")

    general = _run("bash", RECONCILE, cwd=repo, env=env)
    result = _run(
        "bash", RECONCILE, "--issue", "103", cwd=repo, env=env, check=False
    )

    assert general.returncode == 0
    assert result.returncode == 2
    assert worktree.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0
    assert not dev_log.exists()
    assert "unsaved.txt" in result.stderr


def test_close_ticket_closes_then_removes_clean_worktree(
    lifecycle_repo: tuple[Path, Path, Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, issue_dir, _, env = lifecycle_repo
    worktree, branch = _card_worktree(repo, tmp_path, 104)
    _commit_on_card(worktree, "closed-not-planned")
    (issue_dir / "104").write_text("OPEN\n", encoding="utf-8")

    result = _run(
        "bash",
        CLOSE_TICKET,
        "104",
        "--reason",
        "not-planned",
        cwd=repo,
        env=env,
    )

    assert (issue_dir / "104").read_text(encoding="utf-8") == "CLOSED\n"
    assert not worktree.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0
    assert "preserved unmerged local branch" in result.stdout


def test_reconciliation_prunes_missing_worktree_registration(
    lifecycle_repo: tuple[Path, Path, Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, issue_dir, _, env = lifecycle_repo
    worktree, _ = _card_worktree(repo, tmp_path, 105)
    (issue_dir / "105").write_text("CLOSED\n", encoding="utf-8")
    shutil.rmtree(worktree)

    _run("bash", RECONCILE, cwd=repo, env=env)

    listed = _git(repo, "worktree", "list", "--porcelain").stdout
    assert str(worktree) not in listed


def test_card_stop_refuses_listener_owned_by_another_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "foreign-listener"
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(DEV_SCRIPT, scripts / "dev.sh")
    _run("git", "init", "--initial-branch=wkp-106-fixture", repo, cwd=tmp_path)

    fake_lsof = bin_dir / "lsof"
    fake_lsof.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *'-iTCP:'* ]]; then\n"
        "  printf '424242\\n'\n"
        "else\n"
        "  printf 'n/some/other/worktree\\n'\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_lsof.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SMART_ANSWER_DEV_STATE": str(state_dir),
    }

    result = _run("bash", scripts / "dev.sh", "stop", cwd=repo, env=env, check=False)

    assert result.returncode != 0
    assert "refusing to stop port" in result.stderr
    assert "outside" in result.stderr

    state_dir.mkdir(exist_ok=True)
    (state_dir / "9106.pid").write_text("111111\n", encoding="utf-8")
    stop_all = _run(
        "bash", scripts / "dev.sh", "stop", "--all", cwd=repo, env=env, check=False
    )

    assert stop_all.returncode != 0
    assert "is not the recorded owner" in stop_all.stderr
