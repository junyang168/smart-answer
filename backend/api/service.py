from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import math
import re
from urllib.parse import quote_plus

import httpx
from fastapi import HTTPException, UploadFile, status

from .gemini_client import gemini_client
from .models import (
    ArticleDetail,
    ArticleSummary,
    DepthOfFaithEpisode,
    DepthOfFaithEpisodeCreate,
    DepthOfFaithEpisodeUpdate,
    FellowshipEntry,
    GenerateArticleRequest,
    GenerateArticleResponse,
    GenerateSummaryResponse,
    PromptResponse,
    SaveArticleRequest,
    SaveArticleResponse,
    SermonSeries,
    SundayServiceEntry,
    SundayServiceResources,
    SundayWorker,
    HymnMetadata,
    SundaySong,
    GenerateHymnLyricsRequest,
    GenerateHymnLyricsResponse,
    SundaySongCreate,
)
from .storage import repository
from .config import SUNDAY_WORSHIP_DIR, PPT_TEMPLATE_FILE
from .ppt_generator import generate_presentation_from_template
from .scripture import parse_reference, BIBLE_API_TRANSLATION_ZH, ALIAS_TO_API_BOOK, BOOK_SLUG_TO_NAME
from .webpage_extractor import fetch_lyrics_text

def compose_generation_prompt(prompt_template: str, script_markdown: str) -> str:
    placeholder = "{{SCRIPT}}"
    if placeholder in prompt_template:
        return prompt_template.replace(placeholder, script_markdown)
    return f"{prompt_template.rstrip()}\n\n---\n\n{script_markdown}".strip()


def _raise_value_error(exc: ValueError, *, not_found_status: int = status.HTTP_404_NOT_FOUND) -> None:
    message = str(exc)
    if "not found" in message.lower():
        raise HTTPException(status_code=not_found_status, detail=message) from exc
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc


def _prepare_song_payload(payload: SundaySongCreate) -> SundaySongCreate:
    if payload.source != "hymnal":
        return payload
    if payload.hymnal_index is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="需提供詩歌索引")
    try:
        hymn = repository.get_hymn_metadata(payload.hymnal_index)
    except ValueError as exc:
        _raise_value_error(exc)
    return payload.model_copy(update={"title": hymn.title, "hymn_link": hymn.link or None})


def list_articles() -> list[ArticleSummary]:
    return repository.list_articles()


