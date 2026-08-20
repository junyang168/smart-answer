"""Record what every pipeline run did, from inside the runner that did it.

The overview page reads this table instead of scanning staging directories.
Scanning cannot answer the question: the 28 extraction packages on disk today
sit under seven different layouts, cover 19 sources, and one manuscript appears
under three SHAs in four places.  A directory scan starts lying the moment
somebody writes a batch into an eighth layout, and it can never report what a
run cost or how long it took, because those were never on disk at all.

Writing is the runner's job, not the API's.  Every piece of work on this corpus
is currently started from a terminal, so a ledger that only recorded
panel-triggered runs would show an empty table while the machine was busy.

**Recording never breaks the work.**  No database configured, connection
refused, migration not yet applied -- each degrades to a warning on stderr and
the run proceeds unrecorded.  That silence is a known way for the overview to be
wrong, and it is listed as such in the spec; a ledger that could abort a
ten-minute extraction would be a worse bargain than an incomplete one.
"""

from __future__ import annotations

import getpass
import json
import os
import secrets
import shlex
import sys
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

from backend.pipeline.model_prices import RunCost, price_usage


STAGES = ("extraction", "review", "adjudication", "merge", "ingest", "article")

#: Stages that call a model, and so can cost money.  `merge` and `ingest` are
#: arithmetic and a database write; their cost is zero, and zero is a fact about
#: them rather than an absence of measurement.
MODEL_STAGES = ("extraction", "review", "adjudication", "article")
SUBJECT_KINDS = ("source", "draft", "batch")

#: The worker refreshes this while it works; a row whose heartbeat is older than
#: this is treated as interrupted.  A deploy is `launchctl unload` on the API
#: job, so a run really can stop without ever writing a terminal status, and
#: without this the row would claim to be running forever.
HEARTBEAT_INTERVAL_SECONDS = 30.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _warn(message: str) -> None:
    print(f"run_ledger: {message}", file=sys.stderr)


def _connect() -> Optional[Any]:
    """A connection, or None with a warning -- never an exception."""

    url = os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        _warn("KNOWLEDGE_DATABASE_URL is not set; this run will not be recorded")
        return None
    try:
        import psycopg
    except ImportError:
        _warn("psycopg is not installed; this run will not be recorded")
        return None
    try:
        return psycopg.connect(url, autocommit=True)
    except Exception as exc:  # pragma: no cover - depends on local environment
        _warn(f"cannot reach the ledger database ({exc}); this run will not be recorded")
        return None


def new_run_id() -> str:
    return f"RUN-{secrets.token_hex(13)}"


def current_command() -> str:
    """The command line that started this run, so a reader can repeat it."""

    return shlex.join([Path(sys.argv[0]).name, *sys.argv[1:]]) if sys.argv else ""


