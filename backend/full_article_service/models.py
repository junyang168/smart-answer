from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict


ArticleStatus = Literal["draft", "generated", "final"]


class ArticleMetadata(BaseModel):
    id: str
    name: str
    slug: str
    script_filename: str
    article_filename: str
    created_at: datetime
    updated_at: datetime
    status: ArticleStatus = "draft"
    model: Optional[str] = None
    last_generated_at: Optional[datetime] = None


class ArticleSummary(BaseModel):
    id: str
    name: str
    slug: str
    status: ArticleStatus
    updated_at: datetime
    created_at: datetime
    model: Optional[str] = None


class ArticleDetail(ArticleSummary):
    script_markdown: str = Field(..., alias="scriptMarkdown")
    article_markdown: str = Field(..., alias="articleMarkdown")
    prompt_markdown: str = Field(..., alias="promptMarkdown")

    model_config = ConfigDict(populate_by_name=True)


class SaveArticleRequest(BaseModel):
    id: Optional[str] = None
    name: str
    script_markdown: str = Field(..., alias="scriptMarkdown")
    article_markdown: str = Field(..., alias="articleMarkdown")
    status: ArticleStatus = "draft"
    prompt_markdown: Optional[str] = Field(None, alias="promptMarkdown")

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


class PromptResponse(BaseModel):
    prompt_markdown: str = Field(..., alias="promptMarkdown")

    model_config = ConfigDict(populate_by_name=True)


class UpdatePromptRequest(BaseModel):
    prompt_markdown: str = Field(..., alias="promptMarkdown")

    model_config = ConfigDict(populate_by_name=True)
