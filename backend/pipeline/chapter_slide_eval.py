"""按章投影片的自評：逐句走一遍錄音，看他此刻講的和畫面此刻顯示的對不對得上。

負責人 2026-11-16 列的不可以：
1. 教授講要點 A，投影片亮著 B；
2. 教授論證 X，投影片顯示不相關的；
3. 教授在念某節經文，投影片根本沒顯示；
4. 教授講希臘文文法，投影片裡沒有。

1、2 由 `chapter_slide_claims.retime` 與 `argument_alignment` 處理（要點時間按論證步驟定，
畫面要有同一 Claim 的要點）。這裡逐句查 3 和 4，兩項都只看程序判得準的訊號：
- 3：一句逐字稿一半以上的相鄰兩字出現在某節和合本裡（與聆聽頁跟讀同一準則），
  而且這句與該節吻合度達 0.7，並有足夠的經文字詞覆蓋，才由程式判定正在念；畫面此刻要顯示該節。
- 4：一句裡有希臘文或希伯來文字母，或講到「原文、時態、現在式、將來式」等，
  畫面那一張要有原文註解或提到它的要點。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.pipeline import chapter_slides

#: 他念經文的判斷：這句的相鄰兩字有這麼多比例出現在某一節。
READING_SHARE = 0.7
#: 已顯示的經文只要有一節與這句這麼相近，就算畫面上有他念的經文。
SHOWN_SHARE = 0.5
_ORIGINAL_SCRIPT = re.compile(r"[Ͱ-Ͽἀ-῿֐-׿]")
_GRAMMAR_WORDS = re.compile(r"原文|希臘文|希伯來文|時態|現在式|將來式|未來式|過去式|不定過去")


def _bigrams(text: str) -> set[str]:
    han = "".join(re.findall(r"[㐀-鿿]", text))
    return {han[i:i + 2] for i in range(len(han) - 1)}


def _share(pairs: set[str], verse: set[str]) -> float:
    return len(pairs & verse) / len(pairs) if len(pairs) >= 5 and verse else 0.0


def _is_reading(pairs: set[str], verse: set[str]) -> bool:
    # A short phrase in a story can resemble a verse without being a reading.
    # Require half of the verse or a longer match for the automatic decision.
    return _share(pairs, verse) >= READING_SHARE and (
        len(pairs & verse) >= 12 or _share(verse, pairs) >= 0.5)


class Bible:
    """和合本，按書和章讀進來一次。"""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            from backend.bible_study.paths import cuv_dir

            root = cuv_dir()
        self.root = Path(root)
        self._chapters: dict[tuple[str, int], dict[str, str]] = {}

    def chapter(self, book: str, number: int) -> dict[str, str]:
        key = (book.upper(), number)
        if key not in self._chapters:
            try:
                self._chapters[key] = json.loads((self.root / key[0] / f"{number}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._chapters[key] = {}
        return self._chapters[key]

    def verses(self, slug: str) -> list[tuple[str, set[str]]]:
        from backend.api.scripture import parse_reference

        ref = parse_reference(slug)
        if not ref:
            return []
        chapter = self.chapter(ref["slug_book"], ref["chapter"])
        book = slug.split("-")[0]
        return [(f"{book}-{ref['chapter']}-{n}", _bigrams(chapter.get(str(n), "")))
                for n in range(ref["start"], ref["end"] + 1)]


def evaluate(
    slides: list[dict[str, Any]],
    kept: list[dict[str, Any]],
    *,
    candidates: list[str],
    bible: Bible | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """逐句的自評。candidates 是他可能在念的經文（slug），通常是本堂投影片與 Evidence 用到的各章。

    回 {"problems": [...], "checked_sentences": n}；problems 會被當成程序核對不通過。"""
    bible = bible or Bible()
    lines = chapter_slides.sentences(kept)
    order = {line["id"]: i for i, line in enumerate(lines)}
    pool = [verse for slug in candidates for verse in bible.verses(slug)]
    problems = diagram_problems(slides, lines, context or {})
    for i, line in enumerate(lines):
        slide = next((s for s in reversed(slides) if order.get(s["first"], 10 ** 9) <= i), None)
        if slide is None:
            continue
        shown = [v for item in slide.get("scriptures", []) if order.get(item["sentence"], 10 ** 9) <= i
                 for v in bible.verses(item["slug"])]
        pairs = _bigrams(line["text"])
        best = max((v for v in pool if _is_reading(pairs, v[1])),
                   key=lambda v: _share(pairs, v[1]), default=None)
        if best and not any(
                _share(pairs, verse) >= SHOWN_SHARE for _, verse in shown):
            problems.append(f"{slide['title']}（{line['id']}）: 他在念 {best[0]}，畫面沒有顯示這節經文")
        if _ORIGINAL_SCRIPT.search(line["text"]) or _GRAMMAR_WORDS.search(line["text"]):
            notes = [n for n in slide.get("inline_notes", []) if n.get("original") or n.get("explanation")]
            mentioned = any(_ORIGINAL_SCRIPT.search(p["text"]) or _GRAMMAR_WORDS.search(p["text"])
                            or "原" in p["text"] for p in slide["points"])
            if not notes and not mentioned:
                problems.append(f"{slide['title']}（{line['id']}）: 他在講原文或文法，這一張沒有原文註解或相應要點")
    return {"problems": problems, "checked_sentences": len(lines)}


def diagram_problems(slides: list[dict], lines: list[dict], context: dict) -> list[str]:
    """Check the saved diagram choices and require golden diagrams in retained scope."""
    from backend.pipeline.chapter_slide_cases import diagrams_for_lecture

    try:
        placed = chapter_slides.timed_diagrams(slides, lines, context)
    except ValueError as exc:
        return [str(exc)]
    if context.get("diagram_contract") != "claim-bound.v1":
        return []
    by_sentence = {line["id"]: line["start"] for line in lines}
    problems = []
    for case in diagrams_for_lecture(context.get("lecture", "")):
        for claim in context.get("claims", []):
            if claim["claim_id"].rsplit("-", 1)[-1] != case["claim"]:
                continue
            sentence = claim.get("stated")
            if sentence not in by_sentence:
                continue
            current = next((s for s in slides if s["first"] <= sentence <= s["last"]), None)
            diagram = placed.get(current["first"]) if current else None
            wanted = {d["sha256"] for d in claim.get("diagrams", []) if d.get("layout") == case["layout"]}
            if not diagram or diagram["sha256"] not in wanted or diagram["at"] != by_sentence[sentence]:
                problems.append(f"論證圖缺漏或出場錯誤：{case['layout']}（{sentence}）")
    return problems


def displayed_reading_sentences(slides, lines, *, bible=None):
    """A currently displayed verse carries the listener during actual Scripture reading.

    This does not excuse stale points while explaining a verse or stating a conclusion.
    Reuses the same conservative word-overlap detector as the Scripture Eval.
    """
    bible = bible or Bible()
    reading = set()
    for line in lines:
        slide = next((s for s in reversed(slides) if s['first'] <= line['id']), None)
        shown = [v for item in (slide or {}).get('scriptures', []) if item['sentence'] <= line['id']
                 for v in bible.verses(item['slug'])]
        pairs = _bigrams(line['text'])
        if any(_is_reading(pairs, verse) for _, verse in shown):
            reading.add(line['id'])
    return reading


def candidate_chapters(slides: list[dict[str, Any]], context: dict[str, Any] | None) -> list[str]:
    """他可能在念的經文：投影片和 Evidence 用到的每一章，整章都算（念到別節也認得出來）。"""
    from backend.api.scripture import parse_reference

    slugs = [item["slug"] for slide in slides for item in slide.get("scriptures", [])]
    for claim in (context or {}).get("claims", []):
        for step in claim.get("evidence_steps", []):
            slugs += step.get("scriptures", [])
    chapters = []
    for slug in slugs:
        ref = parse_reference(slug)
        if ref:
            whole = f"{slug.split('-')[0]}-{ref['chapter']}-1-200"
            if whole not in chapters:
                chapters.append(whole)
    return chapters
