from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path

from zoneinfo import ZoneInfo

try:
    from .emailing import resolve_data_base_dir
except ImportError:  # Allows fellowship_reminder_job.py to run as a script.
    from emailing import resolve_data_base_dir  # type: ignore[no-redef]


TIMEZONE = ZoneInfo("America/Chicago")


def resolve_fellowship_reminder_status_file() -> Path:
    return resolve_data_base_dir() / "notification" / "last_sent.json"


def load_last_sent_fellowship_date(status_file: Path | None = None) -> str | None:
    path = status_file or resolve_fellowship_reminder_status_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        last_event = data.get("event_date")
        if isinstance(last_event, str):
            return last_event
    return None


def record_fellowship_email_sent(
    event_date: date,
    status_file: Path | None = None,
) -> None:
    path = status_file or resolve_fellowship_reminder_status_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_date": event_date.isoformat(),
        "recorded_at": datetime.now(TIMEZONE).isoformat(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
