"""Build a unified evidence-led source map for Matthew 1–28.

The read model combines two distinct source lineages without conflating them:

* sermon transcripts found through the corpus survey; and
* reviewed notes-to-manuscript projects registered in the Matthew series.

A sermon is attached to a chapter only when the corpus survey found an actual
Matthew reference.  A notes project is attached through its explicit
``bible_verse`` metadata or a clear dominant chapter in its frozen manuscript.
Projects whose ``project_type`` is ``transcript`` are derived views of sermon
transcripts, so they are excluded from this source lineage; the linked sermon
continues to enter through the original sermon-transcript lineage. Titles,
series names, folders and organizations never create chapter coverage.
Projects without a defensible chapter scope remain visible as book-level
sources instead of being guessed into a chapter.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.api.config import DATA_BASE_PATH, WANG_PLATFORM_PATHS
from backend.api.sermon_search.bible_refs import normalize_ref


SCHEMA_VERSION = "matthew_source_coverage_v3"
DEFAULT_SURVEY_DIR = WANG_PLATFORM_PATHS.corpus_survey_staging
DEFAULT_CATALOG_PATH = DATA_BASE_PATH / "sermon_catalog.json"
DEFAULT_NOTES_ROOT = DATA_BASE_PATH / "notes_to_surmon"
DEFAULT_NOTES_SERIES_ID = "d5c55bdf-6375-49e9-a08d-22eda1eaf21d"
DEFAULT_OUTPUT_PATH = WANG_PLATFORM_PATHS.matthew_source_coverage
DEFAULT_REPORT_PATH = WANG_PLATFORM_PATHS.matthew_source_coverage_report


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_json(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _explicit_matthew_refs(raw: Any) -> list[dict[str, Any]]:
    """Parse explicit Matthew metadata, including shorthand such as ``太 6,7``."""

    text = str(raw or "").strip()
    if not text:
        return []
    refs: dict[str, dict[str, Any]] = {}
    normalized = normalize_ref(text)
    if normalized is not None and normalized.book == "Matt":
        refs[normalized.osis] = normalized.model_dump(mode="json")

    # The general scripture parser intentionally does not infer an omitted
    # book name.  In the curated bible_verse field, however, ``太 6,7`` and
    # ``太 14,15`` are established chapter-list notation.
    if re.search(r"(?:太|馬太(?:福音)?|马太(?:福音)?|Matthew|Matt|Mt)\s*\d", text, re.I):
        head = re.search(
            r"(?:太|馬太(?:福音)?|马太(?:福音)?|Matthew|Matt|Mt)\s*(\d{1,2})(?!\s*[:：])",
            text,
            re.I,
        )
        if head:
            suffix = text[head.end() :]
            chapters = [int(head.group(1))]
            chapters.extend(int(value) for value in re.findall(r"[,，、]\s*(\d{1,2})", suffix))
            for chapter in chapters:
                ref = normalize_ref(f"Matt.{chapter}")
                if ref is not None:
                    refs[ref.osis] = ref.model_dump(mode="json")
    return sorted(refs.values(), key=lambda item: (item["chapter_start"], item["osis"]))


def _dominant_matthew_chapter_from_manuscript(
    path: Path, *, minimum_mentions: int = 3, minimum_share: float = 0.6
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Infer one chapter only when a reviewed manuscript has a clear reference majority.

    This is a content-derived fallback for incomplete metadata, not title
    inference.  Mixed structural essays remain unscoped.
    """

    if not path.is_file():
        return [], None
    text = path.read_text(encoding="utf-8")
    mentions = Counter(
        int(value)
        for value in re.findall(
            r"(?:太|馬太(?:福音)?|马太(?:福音)?|Matthew|Matt|Mt)\s*(\d{1,2})\s*[:：]",
            text,
            re.I,
        )
    )
    total = sum(mentions.values())
    if not mentions or total < minimum_mentions:
        return [], None
    chapter, count = mentions.most_common(1)[0]
    share = count / total
    if count < minimum_mentions or share < minimum_share:
        return [], None
    ref = normalize_ref(f"Matt.{chapter}")
    if ref is None:
        return [], None
    return [ref.model_dump(mode="json")], {
        "chapter": chapter,
        "mention_count": count,
        "total_matthew_reference_mentions": total,
        "share": round(share, 4),
        "minimum_mentions": minimum_mentions,
        "minimum_share": minimum_share,
    }


