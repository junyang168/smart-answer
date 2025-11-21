from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse

from .models import (
    ArticleDetail,
    ArticleSummary,
    DepthOfFaithEpisode,
    DepthOfFaithEpisodeCreate,
    DepthOfFaithEpisodeUpdate,
    DepthOfFaithAudioUploadResponse,
    FellowshipEntry,
    GenerateArticleRequest,
    GenerateArticleResponse,
    GenerateSummaryResponse,
    PromptResponse,
    SaveArticleRequest,
    SaveArticleResponse,
    SermonSeries,
    GenerateHymnLyricsRequest,
    GenerateHymnLyricsResponse,
    HymnMetadata,
    SundayServiceEntry,
    SundayServiceResources,
    SundayServiceEmailResult,
    SundayServiceEmailBody,
    SundaySong,
    SundaySongCreate,
    SundayWorker,
    UpdatePromptRequest,
)
from .service import (
    generate_article,
    generate_summary,
    get_article,
    get_prompt,
    list_fellowships,
    create_fellowship,
    update_fellowship,
    delete_fellowship,
    list_sermon_series,
    create_sermon_series,
    update_sermon_series,
    delete_sermon_series,
    list_articles,
    new_article_template,
    save_article,
    delete_article,
    commit_article,
    update_prompt,
    list_sunday_services,
    create_sunday_service,
    update_sunday_service,
    delete_sunday_service,
    generate_sunday_service_ppt,
    email_sunday_service,
    upload_final_sunday_service_ppt,
    get_final_sunday_service_ppt,
    get_sunday_service_email_body,
    update_sunday_service_email_body,
    list_sunday_workers,
    create_sunday_worker,
    update_sunday_worker,
    delete_sunday_worker,
    list_sunday_songs,
    create_sunday_song,
    update_sunday_song,
    delete_sunday_song,
    sunday_service_resources,
    create_depth_of_faith_episode,
    update_depth_of_faith_episode,
    delete_depth_of_faith_episode,
    upload_depth_of_faith_audio,
    list_depth_of_faith_episodes,
    get_depth_of_faith_audio,
    get_hymn_metadata,
    generate_hymn_lyrics,
)

router = APIRouter(prefix="/admin/full-articles", tags=["full-articles"])


@router.get("", response_model=list[ArticleSummary])
def list_full_articles() -> list[ArticleSummary]:
    return list_articles()


@router.get("/prompt", response_model=PromptResponse)
def read_prompt() -> PromptResponse:
    return get_prompt()


@router.put("/prompt", response_model=PromptResponse)
def write_prompt(payload: UpdatePromptRequest) -> PromptResponse:
    return update_prompt(payload.prompt_markdown)


@router.get("/new", response_model=SaveArticleResponse)
def new_article() -> SaveArticleResponse:
    return new_article_template()


@router.get("/{article_id}", response_model=ArticleDetail)
def retrieve_article(article_id: str) -> ArticleDetail:
    return get_article(article_id)


@router.post("", response_model=SaveArticleResponse)
def create_or_update_article(payload: SaveArticleRequest) -> SaveArticleResponse:
    return save_article(payload)


@router.delete("/{article_id}")
def delete_article_endpoint(article_id: str) -> dict[str, str]:
    delete_article(article_id)
    return {"message": "Article deleted successfully"}


@router.post("/{article_id}/commit")
def commit_article_endpoint(article_id: str) -> dict[str, str]:
    result = commit_article(article_id)
    return {"message": "Committed successfully", "commit_id": result}


@router.post("/{article_id}/generate", response_model=GenerateArticleResponse)
def regenerate_article(article_id: str, payload: GenerateArticleRequest) -> GenerateArticleResponse:
    return generate_article(article_id, payload)


@router.post("/{article_id}/summary", response_model=GenerateSummaryResponse)
def regenerate_summary(article_id: str) -> GenerateSummaryResponse:
    return generate_summary(article_id)


fellowship_router = APIRouter(prefix="/admin/fellowships", tags=["fellowships"])


@fellowship_router.get("", response_model=list[FellowshipEntry])
def get_fellowships() -> list[FellowshipEntry]:
    return list_fellowships()


