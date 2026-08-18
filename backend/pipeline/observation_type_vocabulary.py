"""The closed vocabulary for `observation_type`, and the map onto it.

`step_type` has been an enum since the first extraction schema and holds ten
values across 1016 evidence steps.  `observation_type` was declared as a bare
string, and 430 observations produced 246 distinct values -- 196 of them used
exactly once, in mixed Simplified Chinese, Traditional Chinese and English.
Sixty-eight of those values describe original-language observations, so
`observation_type = 'original_language'` finds 36 records out of roughly 138.
Any measurement of how much of the professor's exegesis reaches the argument
layer is computed against the wrong set until this is closed.

The six categories are not a new taxonomy.  They are the ones the extraction
prompt already names: 经文文字、原文、文体、上下文、历史文化和叙事结构.

Rules carry a confidence.  `CERTAIN` is for values that differ from a category
only by spelling or language -- `原文语法`, `greek_grammar`, `希腊文文法观察`
are one thing written three ways, and folding them together loses nothing.
`PROPOSED` is for values whose category is a genuine editorial judgment:
a translation note may or may not be an original-language observation, and
deciding that silently would inflate the very number this work exists to
measure.  `normalize` returns only `CERTAIN` mappings; `classify` returns the
proposal too, so a reviewer decides from a suggestion rather than from blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


SCRIPTURE_TEXT = "scripture_text"
ORIGINAL_LANGUAGE = "original_language"
LITERARY_FORM = "literary_form"
LITERARY_CONTEXT = "literary_context"
HISTORICAL_CULTURAL = "historical_cultural"
NARRATIVE_STRUCTURE = "narrative_structure"

OBSERVATION_TYPES: tuple[str, ...] = (
    SCRIPTURE_TEXT,
    ORIGINAL_LANGUAGE,
    LITERARY_FORM,
    LITERARY_CONTEXT,
    HISTORICAL_CULTURAL,
    NARRATIVE_STRUCTURE,
)

CERTAIN = "certain"
PROPOSED = "proposed"

# Labels that name the field rather than the value, and were used for
# genuinely unrelated records.  These cannot be mapped label-at-a-time at any
# confidence: all twelve observations typed `背景` were checked, and they are
# genre instruction, the professor's own academic biography, and critiques of
# dispensationalism -- not one is historical-cultural background to a passage.
# A pattern would file all twelve under `historical_cultural` on the strength
# of the word alone.  Reviewing these per record is the only correct handling.
AMBIGUOUS_LABELS: frozenset[str] = frozenset({"背景"})


@dataclass(frozen=True)
class Classification:
    """One legacy value's category, and how much the map is claiming."""

    raw: str
    category: Optional[str]
    confidence: Optional[str]

    @property
    def needs_review(self) -> bool:
        return self.confidence != CERTAIN


