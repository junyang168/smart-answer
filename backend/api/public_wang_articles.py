from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from backend.api.config import WANG_REPOSITORY_DIR
from backend.api.canonical_repository.service import CanonicalRepositoryService


router = APIRouter(prefix="/public/wang-articles", tags=["wang-articles-public"])

EDITORIAL_DRAFT_ROOT = WANG_REPOSITORY_DIR
APPROVAL_SCHEMA_VERSIONS = {
    "human-publication-decision.v1",
    "automated-publication-decision.v1",
}

BOOK_SLUGS = {
    "Matt": "matthew",
    "Mark": "mark",
    "Luke": "luke",
    "John": "john",
    "Acts": "acts",
    "Rom": "romans",
}

BOOK_METADATA = {
    "創": ("Gen", "創世記"), "出": ("Exod", "出埃及記"), "利": ("Lev", "利未記"),
    "民": ("Num", "民數記"), "申": ("Deut", "申命記"), "書": ("Josh", "約書亞記"),
    "士": ("Judg", "士師記"), "得": ("Ruth", "路得記"), "撒上": ("1Sam", "撒母耳記上"),
    "撒下": ("2Sam", "撒母耳記下"), "王上": ("1Kgs", "列王紀上"), "王下": ("2Kgs", "列王紀下"),
    "代上": ("1Chr", "歷代志上"), "代下": ("2Chr", "歷代志下"), "拉": ("Ezra", "以斯拉記"),
    "尼": ("Neh", "尼希米記"), "斯": ("Esth", "以斯帖記"), "伯": ("Job", "約伯記"),
    "詩": ("Ps", "詩篇"), "箴": ("Prov", "箴言"), "傳": ("Eccl", "傳道書"),
    "歌": ("Song", "雅歌"), "賽": ("Isa", "以賽亞書"), "耶": ("Jer", "耶利米書"),
    "哀": ("Lam", "耶利米哀歌"), "結": ("Ezek", "以西結書"), "但": ("Dan", "但以理書"),
    "何": ("Hos", "何西阿書"), "珥": ("Joel", "約珥書"), "摩": ("Amos", "阿摩司書"),
    "俄": ("Obad", "俄巴底亞書"), "拿": ("Jonah", "約拿書"), "彌": ("Mic", "彌迦書"),
    "鴻": ("Nah", "那鴻書"), "哈": ("Hab", "哈巴谷書"), "番": ("Zeph", "西番雅書"),
    "該": ("Hag", "哈該書"), "亞": ("Zech", "撒迦利亞書"), "瑪": ("Mal", "瑪拉基書"),
    "太": ("Matt", "馬太福音"), "可": ("Mark", "馬可福音"), "路": ("Luke", "路加福音"),
    "約": ("John", "約翰福音"), "徒": ("Acts", "使徒行傳"), "羅": ("Rom", "羅馬書"),
    "林前": ("1Cor", "哥林多前書"), "林後": ("2Cor", "哥林多後書"), "加": ("Gal", "加拉太書"),
    "弗": ("Eph", "以弗所書"), "腓": ("Phil", "腓立比書"), "西": ("Col", "歌羅西書"),
    "帖前": ("1Thess", "帖撒羅尼迦前書"), "帖後": ("2Thess", "帖撒羅尼迦後書"),
    "提前": ("1Tim", "提摩太前書"), "提後": ("2Tim", "提摩太後書"), "多": ("Titus", "提多書"),
    "門": ("Phlm", "腓利門書"), "來": ("Heb", "希伯來書"), "雅": ("Jas", "雅各書"),
    "彼前": ("1Pet", "彼得前書"), "彼後": ("2Pet", "彼得後書"), "約壹": ("1John", "約翰一書"),
    "約貳": ("2John", "約翰二書"), "約參": ("3John", "約翰三書"), "猶": ("Jude", "猶大書"),
    "啟": ("Rev", "啟示錄"),
}


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_child(root: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(EDITORIAL_DRAFT_ROOT.resolve())
    except ValueError:
        return None
    return candidate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug_from_passage(passage: str) -> str:
    match = re.fullmatch(
        r"([1-3]?[A-Za-z]+)\.(\d+)(?:\.(\d+))?(?:-([1-3]?[A-Za-z]+)\.(\d+)(?:\.(\d+))?)?",
        passage.strip(),
    )
    if not match:
        return ""
    book, chapter, verse, end_book, end_chapter, end_verse = match.groups()
    book_slug = BOOK_SLUGS.get(book, book.lower())
    parts = [book_slug, chapter]
    if verse:
        parts.append(verse)
    if end_book and end_book != book:
        parts.extend([BOOK_SLUGS.get(end_book, end_book.lower()), end_chapter or ""])
    elif end_chapter and end_chapter != chapter:
        parts.append(end_chapter)
    if end_verse:
        parts.append(end_verse)
    return "-".join(part for part in parts if part)


def _manifest_passage_slug(item: dict) -> str:
    public_slug = str(item.get("public_slug") or "").strip()
    if public_slug:
        return public_slug
    audit_config = item.get("audit_config") or {}
    for quotation in audit_config.get("required_scripture_quotations", []) or []:
        for marker in quotation.get("scripture_refs", []) or []:
            slug = _slug_from_passage(str(marker))
            if slug:
                return slug
    passage = str(item.get("passage") or "").strip()
    match = re.fullmatch(r"太\s*(\d+):(\d+)[–—-](\d+)", passage)
    if match:
        return f"matthew-{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return ""


def _public_scripture_reference(item: dict) -> dict | None:
    passage = str(item.get("passage") or "").strip()
    match = re.fullmatch(r"([^\d\s]+)\s*(\d+):(\d+)(?:[–—-](?:(\d+):)?(\d+))?", passage)
    if not match:
        return None
    book_display, chapter, verse_start, end_chapter, verse_end = match.groups()
    metadata = BOOK_METADATA.get(book_display)
    if not metadata:
        return None
    book, book_label = metadata
    return {
        "book": book,
        "book_label": book_label,
        "chapter": int(chapter),
        "verse_start": int(verse_start),
        "end_chapter": int(end_chapter or chapter),
        "verse_end": int(verse_end or verse_start),
        "display": passage,
    }


def _public_topics(item: dict) -> list[str]:
    configured = item.get("public_topics")
    if isinstance(configured, list):
        topics = [str(topic).strip() for topic in configured if str(topic).strip()]
        if topics:
            return list(dict.fromkeys(topics))
    title = str(item.get("title") or "")
    subtitle = title.split("：", maxsplit=1) if "：" in title else re.split(r":\s+", title, maxsplit=1)
    if len(subtitle) < 2:
        return []
    topics = [topic.strip() for topic in re.split(r"[、，,]|(?:與|和|及)", subtitle[1]) if topic.strip()]
    return list(dict.fromkeys(topics))


def _approved_publication(manifest_path: Path, item: dict) -> tuple[Path, dict] | None:
    draft_id = str(item.get("draft_id") or "").strip()
    manuscript_path = _safe_child(manifest_path.parent, str(item.get("relative_path") or "").strip())
    decision_relative = str((item.get("audit_config") or {}).get("publication_decision_path") or "").strip()
    decision_path = _safe_child(manifest_path.parent, decision_relative)
    if not draft_id or not manuscript_path or not manuscript_path.is_file() or not decision_path:
        return None
    decision = _read_json(decision_path)
    if (
        decision.get("schema_version") not in APPROVAL_SCHEMA_VERSIONS
        or decision.get("draft_id") != draft_id
        or decision.get("decision") != "approved"
        or decision.get("editorial_review_passed") is not True
        or decision.get("technical_audit_status") not in {"pass", "pass_with_warnings"}
        or decision.get("manuscript_sha256") != _sha256(manuscript_path)
    ):
        return None
    return manuscript_path, decision


def _public_source(source_document: dict) -> dict | None:
    if source_document.get("source_type") != "sermon_transcript":
        return None
    transcript_id = str(source_document.get("transcript_id") or "").strip()
    if not transcript_id:
        return None
    catalog = CanonicalRepositoryService._sermon_catalog_record(transcript_id)
    metadata: dict = {}
    source_path = Path(str(source_document.get("source_path") or ""))
    if source_path.is_file():
        raw = _read_json(source_path)
        metadata = raw.get("metadata") or {}
    media = CanonicalRepositoryService._sermon_media(transcript_id, metadata, catalog)
    media_payload = media.model_dump(mode="json")
    media_origin = os.getenv("PUBLIC_MEDIA_ORIGIN", "").rstrip("/")
    if media_origin and str(media_payload.get("url") or "").startswith("/"):
        media_payload["url"] = f"{media_origin}{media_payload['url']}"
    return {
        "title": str(catalog.get("title") or source_document.get("title") or transcript_id),
        "sermon_label": str(source_document.get("title") or transcript_id),
        "delivered_on": catalog.get("deliver_date") or None,
        "public_url": f"/resources/sermons/{quote(transcript_id, safe='')}",
        "media": media_payload,
    }


def _public_markdown(markdown: str) -> str:
    markdown = re.sub(r"<!--\s*provenance:\s*[\s\S]*?-->\s*", "", markdown)
    markdown = markdown.replace("**資料說明：**", "**閱讀提示：**")
    markdown = markdown.replace("現有材料沒有對第 17 節作獨立展開", "本文不在此對第 17 節作獨立展開")
    markdown = markdown.replace("現有材料沒有充分展開其語義", "本文不在此進一步展開其語義")
    return markdown.strip()


def public_article_data(slug: str) -> dict:
    for manifest_path in sorted(EDITORIAL_DRAFT_ROOT.glob("**/editorial-draft-manifest.json")):
        manifest = _read_json(manifest_path)
        for item in manifest.get("drafts", []) or []:
            if _manifest_passage_slug(item) != slug:
                continue
            approved = _approved_publication(manifest_path, item)
            if not approved:
                continue
            manuscript_path, _decision = approved
            package_path = _safe_child(
                manifest_path.parent,
                str(item.get("presentation_package_path") or "").strip(),
            )
            package = _read_json(package_path) if package_path and package_path.is_file() else {}
            plan_id = str((item.get("audit_config") or {}).get("plan_id") or item.get("candidate_id") or "")
            plan = next(
                (candidate for candidate in package.get("product_plans", []) or [] if candidate.get("plan_id") == plan_id),
                {},
            )
            decisions = {
                str(decision.get("decision_id") or ""): decision
                for decision in plan.get("decisions", []) or []
            }
            sources = {
                str(source.get("source_id") or ""): source
                for source in package.get("source_documents", []) or []
            }
            audio_sections = []
            for section in (item.get("audit_config") or {}).get("decision_sections", []) or []:
                decision = decisions.get(str(section.get("decision_id") or "")) or {}
                clips = []
                for presentation in decision.get("source_presentations", []) or []:
                    source = _public_source(sources.get(str(presentation.get("source_id") or "")) or {})
                    if not source or not source.get("media", {}).get("url"):
                        continue
                    clips.append(
                        {
                            "title": source["title"],
                            "sermon_label": source["sermon_label"],
                            "delivered_on": source["delivered_on"],
                            "public_url": source["public_url"],
                            "media": source["media"],
                            "start_seconds": presentation.get("start_seconds"),
                            "end_seconds": presentation.get("end_seconds"),
                        }
                    )
                if clips:
                    audio_sections.append(
                        {
                            "heading": str(section.get("markdown_heading") or "").strip(),
                            "title": str(decision.get("section_title") or section.get("markdown_heading") or "").strip(),
                            "passage": str(decision.get("passage") or "").strip(),
                            "clips": clips,
                        }
                    )
            markdown = _public_markdown(manuscript_path.read_text(encoding="utf-8"))
            source_annotations = []
            annotations_path = _safe_child(
                manifest_path.parent,
                str(item.get("source_annotations_path") or "").strip(),
            )
            if annotations_path and annotations_path.is_file():
                loaded = _read_json(annotations_path).get("source_annotations")
                if isinstance(loaded, list):
                    source_annotations = loaded
            return {
                "slug": slug,
                "title": str(item.get("title") or "").strip(),
                "passage": str(item.get("passage") or "").strip(),
                "markdown": markdown,
                "audio_sections": audio_sections,
                "source_annotations": source_annotations,
                "audio_section_count": len(audio_sections),
                "player_count": sum(len(section["clips"]) for section in audio_sections),
            }
    raise HTTPException(status_code=404, detail="找不到這篇文章。")


@router.get("")
def list_public_articles():
    articles = []
    seen: set[str] = set()
    for manifest_path in sorted(EDITORIAL_DRAFT_ROOT.glob("**/editorial-draft-manifest.json")):
        manifest = _read_json(manifest_path)
        for item in manifest.get("drafts", []) or []:
            slug = _manifest_passage_slug(item)
            if not slug or slug in seen or not _approved_publication(manifest_path, item):
                continue
            seen.add(slug)
            articles.append(
                {
                    "slug": slug,
                    "title": str(item.get("title") or "").strip(),
                    "passage": str(item.get("passage") or "").strip(),
                    "scripture": _public_scripture_reference(item),
                    "topics": _public_topics(item),
                    "href": f"/resources/wang-repository/articles/{slug}",
                }
            )
    return {"articles": articles}


@router.get("/{slug}")
def get_public_article(slug: str):
    return public_article_data(slug)
