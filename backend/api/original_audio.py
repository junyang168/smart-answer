"""教授的原声，按经文重排——读者侧的读取接口。

这一层不产出任何新的文字，只回答「教授在哪几个地方讲过这段经文」，每一处给一
个能寻址的位置。地址是 `经文 × 讲道 × 起止时间`；文章那边将来引用同一个地址就
能「听教授自己讲这一段」，不必重新设计。

现读现算。一次遍历几千条记录不到一秒，而缓存会在观点层改动之后继续端出旧的分
组——那正是这一页最不该说错的东西。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.config import DATA_BASE_PATH
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import original_audio_index

router = APIRouter(prefix="/public/original-audio", tags=["original-audio"])


@router.get("/passages")
def passages() -> dict[str, Any]:
    """有原声的段落，各有多少可听——文库落地页上那一排。"""

    store = PostgresKnowledgeStore()
    return {"passages": original_audio_index.passage_summaries(store, Path(DATA_BASE_PATH))}


@router.get("/{passage}")
def index(passage: str) -> dict[str, Any]:
    """一段经文底下，教授讲过的每一段。

    只认列在 `PASSAGES` 里的段落。任由 URL 指定经文范围的话，`mat-16-14-15` 这
    种没人对过逐字稿的切法也会出一个页面——而「每个入口都对过逐字稿」是这一页
    的验收条件，不是可选项。
    """

    if passage not in {slug for slug, _ in original_audio_index.PASSAGES}:
        raise HTTPException(status_code=404, detail="這段經文還沒有原聲頁面。")
    store = PostgresKnowledgeStore()
    return original_audio_index.build_index(store, Path(DATA_BASE_PATH), passage)
