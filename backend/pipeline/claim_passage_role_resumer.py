"""Bounded, auditable supervisor for an already frozen #409 role review.

This invokes the unchanged SHA-bound runner. Cached valid model artifacts are
reused by that runner; only a failed model response can be called again.
Database drift, subscription errors, and damaged cached artifacts never retry.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.pipeline import claim_passage_role_runner as role_runner


RETRYABLE_VALIDATION_ERRORS = (
    "ValueError: role response denominator mismatch",
    "ValueError: invalid Claim decision:",
    "ValueError: invalid role decision:",
    "ValueError: invalid interpreted reference index:",
    "ValueError: interpreted EvidenceStep reference is not exact:",
    "ValueError: interpreted references disagree with role:",
)


def retryable_validation_error(output: str) -> str | None:
    """Only a fresh model decision's structural validation may be retried."""

    for line in reversed(output.splitlines()):
        if any(line.startswith(message) for message in RETRYABLE_VALIDATION_ERRORS):
            return line
    return None


def _progress(root: Path) -> tuple[int, int]:
    return (
        len(list(root.glob("primary-[0-9][0-9][0-9][0-9][0-9].json"))),
        len(list(root.glob("independent-[0-9][0-9][0-9][0-9][0-9].json"))),
    )


def _append_event(path: Path, event: dict[str, object]) -> None:
    row = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, row.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def supervise(root: Path, *, batch_size: int, max_invalid_retries: int,
              primary_model: str, independent_model: str) -> int:
    if batch_size < 1 or max_invalid_retries < 0:
        raise ValueError("invalid batch size or retry limit")
    packet = role_runner._read_json(root / "role-packet.json")
    role_runner._check_artifact(packet)
    if packet.get("mode") != "all_eligible":
        raise ValueError("supervisor requires an all-eligible frozen packet")
    runner_sha = hashlib.sha256(Path(role_runner.__file__).read_bytes()).hexdigest()
    if packet.get("runner_code_sha256") != runner_sha:
        raise ValueError("frozen runner code SHA changed; refusing resume")
    if packet.get("prompt_sha256") != hashlib.sha256(role_runner.PROMPT.read_bytes()).hexdigest():
        raise ValueError("frozen prompt SHA changed; refusing resume")
    expected_batches = math.ceil(len(packet["claims"]) / batch_size)
    if any(count > expected_batches for count in _progress(root)):
        raise ValueError("review artifact count exceeds frozen denominator")

    # The lock protects the entire series of resumed subprocesses. It never
    # deletes or replaces another process's output.
    with (root / ".role-resumer.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempts_at_checkpoint: dict[tuple[int, int], int] = {}
        command = [
            sys.executable, "-m", "backend.pipeline.claim_passage_role_runner",
            "--output-root", str(root), "--review", "--batch-size", str(batch_size),
            "--primary-model", primary_model,
            "--independent-model", independent_model,
        ]
        while True:
            before = _progress(root)
            started = datetime.now(timezone.utc).isoformat()
            with (root / "review.log").open("a+", encoding="utf-8") as log:
                log.seek(0, os.SEEK_END)
                log.write(f"\n# supervised resume {started} checkpoint={before}\n")
                log.flush()
                position = log.tell()
                completed = subprocess.run(
                    command, cwd=Path.cwd(), stdout=log, stderr=subprocess.STDOUT,
                    check=False,
                )
                log.flush()
                log.seek(position)
                output = log.read()
            after = _progress(root)
            event: dict[str, object] = {
                "time_utc": datetime.now(timezone.utc).isoformat(),
                "packet_sha256": packet["artifact_sha256"],
                "runner_code_sha256": runner_sha,
                "before": list(before), "after": list(after),
                "exit_code": completed.returncode,
            }
            if completed.returncode == 0:
                if after != (expected_batches, expected_batches):
                    raise ValueError("runner exited successfully without exact batch coverage")
                ledger = role_runner._read_json(root / "role-ledger-v4.json")
                role_runner._check_artifact(ledger)
                event["status"] = "complete"
                event["ledger_sha256"] = ledger["artifact_sha256"]
                _append_event(root / "resume-events.jsonl", event)
                print(json.dumps(event, ensure_ascii=False), flush=True)
                return 0

            error = retryable_validation_error(output)
            checkpoint = after
            attempts = attempts_at_checkpoint.get(checkpoint, 0)
            event["validation_error"] = error
            if error and attempts < max_invalid_retries:
                attempts_at_checkpoint[checkpoint] = attempts + 1
                event["status"] = "retrying_invalid_model_response"
                event["retry_number"] = attempts + 1
                _append_event(root / "resume-events.jsonl", event)
                print(json.dumps(event, ensure_ascii=False), flush=True)
                continue
            event["status"] = "stopped_fail_closed"
            _append_event(root / "resume-events.jsonl", event)
            print(json.dumps(event, ensure_ascii=False), flush=True)
            return completed.returncode or 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-invalid-retries", type=int, default=1)
    parser.add_argument("--primary-model", default="gpt-6-sol")
    parser.add_argument("--independent-model", default="claude-fable-5-1")
    args = parser.parse_args()
    return supervise(
        args.output_root, batch_size=args.batch_size,
        max_invalid_retries=args.max_invalid_retries,
        primary_model=args.primary_model, independent_model=args.independent_model,
    )


if __name__ == "__main__":
    raise SystemExit(main())
