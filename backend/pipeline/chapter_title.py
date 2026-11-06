"""按章页面的一句话标题：模型读这一章的投影片，写一句教授在这一章讲了什么。

负责人 2026-11-05：书卷页每章一行，不写各来源几篇，写这一章讲什么；这一句要
由程序预先生成，不是手写；要让第一次来的读者看得懂（负责人 2026-11-05）。太 5 原来那一句「八福與彌賽亞的身份，成全律法，與超
越法利賽人的義」是从 2011（二）1、2 的小标题手拼的（`PASSAGE_TITLES`），来历
无人记得。

依据是存档的投影片（每堂的标题和要点，按堂、按讲的次序），不是经文：书卷页上
这一句说的是点进去会听到什么。存档记下每堂投影片文件的 SHA；任何一堂重做过，
这一句就作废，页面退回 `PASSAGE_TITLES`。

用法::

    backend/.venv/bin/python -m backend.pipeline.chapter_title mat-5-1-48
    backend/.venv/bin/python -m backend.pipeline.chapter_title mat-5-1-48 --source 教授釋經筆記

教授釋經筆記的按章页面（AI 朗读）也一样写这一句，跟讲道一致（负责人 2026-11-06），
另存一份，不跟同一章讲道的那一句互相覆盖。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.pipeline import chapter_exposition, chapter_material, chapter_slides
from backend.pipeline.chapter_material import Chapter

SCHEMA_VERSION = "chapter_title_v1"
DEFAULT_MODEL = chapter_exposition.DEFAULT_MODEL
MOST_CHARACTERS = 32

SYSTEM_PROMPT = f"""你在為王教授馬太福音講座的按章頁面寫一句標題，放在書卷目錄上這一章的旁邊。讀者靠這一句決定要不要點進去聽。

使用者給你這一章各堂課的投影片（標題和要點，按講的次序）。請寫一句話，說出教授在這一章講了哪幾件主要的事。

- 一句，不超過 {MOST_CHARACTERS} 字，繁體中文，不加句號。
- 寫給第一次來、沒讀過這一章也沒聽過教授的讀者：用平常的話，一看就懂這一章在講什麼；不用神學術語、原文詞或只有聽過課才懂的說法。
- 按經文次序列出主要的幾個主題，用頓號或逗號分開；主題必須是投影片上教授講的，不要加他沒講的。
- 只寫他講的，不加評價、不加你自己的解釋，不寫「教授認為」之類的話。
- 不寫章號、經文出處。"""

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title"],
    "properties": {"title": {"type": "string"}},
}


NOTES_WORDING = (("馬太福音講座", "馬太福音釋經筆記"), ("各堂課的投影片", "各篇筆記的投影片"))


def _notes(source: str) -> bool:
    from backend.pipeline import notes_chapter

    return source == notes_chapter.NOTES_SOURCE


def system_prompt(source: str = "") -> str:
    prompt = SYSTEM_PROMPT
    if _notes(source):
        for old, new in NOTES_WORDING:
            prompt = prompt.replace(old, new)
    return prompt


def record_path(data_base_dir: Path, passage: str, source: str = "") -> Path:
    name = "chapter-title-notes.json" if _notes(source) else "chapter-title.json"
    return Path(data_base_dir) / "chapter_slides" / passage / name


def chapters(data_base_dir: Path, source: str = "") -> dict[str, Chapter]:
    if _notes(source):
        from backend.pipeline import notes_chapter

        return notes_chapter.load_chapters(Path(data_base_dir))
    return chapter_material.CHAPTERS


def _slides_shas(chapter: Chapter, data_base_dir: Path) -> dict[str, str] | None:
    """每堂投影片文件的 SHA；有一堂没有有效的投影片就是 None。"""

    out = {}
    for lecture in chapter.lectures:
        if chapter_slides.load_record(data_base_dir, chapter.passage, lecture) is None:
            return None
        out[lecture] = chapter_exposition.file_sha256(
            chapter_slides.record_path(data_base_dir, chapter.passage, lecture)
        )
    return out


def user_prompt(chapter: Chapter, data_base_dir: Path) -> str:
    parts = []
    unit = "篇" if _notes(chapter.source) else "堂"
    for number, lecture in enumerate(chapter.lectures, 1):
        record = chapter_slides.load_record(data_base_dir, chapter.passage, lecture) or {}
        lines = [f"第 {number} {unit}"]
        for slide in record.get("slides") or []:
            lines.append(f"- {slide['title']}")
            lines.extend(f"  · {point['text']}" for point in slide.get("points") or [])
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def check(title: str) -> list[str]:
    problems = []
    if not title:
        problems.append("空的")
    if len(title) > MOST_CHARACTERS:
        problems.append(f"{len(title)} 字，超过 {MOST_CHARACTERS}")
    if "\n" in title:
        problems.append("不止一行")
    return problems


def load(data_base_dir: Path, passage: str, source: str = "") -> str | None:
    """存档的一句；哪一堂投影片重做过、或没有存档，就是 None。"""

    chapter = chapters(data_base_dir, source).get(passage)
    path = record_path(data_base_dir, passage, source)
    if chapter is None or not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != SCHEMA_VERSION:
        return None
    if record.get("slides_sha256") != _slides_shas(chapter, data_base_dir):
        return None
    return str(record.get("title") or "") or None


def decide(chapter: Chapter, *, data_base_dir: Path, client: Any, force: bool = False) -> dict[str, Any]:
    shas = _slides_shas(chapter, data_base_dir)
    if shas is None:
        return {"passage": chapter.passage, "status": "no_slides"}
    source = chapter.source if _notes(chapter.source) else ""
    if not force and load(data_base_dir, chapter.passage, source):
        return {"passage": chapter.passage, "status": "unchanged"}
    prompt = user_prompt(chapter, data_base_dir)
    system = system_prompt(source)
    title = str(client.generate_json(system, prompt, RESPONSE_SCHEMA).get("title") or "").strip().rstrip("。")
    problems = check(title)
    if problems:
        # 四个主题的章常写过长（太 12 两次 34 字）；告诉它哪里不对，再写一次，只一次。
        retry = f"{prompt}\n\n你先前寫的「{title}」不能用：{'；'.join(problems)}。請重寫，保留最主要的主題。"
        title = str(client.generate_json(system, retry, RESPONSE_SCHEMA).get("title") or "").strip().rstrip("。")
        problems = check(title)
    if problems:
        return {"passage": chapter.passage, "status": "rejected", "title": title, "problems": problems}
    record = {
        "schema_version": SCHEMA_VERSION,
        "passage": chapter.passage,
        "title": title,
        "slides_sha256": shas,
        "model": getattr(client, "model", ""),
        "prompt_sha256": hashlib.sha256((system + prompt).encode("utf-8")).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = record_path(data_base_dir, chapter.passage, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"passage": chapter.passage, "status": "saved", "title": title}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("passage")
    parser.add_argument("--source", default="", help="教授釋經筆記：写 AI 朗读页面那一句")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient

    found = chapters(Path(DATA_BASE_PATH), args.source)
    if args.passage not in found:
        parser.error(f"没有这一章：{args.passage}（可选：{', '.join(sorted(found))}）")
    report = decide(
        found[args.passage],
        data_base_dir=Path(DATA_BASE_PATH),
        client=ClaudeSubscriptionClient(model=args.model),
        force=args.force,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] in {"saved", "unchanged"} else 1


if __name__ == "__main__":
    sys.exit(main())
