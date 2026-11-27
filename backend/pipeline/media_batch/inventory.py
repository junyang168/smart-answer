"""Read copy receipts into a file inventory; never downloads, hashes or transcribes media."""

import json
import re
from pathlib import Path
from psycopg.types.json import Jsonb
from .planner import EXTENSIONS


def refresh(store, batch):
    with store.connection() as c:
        entry = c.execute(
            "SELECT config,inventory_catalog_root FROM media_ingest.batches WHERE id=%s",
            (batch,),
        ).fetchone()
        cfg = {**entry["config"], "catalog_root": entry["inventory_catalog_root"]}
    manifest = json.loads(Path(cfg["inventory_manifest"]).read_text())
    root = Path(manifest["destination"]).resolve()
    # The copy owner appends one JSON line at a time. Ignore an incomplete
    # trailing line and read it next time; never change that owner's files.
    receipts = {}
    lines = Path(cfg["inventory_receipts"]).read_text().splitlines()
    for n, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if n == len(lines) - 1:
                break
            raise ValueError("Invalid copy receipt")
        if row.get("path"):
            receipts.setdefault(row["path"], []).append(row)
    rows, seen = [], {}
    from .existing import find

    matches = find(
        cfg.get("catalog_root"),
        [r["path"] for r in manifest["files"]],
        require_catalog=bool(cfg.get("catalog_root")),
    )
    for item in sorted(manifest["files"], key=lambda item: item["path"]):
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Inventory path escaped its folder")
        path = root / relative
        if root not in path.resolve().parents:
            raise ValueError("Inventory symlink escaped its folder")
        size = int(item["bytes"])
        if size < 0:
            raise ValueError("Negative inventory size")
        proofs = receipts.get(str(relative), [])
        hashes = {
            r.get("sha256")
            for r in proofs
            if r.get("bytes") == size
            and re.fullmatch(r"[0-9a-f]{64}", str(r.get("sha256", "")))
        }
        sha = next(iter(hashes)) if len(hashes) == 1 else None
        state, reason = "waiting_download", "尚無完整複製回執，等待下載／核對"
        if len(hashes) > 1:
            state, reason = (
                "needs_resolution",
                "同一路徑的複製回執 hash 不一致，需核實來源",
            )
        elif sha:
            if not path.is_file():
                reason = "有複製回執，但 SSD 檔案目前不可用"
            elif (
                path.stat().st_size != size
                or getattr(path.stat(), "st_flags", 0) & 0x40000000
            ):
                state, reason = "needs_resolution", "SSD 檔案大小／下載狀態與回執不符"
            else:
                state, reason = (
                    "ready_to_plan",
                    "複製回執已記錄，等待轉錄前來源核對及查重",
                )
        duplicate = None
        if relative.suffix.lower() not in EXTENSIONS:
            state, reason = "excluded", "非支援的影音檔，不需轉錄"
        elif state == "ready_to_plan":
            if sha in seen:
                state, reason, duplicate = (
                    "duplicate",
                    "複製回執的 bytes hash 相同，可共用同一來源",
                    seen[sha],
                )
            else:
                seen[sha] = str(relative)
                if relative.suffix.lower() in (".mts", ".m2ts"):
                    state, reason = (
                        "needs_assembly",
                        "相機分段已複製，等待確認講次、順序及時間連續性",
                    )
                if any(
                    name in str(relative).lower()
                    for name in ("andrew wang", "felix liu")
                ):
                    state, reason = (
                        "needs_scope_review",
                        "檔名顯示其他講員，等待確認是否納入教授轉錄",
                    )
        matched = matches.get(str(relative), {})
        if matched and state not in ("duplicate", "excluded", "needs_resolution"):
            state = "existing_transcript"
            reason = (
                "網站已有"
                + ("發布稿" if matched["published"] else "逐字稿（尚未發布）")
                + "，不自動重轉錄；媒體身份於排程前另行核對"
            )
        rows.append(
            (batch, str(relative), sha, size, state, reason, duplicate, Jsonb(matched))
        )
    with store.connection() as c:
        for row in rows:
            c.execute(
                """INSERT INTO media_ingest.batch_files(batch,path,sha,bytes,state,reason,duplicate_of,catalog_match)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(batch,path) DO UPDATE SET
              catalog_match=excluded.catalog_match,
              sha=excluded.sha,bytes=excluded.bytes,state=excluded.state,reason=excluded.reason,duplicate_of=excluded.duplicate_of,
              updated_at=CASE WHEN (batch_files.sha,batch_files.bytes,batch_files.state,batch_files.reason,batch_files.duplicate_of,batch_files.catalog_match)
              IS DISTINCT FROM (excluded.sha,excluded.bytes,excluded.state,excluded.reason,excluded.duplicate_of,excluded.catalog_match) THEN now() ELSE batch_files.updated_at END""",
                row,
            )
        c.execute(
            "UPDATE media_ingest.batches SET inventory_at=now(),inventory_error=NULL WHERE id=%s",
            (batch,),
        )
    return len(rows)


def create(store, manifest, receipts, config):
    manifest, receipts = Path(manifest).resolve(), Path(receipts).resolve()
    value = json.loads(manifest.read_text())
    catalog_root = config.get("catalog_root")
    cfg = {
        **{k: v for k, v in config.items() if k != "catalog_root"},
        "mode": "inventory",
        "folder": value["destination"],
        "inventory_manifest": str(manifest),
        "inventory_receipts": str(receipts),
    }
    batch = store.batch(cfg)
    if catalog_root:
        with store.connection() as c:
            c.execute(
                "UPDATE media_ingest.batches SET inventory_catalog_root=%s WHERE id=%s",
                (str(Path(catalog_root).resolve()), batch),
            )
    refresh(store, batch)
    return batch


def watch(store, batch, drain=False):
    import time

    while True:
        with store.connection() as c:
            desired = c.execute(
                "SELECT desired FROM media_ingest.batches WHERE id=%s", (batch,)
            ).fetchone()["desired"]
        if desired == "running":
            try:
                refresh(store, batch)
            except (OSError, ValueError, KeyError) as error:
                with store.connection() as c:
                    c.execute(
                        "UPDATE media_ingest.batches SET inventory_error=%s WHERE id=%s",
                        ("無法同步來源清單：" + type(error).__name__, batch),
                    )
        if drain:
            return store.snapshot(batch)
        time.sleep(15)
