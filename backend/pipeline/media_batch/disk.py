"""Short transactional reservations for future stage writes, shared across batches.

These are conservative admission budgets, not filesystem quotas. Existing bytes
are already reflected by disk_usage; reservations cover additional writes only.
No timer frees a reservation owned by a possibly live descendant.
"""

from pathlib import Path
import shutil
from .runtime import WaitingCapacity, ensure_storage
from .store import Conflict

GIB = 2**30
MIB = 2**20


def anchor(path):
    path = Path(path).resolve()
    while not path.exists():
        path = path.parent
    return path


def requirements(task):
    payload = task["payload"]
    cfg, info, stage = payload["config"], payload["info"], task["stage"]
    seconds = float(info["duration"])
    pcm = int(seconds * 32000)
    overhead = 64 * MIB
    work = {
        "prepare": pcm * 4,
        "speech": int(min(seconds, 900) * 32000) * 12,
        "final-align": pcm * 4,
        "finish": pcm * 10,
        "speakers": pcm * 6,
    }.get(stage, 0)
    if stage == "visuals" and info["video"]:
        # Two 960px JPEG samples per second plus selected images/OCR. This
        # is an admission estimate; the worker also monitors actual free space.
        height = int(960 * info.get("height", 720) / max(info.get("width", 1280), 1))
        work = int(seconds * 2 + 1) * 960 * height * 3 // 2 + 256 * MIB
    requests = [(cfg["artifact_root"], work + overhead)]
    if stage == "media":
        size = (
            max(info["bytes"] * 2, int(seconds * 600000))
            if info["video"]
            else int(seconds * 16000)
        )
        requests.append((cfg["media_root"], size + overhead))
    if stage == "handoff":
        # Cross-volume source installation needs a real copy. Same-volume
        # imports use a hardlink; selected assets still need their own bytes.
        source = Path(info["source"])
        review = anchor(cfg["review_root"])
        source_copy = (
            0
            if source.is_file() and source.stat().st_dev == review.stat().st_dev
            else info["bytes"]
        )
        requests.append((cfg["review_root"], source_copy + pcm + overhead))
    grouped = {}
    for root, amount in requests:
        path = anchor(root)
        device = str(path.stat().st_dev)
        floor = int(cfg.get("ssd_reserve_gib", 80) * GIB)
        if not str(path).startswith("/Volumes/"):
            floor = int(cfg.get("internal_reserve_gib", 10) * GIB)
        row = grouped.setdefault(
            device,
            dict(
                device=device,
                path=str(path),
                bytes=0,
                floor_bytes=floor,
                limit_bytes=int(cfg.get("working_set_gib", 15) * GIB),
            ),
        )
        row["bytes"] += amount
        row["floor_bytes"] = max(row["floor_bytes"], floor)
    return list(grouped.values())


def reserve(store, task, requests=None):
    ensure_storage(task["payload"]["config"])
    requests = requirements(task) if requests is None else requests
    with store.connection() as c:
        # Every participant takes device locks in the same order. Disk checks
        # and inserts are one short transaction across all required volumes.
        for row in sorted(requests, key=lambda r: r["device"]):
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("media-disk:" + row["device"],),
            )
        current = c.execute(
            "SELECT state,attempt,worker FROM media_ingest.tasks WHERE id=%s FOR UPDATE",
            (task["id"],),
        ).fetchone()
        if (
            not current
            or current["state"] != "running"
            or (current["attempt"], current["worker"])
            != (task["attempt"], task["worker"])
        ):
            raise Conflict("Disk reservation lost task ownership")
        for row in requests:
            old = c.execute(
                "SELECT * FROM media_ingest.disk_reservations WHERE task=%s AND attempt=%s AND device=%s",
                (task["id"], task["attempt"], row["device"]),
            ).fetchone()
            if old:
                if any(
                    old[k] != row[k]
                    for k in ("path", "bytes", "floor_bytes", "limit_bytes")
                ):
                    raise Conflict("Disk reservation changed within an attempt")
                continue
            others = c.execute(
                "SELECT COALESCE(sum(bytes),0) AS bytes,max(floor_bytes) AS floor,min(limit_bytes) AS ceiling FROM media_ingest.disk_reservations WHERE device=%s",
                (row["device"],),
            ).fetchone()
            total = row["bytes"] + int(others["bytes"])
            floor = max(row["floor_bytes"], others["floor"] or 0)
            ceiling = min(row["limit_bytes"], others["ceiling"] or row["limit_bytes"])
            free = shutil.disk_usage(row["path"]).free
            if row["bytes"] > row["limit_bytes"]:
                raise WaitingCapacity(
                    f"本階段預估需 {row['bytes']/GIB:.2f} GiB，超過設定的 {row['limit_bytes']/GIB:.2f} GiB 寫入預算；需調整明示上限或縮小來源"
                )
            if total > ceiling or free - total < floor:
                raise WaitingCapacity(
                    f"等待磁碟空間：需預約 {row['bytes']/GIB:.2f} GiB；其他工作已預約 {int(others['bytes'])/GIB:.2f} GiB"
                )
            c.execute(
                "INSERT INTO media_ingest.disk_reservations(task,attempt,device,path,bytes,floor_bytes,limit_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    task["id"],
                    task["attempt"],
                    row["device"],
                    row["path"],
                    row["bytes"],
                    row["floor_bytes"],
                    row["limit_bytes"],
                ),
            )


def release(store, task):
    with store.connection() as c:
        c.execute(
            "DELETE FROM media_ingest.disk_reservations WHERE task=%s AND attempt=%s",
            (task["id"], task["attempt"]),
        )


def status(store, cfg):
    ensure_storage(cfg)
    result = {}
    with store.connection() as c:
        for name in ("artifact_root", "media_root"):
            path = anchor(cfg[name])
            row = c.execute(
                "SELECT COALESCE(sum(bytes),0) AS reserved_bytes FROM media_ingest.disk_reservations WHERE device=%s",
                (str(path.stat().st_dev),),
            ).fetchone()
            result[name] = dict(
                available_bytes=shutil.disk_usage(path).free,
                reserved_bytes=int(row["reserved_bytes"]),
            )
    return result


def check_floor(cfg):
    ensure_storage(cfg)
    for name in ("artifact_root", "media_root", "review_root"):
        path = anchor(cfg[name])
        floor = (
            cfg.get("ssd_reserve_gib", 80)
            if str(path).startswith("/Volumes/")
            else cfg.get("internal_reserve_gib", 10)
        )
        if shutil.disk_usage(path).free < floor * GIB:
            raise WaitingCapacity("磁碟已到保留空間下限，工作已停止，等待釋放空間")
