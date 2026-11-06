"""Small PostgreSQL queue. Transactions never contain model calls or file I/O."""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def key(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, dsn=None):
        self.dsn = (
            dsn
            or os.getenv("MEDIA_BATCH_DATABASE_URL")
            or os.getenv("KNOWLEDGE_DATABASE_URL")
            or os.getenv("DATABASE_URL")
        )
        if not self.dsn:
            raise ValueError("MEDIA_BATCH_DATABASE_URL is required")

    @contextmanager
    def connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            yield conn

    def install(self):
        with self.connection() as c:
            c.execute(Path(__file__).with_name("schema.sql").read_text())

    def batch(self, config):
        bid = "B-" + key(config)[:24]
        with self.connection() as c:
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('media-ingest-config',0))"
            )
            prior = c.execute(
                "SELECT config->>'capacity_root' AS root FROM media_ingest.batches LIMIT 1"
            ).fetchone()
            if prior and prior["root"] != config["capacity_root"]:
                raise Conflict("All batches must share the same capacity root")
            c.execute(
                "INSERT INTO media_ingest.batches(id,config) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (bid, Jsonb(config)),
            )
        return bid

    def task(self, c, batch, recording, stage, unit, binding, payload, dependencies=()):
        tid = key([recording, stage, unit, binding])
        c.execute(
            """INSERT INTO media_ingest.tasks(id,recording,stage,unit,input_sha,payload,dependencies)
          VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (tid, recording, stage, unit, binding, Jsonb(payload), list(dependencies)),
        )
        c.execute(
            "INSERT INTO media_ingest.batch_tasks VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (batch, tid),
        )
        c.execute(
            """WITH RECURSIVE needed(id) AS (
          SELECT %s::text UNION SELECT unnest(t.dependencies) FROM media_ingest.tasks t JOIN needed n ON n.id=t.id)
          INSERT INTO media_ingest.batch_tasks SELECT %s,id FROM needed ON CONFLICT DO NOTHING""",
            (tid, batch),
        )
        return tid

    def claim(self, batch, worker):
        with self.connection() as c:
            # One mutable pipeline workspace per recording. SKIP LOCKED lets unrelated sermons proceed.
            row = c.execute(
                """SELECT t.* FROM media_ingest.tasks t
              JOIN media_ingest.batch_tasks bt ON bt.task=t.id JOIN media_ingest.batches b ON b.id=bt.batch
              WHERE bt.batch=%s AND b.desired='running' AND t.state='pending' AND t.not_before<=now()
                AND NOT EXISTS (SELECT 1 FROM media_ingest.tasks d WHERE d.id=ANY(t.dependencies) AND d.state<>'complete')
                AND NOT EXISTS (SELECT 1 FROM media_ingest.tasks a WHERE a.recording=t.recording AND a.state='running')
              ORDER BY t.updated_at,t.id FOR UPDATE OF t SKIP LOCKED LIMIT 1""",
                (batch,),
            ).fetchone()
            if row is None:
                return None
            # Serialize competing claims of different tasks for the same recording across controllers.
            if not c.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS ok",
                (row["recording"],),
            ).fetchone()["ok"]:
                return None
            if c.execute(
                "SELECT 1 FROM media_ingest.tasks WHERE recording=%s AND state='running'",
                (row["recording"],),
            ).fetchone():
                return None
            return c.execute(
                """UPDATE media_ingest.tasks SET state='running',attempt=attempt+1,
              worker=%s,heartbeat=now(),updated_at=now(),error=NULL WHERE id=%s RETURNING *""",
                (worker, row["id"]),
            ).fetchone()

    def update(self, task, *, state=None, progress=None, result=None, error=None):
        with self.connection() as c:
            row = c.execute(
                """UPDATE media_ingest.tasks SET state=COALESCE(%s,state),heartbeat=now(),
              updated_at=now(),not_before=CASE WHEN %s='pending' THEN now()+interval '10 seconds' ELSE not_before END,progress=COALESCE(%s,progress),result=COALESCE(%s,result),error=%s
              WHERE id=%s AND attempt=%s AND worker=%s AND state='running' RETURNING id""",
                (
                    state,
                    state,
                    Jsonb(progress) if progress is not None else None,
                    Jsonb(result) if result is not None else None,
                    error,
                    task["id"],
                    task["attempt"],
                    task["worker"],
                ),
            ).fetchone()
            if not row:
                raise Conflict("Task attempt no longer owns this work")

    def control(self, batch, operation, revision, action):
        if action not in ("pause", "resume", "retry"):
            raise ValueError("Unsupported action")
        request = dict(revision=revision, action=action)
        with self.connection() as c:
            b = c.execute(
                "SELECT * FROM media_ingest.batches WHERE id=%s FOR UPDATE", (batch,)
            ).fetchone()
            if not b:
                raise KeyError(batch)
            old = c.execute(
                "SELECT * FROM media_ingest.controls WHERE batch=%s AND operation=%s",
                (batch, operation),
            ).fetchone()
            if old:
                if old["request"] != request:
                    raise Conflict("Operation ID reused with different input")
                return old["revision"]
            if b["revision"] != revision:
                raise Conflict("Batch version changed; reload")
            if action == "retry":
                # Unknown model dispatches require reconciliation; a UI click cannot clear them.
                c.execute(
                    """UPDATE media_ingest.tasks SET state='pending',error=NULL WHERE state IN ('failed','interrupted')
                  AND id IN (SELECT task FROM media_ingest.batch_tasks WHERE batch=%s)
                  AND NOT EXISTS (SELECT 1 FROM media_ingest.calls m WHERE m.task=tasks.id AND m.state='unknown')""",
                    (batch,),
                )
            desired = "paused" if action == "pause" else "running"
            c.execute(
                "UPDATE media_ingest.batches SET desired=%s,revision=revision+1 WHERE id=%s",
                (desired, batch),
            )
            c.execute(
                "INSERT INTO media_ingest.controls VALUES (%s,%s,%s,%s)",
                (batch, operation, Jsonb(request), revision + 1),
            )
            return revision + 1

    def raise_budget(self, batch, operation, revision, budget_usd, reason):
        """Append an authorized cap increase; never reset usage or source identity."""
        import math

        if not math.isfinite(budget_usd) or budget_usd <= 0 or not reason.strip():
            raise ValueError("A positive budget and authorization reason are required")
        request = dict(
            action="raise-budget",
            revision=revision,
            budget_usd=budget_usd,
            reason=reason,
        )
        with self.connection() as c:
            b = c.execute(
                "SELECT * FROM media_ingest.batches WHERE id=%s FOR UPDATE", (batch,)
            ).fetchone()
            if not b:
                raise KeyError(batch)
            prior = c.execute(
                "SELECT * FROM media_ingest.controls WHERE batch=%s AND operation=%s",
                (batch, operation),
            ).fetchone()
            if prior:
                if prior["request"] != request:
                    raise Conflict("Operation ID reused with different input")
                return prior["revision"]
            if b["revision"] != revision:
                raise Conflict("Batch version changed; reload")
            if budget_usd <= self.budget_limit(c, b):
                raise Conflict("Budget adjustment must increase the current cap")
            c.execute(
                "INSERT INTO media_ingest.controls VALUES (%s,%s,%s,%s)",
                (batch, operation, Jsonb(request), revision + 1),
            )
            c.execute(
                "UPDATE media_ingest.batches SET revision=revision+1 WHERE id=%s",
                (batch,),
            )
            return revision + 1

    @staticmethod
    def budget_limit(c, batch):
        override = c.execute(
            "SELECT max((request->>'budget_usd')::numeric) AS cap FROM media_ingest.controls WHERE batch=%s AND request->>'action'='raise-budget'",
            (batch["id"],),
        ).fetchone()["cap"]
        return max(float(batch["config"]["budget_usd"]), float(override or 0))

    def reconcile(self, batch):
        from .runtime import lock_held

        changed = []
        with self.connection() as c:
            rows = c.execute(
                "SELECT t.* FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.state='running' FOR UPDATE OF t",
                (batch,),
            ).fetchall()
            for row in rows:
                lease = Path(row["payload"]["out"]) / "leases" / row["id"]
                if lock_held(lease):
                    continue
                # A controller may be between claiming and opening the lease. Fresh heartbeats never get stolen.
                if (
                    row["heartbeat"]
                    and c.execute(
                        "SELECT now()-%s < interval '30 seconds' AS fresh",
                        (row["heartbeat"],),
                    ).fetchone()["fresh"]
                ):
                    continue
                unknown = c.execute(
                    "SELECT 1 FROM media_ingest.calls WHERE task=%s AND state='unknown'",
                    (row["id"],),
                ).fetchone()
                state = "unknown_outcome" if unknown else "interrupted"
                c.execute(
                    "UPDATE media_ingest.tasks SET state=%s,error='Previous process stopped; saved output requires reconciliation',updated_at=now() WHERE id=%s",
                    (state, row["id"]),
                )
                c.execute(
                    "DELETE FROM media_ingest.disk_reservations WHERE task=%s AND attempt=%s",
                    (row["id"], row["attempt"]),
                )
                changed.append(dict(task=row["id"], state=state))
            # A crash after saving completion but before releasing its space
            # must be recoverable too. Never reclaim a live descendant's lease.
            finished = c.execute(
                "SELECT DISTINCT t.id,t.payload,d.attempt FROM media_ingest.disk_reservations d JOIN media_ingest.tasks t ON t.id=d.task JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.state<>'running'",
                (batch,),
            ).fetchall()
            for row in finished:
                if not lock_held(Path(row["payload"]["out"]) / "leases" / row["id"]):
                    c.execute(
                        "DELETE FROM media_ingest.disk_reservations WHERE task=%s AND attempt=%s",
                        (row["id"], row["attempt"]),
                    )
        return changed

    def reserve(self, batch, task, call, model, usd):
        with self.connection() as c:
            b = c.execute(
                "SELECT * FROM media_ingest.batches WHERE id=%s FOR UPDATE", (batch,)
            ).fetchone()
            old = c.execute(
                "SELECT * FROM media_ingest.calls WHERE id=%s", (call,)
            ).fetchone()
            if old:
                raise Conflict("Call already reserved; reconcile its saved response")
            if b["desired"] != "running":
                from .runtime import WaitingCapacity

                raise WaitingCapacity("Batch paused before dispatch")
            cfg = b["config"]
            if b["calls"] >= cfg["max_calls"] or float(
                b["reserved_usd"]
            ) + usd > self.budget_limit(c, b):
                raise Conflict("Batch call/cost reservation exhausted")
            c.execute(
                "INSERT INTO media_ingest.calls(id,batch,task,model,reserved_usd,state) VALUES (%s,%s,%s,%s,%s,'unknown')",
                (call, batch, task, model, usd),
            )
            c.execute(
                "UPDATE media_ingest.batches SET reserved_usd=reserved_usd+%s,calls=calls+1 WHERE id=%s",
                (usd, batch),
            )

    def call_complete(self, call, info):
        with self.connection() as c:
            c.execute(
                "UPDATE media_ingest.calls SET state='complete',info=%s,updated_at=now() WHERE id=%s",
                (Jsonb(info), call),
            )

    def snapshot(self, batch=None, offset=0, limit=50):
        with self.connection() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            batches = c.execute(
                """SELECT id,created_at,revision,desired,config->>'title' AS title,config->>'mode' AS mode,calls,reserved_usd
              FROM media_ingest.batches ORDER BY created_at DESC LIMIT 100"""
            ).fetchall()
            if batch is None and batches:
                batch = next(
                    (b["id"] for b in batches if b.get("mode") == "inventory"),
                    batches[0]["id"],
                )
            selected = next((b for b in batches if b["id"] == batch), None)
            if batch and selected is None:
                raise KeyError(batch)
            grouped = """WITH g AS (
              SELECT t.recording, bool_and(t.state='complete') AS ready,
              bool_and(t.stage='existing') AS reused,
              bool_or(t.state='running') AS running,
              bool_or(t.state='running' AND t.heartbeat>now()-interval '30 seconds') AS live,
              array_agg(t.state) AS states
              FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id
              WHERE b.batch=%s GROUP BY t.recording), r AS (
              SELECT recording, CASE WHEN ready AND reused THEN 'reused' WHEN ready THEN 'complete' WHEN running THEN
                CASE WHEN live THEN 'running' ELSE 'unverified' END
                WHEN 'unknown_outcome'=ANY(states) THEN 'unknown_outcome'
                WHEN 'needs_resolution'=ANY(states) THEN 'needs_resolution'
                WHEN 'failed'=ANY(states) THEN 'failed'
                WHEN 'interrupted'=ANY(states) THEN 'interrupted' ELSE 'pending' END AS state FROM g)
            """
            counts = c.execute(
                grouped + "SELECT state,count(*) AS n FROM r GROUP BY state", (batch,)
            ).fetchall()
            summary = {
                state: 0
                for state in (
                    "complete",
                    "running",
                    "unverified",
                    "unknown_outcome",
                    "needs_resolution",
                    "failed",
                    "interrupted",
                    "pending",
                )
            }
            summary.update({r["state"]: r["n"] for r in counts})
            summary["total"] = sum(summary.values())
            ids = [
                r["recording"]
                for r in c.execute(
                    grouped
                    + "SELECT recording FROM r ORDER BY recording LIMIT %s OFFSET %s",
                    (batch, limit, offset),
                ).fetchall()
            ]
            tasks = (
                c.execute(
                    """SELECT t.id,t.recording,t.stage,t.unit,t.state,t.attempt,t.heartbeat,t.progress,t.error,t.result,
              t.updated_at,r.item,r.info->>'name' AS name,r.info->>'duration' AS duration,
              extract(epoch FROM (now()-t.heartbeat)) AS heartbeat_age
              FROM media_ingest.tasks t JOIN media_ingest.batch_tasks bt ON bt.task=t.id
              JOIN media_ingest.recordings r ON r.id=t.recording WHERE bt.batch=%s AND t.recording=ANY(%s)
              ORDER BY r.item,array_position(ARRAY['existing','prepare','visuals','speech','final-align','finish','speakers','media','handoff'],t.stage),t.unit,t.id""",
                    (batch, ids),
                ).fetchall()
                if batch
                else []
            )
            records = {}
            for t in tasks:
                rid = t["recording"]
                record = records.setdefault(
                    rid,
                    dict(
                        id=rid,
                        item=t["item"],
                        name=t["name"],
                        duration=t["duration"],
                        tasks=[],
                    ),
                )
                t["live"] = (
                    t["state"] == "running"
                    and t["heartbeat_age"] is not None
                    and t["heartbeat_age"] < 30
                )
                t.pop("heartbeat_age")
                record["tasks"].append(t)
            values = list(records.values())
            for r in values:
                states = [t["state"] for t in r["tasks"]]
                r["ready"] = bool(states) and all(s == "complete" for s in states)
                active = next((t for t in r["tasks"] if t["state"] == "running"), None)
                r["state"] = (
                    (
                        "reused"
                        if all(t["stage"] == "existing" for t in r["tasks"])
                        else "complete"
                    )
                    if r["ready"]
                    else (
                        ("running" if active and active["live"] else "unverified")
                        if active
                        else next(
                            (
                                s
                                for s in (
                                    "unknown_outcome",
                                    "needs_resolution",
                                    "failed",
                                    "interrupted",
                                )
                                if s in states
                            ),
                            "pending",
                        )
                    )
                )
            return dict(
                batches=batches,
                selected=selected,
                records=values,
                summary=summary,
                offset=offset,
                limit=limit,
                generated_at=c.execute("SELECT now() AS at").fetchone()["at"],
            )
