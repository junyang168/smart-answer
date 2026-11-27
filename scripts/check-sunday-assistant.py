#!/usr/bin/env python3
"""Verify the authenticated bridge listener and a fresh worker heartbeat."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
from urllib.request import Request, urlopen

from dotenv import dotenv_values


def check(config, since):
    url = config["SUNDAY_WHATSAPP_BRIDGE_URL"] + "/status"
    request = Request(url, headers={"Authorization": "Bearer " + config["SUNDAY_ASSISTANT_SECRET"]})
    with urlopen(request, timeout=2) as response:
        bridge = json.load(response)
    if bridge.get("state") not in {"ready", "qr"} or bridge.get("error"):
        raise ValueError("WhatsApp bridge has not reached a healthy connection/login state")
    path = Path(config["DATA_BASE_DIR"]) / "sunday-assistant/assistant.sqlite3"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        row = db.execute("SELECT value FROM kv WHERE key='worker'").fetchone()
        settings_row = db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
    worker = json.loads(row[0]) if row else {}
    timestamp = datetime.fromisoformat(worker["at"]).timestamp()
    if timestamp < since or time.time() - timestamp > 180 or worker.get("status") in {"error", "stale"}:
        raise ValueError("Sunday worker has no healthy heartbeat since service activation")
    settings = json.loads(settings_row[0]) if settings_row else {}
    if settings.get("admin_help_enabled"):
        monitor = Path(config["DATA_BASE_DIR"]) / "sunday-assistant/admin-monitor.sqlite3"
        with sqlite3.connect(f"file:{monitor}?mode=ro", uri=True) as db:
            row = db.execute("SELECT value FROM monitor_kv WHERE key='last_check'").fetchone()
        stamp = json.loads(row[0]) if row else 0
        if stamp < since or time.time() - stamp > 60:
            raise ValueError("Sunday administrator incident monitor is not updating")



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--since", type=float, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    config = dotenv_values(args.config)
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            check(config, args.since)
            print("Sunday assistant authenticated bridge and worker heartbeat are healthy")
            return
        except Exception:
            if time.monotonic() >= deadline:
                raise SystemExit("Sunday assistant health check failed; check private service logs")
            time.sleep(1)


if __name__ == "__main__":
    main()
