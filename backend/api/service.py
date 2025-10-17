from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status

from .gemini_client import gemini_client
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
    SermonSeries,
)
from .storage import repository


def compose_generation_prompt(prompt_template: str, script_markdown: str) -> str:
    placeholder = "{{SCRIPT}}"
    if placeholder in prompt_template:
        return prompt_template.replace(placeholder, script_markdown)
    return f"{prompt_template.rstrip()}\n\n---\n\n{script_markdown}".strip()


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
