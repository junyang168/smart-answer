"""Durable single-host queue for committed-CVP ArgumentRoute work.

Enqueue artifacts are immutable. Mutable execution state lives in separate
current pointers backed by append-only events, so a crashed worker can recover
an expired lease without rewriting what was originally requested.
"""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .viewpoint_batch_resolution import (
    RouteResolutionJob,
    RouteResolutionWorkUnit,
    coalesce_route_resolution_jobs,
)
from .viewpoint_foundation import sha256_json

QUEUE_STATE_VERSION = "wang_route_resolution_job_current_state_v1"
QUEUE_EVENT_VERSION = "wang_route_resolution_job_state_event_v1"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_immutable(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        if _read(path) != dict(payload):
            raise ValueError(f"immutable route queue artifact differs at {path}")
        return
    _write_atomic(path, payload)


class FileRouteResolutionQueue:
    """A filesystem-backed queue suitable for the platform's single host.

    `flock` serializes claim/enqueue state transitions across processes. Lease
    expiry makes a worker crash recoverable; it is not treated as a semantic
    exception and does not alter the immutable job.
    """

    def __init__(self, root: Path):
        self.root = root
        self.jobs_dir = root / "jobs"
        self.states_dir = root / "states"
        self.events_dir = root / "events"
        self.work_units_dir = root / "work-units"
        self.lock_path = root / ".queue.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _state_path(self, job_id: str) -> Path:
        return self.states_dir / f"{job_id}.json"

    def _state(self, job_id: str) -> dict[str, Any] | None:
        path = self._state_path(job_id)
        return _read(path) if path.exists() else None

    def _transition(
        self,
        job_id: str,
        *,
        status: str,
        occurred_at: str,
        attempt: int,
        worker_id: str | None = None,
        lease_expires_at: str | None = None,
        work_unit_sha256: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        event_body = {
            "schema_version": QUEUE_EVENT_VERSION,
            "job_id": job_id,
            "status": status,
            "attempt": attempt,
            "worker_id": worker_id,
            "lease_expires_at": lease_expires_at,
            "work_unit_sha256": work_unit_sha256,
            "detail": detail,
            "occurred_at": occurred_at,
        }
        event = event_body | {"artifact_sha256": sha256_json(event_body)}
        _write_immutable(
            self.events_dir / job_id / f"{event['artifact_sha256']}.json", event
        )
        state_body = {
            "schema_version": QUEUE_STATE_VERSION,
            "job_id": job_id,
            "status": status,
            "attempt": attempt,
            "worker_id": worker_id,
            "lease_expires_at": lease_expires_at,
            "work_unit_sha256": work_unit_sha256,
            "detail": detail,
            "latest_event_sha256": event["artifact_sha256"],
        }
        state = state_body | {"artifact_sha256": sha256_json(state_body)}
        _write_atomic(self._state_path(job_id), state)
        return state

    def enqueue(
        self, job: RouteResolutionJob, *, enqueued_at: str | None = None
    ) -> dict[str, Any]:
        timestamp = enqueued_at or datetime.now(timezone.utc).isoformat()
        with self._lock():
            _write_immutable(
                self.jobs_dir / f"{job.job_id}.json", job.model_dump(mode="json")
            )
            current = self._state(job.job_id)
            if current is not None:
                return current
            return self._transition(
                job.job_id,
                status="queued",
                occurred_at=timestamp,
                attempt=0,
            )

    def claim(
        self,
        *,
        worker_id: str,
        current_viewpoint_revisions: Mapping[str, str],
        now: datetime | None = None,
        lease_seconds: int = 900,
    ) -> RouteResolutionWorkUnit | None:
        if lease_seconds < 1:
            raise ValueError("route queue lease must be positive")
        claimed_at = now or datetime.now(timezone.utc)
        if claimed_at.tzinfo is None:
            raise ValueError("route queue time must be timezone-aware")
        with self._lock():
            available: list[RouteResolutionJob] = []
            for path in sorted(self.jobs_dir.glob("RRJ-*.json")):
                job = RouteResolutionJob.model_validate(_read(path))
                state = self._state(job.job_id)
                if state is None:
                    continue
                if state["status"] == "queued":
                    available.append(job)
                    continue
                if state["status"] == "running" and state.get("lease_expires_at"):
                    expiry = datetime.fromisoformat(str(state["lease_expires_at"]))
                    if expiry <= claimed_at:
                        available.append(job)
            if not available:
                return None

            first_scope = min(
                (item.scope_label, item.scope_manifest_sha256) for item in available
            )
            available = [
                item
                for item in available
                if (item.scope_label, item.scope_manifest_sha256) == first_scope
            ]

            work = coalesce_route_resolution_jobs(
                available,
                current_viewpoint_revisions=current_viewpoint_revisions,
            )
            live_jobs = [
                item for item in available if item.job_id not in work.superseded_job_ids
            ]
            live_coverage = {
                viewpoint_id
                for item in live_jobs
                for viewpoint_id, revision_id in zip(
                    item.logical_viewpoint_ids,
                    item.enqueued_viewpoint_revision_ids,
                    strict=True,
                )
                if current_viewpoint_revisions.get(viewpoint_id) == revision_id
            }
            missing_current_enqueue = sorted(
                {
                    item.viewpoint_id for item in work.current_viewpoint_revisions
                }
                - live_coverage
            )
            if missing_current_enqueue:
                raise ValueError(
                    "current viewpoint revision has no committed enqueue job: "
                    + ", ".join(missing_current_enqueue)
                )
            _write_immutable(
                self.work_units_dir / f"{work.artifact_sha256}.json",
                work.model_dump(mode="json"),
            )
            expiry = (claimed_at + timedelta(seconds=lease_seconds)).isoformat()
            for job in available:
                prior = self._state(job.job_id) or {"attempt": 0}
                if job.job_id in work.superseded_job_ids:
                    self._transition(
                        job.job_id,
                        status="superseded",
                        occurred_at=claimed_at.isoformat(),
                        attempt=int(prior["attempt"]),
                        work_unit_sha256=work.artifact_sha256,
                        detail="queued conclusion revision was replaced by Registry current",
                    )
                else:
                    self._transition(
                        job.job_id,
                        status="running",
                        occurred_at=claimed_at.isoformat(),
                        attempt=int(prior["attempt"]) + 1,
                        worker_id=worker_id,
                        lease_expires_at=expiry,
                        work_unit_sha256=work.artifact_sha256,
                    )
            return work

    def finish(
        self,
        work: RouteResolutionWorkUnit,
        *,
        worker_id: str,
        status: str,
        detail: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        if status not in {"resolved", "exception"}:
            raise ValueError("route work may finish only as resolved or exception")
        timestamp = finished_at or datetime.now(timezone.utc).isoformat()
        with self._lock():
            for job_id in work.source_job_ids:
                if job_id in work.superseded_job_ids:
                    continue
                current = self._state(job_id)
                if (
                    current is None
                    or current["status"] != "running"
                    or current.get("worker_id") != worker_id
                    or current.get("work_unit_sha256") != work.artifact_sha256
                ):
                    raise ValueError(f"worker does not own route job {job_id}")
                self._transition(
                    job_id,
                    status=status,
                    occurred_at=timestamp,
                    attempt=int(current["attempt"]),
                    work_unit_sha256=work.artifact_sha256,
                    detail=detail,
                )
