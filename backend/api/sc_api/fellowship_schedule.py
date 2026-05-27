from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


DATE_FORMAT = "%m/%d/%Y"


def load_fellowship_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("fellowship.json must be a non-empty list")
    entries: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("date"), str):
            raise ValueError("Each fellowship entry must be an object with a string date")
        entries.append(dict(entry))
    return entries


def save_fellowship_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def compute_next_fellowship(
    entries: list[dict[str, Any]],
    now: datetime | None = None,
) -> tuple[str, list[dict[str, Any]], bool]:
    now = now or datetime.now()
    dated_entries = [
        (datetime.strptime(entry["date"], DATE_FORMAT), entry)
        for entry in entries
    ]
    last_date = max(date for date, _entry in dated_entries)

    if last_date > now:
        return last_date.strftime(DATE_FORMAT), entries, False

    existing_dates = {date.date() for date, _entry in dated_entries}
    next_date = last_date + timedelta(weeks=2)
    updated_entries = list(entries)
    changed = False

    while next_date < now:
        if next_date.date() not in existing_dates:
            updated_entries.append({"date": next_date.strftime(DATE_FORMAT)})
            existing_dates.add(next_date.date())
            changed = True
        last_date = next_date
        next_date = last_date + timedelta(weeks=2)

    return next_date.strftime(DATE_FORMAT), updated_entries, changed
