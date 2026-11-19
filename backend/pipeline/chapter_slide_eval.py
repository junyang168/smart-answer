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

import hashlib
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


def _reading_samples(text: str):
    # An explicit quotation can contain two disjoint verses in one sentence.
    # Keep the full sentence for paraphrase detection and split only quoted omissions.
    yield _bigrams(text)
    for quote in re.findall(r"[「『“]([^」』”]+)[」』”]", text):
        if re.search(r"…+|\.{3,}", quote):
            yield from (_bigrams(part) for part in re.split(r"…+|\.{3,}", quote))


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
        missing = set()
        for pairs in _reading_samples(line["text"]):
            best = max((v for v in pool if _is_reading(pairs, v[1])),
                       key=lambda v: _share(pairs, v[1]), default=None)
            if best and not any(_share(pairs, verse) >= SHOWN_SHARE for _, verse in shown):
                missing.add(best[0])
        for slug in sorted(missing):
            problems.append(f"{slide['title']}（{line['id']}）: 他在念 {slug}，畫面沒有顯示這節經文")
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
            said = [claim.get("stated")] + [sentence for step in claim.get("evidence_steps", [])
                                             for anchor in step.get("anchors", []) for sentence in anchor.get("sentences", [])]
            said = sorted({by_sentence[x] for x in said if x in by_sentence})
            if not said:
                continue
            wanted = {d["sha256"] for d in claim.get("diagrams", [])}
            shown = [d for d in placed.values() if d["sha256"] in wanted]
            # It must be on screen when he starts this argument, not when he sums it up.
            if not shown or min(d["at"] for d in shown) > said[0]:
                problems.append(f"論證圖缺漏或出場太晚：{case['claim']}（他從 {said[0]:.0f} 秒開始講這個論證）")
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


SEMANTIC_SCHEMA_VERSION = "chapter-slide-semantic-eval.v1"
SEMANTIC_CRITERIA = {
    "faithfulness": "標題與要點保留原文的說話者、否定、限定及因果。每個實質意思必須有原文支持。這是口語講課，不是法律條文：只有改變了他的判斷才算不忠實；措辭、語氣或「很多人／有人」這類程度差異不算錯。",
    "coverage": "從原文反查承重理由與結論。比較、對比和首尾呼應要交代兩端如何支持結論。",
    "timing": "過早和過晚都要查。過晚：他已開始講某一點，畫面要等他講完幾句才出現這條要點（例：他在 S0015 說「五章十三節到七章二十九節談門徒的使命」，要點卻綁在兩句後的 S0017），報出應移到哪一句。一條要點合併了他在不同句講的意思時，應拆成兩條，不是等到最後一個意思。過早：標題在換張時立即可見。若結論尚未講出，標題只能用問題或中性主題。要點的全部意思須在錨點已講出。檢查故事結果、限定語及複合要點。按整段論證順序查看每張，尤其是問題之後、答案之前的相鄰投影片。第一張用問題不代表後面的標題可以先說出答案。",
    "scripture": "核對成品實際顯示的經文。每一節讀出或幾乎逐字引用的經文都應顯示，只有提及範圍的經節不作逐字讀出。",
}


OWNER_RULES = [
    "負責人已裁定的條件（owner_rulings）優先於一般規則；不要建議違反它們的改法。",
    "論證圖由程序在他開始講該論證時顯示，不算提前講出答案；負責人 2026-11-18 指出圖等到歸納句才出現是太晚。不要建議把圖移後或拆到別張。",
]


def semantic_packet(slide_record: dict, selection: dict) -> dict:
    """Send the displayed slides, the exact source, the argument context and the owner's rulings together."""
    from backend.pipeline.chapter_slide_cases import cases_for, resolve_cases

    source = chapter_slides.sentences(chapter_slides.kept_pieces(selection))
    context = slide_record.get("claim_context") or {}
    rulings = resolve_cases(cases_for(slide_record["lecture"]), source, partial=context.get("scope") == "subunit")
    slides = slide_record["slides"]
    if (timings := slide_record.get("diagram_timing")) is not None:
        # The reviewer sees the same diagrams and sentence anchors as the player.
        chapter_slides.timed_diagrams(slides, source, context, timings)
        slides = [{**{k: v for k, v in slide.items() if k != "diagram_ref"},
                   **({"diagram_ref": timings[slide["first"]]["sha256"],
                       "diagram_sentence": timings[slide["first"]]["sentence"]}
                      if timings.get(slide["first"], {}).get("sentence") else {})}
                  for slide in slides]
    return {
        "lecture": slide_record["lecture"],
        "criteria": SEMANTIC_CRITERIA,
        "owner_rules": OWNER_RULES,
        "owner_rulings": [{k: case[k] for k in ("kind", "at", "from", "to", "pattern", "why") if k in case}
                          for case in rulings],
        "source": source,
        "claims": [
            {**claim, "diagrams": [{k: v for k, v in d.items() if k != "svg"} for d in claim.get("diagrams", [])]}
            for claim in (slide_record.get("claim_context") or {}).get("claims", [])
        ],
        "slides": slides,
    }


