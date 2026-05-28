from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Sequence

from backend.api.config import DATA_BASE_PATH


@dataclass(frozen=True)
class TopicDefinition:
    label: str
    aliases: tuple[str, ...]


DEFAULT_TOPIC_TAXONOMY = {
    "topics": [
        {"label": "基督論", "aliases": ["神的兒子", "彌賽亞", "基督", "以馬內利", "道成肉身"]},
        {"label": "救恩論", "aliases": ["救贖", "赦罪", "成聖", "稱義", "成為義", "挽回祭", "救恩"]},
        {"label": "神的信實", "aliases": ["信實可靠", "信实", "信實", "神的義"]},
        {"label": "聖經無誤", "aliases": ["無誤性", "作者意圖", "inerrancy"]},
        {"label": "大衛之約", "aliases": ["大衛的子孫", "大衛王", "作王的正統性", "王位"]},
        {"label": "亞伯拉罕之約", "aliases": ["亞伯拉罕的兒子", "應許"]},
        {"label": "國度", "aliases": ["天國", "神的國", "君王", "王權"]},
        {"label": "律法", "aliases": ["法利賽人", "人的遺傳", "神的誡命", "安息日"]},
        {"label": "舊約應驗", "aliases": ["應驗", "舊約", "先知", "預言", "以賽亞", "創世記"]},
        {"label": "耶和華的僕人", "aliases": ["耶和華僕人", "我的僕人", "受苦僕人", "僕人之歌", "仆人之歌"]},
        {"label": "原文分析", "aliases": ["希臘文", "希伯來文", "原文", "詞義", "文法", "παρθένος", "הָעַלְמָה"]},
        {"label": "翻譯批判", "aliases": ["翻譯", "和合本", "中文翻譯", "譯本", "翻錯"]},
        {"label": "護教", "aliases": ["科學", "碳定年", "宗教", "衝突"]},
        {"label": "生活應用", "aliases": ["應用", "牧養", "焦慮", "信心", "順服"]},
        {"label": "家譜", "aliases": ["十四代", "跳代", "族譜", "世代"]},
        {"label": "門徒訓練", "aliases": ["門徒", "小信", "跟隨", "捨己"]},
    ]
}

CONTENT_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "釋經": ("釋經", "經文解析", "文本觀察", "原始語境"),
    "聖經神學": ("聖經神學", "舊約應驗", "創世記", "先知"),
    "系統神學": ("系統神學", "神學意義", "救恩論", "基督論"),
    "原文分析": ("希臘文", "希伯來文", "原文", "逐詞分析", "詞義"),
    "翻譯批判": ("翻譯", "和合本", "翻錯", "譯本"),
    "護教": ("護教", "科學", "宗教", "碳定年"),
    "生活應用": ("生活應用", "應用", "牧養"),
    "附錄": ("附錄",),
}

GENERIC_HEADING_TOPICS = {
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "七",
    "八",
    "九",
    "十",
    "釋經",
    "神學意義",
    "生活應用",
    "附錄",
    "開場",
    "結論",
}


def topic_taxonomy_path() -> Path:
    configured = os.getenv("SERMON_SEARCH_TOPIC_TAXONOMY")
    if configured:
        return Path(configured).expanduser().resolve()
    return DATA_BASE_PATH / "sermon_search" / "topic_taxonomy.json"


def ensure_topic_taxonomy_file() -> Path:
    path = topic_taxonomy_path()
    return ensure_topic_taxonomy_file_at(path)


def ensure_topic_taxonomy_file_at(path: Path) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_TOPIC_TAXONOMY, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@lru_cache(maxsize=4)
def load_topic_definitions(path_value: str | None = None) -> tuple[TopicDefinition, ...]:
    path = ensure_topic_taxonomy_file_at(Path(path_value)) if path_value else ensure_topic_taxonomy_file()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = DEFAULT_TOPIC_TAXONOMY
    definitions: List[TopicDefinition] = []
    for item in payload.get("topics", []):
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        aliases = tuple(
            alias.strip()
            for alias in item.get("aliases", [])
            if isinstance(alias, str) and alias.strip()
        )
        definitions.append(TopicDefinition(label=label, aliases=aliases))
    return tuple(definitions)


def reload_topic_definitions() -> None:
    load_topic_definitions.cache_clear()


def configured_topics() -> List[str]:
    return [definition.label for definition in load_topic_definitions(str(topic_taxonomy_path()))]


def extract_topics(texts: Iterable[str]) -> List[str]:
    blob = "\n".join(t for t in texts if t)
    found: List[str] = []
    for topic in load_topic_definitions(str(topic_taxonomy_path())):
        candidates = (topic.label, *topic.aliases)
        if any(alias.lower() in blob.lower() for alias in candidates):
            found.append(topic.label)
    return found


def discover_topics_from_headings(heading_path: Sequence[str]) -> List[str]:
    topics: List[str] = []
    for heading in heading_path:
        cleaned = _clean_heading_topic(heading)
        if cleaned and cleaned not in GENERIC_HEADING_TOPICS:
            topics.append(cleaned)
    return _dedupe(topics)


def discover_topics_from_text(text: str, max_topics: int = 8) -> List[str]:
    candidates: List[str] = []
    for quoted in re.findall(r"[「『《]([^」』》]{2,18})[」』》]", text):
        candidates.append(quoted.strip())
    for match in re.finditer(r"([\u4e00-\u9fff]{2,12})(?:這一|的)?(?:觀念|主題|意象|稱謂|身份|問題|結構|預言)", text):
        candidates.append(match.group(1).strip())
    for match in re.finditer(r"(?:關於|涉及|指向|強調)([\u4e00-\u9fff]{2,12})", text):
        candidates.append(match.group(1).strip())
    return [topic for topic in _dedupe(_clean_heading_topic(c) for c in candidates) if topic][:max_topics]


def merge_topics(*topic_lists: Iterable[str]) -> List[str]:
    return _dedupe(topic for topic_list in topic_lists for topic in topic_list if topic)


def infer_content_types(texts: Iterable[str]) -> List[str]:
    blob = "\n".join(t for t in texts if t)
    found: List[str] = []
    for content_type, aliases in CONTENT_TYPE_ALIASES.items():
        if any(alias.lower() in blob.lower() for alias in aliases):
            found.append(content_type)
    return found or ["講稿"]


def expand_topic_query(topics: Iterable[str]) -> List[str]:
    requested = {topic for topic in topics if topic}
    expanded: set[str] = set(requested)
    lowered = {topic.lower() for topic in requested}
    for definition in load_topic_definitions(str(topic_taxonomy_path())):
        aliases = (definition.label, *definition.aliases)
        if definition.label in requested or any(alias.lower() in lowered for alias in aliases):
            expanded.add(definition.label)
            expanded.update(definition.aliases)
    return sorted(expanded)


def _clean_heading_topic(heading: str) -> str:
    text = re.sub(r"^#+\s*", "", heading or "").strip()
    text = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", text)
    text = re.sub(r"^\d+[、.．]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[（(].*?[）)]", "", text).strip()
    text = text.strip("：: -—")
    if len(text) > 24:
        text = re.split(r"[：:，,；;、—-]", text)[0].strip()
    return text


def _dedupe(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = (item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out
