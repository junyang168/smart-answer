"""教授的原声，按中心观点重排——读者侧的读取接口。

这一层不产出任何新的文字，只回答「教授在哪几个地方讲过这个判断」，每一处给一
个能寻址的位置。地址是 `观点 × 讲道 × 起止时间`；文章那边将来引用同一个地址就
能「听教授自己讲这一段」，不必重新设计。

现读现算。一次遍历几千条记录不到一秒，而缓存会在观点层改动之后继续端出旧的分
组——那正是这一页最不该说错的东西。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from backend.api.config import DATA_BASE_PATH
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import original_audio_index

router = APIRouter(prefix="/public/original-audio", tags=["original-audio"])


@router.get("")
def index(scripture: str = "16:18-19") -> dict[str, Any]:
    store = PostgresKnowledgeStore()
    return original_audio_index.build_index(store, Path(DATA_BASE_PATH), scripture)
