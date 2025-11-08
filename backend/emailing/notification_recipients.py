from __future__ import annotations

import os
import re
from email.utils import parseaddr
from pathlib import Path
from typing import Iterable, Optional, Sequence

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_data_base_dir() -> Path:
    base_dir = os.getenv("DATA_BASE_DIR")
    if not base_dir:
        raise RuntimeError("DATA_BASE_DIR environment variable is required")
    return Path(base_dir)


def determine_notification_recipients_file(is_production: bool, *, base_dir: Optional[Path] = None) -> Path:
    root = base_dir or resolve_data_base_dir()
    filename = "email_recipients.txt" if is_production else "email_recipients_test.txt"
    return root / "notification" / filename


def _validate_recipient(raw_value: str, source_path: Path) -> str:
    _, parsed_email = parseaddr(raw_value)
    candidate = (parsed_email or raw_value).strip()
    if not candidate:
        raise ValueError(f"Empty email entry in {source_path}")
    if not _EMAIL_PATTERN.fullmatch(candidate):
        raise ValueError(f"Invalid email address '{raw_value}' in {source_path}")
    return candidate


def load_notification_recipients(path: Path | str) -> list[str]:
    source_path = Path(path)
    recipients: list[str] = []
    for line in source_path.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        recipients.append(_validate_recipient(trimmed, source_path))
    if not recipients:
        raise ValueError(f"No recipients found in {source_path}")
    return recipients


def determine_recipient_batch_size(env_var: str = "REMINDER_RECIPIENT_BATCH_SIZE") -> int:
    raw = os.getenv(env_var, "10")
    try:
        size = int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{env_var} must be an integer") from exc
    if size <= 0:
        raise ValueError(f"{env_var} must be a positive integer")
    return size


def chunked(items: Sequence[str], size: int) -> Iterable[list[str]]:
    if size <= 0:
        raise ValueError("Batch size must be positive")
    total = len(items)
    for start in range(0, total, size):
        yield list(items[start : start + size])
