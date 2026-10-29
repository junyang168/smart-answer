"""Compatible transcript proofreading: canonical arrays + SHA-bound sidecars.

No model calls or knowledge writes. Every writer holds the same workflow lock;
prepared multi-file operations are rolled forward before another command runs.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
import re
import tempfile
import uuid


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def read(path, default=None):
    return json.loads(path.read_bytes()) if path.exists() else copy.deepcopy(default)


def atomic(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ReviewError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


class ReviewStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def folder(self, item):
        if (
            not item
            or item in (".", "..")
            or any(c in item for c in "/\\\x00")
            or len(item) > 240
        ):
            raise ReviewError(400, "無效的講道 ID")
        return self.root / "transcript_review" / item

    def enabled(self, item):
        return (self.folder(item) / "context.json").is_file()

    @contextmanager
    def lock(self, item):
        folder = self.folder(item)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "workflow.lock").open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.recover(item)
            yield

    def recover(self, item):
        for path in sorted((self.folder(item) / "operations").glob("*.prepared.json")):
            op = read(path)
            operation_id = path.name.removesuffix(".prepared.json")
            receipt_path = path.with_name(operation_id + ".json")
            if not receipt_path.exists():
                if any(e["path"] == "config/sermon.json" for e in op["files"]):
                    with (self.root / "config/.sermon-review.lock").open(
                        "a+b"
                    ) as catalog_lock:
                        fcntl.flock(catalog_lock, fcntl.LOCK_EX)
                        self.install(op)
                else:
                    self.install(op)
                atomic(
                    receipt_path,
                    {
                        k: v
                        for k, v in {**op, "state": "committed"}.items()
                        if k != "files"
                    },
                )
            self.remove_prepared(path)

    @staticmethod
    def remove_prepared(path):
        path.unlink(missing_ok=True)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def execute(self, item, operation_id, operation):
        folder = self.folder(item) / "operations"
        pending = folder / f"{operation_id}.prepared.json"
        atomic(pending, operation)
        self.install(operation)
        atomic(
            folder / f"{operation_id}.json",
            {
                k: v
                for k, v in {**operation, "state": "committed"}.items()
                if k != "files"
            },
        )
        self.remove_prepared(pending)
        return operation["result"]

    def journal_entry(self, path, value, item):
        entry = {
            "path": str(path.relative_to(self.root)),
            "before": file_hash(path),
            "value": value,
        }
        if entry["path"] == "config/sermon.json":
            entry["catalog_item"] = item
            entry["before_row"] = next(
                (r for r in read(path, []) if r["item"] == item), None
            )
        return entry

    def install(self, op):
        # Catalog operations compare and replace only their own sermon row.
        # Another sermon's successful edit must survive recovery of this one.
        for entry in op["files"]:
            target = self.root / entry["path"]
            if "catalog_item" in entry:
                item = entry["catalog_item"]
                current = next((r for r in read(target, []) if r["item"] == item), None)
                after = next(r for r in entry["value"] if r["item"] == item)
                valid = current in (entry["before_row"], after)
            else:
                valid = file_hash(target) in (entry["before"], digest(entry["value"]))
            if not valid:
                raise ReviewError(409, "偵測到未預期的檔案版本；修改已保留，請解決衝突")
        for entry in op["files"]:
            target = self.root / entry["path"]
            if "catalog_item" in entry:
                item = entry["catalog_item"]
                rows = read(target, [])
                after = next(r for r in entry["value"] if r["item"] == item)
                rows = [after if r["item"] == item else r for r in rows]
                if all(r["item"] != item for r in rows):
                    rows.append(after)
                if read(target) != rows:
                    atomic(target, rows)
            elif file_hash(target) != digest(entry["value"]):
                atomic(target, entry["value"])

    def permissions(self, actor):
        config = read(self.root / "config/config.json", {})
        if actor not in {u["id"] for u in config.get("users", [])}:
            return set()
        roles = {
            r["role"] for r in config.get("user_roles", []) if r["user"] == actor
        } or {"reader"}
        return {
            r["permission"]
            for r in config.get("role_permissions", [])
            if r["role"] in roles
        }

    def meta(self, item):
        rows = read(self.root / "config/sermon.json", [])
        row = next((m for m in rows if m["item"] == item), None)
        if row is None:
            raise ReviewError(404, "找不到講道")
        return rows, row

    def require(self, actor, item, *, write=False, epoch=None):
        permissions = self.permissions(actor)
        _, meta = self.meta(item)
        if not any(p.startswith("read") for p in permissions):
            raise ReviewError(403, "沒有讀取權限")
        if write:
            if meta.get("assigned_to") != actor or not permissions.intersection(
                {"write_owned_item", "write_any_item"}
            ):
                raise ReviewError(403, "只有當前認領人可以修改；管理員請先明確接手")
            if not epoch or epoch != meta.get("assignment_epoch"):
                raise ReviewError(409, "認領已變更，請保留修改並重新載入")
        return meta, permissions

    def load(self, item):
        context = read(self.folder(item) / "context.json")
        if context is None:
            raise ReviewError(404, "此講道尚未採用圖文審核資料")
        rows = read(self.root / "script_review" / f"{item}.json")
        if (
            file_hash(self.root / "script_review" / f"{item}.json")
            != context["script_sha256"]
        ):
            raise ReviewError(409, "逐字稿與審核資料版本不一致；不會覆蓋現有修改")
        return rows, context

    def document(self, actor, item):
        with self.lock(item):
            meta, permissions = self.require(actor, item)
            rows, context = self.load(item)
            value = self.document_value(rows, context)
            return {
                **value,
                "version": digest(value),
                "header": {
                    k: (meta.get(k) or "")[:512]
                    for k in ("title", "assigned_to_name", "assigned_to")
                },
                "actor": actor,
                "epoch": meta.get("assignment_epoch"),
                "can_edit": meta.get("assigned_to") == actor
                and bool(
                    permissions.intersection({"write_owned_item", "write_any_item"})
                ),
                "can_claim": not meta.get("assigned_to")
                and bool(permissions.intersection({"assign_item", "assign_any_item"})),
                "can_takeover": "assign_any_item" in permissions,
                "can_publish": meta.get("assigned_to") == actor
                and "assign_item" in permissions
                and "assign_any_item" not in permissions,
                "published": self.active_publication(item, meta),
                **self.target_shas(rows, context),
            }

    def document_value(self, rows, c):
        return {"script": rows, "context": c, **self.target_shas(rows, c)}

    def target_shas(self, rows, c):
        return {
            "paragraph_shas": {
                p["index"]: digest(
                    {"text": p["text"], **c["paragraph_bindings"].get(p["index"], {})}
                )
                for p in rows
            },
            "group_shas": {
                g["id"]: digest(self.group_target(c, g)) for g in c["groups"]
            },
        }

    def active_publication(self, item, meta):
        pub = read(self.root / "script_published" / f"{item}.json", {})
        return bool(
            pub
            and (
                pub.get("metadata", {}).get("review_revision") is not None
                or meta.get("status") == "published"
            )
        )

    def receipt(self, item, operation_id, actor, request=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", operation_id or ""):
            raise ReviewError(400, "缺少有效操作 ID")
        path = self.folder(item) / "operations" / f"{operation_id}.json"
        op = read(path)
        if op:
            if op["actor"] != actor:
                raise ReviewError(403, "此操作不屬於目前使用者")
            if request is not None and op["request_sha256"] != digest(request):
                raise ReviewError(409, "操作 ID 已用於不同修改")
            if op["state"] != "committed":
                raise ReviewError(409, "操作尚待恢復")
            return op["result"]
        return None

    def commit(
        self, item, actor, operation_id, request, rows, context, kind, *, extra=None
    ):
        before_script_sha256 = context["script_sha256"]
        context = copy.deepcopy(context)
        context["revision"] += 1
        context["script_sha256"] = digest(rows)
        revision = context["revision"]
        folder = self.folder(item)
        if (folder / "revisions" / f"{revision:08}.json").exists():
            raise ReviewError(409, "歷史版本已存在，不覆寫既有版本")
        snapshot = {
            "revision": revision,
            "actor": actor,
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "operation_id": operation_id,
            "script": rows,
            "context": context,
            "parent_revision_sha256": file_hash(
                folder / "revisions" / f"{revision - 1:08}.json"
            )
            if revision > 1
            else file_hash(folder / "legacy-baseline.json"),
            "before_script_sha256": before_script_sha256,
            "after_script_sha256": context["script_sha256"],
            "before_context_sha256": file_hash(folder / "context.json"),
            "after_context_sha256": digest(context),
            "change_targets": [
                {"kind": op.get("kind"), "id": op.get("id")}
                for op in request.get("operations", [])
            ],
        }
        for path, value in (extra or {}).items():
            if path == self.root / "script_published" / f"{item}.json":
                value["metadata"]["review_snapshot_sha256"] = digest(snapshot)
        result = {
            "revision": revision,
            "version": digest(self.document_value(rows, context)),
            "operation_id": operation_id,
            "revision_sha256": digest(snapshot),
            **self.target_shas(rows, context),
            "reviews": context["reviews"],
        }
        files = {
            self.root / "script_review" / f"{item}.json": rows,
            folder / "context.json": context,
            folder / "revisions" / f"{revision:08}.json": snapshot,
            **(extra or {}),
        }
        op = {
            "state": "prepared",
            "actor": actor,
            "request_sha256": digest(request),
            "result": result,
            "files": [
                self.journal_entry(path, value, item) for path, value in files.items()
            ],
        }
        return self.execute(item, operation_id, op)

    def version(self, rows, context, expected):
        if not expected or digest(self.document_value(rows, context)) != expected:
            raise ReviewError(412, "稿件已更新；本機修改已保留，請比較後再同步")

    def mutate(self, actor, item, request):
        with self.lock(item):
            self.require(actor, item)
            prior = self.receipt(item, request.get("operation_id"), actor, request)
            if prior:
                return prior
            self.require(actor, item, write=True, epoch=request.get("epoch"))
            rows, c = self.load(item)
            self.version(rows, c, request.get("version"))
            by_id = {p["index"]: p for p in rows}
            for op in request.get("operations", []):
                kind, key = op.get("kind"), op.get("id")
                if kind == "text":
                    if key not in by_id or not isinstance(op.get("text"), str):
                        raise ReviewError(400, "無效段落")
                    if by_id[key]["text"] != op["text"]:
                        by_id[key]["text"] = op["text"]
                        by_id[key]["user_id"] = actor
                        c["reviews"].pop("p:" + key, None)
                elif kind == "paragraph_review":
                    if key not in by_id or op.get("decision") not in (
                        "confirmed",
                        "question",
                        "pending",
                    ):
                        raise ReviewError(400, "無效確認")
                    target = {
                        "text": by_id[key]["text"],
                        **c["paragraph_bindings"].get(key, {}),
                    }
                    if op.get("content_sha256") != digest(target):
                        raise ReviewError(412, "確認內容已改變")
                    c["reviews"]["p:" + key] = {
                        "decision": op["decision"],
                        "sha256": digest(target),
                        "actor": actor,
                        "time": datetime.now(timezone.utc).isoformat(),
                    }
                elif kind == "ocr":
                    asset = c["visual_assets"].get(key)
                    region = next(
                        (
                            r
                            for r in (asset or {})
                            .get("content", {})
                            .get("text_regions", [])
                            if r["id"] == op.get("region_id")
                        ),
                        None,
                    )
                    if region is None or not isinstance(op.get("text"), str):
                        raise ReviewError(400, "無效 OCR 區域")
                    region["text"] = op["text"]
                    asset["visible_text"] = "\n".join(
                        r["text"] for r in asset["content"]["text_regions"]
                    )
                    asset["svg_review_status"] = "unreviewed_model_candidate"
                    for g in c["groups"]:
                        if any(
                            c["occurrences"][s]["asset_id"] == key
                            for s in g["member_ids"]
                        ):
                            c["reviews"].pop("g:" + g["id"], None)
                elif kind in ("representative", "placement", "hidden", "group_review"):
                    group = next((g for g in c["groups"] if g["id"] == key), None)
                    if group is None:
                        raise ReviewError(400, "無效圖片組")
                    if kind == "representative":
                        if op.get("slide_id") not in group["member_ids"]:
                            raise ReviewError(400, "代表圖必須來自本組原畫面")
                        group["slide_id"] = op["slide_id"]
                    elif kind == "placement":
                        if op.get("paragraph_id") not in by_id:
                            raise ReviewError(400, "無效段落位置")
                        group["before_paragraph_id"] = op["paragraph_id"]
                    elif kind == "hidden":
                        if not isinstance(op.get("hidden"), bool) or not isinstance(
                            op.get("reason"), str
                        ):
                            raise ReviewError(400, "請說明圖片處置")
                        group["hidden"] = op["hidden"]
                        group["reason"] = op["reason"]
                    else:
                        if op.get("decision") == "confirmed" and (
                            (group["hidden"] and not group.get("reason", "").strip())
                            or (
                                not group["hidden"]
                                and group.get("before_paragraph_id") not in by_id
                            )
                        ):
                            raise ReviewError(400, "請先指定有效閱讀位置或隱藏原因")
                        target = self.group_target(c, group)
                        if op.get("content_sha256") != digest(target):
                            raise ReviewError(412, "圖片核對版本已改變")
                        if op.get("decision") not in (
                            "confirmed",
                            "question",
                            "pending",
                        ):
                            raise ReviewError(400, "無效確認")
                        c["reviews"]["g:" + key] = {
                            "decision": op["decision"],
                            "sha256": digest(target),
                            "actor": actor,
                            "time": datetime.now(timezone.utc).isoformat(),
                        }
                    if kind != "group_review":
                        c["reviews"].pop("g:" + key, None)
                elif kind == "bookmark":
                    if key not in by_id:
                        raise ReviewError(400, "無效段落位置")
                    c.setdefault("bookmarks", {})[actor] = key
                elif kind == "metadata":
                    if not isinstance(op.get("value"), dict) or set(op["value"]) - {
                        "title",
                        "summary",
                        "keypoints",
                        "core_bible_verse",
                    }:
                        raise ReviewError(400, "無效講道資料")
                    if any(
                        not isinstance(v, str)
                        for k, v in op["value"].items()
                        if k != "core_bible_verse"
                    ):
                        raise ReviewError(400, "無效講道資料值")
                    if "core_bible_verse" in op["value"] and (
                        not isinstance(op["value"]["core_bible_verse"], list)
                        or any(
                            not isinstance(v, dict)
                            or set(v) - {"book", "chapter_verse", "text"}
                            or any(not isinstance(t, str) for t in v.values())
                            for v in op["value"]["core_bible_verse"]
                        )
                    ):
                        raise ReviewError(400, "無效經文")
                    c["editorial_metadata"].update(op["value"])
                else:
                    raise ReviewError(400, "不支援的修改")
            if not request.get("operations"):
                raise ReviewError(400, "沒有修改")
            return self.commit(
                item, actor, request["operation_id"], request, rows, c, "edit"
            )

    @staticmethod
    def group_target(c, g):
        return {
            "group": g,
            "assets": {
                c["occurrences"][sid]["asset_id"]: c["visual_assets"][
                    c["occurrences"][sid]["asset_id"]
                ]["content"]
                for sid in g["member_ids"]
            },
        }

    def claim(self, actor, item, request):
        with self.lock(item):
            meta, perms = self.require(actor, item)
            prior = self.receipt(item, request.get("operation_id"), actor, request)
            if prior:
                return prior
            rows, c = self.load(item)
            self.version(rows, c, request.get("version"))
            action = request.get("action")
            with (self.root / "config/.sermon-review.lock").open("a+b") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                metadata, meta = self.meta(item)
                if action == "claim":
                    if (
                        meta.get("assigned_to")
                        or not perms.intersection({"assign_item", "assign_any_item"})
                        or meta.get("status") == "in development"
                    ):
                        raise ReviewError(409, "已被認領或不能認領")
                    meta["assigned_to"] = actor
                elif action == "takeover":
                    if "assign_any_item" not in perms:
                        raise ReviewError(403, "沒有轉交權限")
                    meta["assigned_to"] = actor
                elif action == "unclaim":
                    self.require(actor, item, write=True, epoch=request.get("epoch"))
                    meta["assigned_to"] = None
                else:
                    raise ReviewError(400, "無效認領操作")
                meta["assignment_epoch"] = uuid.uuid4().hex
                config = read(self.root / "config/config.json", {})
                meta["assigned_to_name"] = next(
                    (
                        u.get("name", actor)
                        for u in config.get("users", [])
                        if u["id"] == meta.get("assigned_to")
                    ),
                    None,
                )
                meta["assigned_to_date"] = (
                    datetime.now(timezone.utc).isoformat()
                    if meta["assigned_to"]
                    else None
                )
                meta["status"] = (
                    "published"
                    if self.active_publication(item, meta)
                    else ("assigned" if meta["assigned_to"] else "ready")
                )
                return self.commit(
                    item,
                    actor,
                    request["operation_id"],
                    request,
                    rows,
                    c,
                    action,
                    extra={self.root / "config/sermon.json": metadata},
                )

    def publish(self, actor, item, request):
        with self.lock(item):
            meta, perms = self.require(actor, item)
            prior = self.receipt(item, request.get("operation_id"), actor, request)
            if prior:
                return prior
            self.require(actor, item, write=True, epoch=request.get("epoch"))
            if "assign_item" not in perms or "assign_any_item" in perms:
                raise ReviewError(403, "沒有發佈權限")
            rows, c = self.load(item)
            self.version(rows, c, request.get("version"))
            missing = []
            for p in rows:
                target = {
                    "text": p["text"],
                    **c["paragraph_bindings"].get(p["index"], {}),
                }
                review = c["reviews"].get("p:" + p["index"], {})
                if review.get("decision") != "confirmed" or review.get(
                    "sha256"
                ) != digest(target):
                    missing.append("p:" + p["index"])
            for g in c["groups"]:
                review = c["reviews"].get("g:" + g["id"], {})
                if review.get("decision") != "confirmed" or review.get(
                    "sha256"
                ) != digest(self.group_target(c, g)):
                    missing.append("g:" + g["id"])
            if missing:
                raise ReviewError(
                    409,
                    f"還有 {sum(v.startswith('p:') for v in missing)} 段口述、{sum(v.startswith('g:') for v in missing)} 組投影片未確認。請先完成核對。",
                )
            from backend.pipeline.slide_visual_evidence import render_svg

            script = copy.deepcopy(rows)
            for row in script:
                binding = c["paragraph_bindings"][row["index"]]
                row.update(
                    start_time=binding["start_ms"] / 1000
                    if binding["start_ms"] is not None
                    else None,
                    end_time=binding["end_ms"] / 1000
                    if binding["end_ms"] is not None
                    else None,
                )
                visuals = []
                for g in c["groups"]:
                    if g["hidden"] or g["before_paragraph_id"] != row["index"]:
                        continue
                    asset_id = c["occurrences"][g["slide_id"]]["asset_id"]
                    a = c["visual_assets"][asset_id]
                    content = copy.deepcopy(a["content"])
                    content["text_regions"] = [
                        r for r in content["text_regions"] if r["text"].strip()
                    ]
                    regions = {r["id"] for r in content["text_regions"]}
                    content["edges"] = [
                        e
                        for e in content["edges"]
                        if all(
                            e[k] is None or e[k] in regions
                            for k in ("from_region", "to_region")
                        )
                    ]
                    try:
                        svg = render_svg(
                            content,
                            a["width"],
                            a["height"],
                            a["image_sha256"],
                            allow_text_only=True,
                        )
                    except ValueError as exc:
                        raise ReviewError(
                            422,
                            "投影片文字包含無法輸出的內容，請檢查文字或先隱藏該圖片。",
                        ) from exc
                    image_url = f"/api/sc_api/review_public/{quote(item, safe='')}/assets/{asset_id}"
                    visuals.append(
                        f'<figure data-transcript-slide="v1" data-source-image-sha256="{a["image_sha256"]}">\n<img src="{image_url}" alt="投影片（非口述）" />\n<figcaption>投影片（非口述）</figcaption>\n<details><summary>投影片文字（機器重建，非口述）</summary>\n{svg}\n</details>\n</figure>'
                    )
                if visuals:
                    row["text"] = "\n\n".join(visuals) + "\n\n" + row["text"]
            revision = c["revision"] + 1
            pub = {
                "metadata": {
                    **c.get("source_metadata", {}),
                    **c["editorial_metadata"],
                    "author": c.get("source_metadata", {}).get("author_name")
                    or c.get("source_metadata", {}).get("author")
                    or "王守仁",
                    "reviewed_by": actor,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "status": "published",
                    "title": c["editorial_metadata"].get("title") or item,
                    "review_revision": revision,
                    "review_operation_id": request["operation_id"],
                    "review_context_path": f"transcript_review/{item}/revisions/{revision:08}.json",
                    "source_video_sha256": c["source_video_sha256"],
                    "visual_source_attestations": [],
                    "visual_admission_status": "candidate_requires_source_fidelity_review",
                },
                "script": script,
            }
            with (self.root / "config/.sermon-review.lock").open("a+b") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                metadata, meta = self.meta(item)
                meta.update(c["editorial_metadata"])
                meta["status"] = "published"
                meta["published_date"] = datetime.now(timezone.utc).isoformat()
                return self.commit(
                    item,
                    actor,
                    request["operation_id"],
                    request,
                    rows,
                    c,
                    "publish",
                    extra={
                        self.root / "script_published" / f"{item}.json": pub,
                        self.root / "config/sermon.json": metadata,
                    },
                )

    def history(self, actor, item, *, before=10**12, limit=30):
        with self.lock(item):
            self.require(actor, item)
            self.load(item)
            committed = {}
            for path in (self.folder(item) / "operations").glob("*.json"):
                op = read(path)
                if (
                    op["state"] == "committed"
                    and op["result"].get("revision") is not None
                ):
                    committed[op["result"]["revision"]] = op["result"].get(
                        "revision_sha256"
                    )
            result = []
            for path in sorted(
                (self.folder(item) / "revisions").glob("*.json"), reverse=True
            ):
                revision = int(path.stem)
                if revision >= before or revision not in committed:
                    continue
                r = read(path)
                if committed[revision] and digest(r) != committed[revision]:
                    raise ReviewError(409, "歷史版本雜湊不一致，不顯示被改動的紀錄")
                result.append(
                    {
                        k: r[k]
                        for k in ("revision", "actor", "time", "kind", "operation_id")
                    }
                )
                if len(result) >= min(max(limit, 1), 100):
                    break
            return result

    def published_snapshot(self, item):
        pub = read(self.root / "script_published" / f"{item}.json", {})
        _, meta = self.meta(item)
        if not self.active_publication(item, meta) or "review_revision" not in pub.get(
            "metadata", {}
        ):
            raise ReviewError(404, "尚未發佈圖文版本")
        path = (
            self.folder(item)
            / "revisions"
            / f"{int(pub['metadata']['review_revision']):08}.json"
        )
        expected = pub["metadata"].get("review_snapshot_sha256")
        if expected and file_hash(path) != expected:
            raise ReviewError(409, "已發佈版本的來源快照不一致")
        snapshot = read(path)
        if snapshot is None:
            raise ReviewError(409, "找不到已發佈版本的來源快照")
        return pub, snapshot

    def revision(self, actor, item, revision):
        allowed = {
            r["revision"]
            for r in self.history(actor, item, before=revision + 1, limit=1)
        }
        if revision not in allowed:
            raise ReviewError(404, "找不到已提交的歷史版本")
        return read(self.folder(item) / "revisions" / f"{revision:08}.json")

    def asset_path(self, item, asset_id, *, actor=None, public=False):
        if public:
            self.meta(item)
        with self.lock(item):
            if public:
                _, snapshot = self.published_snapshot(item)
                c = snapshot["context"]
            else:
                self.require(actor, item)
                _, c = self.load(item)
            asset = c["visual_assets"].get(asset_id)
            if asset is None:
                raise ReviewError(404, "找不到原圖")
            path = self.root / asset["image"]
            if file_hash(path) != asset["image_sha256"]:
                raise ReviewError(409, "原圖版本不一致")
            return path

    def candidate_path(self, item):
        self.folder(item)  # Validate before constructing any path.
        return self.root / "transcript_candidates" / f"{item}.json"

    def register_candidate(self, item, source, *, expected_script_sha256):
        source = Path(source).resolve()
        record = read(source / "full_transcript.json")
        layout = read(source / "reading-layout/layout.json")
        if (
            not record
            or not layout
            or layout["source_record_sha256"] != digest_compatible(record)
        ):
            raise ReviewError(409, "候選檔案不完整或版本不符")
        with self.lock(item):
            self.meta(item)
            if self.enabled(item):
                raise ReviewError(409, "已採用的稿件不能重新登記")
            if (
                file_hash(self.root / "script_review" / f"{item}.json")
                != expected_script_sha256
            ):
                raise ReviewError(412, "原稿已更新")
            value = {
                "source": str(source),
                "source_record_sha256": digest(record),
                "source_input_file_sha256": file_hash(source / "full_transcript.json"),
                "source_record_path": f"transcript_review/{item}/source-record.json",
                "layout_sha256": file_hash(source / "reading-layout/layout.json"),
                "expected_script_sha256": expected_script_sha256,
                "source_video_sha256": record["source_sha256"],
            }
            path = self.candidate_path(item)
            if path.exists() and read(path) != value:
                raise ReviewError(409, "已有不同候選，請先解決版本差異")
            atomic(path, value)

    def candidate(self, actor, item):
        with self.lock(item):
            meta, perms = self.require(actor, item)
            candidate = read(self.candidate_path(item))
            if not candidate or self.enabled(item):
                raise ReviewError(404, "沒有待採用候選")
            return {
                "header": meta,
                "actor": actor,
                "epoch": meta.get("assignment_epoch"),
                "version": digest({"candidate": candidate, "header": meta}),
                "can_claim": not meta.get("assigned_to")
                and bool(perms.intersection({"assign_item", "assign_any_item"})),
                "can_adopt": meta.get("assigned_to") == actor
                and bool(perms.intersection({"write_owned_item", "write_any_item"})),
                "can_takeover": "assign_any_item" in perms,
                "source_video_sha256": candidate["source_video_sha256"],
            }

    def candidate_command(self, actor, item, request):
        # Candidate commands also use the workflow journal. Adoption never accepts
        # a client-supplied filesystem path or replaces an existing reviewed draft.
        if request.get("action") == "adopt":
            candidate = read(self.candidate_path(item))
            if not candidate:
                raise ReviewError(404, "沒有候選")
            with self.lock(item):
                self.require(actor, item)
                prior = self.receipt(item, request.get("operation_id"), actor, request)
                if prior:
                    return prior
            return self.import_candidate(
                item,
                Path(candidate["source"]),
                expected_script_sha256=candidate["expected_script_sha256"],
                actor=actor,
                request=request,
            )
        with self.lock(item):
            meta, perms = self.require(actor, item)
            prior = self.receipt(item, request.get("operation_id"), actor, request)
            if prior:
                return prior
            candidate = read(self.candidate_path(item))
            if not candidate or self.enabled(item):
                raise ReviewError(409, "候選狀態已改變")
            with (self.root / "config/.sermon-review.lock").open("a+b") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                metadata, meta = self.meta(item)
                if request.get("version") != digest(
                    {"candidate": candidate, "header": meta}
                ):
                    raise ReviewError(412, "認領狀態已更新")
                action = request.get("action")
                if action == "claim":
                    if (
                        meta.get("assigned_to")
                        or not perms.intersection({"assign_item", "assign_any_item"})
                        or meta.get("status") == "in development"
                    ):
                        raise ReviewError(409, "不能認領")
                    meta["assigned_to"] = actor
                elif action == "takeover":
                    if "assign_any_item" not in perms:
                        raise ReviewError(403, "沒有轉交權限")
                    meta["assigned_to"] = actor
                elif action == "unclaim":
                    self.require(actor, item, write=True, epoch=request.get("epoch"))
                    meta["assigned_to"] = None
                else:
                    raise ReviewError(400, "無效候選操作")
                meta["assignment_epoch"] = uuid.uuid4().hex
                config = read(self.root / "config/config.json", {})
                meta["assigned_to_name"] = next(
                    (
                        u.get("name", actor)
                        for u in config.get("users", [])
                        if u["id"] == meta.get("assigned_to")
                    ),
                    None,
                )
                meta["status"] = "assigned" if meta["assigned_to"] else "ready"
                target = self.root / "config/sermon.json"
                result = {"version": digest({"candidate": candidate, "header": meta})}
                op = {
                    "state": "prepared",
                    "actor": actor,
                    "request_sha256": digest(request),
                    "result": result,
                    "files": [self.journal_entry(target, metadata, item)],
                }
                return self.execute(item, request["operation_id"], op)

    def import_candidate(
        self, item, source: Path, *, expected_script_sha256, actor=None, request=None
    ):
        """Explicit operator import, never a background overwrite. Source folder is local/trusted."""
        import shutil
        from backend.pipeline.slide_change_pilot import read_json

        record = read_json(source / "full_transcript.json")
        layout = read_json(source / "reading-layout/layout.json")
        if layout["source_record_sha256"] != digest_compatible(record):
            raise ReviewError(409, "排版與來源版本不同")
        with self.lock(item):
            if actor:
                self.require(actor, item)
                prior = self.receipt(item, request.get("operation_id"), actor, request)
                if prior:
                    return prior
                meta, _ = self.require(
                    actor, item, write=True, epoch=request.get("epoch")
                )
                candidate = read(self.candidate_path(item))
                if request.get("version") != digest(
                    {"candidate": candidate, "header": meta}
                ):
                    raise ReviewError(412, "候選或認領狀態已改變")
                if (
                    file_hash(source / "full_transcript.json")
                    != candidate["source_record_sha256"]
                    or file_hash(source / "reading-layout/layout.json")
                    != candidate["layout_sha256"]
                ):
                    raise ReviewError(409, "候選已變更，拒絕採用")
            if self.enabled(item):
                raise ReviewError(409, "候選已採用，不覆寫人工審核稿")
            _, meta = self.meta(item)
            path = self.root / "script_review" / f"{item}.json"
            if file_hash(path) != expected_script_sha256:
                raise ReviewError(412, "原稿已更新")
            if (meta.get("assigned_to") and not actor) or meta.get(
                "status"
            ) == "published":
                raise ReviewError(409, "已認領或已發佈，不取代人工稿")
            baseline = read(path, [])
            patched = read(self.root / "script_patched" / f"{item}.json", baseline)
            if baseline != patched:
                raise ReviewError(409, "已有人工修改，拒絕整份取代")
            folder = self.folder(item)
            atomic(
                folder / "legacy-baseline.json",
                {
                    "script": baseline,
                    "patched": patched,
                    "timeline": read(self.root / "script" / f"{item}.json"),
                },
            )
            mapping = {p["id"]: str(i + 1) for i, p in enumerate(record["paragraphs"])}
            rows = [
                {
                    "index": mapping[p["id"]],
                    "end_index": mapping[p["id"]],
                    "type": "content",
                    "text": p["text"],
                }
                for p in record["paragraphs"]
            ]
            bindings = {
                mapping[p["id"]]: {
                    "source_paragraph_id": p["id"],
                    "start_ms": p["start_ms"],
                    "end_ms": p["end_ms"],
                }
                for p in record["paragraphs"]
            }
            if any(
                p["start_ms"] is None or p["end_ms"] is None
                for p in record["paragraphs"]
            ):
                raise ReviewError(400, "存在時間未定段落")

            def ts(ms):
                ms = int(ms)
                return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

            timeline = {
                "entries": [
                    {
                        "index": mapping[p["id"]],
                        "start": ts(p["start_ms"]),
                        "end": ts(p["end_ms"]),
                        "text": p["text"],
                    }
                    for p in record["paragraphs"]
                ]
            }
            assets = {}
            for a in record["visual_assets"]:
                image = Path(a["original_image"])
                if file_hash(image) != a["image_sha256"]:
                    raise ReviewError(409, "原圖雜湊不同")
                relative = f"transcript_review/{item}/assets/{a['asset_id']}.png"
                dest = self.root / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image, dest)
                assets[a["asset_id"]] = {
                    "image": relative,
                    "image_sha256": a["image_sha256"],
                    "visible_text": a["visible_text"],
                    "content": a["content"],
                    "width": a["image_width"],
                    "height": a["image_height"],
                    "svg_review_status": "unreviewed_model_candidate",
                }
            occurrences = {
                s["id"]: {**s, "asset_id": "a" + s["slide_sha256"]}
                for s in record["slides"]
            }
            groups = []
            for g in layout["groups"]:
                for i, rep in enumerate(g["representatives"]):
                    groups.append(
                        {
                            "id": g["id"] + ("-" + str(i) if i else ""),
                            "member_ids": g["member_ids"],
                            "slide_id": rep["slide_id"],
                            "before_paragraph_id": mapping.get(
                                rep["before_paragraph_id"]
                            ),
                            "hidden": rep["before_paragraph_id"] is None,
                            "reason": g["reason"],
                            "needs_review": rep["needs_review"],
                        }
                    )
            c = {
                "schema_version": "transcript-review-context.v1",
                "revision": 0,
                "script_sha256": digest(rows),
                "source_video_sha256": record["source_sha256"],
                "source_record_sha256": digest(record),
                "source_input_file_sha256": file_hash(source / "full_transcript.json"),
                "source_record_path": f"transcript_review/{item}/source-record.json",
                "paragraph_bindings": bindings,
                "editorial_metadata": {
                    **{k: meta.get(k) or "" for k in ("title", "summary", "keypoints")},
                    "core_bible_verse": meta.get("core_bible_verse") or [],
                },
                "source_metadata": {
                    k: meta.get(k)
                    for k in (
                        "item",
                        "author",
                        "author_name",
                        "type",
                        "deliver_date",
                        "theme",
                        "source",
                        "series_id",
                    )
                },
                "visual_assets": assets,
                "occurrences": occurrences,
                "groups": groups,
                "reviews": {},
                "visibility": record["visibility"],
            }
            media = source / "source.mp4"
            dest = folder / "source.mp4"
            if not media.exists():
                raise ReviewError(400, "缺少原片，請先保存來源影片")
            if file_hash(media) != record["source_sha256"]:
                raise ReviewError(409, "影片與候選來源雜湊不同")
            if (
                not dest.exists()
                or dest.is_symlink()
                or file_hash(dest) != record["source_sha256"]
            ):
                # Keep a durable media entry; deleting a processing directory must
                # not break a claimed review. Hard-link on one volume, copy otherwise.
                temporary = folder / (".media-" + uuid.uuid4().hex)
                try:
                    try:
                        os.link(media.resolve(), temporary)
                    except OSError:
                        shutil.copyfile(media, temporary)
                    if file_hash(temporary) != record["source_sha256"]:
                        raise ReviewError(409, "保存的影片版本不符")
                    with temporary.open("rb") as stream:
                        os.fsync(stream.fileno())
                    os.replace(temporary, dest)
                finally:
                    temporary.unlink(missing_ok=True)
            return self.commit(
                item,
                actor or "operator-import",
                request["operation_id"] if request else uuid.uuid4().hex,
                request
                or {
                    "source": c["source_record_sha256"],
                    "expected": expected_script_sha256,
                },
                rows,
                c,
                "adopt",
                extra={
                    folder / "source-record.json": record,
                    folder / "source-layout.json": layout,
                    self.root / "script" / f"{item}.json": timeline,
                    self.root / "script_patched" / f"{item}.json": rows,
                },
            )


def digest_compatible(value):
    # Match the existing pipeline's canonical object hash, not file serialization.
    from backend.pipeline.slide_change_pilot import object_sha

    return object_sha(value)
