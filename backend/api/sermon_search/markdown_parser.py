from __future__ import annotations

import hashlib
import re
from typing import List, Sequence

from .bible_refs import dedupe_refs, extract_refs
from .models import DiscoveredManuscript, SourceUnit
from .topics import (
    discover_topics_from_headings,
    discover_topics_from_text,
    extract_topics,
    infer_content_types,
    merge_topics,
)


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TERM_RE = re.compile(r"[\u0370-\u03ff]{2,}|[\u0590-\u05ff]{2,}|[A-Za-z][A-Za-z'-]{3,}")


def _stable_doc_id(project_id: str) -> str:
    return hashlib.sha1(project_id.encode("utf-8")).hexdigest()[:12]


def _heading_path(stack: Sequence[tuple[int, str]]) -> List[str]:
    return [title for _, title in stack if title]


def _extract_terms(text: str) -> List[str]:
    terms: List[str] = []
    seen: set[str] = set()
    for match in TERM_RE.finditer(text):
        term = match.group(0)
        key = term.lower()
        if key not in seen:
            terms.append(term)
            seen.add(key)
    return terms[:30]


def _split_markdown(markdown: str) -> List[tuple[List[str], str]]:
    stack: List[tuple[int, str]] = []
    current: List[str] = []
    chunks: List[tuple[List[str], str]] = []

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            chunks.append((_heading_path(stack), text))
        current.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue
        current.append(line)
    flush()
    return chunks


def _split_long_text(text: str, max_chars: int) -> List[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []
    units: List[str] = []
    buf: List[str] = []
    size = 0
    for para in paragraphs:
        if buf and size + len(para) > max_chars:
            units.append("\n\n".join(buf).strip())
            buf = []
            size = 0
        buf.append(para)
        size += len(para) + 2
    if buf:
        units.append("\n\n".join(buf).strip())
    return units


def parse_manuscript(manuscript: DiscoveredManuscript, markdown: str, max_chars: int = 2200) -> List[SourceUnit]:
    document_id = _stable_doc_id(manuscript.project_id)
    heading_chunks = _split_markdown(markdown)
    units: List[SourceUnit] = []
    ordinal = 0

    doc_context = " ".join(
        [
            manuscript.series_title,
            manuscript.lecture_title,
            manuscript.lecture_description or "",
            manuscript.project_title,
            manuscript.bible_verse or "",
        ]
    )
    document_refs = extract_refs(doc_context)

    for heading_path, text in heading_chunks:
        for part in _split_long_text(text, max_chars):
            heading_text = " ".join(heading_path)
            heading_refs = extract_refs(heading_text)
            body_refs = extract_refs(part)
            all_refs = dedupe_refs([*heading_refs, *body_refs])
            primary_refs = dedupe_refs(heading_refs)
            primary_osis = {ref.osis for ref in primary_refs}
            cross_refs = [ref for ref in all_refs if ref.osis not in primary_osis]

            source_id = f"{document_id}-{ordinal:04d}"
            topic_tags = merge_topics(
                extract_topics([doc_context, heading_text, part]),
                discover_topics_from_headings(heading_path),
                discover_topics_from_text(part),
            )
            content_types = infer_content_types([heading_text, part])
            units.append(
                SourceUnit(
                    source_id=source_id,
                    document_id=document_id,
                    series_id=manuscript.series_id,
                    series_title=manuscript.series_title,
                    lecture_id=manuscript.lecture_id,
                    lecture_title=manuscript.lecture_title,
                    project_id=manuscript.project_id,
                    project_title=manuscript.project_title,
                    heading_path=heading_path,
                    text=part,
                    primary_passage_refs=primary_refs,
                    cross_refs=cross_refs,
                    all_canonical_refs=all_refs,
                    document_scope_refs=document_refs,
                    topic_tags=topic_tags,
                    content_types=content_types,
                    terms=_extract_terms(part),
                    ordinal=ordinal,
                )
            )
            ordinal += 1

    return units
