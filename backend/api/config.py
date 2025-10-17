from __future__ import annotations

from pathlib import Path
from typing import Final, Optional
import os

from dotenv import load_dotenv

load_dotenv()

DATA_BASE_DIR = os.getenv("DATA_BASE_DIR")
if not DATA_BASE_DIR:
    raise RuntimeError("DATA_BASE_DIR environment variable is required")

DATA_BASE_PATH: Final[Path] = Path(DATA_BASE_DIR).resolve()
FULL_ARTICLE_ROOT: Final[Path] = Path(
    os.getenv("FULL_ARTICLE_ROOT", DATA_BASE_PATH / "full_article")
).resolve()
CONFIG_DIR: Final[Path] = DATA_BASE_PATH / "config"
METADATA_FILE: Final[Path] = FULL_ARTICLE_ROOT / "full_articles.json"
PROMPT_FILE: Final[Path] = FULL_ARTICLE_ROOT / "full_article_prompt.md"
FELLOWSHIP_FILE: Final[Path] = CONFIG_DIR / "fellowship.json"
SCRIPTS_DIR: Final[Path] = FULL_ARTICLE_ROOT / "scripts"
ARTICLES_DIR: Final[Path] = FULL_ARTICLE_ROOT / "articles"
SERMON_SERIES_FILE: Final[Path] = CONFIG_DIR / "sermon_series.json"

GENERATION_MODEL: Final[str] = os.getenv("FULL_ARTICLE_MODEL", "gemini-2.5-pro")
GEMINI_API_KEY: Final[Optional[str]] = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
