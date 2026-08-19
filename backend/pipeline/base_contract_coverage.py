"""量測 base contract 對母本（notes manuscript）的覆蓋率。

這是一個**純量測**工具：讀取已發布文章的 base contract 與其母本 ``final.md``，
以確定性規則（不呼叫任何模型、不寫入 ``DATA_BASE_DIR``）比對：

1. 掃母本，找出所有「引用或解釋本篇經文範圍」的段落 —— 依**經文引用**判斷，
   不依章節標題。
2. 對照 contract 的 ``base_source``（範圍）與 ``required_argument_steps``（已成為
   論證步驟的句子）。
3. 把每一句分類成三類：

   * ``已成為 required step``
   * ``在契約範圍內但未成為 step``
   * ``完全在契約範圍外``

4. 標出承重候選（原文觀察 / 交叉經文 / 明確推論橋梁）。

CLI::

    python -m backend.pipeline.base_contract_coverage \
        --output-dir docs/wang-knowledge-platform/base-contract-coverage

相同輸入必定產生相同輸出（沒有時間戳、沒有隨機性）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Sequence

# --------------------------------------------------------------------------
# 分類標籤
# --------------------------------------------------------------------------

CATEGORY_STEP = "已成為 required step"
CATEGORY_IN_SCOPE_UNUSED = "在契約範圍內但未成為 step"
CATEGORY_OUT_OF_SCOPE = "完全在契約範圍外"

CATEGORY_ORDER = (CATEGORY_STEP, CATEGORY_IN_SCOPE_UNUSED, CATEGORY_OUT_OF_SCOPE)

FLAG_ORIGINAL_LANGUAGE = "原文觀察"
FLAG_CROSS_REFERENCE = "交叉經文"
FLAG_INFERENCE_BRIDGE = "推論橋梁"

FLAG_ORDER = (FLAG_ORIGINAL_LANGUAGE, FLAG_CROSS_REFERENCE, FLAG_INFERENCE_BRIDGE)


# --------------------------------------------------------------------------
# 經文引用解析
# --------------------------------------------------------------------------

#: 常見中文聖經書卷簡稱（繁簡並列）。長簡稱必須排在短簡稱之前。
BOOK_ABBREVIATIONS: tuple[str, ...] = (
    "撒上", "撒下", "王上", "王下", "代上", "代下",
    "林前", "林後", "林后", "帖前", "帖後", "帖后",
    "提前", "提後", "提后", "彼前", "彼後", "彼后",
    "約一", "約二", "約三", "约一", "约二", "约三",
    "創", "创", "出", "利", "民", "申", "書", "书", "士", "得",
    "拉", "尼", "斯", "伯", "詩", "诗", "箴", "傳", "传", "歌",
    "賽", "赛", "耶", "哀", "結", "结", "但", "何", "珥", "摩",
    "俄", "拿", "彌", "弥", "鴻", "鸿", "哈", "番", "該", "该",
    "亞", "亚", "瑪", "玛",
    "太", "可", "路", "約", "约", "徒", "羅", "罗",
    "加", "弗", "腓", "西", "多", "門", "门", "來", "来",
    "雅", "猶", "犹", "啟", "启",
)

#: 繁簡歸一，讓 ``太``/``马太`` 之類的書卷代號可以互相比對。
BOOK_CANONICAL: dict[str, str] = {
    "创": "創", "书": "書", "诗": "詩", "传": "傳", "赛": "賽", "结": "結",
    "弥": "彌", "鸿": "鴻", "该": "該", "亚": "亞", "玛": "瑪",
    "约": "約", "罗": "羅", "门": "門", "来": "來", "犹": "猶", "启": "啟",
    "林后": "林後", "帖后": "帖後", "提后": "提後", "彼后": "彼後",
    "约一": "約一", "约二": "約二", "约三": "約三",
}

_BOOK_PATTERN = "|".join(re.escape(book) for book in BOOK_ABBREVIATIONS)
_DASH = r"[-–—~〜]"

_REF_RE = re.compile(
    rf"(?P<book>{_BOOK_PATTERN})\s*"
    rf"(?P<chapter>\d{{1,3}})\s*[:：]\s*"
    rf"(?P<start>\d{{1,3}})\s*(?:{_DASH}\s*(?P<end>\d{{1,3}}))?"
)

#: 「、17–20、25」這類同章續接引用。
_REF_TAIL_RE = re.compile(rf"\s*[、,，]\s*(?P<start>\d{{1,3}})\s*(?:{_DASH}\s*(?P<end>\d{{1,3}}))?(?![:：\d])")

#: 「第15、16節」這類只有節數的引用，書卷與章由上文繼承。
_VERSE_ONLY_RE = re.compile(
    rf"第\s*(?P<first>\d{{1,3}})\s*(?:{_DASH}\s*(?P<first_end>\d{{1,3}}))?"
    rf"(?P<rest>(?:\s*[、,，]\s*\d{{1,3}}(?:\s*{_DASH}\s*\d{{1,3}})?)*)\s*節"
)

_GREEK_HEBREW_RE = re.compile(r"[Ͱ-Ͽἀ-῿֐-׿]")

_ORIGINAL_LANGUAGE_KEYWORDS: tuple[str, ...] = (
    "原文", "希臘文", "希伯來文", "希伯来文", "字義", "詞義", "词义", "直譯",
    "定冠詞", "陽性", "陰性", "中性名詞", "單數", "复数", "複數", "語法", "词性",
    "詞性", "動詞", "名詞", "時態", "未來完成式",
)

_INFERENCE_KEYWORDS: tuple[str, ...] = (
    "因此", "所以", "由此", "可見", "可见", "這說明", "这说明", "說明",
    "表明", "意味", "正是因為", "正是因为", "從而", "从而", "故此",
    "可以推論", "可以推论", "因而", "換言之", "换言之", "這一原則",
    "這正是", "反映了", "顯示", "显示", "揭示", "證明", "证明",
)


@dataclass(frozen=True)
class ScriptureRef:
    """一個經文引用（單章、節區間）。"""

    book: str
    chapter: int
    start_verse: int
    end_verse: int

    def label(self) -> str:
        if self.start_verse == self.end_verse:
            return f"{self.book}{self.chapter}:{self.start_verse}"
        return f"{self.book}{self.chapter}:{self.start_verse}-{self.end_verse}"

    def overlaps(self, other: "ScriptureRef") -> bool:
        return (
            self.book == other.book
            and self.chapter == other.chapter
            and self.start_verse <= other.end_verse
            and other.start_verse <= self.end_verse
        )


def canonical_book(book: str) -> str:
    return BOOK_CANONICAL.get(book, book)


def parse_scripture_refs(text: str, inherited_book_chapter: tuple[str, int] | None = None) -> list[ScriptureRef]:
    """從一段文字取出所有經文引用（依出現順序，保留重複）。

    ``inherited_book_chapter`` 供「第15、16節」這類只有節數的引用繼承書卷與章。
    """

    refs: list[ScriptureRef] = []
    last_book_chapter = inherited_book_chapter

    pos = 0
    while True:
        match = _REF_RE.search(text, pos)
        if match is None:
            break
        book = canonical_book(match.group("book"))
        chapter = int(match.group("chapter"))
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        if end < start:
            end = start
        refs.append(ScriptureRef(book, chapter, start, end))
        last_book_chapter = (book, chapter)

        pos = match.end()
        # 同章續接引用，例如「可 5:9、17–20、25」。
        while True:
            tail = _REF_TAIL_RE.match(text, pos)
            if tail is None:
                break
            t_start = int(tail.group("start"))
            t_end = int(tail.group("end") or t_start)
            refs.append(ScriptureRef(book, chapter, t_start, max(t_start, t_end)))
            pos = tail.end()

    if last_book_chapter is not None:
        book, chapter = last_book_chapter
        for match in _VERSE_ONLY_RE.finditer(text):
            first = int(match.group("first"))
            first_end = int(match.group("first_end") or first)
            refs.append(ScriptureRef(book, chapter, first, max(first, first_end)))
            rest = match.group("rest") or ""
            for piece in re.finditer(rf"(\d{{1,3}})(?:\s*{_DASH}\s*(\d{{1,3}}))?", rest):
                p_start = int(piece.group(1))
                p_end = int(piece.group(2) or p_start)
                refs.append(ScriptureRef(book, chapter, p_start, max(p_start, p_end)))

    return refs


def parse_passage_range(passage: str) -> ScriptureRef:
    """解析 contract 的 ``passage`` 欄位，例如 ``Matt.16.21-Matt.16.23``。"""

    parts = passage.split("-")
    first = parts[0].strip().split(".")
    if len(first) < 3:
        raise ValueError(f"unsupported passage format: {passage!r}")
    book = first[0]
    chapter = int(first[1])
    start = int(first[2])
    end = start
    if len(parts) > 1:
        last = parts[-1].strip().split(".")
        if len(last) >= 3:
            end = int(last[2])
    return ScriptureRef(book, chapter, start, max(start, end))


#: contract 的 passage 使用英文書卷代號，母本使用中文簡稱。
BOOK_CODE_TO_CHINESE: dict[str, str] = {
    "Matt": "太", "Mark": "可", "Luke": "路", "John": "約", "Acts": "徒",
    "Rom": "羅", "Eph": "弗",
}


# --------------------------------------------------------------------------
# Markdown 切分
# --------------------------------------------------------------------------


@dataclass
class Segment:
    """母本中的一個區塊（標題、段落或引用區塊）。"""

    kind: str  # "heading" | "paragraph" | "quote"
    text: str
    line_no: int
    heading_level: int = 0
    section_title: str = ""  # 最近的 "##" 標題（含標記）
    block_title: str = ""  # 最近的最深層標題（含標記）
    refs: list[ScriptureRef] = field(default_factory=list)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def split_segments(markdown_text: str) -> list[Segment]:
    """把 markdown 切成標題 / 段落 / 引用區塊，保留行號與所屬標題。"""

    lines = markdown_text.splitlines()
    segments: list[Segment] = []
    section_title = ""
    block_title = ""

    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            if level <= 2:
                section_title = stripped
            block_title = stripped
            segments.append(
                Segment(
                    kind="heading",
                    text=stripped,
                    line_no=index + 1,
                    heading_level=level,
                    section_title=section_title,
                    block_title=block_title,
                )
            )
            index += 1
            continue

        kind = "quote" if stripped.startswith(">") else "paragraph"
        start_line = index + 1
        buffer: list[str] = []
        while index < len(lines):
            current = lines[index].strip()
            if not current or _HEADING_RE.match(current):
                break
            is_quote = current.startswith(">")
            if (kind == "quote") != is_quote:
                break
            buffer.append(current.lstrip(">").strip() if is_quote else current)
            index += 1

        segments.append(
            Segment(
                kind=kind,
                text="\n".join(buffer),
                line_no=start_line,
                section_title=section_title,
                block_title=block_title,
            )
        )

    return segments


#: 句尾標點（含跟在後面的收尾引號／括號）。
_SENTENCE_END_RE = re.compile(r"[。！？!?]+[」』）\)”\"’']*")


def _append_trimmed(
    spans: list[tuple[int, int]], line: str, offset: int, start: int, end: int
) -> None:
    piece = line[start:end]
    lead = len(piece) - len(piece.lstrip())
    trail = len(piece) - len(piece.rstrip())
    if end - start > lead + trail:
        spans.append((offset + start + lead, offset + end - trail))


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """同樣的斷句，但回報每一句在 ``text`` 裡的 ``[start, end)``。

    只回傳句子文字，就無法判斷某個 verbatim span 蓋到了哪一句；而斷句規則有兩
    份就等於有兩種句子。因此帶偏移量的版本是本體，``split_sentences`` 由它導出。
    """

    spans: list[tuple[int, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        position = 0
        for match in _SENTENCE_END_RE.finditer(bare):
            _append_trimmed(spans, bare, offset, position, match.end())
            position = match.end()
        _append_trimmed(spans, bare, offset, position, len(bare))
        offset += len(line)
    return spans


def split_sentences(text: str) -> list[str]:
    """中文句子切分：以 。！？ 斷句，收尾的引號跟著上一句。"""

    return [text[start:end] for start, end in sentence_spans(text)]


# --------------------------------------------------------------------------
# 相關段落判定 + 分類
# --------------------------------------------------------------------------


def annotate_scripture_refs(segments: Sequence[Segment]) -> None:
    """就地填入每個 segment 的經文引用（含節數繼承）。"""

    last_book_chapter: tuple[str, int] | None = None
    current_section = None
    for segment in segments:
        if segment.section_title != current_section:
            current_section = segment.section_title
            last_book_chapter = None
        refs = parse_scripture_refs(segment.text, last_book_chapter)
        segment.refs = refs
        for ref in refs:
            last_book_chapter = (ref.book, ref.chapter)


def mark_passage_relevance(
    segments: Sequence[Segment],
    target: ScriptureRef,
    step_excerpts: Sequence[str] = (),
    dominance_threshold: float = 0.6,
) -> dict[int, str]:
    """判定哪些段落「引用或解釋本篇經文範圍」。

    回傳 ``{segment index: reason}``。三條確定性規則：

    ``direct``
        段落本身含有與本篇經文重疊的引用。
    ``continuation``
        段落完全沒有經文引用，且緊接在同一個最深層標題區塊內的相關段落之後
        （例如引用區塊後面的原文解釋）。
    ``section_dominant``
        整個 ``##`` 章節的經文引用有 ≥ ``dominance_threshold`` 比例落在本篇範圍
        內，該章節內沒有引用的段落也視為在解釋本篇（例如「附錄」的教學原則）。
    ``required_step``
        contract 自己已把該段落的句子選為 required step，因此依定義屬於本篇。
    """

    relevance: dict[int, str] = {}

    # 規則 0：contract 自己指認的段落
    for index, segment in enumerate(segments):
        if segment.kind == "heading":
            continue
        if any(excerpt_matches_paragraph(segment.text, excerpt) for excerpt in step_excerpts):
            relevance[index] = "required_step"

    # 規則 1：直接引用
    for index, segment in enumerate(segments):
        if segment.kind == "heading":
            continue
        if any(ref.overlaps(target) for ref in segment.refs):
            relevance[index] = "direct"

    # 規則 1b：標題含本篇引用 → 該區塊所有段落
    heading_hit_block: set[str] = set()
    for segment in segments:
        if segment.kind == "heading" and any(ref.overlaps(target) for ref in segment.refs):
            heading_hit_block.add(f"{segment.line_no}:{segment.text}")
    if heading_hit_block:
        current_key = ""
        for index, segment in enumerate(segments):
            if segment.kind == "heading":
                current_key = f"{segment.line_no}:{segment.text}"
                continue
            if current_key in heading_hit_block and index not in relevance:
                relevance[index] = "heading"

    # 規則 2：區塊內續接
    previous_relevant = False
    for index, segment in enumerate(segments):
        if segment.kind == "heading":
            previous_relevant = False
            continue
        if index in relevance:
            previous_relevant = True
            continue
        if previous_relevant and not segment.refs:
            relevance[index] = "continuation"
            previous_relevant = True
            continue
        previous_relevant = False

    # 規則 3：章節主導
    section_counts: dict[str, list[int]] = {}
    for segment in segments:
        counts = section_counts.setdefault(segment.section_title, [0, 0])
        for ref in segment.refs:
            counts[1] += 1
            if ref.overlaps(target):
                counts[0] += 1
    dominant_sections = {
        title
        for title, (hits, total) in section_counts.items()
        if total > 0 and hits / total >= dominance_threshold
    }
    # 章節主導只作用到「沒有引到別段經文」的區塊，否則像「以弗所書的作者問題」
    # 這種延伸討論會被誤判為本篇內容。
    block_profile: dict[int, list[bool]] = {}
    block_of: dict[int, int] = {}
    current_block = -1
    for index, segment in enumerate(segments):
        if segment.kind == "heading":
            current_block = index
        block_of[index] = current_block
        profile = block_profile.setdefault(current_block, [False, False])
        for ref in segment.refs:
            if ref.overlaps(target):
                profile[0] = True
            else:
                profile[1] = True

    for index, segment in enumerate(segments):
        if segment.kind == "heading" or index in relevance:
            continue
        if segment.section_title not in dominant_sections:
            continue
        has_overlap, has_other = block_profile.get(block_of[index], [False, False])
        if has_overlap or not has_other:
            relevance[index] = "section_dominant"

    return relevance


def normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


#: 太短的片段（例如「（太 16:20）」「彼得拉住耶穌說：」）不足以構成 step 對應，
#: 否則模糊比對會把任何含經文編號的短句都算成 required step。
MIN_MATCH_CHARS = 12


def _pair_matches(sentence: str, excerpt: str, threshold: float) -> bool:
    s = normalize_for_match(sentence)
    e = normalize_for_match(excerpt)
    if not s or not e:
        return False
    if len(s) < MIN_MATCH_CHARS or len(e) < MIN_MATCH_CHARS:
        return False
    if e in s or s in e:
        return True
    matcher = SequenceMatcher(None, s, e, autojunk=False)
    blocks = matcher.get_matching_blocks()
    overlap = sum(block.size for block in blocks)
    longest = max((block.size for block in blocks), default=0)
    if longest < MIN_MATCH_CHARS:
        return False
    return overlap / len(s) >= threshold


def _pair_score(sentence: str, excerpt: str, threshold: float) -> float:
    s = normalize_for_match(sentence)
    e = normalize_for_match(excerpt)
    if not s or not e:
        return 0.0
    if len(s) < MIN_MATCH_CHARS or len(e) < MIN_MATCH_CHARS:
        return 0.0
    if e in s or s in e:
        return 1.0
    matcher = SequenceMatcher(None, s, e, autojunk=False)
    blocks = matcher.get_matching_blocks()
    longest = max((block.size for block in blocks), default=0)
    if longest < MIN_MATCH_CHARS:
        return 0.0
    ratio = sum(block.size for block in blocks) / len(s)
    return ratio if ratio >= threshold else 0.0


def excerpt_match_score(sentence: str, excerpt: str, threshold: float = 0.6) -> float:
    """句子 ↔ ``source_excerpt`` 的比對分數（0 表示不算對應）。

    ``source_excerpt`` 可能橫跨母本的數個句子，也可能只是某一句的片段，因此除了
    整段比對之外，也逐句比對 excerpt 自身切出的句子。完全包含記 1.0，其餘以
    共同子字串佔句子的比例計分，低於 ``threshold`` 一律視為不對應。
    """

    best = _pair_score(sentence, excerpt, threshold)
    if best >= 1.0:
        return best
    pieces = split_sentences(excerpt)
    if len(pieces) > 1:
        for piece in pieces:
            best = max(best, _pair_score(sentence, piece, threshold))
    return best


def excerpt_matches(sentence: str, excerpt: str, threshold: float = 0.6) -> bool:
    """``excerpt_match_score`` 的布林版本。"""

    return excerpt_match_score(sentence, excerpt, threshold) > 0.0


def excerpt_matches_paragraph(paragraph: str, excerpt: str, threshold: float = 0.6) -> bool:
    """段落中是否有任何一句對應到這個 ``source_excerpt``。"""

    return any(excerpt_matches(sentence, excerpt, threshold) for sentence in split_sentences(paragraph))


def load_bearing_flags(sentence: str, refs: Sequence[ScriptureRef], target: ScriptureRef) -> list[str]:
    flags: list[str] = []
    if _GREEK_HEBREW_RE.search(sentence) or any(k in sentence for k in _ORIGINAL_LANGUAGE_KEYWORDS):
        flags.append(FLAG_ORIGINAL_LANGUAGE)
    if any(not ref.overlaps(target) for ref in refs):
        flags.append(FLAG_CROSS_REFERENCE)
    if any(k in sentence for k in _INFERENCE_KEYWORDS):
        flags.append(FLAG_INFERENCE_BRIDGE)
    return flags


# --------------------------------------------------------------------------
# Contract / manuscript 載入
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RequiredStep:
    step_id: str
    section_id: str
    role: str
    source_id: str
    source_span: str
    source_excerpt: str


@dataclass
class ContractView:
    contract_id: str
    passage: str
    target: ScriptureRef
    base_source_id: str
    base_source_path: str
    section_anchor: str
    steps: list[RequiredStep]


def load_contract(path: Path) -> ContractView:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload.get("result", payload)
    base_source = result.get("base_source") or {}
    steps: list[RequiredStep] = []
    for section in result.get("sections", []):
        for step in section.get("required_argument_steps", []):
            steps.append(
                RequiredStep(
                    step_id=step.get("step_id", ""),
                    section_id=section.get("section_id", ""),
                    role=step.get("role", ""),
                    source_id=step.get("source_id", ""),
                    source_span=step.get("source_span", ""),
                    source_excerpt=step.get("source_excerpt", ""),
                )
            )
    passage = result.get("passage", "")
    raw_target = parse_passage_range(passage)
    target = ScriptureRef(
        BOOK_CODE_TO_CHINESE.get(raw_target.book, raw_target.book),
        raw_target.chapter,
        raw_target.start_verse,
        raw_target.end_verse,
    )
    return ContractView(
        contract_id=result.get("contract_id", ""),
        passage=passage,
        target=target,
        base_source_id=base_source.get("source_id", ""),
        base_source_path=base_source.get("path", ""),
        section_anchor=base_source.get("section_anchor", ""),
        steps=steps,
    )


def load_source_paths(snapshot_path: Path) -> dict[str, str]:
    """從 knowledge snapshot 取出 ``source_id -> source_path``。"""

    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for document in payload.get("source_documents", []) or []:
        source_id = document.get("source_id")
        source_path = document.get("source_path")
        if source_id and source_path:
            mapping[source_id] = source_path
    return mapping


def load_notes_manuscripts(snapshot_path: Path) -> list[tuple[str, str]]:
    """快照裡所有的母本（``notes_manuscript``），依快照順序。"""

    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    manuscripts: list[tuple[str, str]] = []
    for document in payload.get("source_documents", []) or []:
        if document.get("source_type") != "notes_manuscript":
            continue
        source_id = document.get("source_id")
        source_path = document.get("source_path")
        if source_id and source_path:
            manuscripts.append((source_id, source_path))
    return manuscripts


# --------------------------------------------------------------------------
# 量測
# --------------------------------------------------------------------------


@dataclass
class SentenceRow:
    manuscript_id: str
    line_no: int
    section_title: str
    block_title: str
    kind: str
    relevance_reason: str
    in_scope: bool
    category: str
    matched_step_ids: list[str]
    flags: list[str]
    text: str


@dataclass
class ArticleCoverage:
    draft_id: str
    contract_id: str
    passage: str
    target_label: str
    section_anchor: str
    base_source_path: str
    manuscripts: list[dict]
    rows: list[SentenceRow]
    steps: list[RequiredStep]
    unmatched_step_ids: list[str]

    def counts(self) -> dict[str, int]:
        counts = {name: 0 for name in CATEGORY_ORDER}
        for row in self.rows:
            counts[row.category] += 1
        return counts

    def flag_counts(self) -> dict[str, dict[str, int]]:
        table = {name: {flag: 0 for flag in FLAG_ORDER} for name in CATEGORY_ORDER}
        for row in self.rows:
            for flag in row.flags:
                table[row.category][flag] += 1
        return table


def _resolve_manuscripts(
    contract: ContractView,
    source_paths: dict[str, str],
    notes_manuscripts: Sequence[tuple[str, str]] = (),
) -> list[tuple[str, Path]]:
    """base_source 的母本 → required step 引用到的其他母本 → 快照裡其餘母本。"""

    ordered: list[tuple[str, Path]] = []
    seen: set[str] = set()
    if contract.base_source_path:
        ordered.append((contract.base_source_id, Path(contract.base_source_path)))
        seen.add(contract.base_source_id)
    for step in contract.steps:
        if step.source_id and step.source_id not in seen:
            path = source_paths.get(step.source_id)
            if path:
                ordered.append((step.source_id, Path(path)))
                seen.add(step.source_id)
    for source_id, path in notes_manuscripts:
        if source_id not in seen:
            ordered.append((source_id, Path(path)))
            seen.add(source_id)
    return [(source_id, path) for source_id, path in ordered if path.exists()]


def measure_article(
    draft_id: str,
    contract_path: Path,
    snapshot_path: Path,
) -> ArticleCoverage:
    contract = load_contract(contract_path)
    source_paths = load_source_paths(snapshot_path)
    manuscripts = _resolve_manuscripts(contract, source_paths, load_notes_manuscripts(snapshot_path))

    rows: list[SentenceRow] = []
    manuscript_meta: list[dict] = []
    # step_id -> (best score, 出現順序, 段落鍵)；一個 step 只認一個段落。
    best_step_location: dict[str, tuple[float, int, tuple[str, int]]] = {}
    row_scores: list[dict[str, float]] = []
    row_segment_keys: list[tuple[str, int]] = []

    for source_id, path in manuscripts:
        text = path.read_text(encoding="utf-8")
        segments = split_segments(text)
        annotate_scripture_refs(segments)
        step_excerpts = [
            step.source_excerpt for step in contract.steps if step.source_id == source_id and step.source_excerpt
        ]
        relevance = mark_passage_relevance(segments, contract.target, step_excerpts)

        is_base = source_id == contract.base_source_id
        anchor = contract.section_anchor.strip() if is_base else ""
        anchor_found = False
        char_total = sum(len(re.sub(r"\s+", "", seg.text)) for seg in segments if seg.kind != "heading")
        scope_chars = 0
        relevant_chars = 0

        for index, segment in enumerate(segments):
            if segment.kind == "heading":
                if anchor and segment.text.strip() == anchor:
                    anchor_found = True
                continue
            in_scope = bool(anchor) and segment.section_title.strip() == anchor
            if in_scope:
                scope_chars += len(re.sub(r"\s+", "", segment.text))
            reason = relevance.get(index)
            if reason is None:
                continue
            relevant_chars += len(re.sub(r"\s+", "", segment.text))

            segment_key = (source_id, segment.line_no)
            for sentence in split_sentences(segment.text):
                # 經文引用區塊是聖經原文，不是母本的論證；required step 一律
                # 錨在母本散文上，因此引用區塊不計為「已成為 step」。
                scores: dict[str, float] = {}
                if segment.kind != "quote":
                    for step in contract.steps:
                        if step.source_id != source_id:
                            continue
                        score = excerpt_match_score(sentence, step.source_excerpt)
                        if score <= 0.0:
                            continue
                        scores[step.step_id] = score
                        order = len(rows)
                        previous = best_step_location.get(step.step_id)
                        if previous is None or score > previous[0]:
                            best_step_location[step.step_id] = (score, order, segment_key)

                sentence_refs = parse_scripture_refs(sentence) or segment.refs
                row_scores.append(scores)
                row_segment_keys.append(segment_key)
                rows.append(
                    SentenceRow(
                        manuscript_id=source_id,
                        line_no=segment.line_no,
                        section_title=segment.section_title,
                        block_title=segment.block_title,
                        kind=segment.kind,
                        relevance_reason=reason,
                        in_scope=in_scope,
                        category=CATEGORY_IN_SCOPE_UNUSED if in_scope else CATEGORY_OUT_OF_SCOPE,
                        matched_step_ids=[],
                        flags=load_bearing_flags(sentence, sentence_refs, contract.target),
                        text=sentence,
                    )
                )

        manuscript_meta.append(
            {
                "source_id": source_id,
                "path": str(path),
                "is_base_source": is_base,
                "section_anchor": anchor,
                "section_anchor_found": anchor_found if anchor else None,
                "manuscript_chars": char_total,
                "contract_scope_chars": scope_chars,
                "passage_relevant_chars": relevant_chars,
            }
        )

    # 每個 step 只保留它分數最高的那個段落；同一段落內可跨多句（excerpt 常橫跨兩句）。
    for row, scores, key in zip(rows, row_scores, row_segment_keys):
        kept = sorted(
            step_id
            for step_id in scores
            if best_step_location[step_id][2] == key
        )
        row.matched_step_ids = kept
        if kept:
            row.category = CATEGORY_STEP

    matched_steps = {step_id for step_id, (_, _, _) in best_step_location.items()}
    unmatched = [step.step_id for step in contract.steps if step.step_id not in matched_steps]

    return ArticleCoverage(
        draft_id=draft_id,
        contract_id=contract.contract_id,
        passage=contract.passage,
        target_label=contract.target.label(),
        section_anchor=contract.section_anchor,
        base_source_path=contract.base_source_path,
        manuscripts=manuscript_meta,
        rows=rows,
        steps=contract.steps,
        unmatched_step_ids=unmatched,
    )


# --------------------------------------------------------------------------
# 報告輸出
# --------------------------------------------------------------------------

_REASON_LABEL = {
    "required_step": "contract 已指認此段落",
    "direct": "直接引用本篇經文",
    "heading": "所屬小標題引用本篇經文",
    "continuation": "緊接相關段落且無其他經文引用",
    "section_dominant": "所屬章節經文引用以本篇為主",
}


def render_markdown(coverage: ArticleCoverage) -> str:
    counts = coverage.counts()
    total = sum(counts.values())
    flags = coverage.flag_counts()

    lines: list[str] = []
    lines.append(f"# {coverage.draft_id} — base contract 對母本覆蓋率")
    lines.append("")
    lines.append("> 由 `python -m backend.pipeline.base_contract_coverage` 確定性產生；純比對，未呼叫任何模型。")
    lines.append("")
    lines.append("## 一、量測對象")
    lines.append("")
    lines.append(f"- contract：`{coverage.contract_id}`（passage `{coverage.passage}` → 母本引用比對目標 `{coverage.target_label}`）")
    lines.append(f"- `base_source.section_anchor`：`{coverage.section_anchor}`")
    lines.append(f"- required steps 總數：{len(coverage.steps)}")
    if coverage.unmatched_step_ids:
        lines.append(f"- **未能在母本比對到 source_excerpt 的 step**：{', '.join(coverage.unmatched_step_ids)}")
    lines.append("")
    lines.append("| 母本 | 角色 | 全文字數 | 契約範圍字數 | 本篇經文相關字數 |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    for meta in coverage.manuscripts:
        role = "base_source" if meta["is_base_source"] else "step 引用之其他母本"
        lines.append(
            f"| `{meta['source_id']}` | {role} | {meta['manuscript_chars']} | "
            f"{meta['contract_scope_chars']} | {meta['passage_relevant_chars']} |"
        )
    lines.append("")

    lines.append("## 二、分類統計（句）")
    lines.append("")
    lines.append("| 分類 | 句數 | 佔比 |")
    lines.append("| --- | ---: | ---: |")
    for name in CATEGORY_ORDER:
        share = f"{counts[name] / total * 100:.1f}%" if total else "-"
        lines.append(f"| {name} | {counts[name]} | {share} |")
    lines.append(f"| **合計（本篇經文相關句）** | **{total}** | 100.0% |")
    lines.append("")

    lines.append("### 承重候選分佈")
    lines.append("")
    lines.append("| 分類 | " + " | ".join(FLAG_ORDER) + " |")
    lines.append("| --- | " + " | ".join(["---:"] * len(FLAG_ORDER)) + " |")
    for name in CATEGORY_ORDER:
        lines.append("| " + name + " | " + " | ".join(str(flags[name][flag]) for flag in FLAG_ORDER) + " |")
    lines.append("")

    lines.append("## 三、逐句清單")
    lines.append("")
    for name in CATEGORY_ORDER:
        subset = [row for row in coverage.rows if row.category == name]
        lines.append(f"### {name}（{len(subset)} 句）")
        lines.append("")
        if not subset:
            lines.append("（無）")
            lines.append("")
            continue
        lines.append("| # | 母本 | 行 | 所屬章節 | 判定依據 | 承重候選 | step | 句子 |")
        lines.append("| ---: | --- | ---: | --- | --- | --- | --- | --- |")
        for order, row in enumerate(subset, start=1):
            sentence = row.text.replace("|", "\\|")
            lines.append(
                f"| {order} | `{row.manuscript_id}` | {row.line_no} | {row.section_title} | "
                f"{_REASON_LABEL.get(row.relevance_reason, row.relevance_reason)} | "
                f"{'、'.join(row.flags) or '-'} | {', '.join(row.matched_step_ids) or '-'} | {sentence} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def coverage_to_dict(coverage: ArticleCoverage) -> dict:
    return {
        "draft_id": coverage.draft_id,
        "contract_id": coverage.contract_id,
        "passage": coverage.passage,
        "target": coverage.target_label,
        "section_anchor": coverage.section_anchor,
        "base_source_path": coverage.base_source_path,
        "manuscripts": coverage.manuscripts,
        "required_step_count": len(coverage.steps),
        "unmatched_step_ids": coverage.unmatched_step_ids,
        "counts": coverage.counts(),
        "flag_counts": coverage.flag_counts(),
        "sentences": [
            {
                "manuscript_id": row.manuscript_id,
                "line_no": row.line_no,
                "section_title": row.section_title,
                "block_title": row.block_title,
                "kind": row.kind,
                "relevance_reason": row.relevance_reason,
                "in_contract_scope": row.in_scope,
                "category": row.category,
                "matched_step_ids": row.matched_step_ids,
                "load_bearing_flags": row.flags,
                "text": row.text,
            }
            for row in coverage.rows
        ],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

#: draft_id -> claim-layer staging 目錄名
ARTICLE_TARGETS: tuple[tuple[str, str], ...] = (
    ("DRAFT-M16-001-V1", "matthew-16-1-12-sources"),
    ("DRAFT-M16-002-V1", "matthew-16-13-20-sources"),
    ("DRAFT-M16-003-V1", "matthew-16-21-23-sources"),
)


def _platform_root(data_base_dir: str | None = None) -> Path:
    value = data_base_dir or os.getenv("DATA_BASE_DIR")
    if not value:
        raise RuntimeError("DATA_BASE_DIR is required")
    return Path(value).expanduser().resolve() / "wang-knowledge-platform"


def resolve_snapshot_path(draft_dir: Path, draft_id: str) -> Path:
    """快照檔名因文章而異，由 ``editorial-draft-manifest.json`` 決定。"""

    manifest_path = draft_dir / "editorial-draft-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for draft in manifest.get("drafts", []) or []:
            if draft.get("draft_id") != draft_id:
                continue
            relative = (draft.get("audit_config") or {}).get("knowledge_snapshot_path")
            if relative:
                candidate = draft_dir / relative
                if candidate.exists():
                    return candidate
    for name in ("knowledge-snapshot.json", "shared-knowledge-projection.json"):
        candidate = draft_dir / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"no knowledge snapshot found under {draft_dir}")


def resolve_article_inputs(draft_id: str, passage_dir: str, data_base_dir: str | None = None) -> tuple[Path, Path]:
    root = _platform_root(data_base_dir)
    contract = root / "staging" / "claim-layer" / passage_dir / "authoring-v1" / "base-manuscript-contract.json"
    draft_dir = root / "repository" / "editorial_drafts" / draft_id
    return contract, resolve_snapshot_path(draft_dir, draft_id)


def run(output_dir: Path, data_base_dir: str | None = None) -> list[ArticleCoverage]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[ArticleCoverage] = []
    for draft_id, passage_dir in ARTICLE_TARGETS:
        contract_path, snapshot_path = resolve_article_inputs(draft_id, passage_dir, data_base_dir)
        coverage = measure_article(draft_id, contract_path, snapshot_path)
        results.append(coverage)
        (output_dir / f"{draft_id}.md").write_text(render_markdown(coverage), encoding="utf-8")
        (output_dir / f"{draft_id}.json").write_text(
            json.dumps(coverage_to_dict(coverage), ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    (output_dir / "summary.md").write_text(render_summary(results), encoding="utf-8")
    return results


def render_summary(results: Iterable[ArticleCoverage]) -> str:
    results = list(results)
    lines = ["# 三篇已發布文章的 base contract 對母本覆蓋率（總表）", ""]
    lines.append("> 由 `python -m backend.pipeline.base_contract_coverage` 確定性產生。")
    lines.append("")
    lines.append("| 文章 | contract | 經文 | required steps | 已成為 step | 範圍內未成為 step | 完全在範圍外 | 相關句合計 |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for coverage in results:
        counts = coverage.counts()
        total = sum(counts.values())
        lines.append(
            f"| {coverage.draft_id} | `{coverage.contract_id}` | {coverage.target_label} | "
            f"{len(coverage.steps)} | {counts[CATEGORY_STEP]} | {counts[CATEGORY_IN_SCOPE_UNUSED]} | "
            f"{counts[CATEGORY_OUT_OF_SCOPE]} | {total} |"
        )
    lines.append("")
    lines.append("## 承重候選（未成為 step 的句子）")
    lines.append("")
    lines.append("| 文章 | 範圍內未成為 step 且承重 | 範圍外且承重 |")
    lines.append("| --- | ---: | ---: |")
    for coverage in results:
        in_scope_rows = sum(
            1 for row in coverage.rows if row.category == CATEGORY_IN_SCOPE_UNUSED and row.flags
        )
        out_rows = sum(1 for row in coverage.rows if row.category == CATEGORY_OUT_OF_SCOPE and row.flags)
        lines.append(f"| {coverage.draft_id} | {in_scope_rows} | {out_rows} |")
    lines.append("")
    lines.append(METHOD_NOTES)
    return "\n".join(lines) + "\n"


METHOD_NOTES = """## 方法

