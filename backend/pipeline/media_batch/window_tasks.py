"""Expand a speech stage into source-bound windows, then clean the full context."""

import json
import os
from pathlib import Path
from .store import Store, Conflict
from .runtime import WaitingCapacity


def prepare(payload):
    context = json.loads(os.environ["MEDIA_BATCH_CONTEXT"])
    master = json.loads((Path(payload["out"]) / "master.json").read_text())
    if context.get("unit", "all") != "all":
        unit = context["unit"]
        if unit not in {w["id"] for w in master["windows"]}:
            raise ValueError("Unknown source window")
        return unit
    store = Store()
    ids = []
    with store.connection() as c:
        parent = c.execute(
            "SELECT * FROM media_ingest.tasks WHERE id=%s FOR UPDATE",
            (context["task"],),
        ).fetchone()
        if parent["state"] != "running" or parent["attempt"] != context["attempt"]:
            raise Conflict("Parent attempt lost ownership")
        dependencies = [
            r["id"]
            for r in c.execute(
                "SELECT id FROM media_ingest.tasks WHERE id=ANY(%s) AND NOT(stage='speech' AND unit<>'all')",
                (parent["dependencies"],),
            ).fetchall()
        ]
        for window in master["windows"]:
            ids.append(
                store.task(
                    c,
                    context["batch"],
                    parent["recording"],
                    "speech",
                    window["id"],
                    parent["input_sha"],
                    payload,
                    dependencies,
                )
            )
        states = c.execute(
            "SELECT state FROM media_ingest.tasks WHERE id=ANY(%s)", (ids,)
        ).fetchall()
        ready = bool(states) and all(s["state"] == "complete" for s in states)
        c.execute(
            "UPDATE media_ingest.tasks SET dependencies=%s WHERE id=%s",
            (dependencies + ids, parent["id"]),
        )
    if not ready:
        raise WaitingCapacity("各音訊窗口已排程，完成後接續全講整理")
    return None
