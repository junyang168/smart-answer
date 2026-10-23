"""Durable one-CVP route jobs with fenced, renewable file leases."""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .viewpoint_foundation import sha256_json


def make_cvp_route_job(viewpoint_id: str, material_sha256: str,
                       policy_sha256: str) -> dict[str, Any]:
    body = {"schema_version": "wang_cvp_route_job_v2", "viewpoint_id": viewpoint_id,
            "material_sha256": material_sha256, "policy_sha256": policy_sha256}
    body["job_id"] = "CRJ-" + sha256_json(body)[:24]
    return body | {"artifact_sha256": sha256_json(body)}


class CvpRouteQueue:
    def __init__(self, root: Path):
        self.root = root
        self.jobs = root / "jobs"
        self.states = root / "states"
        self.lock_file = root / ".queue.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_file.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".partial")
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
                             encoding="utf-8")
        temporary.replace(path)

    def _state_path(self, job_id: str) -> Path:
        return self.states / f"{job_id}.json"

    def enqueue(self, job: dict[str, Any]) -> dict[str, Any]:
        if (job.get("schema_version") != "wang_cvp_route_job_v2"
                or job.get("artifact_sha256") != sha256_json({
                    key: value for key, value in job.items() if key != "artifact_sha256"
                })):
            raise ValueError("CVP route job identity is invalid")
        path = self.jobs / f"{job['job_id']}.json"
        with self._lock():
            if path.exists() and self._read(path) != job:
                raise ValueError("CVP route job collision")
            if not path.exists():
                self._write(path, job)
            state_path = self._state_path(job["job_id"])
            if not state_path.exists():
                self._write(state_path, {"status": "queued", "owner_epoch": 0,
                                         "worker_id": None, "lease_expires_at": None})
            return self._read(state_path)

    def claim(self, *, worker_id: str, policy_sha256: str, lease_seconds: int,
              retry_exceptions: bool = False,
              resume_planned: bool = False, job_id: str | None = None) -> tuple[dict[str, Any], int] | None:
        if lease_seconds < 1:
            raise ValueError("lease must be positive")
        now = datetime.now(timezone.utc)
        with self._lock():
            paths = sorted(self.jobs.glob("CRJ-*.json"))
            active_viewpoints = set()
            for path in paths:
                item = self._read(path)
                state = self._read(self._state_path(item["job_id"]))
                if (state["status"] == "running" and datetime.fromisoformat(
                    state["lease_expires_at"]
                ) > now):
                    active_viewpoints.add(item["viewpoint_id"])
            for path in paths:
                job = self._read(path)
                if job_id is not None and job['job_id'] != job_id:
                    continue
                if (job.get("artifact_sha256") != sha256_json({
                    key: value for key, value in job.items() if key != "artifact_sha256"
                }) or path.stem != job.get("job_id")):
                    raise ValueError("queued CVP Route job artifact changed")
                if job["policy_sha256"] != policy_sha256:
                    continue
                if job["viewpoint_id"] in active_viewpoints:
                    continue
                state_path = self._state_path(job["job_id"])
                state = self._read(state_path)
                claimable = state["status"] == "queued" or (
                    retry_exceptions and state["status"] == "exception"
                ) or (
                    resume_planned and state["status"] == "planned"
                ) or (state["status"] == "running" and datetime.fromisoformat(
                    state["lease_expires_at"]
                ) <= now)
                if not claimable:
                    continue
                epoch = int(state["owner_epoch"]) + 1
                self._write(state_path, {"status": "running", "owner_epoch": epoch,
                                         "worker_id": worker_id,
                                         "lease_expires_at": (now + timedelta(
                                             seconds=lease_seconds)).isoformat()})
                return job, epoch
        return None

    def assert_owner(self, job_id: str, *, worker_id: str, epoch: int) -> None:
        with self._lock():
            state = self._read(self._state_path(job_id))
            if (state["status"] != "running" or state["worker_id"] != worker_id
                    or int(state["owner_epoch"]) != epoch or datetime.fromisoformat(
                        state["lease_expires_at"]
                    ) <= datetime.now(timezone.utc)):
                raise ValueError("CVP route lease lost")

    def renew(self, job_id: str, *, worker_id: str, epoch: int,
              lease_seconds: int) -> None:
        if lease_seconds < 1:
            raise ValueError("lease must be positive")
        with self._lock():
            path = self._state_path(job_id)
            state = self._read(path)
            if (state["status"] != "running" or state["worker_id"] != worker_id
                    or int(state["owner_epoch"]) != epoch or datetime.fromisoformat(
                        state["lease_expires_at"]
                    ) <= datetime.now(timezone.utc)):
                raise ValueError("CVP route lease lost")
            state["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(
                seconds=lease_seconds)).isoformat()
            self._write(path, state)

    def finish(self, job_id: str, *, worker_id: str, epoch: int,
               status: str, detail: str | None = None) -> None:
        if status not in {"complete", "planned", "exception", "superseded"}:
            raise ValueError("invalid CVP route terminal state")
        with self._lock():
            path = self._state_path(job_id)
            state = self._read(path)
            if (state["status"] != "running" or state["worker_id"] != worker_id
                    or int(state["owner_epoch"]) != epoch or datetime.fromisoformat(
                        state["lease_expires_at"]
                    ) <= datetime.now(timezone.utc)):
                raise ValueError("CVP route lease lost")
            self._write(path, {"status": status, "owner_epoch": epoch,
                               "worker_id": None, "lease_expires_at": None,
                               "detail": detail})
