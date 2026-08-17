"""Tests for the deterministic base-contract → base-manuscript coverage measurement."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.pipeline.base_contract_coverage import (
    CATEGORY_IN_SCOPE_UNUSED,
    CATEGORY_OUT_OF_SCOPE,
    CATEGORY_STEP,
    FLAG_CROSS_REFERENCE,
    FLAG_INFERENCE_BRIDGE,
    FLAG_ORIGINAL_LANGUAGE,
    ScriptureRef,
    coverage_to_dict,
    excerpt_matches,
    load_bearing_flags,
    measure_article,
    parse_passage_range,
    parse_scripture_refs,
    render_markdown,
    resolve_article_inputs,
    split_segments,
    split_sentences,
)


# ---------------------------------------------------------------------------
# scripture reference parsing
# ---------------------------------------------------------------------------


def test_parse_scripture_refs_basic_and_range():
    refs = parse_scripture_refs("太 16:21 以「從此」作為轉折，另見太 16:5–11。")
    assert refs == [
        ScriptureRef("太", 16, 21, 21),
        ScriptureRef("太", 16, 5, 11),
    ]


def test_parse_scripture_refs_same_chapter_tail():
    refs = parse_scripture_refs("「彌賽亞的秘密」的例外：可 5:9、17–20、25")
    assert refs == [
        ScriptureRef("可", 5, 9, 9),
        ScriptureRef("可", 5, 17, 20),
        ScriptureRef("可", 5, 25, 25),
    ]


def test_parse_scripture_refs_verse_only_inherits_book_chapter():
    refs = parse_scripture_refs("太 16:13 之後，耶穌在第15、16節直接轉向門徒。")
    assert ScriptureRef("太", 16, 15, 15) in refs
    assert ScriptureRef("太", 16, 16, 16) in refs


def test_parse_scripture_refs_ignores_bare_numbers_without_book():
    assert parse_scripture_refs("他在15:7 說明了這件事") == []


def test_parse_scripture_refs_simplified_book_is_canonicalised():
    assert parse_scripture_refs("约 20:23") == [ScriptureRef("約", 20, 23, 23)]


def test_scripture_ref_overlap():
    target = ScriptureRef("太", 16, 21, 23)
    assert ScriptureRef("太", 16, 22, 22).overlaps(target)
    assert ScriptureRef("太", 16, 20, 21).overlaps(target)
    assert not ScriptureRef("太", 16, 17, 17).overlaps(target)
    assert not ScriptureRef("可", 16, 22, 22).overlaps(target)


def test_parse_passage_range():
    assert parse_passage_range("Matt.16.21-Matt.16.23") == ScriptureRef("Matt", 16, 21, 23)
    assert parse_passage_range("Matt.16.18") == ScriptureRef("Matt", 16, 18, 18)


# ---------------------------------------------------------------------------
# markdown / sentence splitting
# ---------------------------------------------------------------------------


def test_split_segments_tracks_section_and_kind():
    segments = split_segments("## 一、甲\n\n段落一。\n\n> 引用文字。\n\n### 附錄\n\n段落二。\n")
    kinds = [(segment.kind, segment.section_title) for segment in segments]
    assert kinds == [
        ("heading", "## 一、甲"),
        ("paragraph", "## 一、甲"),
        ("quote", "## 一、甲"),
        ("heading", "## 一、甲"),
        ("paragraph", "## 一、甲"),
    ]


def test_split_sentences_keeps_closing_quote_with_sentence():
    assert split_sentences("他說：「不可如此。」然後離開。") == ["他說：「不可如此。」", "然後離開。"]


# ---------------------------------------------------------------------------
# excerpt matching
# ---------------------------------------------------------------------------


def test_excerpt_matches_substring_of_longer_sentence():
    sentence = "然而，從太 16:21 開始，耶穌轉向教導彌賽亞的「性質」：祂必須上耶路撒冷，並且被殺，第三日復活。"
    excerpt = "祂必須上耶路撒冷，並且被殺，第三日復活。"
    assert excerpt_matches(sentence, excerpt)


def test_excerpt_matches_rejects_short_fragments():
    # 「（太 16:20）」不得因為含經節編號就被算成 required step。
    assert not excerpt_matches("（太 16:20）", "太 16:21 以「從此」作為轉折，標誌著教導進入第二個階段。")


def test_excerpt_matches_unrelated_sentence_is_false():
    sentence = "這一原則反映了有效教導需要配合學習者當前的理解程度，教師需要先將自己的程度降低到與學生相近的位置，再逐步引導提升。"
    excerpt = "耶穌先確立門徒對彌賽亞「身分」的認識，才進一步教導彌賽亞的「性質」，正是因為後者建立在前者的基礎之上。"
    assert not excerpt_matches(sentence, excerpt)


def test_excerpt_matches_multi_sentence_excerpt():
    # source_excerpt 橫跨兩句，而母本的第一句還帶有 excerpt 沒有的前綴。
    excerpt = (
        "卻共同持有一個教訓（the teaching，原文單數）：不接受耶穌是神所差來的彌賽亞。"
        "這正是耶穌要門徒防備的「酵」的真正所指。"
    )
    sentence = "法利賽人和撒都該人在神學上彼此對立，卻共同持有一個教訓（the teaching，原文單數）：不接受耶穌是神所差來的彌賽亞。"
    assert excerpt_matches(sentence, excerpt)


# ---------------------------------------------------------------------------
# load-bearing flags
# ---------------------------------------------------------------------------


def test_load_bearing_flags():
    target = ScriptureRef("太", 16, 21, 23)
    greek = "此處原文動詞 φρονέω（fron-eh'-o），意為「關心、重視」。"
    assert FLAG_ORIGINAL_LANGUAGE in load_bearing_flags(greek, [], target)

    cross = "這一認信在太 16:17 彼得的回答中達到頂點。"
    flags = load_bearing_flags(cross, parse_scripture_refs(cross), target)
    assert FLAG_CROSS_REFERENCE in flags

    bridge = "因此，門徒真正難以接受的不是身分，而是性質。"
    assert FLAG_INFERENCE_BRIDGE in load_bearing_flags(bridge, [], target)


# ---------------------------------------------------------------------------
# end-to-end on a synthetic corpus
# ---------------------------------------------------------------------------

SYNTHETIC_MANUSCRIPT = """## 二、從馬可福音現象回應錯誤解經

