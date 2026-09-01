from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "backup-registry.sh"


def _write_fake_tools(tmp_path: Path) -> Path:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    pg_dump = binary_dir / "pg_dump"
    pg_dump.write_text(
        """#!/usr/bin/env bash
set -eu
output=""
for argument in "$@"; do
    case "$argument" in
        --file=*) output="${argument#--file=}" ;;
    esac
done
[[ -n "$output" ]]
printf 'partial archive' > "$output"
if [[ "${FAKE_PG_DUMP_MODE:-success}" == "fail" ]]; then
    exit 7
fi
printf 'complete custom archive' > "$output"
""",
        encoding="utf-8",
    )
    pg_restore = binary_dir / "pg_restore"
    pg_restore.write_text(
        """#!/usr/bin/env bash
set -eu
case "${FAKE_PG_RESTORE_MODE:-success}" in
    fail) exit 9 ;;
    empty) exit 0 ;;
    success) printf '53; 1259 1 TABLE wang_knowledge records owner\n' ;;
esac
""",
        encoding="utf-8",
    )
    pg_dump.chmod(0o755)
    pg_restore.chmod(0o755)
    return binary_dir


def _run(
    tmp_path: Path,
    label: str,
    *,
    dump_mode: str = "success",
    restore_mode: str = "success",
) -> subprocess.CompletedProcess[str]:
    binary_dir = (
        _write_fake_tools(tmp_path)
        if not (tmp_path / "bin").exists()
        else tmp_path / "bin"
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{binary_dir}:{env['PATH']}",
            "DATA_BASE_DIR": str(tmp_path / "data"),
            "KNOWLEDGE_DATABASE_URL": "postgresql://test.invalid/registry",
            "FAKE_PG_DUMP_MODE": dump_mode,
            "FAKE_PG_RESTORE_MODE": restore_mode,
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT), label],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _target(tmp_path: Path, label: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        tmp_path
        / "data"
        / "wang-knowledge-platform"
        / "staging"
        / "viewpoint-backfill"
        / f"registry-backup-{stamp}-{label}"
    )


def test_backup_is_published_only_after_archive_validation(tmp_path: Path):
    result = _run(tmp_path, "successful")

    final = _target(tmp_path, "successful") / "smart_answer_knowledge.dump"
    assert result.returncode == 0, result.stderr
    assert final.read_text(encoding="utf-8") == "complete custom archive"
    assert not list(final.parent.glob("*.partial.*"))


def test_existing_successful_backup_is_never_overwritten(tmp_path: Path):
    first = _run(tmp_path, "existing")
    final = _target(tmp_path, "existing") / "smart_answer_knowledge.dump"
    original = final.read_bytes()

    second = _run(tmp_path, "existing", dump_mode="fail")

    assert first.returncode == 0
    assert second.returncode != 0
    assert "backup already exists" in second.stderr
    assert final.read_bytes() == original


def test_existing_target_directory_is_treated_as_an_active_backup_lock(
    tmp_path: Path,
):
    target = _target(tmp_path, "in-progress")
    target.mkdir(parents=True)
    marker = target / "owned-by-another-process"
    marker.write_text("keep", encoding="utf-8")

    result = _run(tmp_path, "in-progress")

    assert result.returncode != 0
    assert "another backup is running" in result.stderr
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (target / "smart_answer_knowledge.dump").exists()


def test_failed_dump_leaves_no_final_or_partial_and_same_label_can_retry(tmp_path: Path):
    failed = _run(tmp_path, "dump-failure", dump_mode="fail")
    target = _target(tmp_path, "dump-failure")

    assert failed.returncode != 0
    assert not target.exists()

    retried = _run(tmp_path, "dump-failure")
    assert retried.returncode == 0, retried.stderr
    assert (target / "smart_answer_knowledge.dump").is_file()


@pytest.mark.parametrize("restore_mode", ["fail", "empty"])
def test_invalid_archive_leaves_no_final_and_same_label_can_retry(
    tmp_path: Path, restore_mode: str
):
    failed = _run(tmp_path, "invalid-archive", restore_mode=restore_mode)
    target = _target(tmp_path, "invalid-archive")

    assert failed.returncode != 0
    assert not target.exists()

    retried = _run(tmp_path, "invalid-archive")
    assert retried.returncode == 0, retried.stderr
    assert (target / "smart_answer_knowledge.dump").is_file()
