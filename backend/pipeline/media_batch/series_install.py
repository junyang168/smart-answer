"""Install an approved rough-series index as metadata and future queue priority.

No transcription, ownership, draft, Claim or publication writes. Existing series
and existing human metadata are preserved. A saved plan makes file/SQL recovery
repeatable without resetting the approved source or completed work.
"""

from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
from psycopg.types.json import Jsonb
from .runtime import atomic, file_sha
from .store import Store, key
from .series_catalog import SERIES

IDS = {s: "Ruxin " + s.replace("系列", "") for s in SERIES}


def prepare(store, catalog_path, root, directory):
    catalog_path, root, directory = map(Path, (catalog_path, root, directory))
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        if plan["catalog_sha256"] != file_sha(catalog_path) or plan["root"] != str(
            root.resolve()
        ):
            raise ValueError("Existing install plan belongs to different input")
        return plan
    catalog = json.loads(catalog_path.read_text())
    records = catalog["records"]
    if set(catalog["counts"]) != set(SERIES) or len(
        {r["recording"] for r in records}
    ) != len(records):
        raise ValueError("Exactly five approved series and unique recordings required")
    batch = catalog["batch"]
    with store.connection() as c:
        actual = c.execute(
            "SELECT r.id,r.item,r.source_sha,t.result,t.payload FROM media_ingest.recordings r JOIN media_ingest.tasks t ON t.recording=r.id JOIN media_ingest.batch_tasks bt ON bt.task=t.id WHERE bt.batch=%s AND t.stage='rough_summary' AND t.state='complete'",
            (batch,),
        ).fetchall()
    by = {r["id"]: r for r in actual}
    if set(by) != {r["recording"] for r in records}:
        raise ValueError("Completed SQL scope differs from reviewed catalog")
    metadata_path = root / "config/sermon.json"
    series_path = root / "config/sermon_series.json"
    original = {
        metadata_path: metadata_path.read_bytes(),
        series_path: series_path.read_bytes(),
    }
    metadata = json.loads(original[metadata_path])
    series = json.loads(original[series_path])
    if any(s["id"] in IDS.values() for s in series):
        raise ValueError(
            "A target series already exists; refuse replacing its membership"
        )
    existing = {r["item"]: r for r in metadata}
    members = {s: [] for s in SERIES}
    changes = []
    for order, record in enumerate(records):
        row = by[record["recording"]]
        out = Path(record["out"])
        if (
            row["source_sha"] != record["source_sha256"]
            or row["result"]["rough_sha256"] != record["rough_sha256"]
            or row["result"]["summary_sha256"] != record["summary_sha256"]
            or file_sha(out / "rough.json") != record["rough_sha256"]
            or file_sha(out / "rough-summary.json") != record["summary_sha256"]
        ):
            raise ValueError("Reviewed recording changed")
        item = row["item"]
        if item in existing or any(item in s.get("sermons", []) for s in series):
            raise ValueError("Existing website item must not be replaced: " + item)
        if any(
            (root / f / item).exists() or (root / f / (item + ".json")).exists()
            for f in ("script_review", "script_published", "transcript_review")
        ):
            raise ValueError("Existing draft or publication needs reconciliation")
        name = record["series"]
        sid = IDS[name]
        members[name].append(item)
        fields = dict(
            series_id=sid,
            series_title=name,
            series_order=record["reading_position"],
            processing_order=order,
            series_catalog_sha256=file_sha(catalog_path),
            sermon_title=record["summary"]["theme"],
        )
        metadata.append(
            dict(
                item=item,
                title=record["summary"]["theme"],
                author="王守仁",
                type="video" if row["payload"]["info"]["video"] else "audio",
                status="processing",
                assigned_to=None,
                deliver_date=record["date"] or None,
                summary=None,
                rough_href=record["href"],
                media_ingest_source_sha256=row["source_sha"],
                media_ingest_batch=batch,
                series_id=sid,
                series_title=name,
                series_order=record["reading_position"],
            )
        )
        changes.append(
            dict(
                recording=row["id"],
                item=item,
                source_sha256=row["source_sha"],
                fields=fields,
            )
        )
    for name in SERIES:
        if len(members[name]) != catalog["counts"][name]:
            raise ValueError("Series count differs")
        series.append(
            dict(
                id=IDS[name],
                title=name,
                summary="Ruxin 錄音整理；粗逐字稿已完成，待完整處理及同工校對。",
                topics="",
                keypoints="",
                sermons=members[name],
            )
        )
    policy = dict(
        schema_version="media-series-processing-order.v1",
        batch=batch,
        catalog_sha256=file_sha(catalog_path),
        series_ids=IDS,
        ordered=changes,
    )
    files = []
    for target, value in [
        (metadata_path, metadata),
        (series_path, series),
        (root / "config/media-batch-series-order.json", policy),
    ]:
        backup = directory / ("before-" + target.name)
        post = directory / ("after-" + target.name)
        before = file_sha(target) if target.exists() else None
        if target in original and target.read_bytes() != original[target]:
            raise ValueError("Metadata changed while preparing")
        if target.exists():
            backup.write_bytes(target.read_bytes())
        atomic(post, value)
        files.append(
            dict(
                path=str(target),
                before_sha256=before,
                backup=str(backup),
                post=str(post),
                after_sha256=file_sha(post),
            )
        )
    plan = dict(
        schema_version="media-series-install.v1",
        batch=batch,
        root=str(root.resolve()),
        catalog_sha256=file_sha(catalog_path),
        counts=catalog["counts"],
        files=files,
        recordings=changes,
    )
    atomic(plan_path, plan)
    return plan


