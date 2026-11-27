#!/usr/bin/env python3
"""Replay a private sample corpus through the real agent into a new isolated store.

No WhatsApp client, supervisor or email sender is started. Credentials are read
only for model calls; all other configuration comes from the isolated scenario.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output must be a new directory; never reuse a live data store")
    if str(output).startswith("/opt/homebrew/var/www/"):
        parser.error("Production paths are not permitted")
    scenario = json.loads(args.scenario.read_text())
    output.mkdir(parents=True, mode=0o700)
    root = output / "data"
    root.mkdir(mode=0o700)
    (root / "sunday_worship").mkdir()
    (root / "config").mkdir()
    from dotenv import dotenv_values
    for key, value in dotenv_values(args.model_config).items():
        if value is not None and (key.startswith("OPENAI_") or key == "SUNDAY_ASSISTANT_MODEL"):
            os.environ[key] = value
    os.environ.update(DATA_BASE_DIR=str(root), FULL_ARTICLE_ROOT=str(root / "full_article"),
                      SUNDAY_WHATSAPP_BRIDGE_URL="http://127.0.0.1:1", LANGCHAIN_TRACING_V2="false")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    for name in ["services", "workers", "songs"]:
        write_json(root / "sunday_worship" / f"sunday_{name}.json", scenario["seed"][name])
    write_json(root / "sunday_worship/hymns.json", scenario["seed"].get("hymnal", []))
    write_json(root / "config/config.json", {"users": [], "user_roles": []})
    write_json(root / "config/sermon.json", [])
    write_json(root / "config/fellowship.json", [{"date": datetime.fromisoformat(scenario["target_date"]).strftime("%m/%d/%Y")}])

    from backend.api import config
    assert config.DATA_BASE_PATH == root
    from backend.api.storage import repository
    from backend.sunday_assistant.engine import Assistant
    from backend.sunday_assistant.models import ChatMessage, Settings
    from backend.sunday_assistant.store import Store

    class RecordingBridge:
        def __init__(self):
            self.sent = []

        def send(self, key, group, text):
            self.sent.append({"key": key, "group": group, "text": text, "external_delivery": False})
            return {"status": "sent", "message_id": f"simulation-{len(self.sent)}"}

        def request(self, *_args, **_kwargs):
            raise RuntimeError("Simulation has no external WhatsApp transport")

    settings = Settings.model_validate(scenario["settings"])
    assert not settings.group_questions_enabled and not settings.reminders_enabled and not settings.admin_help_enabled
    store = Store(root / "sunday-assistant")
    store.put("settings", settings.model_dump())
    bridge = RecordingBridge()
    agent = Assistant(store, repository, bridge=bridge)

    def snapshot():
        return {entry.date: entry.model_dump(by_alias=True, mode="json") for entry in repository.list_sunday_services()}

    initial = snapshot()
    initial_catalog = hashlib.sha256(config.SUNDAY_SONGS_FILE.read_bytes()).hexdigest()
    write_json(output / "initial-forms.json", initial)
    steps = []
    for index, item in enumerate(scenario["events"], 1):
        before = snapshot()
        msg = ChatMessage.model_validate(item["message"])
        assert not msg.initial_sync, "Replay models an agent present at the time, not historical auto-application"
        assert store.receive(msg)
        with store.db() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (msg.message_id,)).fetchone()
        agent.process_message(row)
        agent.deliver_outbox()
        after = snapshot()
        changes = {day: {field: {"before": before.get(day, {}).get(field), "after": after.get(day, {}).get(field)}
                         for field in set(before.get(day, {})) | set(after.get(day, {}))
                         if before.get(day, {}).get(field) != after.get(day, {}).get(field)}
                   for day in set(before) | set(after) if before.get(day) != after.get(day)}
        with store.db() as db:
            saved = dict(db.execute("SELECT status,error,result FROM messages WHERE id=?", (msg.message_id,)).fetchone())
        step = {"index": index, "source": item.get("source"), "message": msg.model_dump(mode="json"), **saved, "changes": changes}
        steps.append(step)
        write_json(output / "steps.json", steps)
        write_json(output / "current-forms.json", after)
        print(json.dumps({"step": index, "status": saved["status"], "changed": {day: sorted(fields) for day, fields in changes.items()}, "error": saved["error"]}, ensure_ascii=False), flush=True)

    final = snapshot()
    checks = []
    for day, fields in scenario["expected"].items():
        for field, expected in fields.items():
            checks.append({"date": day, "field": field, "expected": expected, "actual": final.get(day, {}).get(field), "passed": final.get(day, {}).get(field) == expected})
    for day, fields in scenario.get("preserve", {}).items():
        for field in fields:
            checks.append({"date": day, "field": field, "preserved": True, "passed": final.get(day, {}).get(field) == initial.get(day, {}).get(field)})
    for item in scenario["events"]:
        assert not store.receive(ChatMessage.model_validate(item["message"]))
    checks.append({"check": "delivery_replay_is_idempotent", "passed": snapshot() == final})
    checks.append({"check": "no_internal_errors", "passed": all(step["status"] != "error" for step in steps)})
    checks.append({"check": "catalog_unchanged", "passed": hashlib.sha256(config.SUNDAY_SONGS_FILE.read_bytes()).hexdigest() == initial_catalog})
    checks.append({"check": "no_external_group_messages", "passed": all(not item["external_delivery"] for item in bridge.sent)})
    report = {"scenario": scenario.get("name"), "source_sha256": hashlib.sha256(args.scenario.read_bytes()).hexdigest(),
              "completed_at": datetime.now(timezone.utc).isoformat(), "target_form": final.get(scenario["target_date"]),
              "checks": checks, "passed": all(check["passed"] for check in checks), "recorded_replies": bridge.sent}
    write_json(output / "report.json", report)
    print(json.dumps({"passed": report["passed"], "checks": len(checks), "failed_checks": [c for c in checks if not c["passed"]]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
