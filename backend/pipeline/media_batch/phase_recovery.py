"""Explicitly defer a quarantined recording from later rough phase barriers.

This changes scheduling only. It never retries or settles an unknown call and
never removes the per-recording ASR/summary dependency.
"""

import json
from pathlib import Path
from psycopg.types.json import Jsonb
from .runtime import file_sha, lock_held
from .store import Conflict, key


def defer_unknown_barrier(store, batch, task_id, operation):
    with store.connection() as c:
        header = c.execute(
            "SELECT * FROM media_ingest.batches WHERE id=%s FOR UPDATE", (batch,)
        ).fetchone()
        if not header or header["config"].get("mode") != "rough":
            raise Conflict("Rough batch required")
        prior = c.execute(
            "SELECT request FROM media_ingest.controls WHERE batch=%s AND operation=%s",
            (batch, operation),
        ).fetchone()
        if prior:
            if prior["request"].get("held_task") != task_id:
                raise Conflict("Operation already bound to another task")
            return prior["request"]
        tasks = c.execute(
            "SELECT t.* FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s FOR UPDATE OF t",
            (batch,),
        ).fetchall()
        held = next((t for t in tasks if t["id"] == task_id), None)
        if (
            not held
            or held["stage"] != "rough_asr"
            or held["state"] != "unknown_outcome"
        ):
            raise Conflict("Quarantined ASR required")
        out = Path(held["payload"]["out"])
        if lock_held(out / "leases" / task_id):
            raise Conflict("ASR worker is still live")
        calls = c.execute(
            "SELECT id FROM media_ingest.calls WHERE task=%s AND state='unknown' FOR UPDATE",
            (task_id,),
        ).fetchall()
        if len(calls) != 1:
            raise Conflict("Exactly one unknown call required")
        proof = []
        for state_path in out.glob("**/calls/*/state.json"):
            folder = state_path.parent
            request = json.loads((folder / "request.json").read_text())
            if (
                key([task_id, str(folder.parent.parent.resolve()), request])
                != calls[0]["id"]
            ):
                continue
            state = json.loads(state_path.read_text())
            if (
                state.get("status") != "sent_outcome_unknown"
                or (folder / "response.json").exists()
            ):
                raise Conflict("Saved outcome needs reconciliation")
            proof.append(
                dict(
                    call=calls[0]["id"],
                    state_path=str(state_path),
                    state_sha256=file_sha(state_path),
                    request_sha256=file_sha(folder / "request.json"),
                )
            )
        if len(proof) != 1:
            raise Conflict("Exact saved unknown request required")
        summaries = [
            t
            for t in tasks
            if t["recording"] == held["recording"] and t["stage"] == "rough_summary"
        ]
        if (
            len(summaries) != 1
            or summaries[0]["state"] != "pending"
            or summaries[0]["dependencies"] != [task_id]
        ):
            raise Conflict("Expected pending summary required")
        summary = summaries[0]["id"]
        changes = []
        for t in tasks:
            if t["stage"] != "rough_prepare" or summary not in t["dependencies"]:
                continue
            if (
                t["state"] != "pending"
                or t["attempt"] != 0
                or t["payload"]["phase"] <= held["payload"]["phase"]
            ):
                raise Conflict(
                    "Only unstarted later-phase prepare tasks may be released"
                )
            after = [d for d in t["dependencies"] if d != summary]
            changes.append(dict(task=t["id"], before=t["dependencies"], after=after))
        if not changes:
            raise Conflict("No later phase barriers to release")
        receipt = dict(
            schema_version="rough-phase-deferral.v1",
            held_task=task_id,
            held_summary=summary,
            proof=proof,
            changes=changes,
            unknown_preserved=True,
            model_inputs_unchanged=True,
        )
        for change in changes:
            c.execute(
                "UPDATE media_ingest.tasks SET dependencies=%s,updated_at=now() WHERE id=%s",
                (change["after"], change["task"]),
            )
        c.execute(
            "INSERT INTO media_ingest.controls VALUES (%s,%s,%s,%s)",
            (batch, operation, Jsonb(receipt), header["revision"]),
        )
        return receipt