@fellowship_router.post("", response_model=FellowshipEntry)
def create_fellowship_entry(entry: FellowshipEntry) -> FellowshipEntry:
    return create_fellowship(entry)


@fellowship_router.put("/{date:path}", response_model=FellowshipEntry)
def update_fellowship_entry(date: str, entry: FellowshipEntry) -> FellowshipEntry:
    return update_fellowship(date, entry)


@fellowship_router.delete("/{date:path}")
def delete_fellowship_entry(date: str) -> None:
    delete_fellowship(date)

surmon_series_router = APIRouter(prefix="/admin/surmon-series", tags=["surmon-series"])


@surmon_series_router.get("", response_model=list[SermonSeries])
def get_sermon_series() -> list[SermonSeries]:
    return list_sermon_series()


@surmon_series_router.post("", response_model=SermonSeries)
def create_sermon_series_entry(payload: SermonSeries) -> SermonSeries:
    return create_sermon_series(payload)


@surmon_series_router.put("/{series_id}", response_model=SermonSeries)
def update_sermon_series_entry(series_id: str, payload: SermonSeries) -> SermonSeries:
    return update_sermon_series(series_id, payload)


@surmon_series_router.delete("/{series_id}")
def delete_sermon_series_entry(series_id: str) -> None:
    delete_sermon_series(series_id)


sunday_service_router = APIRouter(prefix="/admin/sunday-services", tags=["sunday-services"])


@sunday_service_router.get("", response_model=list[SundayServiceEntry])
def get_sunday_services() -> list[SundayServiceEntry]:
    return list_sunday_services()


@sunday_service_router.post("", response_model=SundayServiceEntry)
def create_sunday_service_entry(entry: SundayServiceEntry) -> SundayServiceEntry:
    return create_sunday_service(entry)


@sunday_service_router.get("/{date:path}/email-body", response_model=SundayServiceEmailBody)
def read_sunday_service_email_body(date: str) -> SundayServiceEmailBody:
    html = get_sunday_service_email_body(date)
    return SundayServiceEmailBody(html=html)


@sunday_service_router.put("/{date:path}/email-body", response_model=SundayServiceEmailBody)
def write_sunday_service_email_body(date: str, payload: SundayServiceEmailBody) -> SundayServiceEmailBody:
    updated = update_sunday_service_email_body(date, payload.html)
    return SundayServiceEmailBody(html=updated.email_body_html or "")


@sunday_service_router.put("/{date:path}", response_model=SundayServiceEntry)
def update_sunday_service_entry(date: str, entry: SundayServiceEntry) -> SundayServiceEntry:
    return update_sunday_service(date, entry)


@sunday_service_router.delete("/{date:path}")
def delete_sunday_service_entry(date: str) -> None:
    delete_sunday_service(date)


@sunday_service_router.get("/resources", response_model=SundayServiceResources)
def get_sunday_service_resources() -> SundayServiceResources:
    return sunday_service_resources()


@sunday_service_router.post("/{date:path}/ppt")
def create_sunday_service_ppt(date: str) -> FileResponse:
    ppt_path = generate_sunday_service_ppt(date)
    return FileResponse(
        ppt_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=ppt_path.name,
    )


@sunday_service_router.post("/{date:path}/ppt/final", response_model=SundayServiceEntry)
async def upload_sunday_service_final_ppt(date: str, file: UploadFile = File(...)) -> SundayServiceEntry:
    return upload_final_sunday_service_ppt(date, file)


@sunday_service_router.get("/{date:path}/ppt/final")
def download_sunday_service_final_ppt(date: str) -> FileResponse:
    ppt_path = get_final_sunday_service_ppt(date)
    return FileResponse(
        ppt_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=ppt_path.name,
    )


@sunday_service_router.post("/{date:path}/email", response_model=SundayServiceEmailResult)
def send_sunday_service_email(date: str) -> SundayServiceEmailResult:
    return email_sunday_service(date)


sunday_workers_router = APIRouter(prefix="/admin/sunday-workers", tags=["sunday-workers"])


