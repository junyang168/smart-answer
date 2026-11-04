"""按章页面的投影片：模型读保留的逐字稿，写重点；程序核对后存档。

负责人 2026-11-03：原来的幻灯由程序从 Claim 拼——Claim 是论证结构，不是讲课
的呈现，（二）1 开头三分半讲登山宝训的结构，幻灯只剩一条。投影片要像查经 PPT
那样，由模型读逐字稿写要点；**教授讲到、读到的经文必须显示在投影片上。**

模型只给：每张投影片从哪一句开始、标题、要点、讲第五章哪几节、读到了哪些经文
（在哪一句）。时间由程序按字的位置推算；经文全文由页面按出处去取，模型不抄经
文。

投影片在句子上换，不在片段上换：第一版按约 300 字的片段换，评审发现一个话题的
尾巴、下一个话题的开头常落在别的投影片上（问题在这一张、答案在下一张）。

要点也标句子（v3）：负责人 2026-11-03 指出「八福都是天國的祝福」那张四条一起
出来，他还在读经文，听众已经读到两分钟后的结论；两条又说同一件事。现在每条要
点在他讲到时才出现，次序就是他讲的次序。

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

SCHEMA_VERSION = "chapter_slides_v3"

DEFAULT_MODEL = chapter_exposition.DEFAULT_MODEL

MOST_POINTS = 4
FEWEST_POINTS = 2

SYSTEM_PROMPT = """你在為王教授馬太福音講座的錄音配投影片。聽眾一邊聽錄音，一邊看投影片；投影片幫他們抓住教授此刻在講什麼、怎樣推論。

使用者給你這一堂課裡解釋馬太福音第五章的逐字稿，每一句有編號（S0001 起），按時間排；中間略去的部分不在其中。請把它分成一張張投影片。

分張：
- 每張投影片從某一句開始（first），到下一張的第一句之前為止。第一張從第一句開始。
- 一張講一個話題；教授換話題，就從新話題的第一句換一張。一個問題和它的答案、一個例子和它說明的道理，放在同一張。
- 一張大約一到五分鐘。要點放不下時多分一張，不要漏掉內容。

要點（points，2 到 4 條，每條不超過 28 字）：
- 每條寫 sentence：教授講到這一點的那一句（他說出這個問題、理由或結論的那一句）。要點在那一句才出現在螢幕上，所以按他講的次序排，sentence 遞增。
- 每條一個意思；同一張裡兩條不可說同一件事（例如「全與天國有關」和「都與天國有關係」只留一條）。
- 寫教授的推論：他提出的問題、他的理由、他的結論、他的勸告。理由和結論比場景、鋪陳重要，不可只留鋪陳而漏掉結論。
- 不要把會眾或他讀的經文內容摘要成要點——經文全文程序會另外顯示；要點寫他從經文看出什麼。
- 不重複前面投影片已經有的要點。
- 只寫教授講的，不加你自己的解釋或評論；他說「很好的證據」就不要寫成「證明」。
- 講見證、故事時，要點要寫出這個故事要說明的道理，以及他給的勸告。
- 聽眾提問時，第一條寫「問：……」，其餘寫他的回答。
- 短句，是重點，不是逐字稿的句子。

title：這一張在講什麼，不超過 14 字，不加引號。

scriptures：這一張裡教授（或會眾）讀出來、或幾乎逐字引用的每一處經文，一處都不能少——包括馬太福音第五章本身和別卷書，按出現順序。
- sentence 寫開始讀它的那一句；ref 寫書卷全名加章節，寫他實際讀的那幾節，如「以賽亞書 61:1-2」。
- 只提到書卷、章節而沒有讀出經文的不列（例如「如果你去看二十三章」）。
- 同一段經文在同一張裡讀了幾次，只列第一次。
- 經文全文由程序顯示，要點裡不必抄經文。

verses：這一張在解釋第五章的哪幾節，格式「5:3-10」，幾處用逗號分開。講整篇登山寶訓結構的，寫「5:1-48」。

用繁體中文。"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first": {"type": "string"},
                    "title": {"type": "string"},
                    "points": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"sentence": {"type": "string"}, "text": {"type": "string"}},
                            "required": ["sentence", "text"],
                        },
                    },
                    "verses": {"type": "string"},
                    "scriptures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"sentence": {"type": "string"}, "ref": {"type": "string"}},
                            "required": ["sentence", "ref"],
                        },
                    },
                },
                "required": ["first", "title", "points", "verses", "scriptures"],
            },
        }
    },
    "required": ["slides"],
}


def kept_pieces(decisions_record: dict[str, Any]) -> list[dict[str, Any]]:
    """保留的片段，按时间排。"""

    keep = {d["id"] for d in decisions_record["decisions"] if d.get("keep")}
    return [piece for piece in decisions_record["pieces"] if piece["id"] in keep]