def _matthew_chapters(raw_refs: Iterable[Any], start: int, end: int) -> list[tuple[int, dict[str, Any]]]:
    matches: list[tuple[int, dict[str, Any]]] = []
    for raw in raw_refs:
        ref = normalize_ref(str(raw or ""))
        if ref is None or ref.book != "Matt":
            continue
        chapter_end = ref.chapter_end or ref.chapter_start
        for chapter in range(max(start, ref.chapter_start), min(end, chapter_end) + 1):
            matches.append((chapter, ref.model_dump(mode="json")))
    return matches


def _anchor_payload(anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        key: anchor.get(key)
        for key in (
            "segment_index",
            "source_segment_index",
            "source_segment_ordinal",
            "start_time",
            "end_time",
            "verbatim_excerpt",
        )
        if anchor.get(key) is not None
    }


def _source_shell(record: dict[str, Any], transcript_id: str) -> dict[str, Any]:
    return {
        "source_id": f"sermon:{transcript_id}",
        "source_type": "sermon_transcript",
        "source_type_label": "原始講道／逐字稿",
        "transcript_id": transcript_id,
        "title": record.get("title") or transcript_id,
        "deliver_date": record.get("deliver_date"),
        "series_id": record.get("series_id"),
        "series_title": record.get("series_title"),
        "series_order": record.get("series_order"),
        "source_category": record.get("source_category") or "unknown",
        "source_category_label": record.get("source_category_label") or "來源待確認",
        "source_organization": record.get("source_organization"),
        "source_provider": record.get("source_provider"),
        "source_url": record.get("source_url"),
        "source_raw": record.get("source_raw"),
        "references": {},
        "content_clusters": [],
        "candidate_claims": [],
    }


def _notes_project_source(
    *,
    notes_root: Path,
    series: dict[str, Any],
    lecture: dict[str, Any],
    project_id: str,
    project_order: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    project_root = notes_root / project_id
    meta_path = project_root / "meta.json"
    meta = _load_json(meta_path) if meta_path.is_file() else {}
    final_path = project_root / "final.md"
    original_notes_path = project_root / "original_notes.md"
    refs = _explicit_matthew_refs(meta.get("bible_verse"))
    manuscript_scope = None
    if not refs:
        refs, manuscript_scope = _dominant_matthew_chapter_from_manuscript(final_path)
    pages = [str(item) for item in meta.get("pages") or []]
    source = {
        "source_id": f"notes_manuscript:{project_id}",
        "source_type": "notes_to_manuscript",
        "source_type_label": "筆記轉講稿",
        "project_id": project_id,
        "title": meta.get("title") or project_id,
        "project_type": meta.get("project_type") or series.get("project_type") or "sermon_note",
        "series_id": series.get("id"),
        "series_title": series.get("title"),
        "lecture_id": lecture.get("id"),
        "lecture_title": lecture.get("title"),
        "lecture_order": lecture.get("_order"),
        "project_order": project_order,
        "bible_verse_raw": meta.get("bible_verse"),
        "references": refs,
        "source_category": "notes_to_manuscript",
        "source_category_label": "王教授釋經課筆記",
        "source_organization": None,
        "source_provider": "王教授釋經課筆記",
        "source_url": f"/resources/notes_to_manuscript_series/{series.get('id')}/{project_id}",
        "source_pages": pages,
        "source_page_count": len(pages),
        "linked_sermon_transcript_id": meta.get("sermon_transcript_id"),
        "artifacts": {
            "meta_path": str(meta_path.resolve()),
            "meta_sha256": _sha256_file(meta_path),
            "original_notes_path": str(original_notes_path.resolve()) if original_notes_path.is_file() else None,
            "original_notes_sha256": _sha256_file(original_notes_path),
            "manuscript_path": str(final_path.resolve()) if final_path.is_file() else None,
            "manuscript_sha256": _sha256_file(final_path),
        },
        "evidence_summary": {
            "evidence_level": "reviewed_notes_manuscript_lineage",
            "material_role": "notes_manuscript_primary_or_supplementary_source",
            "editorial_status": "available_for_argument_extraction",
            "chapter_assignment_basis": (
                "explicit_project_bible_verse"
                if refs and manuscript_scope is None
                else "reviewed_manuscript_dominant_matthew_chapter"
                if manuscript_scope
                else None
            ),
            "manuscript_scope_evidence": manuscript_scope,
        },
    }
    return source, refs


def _load_notes_sources(
    notes_root: Path, series_id: str
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], dict[str, Any]]:
    series_db_path = notes_root / "series_db.json"
    if not series_db_path.is_file():
        return [], {"series_db_path": str(series_db_path.resolve()), "series_db_sha256": None}
    series_db = _load_json(series_db_path)
    series = next((item for item in series_db if item.get("id") == series_id), None)
    if series is None:
        raise ValueError(f"Matthew notes-to-manuscript series not found: {series_id}")
    records: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    excluded_transcript_project_ids: list[str] = []
    for lecture_order, raw_lecture in enumerate(series.get("lectures") or [], start=1):
        lecture = {**raw_lecture, "_order": lecture_order}
        for project_order, project_id in enumerate(lecture.get("project_ids") or [], start=1):
            record = _notes_project_source(
                notes_root=notes_root,
                series=series,
                lecture=lecture,
                project_id=str(project_id),
                project_order=project_order,
            )
            source, _refs = record
            if str(source.get("project_type") or "").strip().lower() == "transcript":
                excluded_transcript_project_ids.append(str(project_id))
                continue
            records.append(record)
    return records, {
        "series_db_path": str(series_db_path.resolve()),
        "series_db_sha256": _sha256_file(series_db_path),
        "series_id": series_id,
        "series_title": series.get("title"),
        "included_project_rule": "all_except_transcript",
        "excluded_project_type": "transcript",
        "excluded_transcript_project_ids": excluded_transcript_project_ids,
    }


