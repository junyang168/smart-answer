"""按章页面的投影片：模型读保留的逐字稿，写重点；程序核对后存档。

负责人 2026-11-03：原来的幻灯由程序从 Claim 拼——Claim 是论证结构，不是讲课
的呈现，（二）1 开头三分半讲登山宝训的结构，幻灯只剩一条。投影片要像查经 PPT
那样，由模型读逐字稿写要点；**教授讲到、读到的经文必须显示在投影片上。**

模型只给：每张投影片涵盖哪几个保留片段、标题、要点、讲第五章哪几节、读到了哪
些经文（在哪个片段）。时间由程序按片段推算；经文全文由页面按出处去取，模型不
抄经文。

用法::

    backend/.venv/bin/python -m backend.pipeline.chapter_slides mat-5-1-48 --lecture （二）1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.pipeline import chapter_exposition, chapter_material
from backend.pipeline.chapter_material import Chapter

SCHEMA_VERSION = "chapter_slides_v1"

DEFAULT_MODEL = chapter_exposition.DEFAULT_MODEL

MOST_POINTS = 4
FEWEST_POINTS = 2

SYSTEM_PROMPT = """你在為王教授馬太福音講座的錄音配投影片。聽眾一邊聽錄音，一邊看投影片；投影片幫他們抓住教授此刻在講什麼。

使用者給你這一堂課裡解釋馬太福音第五章的逐字稿片段（已編號、按時間排；中間略去的部分不在其中）。請把這些片段分成一張張投影片。

規則：
- 每張投影片涵蓋連續的幾個片段（first 到 last）。全部片段都要被涵蓋，按順序，不重疊、不遺漏。
- 一張講一個話題；教授換話題就換一張。一張通常 1 到 4 個片段。
- title：這一張在講什麼，短（不超過 14 字）。
- points：2 到 4 條要點。短句（每條不超過 24 字），是重點，不是逐字稿的句子。只寫教授講的，不加你自己的解釋或評論。
- scriptures：教授在這張投影片範圍內讀到或引用的每一處經文，一處都不能少——包括馬太福音第五章本身和別卷書，按出現順序。piece 寫是在哪個片段讀到的；ref 寫書卷全名加章節，如「以賽亞書 61:1-2」「馬太福音 5:3」。他只說了書卷和章、沒說節的，從上下文判斷他指的是哪幾節。同一段經文在同一張裡讀了幾次，只列第一次。經文全文由程序另外顯示，要點裡不必抄經文。
- verses：這一張解釋第五章的哪幾節，格式「5:3-10」，幾處用逗號分開。講整篇登山寶訓結構的，寫「5:1-12」。
- 用繁體中文。"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first": {"type": "string"},
                    "last": {"type": "string"},
                    "title": {"type": "string"},
                    "points": {"type": "array", "items": {"type": "string"}},
                    "verses": {"type": "string"},
                    "scriptures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"piece": {"type": "string"}, "ref": {"type": "string"}},
                            "required": ["piece", "ref"],
                        },
                    },
                },
                "required": ["first", "last", "title", "points", "verses", "scriptures"],
            },
        }
    },
    "required": ["slides"],
}


def kept_pieces(decisions_record: dict[str, Any]) -> list[dict[str, Any]]:
    """保留的片段，按时间排。"""

    keep = {d["id"] for d in decisions_record["decisions"] if d.get("keep")}
    return [piece for piece in decisions_record["pieces"] if piece["id"] in keep]


def _clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def user_prompt(lecture: str, decisions_record: dict[str, Any]) -> str:
    position = {piece["id"]: index for index, piece in enumerate(decisions_record["pieces"])}
    lines: list[str] = []
    previous = None
    for piece in kept_pieces(decisions_record):
        if previous is not None and position[piece["id"]] != position[previous["id"]] + 1:
            lines.append("（……中間略去……）\n")
        lines.append(f"[{piece['id']}] {_clock(piece['start'])}–{_clock(piece['end'])}\n{piece['text']}\n")
        previous = piece
    return f"課程：{lecture}\n\n" + "\n".join(lines)


