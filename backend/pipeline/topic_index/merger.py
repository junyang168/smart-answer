from __future__ import annotations

import re
from typing import Dict, List

from backend.api.sermon_search.bible_refs import extract_refs

from .extractor import build_taxonomy_aliases
from .models import TopicEntry, TopicSource


def _normalize_name(name: str) -> str:
    """Canonical form used for concept-topic deduplication — not shown to users."""
    text = name.strip()
    # Drop parenthetical qualifiers: （人的整體性）or (整體性)
    text = re.sub(r"[（(][^）)]{1,20}[）)]", "", text)
    # Normalize full-width punctuation
    text = text.replace("：", ":").replace("—", "-").replace("－", "-")
    # Collapse whitespace
    text = re.sub(r"\s+", "", text)
    return text.lower()


def _merge_key(entry: TopicEntry) -> str:
    """
    Passage topics are keyed by their canonical Bible-verse anchor (OSIS), so
    that two entries about the same verse merge deterministically regardless of
    how the LLM phrased the topic name. Concept topics fall back to name.
    """
    if entry.type == "passage":
        refs = extract_refs(entry.name)
        if refs:
            return f"passage:{refs[0].osis}"
    return f"{entry.type}:{_normalize_name(entry.name)}"


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _merge_sources(entries: List[TopicEntry]) -> List[TopicSource]:
    """
    Union sources across all group members. Sources from the SAME project are
    combined into one TopicSource with unioned section labels and lun_dian, so
    no arguments are lost when two same-project topics merge on one verse.
    """
    by_project: Dict[str, TopicSource] = {}
    order: List[str] = []
    for entry in entries:
        for source in entry.sources:
            pid = source.project_id
            if pid not in by_project:
                by_project[pid] = TopicSource(
                    project_id=source.project_id,
                    project_title=source.project_title,
                    series_id=source.series_id,
                    lecture_title=source.lecture_title,
                    source_sections=list(source.source_sections),
                    lun_dian=list(source.lun_dian),
                )
                order.append(pid)
            else:
                existing = by_project[pid]
                existing.source_sections = _dedupe_keep_order(
                    existing.source_sections + source.source_sections
                )
                existing.lun_dian = _dedupe_keep_order(existing.lun_dian + source.lun_dian)
    return [by_project[pid] for pid in order]


def merge_entries(all_entries: List[TopicEntry]) -> List[TopicEntry]:
    """
    Group entries by merge key (verse anchor for passages, normalized name for
    concepts) and merge their sources. The entry with the most lun_dian is kept
    as the representative for name/type/size/notes.
    """
    groups: Dict[str, List[TopicEntry]] = {}
    order: List[str] = []  # insertion order for stable output

    for entry in all_entries:
        key = _merge_key(entry)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(entry)

    merged: List[TopicEntry] = []
    for key in order:
        group = groups[key]
        # Representative = entry with the most lun_dian across all its sources
        rep = max(group, key=lambda e: sum(len(s.lun_dian) for s in e.sources))

        combined_sources = _merge_sources(group)

        # Recompute aliases from the merged name + all lun_dian, so alias logic
        # is a pure derived artifact independent of what the cache stored.
        all_lun_dian = [ld for src in combined_sources for ld in src.lun_dian]
        combined_aliases = build_taxonomy_aliases(rep.name, all_lun_dian)

        # Merge notes from all members (some may be null)
        notes_parts = _dedupe_keep_order([e.notes for e in group if e.notes])
        notes = "；".join(notes_parts) if notes_parts else None

        canonical_ref = None
        canonical_ref_raw = None
        if rep.type == "passage":
            refs = extract_refs(rep.name)
            if refs:
                canonical_ref = refs[0].osis
                canonical_ref_raw = refs[0].raw

        merged.append(
            TopicEntry(
                id="",  # assigned below
                name=rep.name,
                type=rep.type,
                size=rep.size,
                sources=combined_sources,
                notes=notes,
                taxonomy_aliases=combined_aliases[:8],
                canonical_ref=canonical_ref,
                canonical_ref_raw=canonical_ref_raw,
            )
        )

    # Sort: passages first (by verse anchor), then concepts alphabetically
    def _sort_key(entry: TopicEntry):
        if entry.type == "passage":
            refs = extract_refs(entry.name)
            anchor = refs[0].osis if refs else entry.name
            return (0, anchor)
        return (1, entry.name)

    merged.sort(key=_sort_key)

    # Assign stable sequential IDs
    for idx, entry in enumerate(merged, start=1):
        entry.id = f"topic_{idx:03d}"

    return merged