def _merge_evidence(
    target: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    identity_key: str,
    matched_reference: dict[str, Any],
) -> None:
    """Keep one evidence record per cluster/claim and retain every matching ref."""

    identity = payload.get(identity_key)
    existing = next((item for item in target if item.get(identity_key) == identity), None)
    if existing is None:
        payload["matched_references"] = [matched_reference]
        target.append(payload)
        return
    seen = {item.get("osis") for item in existing.get("matched_references") or []}
    if matched_reference.get("osis") not in seen:
        existing.setdefault("matched_references", []).append(matched_reference)


def build_matthew_source_coverage(
    survey_dir: Path = DEFAULT_SURVEY_DIR,
    *,
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    notes_root: Path = DEFAULT_NOTES_ROOT,
    notes_series_id: str = DEFAULT_NOTES_SERIES_ID,
    chapter_start: int = 1,
    chapter_end: int = 28,
) -> dict[str, Any]:
    """Return unified chapter sources grounded in survey and project metadata."""

    catalog = _load_json(catalog_path)
    catalog_by_id = {
        str(record.get("transcript_id")): record
        for record in catalog.get("records") or []
        if record.get("transcript_id")
    }
    chapter_sources: dict[int, dict[str, dict[str, Any]]] = {
        chapter: {} for chapter in range(chapter_start, chapter_end + 1)
    }
    survey_fingerprints: dict[str, str] = {}
    notes_sources, notes_source_meta = _load_notes_sources(notes_root, notes_series_id)

    for path in sorted(survey_dir.glob("*.first-pass.json")):
        survey = _load_json(path)
        transcript_id = str((survey.get("source") or {}).get("transcript_id") or "").strip()
        if not transcript_id:
            continue
        survey_fingerprints[transcript_id] = _sha256_json(survey)
        record = catalog_by_id.get(transcript_id, {})

        for cluster in survey.get("content_clusters") or []:
            for chapter, ref in _matthew_chapters(
                cluster.get("scripture_refs") or [], chapter_start, chapter_end
            ):
                source = chapter_sources[chapter].setdefault(
                    transcript_id, _source_shell(record, transcript_id)
                )
                source["references"][ref["osis"]] = ref
                _merge_evidence(
                    source["content_clusters"],
                    {
                        "cluster_id": cluster.get("cluster_id"),
                        "title": cluster.get("title"),
                        "summary": cluster.get("summary"),
                        "function": cluster.get("function"),
                        "segment_indexes": cluster.get("segment_indexes") or [],
                    },
                    identity_key="cluster_id",
                    matched_reference=ref,
                )

        for claim in survey.get("candidate_claims") or []:
            for chapter, ref in _matthew_chapters(
                claim.get("scripture_refs") or [], chapter_start, chapter_end
            ):
                source = chapter_sources[chapter].setdefault(
                    transcript_id, _source_shell(record, transcript_id)
                )
                source["references"][ref["osis"]] = ref
                _merge_evidence(
                    source["candidate_claims"],
                    {
                        "claim_id": claim.get("claim_id"),
                        "statement": claim.get("statement"),
                        "claim_kind": claim.get("claim_kind"),
                        "attribution": claim.get("attribution"),
                        "confidence": claim.get("confidence"),
                        "review_status": claim.get("review_status"),
                        "anchors": [
                            _anchor_payload(anchor) for anchor in claim.get("anchors") or []
                        ],
                    },
                    identity_key="claim_id",
                    matched_reference=ref,
                )

    book_level_sources: list[dict[str, Any]] = []
    for source, refs in notes_sources:
        assigned_chapters: set[int] = set()
        for ref in refs:
            assigned_chapters.update(
                range(
                    max(chapter_start, ref["chapter_start"]),
                    min(chapter_end, ref.get("chapter_end") or ref["chapter_start"]) + 1,
                )
            )
        assignment_basis = (
            source["evidence_summary"].get("chapter_assignment_basis")
            if assigned_chapters
            else None
        )

        if assigned_chapters:
            source["evidence_summary"]["chapter_assignment_basis"] = assignment_basis
            source["assigned_chapters"] = sorted(assigned_chapters)
            for chapter in assigned_chapters:
                chapter_sources[chapter][source["source_id"]] = source
        else:
            source["evidence_summary"]["material_role"] = "book_level_or_unscoped_notes_source"
            source["evidence_summary"]["editorial_status"] = "needs_chapter_scope_review"
            book_level_sources.append(source)

    source_categories: Counter[str] = Counter()
    chapters: list[dict[str, Any]] = []
    distinct_sources: set[str] = set()
    source_directory: dict[str, dict[str, Any]] = {}
    for chapter in range(chapter_start, chapter_end + 1):
        sources: list[dict[str, Any]] = []
        for raw_source in chapter_sources[chapter].values():
            source = deepcopy(raw_source)
            if source.get("source_type") == "sermon_transcript":
                source["references"] = sorted(
                    source["references"].values(),
                    key=lambda ref: (
                        ref["chapter_start"],
                        ref.get("verse_start") or 0,
                        ref["osis"],
                    ),
                )
                anchored_claim_count = sum(
                    1 for claim in source["candidate_claims"] if claim.get("anchors")
                )
                referenced_segment_count = len(
                    {
                        str(segment)
                        for cluster in source["content_clusters"]
                        for segment in cluster.get("segment_indexes") or []
                    }
                )
                if anchored_claim_count >= 2:
                    material_role = "multi_claim_candidate"
                elif anchored_claim_count == 1:
                    material_role = "single_claim_candidate"
                else:
                    material_role = "cluster_reference_only"
                source["evidence_summary"] = {
                    "content_cluster_count": len(source["content_clusters"]),
                    "candidate_claim_count": len(source["candidate_claims"]),
                    "anchored_claim_count": anchored_claim_count,
                    "referenced_segment_count": referenced_segment_count,
                    "evidence_level": (
                        "anchored_candidate_claims"
                        if anchored_claim_count
                        else "cluster_reference_only"
                    ),
                    "material_role": material_role,
                    "editorial_status": "needs_detailed_extraction",
                }
            source_categories[source["source_category"]] += 1
            distinct_sources.add(source["source_id"])
            directory_entry = source_directory.setdefault(
                source["source_id"],
                {
                    key: source.get(key)
                    for key in (
                        "source_id",
                        "source_type",
                        "source_type_label",
                        "transcript_id",
                        "project_id",
                        "title",
                        "series_id",
                        "series_title",
                        "lecture_id",
                        "lecture_title",
                        "source_category",
                        "source_category_label",
                        "source_organization",
                        "source_provider",
                        "source_url",
                        "linked_sermon_transcript_id",
                    )
                    if source.get(key) is not None
                },
            )
            directory_entry.setdefault("assigned_chapters", []).append(chapter)
            sources.append(source)
        sources.sort(
            key=lambda item: (
                0 if item.get("source_type") == "notes_to_manuscript" else 1,
                item.get("lecture_order") or 999,
                item.get("project_order") or 999,
                item.get("deliver_date") or "9999",
                item["title"],
            )
        )
        chapters.append(
            {
                "chapter": chapter,
                "source_count": len(sources),
                "sources": sources,
            }
        )

    for source in book_level_sources:
        source_directory[source["source_id"]] = {
            key: source.get(key)
            for key in (
                "source_id",
                "source_type",
                "source_type_label",
                "transcript_id",
                "project_id",
                "title",
                "series_id",
                "series_title",
                "lecture_id",
                "lecture_title",
                "source_category",
                "source_category_label",
                "source_organization",
                "source_provider",
                "source_url",
                "linked_sermon_transcript_id",
            )
            if source.get(key) is not None
        } | {"assigned_chapters": [], "scope_status": "book_level_or_unscoped"}

    source_directory_list = sorted(
        source_directory.values(),
        key=lambda item: (
            0 if item.get("source_type") == "notes_to_manuscript" else 1,
            item.get("assigned_chapters") or [999],
            item.get("title") or "",
        ),
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "book": "Matt",
            "book_label": "馬太福音",
            "chapter_start": chapter_start,
            "chapter_end": chapter_end,
        },
        "method": {
            "coverage_basis": {
                "sermon_transcript": "actual_scripture_references_in_corpus_survey",
                "notes_to_manuscript": [
                    "explicit_project_bible_verse_metadata",
                    "reviewed_manuscript_dominant_matthew_chapter_when_metadata_is_incomplete",
                ],
            },
            "excluded_project_types": ["transcript"],
            "excluded_as_coverage_basis": ["sermon_title", "series_title", "source_organization"],
            "warning": "本表是來源與候選材料地圖，不表示任何單一來源完整覆蓋該章。",
        },
        "source": {
            "survey_dir": str(survey_dir.resolve()),
            "catalog_path": str(catalog_path.resolve()),
            "survey_fingerprints_sha256": _sha256_json(survey_fingerprints),
            "catalog_sha256": _sha256_json(catalog),
            "notes_to_manuscript": notes_source_meta,
        },
        "summary": {
            "chapter_count": len(chapters),
            "distinct_candidate_source_count": len(distinct_sources),
            "total_listed_source_count": len(distinct_sources) + len(book_level_sources),
            "chapter_source_assignment_count": sum(item["source_count"] for item in chapters),
            "source_category_assignment_counts": dict(sorted(source_categories.items())),
            "notes_to_manuscript_project_count": len(notes_sources),
            "book_level_or_unscoped_notes_source_count": len(book_level_sources),
        },
        "source_directory": source_directory_list,
        "book_level_sources": book_level_sources,
        "chapters": chapters,
    }


