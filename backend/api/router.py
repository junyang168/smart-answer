from __future__ import annotations

from fastapi import APIRouter

from .models import (
    ArticleDetail,
    ArticleSummary,
    FellowshipEntry,
    GenerateArticleRequest,
    GenerateArticleResponse,
    GenerateSummaryResponse,
    PromptResponse,
    SaveArticleRequest,
    SaveArticleResponse,
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
    list_articles,
    new_article_template,
    save_article,
    update_prompt,
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
