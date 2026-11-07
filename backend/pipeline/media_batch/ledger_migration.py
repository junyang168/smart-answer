"""Explicit, source-preserving ledger cutover. Never dispatch tasks or call models."""

import json
from pathlib import Path
from psycopg import sql
from .runtime import atomic, file_sha

TABLE_KEYS = {
    "batches": ("id",),
    "recordings": ("id",),
    "assets": ("sha",),
    "tasks": ("id",),
    "batch_tasks": ("batch", "task"),
    "calls": ("id",),
    "controls": ("batch", "operation"),
    "batch_files": ("batch", "path"),
}


def normalize(value):
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def export_ledger(store, batches, output):
    output = Path(output)
    if output.exists():
        raise ValueError("Snapshot already exists; retain it and use restore_ledger")
    with store.connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = {
            "batches": c.execute(
                "SELECT * FROM media_ingest.batches WHERE id=ANY(%s) ORDER BY id",
                (batches,),
            ).fetchall()
        }
        if {r["id"] for r in rows["batches"]} != set(batches):
            raise ValueError("Missing selected batch")
        rows["batch_tasks"] = c.execute(
            "SELECT * FROM media_ingest.batch_tasks WHERE batch=ANY(%s) ORDER BY batch,task",
            (batches,),
        ).fetchall()
        ids = [r["task"] for r in rows["batch_tasks"]]
        rows["tasks"] = c.execute(
            "SELECT * FROM media_ingest.tasks WHERE id=ANY(%s) ORDER BY id", (ids,)
        ).fetchall()
        if any(r["state"] != "complete" for r in rows["tasks"]):
            raise ValueError("Only completed tasks may be cut over by this tool")
        if any(set(r["dependencies"]) - set(ids) for r in rows["tasks"]):
            raise ValueError("Task dependency outside selected batches")
        if c.execute(
            "SELECT 1 FROM media_ingest.disk_reservations WHERE task=ANY(%s) LIMIT 1",
            (ids,),
        ).fetchone():
            raise ValueError("Active disk reservations remain")
        recordings = sorted({r["recording"] for r in rows["tasks"]})
        rows["recordings"] = c.execute(
            "SELECT * FROM media_ingest.recordings WHERE id=ANY(%s) ORDER BY id",
            (recordings,),
        ).fetchall()
        rows["assets"] = c.execute(
            "SELECT * FROM media_ingest.assets WHERE recording=ANY(%s) ORDER BY sha",
            (recordings,),
        ).fetchall()
        rows["calls"] = c.execute(
            "SELECT * FROM media_ingest.calls WHERE task=ANY(%s) ORDER BY id", (ids,)
        ).fetchall()
        if any(r["batch"] not in batches for r in rows["calls"]):
            raise ValueError("Call belongs to unselected batch")
        rows["controls"] = c.execute(
            "SELECT * FROM media_ingest.controls WHERE batch=ANY(%s) ORDER BY batch,operation",
            (batches,),
        ).fetchall()
        rows["batch_files"] = c.execute(
            "SELECT * FROM media_ingest.batch_files WHERE batch=ANY(%s) ORDER BY batch,path",
            (batches,),
        ).fetchall()
        if any(
            r["recording"] and r["recording"] not in recordings
            for r in rows["batch_files"]
        ):
            raise ValueError("File recording outside selected batches")
        packet = normalize(
            dict(
                schema_version="media-batch-ledger-migration.v1",
                batches=batches,
                tables=rows,
            )
        )
    atomic(output, packet)
    return dict(
        path=str(output),
        sha256=file_sha(output),
        counts={k: len(v) for k, v in rows.items()},
    )


def restore_ledger(store, snapshot):
    packet = json.loads(Path(snapshot).read_text())
    if packet.get("schema_version") != "media-batch-ledger-migration.v1" or set(
        packet["tables"]
    ) != set(TABLE_KEYS):
        raise ValueError("Invalid ledger snapshot")
    with store.connection() as c:
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('media-ingest-cutover',0))"
        )
        for table, keys in TABLE_KEYS.items():
            values = packet["tables"][table]
            c.execute(
                sql.SQL(
                    "INSERT INTO media_ingest.{} SELECT * FROM jsonb_populate_recordset(NULL::media_ingest.{},%s::jsonb) ON CONFLICT DO NOTHING"
                ).format(sql.Identifier(table), sql.Identifier(table)),
                (json.dumps(values),),
            )
            for value in values:
                query = sql.SQL("SELECT * FROM media_ingest.{} WHERE ").format(
                    sql.Identifier(table)
                ) + sql.SQL(" AND ").join(
                    sql.SQL("{}=%s").format(sql.Identifier(k)) for k in keys
                )
                actual = c.execute(query, tuple(value[k] for k in keys)).fetchone()
                if normalize(actual) != value:
                    raise ValueError(f"Target differs in {table}; refusing overwrite")
    return dict(
        snapshot_sha256=file_sha(Path(snapshot)),
        counts={k: len(v) for k, v in packet["tables"].items()},
        model_calls=0,
    )
