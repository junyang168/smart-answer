from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest


@pytest.mark.parametrize("failure", [None, "auth", "bridge", "worker", "stale", "old_activation", "monitor_missing", "monitor_stale"])
def test_health_requires_authenticated_listener_and_current_worker(tmp_path, failure):
    path = Path(__file__).resolve().parents[2] / "scripts/check-sunday-assistant.py"
    spec = importlib.util.spec_from_file_location("sunday_health", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer correct-secret":
                self.send_error(401)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"state": "error" if failure == "bridge" else "ready", "error": None}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    folder = tmp_path / "sunday-assistant"
    folder.mkdir()
    stamp = time.time() - (400 if failure == "stale" else 0)
    worker = {"at": datetime.fromtimestamp(stamp, timezone.utc).isoformat(), "status": "error" if failure == "worker" else "paused"}
    with sqlite3.connect(folder / "assistant.sqlite3") as db:
        db.execute("CREATE TABLE kv(key TEXT, value TEXT)")
        db.execute("INSERT INTO kv VALUES('worker',?)", (json.dumps(worker),))
        db.execute("INSERT INTO kv VALUES('settings',?)", (json.dumps({"admin_help_enabled": True}),))
    if failure != "monitor_missing":
        with sqlite3.connect(folder / "admin-monitor.sqlite3") as db:
            db.execute("CREATE TABLE monitor_kv(key TEXT,value TEXT)")
            db.execute("INSERT INTO monitor_kv VALUES('last_check',?)", (json.dumps(0 if failure == "monitor_stale" else time.time()),))
    config = {"SUNDAY_WHATSAPP_BRIDGE_URL": f"http://127.0.0.1:{server.server_port}", "SUNDAY_ASSISTANT_SECRET": "wrong" if failure == "auth" else "correct-secret", "DATA_BASE_DIR": str(tmp_path)}
    try:
        since = stamp + 10 if failure == "old_activation" else stamp - 1
        if failure:
            with pytest.raises(Exception):
                module.check(config, since)
        else:
            module.check(config, since)
    finally:
        server.shutdown()
        server.server_close()
