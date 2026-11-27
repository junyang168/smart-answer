"""Church fellowship schedule: biweekly Thursday, 20:00 America/Chicago."""
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo('America/Chicago')
START_TIME = time(20, 0)
SCHEDULE_CHANGED_ON = date(2026, 11, 21)
TIME_LABEL = '週四晚 8:00（美國中部時間）'


def next_biweekly_date(previous: date) -> date:
    target = previous + timedelta(days=14)
    # Retain the biweekly cycle across the Friday-to-Thursday transition.
    if target >= SCHEDULE_CHANGED_ON and target.weekday() == 4:
        target -= timedelta(days=1)
    return target


def following_sunday(day: date) -> date:
    return day + timedelta(days=(6 - day.weekday()) % 7)