def write_matthew_source_coverage(
    payload: dict[str, Any], output_path: Path = DEFAULT_OUTPUT_PATH
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output_path)
    return output_path


def _markdown_link(label: str, url: Any) -> str:
    safe_label = str(label or "未命名來源").replace("[", "［").replace("]", "］")
    return f"[{safe_label}]({url})" if url else safe_label


def render_matthew_source_coverage_markdown(payload: dict[str, Any]) -> str:
    """Render the complete chapter source register for editorial reading."""

    summary = payload.get("summary") or {}
    lines = [
        "# 馬太福音第一至二十八章完整來源清單",
        "",
        "> 本表同時列出筆記轉講稿與原始講道／逐字稿。章節歸屬表示該來源可供該章整理使用，不表示單一來源完整覆蓋全章。",
        "",
        f"- 去重後來源：{summary.get('total_listed_source_count', 0)}",
        f"- 筆記轉講稿 Projects：{summary.get('notes_to_manuscript_project_count', 0)}",
        f"- 尚未定章的全書／結構材料：{summary.get('book_level_or_unscoped_notes_source_count', 0)}",
        "",
    ]
    for chapter in payload.get("chapters") or []:
        lines.extend([f"## 第 {chapter.get('chapter')} 章", ""])
        sources = chapter.get("sources") or []
        if not sources:
            lines.extend(["_目前沒有已登記來源。_", ""])
            continue
        for source in sources:
            label = source.get("title") or source.get("source_id")
            link = _markdown_link(label, source.get("source_url"))
            source_type = source.get("source_type_label") or source.get("source_type")
            details = [str(source_type)]
            if source.get("series_title"):
                details.append(str(source["series_title"]))
            if source.get("deliver_date"):
                details.append(str(source["deliver_date"]))
            if source.get("source_category_label") and source.get("source_type") != "notes_to_manuscript":
                details.append(str(source["source_category_label"]))
            lines.append(f"- {link} — {'；'.join(details)}")
        lines.append("")

    lines.extend(["## 全書／結構材料（尚未定章）", ""])
    book_sources = payload.get("book_level_sources") or []
    if not book_sources:
        lines.extend(["_無。_", ""])
    else:
        for source in book_sources:
            link = _markdown_link(source.get("title"), source.get("source_url"))
            raw_scope = source.get("bible_verse_raw")
            scope_note = f"；原始經文欄：{raw_scope}" if raw_scope else ""
            lines.append(f"- {link} — {source.get('source_type_label')}{scope_note}")
        lines.append("")
    return "\n".join(lines)


def write_matthew_source_coverage_report(
    payload: dict[str, Any], output_path: Path = DEFAULT_REPORT_PATH
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(render_matthew_source_coverage_markdown(payload), encoding="utf-8")
    temporary.replace(output_path)
    return output_path
