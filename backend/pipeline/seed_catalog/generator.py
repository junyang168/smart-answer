from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from backend.api.sermon_search.bible_refs import BOOKS, extract_refs, normalize_ref
from backend.api.sermon_search.slugify import slugify_heading


SCHEMA_VERSION = "1.0-review"
DEFAULT_TAXONOMY_PATH = Path(__file__).with_name("taxonomy_seed.json")
DEFAULT_REVIEW_DECISIONS_PATH = Path(__file__).with_name("review_decisions.json")
CONTENT_CATEGORIES = ("釋經", "神學意義", "生活應用", "附錄")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_unit_id(topic: dict[str, Any]) -> str:
    sources = topic.get("sources") or []
    source_key = "|".join(
        sorted(
            f"{source.get('series_id','')}:{source.get('project_id','')}:{' > '.join(source.get('source_sections') or [])}"
            for source in sources
        )
    )
    seed = f"{topic.get('type','concept')}|{topic.get('name','').strip()}|{source_key}"
    return f"CU-SEED-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def _find_project_root(data_root: Path, project_id: str) -> Path | None:
    for folder in ("notes_to_surmon", "transcripts_to_manuscript"):
        candidate = data_root / folder / project_id
        if candidate.is_dir():
            return candidate
    return None


def _load_published_projects(data_root: Path, series_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    series_db_path = data_root / "notes_to_surmon" / "series_db.json"
    series_db = _load_json(series_db_path)
    series = next((item for item in series_db if item.get("id") == series_id), None)
    if not series:
        raise ValueError(f"Series not found: {series_id}")

    projects: dict[str, dict[str, Any]] = {}
    for lecture_order, lecture in enumerate(series.get("lectures") or [], start=1):
        for project_order, project_id in enumerate(lecture.get("project_ids") or [], start=1):
            root = _find_project_root(data_root, project_id)
            if root is None:
                continue
            final_path = root / "final.md"
            meta_path = root / "meta.json"
            if not final_path.is_file() or not final_path.read_text(encoding="utf-8").strip():
                continue
            meta = _load_json(meta_path) if meta_path.is_file() else {}
            projects[project_id] = {
                "project_id": project_id,
                "project_title": meta.get("title") or project_id,
                "project_type": meta.get("project_type") or series.get("project_type") or "sermon_note",
                "bible_verse": meta.get("bible_verse"),
                "lecture_id": lecture.get("id"),
                "lecture_title": lecture.get("title") or "",
                "lecture_order": lecture_order,
                "project_order": project_order,
                "final_path": str(final_path),
                "final_sha256": _sha256(final_path),
                "meta_path": str(meta_path),
                "meta_sha256": _sha256(meta_path) if meta_path.is_file() else None,
                "public_url": (
                    f"/resources/notes_to_manuscript_series/{series_id}/{project_id}"
                ),
            }
    return projects, series


def _category_suggestions(
    topic: dict[str, Any], sources: list[dict[str, Any]]
) -> list[dict[str, str]]:
    sections = [
        section
        for source in topic.get("sources") or []
        for section in (source.get("source_sections") or [])
    ]
    blob = " ".join([topic.get("name") or "", *sections])
    found: list[dict[str, str]] = []
    for category in CONTENT_CATEGORIES:
        if category in blob:
            found.append({"category": category, "basis": "source_heading", "confidence": "high"})
    published_categories = {
        category
        for source in sources
        for category in source.get("content_categories") or []
    }
    for category in CONTENT_CATEGORIES:
        if category in published_categories and not any(item["category"] == category for item in found):
            found.append({"category": category, "basis": "published_subheading", "confidence": "high"})
    if found:
        return found
    if topic.get("type") == "passage":
        return [{"category": "釋經", "basis": "passage_topic_default", "confidence": "medium"}]
    return [{"category": "神學意義", "basis": "concept_topic_default", "confidence": "low"}]


def _classification_blob(topic: dict[str, Any]) -> tuple[str, str, str]:
    title = topic.get("name") or ""
    aliases = " ".join(topic.get("taxonomy_aliases") or [])
    arguments = " ".join(
        item
        for source in topic.get("sources") or []
        for item in (source.get("lun_dian") or [])
    )
    return title.lower(), aliases.lower(), arguments.lower()


def _classify_topic(topic: dict[str, Any], taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    title, aliases, arguments = _classification_blob(topic)
    candidates: list[dict[str, Any]] = []
    for parent in taxonomy.get("topics") or []:
        for child in parent.get("children") or []:
            matched: list[str] = []
            score = 0
            for raw_signal in child.get("signals") or []:
                signal = raw_signal.lower()
                if signal in title:
                    score += 4
                    matched.append(raw_signal)
                elif signal in aliases:
                    score += 2
                    matched.append(raw_signal)
                elif signal in arguments:
                    score += 1
                    matched.append(raw_signal)
            if score:
                candidates.append(
                    {
                        "path": [parent["label"], child["label"]],
                        "topic_ids": [parent["id"], child["id"]],
                        "score": score,
                        "confidence": "high" if score >= 8 else "medium" if score >= 4 else "low",
                        "matched_signals": matched[:8],
                    }
                )
    candidates.sort(key=lambda item: (-item["score"], item["path"]))
    if not candidates:
        return []
    best_score = candidates[0]["score"]
    # Preserve genuinely multi-topic units, but do not flood the review with
    # every one-point keyword match.
    selected = [item for item in candidates if item["score"] >= max(2, best_score // 2)][:4]
    for index, item in enumerate(selected):
        item["role"] = "primary" if index == 0 else "secondary"
    return selected


def _primary_refs(topic: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    seen: set[str] = set()
    canonical = topic.get("canonical_ref")
    if canonical:
        ref = normalize_ref(canonical)
        if ref:
            refs.append(ref)
            seen.add(ref.osis)
    for ref in extract_refs(topic.get("name") or ""):
        if ref.osis not in seen:
            refs.append(ref)
            seen.add(ref.osis)
    return [
        {
            "osis": ref.osis,
            "display": topic.get("canonical_ref_raw") if ref.osis == canonical else ref.raw,
            "book": ref.book,
            "book_zh": ref.book_zh,
            "chapter_start": ref.chapter_start,
            "verse_start": ref.verse_start,
            "chapter_end": ref.chapter_end,
            "verse_end": ref.verse_end,
        }
        for ref in refs
    ]


def _source_records(
    topic: dict[str, Any], projects: dict[str, dict[str, Any]], series_id: str
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source in topic.get("sources") or []:
        if source.get("series_id") != series_id:
            continue
        project = projects.get(source.get("project_id"))
        if not project:
            continue
        sections = source.get("source_sections") or []
        manuscript = Path(project["final_path"]).read_text(encoding="utf-8")
        heading_matches = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", manuscript, re.MULTILINE))
        headings = [match.group(2).strip() for match in heading_matches]
        resolved_headings: list[str | None] = []
        unresolved_sections: list[str] = []
        for section in sections:
            parent = section.split("＞", 1)[0].strip()
            if parent in headings:
                resolved_headings.append(parent)
                continue
            # Early topic-index versions sometimes stored only "一" or
            # "一（附錄）" instead of the full heading. Resolve the unique
            # numeral-prefixed heading, but retain the original source label.
            numeral_match = re.match(r"^([一二三四五六七八九十]+)(?:[（(].*?[）)])?$", parent)
            candidates = []
            if numeral_match:
                prefix = numeral_match.group(1)
                candidates = [heading for heading in headings if re.match(rf"^{re.escape(prefix)}[、，,:：\s]", heading)]
            if len(candidates) == 1:
                resolved_headings.append(candidates[0])
            else:
                resolved_headings.append(None)
                unresolved_sections.append(section)
        anchors = [slugify_heading(heading) if heading else "" for heading in resolved_headings]
        links = [f"{project['public_url']}#{anchor}" for anchor in anchors]
        source_categories: list[str] = []
        for resolved_heading in resolved_headings:
            if not resolved_heading:
                continue
            parent_index = next(
                (index for index, match in enumerate(heading_matches) if match.group(2).strip() == resolved_heading),
                None,
            )
            if parent_index is None:
                continue
            parent_level = len(heading_matches[parent_index].group(1))
            for child_match in heading_matches[parent_index + 1 :]:
                child_level = len(child_match.group(1))
                if child_level <= parent_level:
                    break
                child_title = child_match.group(2).strip()
                if child_title in CONTENT_CATEGORIES and child_title not in source_categories:
                    source_categories.append(child_title)
        sources.append(
            {
                "project_id": project["project_id"],
                "project_title": project["project_title"],
                "project_type": project["project_type"],
                "lecture_title": project["lecture_title"],
                "source_sections": sections,
                "resolved_source_headings": resolved_headings,
                "unresolved_source_sections": unresolved_sections,
                "section_anchors": anchors,
                "public_links": [link for link, anchor in zip(links, anchors) if anchor] or [project["public_url"]],
                "content_categories": source_categories,
                "source_file": project["final_path"],
                "source_sha256": project["final_sha256"],
                "arguments": source.get("lun_dian") or [],
            }
        )
    return sources


def _apply_review_decision(unit: dict[str, Any], decisions: dict[str, Any]) -> dict[str, Any]:
    decision = next(
        (
            item
            for item in decisions.get("decisions") or []
            if item.get("source_topic_id") == unit.get("source_topic_id")
            and (not item.get("title") or item.get("title") == unit.get("title"))
        ),
        None,
    )
    if not decision:
        return unit
    unit["status"] = decision.get("status") or unit["status"]
    if decision.get("content_categories"):
        unit["content_category_suggestions"] = [
            {"category": category, "basis": "human_review", "confidence": "high"}
            for category in decision["content_categories"]
        ]
    assignment = decision.get("topic_assignment")
    if assignment:
        matching = next(
            (item for item in unit["topic_assignments"] if item["topic_ids"] == assignment.get("topic_ids")),
            None,
        )
        confirmed = {
            "path": assignment["path"],
            "topic_ids": assignment["topic_ids"],
            "score": matching["score"] if matching else None,
            "confidence": "high",
            "matched_signals": matching["matched_signals"] if matching else [],
            "role": assignment.get("role") or "primary",
            "basis": "human_review",
        }
        remainder = [item for item in unit["topic_assignments"] if item["topic_ids"] != assignment.get("topic_ids")]
        unit["topic_assignments"] = [confirmed, *remainder]
    unit["review_decision"] = {
        "version": decisions.get("version"),
        "rationale": decision.get("rationale"),
    }
    return unit


def _book_order() -> dict[str, int]:
    return {book: index for index, (book, _zh, _aliases) in enumerate(BOOKS)}


def _build_bible_index(units: list[dict[str, Any]]) -> dict[str, Any]:
    order = _book_order()
    grouped: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    book_names: dict[str, str] = {}
    for unit in units:
        for ref in unit["primary_bible_refs"]:
            book_names[ref["book"]] = ref["book_zh"]
            grouped[ref["book"]][ref["chapter_start"]].append(
                {
                    "unit_id": unit["unit_id"],
                    "title": unit["title"],
                    "osis": ref["osis"],
                    "display": ref["display"],
                    "source_project_ids": [source["project_id"] for source in unit["sources"]],
                }
            )
    books = []
    for book in sorted(grouped, key=lambda item: order.get(item, 999)):
        chapters = []
        for chapter in sorted(grouped[book]):
            items = grouped[book][chapter]
            items.sort(key=lambda item: (item["osis"], item["title"]))
            chapters.append({"chapter": chapter, "units": items})
        books.append({"book": book, "book_zh": book_names[book], "chapters": chapters})
    return {"schema_version": SCHEMA_VERSION, "books": books}


def _build_topic_tree(units: list[dict[str, Any]], taxonomy: dict[str, Any]) -> dict[str, Any]:
    by_child: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        for assignment in unit["topic_assignments"]:
            by_child[assignment["topic_ids"][-1]].append(
                {
                    "unit_id": unit["unit_id"],
                    "title": unit["title"],
                    "role": assignment["role"],
                    "confidence": assignment["confidence"],
                    "matched_signals": assignment["matched_signals"],
                    "source_project_ids": [source["project_id"] for source in unit["sources"]],
                }
            )
    topics = []
    for parent in taxonomy.get("topics") or []:
        children = []
        for child in parent.get("children") or []:
            entries = sorted(by_child.get(child["id"], []), key=lambda item: item["title"])
            children.append({"id": child["id"], "label": child["label"], "units": entries})
        topics.append({"id": parent["id"], "label": parent["label"], "children": children})
    return {
        "schema_version": SCHEMA_VERSION,
        "taxonomy_version": taxonomy.get("version"),
        "status": "candidate_requires_review",
        "topics": topics,
    }


def _normalized_title(value: str) -> str:
    text = re.sub(r"[「」『』《》〈〉（）()：:，,。；;、\s—–_-]", "", value.lower())
    return re.sub(r"^(太|馬太福音)\d+(?:\d+)?", "", text)


def _ref_interval(ref: dict[str, Any]) -> tuple[int, int]:
    start = ref["chapter_start"] * 1000 + (ref.get("verse_start") or 0)
    end_chapter = ref.get("chapter_end") or ref["chapter_start"]
    end_verse = ref.get("verse_end")
    if end_verse is None:
        end_verse = ref.get("verse_start") if ref.get("verse_start") is not None else 999
    return start, end_chapter * 1000 + end_verse


def _refs_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["book"] != right["book"]:
        return False
    left_start, left_end = _ref_interval(left)
    right_start, right_end = _ref_interval(right)
    return left_start <= right_end and right_start <= left_end


def _duplicate_candidates(units: list[dict[str, Any]], alias_groups: list[dict[str, Any]]) -> dict[str, Any]:
    same_ref: dict[str, list[dict[str, str]]] = defaultdict(list)
    same_section: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for unit in units:
        for ref in unit["primary_bible_refs"]:
            same_ref[ref["osis"]].append({"unit_id": unit["unit_id"], "title": unit["title"]})
        for source in unit["sources"]:
            for section in source["source_sections"]:
                same_section[(source["project_id"], section)].append(
                    {"unit_id": unit["unit_id"], "title": unit["title"]}
                )

    similar_pairs = []
    overlapping_reference_pairs = []
    for index, left in enumerate(units):
        left_title = _normalized_title(left["title"])
        if not left_title:
            continue
        for right in units[index + 1 :]:
            overlaps = [
                {"left": left_ref["osis"], "right": right_ref["osis"]}
                for left_ref in left["primary_bible_refs"]
                for right_ref in right["primary_bible_refs"]
                if _refs_overlap(left_ref, right_ref)
            ]
            if overlaps:
                overlapping_reference_pairs.append(
                    {
                        "left_unit_id": left["unit_id"],
                        "left_title": left["title"],
                        "right_unit_id": right["unit_id"],
                        "right_title": right["title"],
                        "overlaps": overlaps,
                        "decision": "review_merge_supplement_or_keep_separate",
                    }
                )
            if left["unit_type"] != right["unit_type"]:
                continue
            ratio = SequenceMatcher(None, left_title, _normalized_title(right["title"])).ratio()
            left_aliases = set(left.get("aliases") or [])
            right_aliases = set(right.get("aliases") or [])
            shared_aliases = sorted(left_aliases & right_aliases)
            if ratio >= 0.62 or (shared_aliases and ratio >= 0.38):
                similar_pairs.append(
                    {
                        "left_unit_id": left["unit_id"],
                        "left_title": left["title"],
                        "right_unit_id": right["unit_id"],
                        "right_title": right["title"],
                        "title_similarity": round(ratio, 3),
                        "shared_aliases": shared_aliases,
                        "decision": "review_merge_supplement_or_keep_separate",
                    }
                )
    similar_pairs.sort(key=lambda item: -item["title_similarity"])
    alias_group_candidates = []
    for group in alias_groups:
        terms = [group.get("preferred") or "", *(group.get("aliases") or [])]
        # One-character terms (e.g. 義) are too broad for automatic matching.
        terms = [term.lower() for term in terms if len(term.strip()) >= 2]
        matches = []
        for unit in units:
            haystack = " ".join([unit["title"], *(unit.get("aliases") or [])]).lower()
            matched = [term for term in terms if term in haystack]
            if matched:
                matches.append({"unit_id": unit["unit_id"], "title": unit["title"], "matched_terms": matched})
        if len(matches) > 1:
            alias_group_candidates.append(
                {
                    "preferred": group.get("preferred"),
                    "units": matches,
                    "decision": "shared_topic_entry_not_automatic_merge",
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "same_primary_reference_groups": [
            {"osis": key, "units": value, "decision": "review_scope_overlap"}
            for key, value in sorted(same_ref.items())
            if len(value) > 1
        ],
        "same_source_section_groups": [
            {"project_id": key[0], "source_section": key[1], "units": value, "decision": "review_unit_boundary"}
            for key, value in sorted(same_section.items())
            if len(value) > 1
        ],
        "overlapping_reference_pairs": overlapping_reference_pairs,
        "alias_group_candidates": alias_group_candidates,
        "similar_title_pairs": similar_pairs,
    }


def _review_items(units: list[dict[str, Any]], duplicates: dict[str, Any]) -> list[dict[str, Any]]:
    review: list[dict[str, Any]] = []
    for unit in units:
        if unit["status"] == "confirmed_seed":
            continue
        reasons = []
        if not unit["primary_bible_refs"] and unit["unit_type"] == "passage":
            reasons.append("passage_topic_without_reliable_reference")
        if not unit["topic_assignments"]:
            reasons.append("no_topic_assignment")
        elif any(item["confidence"] == "low" for item in unit["topic_assignments"]):
            reasons.append("low_confidence_topic_assignment")
        if any(item["confidence"] == "low" for item in unit["content_category_suggestions"]):
            reasons.append("content_category_inferred_from_topic_type")
        if len(unit["topic_assignments"]) > 3:
            reasons.append("many_topic_assignments")
        if any(source["unresolved_source_sections"] for source in unit["sources"]):
            reasons.append("source_heading_could_not_be_resolved")
        if reasons:
            review.append(
                {
                    "unit_id": unit["unit_id"],
                    "title": unit["title"],
                    "reasons": reasons,
                    "suggested_action": "confirm_or_edit_metadata_only",
                }
            )

    for group in duplicates["same_primary_reference_groups"]:
        review.append(
            {
                "reference": group["osis"],
                "unit_ids": [unit["unit_id"] for unit in group["units"]],
                "titles": [unit["title"] for unit in group["units"]],
                "reasons": ["multiple_units_share_primary_reference"],
                "suggested_action": "decide_merge_supplement_or_keep_separate",
            }
        )
    return review


def _review_markdown(
    manifest: dict[str, Any],
    bible_index: dict[str, Any],
    topic_tree: dict[str, Any],
    alias_groups: list[dict[str, Any]],
    duplicates: dict[str, Any],
    review_items: list[dict[str, Any]],
) -> str:
    lines = [
        "# 種子目錄審閱稿",
        "",
        "> 這是只讀盤點產生的候選目錄；尚未修改任何已發布文稿或正式前台索引。",
        "",
        "## 一、盤點摘要",
        "",
        f"- 系列：{manifest['series_title']}",
        f"- 已發布 Project：{manifest['published_project_count']}",
        f"- 候選 canonical units：{manifest['canonical_unit_count']}",
        f"- 經文單元：{manifest['passage_unit_count']}",
        f"- 概念單元：{manifest['concept_unit_count']}",
        f"- 待確認事項：{len(review_items)}",
        "",
        "## 二、聖經目錄候選稿",
        "",
    ]
    for book in bible_index["books"]:
        lines.append(f"### {book['book_zh']}")
        lines.append("")
        for chapter in book["chapters"]:
            lines.append(f"#### 第 {chapter['chapter']} 章")
            lines.append("")
            for unit in chapter["units"]:
                lines.append(f"- {unit['display'] or unit['osis']}｜{unit['title']}｜`{unit['unit_id']}`")
            lines.append("")

    lines.extend(["## 三、主題目錄候選稿", ""])
    for parent in topic_tree["topics"]:
        count = sum(len(child["units"]) for child in parent["children"])
        lines.append(f"### {parent['label']}（{count}）")
        lines.append("")
        for child in parent["children"]:
            lines.append(f"#### {child['label']}（{len(child['units'])}）")
            lines.append("")
            for unit in child["units"]:
                role = "主要" if unit["role"] == "primary" else "次要"
                lines.append(f"- {unit['title']}｜{role}｜{unit['confidence']}｜`{unit['unit_id']}`")
            if not child["units"]:
                lines.append("- 暫無內容")
            lines.append("")

    lines.extend(["## 四、同義主題種子", ""])
    for group in alias_groups:
        lines.append(f"- **{group['preferred']}**：{'、'.join(group['aliases'])}")
        lines.append(f"  - 說明：{group['reason']}")
    lines.extend(
        [
            "",
            "## 五、重複與邊界審閱摘要",
            "",
            f"- 同一主要經文的單元群組：{len(duplicates['same_primary_reference_groups'])}",
            f"- 同一來源段落的單元群組：{len(duplicates['same_source_section_groups'])}",
            f"- 經文範圍重疊的單元配對：{len(duplicates['overlapping_reference_pairs'])}",
            f"- 同義主題群組：{len(duplicates['alias_group_candidates'])}",
            f"- 標題相近的單元配對：{len(duplicates['similar_title_pairs'])}",
            "",
            "## 六、待人工確認",
            "",
        ]
    )
    for item in review_items:
        title = item.get("title") or "／".join(item.get("titles") or [])
        lines.append(f"- {title}｜{', '.join(item['reasons'])}")
    return "\n".join(lines).rstrip() + "\n"


def build_seed_catalog(
    *,
    data_root: Path,
    series_id: str,
    output_dir: Path,
    taxonomy_path: Path = DEFAULT_TAXONOMY_PATH,
    review_decisions_path: Path = DEFAULT_REVIEW_DECISIONS_PATH,
) -> dict[str, Any]:
    """Build a review-only catalog without mutating source manuscripts or indexes."""
    data_root = data_root.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    taxonomy = _load_json(taxonomy_path)
    review_decisions = _load_json(review_decisions_path)
    projects, series = _load_published_projects(data_root, series_id)
    index_path = data_root / "sermon_search" / "topic_index.json"
    topic_index = _load_json(index_path)

    source_hashes_before = {project_id: item["final_sha256"] for project_id, item in projects.items()}
    units: list[dict[str, Any]] = []
    for topic in topic_index.get("topics") or []:
        source_records = _source_records(topic, projects, series_id)
        if not source_records:
            continue
        unit = {
                "unit_id": _stable_unit_id(topic),
                "source_topic_id": topic.get("id"),
                "title": topic.get("name") or "",
                "unit_type": topic.get("type") or "concept",
                "size": topic.get("size") or "medium",
                "status": "candidate_requires_review",
                "primary_bible_refs": _primary_refs(topic),
                "content_category_suggestions": _category_suggestions(topic, source_records),
                "topic_assignments": _classify_topic(topic, taxonomy),
                "aliases": topic.get("taxonomy_aliases") or [],
                "notes": topic.get("notes"),
                "sources": source_records,
            }
        units.append(_apply_review_decision(unit, review_decisions))
    units.sort(key=lambda item: (item["unit_type"], item["title"], item["unit_id"]))

    bible_index = _build_bible_index(units)
    topic_tree = _build_topic_tree(units, taxonomy)
    duplicates = _duplicate_candidates(units, taxonomy.get("alias_groups") or [])
    review_items = _review_items(units, duplicates)
    generated_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "review_only_not_published",
        "generated_at": generated_at,
        "series_id": series_id,
        "series_title": series.get("title") or "",
        "published_project_count": len(projects),
        "canonical_unit_count": len(units),
        "passage_unit_count": sum(unit["unit_type"] == "passage" for unit in units),
        "concept_unit_count": sum(unit["unit_type"] == "concept" for unit in units),
        "input_topic_index": str(index_path),
        "input_topic_index_sha256": _sha256(index_path),
        "input_taxonomy": str(taxonomy_path.resolve()),
        "input_taxonomy_sha256": _sha256(taxonomy_path),
        "input_review_decisions": str(review_decisions_path.resolve()),
        "input_review_decisions_sha256": _sha256(review_decisions_path),
        "source_projects": sorted(projects.values(), key=lambda item: (item["lecture_order"], item["project_order"])),
        "safety": {
            "published_manuscripts_modified": False,
            "formal_topic_index_modified": False,
            "output_isolated_from_data_root": not output_dir.is_relative_to(data_root),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "catalog_manifest.json", manifest)
    _write_json(
        output_dir / "canonical_units.json",
        {"schema_version": SCHEMA_VERSION, "series_id": series_id, "units": units},
    )
    _write_json(output_dir / "bible_index.json", bible_index)
    _write_json(output_dir / "topic_taxonomy.json", topic_tree)
    _write_json(
        output_dir / "topic_aliases.json",
        {
            "schema_version": SCHEMA_VERSION,
            "status": "candidate_requires_review",
            "alias_groups": taxonomy.get("alias_groups") or [],
        },
    )
    _write_json(output_dir / "duplicate_candidates.json", duplicates)
    _write_json(
        output_dir / "review_needed.json",
        {"schema_version": SCHEMA_VERSION, "count": len(review_items), "items": review_items},
    )
    (output_dir / "review.md").write_text(
        _review_markdown(
            manifest,
            bible_index,
            topic_tree,
            taxonomy.get("alias_groups") or [],
            duplicates,
            review_items,
        ),
        encoding="utf-8",
    )

    source_hashes_after = {project_id: _sha256(Path(item["final_path"])) for project_id, item in projects.items()}
    if source_hashes_before != source_hashes_after:
        raise RuntimeError("Published manuscript changed while building the review catalog")
    return manifest


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a review-only seed catalog from published manuscripts")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY_PATH)
    parser.add_argument("--review-decisions", type=Path, default=DEFAULT_REVIEW_DECISIONS_PATH)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    manifest = build_seed_catalog(
        data_root=args.data_root,
        series_id=args.series_id,
        output_dir=args.output,
        taxonomy_path=args.taxonomy,
        review_decisions_path=args.review_decisions,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
