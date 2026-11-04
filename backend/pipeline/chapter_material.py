"""一章的材料：哪个系列的哪几堂讲道讲这一章，页面怎样按经节分段。

方案见 `docs/wang-knowledge-platform/30-authoring/matthew_chapter_exposition_page_solution.md`。

哪些段落是这一章的释经，不在这里推——由模型按定义逐片段决定，见
`chapter_exposition`。这里只放不需要判断的配置：系列与章的对照（负责人
2026-11-03 确认），以及和合本的分段。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.api.scripture import parse_reference


@dataclass(frozen=True)
class Chapter:
    """一章：哪一段经文、出自哪个系列的哪几堂讲道、页面怎样分段。"""

    passage: str
    series: str
    lectures: tuple[str, ...]
    #: 页面按经节次序分段，用和合本的分段。
    sections: tuple[str, ...] = field(default_factory=tuple)


_NYSC_2 = "2011 NYSC 專題：馬太福音釋經（二）"

#: 有按章页面的章。
CHAPTERS: dict[str, Chapter] = {
    "mat-5-1-48": Chapter(
        passage="mat-5-1-48",
        series=_NYSC_2,
        lectures=tuple(f"{_NYSC_2}{n}" for n in range(1, 7)),
        sections=(
            "mat-5-1-12",
            "mat-5-13-16",
            "mat-5-17-20",
            "mat-5-21-26",
            "mat-5-27-30",
            "mat-5-31-32",
            "mat-5-33-37",
            "mat-5-38-42",
            "mat-5-43-48",
        ),
    ),
}


def verse_range(slug: str) -> tuple[str, int, int, int]:
    """`mat-5-27-30` → (`mat`, 5, 27, 30)。"""

    reference = parse_reference(slug)
    return (
        str(reference["slug"]),
        int(reference["chapter"]),
        int(reference["start"]),
        int(reference["end"]),
    )


def section_of(chapter: Chapter, verse: str) -> str | None:
    """这一节归哪一段：看它的起点落在哪一段里。"""

    _, _, start, _ = verse_range(verse)
    for section in chapter.sections:
        _, _, low, high = verse_range(section)
        if low <= start <= high:
            return section
    return None
