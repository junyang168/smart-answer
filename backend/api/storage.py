from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from .config import (
    ARTICLES_DIR,
    FELLOWSHIP_FILE,
    FULL_ARTICLE_ROOT,
    METADATA_FILE,
    PROMPT_FILE,
    SCRIPTS_DIR,
    SERMON_SERIES_FILE,
)
from .models import (
    ArticleDetail,
    ArticleMetadata,
    ArticleStatus,
    ArticleSummary,
    ArticleType,
    SaveArticleRequest,
    SaveArticleResponse,
    FellowshipEntry,
    SermonSeries,
)


def _ensure_directories() -> None:
    for path in (FULL_ARTICLE_ROOT, SCRIPTS_DIR, ARTICLES_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _load_metadata_raw() -> list[dict]:
    if not METADATA_FILE.exists():
        return []
    try:
        return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Unable to parse metadata file at {METADATA_FILE}") from exc


def _persist_metadata(entries: Iterable[ArticleMetadata]) -> None:
    data = [entry.dict() for entry in entries]
    tmp_path = METADATA_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")
    tmp_path.replace(METADATA_FILE)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Type {type(value)} not serialisable")


def _load_metadata_models() -> list[ArticleMetadata]:
    raw_entries = _load_metadata_raw()
    entries: list[ArticleMetadata] = []
    for raw in raw_entries:
        try:
            entries.append(ArticleMetadata.parse_obj(raw))
        except Exception as exc:
            raise ValueError(f"Invalid metadata entry: {raw}") from exc
    return entries


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    slug = slug.strip("-")
    return slug or f"article-{uuid.uuid4().hex[:8]}"


class ArticleRepository:
    def __init__(self) -> None:
        _ensure_directories()
        if not METADATA_FILE.exists():
            METADATA_FILE.write_text("[]", encoding="utf-8")
        FELLOWSHIP_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not FELLOWSHIP_FILE.exists():
            FELLOWSHIP_FILE.write_text("[]", encoding="utf-8")
        SERMON_SERIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not SERMON_SERIES_FILE.exists():
            SERMON_SERIES_FILE.write_text("[]", encoding="utf-8")

    # Prompt operations
    def load_prompt(self) -> str:
        if PROMPT_FILE.exists():
            return PROMPT_FILE.read_text(encoding="utf-8")
        default_prompt = (
            "Please craft a full-length article from the provided script."
        )
        PROMPT_FILE.write_text(default_prompt, encoding="utf-8")
        return default_prompt

    def save_prompt(self, prompt_markdown: str) -> str:
        PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROMPT_FILE.write_text(prompt_markdown, encoding="utf-8")
        return prompt_markdown

    # Article operations
    def list_articles(self) -> list[ArticleSummary]:
        records = _load_metadata_models()
        return [
            ArticleSummary(
                id=entry.id,
                name=entry.name,
                slug=entry.slug,
                subtitle=entry.subtitle,
                status=entry.status,
                updated_at=entry.updated_at,
                created_at=entry.created_at,
                model=entry.model,
                summary_markdown=entry.summary_markdown or "",
                article_type=entry.article_type,
                core_bible_verses=entry.core_bible_verses or [],
            )
            for entry in sorted(records, key=lambda e: e.updated_at, reverse=True)
        ]

    def _determine_slug(self, name: str, existing: list[ArticleMetadata], current_id: Optional[str]) -> str:
        base = _slugify(name)
        slug = base
        counter = 2
        occupied = {entry.slug: entry.id for entry in existing}
        while slug in occupied and occupied[slug] != current_id:
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    def get_article(self, article_id: str) -> ArticleDetail:
        records = _load_metadata_models()
        for entry in records:
            if entry.id == article_id:
                return self._assemble_detail(entry)
        raise ValueError(f"Article {article_id} not found")

    def _assemble_detail(self, entry: ArticleMetadata) -> ArticleDetail:
        script_md = self._read_markdown(SCRIPTS_DIR / entry.script_filename)
        article_md = self._read_markdown(ARTICLES_DIR / entry.article_filename)
        prompt_md = self.load_prompt()
        return ArticleDetail(
            id=entry.id,
            name=entry.name,
            slug=entry.slug,
            subtitle=entry.subtitle,
            status=entry.status,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            model=entry.model,
            script_markdown=script_md,
            article_markdown=article_md,
            prompt_markdown=prompt_md,
            summary_markdown=entry.summary_markdown or "",
            article_type=entry.article_type,
            core_bible_verses=entry.core_bible_verses or [],
        )

    def _read_markdown(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _write_markdown(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)

    def save_article(self, payload: SaveArticleRequest) -> SaveArticleResponse:
        records = _load_metadata_models()
        now = datetime.now(timezone.utc)

        previous_script_path: Optional[Path] = None
        previous_article_path: Optional[Path] = None

        if payload.id:
            entry = next((item for item in records if item.id == payload.id), None)
            if not entry:
                raise ValueError(f"Article {payload.id} not found")
            if entry.script_filename:
                previous_script_path = SCRIPTS_DIR / entry.script_filename
            if entry.article_filename:
                previous_article_path = ARTICLES_DIR / entry.article_filename
            entry.name = payload.name
            entry.subtitle = payload.subtitle
            entry.status = payload.status
            entry.updated_at = now
        else:
            entry = ArticleMetadata(
                id=str(uuid.uuid4()),
                name=payload.name,
                subtitle=payload.subtitle,
                slug="",
                script_filename="",
                article_filename="",
                created_at=now,
                updated_at=now,
                status=payload.status,
                summary_markdown=payload.summary_markdown or "",
                article_type=payload.article_type,
                core_bible_verses=[verse for verse in payload.core_bible_verses if verse],
            )
            records.append(entry)

        entry.slug = self._determine_slug(payload.name, records, entry.id)
        entry.script_filename = f"{entry.slug}.md"
        entry.article_filename = f"{entry.slug}.md"
        entry.summary_markdown = payload.summary_markdown if payload.summary_markdown is not None else (entry.summary_markdown or "")
        entry.article_type = payload.article_type
        entry.core_bible_verses = [verse for verse in payload.core_bible_verses if verse]

        script_path = SCRIPTS_DIR / entry.script_filename
        article_path = ARTICLES_DIR / entry.article_filename

        self._write_markdown(script_path, payload.script_markdown)
        self._write_markdown(article_path, payload.article_markdown)

        if (
            previous_script_path
            and previous_script_path != script_path
            and previous_script_path.exists()
        ):
            previous_script_path.unlink()
        if (
            previous_article_path
            and previous_article_path != article_path
            and previous_article_path.exists()
        ):
            previous_article_path.unlink()

        _persist_metadata(records)

        if payload.prompt_markdown is not None:
            self.save_prompt(payload.prompt_markdown)

        return self._assemble_detail(entry)

    def update_generated_article(
        self,
        article_id: str,
        article_markdown: str,
        model_name: Optional[str],
        status: ArticleStatus,
    ) -> ArticleDetail:
        records = _load_metadata_models()
        entry = next((item for item in records if item.id == article_id), None)
        if not entry:
            raise ValueError(f"Article {article_id} not found")

        entry.article_filename = entry.article_filename or f"{entry.slug}.md"
        entry.status = status
        entry.model = model_name
        now = datetime.now(timezone.utc)
        entry.updated_at = now
        entry.last_generated_at = now

        self._write_markdown(ARTICLES_DIR / entry.article_filename, article_markdown)
        _persist_metadata(records)
        return self._assemble_detail(entry)

    def update_article_summary(
        self,
        article_id: str,
        summary_markdown: str,
        model_name: Optional[str],
    ) -> ArticleDetail:
        records = _load_metadata_models()
        entry = next((item for item in records if item.id == article_id), None)
        if not entry:
            raise ValueError(f"Article {article_id} not found")

        entry.summary_markdown = summary_markdown
        entry.updated_at = datetime.now(timezone.utc)
        entry.last_generated_at = entry.updated_at
        entry.model = model_name or entry.model

        _persist_metadata(records)
        return self._assemble_detail(entry)

    def create_draft(
        self,
        payload: SaveArticleRequest,
    ) -> SaveArticleResponse:
        return self.save_article(payload)

    def get_new_article_template(self) -> SaveArticleResponse:
        prompt_md = self.load_prompt()
        now = datetime.now(timezone.utc)
        placeholder = ArticleDetail(
            id="",
            name="",
            slug="",
            subtitle="",
            status="draft",
            created_at=now,
            updated_at=now,
            model=None,
            scriptMarkdown="",
            articleMarkdown="",
            promptMarkdown=prompt_md,
            summaryMarkdown="",
            articleType=None,
            coreBibleVerses=[],
        )
        return SaveArticleResponse.parse_obj(placeholder.dict(by_alias=True))


        repository = ArticleRepository()

    # Fellowship operations
    def _load_fellowship_entries(self) -> list[FellowshipEntry]:
        try:
            raw = json.loads(FELLOWSHIP_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Unable to parse fellowship.json") from exc
        entries: list[FellowshipEntry] = []
        for item in raw:
            try:
                entries.append(FellowshipEntry.model_validate(item))
            except Exception as exc:
                raise ValueError(f"Invalid fellowship entry: {item}") from exc
        return entries

    def _save_fellowship_entries(self, entries: list[FellowshipEntry]) -> None:
        tmp_path = FELLOWSHIP_FILE.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(FELLOWSHIP_FILE)

    def list_fellowships(self) -> list[FellowshipEntry]:
        entries = self._load_fellowship_entries()

        def parse_date(value: str) -> datetime:
            try:
                return datetime.strptime(value, "%m/%d/%Y")
            except Exception:
                return datetime.min

        return sorted(
            entries,
            key=lambda entry: (
                parse_date(entry.date),
                entry.sequence if entry.sequence is not None else 0,
            ),
            reverse=True,
        )


    def create_fellowship(self, entry: FellowshipEntry) -> FellowshipEntry:
        entries = self._load_fellowship_entries()
        if any(existing.date == entry.date for existing in entries):
            raise ValueError(f"Fellowship date {entry.date} already exists")
        entries.append(entry)
        self._save_fellowship_entries(entries)
        return entry

    def update_fellowship(self, date: str, entry: FellowshipEntry) -> FellowshipEntry:
        entries = self._load_fellowship_entries()
        target = None
        for index, existing in enumerate(entries):
            if existing.date == date:
                target = index
                break
        if target is None:
            raise ValueError(f"Fellowship date {date} not found")
        if entry.date != date and any(e.date == entry.date for e in entries):
            raise ValueError(f"Fellowship date {entry.date} already exists")
        entries[target] = entry
        self._save_fellowship_entries(entries)
        return entry

    def delete_fellowship(self, date: str) -> None:
        entries = self._load_fellowship_entries()
        new_entries = [entry for entry in entries if entry.date != date]
        if len(new_entries) == len(entries):
            raise ValueError(f"Fellowship date {date} not found")
        self._save_fellowship_entries(new_entries)


    # Sermon series operations
    def _load_sermon_series_entries(self) -> list[SermonSeries]:
        try:
            raw = json.loads(SERMON_SERIES_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Unable to parse sermon_series.json") from exc
        entries: list[SermonSeries] = []
        for item in raw:
            try:
                entries.append(SermonSeries.model_validate(item))
            except Exception as exc:
                raise ValueError(f"Invalid sermon series entry: {item}") from exc
        return entries

    def _save_sermon_series_entries(self, entries: list[SermonSeries]) -> None:
        tmp_path = SERMON_SERIES_FILE.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(SERMON_SERIES_FILE)

    def list_sermon_series(self) -> list[SermonSeries]:
        return self._load_sermon_series_entries()

    def create_sermon_series(self, series: SermonSeries) -> SermonSeries:
        entries = self._load_sermon_series_entries()
        if any(existing.id == series.id for existing in entries):
            raise ValueError(f"Sermon series {series.id} already exists")
        entries.append(series)
        self._save_sermon_series_entries(entries)
        return series

    def update_sermon_series(self, series_id: str, series: SermonSeries) -> SermonSeries:
        entries = self._load_sermon_series_entries()
        target = None
        for index, existing in enumerate(entries):
            if existing.id == series_id:
                target = index
                break
        if target is None:
            raise ValueError(f"Sermon series {series_id} not found")
        if series.id != series_id and any(existing.id == series.id for existing in entries):
            raise ValueError(f"Sermon series {series.id} already exists")
        entries[target] = series
        self._save_sermon_series_entries(entries)
        return series

    def delete_sermon_series(self, series_id: str) -> None:
        entries = self._load_sermon_series_entries()
        new_entries = [entry for entry in entries if entry.id != series_id]
        if len(new_entries) == len(entries):
            raise ValueError(f"Sermon series {series_id} not found")
        self._save_sermon_series_entries(new_entries)


repository = ArticleRepository()
