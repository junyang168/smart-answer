from __future__ import annotations

import json
import shutil
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable, List, Optional
from collections.abc import Sequence

from .config import (
    ARTICLES_DIR,
    DEPTH_OF_FAITH_FILE,
    FELLOWSHIP_FILE,
    FULL_ARTICLE_ROOT,
    HYMNS_FILE,
    METADATA_FILE,
    PROMPT_FILE,
    SCRIPTS_DIR,
    SERMON_SERIES_FILE,
    SUNDAY_SERVICE_FILE,
    SUNDAY_SONGS_FILE,
    SUNDAY_WORKERS_FILE,
    WEBCAST_DIR,
)
from .models import (
    ArticleDetail,
    ArticleMetadata,
    ArticleStatus,
    ArticleSummary,
    ArticleType,
    DepthOfFaithEpisode,
    DepthOfFaithEpisodeCreate,
    DepthOfFaithEpisodeUpdate,
    SaveArticleRequest,
    SaveArticleResponse,
    FellowshipEntry,
    SundayServiceEntry,
    SundaySong,
    SundaySongCreate,
    SundayWorker,
    HymnMetadata,
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
        config_files = (
            FELLOWSHIP_FILE,
            SERMON_SERIES_FILE,
            SUNDAY_SERVICE_FILE,
            SUNDAY_WORKERS_FILE,
            SUNDAY_SONGS_FILE,
        )
        for path in config_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("[]", encoding="utf-8")
        WEBCAST_DIR.mkdir(parents=True, exist_ok=True)
        if not DEPTH_OF_FAITH_FILE.exists():
            DEPTH_OF_FAITH_FILE.write_text("[]", encoding="utf-8")

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
                source_sermon_ids=entry.source_sermon_ids or [],
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
            source_sermon_ids=entry.source_sermon_ids or [],
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
                source_sermon_ids=[sid for sid in payload.source_sermon_ids if sid],
            )
            records.append(entry)

        entry.slug = self._determine_slug(payload.name, records, entry.id)
        entry.script_filename = f"{entry.slug}.md"
        entry.article_filename = f"{entry.slug}.md"
        entry.summary_markdown = payload.summary_markdown if payload.summary_markdown is not None else (entry.summary_markdown or "")
        entry.article_type = payload.article_type
        entry.core_bible_verses = [verse for verse in payload.core_bible_verses if verse]
        entry.source_sermon_ids = [sid for sid in payload.source_sermon_ids if sid]

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
            sourceSermonIds=[],
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


    # Sunday worship service operations
    def _load_sunday_service_entries(self) -> list[SundayServiceEntry]:
        try:
            raw = json.loads(SUNDAY_SERVICE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Unable to parse sunday_services.json") from exc
        entries: list[SundayServiceEntry] = []
        for item in raw:
            try:
                entries.append(SundayServiceEntry.model_validate(item))
            except Exception as exc:
                raise ValueError(f"Invalid sunday service entry: {item}") from exc
        return entries

    def _save_sunday_service_entries(self, entries: list[SundayServiceEntry]) -> None:
        tmp_path = SUNDAY_SERVICE_FILE.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(
                [entry.model_dump(by_alias=True) for entry in entries],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(SUNDAY_SERVICE_FILE)

    def list_sunday_services(self) -> list[SundayServiceEntry]:
        entries = self._load_sunday_service_entries()

        def parse_date(value: str) -> datetime:
            formats = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y")
            for fmt in formats:
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
            return datetime.min

        return sorted(entries, key=lambda entry: parse_date(entry.date), reverse=True)

    def _ensure_worker_availability(self, entry: SundayServiceEntry) -> None:
        date_text = (entry.date or "").strip()
        if not date_text:
            return
        workers = {worker.name: worker for worker in self._load_sunday_workers()}
        unavailable: list[str] = []

        def check(role: str, name: Optional[str]) -> None:
            if not name:
                return
            worker = workers.get(name)
            if worker and not worker.is_available_on(date_text):
                unavailable.append(f"{role}：{name}")

        check("司會", entry.presider)
        check("領詩", entry.worship_leader)
        check("司琴", entry.pianist)
        check("證道", entry.sermon_speaker)

        for index, reader in enumerate(entry.scripture_readers or [], start=1):
            check(f"讀經同工{index}", reader)

        if unavailable:
            raise ValueError(f"以下同工於 {date_text} 無法服事：{', '.join(unavailable)}")

    def create_sunday_service(self, entry: SundayServiceEntry) -> SundayServiceEntry:
        entry = self._apply_hymn_indices(entry)
        self._ensure_worker_availability(entry)
        entries = self._load_sunday_service_entries()
        if any(existing.date == entry.date for existing in entries):
            raise ValueError(f"Sunday service date {entry.date} already exists")
        entries.append(entry)
        self._save_sunday_service_entries(entries)
        return entry

    def get_sunday_service(self, date: str) -> SundayServiceEntry:
        entries = self._load_sunday_service_entries()
        for entry in entries:
            if entry.date == date:
                return entry
        raise ValueError(f"Sunday service date {date} not found")

    def update_sunday_service(self, date: str, entry: SundayServiceEntry) -> SundayServiceEntry:
        entry = self._apply_hymn_indices(entry)
        self._ensure_worker_availability(entry)
        entries = self._load_sunday_service_entries()
        target = None
        for index, existing in enumerate(entries):
            if existing.date == date:
                target = index
                break
        if target is None:
            raise ValueError(f"Sunday service date {date} not found")
        if entry.date != date and any(existing.date == entry.date for existing in entries):
            raise ValueError(f"Sunday service date {entry.date} already exists")
        existing_entry = entries[target]
        if entry.final_ppt_filename is None and existing_entry.final_ppt_filename:
            entry = entry.model_copy(update={"final_ppt_filename": existing_entry.final_ppt_filename})
        entries[target] = entry
        self._save_sunday_service_entries(entries)
        return entry

    def delete_sunday_service(self, date: str) -> None:
        entries = self._load_sunday_service_entries()
        new_entries = [entry for entry in entries if entry.date != date]
        if len(new_entries) == len(entries):
            raise ValueError(f"Sunday service date {date} not found")
        self._save_sunday_service_entries(new_entries)

    def set_sunday_service_final_ppt(self, date: str, filename: Optional[str]) -> SundayServiceEntry:
        entries = self._load_sunday_service_entries()
        for index, existing in enumerate(entries):
            if existing.date == date:
                updated = existing.model_copy(update={"final_ppt_filename": filename})
                entries[index] = updated
                self._save_sunday_service_entries(entries)
                return updated
        raise ValueError(f"Sunday service date {date} not found")

    def _apply_hymn_indices(self, entry: SundayServiceEntry) -> SundayServiceEntry:
        songs = {song.title: song for song in self._load_sunday_songs()}

        def resolve(title: Optional[str]) -> Optional[int]:
            if not title:
                return None
            song = songs.get(title)
            if song and song.source == "hymnal":
                return song.hymnal_index
            return None

        data = entry.model_dump(by_alias=True)
        data["hymnIndex"] = resolve(entry.hymn)
        data["responseHymnIndex"] = resolve(entry.response_hymn)
        return SundayServiceEntry.model_validate(data)

    def _load_sunday_workers(self) -> list[SundayWorker]:
        try:
            raw = json.loads(SUNDAY_WORKERS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Unable to parse sunday_workers.json") from exc
        if not isinstance(raw, list):
            raise ValueError("sunday_workers.json must contain a list")
        workers: list[SundayWorker] = []
        for item in raw:
            if isinstance(item, str):
                try:
                    workers.append(SundayWorker(name=item))
                except ValueError:
                    continue
            elif isinstance(item, dict):
                data = dict(item)
                try:
                    workers.append(SundayWorker.model_validate(data))
                    continue
                except Exception:
                    name = data.get("name")
                    email = data.get("email")
                    unavailable = (
                        data.get("unavailableRanges")
                        or data.get("unavailable_ranges")
                        or data.get("unavailable_dates")
                        or data.get("unavailableDates")
                    )
                    ranges: list[dict] = []
                    if isinstance(unavailable, Sequence) and not isinstance(unavailable, (str, bytes)):
                        for raw_range in unavailable:
                            if isinstance(raw_range, dict):
                                start = raw_range.get("startDate") or raw_range.get("start_date")
                                end = raw_range.get("endDate") or raw_range.get("end_date")
                                if isinstance(start, str) and isinstance(end, str):
                                    ranges.append({"startDate": start, "endDate": end})
                            else:
                                text = str(raw_range).strip()
                                if text:
                                    ranges.append({"startDate": text, "endDate": text})
                    elif unavailable:
                        text = str(unavailable).strip()
                        if text:
                            ranges.append({"startDate": text, "endDate": text})
                    if isinstance(name, str):
                        try:
                            workers.append(
                                SundayWorker(
                                    name=name,
                                    email=email,
                                    unavailable_ranges=ranges,
                                )
                            )
                        except ValueError:
                            try:
                                workers.append(SundayWorker(name=name, email=email))
                            except ValueError:
                                continue
        return workers

    def _save_sunday_workers(self, workers: list[SundayWorker]) -> None:
        tmp_path = SUNDAY_WORKERS_FILE.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(
                [worker.model_dump(by_alias=True, exclude_none=True) for worker in workers],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(SUNDAY_WORKERS_FILE)

    def list_sunday_workers(self) -> list[SundayWorker]:
        workers = sorted(self._load_sunday_workers(), key=lambda worker: worker.name.casefold())
        return [SundayWorker.model_validate(worker.model_dump()) for worker in workers]

    def create_sunday_worker(self, worker: SundayWorker) -> SundayWorker:
        name = worker.name.strip()
        workers = self._load_sunday_workers()
        lower_set = {existing.name.casefold(): existing for existing in workers}
        if name.casefold() in lower_set:
            raise ValueError(f"Worker {name} already exists")
        new_worker = SundayWorker(
            name=name,
            email=worker.email,
            unavailable_ranges=worker.unavailable_ranges,
        )
        workers.append(new_worker)
        workers.sort(key=lambda value: value.name.casefold())
        self._save_sunday_workers(workers)
        return new_worker

    def update_sunday_worker(self, current_name: str, worker: SundayWorker) -> SundayWorker:
        target = current_name.strip()
        if not target:
            raise ValueError("Current worker name is required")
        workers = self._load_sunday_workers()
        try:
            index = next(i for i, value in enumerate(workers) if value.name == target)
        except StopIteration as exc:
            raise ValueError(f"Worker {current_name} not found") from exc

        new_name = worker.name.strip()
        if new_name != target and any(value.name.casefold() == new_name.casefold() for value in workers):
            raise ValueError(f"Worker {new_name} already exists")

        updated_worker = SundayWorker(
            name=new_name,
            email=worker.email,
            unavailable_ranges=worker.unavailable_ranges,
        )
        workers[index] = updated_worker
        workers.sort(key=lambda value: value.name.casefold())
        self._save_sunday_workers(workers)

        if new_name != target:
            services = self._load_sunday_service_entries()
            updated = False
            for service in services:
                changed = False
                if service.presider == target:
                    service.presider = new_name
                    changed = True
                if service.worship_leader == target:
                    service.worship_leader = new_name
                    changed = True
                if service.pianist == target:
                    service.pianist = new_name
                    changed = True
                if service.sermon_speaker == target:
                    service.sermon_speaker = new_name
                    changed = True
                if changed:
                    updated = True
            if updated:
                self._save_sunday_service_entries(services)

        return updated_worker

    def delete_sunday_worker(self, name: str) -> None:
        target = name.strip()
        if not target:
            raise ValueError("Worker name is required")
        workers = self._load_sunday_workers()
        new_workers = [value for value in workers if value.name != target]
        if len(new_workers) == len(workers):
            raise ValueError(f"Worker {name} not found")
        self._save_sunday_workers(new_workers)

        services = self._load_sunday_service_entries()
        updated = False
        for service in services:
            changed = False
            if service.presider == target:
                service.presider = None
                changed = True
            if service.worship_leader == target:
                service.worship_leader = None
                changed = True
            if service.pianist == target:
                service.pianist = None
                changed = True
            if service.sermon_speaker == target:
                service.sermon_speaker = None
                changed = True
            if changed:
                updated = True
        if updated:
            self._save_sunday_service_entries(services)

    def _load_sunday_songs(self) -> list[SundaySong]:
        try:
            raw = json.loads(SUNDAY_SONGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Unable to parse sunday_songs.json") from exc
        except FileNotFoundError as exc:
            raise ValueError("sunday_songs.json file is missing") from exc

        songs: list[SundaySong] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                songs.append(SundaySong.model_validate(item))
            except Exception:
                legacy = self._convert_legacy_song(item)
                songs.append(legacy)
        return songs

    def _convert_legacy_song(self, item: dict) -> SundaySong:
        title = item.get("title") or item.get("name") or "未命名詩歌"
        song_id = item.get("id") or str(uuid.uuid4())
        lyrics = item.get("lyrics_markdown") or item.get("lyricsMarkdown")
        if isinstance(lyrics, str):
            lyrics = lyrics.strip()
        return SundaySong(
            id=song_id,
            title=title,
            source="custom",
            lyrics_markdown=lyrics or None,
            hymn_link=item.get("hymn_link") or item.get("link") or None,
            hymnal_index=None,
        )

    def _save_sunday_songs(self, songs: list[SundaySong]) -> None:
        tmp_path = SUNDAY_SONGS_FILE.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps([song.model_dump(by_alias=True) for song in songs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(SUNDAY_SONGS_FILE)

    def list_sunday_songs(self) -> list[SundaySong]:
        songs = self._load_sunday_songs()
        return sorted(songs, key=lambda song: song.title.casefold())

    def create_sunday_song(self, payload: SundaySongCreate) -> SundaySong:
        title = payload.title.strip()
        if not title:
            raise ValueError("Song title is required")
        songs = self._load_sunday_songs()
        if any(song.title.casefold() == title.casefold() for song in songs):
            raise ValueError(f"Song {title} already exists")

        if payload.source == "hymnal" and payload.hymnal_index is None:
            raise ValueError("Hymnal songs require an index")

        lyrics = (payload.lyrics_markdown or "").strip()
        new_song = SundaySong(
            id=str(uuid.uuid4()),
            title=title,
            source=payload.source,
            lyrics_markdown=lyrics or None,
            hymn_link=payload.hymn_link,
            hymnal_index=payload.hymnal_index,
        )
        songs.append(new_song)
        songs.sort(key=lambda song: song.title.casefold())
        self._save_sunday_songs(songs)
        return new_song

    def update_sunday_song(self, song_id: str, payload: SundaySongCreate) -> SundaySong:
        songs = self._load_sunday_songs()
        target = None
        for index, existing in enumerate(songs):
            if existing.id == song_id:
                target = index
                break
        if target is None:
            raise ValueError(f"Song {song_id} not found")

        title = payload.title.strip()
        if not title:
            raise ValueError("Song title is required")
        if any(
            existing.id != song_id and existing.title.casefold() == title.casefold()
            for existing in songs
        ):
            raise ValueError(f"Song {title} already exists")

        if payload.source == "hymnal" and payload.hymnal_index is None:
            raise ValueError("Hymnal songs require an index")

        previous_title = songs[target].title
        lyrics = (payload.lyrics_markdown or "").strip()
        updated_song = SundaySong(
            id=song_id,
            title=title,
            source=payload.source,
            lyrics_markdown=lyrics or None,
            hymn_link=payload.hymn_link,
            hymnal_index=payload.hymnal_index,
        )
        songs[target] = updated_song
        songs.sort(key=lambda song: song.title.casefold())
        self._save_sunday_songs(songs)

        if previous_title != title:
            services = self._load_sunday_service_entries()
            updated = False
            for service in services:
                changed = False
                if service.hymn == previous_title:
                    service.hymn = title
                    changed = True
                if service.response_hymn == previous_title:
                    service.response_hymn = title
                    changed = True
                if changed:
                    updated = True
            if updated:
                self._save_sunday_service_entries(services)

        return updated_song

    def delete_sunday_song(self, song_id: str) -> None:
        songs = self._load_sunday_songs()
        target = None
        for index, existing in enumerate(songs):
            if existing.id == song_id:
                target = index
                break
        if target is None:
            raise ValueError(f"Song {song_id} not found")
        removed = songs.pop(target)
        self._save_sunday_songs(songs)

        services = self._load_sunday_service_entries()
        updated = False
        for service in services:
            changed = False
            if service.hymn == removed.title:
                service.hymn = None
                changed = True
            if service.response_hymn == removed.title:
                service.response_hymn = None
                changed = True
            if changed:
                updated = True
        if updated:
            self._save_sunday_service_entries(services)

    def _load_hymn_entries(self) -> list[HymnMetadata]:
        if not HYMNS_FILE.exists():
            raise ValueError("Hymn metadata file not found")
        try:
            raw = json.loads(HYMNS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Unable to parse hymns.json") from exc

        if isinstance(raw, dict):
            if "hymns" in raw and isinstance(raw["hymns"], list):
                entries_raw = raw["hymns"]
            else:
                raise ValueError("Invalid hymns.json format")
        elif isinstance(raw, list):
            entries_raw = raw
        else:
            raise ValueError("Invalid hymns.json format")

        entries: list[HymnMetadata] = []
        for item in entries_raw:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            title = item.get("title")
            if index is None or title is None:
                continue
            try:
                index_value = int(index)
            except (TypeError, ValueError):
                continue
            link = item.get("link")
            lyrics_url = item.get("lyrics_url") or item.get("lyricsUrl")
            entries.append(
                HymnMetadata(index=index_value, title=str(title), link=link, lyrics_url=lyrics_url)
            )
        if not entries:
            raise ValueError("Hymn metadata is empty")
        return entries

    def get_hymn_metadata(self, index: int) -> HymnMetadata:
        entries = self._load_hymn_entries()
        for entry in entries:
            if entry.index == index:
                return entry
        raise ValueError(f"Hymn index {index} not found")


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

    # Webcast operations
    def _load_depth_of_faith_entries(self) -> list[DepthOfFaithEpisode]:
        if not DEPTH_OF_FAITH_FILE.exists():
            return []
        try:
            raw_entries = json.loads(DEPTH_OF_FAITH_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Unable to parse depth_of_faith.json at {DEPTH_OF_FAITH_FILE}") from exc

        if not isinstance(raw_entries, list):
            raise ValueError("depth_of_faith.json must contain a list of episodes")

        episodes: list[DepthOfFaithEpisode] = []
        seen_ids: set[str] = set()

        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue

            episode_id = raw.get("id") or raw.get("slug") or raw.get("item") or raw.get("title")
            if not episode_id:
                raise ValueError("Depth of Faith episode entry is missing an id")
            episode_id = str(episode_id).strip()
            if not episode_id:
                raise ValueError("Depth of Faith episode id cannot be blank")
            if episode_id in seen_ids:
                raise ValueError(f"Duplicate Depth of Faith episode id detected: {episode_id}")
            seen_ids.add(episode_id)

            title = raw.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError(f"Depth of Faith episode {episode_id} is missing title")

            description = raw.get("description") or raw.get("summary")
            if not isinstance(description, str) or not description.strip():
                raise ValueError(f"Depth of Faith episode {episode_id} is missing description")

            audio_field = (
                raw.get("audioFilename")
                or raw.get("audio_filename")
                or raw.get("audioFile")
                or raw.get("audio_file")
                or raw.get("audio")
                or raw.get("audioPath")
            )
            audio_filename = None
            if isinstance(audio_field, str) and audio_field.strip():
                candidate = Path(audio_field).name
                audio_filename = candidate or None
                if audio_filename and not audio_filename.lower().endswith(".mp3"):
                    raise ValueError(
                        f"Depth of Faith episode {episode_id} audio must be an MP3 file when provided"
                    )

            scripture = raw.get("scripture") or raw.get("scriptureText")
            if isinstance(scripture, str):
                scripture = scripture.strip() or None

            duration = raw.get("duration") or raw.get("length")
            if isinstance(duration, str):
                duration = duration.strip() or None

            published_at = raw.get("publishedAt") or raw.get("published_at") or raw.get("date")
            if isinstance(published_at, str):
                published_at = published_at.strip() or None

            episode = DepthOfFaithEpisode(
                id=episode_id,
                title=title.strip(),
                description=description.strip(),
                audioFilename=audio_filename,
                scripture=scripture,
                duration=duration,
                publishedAt=published_at,
            )
            episodes.append(episode)

        return episodes

    def _save_depth_of_faith_entries(self, episodes: list[DepthOfFaithEpisode]) -> None:
        tmp_path = DEPTH_OF_FAITH_FILE.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps([episode.model_dump(by_alias=True) for episode in episodes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(DEPTH_OF_FAITH_FILE)

    def _generate_episode_id(self, title: str, episodes: list[DepthOfFaithEpisode]) -> str:
        base = _slugify(title)
        if not base:
            base = uuid.uuid4().hex[:8]
        candidate = base
        existing = {episode.id for episode in episodes}
        counter = 2
        while candidate in existing:
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def list_depth_of_faith_episodes(self) -> list[DepthOfFaithEpisode]:
        return self._load_depth_of_faith_entries()

    def create_depth_of_faith_episode(self, payload: DepthOfFaithEpisodeCreate) -> DepthOfFaithEpisode:
        title = payload.title.strip()
        if not title:
            raise ValueError("需提供節目標題")
        description = payload.description.strip()
        if not description:
            raise ValueError("需提供節目描述")

        audio_filename = (payload.audio_filename or "").strip() or None

        episodes = self._load_depth_of_faith_entries()
        if payload.id and payload.id.strip():
            episode_id = payload.id.strip()
            if any(existing.id == episode_id for existing in episodes):
                raise ValueError(f"節目代號 {episode_id} 已存在")
        else:
            episode_id = self._generate_episode_id(title, episodes)

        episode = DepthOfFaithEpisode(
            id=episode_id,
            title=title,
            description=description,
            audioFilename=audio_filename,
            scripture=(payload.scripture or "").strip() or None,
            duration=(payload.duration or "").strip() or None,
            publishedAt=(payload.published_at or "").strip() or None,
        )
        episodes.append(episode)
        self._save_depth_of_faith_entries(episodes)
        return episode

    def update_depth_of_faith_episode(
        self,
        episode_id: str,
        payload: DepthOfFaithEpisodeUpdate,
    ) -> DepthOfFaithEpisode:
        episodes = self._load_depth_of_faith_entries()
        target_index = next((index for index, episode in enumerate(episodes) if episode.id == episode_id), None)
        if target_index is None:
            raise ValueError(f"Depth of Faith episode {episode_id} not found")

        current = episodes[target_index]

        title = payload.title.strip() if payload.title is not None else current.title
        if not title:
            raise ValueError("需提供節目標題")

        description = payload.description.strip() if payload.description is not None else current.description
        if not description:
            raise ValueError("需提供節目描述")

        audio_filename = (
            payload.audio_filename.strip() if payload.audio_filename is not None else current.audio_filename
        )
        if audio_filename is not None and audio_filename == "":
            audio_filename = None

        if payload.scripture is not None:
            scripture = payload.scripture.strip() or None
        else:
            scripture = current.scripture
        if payload.duration is not None:
            duration = payload.duration.strip() or None
        else:
            duration = current.duration
        if payload.published_at is not None:
            published_at = payload.published_at.strip() or None
        else:
            published_at = current.published_at

        updated = DepthOfFaithEpisode(
            id=current.id,
            title=title,
            description=description,
            audioFilename=audio_filename,
            scripture=scripture,
            duration=duration,
            publishedAt=published_at,
        )

        episodes[target_index] = updated
        self._save_depth_of_faith_entries(episodes)
        return updated

    def delete_depth_of_faith_episode(self, episode_id: str) -> None:
        episodes = self._load_depth_of_faith_entries()
        new_entries = [episode for episode in episodes if episode.id != episode_id]
        if len(new_entries) == len(episodes):
            raise ValueError(f"Depth of Faith episode {episode_id} not found")
        self._save_depth_of_faith_entries(new_entries)

    def resolve_depth_of_faith_audio(self, filename: str) -> Path:
        sanitized = Path(filename).name
        if not sanitized:
            raise ValueError("Audio filename is required")

        audio_path = (WEBCAST_DIR / sanitized).resolve()
        try:
            base_dir = WEBCAST_DIR.resolve()
        except FileNotFoundError:
            base_dir = WEBCAST_DIR

        if base_dir not in audio_path.parents and audio_path != base_dir:
            raise ValueError("Audio filename is not within the webcast directory")

        if not audio_path.exists():
            raise ValueError(f"Audio file {sanitized} not found")

        return audio_path

    def save_depth_of_faith_audio(self, original_filename: str, file_obj: BinaryIO) -> str:
        if not original_filename:
            raise ValueError("需提供檔案名稱")
        extension = Path(original_filename).suffix.lower()
        if extension != ".mp3":
            raise ValueError("僅支援上傳 MP3 格式")

        stem = _slugify(Path(original_filename).stem)
        if not stem:
            stem = uuid.uuid4().hex[:8]

        candidate = f"{stem}{extension}"
        counter = 2
        while (WEBCAST_DIR / candidate).exists():
            candidate = f"{stem}-{counter}{extension}"
            counter += 1

        destination = (WEBCAST_DIR / candidate).resolve()
        try:
            base_dir = WEBCAST_DIR.resolve()
        except FileNotFoundError:
            base_dir = WEBCAST_DIR

        if base_dir not in destination.parents and destination != base_dir:
            raise ValueError("無法儲存音訊檔案於指定目錄")

        file_obj.seek(0)
        with destination.open("wb") as buffer:
            shutil.copyfileobj(file_obj, buffer)
        file_obj.seek(0)
        return candidate


repository = ArticleRepository()