# Ordered: the first pattern that matches wins.  Order encodes precedence
# between categories that legitimately overlap -- an observation about the
# wording of the Greek is an original-language observation, not a scripture-text
# one, so the original-language markers are tested before the wording markers.
_RULES: tuple[tuple[str, str, str], ...] = (
    # -- CERTAIN ---------------------------------------------------------
    # An explicit source-language marker settles it regardless of what else
    # the label says.  `original_language` is listed literally because several
    # values extend it (`original_language_structure`) with a word that would
    # otherwise be claimed by a later category.
    (CERTAIN, ORIGINAL_LANGUAGE, r"原文|希腊|希臘|希伯來|希伯来|七十士|亞蘭|亚兰"),
    (CERTAIN, ORIGINAL_LANGUAGE, r"original_language|greek|hebrew|septuagint|semitic"),
    # Language-internal description: lexis, semantics, grammar, morphology.
    (CERTAIN, ORIGINAL_LANGUAGE, r"詞義|词义|詞彙|词汇|詞語|词语|用詞|用词|措辭|措辞|術語|术语|詞典|词典"),
    (CERTAIN, ORIGINAL_LANGUAGE, r"語義|语义|語境搭配|语境搭配|語義代稱|语义代称|象徵詞|象征词|死亡語境詞彙"),
    (CERTAIN, ORIGINAL_LANGUAGE, r"文法|語法|语法|時態|时态|時式|时式|語氣|语气"),
    (CERTAIN, ORIGINAL_LANGUAGE, r"介詞|介词|代詞|代词|指稱|指称"),
    (CERTAIN, ORIGINAL_LANGUAGE, r"lexic|semantic|grammat|grammar|tense|aspect|pronoun|connective"),
    (CERTAIN, ORIGINAL_LANGUAGE, r"terminolog|greek_term|key_word|word_contrast|interpretive_wording"),
    # Historical, cultural, geographical and material setting.
    (CERTAIN, HISTORICAL_CULTURAL, r"歷史|历史|文化|背景|地理|習俗|习俗|節期|节期|禮儀|礼仪|獻祭|献祭|逾越節|逾越节"),
    (CERTAIN, HISTORICAL_CULTURAL, r"文物|盟約|盟约|立約|立约|聖餐設立|圣餐设立|收信對象|收信对象|人物歷史|人物历史"),
    (CERTAIN, HISTORICAL_CULTURAL, r"histor|cultural|geograph|medical|demographic|covenant|authorship|audience_context|manuscript"),
    # Where the passage sits relative to other text.
    (CERTAIN, LITERARY_CONTEXT, r"上下文|語境觀察|语境观察|互文|互釋|互释|平行經文|平行经文|跨經文|跨经文|正典次序|經文次序|经文次序"),
    (CERTAIN, LITERARY_CONTEXT, r"context|intertext|parallel_passage|canonical_list|canonical_context|corpus_search|biblical_usage"),
    # Genre and rhetorical shape.
    (CERTAIN, LITERARY_FORM, r"文體|文体|體裁|体裁|詩歌|诗歌|平行結構|平行结构|對偶|对偶|反問|反问|修辭|修辞|隱喻|隐喻"),
    (CERTAIN, LITERARY_FORM, r"書信.*公式|书信.*公式|公式觀察|公式观察|末世宣告|口誤|口误|圖示|图示"),
    (CERTAIN, LITERARY_FORM, r"genre|parallelism|rhetorical|stylistic|figurative|paradox|typolog|literary_form|literary_marker"),
    # How the account is arranged and moves.
    (CERTAIN, NARRATIVE_STRUCTURE, r"敘事|叙事|結構|结构|次序|順序|顺序|階段|阶段|遞進|递进|列舉|列举|清單|清单|論證標記|论证标记"),
    (CERTAIN, NARRATIVE_STRUCTURE, r"narrative|structure|sequence|salvation_historical|distinction_in_argument"),
    # What the text says, once none of the sharper categories claimed it.
    (CERTAIN, SCRIPTURE_TEXT, r"經文|经文|聖經|圣经|正典|scripture|scriptural|canonical|textual|biblical"),
    # -- PROPOSED --------------------------------------------------------
    # A translation note is about the source language often enough to suggest
    # `original_language`, and about the Chinese rendering often enough that
    # the map must not decide.  `原文与古译本` and `原文及英译表述` never reach
    # here; their 原文 marker was already CERTAIN above.
    (PROPOSED, ORIGINAL_LANGUAGE, r"譯本|译本|翻譯|翻译|譯文|译文|英譯|英译|translation"),
    # Number and quantity labels: a grammatical-number observation in some
    # sermons, a plain count of what the text lists in others.
    (PROPOSED, ORIGINAL_LANGUAGE, r"人數|人数|數量|数量|numerical|numbering"),
    # Comparison labels name the operation, not the material being compared.
    (PROPOSED, LITERARY_CONTEXT, r"synoptic|comparison|比較|比较|差異|差异"),
)

_COMPILED: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (confidence, category, re.compile(pattern, re.IGNORECASE))
    for confidence, category, pattern in _RULES
)


def classify(raw: Optional[str]) -> Classification:
    """Categorize one legacy `observation_type`, reporting its confidence.

    A `Classification` with `confidence is None` means no rule claimed the
    value at all; one with `PROPOSED` means a rule has a suggestion but the
    call is an editorial judgment.  Both need a human.
    """

    value = (raw or "").strip()
    if not value:
        return Classification(raw=value, category=None, confidence=None)
    if value in AMBIGUOUS_LABELS:
        return Classification(raw=value, category=None, confidence=None)
    if value in OBSERVATION_TYPES:
        return Classification(raw=value, category=value, confidence=CERTAIN)
    for confidence, category, pattern in _COMPILED:
        if pattern.search(value):
            return Classification(raw=value, category=category, confidence=confidence)
    return Classification(raw=value, category=None, confidence=None)


def normalize(raw: Optional[str]) -> Optional[str]:
    """Map one legacy value, but only when the mapping is a spelling fix.

    Returns `None` for anything a reviewer must decide -- never a default.
    """

    result = classify(raw)
    return result.category if result.confidence == CERTAIN else None


def classify_all(values: Iterable[Optional[str]]) -> dict[str, Classification]:
    """Classify many raw values at once, keyed by the raw value as given."""

    return {str(value or ""): classify(value) for value in values}
