from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.api.sermon_search.bible_refs import extract_refs
from backend.api.sermon_search.models import DiscoveredManuscript
from backend.pipeline.stage1 import Stage1AnthropicClient

from .models import TopicEntry, TopicSource

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "topic_extraction.md"

_EXTRACTION_SCHEMA: Dict[str, Any] = {
    "name": "topic_extraction_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string", "enum": ["concept", "passage"]},
                        "size": {"type": "string", "enum": ["large", "medium", "embedded"]},
                        "source_sections": {"type": "array", "items": {"type": "string"}},
                        "lun_dian": {"type": "array", "items": {"type": "string"}},
                        "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    },
                    "required": ["name", "type", "size", "source_sections", "lun_dian", "notes"],
                },
            }
        },
        "required": ["topics"],
    },
}

_GREEK_HEBREW_RE = re.compile(r"[Ͱ-Ͽἀ-῿֐-׿]{2,}")
# Terms the professor sets off with Chinese quotation marks are deliberate,
# meaningful phrases — unlike arbitrary slices of running prose.
_QUOTED_TERM_RE = re.compile(r"[「『《]([^」』》]{2,12})[」』》]")

# Greek/Hebrew articles, conjunctions and prepositions carry no search signal.
_ORIG_LANG_STOPWORDS = {
    # Greek
    "ὁ", "ἡ", "τό", "τὸ", "οἱ", "αἱ", "τά", "τὰ",
    "τοῦ", "τῆς", "τόν", "τὸν", "τήν", "τὴν", "τῶν", "τοῖς", "ταῖς", "τῷ", "τῇ",
    "καί", "καὶ", "διά", "διὰ", "ἐν", "εἰς", "ἐκ", "ἐξ", "τε", "δέ", "δὲ",
    "μέν", "μὲν", "γάρ", "γὰρ", "ὅτι", "πρός", "πρὸς", "ἀπό", "ἀπὸ",
    # Hebrew (definite article / common particles)
    "הָ", "הַ", "ה", "וְ", "ו", "אֶת", "אֵת", "אֶל", "עַל", "מִן", "בְּ", "לְ", "כְּ",
}


def build_taxonomy_aliases(name: str, lun_dian: List[str]) -> List[str]:
    """
    Build distinctive search aliases from a topic.

    Only include high-signal terms:
      - the topic name itself
      - Greek/Hebrew original-language words (always distinctive)
      - phrases the author explicitly quoted with 「」『』《》

    We deliberately do NOT slice running CJK prose into fixed-width fragments,
    which produced meaningless aliases like 「開門見山說明馬利」.
    """
    blob = " ".join(lun_dian)
    candidates: List[str] = [name]
    candidates.extend(
        m.group(0) for m in _GREEK_HEBREW_RE.finditer(blob)
        if m.group(0) not in _ORIG_LANG_STOPWORDS
    )
    candidates.extend(m.group(1).strip() for m in _QUOTED_TERM_RE.finditer(blob))

    seen: set[str] = set()
    out: List[str] = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
        if len(out) >= 8:
            break
    return out


def _name_has_matthew_ref(name: str) -> bool:
    return any(r.book == "Matt" for r in extract_refs(name))


def _recover_passage_ref(source_sections: List[str], chunk_text: str) -> Optional[str]:
    """
    When the LLM names a passage topic thematically (no 太 X:Y prefix), recover
    the verse from the section's own source text — the reference is almost always
    stated in the body even when the heading is thematic.
    Returns a display ref like '太 14:1–12', or None.
    """
    for section in source_sections:
        heading = section.split("＞")[0].strip()
        if not heading:
            continue
        idx = chunk_text.find(heading)
        if idx == -1:
            continue
        rest = chunk_text[idx + len(heading):]
        nxt = rest.find("\n## ")
        body = rest if nxt == -1 else rest[:nxt]
        for ref in extract_refs(body):
            if ref.book == "Matt":
                return ref.raw
    return None


def extract_topics_from_chunk(
    llm: Stage1AnthropicClient,
    manuscript: DiscoveredManuscript,
    section_labels: List[str],
    chunk_text: str,
) -> List[TopicEntry]:
    """Call the LLM on one manuscript chunk and return TopicEntry objects."""
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    label_hint = "、".join(section_labels) if section_labels else "全文"
    scope = (manuscript.bible_verse or "").strip()
    scope_hint = scope if scope else "無（概論／結構性文件）"
    user_prompt = (
        f"【系列】{manuscript.series_title}\n"
        f"【講稿標題】{manuscript.project_title}\n"
        f"【本講稿經文範圍】{scope_hint}\n"
        f"【本段涵蓋段落】{label_hint}\n\n"
        f"【講稿內容】\n{chunk_text}"
    )

    payload = llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        json_schema=_EXTRACTION_SCHEMA,
        temperature=0.2,
    )

    # Overview/structural documents (no declared passage scope) have no
    # dedicated passage topics — any passage the model surfaces there is cited
    # evidence, not exegesis. Drop those entries deterministically.
    has_scope = bool(scope)

    entries: List[TopicEntry] = []
    for raw in payload.get("topics") or []:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        topic_type = raw.get("type") or "concept"
        if topic_type not in {"concept", "passage"}:
            topic_type = "concept"
        if topic_type == "passage" and not has_scope:
            continue
        size = raw.get("size") or "medium"
        if size not in {"large", "medium", "embedded"}:
            size = "medium"
        lun_dian = [str(ld).strip() for ld in (raw.get("lun_dian") or []) if str(ld).strip()]
        source_sections = [str(s).strip() for s in (raw.get("source_sections") or []) if str(s).strip()]
        notes_raw = raw.get("notes")
        notes: Optional[str] = str(notes_raw).strip() if notes_raw else None

        # Backstop: a passage topic must carry its Matthew verse. If the LLM
        # named it thematically, recover the ref from the section source text.
        if topic_type == "passage" and not _name_has_matthew_ref(name):
            recovered = _recover_passage_ref(source_sections, chunk_text)
            if recovered:
                name = f"{recovered}：{name}"

        source = TopicSource(
            project_id=manuscript.project_id,
            project_title=manuscript.project_title,
            series_id=manuscript.series_id,
            lecture_title=manuscript.lecture_title,
            source_sections=source_sections,
            lun_dian=lun_dian,
        )
        entries.append(
            TopicEntry(
                id="",  # assigned later by merger
                name=name,
                type=topic_type,
                size=size,
                sources=[source],
                notes=notes,
                # Aliases are a derived artifact recomputed at merge time from
                # (name + merged lun_dian); no need to store them in the cache.
                taxonomy_aliases=[],
            )
        )
    return entries


def extract_topics_from_manuscript(
    llm: Stage1AnthropicClient,
    manuscript: DiscoveredManuscript,
    chunk_groups: List[tuple[List[str], str]],
) -> List[TopicEntry]:
    """Run extraction over all chunk groups for one manuscript."""
    all_entries: List[TopicEntry] = []
    for section_labels, chunk_text in chunk_groups:
        entries = extract_topics_from_chunk(llm, manuscript, section_labels, chunk_text)
        all_entries.extend(entries)
    return all_entries