### 釋經

#### 可 1:43-45 的保密命令

耶穌醫治痲瘋病人後不准人傳說，可 1:43-45 有明確的實際原因。

#### 門徒難以明白的核心

耶穌對彼得的責備見於太 16:23：

> 耶穌轉過來，對彼得說，你是絆我腳的，因為你不體貼（φρονεῖς）神的意思。

此處原文動詞 φρονέω（fron-eh'-o），意為「關心、重視」，耶穌責備的是彼得思維的方向。

## 三、彌賽亞身分與性質的兩階段教導

### 釋經

#### 彌賽亞身分與性質的兩階段教導（太 16:21–23）

太 16:21 以「從此」作為轉折，標誌著耶穌對門徒教導進入第二個階段。這一認信在太 16:17 彼得的回答中達到頂點。彼得對此的反應見於太 16:22。

### 附錄

從耶穌分兩階段教導的方式，可以觀察到一個教學原則。耶穌先確立門徒對彌賽亞「身分」的認識，才進一步教導彌賽亞的「性質」，正是因為後者建立在前者的基礎之上。這一原則反映了有效教導需要配合學習者當前的理解程度，教師需要先將自己的程度降低到與學生相近的位置，再逐步引導提升。

### 附錄二

耶穌先確立門徒對彌賽亞身分的認識，之後才進一步教導彌賽亞的性質，這是同一個原則的另一次覆述。

### 附錄三

弗 2:20 的引用涉及以弗所書的作者爭議，此問題屬延伸討論，與本段經文無關。

## 四、捨己與背十字架

### 釋經

