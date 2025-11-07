from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Tuple, List
from collections.abc import Sequence

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


ArticleStatus = Literal["draft", "generated", "final"]
ArticleType = Literal["釋經", "神學觀點", "短文"]


class ArticleMetadata(BaseModel):
    id: str
    name: str
    slug: str
    subtitle: Optional[str] = None
    script_filename: str
    article_filename: str
    created_at: datetime
    updated_at: datetime
    status: ArticleStatus = "draft"
    model: Optional[str] = None
    last_generated_at: Optional[datetime] = None
    summary_markdown: Optional[str] = None
    article_type: Optional[ArticleType] = None
    core_bible_verses: List[str] = Field(default_factory=list)
    source_sermon_ids: List[str] = Field(default_factory=list)


class ArticleSummary(BaseModel):
    id: str
    name: str
    slug: str
    subtitle: Optional[str] = None
    status: ArticleStatus
    updated_at: datetime
    created_at: datetime
    model: Optional[str] = None
    summary_markdown: Optional[str] = Field(None, alias="summaryMarkdown")
    article_type: Optional[ArticleType] = Field(None, alias="articleType")
    core_bible_verses: List[str] = Field(default_factory=list, alias="coreBibleVerses")
    source_sermon_ids: List[str] = Field(default_factory=list, alias="sourceSermonIds")

    model_config = ConfigDict(populate_by_name=True)


class ArticleDetail(ArticleSummary):
    script_markdown: str = Field(..., alias="scriptMarkdown")
    article_markdown: str = Field(..., alias="articleMarkdown")
    prompt_markdown: str = Field(..., alias="promptMarkdown")

    model_config = ConfigDict(populate_by_name=True)


class SaveArticleRequest(BaseModel):
    id: Optional[str] = None
    name: str
    subtitle: Optional[str] = None
    script_markdown: str = Field(..., alias="scriptMarkdown")
    article_markdown: str = Field(..., alias="articleMarkdown")
    status: ArticleStatus = "draft"
    prompt_markdown: Optional[str] = Field(None, alias="promptMarkdown")
    summary_markdown: Optional[str] = Field(None, alias="summaryMarkdown")
    article_type: Optional[ArticleType] = Field(None, alias="articleType")
    core_bible_verses: List[str] = Field(default_factory=list, alias="coreBibleVerses")
    source_sermon_ids: List[str] = Field(default_factory=list, alias="sourceSermonIds")

    model_config = ConfigDict(populate_by_name=True)


class SaveArticleResponse(ArticleDetail):
    pass


class GenerateArticleRequest(BaseModel):
    script_markdown: Optional[str] = Field(None, alias="scriptMarkdown")
    prompt_markdown: Optional[str] = Field(None, alias="promptMarkdown")

    model_config = ConfigDict(populate_by_name=True)


class GenerateArticleResponse(BaseModel):
    article_markdown: str = Field(..., alias="articleMarkdown")
    status: ArticleStatus
    model: Optional[str] = None
    generated_at: datetime = Field(..., alias="generatedAt")

    model_config = ConfigDict(populate_by_name=True)


class GenerateSummaryResponse(BaseModel):
    summary_markdown: str = Field(..., alias="summaryMarkdown")
    model: Optional[str] = None
    generated_at: datetime = Field(..., alias="generatedAt")

    model_config = ConfigDict(populate_by_name=True)


class FellowshipEntry(BaseModel):
    date: str
    host: Optional[str] = None
    title: Optional[str] = None
    series: Optional[str] = None
    sequence: Optional[int] = None

    model_config = ConfigDict(populate_by_name=True)


SongSource = Literal["custom", "hymnal"]


class SundaySong(BaseModel):
    id: str
    title: str
    source: SongSource = "custom"
    lyrics_markdown: Optional[str] = Field(None, alias="lyricsMarkdown")
    hymn_link: Optional[str] = Field(None, alias="hymnLink")
    hymnal_index: Optional[int] = Field(None, alias="hymnalIndex")

    model_config = ConfigDict(populate_by_name=True)


class SundaySongCreate(BaseModel):
    title: str
    source: SongSource = "custom"
    lyrics_markdown: Optional[str] = Field(None, alias="lyricsMarkdown")
    hymn_link: Optional[str] = Field(None, alias="hymnLink")
    hymnal_index: Optional[int] = Field(None, alias="hymnalIndex")

    model_config = ConfigDict(populate_by_name=True)


