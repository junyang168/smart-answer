from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3

from .models import Settings


def now_text():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "assistant.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                    received_at TEXT NOT NULL, result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS proposals (
                    id INTEGER PRIMARY KEY, message_id TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT, updated_at TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS proposal_message ON proposals(message_id);
                CREATE TABLE IF NOT EXISTS ownership (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, sent_at TEXT NOT NULL, message_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (
                    key TEXT PRIMARY KEY, group_id TEXT NOT NULL, text TEXT NOT NULL,
                    status TEXT NOT NULL, remote_id TEXT, error TEXT, updated_at TEXT NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            yield db

    @contextmanager
    def work_lock(self, blocking=True):
        # Every worker/manual action uses this OS lock; abandoned jobs are recoverable.
        with (self.root / "work.lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def get(self, key, default=None):
        with self.db() as db:
            r = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, dump(value)))

    def settings(self):
        return Settings.model_validate(self.get("settings", {}))

    def receive(self, message):
        settings = self.settings()
        if message.group_id not in {settings.availability_group, settings.service_group}:
            raise ValueError("此群未設定為資料來源")
        if message.text.startswith(("【主日資料提醒】", "【請假登記回覆】")):
            return False
        with self.db() as db:
            cur = db.execute("INSERT OR IGNORE INTO messages(id,payload,status,received_at) VALUES (?,?,?,?)",
                             (message.message_id, message.model_dump_json(), "queued", now_text()))
            return cur.rowcount > 0

    def messages(self, limit=100):
        with self.db() as db:
            rows = db.execute("SELECT * FROM messages ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"]),
                 "result": json.loads(r["result"]) if r["result"] else None} for r in rows]

    def proposals(self):
        with self.db() as db:
            rows = db.execute("SELECT * FROM proposals ORDER BY id DESC LIMIT 100").fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def outbox(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM outbox ORDER BY updated_at DESC LIMIT 30")]