@sunday_workers_router.get("", response_model=list[SundayWorker])
def get_sunday_workers() -> list[SundayWorker]:
    return list_sunday_workers()


@sunday_workers_router.post("", response_model=SundayWorker)
def create_sunday_worker_entry(worker: SundayWorker) -> SundayWorker:
    return create_sunday_worker(worker)


@sunday_workers_router.put("/{current_name:path}", response_model=SundayWorker)
def update_sunday_worker_entry(current_name: str, worker: SundayWorker) -> SundayWorker:
    return update_sunday_worker(current_name, worker)


@sunday_workers_router.delete("/{name:path}")
def delete_sunday_worker_entry(name: str) -> None:
    delete_sunday_worker(name)


sunday_songs_router = APIRouter(prefix="/admin/sunday-songs", tags=["sunday-songs"])


@sunday_songs_router.get("", response_model=list[SundaySong])
def get_sunday_songs() -> list[SundaySong]:
    return list_sunday_songs()


@sunday_songs_router.post("", response_model=SundaySong)
def create_sunday_song_entry(payload: SundaySongCreate) -> SundaySong:
    return create_sunday_song(payload)


@sunday_songs_router.put("/{song_id}", response_model=SundaySong)
def update_sunday_song_entry(song_id: str, payload: SundaySongCreate) -> SundaySong:
    return update_sunday_song(song_id, payload)


@sunday_songs_router.delete("/{song_id}")
def delete_sunday_song_entry(song_id: str) -> None:
    delete_sunday_song(song_id)


@sunday_songs_router.get("/hymnal/{index}", response_model=HymnMetadata)
def read_hymn_metadata(index: int) -> HymnMetadata:
    return get_hymn_metadata(index)


@sunday_songs_router.post("/hymnal/{index}/lyrics", response_model=GenerateHymnLyricsResponse)
def create_hymn_lyrics(index: int, payload: GenerateHymnLyricsRequest) -> GenerateHymnLyricsResponse:
    return GenerateHymnLyricsResponse(lyrics_markdown=generate_hymn_lyrics(index, payload))


webcast_router = APIRouter(prefix="/webcast", tags=["webcast"])


@webcast_router.get("/depth-of-faith", response_model=list[DepthOfFaithEpisode])
def list_depth_of_faith_entries() -> list[DepthOfFaithEpisode]:
    return list_depth_of_faith_episodes()


@webcast_router.get("/depth-of-faith/audio/{audio_filename}")
def stream_depth_of_faith_audio(audio_filename: str) -> FileResponse:
    audio_path = get_depth_of_faith_audio(audio_filename)
    return FileResponse(audio_path, media_type="audio/mpeg", filename=audio_path.name)


webcast_admin_router = APIRouter(prefix="/admin/webcast/depth-of-faith", tags=["webcast-admin"])


@webcast_admin_router.get("", response_model=list[DepthOfFaithEpisode])
def admin_list_depth_of_faith_entries() -> list[DepthOfFaithEpisode]:
    return list_depth_of_faith_episodes()


@webcast_admin_router.post("", response_model=DepthOfFaithEpisode)
def admin_create_depth_of_faith_entry(payload: DepthOfFaithEpisodeCreate) -> DepthOfFaithEpisode:
    return create_depth_of_faith_episode(payload)


@webcast_admin_router.put("/{episode_id}", response_model=DepthOfFaithEpisode)
def admin_update_depth_of_faith_entry(
    episode_id: str,
    payload: DepthOfFaithEpisodeUpdate,
) -> DepthOfFaithEpisode:
    return update_depth_of_faith_episode(episode_id, payload)


@webcast_admin_router.delete("/{episode_id}")
def admin_delete_depth_of_faith_entry(episode_id: str) -> None:
    delete_depth_of_faith_episode(episode_id)


@webcast_admin_router.post("/upload", response_model=DepthOfFaithAudioUploadResponse)
def admin_upload_depth_of_faith_audio(file: UploadFile = File(...)) -> DepthOfFaithAudioUploadResponse:
    filename = upload_depth_of_faith_audio(file)
    return DepthOfFaithAudioUploadResponse(filename=filename)