1. **母本範圍**：`base_source.path` 的 `final.md`，加上任何 required step 所引用的其他母本，
   再加上該文章 knowledge snapshot 內其餘 `notes_manuscript` 來源。
2. **相關段落判定（依經文引用，不依章節標題）**，依序套用：
   `required_step`（contract 自己已指認的段落）→ `direct`（段落含本篇範圍內的經文引用）→
   `heading`（所屬小標題含本篇引用）→ `continuation`（緊接相關段落、且自身完全沒有經文引用）→
   `section_dominant`（整個 `##` 章節的經文引用有 ≥60% 落在本篇範圍，且該小標題區塊沒有只引到別段經文）。
3. **契約範圍**＝`base_source.section_anchor` 指向的那一個 `##` 章節，僅適用於 base_source 母本。
4. **分類優先序**：對應到 required step → `已成為 required step`；否則在契約範圍內 → `在契約範圍內但未成為 step`；
   否則 → `完全在契約範圍外`。
5. **step 對應**：句子與 `source_excerpt` 正規化後互相包含即算對應，否則以最長共同子字串佔句子 ≥60% 計；
   片段短於 12 字不算；經文引用區塊（`>`）不算 step；一個 step 只認分數最高的那一個段落。
6. 承重候選：`原文觀察`（希臘／希伯來文字符或原文、語法、詞義等用語）、`交叉經文`（引到本篇範圍外的經文）、
   `推論橋梁`（因此／由此／正是因為 等推論連接詞）。

