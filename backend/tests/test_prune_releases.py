"""Retention rules for `scripts/prune-releases.sh`.

The script deletes directories in production, and the thing it must never
delete is the tree a rollback needs. These tests drive the real script against
a fake deploy root.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prune-releases.sh"

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_D = "d" * 40


def make_release(root: Path, sha: str) -> Path:
    release = root / "releases" / sha
    (release / "web").mkdir(parents=True)
    (release / "web" / "payload").write_text("x" * 1024)
    return release


def run_prune(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={"SMART_ANSWER_DEPLOY_ROOT": str(root), "PATH": "/usr/bin:/bin:/usr/sbin"},
        capture_output=True,
        text=True,
    )


def write_deployment(root: Path, sha: str, previous: str) -> None:
    with (root / "deployments.log").open("a") as log:
        log.write(f"2026-08-20T00:00:00Z {sha} previous={previous}\n")


@pytest.fixture()
def deploy_root(tmp_path: Path) -> Path:
    (tmp_path / "releases").mkdir()
    return tmp_path


def test_keeps_active_and_rollback_target_and_removes_the_rest(deploy_root: Path) -> None:
    oldest = make_release(deploy_root, SHA_A)
    previous = make_release(deploy_root, SHA_B)
    active = make_release(deploy_root, SHA_C)
    write_deployment(deploy_root, SHA_B, str(oldest))
    write_deployment(deploy_root, SHA_C, str(previous))
    (deploy_root / "active-release").write_text(f"{active}\n")

    result = run_prune(deploy_root)

    assert result.returncode == 0, result.stderr
    assert active.is_dir()
    assert previous.is_dir()
    assert not oldest.exists()


def test_dry_run_deletes_nothing(deploy_root: Path) -> None:
    oldest = make_release(deploy_root, SHA_A)
    previous = make_release(deploy_root, SHA_B)
    active = make_release(deploy_root, SHA_C)
    write_deployment(deploy_root, SHA_B, str(oldest))
    write_deployment(deploy_root, SHA_C, str(previous))
    (deploy_root / "active-release").write_text(f"{active}\n")

    result = run_prune(deploy_root, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert oldest.is_dir()
    assert "Would remove" in result.stdout


def test_after_a_rollback_the_failed_release_is_the_one_removed(deploy_root: Path) -> None:
    """`rollback()` repoints active-release without appending a log line.

    The last line then reads `<failed> previous=<active>`, so a script that
    trusted it would keep the failed release and delete the tree the running
    service could still fall back to.
    """
    older = make_release(deploy_root, SHA_A)
    active = make_release(deploy_root, SHA_B)
    failed = make_release(deploy_root, SHA_C)
    write_deployment(deploy_root, SHA_B, str(older))
    write_deployment(deploy_root, SHA_C, str(active))
    (deploy_root / "active-release").write_text(f"{active}\n")

    result = run_prune(deploy_root)

    assert result.returncode == 0, result.stderr
    assert active.is_dir()
    assert older.is_dir()
    assert not failed.exists()


def test_refuses_when_the_active_release_is_unknown(deploy_root: Path) -> None:
    release = make_release(deploy_root, SHA_A)

    result = run_prune(deploy_root)

    assert result.returncode == 1
    assert "no active release recorded" in result.stderr
    assert release.is_dir()


def test_refuses_when_the_active_release_is_missing_from_disk(deploy_root: Path) -> None:
    other = make_release(deploy_root, SHA_A)
    (deploy_root / "active-release").write_text(str(deploy_root / "releases" / SHA_D))

    result = run_prune(deploy_root)

    assert result.returncode == 1
    assert "active release does not exist" in result.stderr
    assert other.is_dir()


def test_refuses_a_legacy_active_release_outside_the_releases_directory(deploy_root: Path) -> None:
    legacy = deploy_root / "smart-answer"
    legacy.mkdir()
    release = make_release(deploy_root, SHA_A)
    (deploy_root / "active-release").write_text(str(legacy))

    result = run_prune(deploy_root)

    assert result.returncode == 1
    assert "not under" in result.stderr
    assert release.is_dir()


def test_leaves_entries_that_are_not_release_directories(deploy_root: Path) -> None:
    active = make_release(deploy_root, SHA_A)
    keepsake = deploy_root / "releases" / "backup-before-upgrade"
    keepsake.mkdir()
    (deploy_root / "active-release").write_text(str(active))

    result = run_prune(deploy_root)

    assert result.returncode == 0, result.stderr
    assert keepsake.is_dir()
    assert "Leaving unrecognised entry" in result.stdout


def test_keeps_a_single_release_when_no_rollback_target_is_recorded(deploy_root: Path) -> None:
    active = make_release(deploy_root, SHA_A)
    write_deployment(deploy_root, SHA_A, "none")
    (deploy_root / "active-release").write_text(str(active))

    result = run_prune(deploy_root)

    assert result.returncode == 0, result.stderr
    assert active.is_dir()
    assert "Nothing to prune" in result.stdout