def semantic_slide_sha256(slide_path: Path) -> str:
    record = json.loads(slide_path.read_text())
    # The publisher adds transaction metadata after Eval. Bind everything the
    # evaluator read, including normalized and original slides and context.
    record.pop("publication", None)
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def semantic_eval_problems(slide_path: Path, selection_path: Path, receipt_path: Path) -> list[str]:
    """Read the single per-candidate semantic Eval with no model call."""
    from backend.pipeline.chapter_exposition import file_sha256

    try:
        result = json.loads(receipt_path.read_text())
    except (OSError, ValueError):
        return ["Missing or unreadable semantic Eval for this slide version"]
    if (not isinstance(result, dict) or result.get("schema_version") != SEMANTIC_SCHEMA_VERSION
            or result.get("slides_sha256") != semantic_slide_sha256(slide_path)
            or result.get("selection_sha256") != file_sha256(selection_path)):
        return ["Semantic Eval belongs to a different slide version or source selection"]
    record = json.loads(slide_path.read_text())
    # Owner 2026-11-18: a receipt the slide builder wrote by hand, reusing an older
    # verdict (「沿用…該項未變」), passed a point that appeared 12 s late. Only a
    # recorded run of a different model than the one that wrote the slides counts.
    run = result.get("run")
    if (not isinstance(run, dict) or not run.get("model") or "raw_response" not in run
            or run.get("raw_sha256") != _raw_sha256(run["raw_response"])):
        return ["Semantic Eval was not produced by a recorded model run (chapter_slide_eval --run)"]
    if run["model"] == record.get("model"):
        return [f"Semantic Eval must use a different model than the slides ({record.get('model')})"]
    if "baseline_eval_sha256" in result:
        return ["Semantic Eval must judge every slide in this run, not reuse an earlier verdict"]
    expected = {(s["first"], key) for s in record["slides"] for key in SEMANTIC_CRITERIA}
    seen = set()
    problems = []
    rows = result.get("checks")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return ["Semantic Eval checks must be a list of decisions"]
    for row in rows:
        key = (row.get("slide"), row.get("criterion"))
        if (key not in expected or key in seen or not isinstance(row.get("passed"), bool)
                or not isinstance(row.get("evidence"), str) or not row["evidence"].strip()):
            return ["Semantic Eval has a duplicate, unknown or incomplete check"]
        seen.add(key)
        if not row["passed"]:
            problems.append(f"{key[0]} {key[1]}: {row['evidence']}")
    if seen != expected:
        problems.append("Semantic Eval must cover each criterion for every slide")
    return problems


def _raw_sha256(raw) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


SEMANTIC_SYSTEM = """你是獨立的投影片驗收者，不是寫投影片的人。使用者給你一堂講座的逐字稿（source，每句有編號和秒數）、論證脈絡（claims）、成品投影片（slides）和驗收標準（criteria）。
逐張、逐項按 criteria 判定，每張投影片的每一項都要給一筆：slide 填該張的 first，criterion 填標準名稱，passed 為是否通過，evidence 寫具體依據（句子編號與原文；不通過時寫應怎麼改）。
不要沿用任何先前的判定，全部重新看原文。
先讀 owner_rules 與 owner_rulings：負責人已裁定的條件優先，改法不得違反它們。
輸出有 split_candidates 欄位時，先逐條找可能混合了兩個以上獨立意思的要點，然後才看時間。
按要點本身拆解，保留其範圍和限定，不補寫本來沒顯示的意思。必要限定或同一意思的重述不拆。
對疑似要點只填所在投影片 first、該點目前 sentence、拆成的兩至四條短句 parts。不用替它們找時間，另一步會核對。沒有填空陣列。"""


def with_run(receipt: dict, model: str = "evaluator-model", raw=None) -> dict:
    """Attach the model run record; the runner passes the model's raw response."""
    raw = raw if raw is not None else {"checks": receipt["checks"]}
    return {**receipt, "run": {"model": model, "raw_sha256": _raw_sha256(raw), "raw_response": raw}}


SEMANTIC_KIND = {"faithfulness": "忠實", "coverage": "漏掉重點", "timing": "出現時間", "scripture": "經文"}


def review_from_semantic(receipt: dict, slides_sha256: str) -> dict:
    """The failed semantic checks, in the review form the slide revision step reads."""
    findings = [{"slide": row["slide"], "kind": SEMANTIC_KIND[row["criterion"]], "problem": row["evidence"],
                 "fix": "按上面意見改；意見與原文不符時保留原樣並說明。"}
                for row in receipt["checks"] if not row["passed"]]
    return {"slides_sha256": slides_sha256, "blocking": bool(findings), "findings": findings,
            "source": "semantic-eval", "semantic_eval_raw_sha256": receipt["run"]["raw_sha256"]}


