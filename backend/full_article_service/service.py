from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status

from .gemini_client import gemini_client
from .models import (
    ArticleDetail,
    ArticleSummary,
    GenerateArticleRequest,
    GenerateArticleResponse,
    PromptResponse,
    SaveArticleRequest,
    SaveArticleResponse,
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
