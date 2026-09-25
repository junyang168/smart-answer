"""Where study folders and the 和合本 cache live. Never in git: a study folder
holds Carson page references and excerpts, and the repository is public."""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    """The repo's `.env`, without overriding anything already set."""

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")


def data_base_dir() -> Path:
    load_env()
    value = os.getenv("DATA_BASE_DIR")
    if not value:
        raise RuntimeError("DATA_BASE_DIR is required")
    return Path(value).expanduser().resolve()


def studies_dir() -> Path:
    return data_base_dir() / "bible-study"


def cuv_dir() -> Path:
    return data_base_dir() / "bible" / "cuv"