太 16:24 記載耶穌對門徒說，若有人要跟從我，就當捨己。
"""


@pytest.fixture()
def synthetic_inputs(tmp_path: Path) -> tuple[Path, Path]:
    manuscript = tmp_path / "final.md"
    manuscript.write_text(SYNTHETIC_MANUSCRIPT, encoding="utf-8")

    contract = tmp_path / "base-manuscript-contract.json"
    contract.write_text(
        json.dumps(
            {
                "result": {
                    "contract_id": "BMC-test-v1",
                    "passage": "Matt.16.21-Matt.16.23",
                    "base_source": {
                        "source_id": "notes_manuscript:test",
                        "path": str(manuscript),
                        "section_anchor": "## 三、彌賽亞身分與性質的兩階段教導",
                    },
                    "sections": [
                        {
                            "section_id": "reader-sec-01",
                            "required_argument_steps": [
                                {
                                    "step_id": "S01",
                                    "role": "narrative_transition",
                                    "source_id": "notes_manuscript:test",
                                    "source_span": "final.md:第三部分",
                                    "source_excerpt": "太 16:21 以「從此」作為轉折，標誌著耶穌對門徒教導進入第二個階段。",
                                },
                                {
                                    "step_id": "S02",
                                    "role": "bounded_teaching_principle",
                                    "source_id": "notes_manuscript:test",
                                    "source_span": "final.md:第三部分附錄",
                                    "source_excerpt": (
                                        "耶穌先確立門徒對彌賽亞「身分」的認識，才進一步教導彌賽亞的"
                                        "「性質」，正是因為後者建立在前者的基礎之上。"
                                    ),
                                },
                            ],
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    snapshot = tmp_path / "knowledge-snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "source_documents": [
                    {"source_id": "notes_manuscript:test", "source_path": str(manuscript)}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return contract, snapshot


def _row_for(coverage, needle: str):
    matches = [row for row in coverage.rows if needle in row.text]
    assert matches, f"sentence containing {needle!r} was not measured at all"
    assert len(matches) == 1, f"expected a single row for {needle!r}, got {len(matches)}"
    return matches[0]


def test_synthetic_measurement_classifies_three_categories(synthetic_inputs):
    contract_path, snapshot_path = synthetic_inputs
    coverage = measure_article("DRAFT-TEST", contract_path, snapshot_path)

    # φρονέω 說明位於 §二，契約 anchor 只指向 §三 → 完全在契約範圍外
    assert _row_for(coverage, "φρονέω").category == CATEGORY_OUT_OF_SCOPE

    # 附錄最後一句在 anchor 範圍內，但 required step 的 source_excerpt 停在前一句
    assert (
        _row_for(coverage, "教師需要先將自己的程度降低到與學生相近的位置").category
        == CATEGORY_IN_SCOPE_UNUSED
    )

    # required step 對應的句子
    step_row = _row_for(coverage, "才進一步教導彌賽亞的「性質」")
    assert step_row.category == CATEGORY_STEP
    assert step_row.matched_step_ids == ["S02"]

    assert coverage.unmatched_step_ids == []

    # 太 16:24 屬於另一段經文，不應被視為本篇相關
    assert not [row for row in coverage.rows if "捨己" in row.text]

    # 可 1:43-45 的段落與本篇無關
    assert not [row for row in coverage.rows if "痲瘋" in row.text]


def test_step_is_credited_to_a_single_paragraph(synthetic_inputs):
    """同一個 step 不得同時算在兩個段落上（覆述句不應重複計入）。"""

    contract_path, snapshot_path = synthetic_inputs
    coverage = measure_article("DRAFT-TEST", contract_path, snapshot_path)
    lines = {row.line_no for row in coverage.rows if "S02" in row.matched_step_ids}
    assert len(lines) == 1
    restatement = _row_for(coverage, "這是同一個原則的另一次覆述")
    assert restatement.matched_step_ids == []
    assert restatement.category == CATEGORY_IN_SCOPE_UNUSED


def test_section_dominance_skips_blocks_about_other_passages(synthetic_inputs):
    """章節主導不得把只引到別段經文的延伸討論算成本篇內容。"""

    contract_path, snapshot_path = synthetic_inputs
    coverage = measure_article("DRAFT-TEST", contract_path, snapshot_path)
    assert not [row for row in coverage.rows if "以弗所書的作者爭議" in row.text]


def test_synthetic_measurement_counts(synthetic_inputs):
    contract_path, snapshot_path = synthetic_inputs
    coverage = measure_article("DRAFT-TEST", contract_path, snapshot_path)
    counts = coverage.counts()
    assert counts[CATEGORY_STEP] == 2
    assert counts[CATEGORY_IN_SCOPE_UNUSED] >= 1
    assert counts[CATEGORY_OUT_OF_SCOPE] >= 1
    assert sum(counts.values()) == len(coverage.rows)


def test_measurement_is_deterministic(synthetic_inputs):
    contract_path, snapshot_path = synthetic_inputs
    first = measure_article("DRAFT-TEST", contract_path, snapshot_path)
    second = measure_article("DRAFT-TEST", contract_path, snapshot_path)
    assert coverage_to_dict(first) == coverage_to_dict(second)
    assert render_markdown(first) == render_markdown(second)


def test_render_markdown_contains_counts_and_listing(synthetic_inputs):
    contract_path, snapshot_path = synthetic_inputs
    markdown = render_markdown(measure_article("DRAFT-TEST", contract_path, snapshot_path))
    assert CATEGORY_STEP in markdown
    assert CATEGORY_IN_SCOPE_UNUSED in markdown
    assert CATEGORY_OUT_OF_SCOPE in markdown
    assert "教師需要先將自己的程度降低到與學生相近的位置" in markdown
    assert "φρονέω" in markdown


# ---------------------------------------------------------------------------
# acceptance fixtures against the real published data (skipped when absent)
# ---------------------------------------------------------------------------


def _real_inputs():
    data_base_dir = os.getenv("DATA_BASE_DIR")
    if not data_base_dir:
        return None
    try:
        contract_path, snapshot_path = resolve_article_inputs(
            "DRAFT-M16-003-V1", "matthew-16-21-23-sources", data_base_dir
        )
    except (RuntimeError, FileNotFoundError):
        return None
    if not contract_path.exists() or not snapshot_path.exists():
        return None
    return contract_path, snapshot_path


@pytest.mark.skipif(_real_inputs() is None, reason="published Matthew 16 data not available")
def test_real_matt16_21_23_acceptance_fixtures():
    contract_path, snapshot_path = _real_inputs()
    coverage = measure_article("DRAFT-M16-003-V1", contract_path, snapshot_path)

    phroneo = [row for row in coverage.rows if "φρονέω" in row.text]
    assert phroneo, "φρονέω explanation must be measured"
    assert all(row.category == CATEGORY_OUT_OF_SCOPE for row in phroneo)
    assert all(FLAG_ORIGINAL_LANGUAGE in row.flags for row in phroneo)

    teacher = [row for row in coverage.rows if "教師需要先將自己的程度降低到與學生相近的位置" in row.text]
    assert len(teacher) == 1
    assert teacher[0].category == CATEGORY_IN_SCOPE_UNUSED
