#!/usr/bin/env python3
"""Supervise the WhatsApp bridge and durable worker from the same checkout.

Use backend/.venv/bin/python scripts/run-sunday-assistant.py --config /path/to/assistant.env
This starts no web/API server and performs no deployment.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, **{k: v for k, v in dotenv_values(args.config).items() if v is not None}}
    for key in ("DATA_BASE_DIR", "FULL_ARTICLE_SERVICE_URL", "SUNDAY_ASSISTANT_SECRET"):
        if not env.get(key):
            parser.error(f"Missing {key} in configuration")
    if len(env["SUNDAY_ASSISTANT_SECRET"]) < 24:
        parser.error("SUNDAY_ASSISTANT_SECRET must contain at least 24 characters")
    env.setdefault("SUNDAY_WHATSAPP_STATE_DIR", str(Path(env["DATA_BASE_DIR"]) / "sunday-assistant" / "whatsapp"))
    env.setdefault("SUNDAY_WHATSAPP_AUTOCONNECT", "1")
    folder = Path(env["SUNDAY_WHATSAPP_STATE_DIR"])
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    node = shutil.which("node")
    if not node or not (root / "services/whatsapp-bridge/node_modules/whatsapp-web.js").exists():
        parser.error("Install bridge dependencies with PUPPETEER_SKIP_DOWNLOAD=1 npm ci --omit=optional in services/whatsapp-bridge")
    with (folder / "supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another bridge supervisor owns this WhatsApp session")
        sys.path.insert(0, str(root))
        from backend.sunday_assistant.admin_monitor import AdminMonitor
        monitor = AdminMonitor(Path(env["DATA_BASE_DIR"]) / "sunday-assistant", env=env)
        processes = []
        def stop(*_):
            for p in processes:
                if p.poll() is None:
                    p.terminate()
            for p in processes:
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()
        signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
        signal.signal(signal.SIGINT, lambda *_: (stop(), sys.exit(0)))
        try:
            processes.append(subprocess.Popen([node, "index.mjs"], cwd=root / "services/whatsapp-bridge", env=env))
            processes.append(subprocess.Popen([sys.executable, "-m", "backend.sunday_assistant_job"], cwd=root, env=env))
            print("Sunday assistant bridge and worker started; monitor the admin page.", flush=True)
            checked = 0
            while all(p.poll() is None for p in processes):
                if time.time() - checked >= 10:
                    try:
                        monitor.tick()
                    except Exception:
                        print("Sunday admin monitor check failed; inspect private runtime logs.", flush=True)
                    checked = time.time()
                time.sleep(2)
            monitor.record_exit()
            try:
                monitor.tick()
            except Exception:
                pass
        finally:
            stop()
    return 1


if __name__ == "__main__":
    sys.exit(main())