class UnavailableDateRange(BaseModel):
    start_date: str = Field(..., alias="startDate")
    end_date: str = Field(..., alias="endDate")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _normalize_date(cls, value):
        if value is None:
            raise ValueError("Unavailable date is required")
        text = str(value).strip()
        if not text:
            raise ValueError("Unavailable date is required")
        return text

    @field_validator("start_date", "end_date")
    @classmethod
    def _validate_format(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Unavailable dates must use YYYY-MM-DD format") from exc
        return value

    @model_validator(mode="after")
    def _validate_order(self):
        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")
        if end < start:
            raise ValueError("Unavailable date range end must be after start date")
        return self

    def contains(self, date: str) -> bool:
        try:
            target = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return False
        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")
        return start <= target <= end


class SundayWorker(BaseModel):
    name: str
    email: Optional[str] = None
    unavailable_ranges: List[UnavailableDateRange] = Field(
        default_factory=list, alias="unavailableRanges"
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy(cls, data):
        if isinstance(data, dict):
            if "unavailableRanges" not in data:
                legacy = data.get("unavailable_dates") or data.get("unavailableDates")
                if legacy:
                    ranges = []
                    for value in legacy:
                        text = str(value).strip()
                        if not text:
                            continue
                        ranges.append({"startDate": text, "endDate": text})
                    if ranges:
                        data = dict(data)
                        data["unavailableRanges"] = ranges
        return data

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Worker name is required")
        return name

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def is_available_on(self, date: Optional[str]) -> bool:
        if not date:
            return True
        target = date.strip()
        if not target:
            return True
        return all(not range_.contains(target) for range_ in self.unavailable_ranges)


class SundayServiceEmailResult(BaseModel):
    date: str
    recipients: List[str]
    ppt_filename: str = Field(..., alias="pptFilename")
    subject: str
    dry_run: bool = Field(False, alias="dryRun")

    model_config = ConfigDict(populate_by_name=True)


class SundayServiceEntry(BaseModel):
    date: str
    presider: Optional[str] = None
    worship_leader: Optional[str] = Field(None, alias="worshipLeader")
    pianist: Optional[str] = None
    scripture: List[str] = Field(default_factory=list)
    sermon_speaker: Optional[str] = Field(None, alias="sermonSpeaker")
    sermon_title: Optional[str] = Field(None, alias="sermonTitle")
    hymn: Optional[str] = None
    hymn_index: Optional[int] = Field(None, alias="hymnIndex")
    response_hymn: Optional[str] = Field(None, alias="responseHymn")
    response_hymn_index: Optional[int] = Field(None, alias="responseHymnIndex")
    announcements_markdown: Optional[str] = Field("", alias="announcementsMarkdown")
    health_prayer_markdown: Optional[str] = Field("", alias="health_prayer_markdown")
    scripture_readers: List[str] = Field(default_factory=list, alias="scriptureReaders")
    hold_holy_communion: bool = Field(False, alias="holdHolyCommunion")
    final_ppt_filename: Optional[str] = Field(None, alias="finalPptFilename")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("scripture", mode="before")
    @classmethod
    def _normalize_scripture(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
        elif isinstance(value, Sequence):
            parts = []
            for item in value:
                if item is None:
                    continue
                parts.append(str(item).strip())
        else:
            return value

        normalized: list[str] = []
        seen: set[str] = set()
        for part in parts:
            if not part:
                continue
            if part in seen:
                continue
            normalized.append(part)
            seen.add(part)
        return normalized


class SundayServiceResources(BaseModel):
    workers: List[SundayWorker] = Field(default_factory=list)
    songs: List[SundaySong] = Field(default_factory=list)


class HymnMetadata(BaseModel):
    index: int
    title: str
    link: Optional[str] = None
    lyrics_url: Optional[str] = Field(None, alias="lyricsUrl")

    model_config = ConfigDict(populate_by_name=True)


class GenerateHymnLyricsRequest(BaseModel):
    title: str


class GenerateHymnLyricsResponse(BaseModel):
    lyrics_markdown: str = Field(..., alias="lyricsMarkdown")

    model_config = ConfigDict(populate_by_name=True)


class DepthOfFaithEpisode(BaseModel):
    id: str
    title: str
    description: str
    audio_filename: Optional[str] = Field(None, alias="audioFilename")
    scripture: Optional[str] = None
    duration: Optional[str] = None
    published_at: Optional[str] = Field(None, alias="publishedAt")

    model_config = ConfigDict(populate_by_name=True)


class DepthOfFaithEpisodeCreate(BaseModel):
    id: Optional[str] = None
    title: str
    description: str
    audio_filename: Optional[str] = Field(None, alias="audioFilename")
    scripture: Optional[str] = None
    duration: Optional[str] = None
    published_at: Optional[str] = Field(None, alias="publishedAt")

    model_config = ConfigDict(populate_by_name=True)


class DepthOfFaithEpisodeUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    audio_filename: Optional[str] = Field(None, alias="audioFilename")
    scripture: Optional[str] = None
    duration: Optional[str] = None
    published_at: Optional[str] = Field(None, alias="publishedAt")

    model_config = ConfigDict(populate_by_name=True)


class DepthOfFaithAudioUploadResponse(BaseModel):
    filename: str


class SermonSeries(BaseModel):
    id: str
    title: Optional[str] = None
    summary: Optional[str] = None
    topics: Optional[str] = None
    keypoints: Optional[str] = None
    sermons: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class PromptResponse(BaseModel):
    prompt_markdown: str = Field(..., alias="promptMarkdown")

    model_config = ConfigDict(populate_by_name=True)


class UpdatePromptRequest(BaseModel):
    prompt_markdown: str = Field(..., alias="promptMarkdown")

    model_config = ConfigDict(populate_by_name=True)


class SurmonSlideAsset(BaseModel):
    id: str
    image: str
    image_url: str
    timestamp_seconds: Optional[float] = None
    average_rgb: Optional[Tuple[int, int, int]] = None
    extracted_text: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)
