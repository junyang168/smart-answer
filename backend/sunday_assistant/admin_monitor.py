"""Independent, non-LLM incident monitor with a private durable admin outbox."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

import httpx

PREFIX = "【主日助理系統求助】"
LINK = "https://dallas-hlc.org/admin/sunday-assistant"


class AdminMonitor:
    def __init__(self, root: Path, env=None, client=None, started=None):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "admin-monitor.sqlite3"
        self.env = env if env is not None else os.environ
        self.client = client or httpx
        self.started = time.time() if started is None else started
        with self.db() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, kind TEXT, label TEXT,
                active INTEGER, count INTEGER, first_seen REAL, last_seen REAL);
              CREATE TABLE IF NOT EXISTS notices(key TEXT PRIMARY KEY, incident TEXT, phase TEXT,
                group_id TEXT, text TEXT, status TEXT, updated REAL, remote_id TEXT);
              CREATE TABLE IF NOT EXISTS monitor_kv(key TEXT PRIMARY KEY, value TEXT);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        with sqlite3.connect(self.path, timeout=2) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            yield db

    def settings_snapshot(self):
        with sqlite3.connect(f"file:{self.root / 'assistant.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
            row = db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
            settings = json.loads(row[0]) if row else {}
            row = db.execute("SELECT value FROM kv WHERE key='worker'").fetchone()
            worker = json.loads(row[0]) if row else {}
            errors = db.execute("SELECT COUNT(*) FROM messages WHERE status='error'").fetchone()[0]
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ppt_jobs'").fetchone():
                errors += db.execute("SELECT COUNT(*) FROM ppt_jobs WHERE status='error' AND day>=date('now','-1 day')").fetchone()[0]
            ppt = db.execute("SELECT value FROM kv WHERE key='ppt_readiness'").fetchone()
            if ppt and json.loads(ppt[0]).get('status') == 'error':
                errors += 1
        return settings, worker, errors

    def record_exit(self, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            row = db.execute("SELECT value FROM monitor_kv WHERE key='exits'").fetchone()
            exits = [stamp for stamp in json.loads(row[0]) if now - stamp < 600] if row else []
            db.execute("INSERT OR REPLACE INTO monitor_kv VALUES('exits',?)", (json.dumps([*exits, now][-20:]),))

    def conditions(self, worker, errors, bridge, backend_ok, now):
        # waiting_reply/review are intentionally absent: no input/clarification alerts.
        found = {}
        if errors:
            found["task_errors"] = ("內部任務執行失敗", errors, 0)
        if worker.get("status") == "error":
            found["worker"] = ("工作程序執行失敗", 1, 0)
        elif now - self.started >= 60:
            try:
                stale = now - datetime.fromisoformat(worker["at"]).timestamp() >= 300
            except (KeyError, ValueError, TypeError):
                stale = True
            if stale:
                found["worker"] = ("工作程序超過五分鐘沒有更新進度", 1, 0)
        if not bridge or bridge.get("state") != "ready" or bridge.get("error") or not bridge.get("synced"):
            found["transport"] = ("WhatsApp 連線或群消息同步持續異常", 1, 120)
        if not backend_ok:
            found["backend"] = ("網站後台連線持續異常", 1, 60)
        with self.db() as db:
            row = db.execute("SELECT value FROM monitor_kv WHERE key='exits'").fetchone()
        if row and sum(now - stamp < 600 for stamp in json.loads(row[0])) >= 3:
            found["restart_loop"] = ("助理程序十分鐘內異常退出至少三次", 1, 0)
        return found

    def observe(self, conditions, settings, now):
        with self.db() as db:
            active = {row["kind"]: row for row in db.execute("SELECT * FROM incidents WHERE active=1")}
            for kind, old in active.items():
                if kind not in conditions:
                    db.execute("UPDATE incidents SET active=0,last_seen=? WHERE id=?", (now, old["id"]))
                    # A transient problem recovered before notification; keep the audit only.
                    db.execute("UPDATE notices SET status='cancelled',updated=? WHERE incident=? AND phase='open' AND status='queued'", (now, old["id"]))
            for kind, (label, count, delay) in conditions.items():
                old = active.get(kind)
                incident = old["id"] if old else uuid.uuid4().hex[:12]
                first = old["first_seen"] if old else now
                db.execute("INSERT OR REPLACE INTO incidents VALUES(?,?,?,?,?,?,?)", (incident, kind, label, 1, count, first, now))
                if now - first >= delay:
                    text = f"{PREFIX}\n事件 {incident}：{label}（{count}項）。\n需要網站管理員協助查看執行紀錄與故障；請先核對已寫入部分，再處理或重試。這是內部故障，不是等待群員補資料。\n{LINK}"
                    self._queue(db, incident, "open", settings["admin_group"], text, now)
            # Recovery is sent once, only after the original alert was confirmed sent.
            for row in db.execute("SELECT i.* FROM incidents i JOIN notices n ON n.incident=i.id WHERE i.active=0 AND n.phase='open' AND n.status='sent'").fetchall():
                text = f"{PREFIX}\n事件 {row['id']}：上述系統故障條件已解除。請在管理頁核對處理結果；這不代表待登記資料已完成。\n{LINK}"
                self._queue(db, row["id"], "recovered", settings["admin_group"], text, now)

    @staticmethod
    def _queue(db, incident, phase, group, text, now):
        db.execute("INSERT OR IGNORE INTO notices VALUES(?,?,?,?,?,'queued',?,NULL)", (f"admin-help:{incident}:{phase}", incident, phase, group, text, now))

    def queue_test(self, settings, now=None):
        if not settings.get("admin_help_enabled") or not settings.get("admin_group"):
            raise ValueError("網站管理員求助群尚未啟用")
        now = time.time() if now is None else now
        incident = uuid.uuid4().hex[:12]
        text = f"{PREFIX}\n【上線測試，無需處理】此訊息確認內部故障可通知網站管理員；沒有實際故障，也未轉發原群內容。\n{LINK}"
        with self.db() as db:
            self._queue(db, incident, "test", settings["admin_group"], text, now)
        return f"admin-help:{incident}:test"

    def request(self, method, path, body=None):
        base = self.env.get("SUNDAY_WHATSAPP_BRIDGE_URL", "http://127.0.0.1:8766")
        return self.client.request(method, base + path, json=body, headers={"Authorization": "Bearer " + self.env["SUNDAY_ASSISTANT_SECRET"]}, timeout=3)

    def deliver(self, now):
        with self.db() as db:
            db.execute("UPDATE notices SET status='unknown' WHERE status='sending' AND updated<?", (now - 30,))
            rows = db.execute("SELECT * FROM notices WHERE status IN ('queued','unknown') ORDER BY updated LIMIT 3").fetchall()
        for row in rows:
            status = "unknown"
            remote = None
            try:
                if row["status"] == "queued":
                    with self.db() as db:
                        claimed = db.execute("UPDATE notices SET status='sending',updated=? WHERE key=? AND status='queued'", (now, row["key"])).rowcount
                    if not claimed:
                        continue
                    response = self.request("POST", "/send", {"key": row["key"], "group_id": row["group_id"], "text": row["text"]})
                    if response.status_code in {409, 503}:
                        status = "queued"  # Explicitly not submitted to WhatsApp.
                    elif response.status_code == 403:
                        status = "held"  # Destination/settings changed; no rerouting.
                    else:
                        response.raise_for_status()
                        data = response.json()
                        status = data.get("status") if data.get("status") in {"sent", "held"} else "unknown"
                        remote = data.get("message_id")
                else:
                    from urllib.parse import quote
                    response = self.request("GET", "/outbox/" + quote(row["key"], safe=""))
                    response.raise_for_status()
                    data = response.json()
                    status = "sent" if data.get("status") == "sent" else "unknown"
                    remote = data.get("message_id")
            except (httpx.ConnectError, httpx.ConnectTimeout):
                status = "queued" if row["status"] == "queued" else "unknown"
            except Exception:
                pass  # Unknown outcomes are queried, never blindly resent.
            with self.db() as db:
                db.execute("UPDATE notices SET status=?,updated=?,remote_id=? WHERE key=?", (status, now, remote, row["key"]))

    def tick(self, now=None):
        now = time.time() if now is None else now
        database_ok = True
        try:
            settings, worker, errors = self.settings_snapshot()
            cached = {key: settings.get(key) for key in ["enabled", "admin_help_enabled", "admin_group"]}
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO monitor_kv VALUES('last_settings',?)", (json.dumps(cached),))
        except Exception:
            database_ok = False
            with self.db() as db:
                row = db.execute("SELECT value FROM monitor_kv WHERE key='last_settings'").fetchone()
            settings, worker, errors = (json.loads(row[0]) if row else {}), {}, 0
        if not settings.get("admin_help_enabled") or not settings.get("admin_group"):
            return
        if settings.get("enabled"):
            try:
                response = self.request("GET", "/status")
                response.raise_for_status()
                bridge = response.json()
            except Exception:
                bridge = None
            try:
                response = self.client.request("GET", self.env["FULL_ARTICLE_SERVICE_URL"] + "/healthz", timeout=2)
                backend_ok = response.status_code == 200
            except Exception:
                backend_ok = False
            if database_ok:
                conditions = self.conditions(worker, errors, bridge, backend_ok, now)
            else:
                # Do not report another incident recovered while its state is unreadable.
                with self.db() as db:
                    conditions = {row["kind"]: (row["label"], row["count"], 0) for row in db.execute("SELECT * FROM incidents WHERE active=1")}
                conditions["database"] = ("執行資料庫持續無法讀取", 1, 60)
            self.observe(conditions, settings, now)
        self.deliver(now)
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO monitor_kv VALUES('last_check',?)", (json.dumps(now),))


def monitor_state(root):
    path = Path(root) / "admin-monitor.sqlite3"
    if not path.exists():
        return {"incidents": [], "notices": [], "last_check": None}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT value FROM monitor_kv WHERE key='last_check'").fetchone()
            return {"incidents": [dict(r) for r in db.execute("SELECT * FROM incidents ORDER BY first_seen DESC LIMIT 30")],
                    "notices": [dict(r) for r in db.execute("SELECT key,phase,status,updated FROM notices ORDER BY updated DESC LIMIT 30")],
                    "last_check": json.loads(row[0]) if row else None}
    except sqlite3.Error:
        return {"incidents": [], "notices": [], "last_check": None, "error": "監測狀態暫時無法讀取"}