class RunRecord:
    """A single row being written as the work happens.

    Every setter is additive and safe to call more than once; the row is written
    on entry and updated on exit, so a run that dies without reaching the exit
    still leaves a `running` row with a stale heartbeat rather than nothing.
    """

    def __init__(
        self,
        *,
        run_id: str,
        subject_id: str,
        stage: str,
        subject_kind: str,
        conn: Optional[Any],
    ) -> None:
        self.run_id = run_id
        self.subject_id = subject_id
        self.stage = stage
        self.subject_kind = subject_kind
        self._conn = conn
        self._usage: list[dict[str, Any]] = []
        self._quality: dict[str, Any] = {}
        self._outputs: list[str] = []
        self._inputs: dict[str, Any] = {}
        self._metadata: dict[str, Any] = {}
        self._source_ids: list[str] = []
        self._model_id: Optional[str] = None
        self._stop = threading.Event()
        self._beat: Optional[threading.Thread] = None

    # -- what the runner tells us -------------------------------------------

    def usage(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """Add token usage, including the attempts that were rejected."""
        self._usage.extend(dict(row) for row in rows or [])

    def quality(self, values: Mapping[str, Any]) -> None:
        """The stage's own measure of how good this result is."""
        self._quality.update(dict(values or {}))

    def outputs(self, *paths: Any) -> None:
        for path in paths:
            if path is None:
                continue
            self._outputs.append(str(path))

    def inputs(self, values: Mapping[str, Any]) -> None:
        """Which inputs this run read, so a later read can tell current from stale."""
        self._inputs.update({k: v for k, v in dict(values or {}).items() if v is not None})

    def metadata(self, values: Mapping[str, Any]) -> None:
        self._metadata.update(dict(values or {}))

    def sources(self, ids: Iterable[str]) -> None:
        """Every source this run touched.

        An article run cites many sources -- the Matt 16:13-20 article cites
        eight -- and the sermon overview projects the run back onto each of
        their rows through this array.
        """
        for value in ids or []:
            text = str(value)
            if text and text not in self._source_ids:
                self._source_ids.append(text)

    def model(self, model_id: Optional[str]) -> None:
        if model_id:
            self._model_id = str(model_id)

    def _recorded_sources(self) -> list[str]:
        """A source-stage run always touches its own subject.

        Stated once rather than at each write site: a per-source run that had to
        remember to declare itself would eventually forget, and it would vanish
        from that source's row while still appearing in the runs list.
        """
        if self._source_ids:
            return list(self._source_ids)
        return [self.subject_id] if self.subject_kind == "source" else []

    # -- lifecycle -----------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self._conn is not None

    def _execute(self, sql: str, params: Sequence[Any]) -> None:
        if self._conn is None:
            return
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params)
        except Exception as exc:  # pragma: no cover - depends on local environment
            _warn(f"could not write run {self.run_id} ({exc}); dropping the ledger for this run")
            self._conn = None

    def start(self, *, trigger: str, triggered_by: Optional[str], batch_id: Optional[str]) -> None:
        self._execute(
            """INSERT INTO wang_knowledge.pipeline_runs
                   (run_id, batch_id, subject_kind, subject_id, source_ids, stage,
                    trigger, triggered_by, status, started_at, heartbeat_at, command)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s)""",
            (
                self.run_id, batch_id, self.subject_kind, self.subject_id,
                self._recorded_sources(),
                self.stage, trigger, triggered_by, _now(), _now(), current_command(),
            ),
        )
        if self._conn is not None:
            self._beat = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._beat.start()

    def _heartbeat_loop(self) -> None:
        # A daemon thread rather than a call the runner has to remember: one
        # extraction section can be a single API call lasting minutes, and
        # there is no point inside it where the runner could report progress.
        while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            self._execute(
                "UPDATE wang_knowledge.pipeline_runs SET heartbeat_at=%s WHERE run_id=%s",
                (_now(), self.run_id),
            )

    def cancel_requested(self) -> bool:
        """Whether somebody asked this run to stop, checked between sections."""

        if self._conn is None:
            return False
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT cancel_requested FROM wang_knowledge.pipeline_runs WHERE run_id=%s",
                    (self.run_id,),
                )
                row = cursor.fetchone()
            return bool(row and row[0])
        except Exception:  # pragma: no cover - depends on local environment
            return False

    def finish(self, status: str, error_message: Optional[str] = None) -> None:
        self._stop.set()
        cost = price_usage(self._usage, self._model_id)
        if not self._usage and self.stage in MODEL_STAGES and status != "succeeded":
            # A run that died partway very likely made calls it never got to
            # report -- the extraction runner hands over its usage rows at the
            # end, so a crash in section three loses all three. Pricing that as
            # $0 would put a free-looking failure in the ledger and let a cost
            # cap be defeated by crashing. Unknown is the truth here.
            cost = RunCost(cost_usd=None, price_version=cost.price_version, unpriced=())
        if cost.unpriced:
            _warn(
                f"no price for {', '.join(cost.unpriced)}; run {self.run_id} "
                "records tokens but not a cost"
            )
        metadata = dict(self._metadata)
        if cost.unpriced:
            metadata["unpriced_models"] = list(cost.unpriced)
        self._execute(
            """UPDATE wang_knowledge.pipeline_runs
                   SET status=%s, finished_at=%s, heartbeat_at=%s, error_message=%s,
                       model_id=%s, usage=%s, cost_usd=%s, price_version=%s,
                       quality=%s, input_sha256=%s, output_paths=%s,
                       source_ids=%s, metadata=%s
                 WHERE run_id=%s""",
            (
                status, _now(), _now(), error_message,
                self._model_id, json.dumps(self._usage, ensure_ascii=False),
                cost.cost_usd, cost.price_version,
                json.dumps(self._quality, ensure_ascii=False),
                json.dumps(self._inputs, ensure_ascii=False),
                self._outputs,
                self._recorded_sources(),
                json.dumps(metadata, ensure_ascii=False),
                self.run_id,
            ),
        )
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover
                pass
            self._conn = None


class RunCancelled(RuntimeError):
    """Raised by a runner that noticed `cancel_requested` and stopped cleanly."""


@contextmanager
def run_record(
    *,
    subject: str,
    stage: str,
    trigger: str = "cli",
    subject_kind: str = "source",
    sources: Optional[Iterable[str]] = None,
    triggered_by: Optional[str] = None,
    batch_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Iterator[RunRecord]:
    """Record one run, whatever happens to it.

    Success writes `succeeded`; an exception writes `failed` with the message and
    re-raises; a clean stop after a cancel request writes `cancelled`.  The one
    status this cannot write is `interrupted` -- a killed process does not get to
    run its own `finally` -- which is why the reader treats a stale heartbeat as
    interrupted instead.
    """

    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    if subject_kind not in SUBJECT_KINDS:
        raise ValueError(f"unknown subject_kind {subject_kind!r}")
    if trigger not in ("cli", "panel"):
        raise ValueError(f"unknown trigger {trigger!r}")

    record = RunRecord(
        run_id=run_id or new_run_id(),
        subject_id=str(subject),
        stage=stage,
        subject_kind=subject_kind,
        conn=_connect(),
    )
    if sources:
        record.sources(sources)
    if triggered_by is None and trigger == "cli":
        try:
            triggered_by = getpass.getuser()
        except Exception:  # pragma: no cover - depends on local environment
            triggered_by = None
    record.start(trigger=trigger, triggered_by=triggered_by, batch_id=batch_id)
    try:
        yield record
    except RunCancelled as exc:
        record.finish("cancelled", str(exc) or "cancelled")
        raise
    except BaseException as exc:
        # The first line is what the runs list shows without being expanded, so
        # it has to be the exception itself; the traceback follows for the
        # detail view. A KeyboardInterrupt is a stop, not a success.
        detail = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        record.finish("cancelled" if isinstance(exc, KeyboardInterrupt) else "failed", detail)
        raise
    else:
        record.finish("succeeded")
