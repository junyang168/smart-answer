"""一章的材料：哪个系列的哪几堂讲道讲这一章，页面怎样按经节分段。

方案见 `docs/wang-knowledge-platform/30-authoring/matthew_chapter_exposition_page_solution.md`。

哪些段落是这一章的释经，不在这里推——由模型按定义逐片段决定，见
`chapter_exposition`。这里只放不需要判断的配置：哪一章用哪个系列的哪几堂（负责人
2026-11-03 确认系列与章的对照），以及和合本的分段——存在
`DATA_BASE_DIR/chapter_exposition/chapters.json`。

加一章：按逐字稿里念到的章，连同前后一堂，写进这个文件，跑
`chapter_exposition`；没有片段保留的那一堂从文件里拿掉，再跑 `chapter_slides`
（加 `--review`）和 `chapter_title`。不用改代码，不用部署。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

from backend.api.scripture import parse_reference


@dataclass(frozen=True)
class Chapter:
    """一章：哪一段经文、出自哪个系列的哪几堂讲道、页面怎样分段。"""

    passage: str
    series: str
    lectures: tuple[str, ...]
    #: 页面按经节次序分段，用和合本的分段。
    sections: tuple[str, ...] = field(default_factory=tuple)
    #: 这些讲道属于哪个来源（讲道目录的 `source_organization`）；文库入口按它把
    #: 按章页面放在这个来源下面。
    source: str = "紐約靈命進深會"


#: 有逐節聆聽页面的章，存在 `DATA_BASE_DIR` 里，不在代码里（负责人 2026-11-05：
#: 加一章不该要合并、部署）。每章：经文、来源、系列、讲了这一章的那几堂、和合本
#: 的分段。改了文件，下一个请求就读到新的。
CHAPTERS_FILE = Path("chapter_exposition") / "chapters.json"
SCHEMA_VERSION = "chapter_pages_v1"

_log = logging.getLogger(__name__)


def chapters_path(data_base_dir: Path | None = None) -> Path:
    if data_base_dir is None:
        from backend.api.config import DATA_BASE_PATH

        data_base_dir = Path(DATA_BASE_PATH)
    return Path(data_base_dir) / CHAPTERS_FILE


def problems(chapter: Chapter) -> list[str]:
    """一章的配置哪里不对：分段要从第 1 节接到最后一节，至少一堂讲道。"""

    out = []
    try:
        book, number, start, end = verse_range(chapter.passage)
        expected = start
        for section in chapter.sections:
            s_book, s_number, low, high = verse_range(section)
            if (s_book, s_number, low) != (book, number, expected) or high < low:
                out.append(f"{chapter.passage}: 分段 {section} 没有接上 {number}:{expected}")
                break
            expected = high + 1
        else:
            if expected != end + 1:
                out.append(f"{chapter.passage}: 分段停在 {number}:{expected - 1}，不到 {number}:{end}")
    except (ValueError, KeyError, TypeError) as exc:
        out.append(f"{chapter.passage}: 读不出经文（{exc}）")
    if not chapter.lectures:
        out.append(f"{chapter.passage}: 没有讲道")
    return out


def load_chapters(path: Path) -> dict[str, Chapter]:
    """读章的清单。文件不在就是没有章；格式不对的那一章跳过并记日志，不让整站出错。"""

    if not path.is_file():
        return {}
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: schema_version 应是 {SCHEMA_VERSION}")
    out: dict[str, Chapter] = {}
    for entry in record.get("chapters") or []:
        chapter = Chapter(
            passage=str(entry["passage"]),
            series=str(entry.get("series") or ""),
            lectures=tuple(entry.get("lectures") or ()),
            sections=tuple(entry.get("sections") or ()),
            source=str(entry.get("source") or "紐約靈命進深會"),
        )
        found = problems(chapter)
        if found:
            _log.warning("chapters.json 跳过一章：%s", "；".join(found))
            continue
        out[chapter.passage] = chapter
    return out


class _Chapters(MutableMapping):
    """`CHAPTERS`：每次用时按文件的修改时间重读；测试可以临时放进一章。"""

    def __init__(self) -> None:
        self._cache: tuple[tuple[str, int], dict[str, Chapter]] | None = None
        self._extra: dict[str, Chapter] = {}

    def _loaded(self) -> dict[str, Chapter]:
        path = chapters_path()
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            key = (str(path), -1)
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, load_chapters(path))
        return {**self._cache[1], **self._extra}

    def __getitem__(self, passage: str) -> Chapter:
        return self._loaded()[passage]

    def __iter__(self) -> Iterator[str]:
        return iter(self._loaded())

    def __len__(self) -> int:
        return len(self._loaded())

    def __setitem__(self, passage: str, chapter: Chapter) -> None:
        self._extra[passage] = chapter

    def __delitem__(self, passage: str) -> None:
        del self._extra[passage]


CHAPTERS = _Chapters()


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
