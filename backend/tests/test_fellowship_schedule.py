from datetime import datetime
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "api" / "sc_api" / "fellowship_schedule.py"
SPEC = importlib.util.spec_from_file_location("fellowship_schedule", MODULE_PATH)
assert SPEC is not None
fellowship_schedule = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(fellowship_schedule)
compute_next_fellowship = fellowship_schedule.compute_next_fellowship


def test_compute_next_fellowship_preserves_existing_enriched_entries():
    entries = [
        {
            "date": "04/10/2026",
            "host": "支勇 弟兄",
            "title": "马太福音 13 章  （2）",
            "series": "馬太福音 8-15 章深入研讀",
            "sequence": 5,
        },
        {
            "date": "04/24/2026",
            "host": "楊軍 弟兄",
            "title": "马太福音 14 章",
            "series": "馬太福音 8-15 章深入研讀",
            "sequence": 6,
        },
        {"date": "05/08/2026"},
    ]

    next_date, updated_entries, changed = compute_next_fellowship(
        entries,
        now=datetime(2026, 5, 27),
    )

    assert next_date == "06/05/2026"
    assert changed is True
    assert updated_entries[1]["host"] == "楊軍 弟兄"
    assert updated_entries[1]["title"] == "马太福音 14 章"
    assert updated_entries[-1] == {"date": "05/22/2026"}


def test_compute_next_fellowship_does_not_append_when_latest_date_is_future():
    entries = [
        {"date": "04/10/2026", "title": "Existing"},
        {"date": "06/05/2026", "title": "Future"},
    ]

    next_date, updated_entries, changed = compute_next_fellowship(
        entries,
        now=datetime(2026, 5, 27),
    )

    assert next_date == "06/05/2026"
    assert changed is False
    assert updated_entries == entries
