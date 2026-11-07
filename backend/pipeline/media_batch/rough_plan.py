"""Freeze approved preflight groups into the existing bounded PostgreSQL queue."""

from pathlib import Path
import json
from psycopg.types.json import Jsonb
from .runtime import atomic, file_sha, storage_guards
from .store import key
from .rough import STAGES


def create(store, report_path, assembly_path, source_root, config):
    report_path = Path(report_path)
    report = json.loads(report_path.read_text())
    source_root = Path(source_root)
    rows = {r["path"]: r for r in report["files"]}
    scope = []
    # The visible preflight group order is the owner's requested ordering.
    paths = [p for group in report["groups"] for p in group["ordered_paths"]]
    for path in paths:
        r = rows[path]
        if r["status"] != "待排程：未找到既有同錄音":
            continue
        scope.append(
            dict(path=path, source=str(source_root / path), sha=r["sha256"], phase=1)
        )
    assembly = json.loads(Path(assembly_path).read_text())
    camera_root = Path(assembly["source_folder"])
    for group in assembly["groups"]:
        if not group["recording_continuity_verified"] or any(
            abs(g) > 0.000002 for g in group["boundary_gap_seconds"]
        ):
            raise ValueError("Unverified camera assembly")
        parts = [
            dict(
                path=str(camera_root / s["file"]),
                sha256=s["source_sha256_from_inventory"],
                start_seconds=s["audio_start_seconds"],
                end_seconds=s["audio_end_seconds"],
            )
            for s in group["segments"]
        ]
        manifest = dict(
            group=group["group"], parts=parts, duration=group["audio_duration_seconds"]
        )
        p = Path(config["artifact_root"]) / "source-groups" / (group["group"] + ".json")
        if p.exists() and json.loads(p.read_text()) != manifest:
            raise ValueError("Frozen camera manifest changed")
        atomic(p, manifest)
        scope.append(
            dict(
                path=group["group"],
                source=str(p),
                sha=file_sha(p),
                phase=2,
                parts=parts,
                duration=group["audio_duration_seconds"],
            )
        )
    for path in paths:
        r = rows[path]
        if r["status"] == "待確認講次":
            scope.append(
                dict(
                    path=path, source=str(source_root / path), sha=r["sha256"], phase=3
                )
            )
    cfg = dict(
        config,
        mode="rough",
        scope=scope,
        preflight_sha256=file_sha(report_path),
        assembly_sha256=file_sha(Path(assembly_path)),
    )
    cfg["storage_guards"] = storage_guards(cfg)
    batch = store.batch(cfg)
    gates = []
    phase_summaries = {1: [], 2: [], 3: []}
    for order, row in enumerate(scope):
        p = Path(row["source"])
        if file_sha(p) != row["sha"]:
            raise ValueError("Preflight source changed: " + row["path"])
        if row.get("parts"):
            duration = row["duration"]
            info = dict(source_parts=row["parts"], video=False, width=0, height=0)
        else:
            meta = json.loads(
                (Path(config["fingerprint_root"]) / (row["sha"] + ".json")).read_text()
            )
            duration = meta["decoded_seconds"]
            info = dict(
                video=p.suffix.lower() in (".mp4", ".mov", ".m4v"), width=0, height=0
            )
        info.update(
            source=str(p),
            source_sha=row["sha"],
            bytes=p.stat().st_size,
            duration=duration,
            relative_path=row["path"],
            name=row["path"],
            sermon_title=row["path"],
            rough_only=True,
        )
        rid = "R-" + row["sha"][:24]
        item = "INGEST-" + row["sha"][:16]
        with store.connection() as c:
            existing = c.execute(
                "SELECT r.* FROM media_ingest.assets a JOIN media_ingest.recordings r ON r.id=a.recording WHERE a.sha=%s",
                (row["sha"],),
            ).fetchone()
            if existing:
                if existing["info"].get("existing_transcript"):
                    raise ValueError("Completed manuscript must not be retranscribed")
                rid, item = existing["id"], existing["item"]
                info = {**existing["info"], **info}
            c.execute(
                "INSERT INTO media_ingest.recordings(id,source_sha,item,info) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (rid, row["sha"], item, Jsonb(info)),
            )
            c.execute(
                "INSERT INTO media_ingest.assets(sha,recording,info) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (row["sha"], rid, Jsonb(info)),
            )
            c.execute(
                "INSERT INTO media_ingest.batch_files(batch,path,sha,bytes,recording,state,reason) VALUES (%s,%s,%s,%s,%s,'pending','粗轉錄佇列；未發布') ON CONFLICT DO NOTHING",
                (batch, row["path"], row["sha"], info["bytes"], rid),
            )
            binding = key([row["sha"], cfg["vocabulary_sha"], cfg["rough_code_sha"]])
            payload = dict(
                recording=rid,
                info=info,
                item=item,
                out=str(Path(cfg["artifact_root"]) / rid / ("rough-" + binding[:16])),
                config={k: v for k, v in cfg.items() if k != "scope"},
                queue_order=order,
                phase=row["phase"],
            )
            dependencies = [
                t
                for ph, ids in phase_summaries.items()
                if ph < row["phase"]
                for t in ids
            ]
            for stage in STAGES:
                tid = store.task(
                    c,
                    batch,
                    rid,
                    stage,
                    "all",
                    key([binding, stage]),
                    payload,
                    dependencies,
                )
                dependencies = [tid]
            phase_summaries[row["phase"]].append(tid)
    # Link the original inventory's camera segments to their assembled recording,
    # without treating an individual segment as a byte alias of the full recording.
    with store.connection() as c:
        for row in scope:
            if not row.get("parts"):
                continue
            rid = "R-" + row["sha"][:24]
            for part in row["parts"]:
                conflicts = c.execute(
                    "SELECT 1 FROM media_ingest.batch_files f JOIN media_ingest.batches b ON b.id=f.batch WHERE b.config->>'mode'='inventory' AND f.sha=%s AND f.recording IS NOT NULL AND f.recording<>%s",
                    (part["sha256"], rid),
                ).fetchone()
                if conflicts:
                    raise ValueError(
                        "Camera segment already assigned to a different recording"
                    )
                c.execute(
                    "UPDATE media_ingest.batch_files f SET recording=%s WHERE f.sha=%s AND EXISTS (SELECT 1 FROM media_ingest.batches b WHERE b.id=f.batch AND b.config->>'mode'='inventory')",
                    (rid, part["sha256"]),
                )
    return dict(
        batch=batch,
        recordings=len(scope),
        phases={k: len(v) for k, v in phase_summaries.items()},
    )
