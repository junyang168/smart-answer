"""文库入口：按书卷、来源、章找他的讲道。

方案见 `docs/wang-knowledge-platform/30-authoring/repository_book_source_navigation_solution.md`。

三层：书卷 + 来源 → 这卷书的各章 → 一章（有按章页面的进按章页面）。

- **来源是讲道所属的机构**，读讲道目录每篇的 `source_organization`；没有记下机构
  的，用目录的类别标签。来源清单从数据汇总，不写死——负责人：来源以后还会增加。
- 一篇讲道归哪卷书、哪一章，看目录的 `canonical_scriptures`（它的主要经文），不
  看 `books`——`books` 是提到过的书卷，哥林多前书因此有 124 篇。
- 釋經筆記读马太福音来源地图里 `notes_to_manuscript` 的条目。

现读现算：目录两百来篇，一次遍历不到一秒。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from backend.api.scripture import BOOK_TO_OSIS, CHINESE_BOOKS

router = APIRouter(prefix="/public/repository", tags=["public-repository"])

#: 笔记这个来源的显示名（负责人 2026-11-04）。
NOTES = "教授釋經筆記"

_SLUG_OF_OSIS = {osis: alias.lower() for alias, osis in BOOK_TO_OSIS.items()}
_ORDER = {slug: index for index, (slug, _) in enumerate(CHINESE_BOOKS)}
_NAME = dict(CHINESE_BOOKS)
#: 和合本前 39 卷是旧约。
_OLD_TESTAMENT = {slug for slug, _ in CHINESE_BOOKS[:39]}
_OSIS_CHAPTER = re.compile(r"^(?P<book>[1-3]?[A-Za-z]+)\.(?P<chapter>\d+)")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def source_of(record: dict[str, Any]) -> str:
    """讲道属于哪个来源：记下的机构，否则类别标签。"""

    return str(record.get("source_organization") or record.get("source_category_label") or "來源待確認")


def _chapters(record: dict[str, Any]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for osis in record.get("canonical_scriptures") or []:
        match = _OSIS_CHAPTER.match(str(osis))
        if not match:
            continue
        slug = _SLUG_OF_OSIS.get(match["book"])
        if slug:
            out.add((slug, int(match["chapter"])))
    return out


def items(catalog: dict[str, Any], coverage: dict[str, Any]) -> list[dict[str, Any]]:
    """每一份材料：来源、标题、链接、讲到的（书卷, 章）。"""

    out = []
    for record in catalog.get("records") or []:
        chapters = _chapters(record)
        if not chapters:
            continue
        transcript_id = str(record.get("transcript_id") or "")
        out.append({
            "source": source_of(record),
            "kind": "sermon",
            "id": transcript_id,
            "title": str(record.get("title") or transcript_id),
            "date": record.get("deliver_date"),
            "href": f"/resources/sermons/{quote(transcript_id, safe='')}",
            "chapters": chapters,
        })
    for entry in coverage.get("source_directory") or []:
        if entry.get("source_category") != "notes_to_manuscript" or not entry.get("assigned_chapters"):
            continue
        out.append({
            "source": NOTES,
            "kind": "notes",
            "id": str(entry.get("source_id") or ""),
            "title": str(entry.get("title") or "").strip(),
            "date": None,
            "href": entry.get("source_url"),
            "chapters": {("mat", int(chapter)) for chapter in entry["assigned_chapters"]},
        })
    return out


def _count(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, int] = {}
    for item in materials:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    return [
        {"name": name, "count": count, "unit": "講" if name == NOTES else "篇"}
        for name, count in sorted(by_source.items(), key=lambda pair: -pair[1])
    ]


def books(materials: list[dict[str, Any]]) -> dict[str, Any]:
    """第一层：来源清单，和有材料的书卷（各来源各几篇）。"""

    by_book: dict[str, list[dict[str, Any]]] = {}
    for item in materials:
        for slug in {slug for slug, _ in item["chapters"]}:
            by_book.setdefault(slug, []).append(item)
    return {
        "sources": _count(materials),
        "books": [
            {
                "slug": slug,
                "name": _NAME[slug],
                "testament": "old" if slug in _OLD_TESTAMENT else "new",
                "sources": _count(by_book[slug]),
            }
            for slug in sorted(by_book, key=lambda slug: _ORDER.get(slug, 999))
        ],
    }


def chapter_pages() -> dict[tuple[str, int], dict[str, Any]]:
    """有按章页面的章，和只有按段落原声页的章。"""

    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline import chapter_material, original_audio_index
    from backend.api.scripture import parse_reference

    pages: dict[tuple[str, int], dict[str, Any]] = {}
    for chapter in chapter_material.CHAPTERS.values():
        reference = parse_reference(chapter.passage)
        pages[(str(reference["slug"]).lower(), int(reference["chapter"]))] = {
            "kind": "chapter",
            "source": chapter.source,
            "passage": chapter.passage,
            # 书卷页每章一行写它讲什么；按章页面的标题就是这一句（负责人 2026-11-05）。
            "title": original_audio_index.passage_title(chapter.passage, Path(DATA_BASE_PATH)),
            # 全是录像就叫「逐節觀看」，否则「逐節聆聽」（负责人 2026-11-05）。
            "media": "video" if all(
                (found := original_audio_index.media_file(Path(DATA_BASE_PATH), lecture)) and found[1] == "video"
                for lecture in chapter.lectures
            ) else "audio",
        }
    for passage, label in original_audio_index.PASSAGES:
        reference = parse_reference(passage)
        key = (str(reference["slug"]).lower(), int(reference["chapter"]))
        if key in pages and pages[key]["kind"] == "chapter":
            continue
        page = pages.setdefault(key, {"kind": "passages", "source": "紐約靈命進深會", "passages": []})
        page["passages"].append({
            "passage": passage,
            "label": label,
            "href": f"{original_audio_index.PASSAGE_URL_PREFIX}/{passage}",
        })
    return pages


def book_chapters(materials: list[dict[str, Any]], slug: str, articles: list[dict[str, Any]]) -> dict[str, Any]:
    """第二层：这卷书有讲道的章，各来源各几篇，有什么页面，有没有综合文章。"""

    pages = chapter_pages()
    by_chapter: dict[int, list[dict[str, Any]]] = {}
    for item in materials:
        for book, chapter in item["chapters"]:
            if book == slug:
                by_chapter.setdefault(chapter, []).append(item)
    return {
        "slug": slug,
        "name": _NAME[slug],
        # 一篇讲几章只算一次。
        "sources": _count(list({(i["kind"], i["id"]): i for rows in by_chapter.values() for i in rows}.values())),
        "chapters": [
            {
                "chapter": chapter,
                "sources": _count(by_chapter[chapter]),
                "page": {k: v for k, v in pages[(slug, chapter)].items() if k != "passages"}
                if (slug, chapter) in pages else None,
                "articles": [a for a in articles if (slug, chapter) in a["chapters"]],
            }
            for chapter in sorted(by_chapter)
        ],
    }


def chapter(materials: list[dict[str, Any]], slug: str, number: int, articles: list[dict[str, Any]]) -> dict[str, Any]:
    """第三层：一章，按来源分开；有按章页面的来源给页面，其余列出讲道。"""

    page = chapter_pages().get((slug, number))
    here = [item for item in materials if (slug, number) in item["chapters"]]
    sources = []
    for entry in _count(here):
        listed = sorted(
            (item for item in here if item["source"] == entry["name"]),
            key=lambda item: (str(item["date"] or ""), item["title"]),
        )
        source = {
            **entry,
            "items": [{k: item[k] for k in ("kind", "id", "title", "date", "href")} for item in listed],
        }
        if page and page["source"] == entry["name"]:
            source["page"] = page
        sources.append(source)
    if not sources:
        raise HTTPException(status_code=404, detail="這一章還沒有講道。")
    return {
        "slug": slug,
        "name": _NAME[slug],
        "chapter": number,
        "sources": sources,
        "articles": [a for a in articles if (slug, number) in a["chapters"]],
    }


def _articles() -> list[dict[str, Any]]:
    """已发布的综合文章，各归它的经文所在的章（专题文章归每一处核心经文）。"""

    from backend.api.public_wang_articles import list_public_articles

    out = []
    for article in list_public_articles()["articles"]:
        references = article.get("core_scripture") if article.get("product_kind") == "theological_topic_essay" else None
        references = references or ([article["scripture"]] if article.get("scripture") else [])
        chapters = set()
        for reference in references:
            slug = _SLUG_OF_OSIS.get(str(reference.get("book") or ""))
            if slug:
                chapters.add((slug, int(reference.get("chapter") or 0)))
        out.append({"title": article["title"], "href": article["href"], "chapters": chapters})
    return out


def _public(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"title": a["title"], "href": a["href"]} for a in articles]


def _materials() -> list[dict[str, Any]]:
    from backend.api.config import WANG_MATTHEW_SOURCE_COVERAGE_FILE, WANG_SERMON_CATALOG_FILE

    return items(_read(WANG_SERMON_CATALOG_FILE), _read(WANG_MATTHEW_SOURCE_COVERAGE_FILE))


def _book(slug: str) -> str:
    slug = slug.lower()
    if slug not in _NAME:
        raise HTTPException(status_code=404, detail="沒有這卷書。")
    return slug


@router.get("/books")
def get_books() -> dict[str, Any]:
    return books(_materials())


@router.get("/books/{slug}")
def get_book(slug: str) -> dict[str, Any]:
    slug = _book(slug)
    result = book_chapters(_materials(), slug, _articles())
    if not result["chapters"]:
        raise HTTPException(status_code=404, detail="這卷書還沒有講道。")
    for row in result["chapters"]:
        row["articles"] = _public(row["articles"])
    return result


@router.get("/books/{slug}/{number}")
def get_chapter(slug: str, number: int) -> dict[str, Any]:
    result = chapter(_materials(), _book(slug), number, _articles())
    result["articles"] = _public(result["articles"])
    return result