def sentences(kept: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留片段切成句子，编号 S0001 起；每句的时间按它在片段里的字位置推算。"""

    out: list[dict[str, Any]] = []
    for piece in kept:
        text = piece["text"]
        length = max(len(text), 1)
        span = piece["end"] - piece["start"]
        for match in chapter_exposition._SENTENCE.finditer(text):
            if not match.group().strip():
                continue
            out.append({
                "id": f"S{len(out) + 1:04d}",
                "piece": piece["id"],
                "start": piece["start"] + span * match.start() / length,
                "end": piece["start"] + span * match.end() / length,
                "text": match.group().strip(),
            })
    return out


def _clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def user_prompt(lecture: str, decisions_record: dict[str, Any]) -> str:
    position = {piece["id"]: index for index, piece in enumerate(decisions_record["pieces"])}
    kept = kept_pieces(decisions_record)
    by_piece: dict[str, list[dict[str, Any]]] = {}
    for sentence in sentences(kept):
        by_piece.setdefault(sentence["piece"], []).append(sentence)
    lines: list[str] = []
    previous = None
    for piece in kept:
        if previous is not None and position[piece["id"]] != position[previous["id"]] + 1:
            lines.append("（……中間略去……）")
        lines.append(f"〔{_clock(piece['start'])}〕")
        lines.extend(f"[{s['id']}] {s['text']}" for s in by_piece.get(piece["id"], []))
        previous = piece
    return f"課程：{lecture}\n\n" + "\n".join(lines)


def _covers(shown: list[dict[str, Any]], wanted: dict[str, Any]) -> bool:
    return any(
        item["slug_book"] == wanted["slug_book"]
        and item["chapter"] == wanted["chapter"]
        and item["start"] <= wanted["start"] <= item["end"]
        for item in shown
    )


def _title(text: str) -> str:
    text = text.strip()
    while len(text) >= 2 and text[0] in "「『\"“" and text[-1] in "」』\"”":
        text = text[1:-1].strip()
    return text


def check(
    slides: list[dict[str, Any]],
    kept: list[dict[str, Any]],
    chapter: Chapter,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """核对模型的投影片。返回（问题、逐字稿里认出却不在投影片上的经文、规范化后的投影片）。

    问题让整份作废：第一张不从第一句开始、乱序、要点条数不对、经节读不出来、经
    文出处读不出来或不在这一张的句子里。

    第二项不作废，列给负责人看：程序认经文出处会认错（「路加啊，第十章二十四
    節」认成马太 10:24），他报段落结构时说的「五章十三節到七章二十九節」也不是
    在读经文。
    """

    from backend.api.scripture import parse_reference, reference_slugs, spoken_references

    lines = sentences(kept)
    ids = [s["id"] for s in lines]
    problems: list[str] = []
    unshown: list[str] = []
    starts: list[int] = []
    for slide in slides:
        first = slide.get("first")
        if first not in ids:
            problems.append(f"{slide.get('title')}: 第一句編號不對（{first}）")
            starts.append(-1)
            continue
        starts.append(ids.index(first))
    if not lines:
        return (["沒有保留的句子"] if slides else []), [], []
    if starts and starts[0] != 0:
        problems.append(f"第一張應從 {ids[0]} 開始")
    if any(b <= a for a, b in zip(starts, starts[1:])):
        problems.append("投影片的第一句沒有按次序遞增")
    if problems:
        return problems, unshown, []

    normalised: list[dict[str, Any]] = []
    for index, slide in enumerate(slides):
        a = starts[index]
        b = starts[index + 1] if index + 1 < len(starts) else len(lines)
        own = lines[a:b]
        own_ids = [s["id"] for s in own]
        title = _title(str(slide.get("title") or ""))
        points = []
        for item in slide.get("points") or []:
            text = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
            if not text:
                continue
            if item.get("sentence") not in own_ids:
                problems.append(f"{title}: 要點「{text}」的句子 {item.get('sentence')} 不在這一張")
                continue
            points.append({"sentence": item["sentence"], "text": text})
        if any(own_ids.index(b["sentence"]) < own_ids.index(a["sentence"]) for a, b in zip(points, points[1:])):
            problems.append(f"{title}: 要點沒有按他講的次序")
        if not title:
            problems.append(f"{own_ids[0]}: 沒有標題")
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
            if item.get("sentence") not in own_ids:
                problems.append(f"{title}: {item.get('ref')} 的句子 {item.get('sentence')} 不在這一張")
                continue
            scriptures.append({"sentence": item["sentence"], "slug": slugs[0]})
        shown = [parse_reference(item["slug"]) for item in scriptures]
        for line in own:
            for _, slug in spoken_references(line["text"]):
                if not _covers(shown, parse_reference(slug)):
                    unshown.append(f"{title}（{line['id']}）：逐字稿裡有 {slug}")
        normalised.append({
            "first": own_ids[0],
            "last": own_ids[-1],
            "title": title,
            "points": points,
            "verses": verses or [],
            "scriptures": scriptures,
        })
    return problems, unshown, normalised


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


def placed(decisions_record: dict[str, Any], slides_record: dict[str, Any]) -> tuple[
    dict[str, list[str]], list[dict[str, Any]]
]:
    """片段归哪张投影片的经节、每张投影片从第几秒开始、经文第几秒出现。

    片段按它第一句所在那张投影片的经节归段；投影片跟着它第一句所在的片段归段，
    这样一张投影片和它开始时播的录音总在同一段里。
    """

    lines = sentences(kept_pieces(decisions_record))
    index = {s["id"]: i for i, s in enumerate(lines)}
    piece_verses: dict[str, list[str]] = {}
    timed: list[dict[str, Any]] = []
    for slide in slides_record["slides"]:
        own = lines[index[slide["first"]]:index[slide["last"]] + 1]
        for line in own:
            piece_verses.setdefault(line["piece"], slide["verses"])
        timed.append({
            **slide,
            "piece": own[0]["piece"],
            "at": own[0]["start"],
            "seconds": own[-1]["end"] - own[0]["start"],
            "points": [
                {**item, "at": lines[index[item["sentence"]]]["start"]}
                for item in slide["points"]
            ],
            "scriptures": [
                {**item, "at": lines[index[item["sentence"]]]["start"]}
                for item in slide["scriptures"]
            ],
        })
    return piece_verses, timed


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
