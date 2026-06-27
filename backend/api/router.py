from __future__ import annotations

import re
from email.utils import parseaddr
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel


from .models import (
    ArticleDetail,
    ArticleSummary,
    DepthOfFaithEpisode,
    DepthOfFaithEpisodeCreate,
    DepthOfFaithEpisodeUpdate,
    DepthOfFaithAudioUploadResponse,
    FellowshipDocument,
    FellowshipEntry,
    FellowshipAnalysisAssets,
    FellowshipAnalysisJob,
    FellowshipEmailContent,
    FellowshipEmailResult,
    FellowshipLearningContent,
    GenerateArticleRequest,
    GenerateArticleResponse,
    GenerateSummaryResponse,
    MicroSermon,
    MicroSermonCreate,
    MicroSermonUpdate,
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
    get_fellowship_email_content,
    update_fellowship_email_content,
    email_fellowship,
    list_fellowship_documents,
    get_fellowship_document_path,
    update_fellowship_learning_content,
    generate_fellowship_learning_content,
    get_fellowship_analysis_job,
    list_public_fellowship_documents,
    resolve_fellowship_analysis_assets,
    run_fellowship_analysis_job,
    start_fellowship_analysis_job,
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
    list_micro_sermons,
    get_featured_micro_sermon,
    create_micro_sermon,
    update_micro_sermon,
    delete_micro_sermon,
)
from backend.api.sunday_service_email import (
    send_email,
    _html_to_text,
    determine_notification_recipients_file,
    load_notification_recipients,
    NOTIFICATION_PRODUCTION,
    TEST_RECIPIENT,
    EMAIL_PRODUCTION,
)


class EmailRequest(BaseModel):
    subject: str
    body: str  # HTML body
    recipients_type: str = "congregation"  # Default to congregation


class EmailRecipientsResponse(BaseModel):
    recipients: list[str]
    count: int
    file_path: str
    production: bool


class EmailRecipientsUpdateRequest(BaseModel):
    recipients: list[str]


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_recipient(raw_value: str) -> str:
    _, parsed_email = parseaddr(raw_value)
    candidate = (parsed_email or raw_value).strip()
    if not candidate or not _EMAIL_PATTERN.fullmatch(candidate):
        raise ValueError(f"Invalid email address: {raw_value}")
    return candidate


def _dedupe_recipients(values: list[str]) -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = _normalize_recipient(value)
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        recipients.append(candidate)
    if not recipients:
        raise ValueError("At least one recipient is required")
    return recipients


def _notification_recipients_path() -> Path:
    return determine_notification_recipients_file(NOTIFICATION_PRODUCTION)


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


@fellowship_router.get("/{date:path}/email-body", response_model=FellowshipEmailContent)
def read_fellowship_email_content(date: str) -> FellowshipEmailContent:
    return get_fellowship_email_content(date)


@fellowship_router.put("/{date:path}/email-body", response_model=FellowshipEmailContent)
def write_fellowship_email_content(
    date: str,
    payload: FellowshipEmailContent,
) -> FellowshipEmailContent:
    return update_fellowship_email_content(date, payload)


@fellowship_router.post("/{date:path}/email", response_model=FellowshipEmailResult)
def send_fellowship_email(date: str) -> FellowshipEmailResult:
    return email_fellowship(date)


@fellowship_router.put("/{date:path}/learning", response_model=FellowshipLearningContent)
def write_fellowship_learning_content(
    date: str,
    payload: FellowshipLearningContent,
) -> FellowshipLearningContent:
    return update_fellowship_learning_content(date, payload)


@fellowship_router.post("/{date:path}/learning/generate", response_model=FellowshipLearningContent)
def generate_fellowship_learning(date: str) -> FellowshipLearningContent:
    return generate_fellowship_learning_content(date)


@fellowship_router.get("/{date:path}/analysis/assets", response_model=FellowshipAnalysisAssets)
def read_fellowship_analysis_assets(date: str) -> FellowshipAnalysisAssets:
    return resolve_fellowship_analysis_assets(date)


@fellowship_router.post("/{date:path}/analysis/generate", response_model=FellowshipAnalysisJob)
def generate_fellowship_analysis(date: str, background_tasks: BackgroundTasks) -> FellowshipAnalysisJob:
    job = start_fellowship_analysis_job(date)
    background_tasks.add_task(run_fellowship_analysis_job, job.job_id)
    return job


