"""一章的材料：教授在这一章的系列讲道里，哪些主张属于这一章。

方案见 `docs/wang-knowledge-platform/30-authoring/matthew_chapter_exposition_page_solution.md`
第 4.1、4.2 节。负责人 2026-11-03 的三条决定都落在这里：

**从主张数据算出来，不手写选段表。** 两步：

1. 起点主张：该章系列讲道里，经文引用落在这一章、或落在这一章经文的平行经文上
   的主张。落在这一章沿用 `original_audio_index.in_passage` 的判法。
2. 往外一步：与起点主张有关系（支持、限定、提供语境、应用、回答、解释）的主张。

第二步就是「神学必须是从这一章的经文推出来的」在数据里的样子。他讲 5:28 时带进
雅各书的 epithymia，那几条主张引的是雅各书，按引用落不进太 5，可关系里记着它支
持哪一条 5:28 的判断。（二）3 中间三段见证（17–47 分）与任何一条太 5 的主张都没
有关系，自然不进来——不必按标题或主张类型逐类排除。

**只走一步。** 量过两步：（二）6 讲太 6「不叫我們遇見試探」的一段会被间接拉进
来。

**平行经文算同一节。** 太 5:32 的离婚，他在（二）4 的问答里是从路加福音 16:18、
马太福音 19:3-9 讲的。表里只列福音书之间同一句话的记载，不列旧约背景或主题相关
的经文——那些靠第二步的关系进来，或者不进来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from backend.api.scripture import parse_reference


#: 「往外一步」走哪几种关系。`refutes` 不走：他驳斥的说法不是从这节经文推出的
#: 他自己的判断。
RELATION_TYPES = frozenset({
    "supports",
    "qualifies",
    "contextualizes",
    "applies",
    "answers",
    "explains",
})


@dataclass(frozen=True)
class Chapter:
    """一章：哪一段经文、出自哪个系列的哪几堂讲道、有哪些平行经文。"""

    passage: str
    series: str
    lectures: tuple[str, ...]
    #: (本章经文, 平行经文……)。平行经文落在本章之外，命中时这条主张讲的就是本
    #: 章的那一节。
    parallels: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)


_NYSC_2 = "2011 NYSC 專題：馬太福音釋經（二）"

#: 有按章页面的章。系列与章的对照由负责人确认（方案第七节第 2 项）。
CHAPTERS: dict[str, Chapter] = {
    "mat-5-1-48": Chapter(
        passage="mat-5-1-48",
        series=_NYSC_2,
        lectures=tuple(f"{_NYSC_2}{n}" for n in range(1, 7)),
        parallels=(
            ("mat-5-3-12", ("luk-6-20-23",)),
            ("mat-5-13", ("mrk-9-50", "luk-14-34-35")),
            ("mat-5-14-16", ("mrk-4-21", "luk-8-16", "luk-11-33")),
            ("mat-5-18", ("luk-16-17",)),
            ("mat-5-25-26", ("luk-12-58-59",)),
            ("mat-5-29-30", ("mat-18-8-9", "mrk-9-43-48")),
            ("mat-5-31-32", ("mat-19-3-9", "mrk-10-2-12", "luk-16-18")),
            ("mat-5-38-42", ("luk-6-29-30",)),
            ("mat-5-43-48", ("luk-6-27-28", "luk-6-32-36")),
        ),
    ),
}


def _occurs_in(claim: dict[str, Any], lectures: Iterable[str]) -> bool:
    wanted = set(lectures)
    return any(
        str(occurrence.get("transcript_id") or "") in wanted
        for occurrence in claim.get("occurrences") or []
    )


def _range(slug: str) -> tuple[str, int, int, int]:
    reference = parse_reference(slug)
    return (
        str(reference["slug"]),
        int(reference["chapter"]),
        int(reference["start"]),
        int(reference["end"]),
    )


def select(
    chapter: Chapter,
    claims: dict[str, dict[str, Any]],
    relations: Iterable[dict[str, Any]],
    in_passage: Callable[..., bool],
    claim_verse: Callable[..., str],
) -> dict[str, str]:
    """这一章的主张，以及每条讲的是哪一节。

    返回 `claim_id → 经文 slug`。起点主张用自己引用里落在本章的那一节；经平行经
    文进来的，用平行经文对应的本章那一节；往外一步进来的，用它连到的起点主张的
    那一节——幻灯上摆的是他此刻在解的太 5 经文，不是雅各书。

    `in_passage`、`claim_verse` 由调用方传入，免得这里反过来 import 原声索引。
    """

    book, number, low, high = _range(chapter.passage)
    own = {
        claim_id: claim
        for claim_id, claim in claims.items()
        if _occurs_in(claim, chapter.lectures)
    }

    seeds: dict[str, str] = {}
    for claim_id, claim in own.items():
        if in_passage(claim, book, number, low, high):
            seeds[claim_id] = claim_verse(claim, book, number, low, high)
            continue
        for verse, parallels in chapter.parallels:
            if any(in_passage(claim, *_range(parallel)) for parallel in parallels):
                seeds[claim_id] = verse
                break

    selected = dict(seeds)
    for relation in relations:
        if str(relation.get("relation_type") or "") not in RELATION_TYPES:
            continue
        ends = (str(relation.get("from_id") or ""), str(relation.get("to_id") or ""))
        for here, there in (ends, ends[::-1]):
            if here in seeds and there in own and there not in selected:
                selected[there] = seeds[here]
    return selected
