from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Tuple, List

from pydantic import BaseModel, Field, ConfigDict


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


class SundayWorker(BaseModel):
    name: str


class SundayServiceEntry(BaseModel):
    date: str
    presider: Optional[str] = None
    worship_leader: Optional[str] = Field(None, alias="worshipLeader")
    pianist: Optional[str] = None
    scripture: Optional[str] = None
    sermon_speaker: Optional[str] = Field(None, alias="sermonSpeaker")
    sermon_title: Optional[str] = Field(None, alias="sermonTitle")
    hymn: Optional[str] = None
    hymn_index: Optional[int] = Field(None, alias="hymnIndex")
    response_hymn: Optional[str] = Field(None, alias="responseHymn")
    response_hymn_index: Optional[int] = Field(None, alias="responseHymnIndex")
    announcements_markdown: Optional[str] = Field("", alias="announcementsMarkdown")
    health_prayer_markdown: Optional[str] = Field("", alias="health_prayer_markdown")
    scripture_readers: List[str] = Field(default_factory=list, alias="scriptureReaders")

    model_config = ConfigDict(populate_by_name=True)


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
    text : Optional[str] = None
