"""負責人指出過的投影片問題，存成案例，每次生成都查（負責人 2026-11-16：「你似乎把以前的問題都忘了」）。

案例在 chapter_slide_cases.json，按講次列出。每條是一個程序判得準的條件：
- point：他講到 at 那一句時，畫面最新的要點要符合 pattern；
- not_before：符合 pattern 的要點不可在 at 之前出現；
- one_slide：from 到 to 要在同一張投影片上；
- note：講到 at 時，這一張要把 span 標在所列的各節上；
- no_title：沒有一張的標題符合 pattern。
查不過的會和其他程序核對一起退回模型。負責人指出新問題時，先在這裡加一條，再修。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CASES = Path(__file__).with_name("chapter_slide_cases.json")


def cases_for(lecture: str, path: Path = CASES) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["lectures"].get(lecture, [])


def _on_screen(slides: list[dict], sentence: str) -> dict | None:
    return next((s for s in reversed(slides) if s["first"] <= sentence), None)


def check_cases(slides: list[dict[str, Any]], cases: list[dict[str, Any]]) -> list[str]:
    problems = []
    for case in cases:
        kind, why = case["kind"], case["why"]
        if kind == "no_title":
            bad = [s["title"] for s in slides if re.search(case["pattern"], s["title"])]
            if bad:
                problems.append(f"負責人指出過：{why}（現在有「{bad[0]}」）")
        elif kind == "one_slide":
            if _on_screen(slides, case["from"]) is not _on_screen(slides, case["to"]):
                problems.append(f"負責人指出過：{why}（{case['from']} 與 {case['to']} 不在同一張）")
        elif kind == "point":
            slide = _on_screen(slides, case["at"])
            shown = [p for p in (slide or {}).get("points", []) if p["sentence"] <= case["at"]]
            if not shown or not re.search(case["pattern"], shown[-1]["text"]):
                current = shown[-1]["text"] if shown else "（沒有要點）"
                problems.append(f"負責人指出過：{why}（{case['at']} 時畫面是「{current}」）")
        elif kind == "not_before":
            early = [(s["title"], p) for s in slides for p in s["points"]
                     if p["sentence"] < case["at"] and re.search(case["pattern"], p["text"])]
            if early:
                problems.append(f"負責人指出過：{why}（「{early[0][1]['text']}」在 {early[0][1]['sentence']} 就出現）")
        elif kind == "note":
            slide = _on_screen(slides, case["at"]) or {}
            marked = {n.get("verse") for n in slide.get("inline_notes", [])
                      if n.get("span") == case["span"] and n["sentence"] <= case["at"]}
            missing = [v for v in case["verses"] if v not in marked]
            if missing:
                problems.append(f"負責人指出過：{why}（第 {missing} 節沒有標出「{case['span']}」）")
        else:
            raise ValueError(f"unknown slide case kind: {kind}")
    return problems