@fellowship_router.get("/{date:path}/analysis/jobs/{job_id}", response_model=FellowshipAnalysisJob)
def read_fellowship_analysis_job(date: str, job_id: str) -> FellowshipAnalysisJob:
    return get_fellowship_analysis_job(date, job_id)


@fellowship_router.get("/{date:path}/documents", response_model=list[FellowshipDocument])
def read_fellowship_documents(
    date: str,
    public_only: bool = Query(False, alias="publicOnly"),
) -> list[FellowshipDocument]:
    if public_only:
        return list_public_fellowship_documents(date)
    return list_fellowship_documents(date)


@fellowship_router.get("/{date:path}/documents/{document_path:path}")
def download_fellowship_document(date: str, document_path: str) -> FileResponse:
    path, media_type = get_fellowship_document_path(date, document_path)
    return FileResponse(path, media_type=media_type, filename=path.name)


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
    filename = upload_depth_of_faith_audio(file)
    return DepthOfFaithAudioUploadResponse(filename=filename)


email_router = APIRouter(prefix="/admin/email", tags=["email"])


@email_router.get("/recipients", response_model=EmailRecipientsResponse)
def get_email_recipients() -> EmailRecipientsResponse:
    recipients_path = _notification_recipients_path()
    recipients = load_notification_recipients(recipients_path)
    return EmailRecipientsResponse(
        recipients=recipients,
        count=len(recipients),
        file_path=str(recipients_path),
        production=NOTIFICATION_PRODUCTION,
    )


@email_router.put("/recipients", response_model=EmailRecipientsResponse)
def update_email_recipients(req: EmailRecipientsUpdateRequest) -> EmailRecipientsResponse:
    try:
        recipients = _dedupe_recipients(req.recipients)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recipients_path = _notification_recipients_path()
    recipients_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = recipients_path.with_suffix(f"{recipients_path.suffix}.tmp")
    tmp_path.write_text("\n".join(recipients) + "\n", encoding="utf-8")
    tmp_path.replace(recipients_path)

    return EmailRecipientsResponse(
        recipients=recipients,
        count=len(recipients),
        file_path=str(recipients_path),
        production=NOTIFICATION_PRODUCTION,
    )


@email_router.post("/send")
def send_custom_email(req: EmailRequest):
    # Determine recipients
    if req.recipients_type == "congregation":
        recipients_path = _notification_recipients_path()
        recipients = load_notification_recipients(recipients_path)
    else:
        # Fallback or other types can be added here
        raise HTTPException(status_code=400, detail=f"Unknown recipients type: {req.recipients_type}")

    recipient_list = list(recipients)
    if not EMAIL_PRODUCTION:
        recipient_list = [TEST_RECIPIENT]

    text_body = _html_to_text(req.body)

    try:
        send_email(
            recipients=recipient_list,
            subject=req.subject,
            text_body=text_body,
            html_body=req.body,
        )
        return {"status": "success", "recipient_count": len(recipient_list)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Micro Sermon Admin Router
micro_sermon_admin_router = APIRouter(prefix="/admin/micro-sermons", tags=["micro-sermons-admin"])


@micro_sermon_admin_router.get("", response_model=list[MicroSermon])
def admin_list_micro_sermons() -> list[MicroSermon]:
    return list_micro_sermons()


@micro_sermon_admin_router.post("", response_model=MicroSermon)
def admin_create_micro_sermon(payload: MicroSermonCreate) -> MicroSermon:
    return create_micro_sermon(payload)


@micro_sermon_admin_router.put("/{sermon_id}", response_model=MicroSermon)
def admin_update_micro_sermon(sermon_id: str, payload: MicroSermonUpdate) -> MicroSermon:
    return update_micro_sermon(sermon_id, payload)


@micro_sermon_admin_router.delete("/{sermon_id}")
def admin_delete_micro_sermon(sermon_id: str) -> None:
    delete_micro_sermon(sermon_id)


# Micro Sermon Public Router
micro_sermon_public_router = APIRouter(prefix="/micro-sermons", tags=["micro-sermons"])


@micro_sermon_public_router.get("", response_model=list[MicroSermon])
def public_list_micro_sermons() -> list[MicroSermon]:
    return list_micro_sermons()


@micro_sermon_public_router.get("/featured", response_model=MicroSermon | None)
def public_get_featured_micro_sermon() -> MicroSermon | None:
    return get_featured_micro_sermon()