def run_semantic_eval(slide_path: Path, selection_path: Path, receipt_path: Path, model: str) -> dict:
    """One independent model call over the whole candidate; writes the receipt the publisher checks."""
    from backend.pipeline.chapter_exposition import chapter_client, file_sha256

    record = json.loads(slide_path.read_text())
    if record["decisions_sha256"] != file_sha256(selection_path):
        raise ValueError("Selection does not match this slide candidate")
    if model == record.get("model"):
        raise ValueError(f"Use a different model than the slides ({model})")
    packet = semantic_packet(record, json.loads(selection_path.read_text()))
    row = {"type": "object", "additionalProperties": False, "required": ["slide", "criterion", "passed", "evidence"],
           "properties": {"slide": {"type": "string"}, "criterion": {"type": "string", "enum": list(SEMANTIC_CRITERIA)},
                          "passed": {"type": "boolean"}, "evidence": {"type": "string"}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["checks"],
              "properties": {"checks": {"type": "array", "items": row}}}
    subunit = (record.get("claim_context") or {}).get("scope") == "subunit"
    if subunit:
        schema["required"].append("split_candidates")
        schema["properties"]["split_candidates"] = {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["slide", "sentence", "parts"],
            "properties": {"slide": {"type": "string"}, "sentence": {"type": "string"},
                           "parts": {"type": "array", "items": {"type": "string"}}},
        }}
    client = chapter_client(model)
    answer = client.generate_json(SEMANTIC_SYSTEM, json.dumps(packet, ensure_ascii=False), schema)
    raw = getattr(client, "last_raw_response", None) or answer
    checks = answer["checks"]
    split_findings = []
    if subunit and answer["split_candidates"]:
        from backend.pipeline.chapter_slide_point_timing import find_splits
        from backend.pipeline import claim_flash_common, evidence_refresh

        split_findings = find_splits(packet, answer["split_candidates"],
            lambda state, questions: claim_flash_common.jev(
                receipt_path.with_suffix(".point-timing"), evidence_refresh.JevPort(), state, questions),
            model=evidence_refresh.MODEL, max_bytes=claim_flash_common.JEV_BYTES)
        for finding in split_findings:
            checks = [{**check, "passed": False, "evidence": (
                        finding["evidence"] if check["passed"] else
                        f'{check["evidence"]}\n{finding["evidence"]}')}
                      if check["slide"] == finding["slide"] and check["criterion"] == "timing" else check
                      for check in checks]
    receipt = with_run({"schema_version": SEMANTIC_SCHEMA_VERSION, "slides_sha256": semantic_slide_sha256(slide_path),
                        "selection_sha256": file_sha256(selection_path), "checks": checks,
                        **({"point_timing_splits": split_findings} if subunit else {})}, model, raw)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1))
    return receipt


def main():
    import argparse
    from backend.pipeline.chapter_exposition import file_sha256

    parser = argparse.ArgumentParser(description="Export the exact slide candidate and its semantic Eval criteria")
    parser.add_argument("slides", type=Path)
    parser.add_argument("selection", type=Path)
    parser.add_argument("--verify", type=Path, help="Verify a saved semantic Eval without issuing a model call")
    parser.add_argument("--run", type=Path, help="Call the evaluator model and write the semantic Eval here")
    parser.add_argument("--model", default="claude-opus-5-5", help="Evaluator; must differ from the slide model")
    args = parser.parse_args()
    if args.run:
        receipt = run_semantic_eval(args.slides, args.selection, args.run, args.model)
        problems = semantic_eval_problems(args.slides, args.selection, args.run)
        print(json.dumps({"status": "failed" if problems else "passed", "problems": problems,
                          "checks": len(receipt["checks"])}, ensure_ascii=False))
        return int(bool(problems))
    if args.verify:
        problems = semantic_eval_problems(args.slides, args.selection, args.verify)
        print(json.dumps({"status": "failed" if problems else "passed", "problems": problems}, ensure_ascii=False))
        return int(bool(problems))
    record = json.loads(args.slides.read_text())
    if record["decisions_sha256"] != file_sha256(args.selection):
        parser.error("Selection does not match this slide candidate")
    fields = {"schema_version": SEMANTIC_SCHEMA_VERSION, "slides_sha256": semantic_slide_sha256(args.slides),
              "selection_sha256": file_sha256(args.selection)}
    print(json.dumps({**semantic_packet(record, json.loads(args.selection.read_text())),
                      "receipt_fields": fields,
                      "receipt_check_fields": ["slide", "criterion", "passed", "evidence"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