## 已知限制（讀數時請一併考慮）

- 字數統計不含 markdown 標題本身，因此比人工逐字計數略低。
- `section_dominant` 的 60% 門檻是人為選定的；章節內交叉經文較多時，母本中確實在解釋本篇、但完全沒有
  經文引用的段落可能被漏掉（偏保守）。
- `已成為 required step` 是「句」數而非「step」數：一條 step 的 `source_excerpt` 常橫跨母本兩句。
- 契約範圍只由 `base_source.section_anchor` 定義。若 required step 引用了 anchor 以外的段落
  （太16:13–20 即如此），那些句子會被歸為 `已成為 required step`，但其所在章節其餘內容仍算 `完全在契約範圍外`。
- 本工具只做確定性字串／引用比對，不判斷語意；`在契約範圍內但未成為 step` 不等於「應該成為 step」。
"""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="docs/wang-knowledge-platform/base-contract-coverage",
        help="報告輸出目錄（repo 內；不會寫入 DATA_BASE_DIR）",
    )
    parser.add_argument("--data-base-dir", default=None, help="覆寫 DATA_BASE_DIR")
    args = parser.parse_args(argv)

    results = run(Path(args.output_dir), args.data_base_dir)
    for coverage in results:
        counts = coverage.counts()
        total = sum(counts.values())
        print(f"{coverage.draft_id} [{coverage.target_label}] required_steps={len(coverage.steps)} total_sentences={total}")
        for name in CATEGORY_ORDER:
            print(f"    {name}: {counts[name]}")
        if coverage.unmatched_step_ids:
            print(f"    (未比對到母本句子的 step: {', '.join(coverage.unmatched_step_ids)})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