def apply(store, plan, operation):
    root = Path(plan["root"])
    request = dict(action="install-five-series", plan_sha256=key(plan))
    with (root / "config/.sermon-review.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with store.connection() as c:
            b = c.execute(
                "SELECT revision FROM media_ingest.batches WHERE id=%s FOR UPDATE",
                (plan["batch"],),
            ).fetchone()
            prior = c.execute(
                "SELECT request FROM media_ingest.controls WHERE batch=%s AND operation=%s",
                (plan["batch"], operation),
            ).fetchone()
            if prior:
                if prior["request"] != request:
                    raise ValueError("Operation already bound to another plan")
                return dict(already_applied=True, counts=plan["counts"])
            for file in plan["files"]:
                p = Path(file["path"])
                actual = file_sha(p) if p.exists() else None
                if actual not in (file["before_sha256"], file["after_sha256"]):
                    raise ValueError(
                        "Website metadata changed since preparation: " + str(p)
                    )
                if file_sha(Path(file["post"])) != file["after_sha256"]:
                    raise ValueError("Saved postimage changed")
            for r in plan["recordings"]:
                row = c.execute(
                    "SELECT source_sha,info FROM media_ingest.recordings WHERE id=%s FOR UPDATE",
                    (r["recording"],),
                ).fetchone()
                if row["source_sha"] != r["source_sha256"]:
                    raise ValueError("SQL source changed")
                if c.execute(
                    "SELECT 1 FROM media_ingest.tasks WHERE recording=%s AND state='running'",
                    (r["recording"],),
                ).fetchone():
                    raise ValueError(
                        "Recording is being processed; wait for safe scheduling boundary"
                    )
                c.execute(
                    "UPDATE media_ingest.recordings SET info=%s WHERE id=%s",
                    (Jsonb({**row["info"], **r["fields"]}), r["recording"]),
                )
                c.execute(
                    "UPDATE media_ingest.assets SET info=info || %s WHERE recording=%s",
                    (Jsonb(r["fields"]), r["recording"]),
                )
            # Postimages can be resumed after a crash; backups and SQL identity are
            # fixed by plan hash. No new queue task or model dispatch is created.
            for file in plan["files"]:
                p = Path(file["path"])
                atomic(p, json.loads(Path(file["post"]).read_text()))
                if file_sha(p) != file["after_sha256"]:
                    raise ValueError("Installed postimage differs")
            c.execute(
                "INSERT INTO media_ingest.controls VALUES (%s,%s,%s,%s)",
                (plan["batch"], operation, Jsonb(request), b["revision"]),
            )
    return dict(applied=True, counts=plan["counts"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["catalog", "root", "directory"]:
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--operation", default="owner-five-series-order-20261107")
    args = p.parse_args()
    store = Store()
    plan = prepare(store, args.catalog, args.root, args.directory)
    print(
        json.dumps(
            (
                apply(store, plan, args.operation)
                if args.apply
                else dict(
                    prepared=True, counts=plan["counts"], directory=str(args.directory)
                )
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