def get_article(article_id: str) -> ArticleDetail:
    try:
        return repository.get_article(article_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def save_article(payload: SaveArticleRequest) -> SaveArticleResponse:
    try:
        return repository.save_article(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def generate_article(article_id: str, payload: GenerateArticleRequest) -> GenerateArticleResponse:
    article = get_article(article_id)
    script_md = payload.script_markdown or article.script_markdown
    prompt_md = payload.prompt_markdown or article.prompt_markdown
    combined_prompt = compose_generation_prompt(prompt_md, script_md)

    try:
        generated_md = gemini_client.generate(combined_prompt)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    updated_detail = repository.update_generated_article(
        article_id=article_id,
        article_markdown=generated_md,
        model_name="gemini-2.5-pro",
        status="generated",
    )

    return GenerateArticleResponse(
        articleMarkdown=updated_detail.article_markdown,
        status=updated_detail.status,
        model=updated_detail.model,
        generatedAt=datetime.now(timezone.utc),
    )


def get_prompt() -> PromptResponse:
    prompt = repository.load_prompt()
    return PromptResponse(promptMarkdown=prompt)


def update_prompt(prompt_markdown: str) -> PromptResponse:
    repository.save_prompt(prompt_markdown)
    return PromptResponse(promptMarkdown=prompt_markdown)


def new_article_template() -> SaveArticleResponse:
    return repository.get_new_article_template()


def generate_summary(article_id: str) -> GenerateSummaryResponse:
    article = get_article(article_id)
    article_md = article.article_markdown.strip()
    if not article_md:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文章內容為空，無法生成摘要")

    prompt = (
        "請根據以下基督教福音派文章內容撰寫一段約 150 字的摘要，以 Markdown 格式輸出，"
        "著重於核心論點與應用。\n\n文章內容：\n" + article_md
    )

    try:
        summary_markdown = gemini_client.generate(prompt)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    repository.update_article_summary(article_id, summary_markdown, model_name="gemini-2.5-pro")

    return GenerateSummaryResponse(
        summaryMarkdown=summary_markdown,
        model="gemini-2.5-pro",
        generatedAt=datetime.now(timezone.utc),
    )


def list_fellowships() -> list[FellowshipEntry]:
    return repository.list_fellowships()


def create_fellowship(entry: FellowshipEntry) -> FellowshipEntry:
    try:
        return repository.create_fellowship(entry)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def update_fellowship(date: str, entry: FellowshipEntry) -> FellowshipEntry:
    try:
        return repository.update_fellowship(date, entry)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def delete_fellowship(date: str) -> None:
    try:
        repository.delete_fellowship(date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def get_sunday_service(date: str) -> SundayServiceEntry:
    try:
        return repository.get_sunday_service(date)
    except ValueError as exc:
        _raise_value_error(exc)


def list_sunday_services() -> list[SundayServiceEntry]:
    return repository.list_sunday_services()


def create_sunday_service(entry: SundayServiceEntry) -> SundayServiceEntry:
    try:
        return repository.create_sunday_service(entry)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def update_sunday_service(date: str, entry: SundayServiceEntry) -> SundayServiceEntry:
    try:
        return repository.update_sunday_service(date, entry)
    except ValueError as exc:
        _raise_value_error(exc)


def delete_sunday_service(date: str) -> None:
    try:
        repository.delete_sunday_service(date)
    except ValueError as exc:
        _raise_value_error(exc)


def list_sunday_workers() -> list[SundayWorker]:
    return repository.list_sunday_workers()


def create_sunday_worker(worker: SundayWorker) -> SundayWorker:
    try:
        return repository.create_sunday_worker(worker)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def update_sunday_worker(current_name: str, worker: SundayWorker) -> SundayWorker:
    try:
        return repository.update_sunday_worker(current_name, worker)
    except ValueError as exc:
        _raise_value_error(exc)


def delete_sunday_worker(name: str) -> None:
    try:
        repository.delete_sunday_worker(name)
    except ValueError as exc:
        _raise_value_error(exc)


def list_sunday_songs() -> list[SundaySong]:
    return repository.list_sunday_songs()


def create_sunday_song(payload: SundaySongCreate) -> SundaySong:
    try:
        prepared = _prepare_song_payload(payload)
        return repository.create_sunday_song(prepared)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def update_sunday_song(song_id: str, payload: SundaySongCreate) -> SundaySong:
    try:
        prepared = _prepare_song_payload(payload)
        return repository.update_sunday_song(song_id, prepared)
    except ValueError as exc:
        _raise_value_error(exc)


def delete_sunday_song(song_id: str) -> None:
    try:
        repository.delete_sunday_song(song_id)
    except ValueError as exc:
        _raise_value_error(exc)


def sunday_service_resources() -> SundayServiceResources:
    return SundayServiceResources(workers=list_sunday_workers(), songs=list_sunday_songs())


def get_hymn_metadata(index: int) -> HymnMetadata:
    try:
        return repository.get_hymn_metadata(index)
    except ValueError as exc:
        _raise_value_error(exc)



def generate_hymn_lyrics(index: int, payload: GenerateHymnLyricsRequest) -> GenerateHymnLyricsResponse:
    hymn = get_hymn_metadata(index)
    title = payload.title.strip() if payload.title else hymn.title
    if title != hymn.title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="詩歌標題與索引不相符")
    
    return fetch_lyrics_text(hymn.lyrics_url)



def list_depth_of_faith_episodes() -> list[DepthOfFaithEpisode]:
    try:
        return repository.list_depth_of_faith_episodes()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


def create_depth_of_faith_episode(payload: DepthOfFaithEpisodeCreate) -> DepthOfFaithEpisode:
    try:
        return repository.create_depth_of_faith_episode(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def update_depth_of_faith_episode(
    episode_id: str,
    payload: DepthOfFaithEpisodeUpdate,
) -> DepthOfFaithEpisode:
    try:
        return repository.update_depth_of_faith_episode(episode_id, payload)
    except ValueError as exc:
        _raise_value_error(exc)


def delete_depth_of_faith_episode(episode_id: str) -> None:
    try:
        repository.delete_depth_of_faith_episode(episode_id)
    except ValueError as exc:
        _raise_value_error(exc)


def get_depth_of_faith_audio(audio_filename: str) -> Path:
    try:
        return repository.resolve_depth_of_faith_audio(audio_filename)
    except ValueError as exc:
        _raise_value_error(exc)


def upload_depth_of_faith_audio(file: UploadFile) -> str:
    filename = file.filename or ""
    try:
        return repository.save_depth_of_faith_audio(filename, file.file)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


def generate_sunday_service_ppt(date: str) -> Path:
    service = get_sunday_service(date)
    songs = {song.title: song for song in repository.list_sunday_songs()}

    hymn = songs.get(service.hymn or "") if service.hymn else None
    response_hymn = songs.get(service.response_hymn or "") if service.response_hymn else None

    readers = [reader.strip() for reader in service.scripture_readers if reader.strip()]

    replacements = _build_ppt_replacements(service, hymn, response_hymn, readers)
    if readers:
        replacements["reader"] = f"⇉讀經 by {readers[0]}"
    else:
        replacements["reader"] = ""
    summary_data: list[dict[str, str]] = []

    section_configs: dict[str, dict[str, object]] = {}
    if hymn and hymn.lyrics_markdown:
        sections = _split_lyrics_sections(hymn.lyrics_markdown)
        if sections:
            index_label = _format_hymn_index_label(service.hymn_index)
            section_configs["hymnLyrics"] = {
                "sections": [
                    {"lines": lines, "index": index_label, "index_token": "hymn_index"}
                    for lines in sections
                ],
                "style": "lyrics",
            }
    if response_hymn and response_hymn.lyrics_markdown:
        sections = _split_lyrics_sections(response_hymn.lyrics_markdown)
        if sections:
            index_label = _format_hymn_index_label(service.response_hymn_index)
            section_configs["responseHymnLyrics"] = {
                "sections": [
                    {"lines": lines, "index": index_label, "index_token": "responseHymn_index"}
                    for lines in sections
                ],
                "style": "lyrics",
            }

    scripture_display, scripture_lines = _fetch_scripture_lines(service.scripture)
    if scripture_display:
        replacements.setdefault("scriptureReference", scripture_display)
    else:
        replacements.setdefault("scriptureReference", "")
    if scripture_lines:
        scripture_sections = _split_scripture_sections(scripture_lines)
        if scripture_sections:
            scripture_sections_data, scripture_summary = _assign_scripture_sections(
                scripture_sections,
                readers,
                scripture_display,
            )
            section_configs["scriptureVerses"] = {
                "sections": scripture_sections_data,
                "style": "scripture",
            }
            summary_data = scripture_summary
        else:
            summary_data = []
    else:
        summary_data = []

    _populate_future_service_tokens(replacements, service)

    if not PPT_TEMPLATE_FILE.exists():
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="找不到 PPT 樣板檔")

    SUNDAY_WORSHIP_DIR.mkdir(parents=True, exist_ok=True)
    filename = _build_ppt_filename(date, service)
    output_path = SUNDAY_WORSHIP_DIR / filename

    generate_presentation_from_template(
        PPT_TEMPLATE_FILE,
        replacements,
        output_path,
        section_configs=section_configs,
        scripture_summary=summary_data,
    )
    return output_path


def list_sermon_series() -> list[SermonSeries]:
    return repository.list_sermon_series()


def create_sermon_series(series: SermonSeries) -> SermonSeries:
    try:
        return repository.create_sermon_series(series)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def update_sermon_series(series_id: str, series: SermonSeries) -> SermonSeries:
    try:
        return repository.update_sermon_series(series_id, series)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def delete_sermon_series(series_id: str) -> None:
    try:
        repository.delete_sermon_series(series_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


_WEEKDAY_LABELS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
_INVALID_FILENAME_CHARS = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff_-]+")


def _build_ppt_replacements(
    service: SundayServiceEntry,
    hymn: SundaySong | None,
    response_hymn: SundaySong | None,
    readers: list[str],
) -> dict[str, str]:
    replacements: dict[str, str] = {}

    for key, value in (
        ("date", service.date),
        ("presider", service.presider),
        ("worshipLeader", service.worship_leader),
        ("pianist", service.pianist),
        ("hymn", service.hymn),
        ("hymn2", service.response_hymn),
        ("scripture", service.scripture),
        ("sermonSpeaker", service.sermon_speaker),
        ("sermonTitle", service.sermon_title),
        ("announcements", service.announcements_markdown),
        ("healthPrayer", service.health_prayer_markdown),
    ):
        replacements[key] = _normalize_text(value)

    replacements["scriptureReaders"] = "、".join(readers)
    replacements["scriptureReader"] = readers[0] if readers else ""
    for idx in range(3):
        replacements[f"scriptureReader{idx + 1}"] = readers[idx] if idx < len(readers) else ""

    sermon_speaker = replacements.get("sermonSpeaker", "")
    sermon_title = replacements.get("sermonTitle", "")
    if sermon_speaker and sermon_title:
        replacements["sermonSpeakerTitle"] = f"{sermon_speaker}《{sermon_title}》"
    else:
        replacements["sermonSpeakerTitle"] = sermon_title or sermon_speaker

    worship_team = [value for value in (
        replacements.get("presider"),
        replacements.get("worshipLeader"),
        replacements.get("pianist"),
    ) if value]
    replacements["worshipTeam"] = " / ".join(worship_team)

    service_date = _parse_service_date(service.date)
    if service_date:
        replacements["dateISO"] = service_date.strftime("%Y-%m-%d")
        replacements["dateDisplay"] = service_date.strftime("%Y/%m/%d")
        replacements["dateLong"] = service_date.strftime("%Y年%m月%d日")
        replacements["dateYear"] = str(service_date.year)
        replacements["dateMonth"] = str(service_date.month)
        replacements["dateDay"] = str(service_date.day)
        weekday_label = _WEEKDAY_LABELS[service_date.weekday()]
        replacements["dateWeekday"] = weekday_label
        replacements["dateWithWeekday"] = f"{replacements['dateLong']}（{weekday_label}）"

    _apply_song_replacements(replacements, "hymn", service.hymn, hymn)
    _apply_song_replacements(replacements, "hymn2", service.response_hymn, response_hymn)

    _populate_fellowship_replacements(replacements, service_date)

    return replacements


def _apply_song_replacements(
    replacements: dict[str, str],
    prefix: str,
    title: str | None,
    song: SundaySong | None,
) -> None:
    replacements[f"{prefix}Title"] = _normalize_text(title)
    if song:
        source = "教會聖詩" if song.source == "hymnal" else "自訂"
        replacements[f"{prefix}Source"] = source
        replacements[f"{prefix}Index"] = source + ' ' + str(song.hymnal_index) + ' 首' if song.hymnal_index is not None else ""
        replacements[f"{prefix}Link"] = song.hymn_link or ""
        replacements[f"{prefix}Lyrics"] = song.lyrics_markdown or ""
    else:
        replacements[f"{prefix}Source"] = ""
        replacements[f"{prefix}Index"] = ""
        replacements[f"{prefix}Link"] = ""
        replacements[f"{prefix}Lyrics"] = ""


def _populate_fellowship_replacements(
    replacements: dict[str, str],
    service_date: datetime | None,
) -> None:
    defaults = {
        "fellowDate1": "",
        "hasFellow1": "無",
        "fellowDate2": "",
        "hasFellow2": "無",
    }
    replacements.update({key: defaults[key] for key in defaults if key not in replacements})

    if not service_date:
        return

    fellowship_entries = list_fellowships()
    fellowship_dates = {
        parsed for entry in fellowship_entries if (parsed := _parse_fellowship_date(entry.date))
    }

    first_friday = _upcoming_friday(service_date)
    second_friday = first_friday + timedelta(weeks=1)

    for date_key, flag_key, target_date in (
        ("fellowDate1", "hasFellow1", first_friday),
        ("fellowDate2", "hasFellow2", second_friday),
    ):
        replacements[date_key] = target_date.strftime("%m/%d")
        replacements[flag_key] = "有" if target_date.date() in fellowship_dates else "無"


def _upcoming_friday(reference: datetime) -> datetime:
    days_until_friday = (4 - reference.weekday()) % 7
    return reference + timedelta(days=days_until_friday)


def _parse_fellowship_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _markdown_to_plain_text(markdown: str | None) -> str:
    if not markdown:
        return ""
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("- "):
            lines.append(f"• {stripped[2:].strip()}")
        elif re.match(r"^\d+\.\s+", stripped):
            lines.append(stripped)
        else:
            lines.append(stripped)
    return "\n".join(lines)


def _normalize_text(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_service_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _build_ppt_filename(date: str, service: SundayServiceEntry) -> str:
    parsed = _parse_service_date(date)
    base = parsed.strftime("%Y-%m-%d") if parsed else _sanitize_for_filename(date)
    if not base:
        base = "worship"
    title_part = _sanitize_for_filename(service.sermon_title) if service.sermon_title else ""
    parts = [part for part in (base, title_part, "主日敬拜") if part]
    return "_".join(parts) + ".pptx"


def _sanitize_for_filename(value: str | None) -> str:
    if not value:
        return ""
    sanitized = _INVALID_FILENAME_CHARS.sub("_", value.strip())
    sanitized = sanitized.strip("_")
    return sanitized


def _split_lyrics_sections(raw: str) -> list[list[str]]:
    text = raw.strip()
    if not text:
        return []
    sections = re.split(r"(?:\r?\n){2,}", text)
    result: list[list[str]] = []
    for section in sections:
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        if lines:
            result.append(lines)
    return result


def _fetch_scripture_lines(reference: str | None) -> tuple[str | None, list[str]]:
    if not reference:
        return None, []
    try:
        info = parse_reference(reference)
    except ValueError:
        return None, []

    translation = BIBLE_API_TRANSLATION_ZH
    if not translation:
        return info.get("display"), []

    verse_part = (
        f"{info['chapter']}:{info['start']}-{info['end']}"
        if info["end"] != info["start"]
        else f"{info['chapter']}:{info['start']}"
    )
#    english_book = ALIAS_TO_API_BOOK.get(info["slug_book"], info["slug_book"])
    book_slug = info["slug_book"]
    query = quote_plus(f"{book_slug} {verse_part}")
    url = f"https://bible-api.com/{query}?translation={translation}"

    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError:
        return info.get("display"), []

    payload = response.json()
    verses = payload.get("verses") or []
    lines: list[str] = []
    for verse in verses:
        text = (verse.get("text") or "").strip()
        if not text:
            continue
        verse_number = verse.get("verse")
        if verse_number is not None:
            lines.append(f"{info['chapter']}:{verse_number} {text}")
        else:
            lines.append(text)

    display = info.get("display")
    slug = info.get("slug")
    if slug and slug in BOOK_SLUG_TO_NAME:
        chinese_name = BOOK_SLUG_TO_NAME[slug]
        display = f"{chinese_name} {verse_part}"

    return display, lines


def _split_scripture_sections(lines: list[str]) -> list[list[str]]:
    total = len(lines)
    if total == 0:
        return []

    def balanced(slides: int) -> list[int]:
        base = total // slides
        remainder = total % slides
        return [base + (1 if idx < remainder else 0) for idx in range(slides)]

    chosen_sizes: list[int] | None = None
    for slides in range(1, min(3, total) + 1):
        sizes = balanced(slides)
        if all(5 <= size <= 7 for size in sizes):
            chosen_sizes = sizes
            break

    if chosen_sizes is None:
        slides = min(3, max(1, math.ceil(total / 7)))
        sizes = balanced(slides)
        while slides > 1 and sizes[-1] < 5:
            sizes[-2] += sizes[-1]
            sizes.pop()
            slides -= 1
        chosen_sizes = sizes

    sections: list[list[str]] = []
    index = 0
    for size in chosen_sizes:
        sections.append(lines[index : index + size])
        index += size
    if index < total:
        sections[-1].extend(lines[index:])
    return sections


def _populate_future_service_tokens(replacements: dict[str, str], current_service: SundayServiceEntry) -> None:
    current_date = _parse_service_date(current_service.date)
    if current_date is None:
        for idx in (1, 2):
            _apply_future_service_defaults(replacements, idx)
        return

    future_entries: list[tuple[datetime, SundayServiceEntry]] = []
    for entry in repository.list_sunday_services():
        entry_date = _parse_service_date(entry.date)
        if entry_date is None or entry_date <= current_date:
            continue
        future_entries.append((entry_date, entry))

    future_entries.sort(key=lambda item: item[0])

    for idx in (1, 2):
        if idx <= len(future_entries):
            _, entry = future_entries[idx - 1]
            _apply_future_service_values(replacements, idx, entry)
        else:
            _apply_future_service_defaults(replacements, idx)


def _apply_future_service_defaults(replacements: dict[str, str], position: int) -> None:
    replacements[f"date{position}"] = ""
    replacements[f"sermonSpeaker{position}"] = ""
    replacements[f"presider{position}"] = ""
    replacements[f"worshipLeader{position}"] = ""
    replacements[f"pianist{position}"] = ""


def _apply_future_service_values(
    replacements: dict[str, str],
    position: int,
    entry: SundayServiceEntry,
) -> None:
    entry_date = _parse_service_date(entry.date)
    replacements[f"date{position}"] = entry_date.strftime("%m/%d") if entry_date else ""
    replacements[f"sermonSpeaker{position}"] = _normalize_text(entry.sermon_speaker)
    replacements[f"presider{position}"] = _normalize_text(entry.presider)
    replacements[f"worshipLeader{position}"] = _normalize_text(entry.worship_leader)
    replacements[f"pianist{position}"] = _normalize_text(entry.pianist)


def _assign_scripture_sections(
    sections: list[list[str]],
    readers: list[str],
    scripture_display: str | None,
) -> tuple[list[dict[str, object]], list[str]]:
    assigned: list[dict[str, object]] = []
    summary: list[str] = []
    book_name = ""
    if scripture_display:
        book_name = scripture_display.split(" ")[0]

    for idx, lines in enumerate(sections):
        if not lines:
            continue
        reader = readers[idx] if idx < len(readers) and readers[idx] else (readers[idx % len(readers)] if readers else "")
        chapter_start, verse_start = _parse_scripture_line(lines[0])
        chapter_end, verse_end = _parse_scripture_line(lines[-1])
        label = _format_scripture_section_label(
            book_name,
            chapter_start,
            verse_start,
            chapter_end,
            verse_end,
        )
        assigned.append({"lines": lines, "reader": reader, "label": label})
        summary_line = {"label" : label}
        if reader:
            summary_line['reader'] = reader
        summary.append(summary_line)
    return assigned, summary


def _parse_scripture_line(line: str) -> tuple[int | None, int | None]:
    match = re.match(r"^(?P<chapter>\d+):(\d+)", line.strip())
    if not match:
        return None, None
    chapter = int(match.group(1))
    verse = int(match.group(2))
    return chapter, verse


def _format_scripture_section_label(
    book_name: str,
    chapter_start: int | None,
    verse_start: int | None,
    chapter_end: int | None,
    verse_end: int | None,
) -> str:
    if chapter_start is None or verse_start is None:
        return book_name or "讀經"
    if chapter_end is None or verse_end is None or (chapter_end == chapter_start and verse_end == verse_start):
        return f"{book_name} {chapter_start}:{verse_start}" if book_name else f"{chapter_start}:{verse_start}"
    if chapter_start == chapter_end:
        return f"{book_name} {chapter_start}:{verse_start}-{verse_end}" if book_name else f"{chapter_start}:{verse_start}-{verse_end}"
    return (
        f"{book_name} {chapter_start}:{verse_start}-{chapter_end}:{verse_end}" if book_name else f"{chapter_start}:{verse_start}-{chapter_end}:{verse_end}"
    )


def _format_hymn_index_label(index: Optional[int]) -> str:
    if index is None:
        return ""
    return f"教會聖詩 {index} 首"