def check(
    slides: list[dict[str, Any]],
    kept: list[dict[str, Any]],
    chapter: Chapter,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """核对模型的投影片。返回（问题、逐字稿里认出却不在投影片上的经文、规范化后的投影片）。

    问题让整份作废：漏片段、乱序、要点条数不对、经节读不出来、经文出处读不出来
    或不在这一张的片段里。

    第二项不作废，列给负责人看：程序认经文出处会认错（「路加福音十六章十六節」
    认出一个 16:1），他报段落结构时说的「五章十三節到七章二十九節」也不是在读
    经文。
    """

    from backend.api.scripture import parse_reference, reference_slugs, spoken_references

    ids = [piece["id"] for piece in kept]
    by_id = {piece["id"]: piece for piece in kept}
    problems: list[str] = []
    unshown: list[str] = []
    normalised: list[dict[str, Any]] = []
    cursor = 0
    for slide in slides:
        title = str(slide.get("title") or "").strip()
        first, last = slide.get("first"), slide.get("last")
        if first not in by_id or last not in by_id:
            problems.append(f"{title}: 片段編號不對（{first}–{last}）")
            continue
        a, b = ids.index(first), ids.index(last)
        if a != cursor:
            expected = ids[cursor] if cursor < len(ids) else "（已完）"
            problems.append(f"{title}: 從 {first} 開始，應從 {expected}")
        if b < a:
            problems.append(f"{title}: last 在 first 之前")
            continue
        cursor = max(cursor, b + 1)
        own = ids[a:b + 1]
        points = [str(p).strip() for p in slide.get("points") or [] if str(p).strip()]
        if not title:
            problems.append(f"{first}: 沒有標題")
        if not FEWEST_POINTS <= len(points) <= MOST_POINTS:
            problems.append(f"{title}: 要點 {len(points)} 條")
        verses = chapter_exposition.parse_verses(str(slide.get("verses") or ""), chapter)
        if verses is None:
            problems.append(f"{title}: 經節讀不出來或不在這一章（{slide.get('verses')}）")
        scriptures = []
        for item in slide.get("scriptures") or []:
            slugs = reference_slugs(str(item.get("ref") or ""))
            if len(slugs) != 1:
                problems.append(f"{title}: 經文出處讀不出來（{item.get('ref')}）")
                continue
            if item.get("piece") not in own:
                problems.append(f"{title}: {item.get('ref')} 的片段 {item.get('piece')} 不在這一張")
                continue
            scriptures.append({"piece": item["piece"], "slug": slugs[0]})
        shown = [parse_reference(item["slug"]) for item in scriptures]
        for piece_id in own:
            for _, slug in spoken_references(by_id[piece_id]["text"]):
                if not _covers_parsed(shown, parse_reference(slug)):
                    unshown.append(f"{title}（{piece_id}）：逐字稿裡有 {slug}")
        normalised.append({
            "pieces": own,
            "title": title,
            "points": points,
            "verses": verses or [],
            "scriptures": scriptures,
        })
    if cursor != len(ids):
        problems.append(f"漏了 {len(ids) - cursor} 個片段")
    return problems, unshown, normalised


def _covers_parsed(shown: list[dict[str, Any]], wanted: dict[str, Any]) -> bool:
    return any(
        item["slug_book"] == wanted["slug_book"]
        and item["chapter"] == wanted["chapter"]
        and item["start"] <= wanted["start"] <= item["end"]
        for item in shown
    )


def record_path(data_base_dir: Path, passage: str, lecture: str) -> Path:
    return Path(data_base_dir) / "chapter_slides" / passage / f"{lecture}.json"


def decisions_sha(data_base_dir: Path, passage: str, lecture: str) -> str | None:
    path = chapter_exposition.record_path(data_base_dir, passage, lecture)
    return chapter_exposition.file_sha256(path) if path.exists() else None


def load_record(data_base_dir: Path, passage: str, lecture: str) -> dict[str, Any] | None:
    """存档的投影片；留不留的决定重跑过，投影片就作废。"""

    path = record_path(data_base_dir, passage, lecture)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != SCHEMA_VERSION:
        return None
    if record.get("decisions_sha256") != decisions_sha(data_base_dir, passage, lecture):
        return None
    return record


def at_of(piece: dict[str, Any], slug: str) -> float:
    """经文在这个片段里第几秒读到：程序认得出出处的，按字的位置推；认不出就从片段开头。"""

    from backend.api.scripture import parse_reference, spoken_references

    wanted = parse_reference(slug)
    length = max(len(piece["text"]), 1)
    for offset, found in spoken_references(piece["text"]):
        if _covers_parsed([wanted], parse_reference(found)):
            return piece["start"] + (piece["end"] - piece["start"]) * offset / length
    return piece["start"]


def decide(
    chapter: Chapter,
    lecture: str,
    *,
    data_base_dir: Path,
    client: Any,
    force: bool = False,
) -> dict[str, Any]:
    decisions_path = chapter_exposition.record_path(data_base_dir, chapter.passage, lecture)
    if not decisions_path.exists():
        return {"lecture": lecture, "status": "no_decisions"}
    if not force and load_record(data_base_dir, chapter.passage, lecture):
        return {"lecture": lecture, "status": "unchanged"}
    decisions_record = json.loads(decisions_path.read_text(encoding="utf-8"))
    kept = kept_pieces(decisions_record)
    prompt = user_prompt(lecture, decisions_record)
    response = client.generate_json(SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA)
    return save(chapter, lecture, data_base_dir, response, prompt, kept, getattr(client, "model", ""))


def save(
    chapter: Chapter,
    lecture: str,
    data_base_dir: Path,
    response: dict[str, Any],
    prompt: str,
    kept: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    problems, unshown, slides = check(response.get("slides") or [], kept, chapter)
    record = {
        "schema_version": SCHEMA_VERSION,
        "passage": chapter.passage,
        "lecture": lecture,
        "decisions_sha256": decisions_sha(data_base_dir, chapter.passage, lecture),
        "model": model,
        "prompt_sha256": hashlib.sha256((SYSTEM_PROMPT + "\n" + prompt).encode("utf-8")).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "slides": slides,
        "unshown_references": unshown,
        "raw_slides": response.get("slides") or [],
    }
    target = record_path(data_base_dir, chapter.passage, lecture)
    target.parent.mkdir(parents=True, exist_ok=True)
    if problems:
        record["problems"] = problems
        rejected = target.with_suffix(".rejected.json")
        rejected.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"lecture": lecture, "status": "rejected", "problems": problems, "file": str(rejected)}
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "lecture": lecture,
        "status": "saved",
        "slides": len(slides),
        "unshown_references": unshown,
        "file": str(target),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("passage", choices=sorted(chapter_material.CHAPTERS))
    parser.add_argument("--lecture", action="append", default=[], help="只跑这几堂，如 （二）1")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient

    chapter = chapter_material.CHAPTERS[args.passage]
    lectures = [
        lecture for lecture in chapter.lectures
        if not args.lecture or any(lecture.endswith(wanted) for wanted in args.lecture)
    ]
    if not lectures:
        parser.error("没有符合的讲道")
    client = ClaudeSubscriptionClient(model=args.model)
    failed = False
    for lecture in lectures:
        report = decide(chapter, lecture, data_base_dir=Path(DATA_BASE_PATH), client=client, force=args.force)
        print(json.dumps(report, ensure_ascii=False))
        failed = failed or report["status"] not in {"saved", "unchanged"}
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
