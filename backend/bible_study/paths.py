"""Where study folders and the 和合本 cache live. Never in git: a study folder
holds Carson page references and excerpts, and the repository is public."""

from __future__ import annotations

import os
from pathlib import Path


def data_base_dir() -> Path:
    value = os.getenv("DATA_BASE_DIR")
    if not value:
        raise RuntimeError("DATA_BASE_DIR is required")
    return Path(value).expanduser().resolve()


def studies_dir() -> Path:
    return data_base_dir() / "bible-study"


def cuv_dir() -> Path:
    return data_base_dir() / "bible" / "cuv"
