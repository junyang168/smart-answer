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
SUNDAY_WORSHIP_DIR: Final[Path] = DATA_BASE_PATH / "sunday_worship"


METADATA_FILE: Final[Path] = FULL_ARTICLE_ROOT / "full_articles.json"
PROMPT_FILE: Final[Path] = FULL_ARTICLE_ROOT / "full_article_prompt.md"
FELLOWSHIP_FILE: Final[Path] = CONFIG_DIR / "fellowship.json"
SCRIPTS_DIR: Final[Path] = FULL_ARTICLE_ROOT / "scripts"
ARTICLES_DIR: Final[Path] = FULL_ARTICLE_ROOT / "articles"
SERMON_SERIES_FILE: Final[Path] = CONFIG_DIR / "sermon_series.json"
SUNDAY_SERVICE_FILE: Final[Path] = SUNDAY_WORSHIP_DIR / "sunday_services.json"
SUNDAY_WORKERS_FILE: Final[Path] = SUNDAY_WORSHIP_DIR / "sunday_workers.json"
SUNDAY_SONGS_FILE: Final[Path] = SUNDAY_WORSHIP_DIR / "sunday_songs.json"
HYMNS_FILE: Final[Path] = SUNDAY_WORSHIP_DIR / "hymns.json"
PPT_TEMPLATE_FILE: Final[Path] = SUNDAY_WORSHIP_DIR / "template.pptx"

GENERATION_MODEL: Final[str] = os.getenv("FULL_ARTICLE_MODEL", "gemini-2.5-pro")
GEMINI_API_KEY: Final[Optional[str]] = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
