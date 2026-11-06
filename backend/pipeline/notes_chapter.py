"""教授釋經筆記（母本）的按章页：AI 朗读、按经节归段、同步幻灯。

方案见 `docs/wang-knowledge-platform/30-authoring/matthew_notes_chapter_page_solution.md`。

母本没有录音，其余与讲座按章页相同。做法是把母本写成讲座按章页已经认得的样子，
后面的归段、灰色开关、幻灯、审阅、页面都不另做一套：

- 母本按标题切成小节（片段），每一小节是一个 `piece`；
- 小节的类别读母本自己的 `###` 标题：釋經、神學意義保留；生活應用、附錄默认收
  起（负责人 2026-11-06），在讲座页就是「不保留、标灰」的片段，理由写类别名；
- 每一小节讲哪几节经文由模型决定（`verses`），一份母本一次调用，程序核对；
- 朗读稿由程序从母本转出（去排版、经文出处改念法），不改一个字的内容；
- Azure 合成（`zh-CN-YunzeNeural`，语速 -10%、音高 -2%，与
  `backend/sermon_to_video/core/audio.py` 相同；负责人 2026-11-06 听样本后选定），
  每一句前打一个书签，得到每一句准确的起止时间；希腊文、希伯来文换希腊语、希伯来
  语的声音念（负责人 2026-11-06：要念出来）；表格不念（负责人 2026-11-06）；
- 每一章写一份与 `chapter_exposition` 同格式的存档，幻灯照 `chapter_slides` 写、审。

用法::

    python -m backend.pipeline.notes_chapter mat-5-1-48 --step all
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.pipeline import chapter_exposition, chapter_material
from backend.pipeline.chapter_material import Chapter

#: 来源的显示名，与 `public_repository.NOTES` 相同。
NOTES_SOURCE = "教授釋經筆記"
#: 母本在知识库里的 transcript_id 前缀；按章页把它当作一「堂」。
LECTURE_PREFIX = "notes_manuscript:"
NOTES_DIR = "notes_to_surmon"
#: 母本有按章页的章，格式同 `chapters.json`。讲座按章页一章只有一个来源，母本另放一个文件。
CHAPTERS_FILE = Path("chapter_exposition") / "notes_chapters.json"
#: 每份母本的归段、朗读时间存在这里。
WORK_DIR = Path("chapter_exposition") / "notes"
#: 合成的音频放在讲道录音旁边，由同一个 `/web/video` 提供。
AUDIO_PREFIX = "AI朗讀-"

VOICE = "zh-CN-YunzeNeural"
RATE = "-10%"
PITCH = "-2%"
#: 原文字词换声音念。
ORIGINAL_VOICES = {"el": "el-GR-NestorasNeural", "he": "he-IL-AvriNeural"}

#: `###` 类别标题 → 讲座按章页的类别；None 表示默认收起，值是灰色处显示的说明。
CATEGORIES: dict[str, tuple[bool, str]] = {
    "釋經": (True, "釋經"),
    "神學意義": (True, "神學"),
    "生活應用": (False, "生活應用"),
    "附錄": (False, "附錄"),
}

VERSES_SCHEMA = "notes_verses_v1"
AUDIO_SCHEMA = "notes_audio_v1"
DEFAULT_MODEL = chapter_exposition.DEFAULT_MODEL

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_IMAGE = re.compile(r"^!\[[^\]]*\]\([^)]*\)\s*$")
#: 分隔线（`---`）是排版，不是字。
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,}|```.*)\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_GREEK = r"Ͱ-Ͽἀ-῿"
_HEBREW = r"֐-׿"
_ORIGINAL = re.compile(rf"[{_GREEK}][{_GREEK}̀-ͯ]*(?:[\s,，]+[{_GREEK}][{_GREEK}̀-ͯ]*)*|[{_HEBREW}][{_HEBREW}\s]*[{_HEBREW}]|[{_HEBREW}]")


def category_of(heading: str) -> str | None:
    """`### 釋經（Exegesis）` → 釋經；不是类别标题的返回 None。"""

    name = re.sub(r"[（(].*$", "", heading).strip()
    return name if name in CATEGORIES else None


_ITALIC = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")


_TABLE_RULE = re.compile(r"^\s*:?-{2,}:?\s*$")


def plain(line: str) -> str:
    """一行母本去掉排版符号：引用号、粗体、链接只留文字。内容一个字不动。"""

    line = re.sub(r"^\s*>\s?", "", line)
    line = line.replace("**", "").replace("__", "")
    line = _ITALIC.sub(r"\1", line)
    line = _LINK.sub(r"\1", line)
    return line.strip()


def split_sentences(line: str) -> list[str]:
    """一行切成句子；没有句末标点的一行（经文列表、标题）整行一句。"""

    parts = [m.group().strip() for m in chapter_exposition._SENTENCE.finditer(line)]
    parts = [p for p in parts if p]
    return parts or ([line.strip()] if line.strip() else [])


def parse(text: str) -> list[dict[str, Any]]:
    """母本切成小节。

    每遇到一个标题（类别标题除外）开一个新小节，标题本身是这一小节的第一句；类别
    标题只改变后面各小节的类别，`##` 大节重新从釋經算起。表格、图片不进朗读稿。
    """

    pieces: list[dict[str, Any]] = []
    category = "釋經"
    current: dict[str, Any] | None = None
    table: list[list[str]] | None = None

    def start(heading: str, level: int) -> dict[str, Any]:
        piece = {
            "id": f"N{len(pieces) + 1:03d}",
            "category": category,
            "heading": heading,
            "level": level,
            "sentences": split_sentences(heading),
        }
        pieces.append(piece)
        return piece

    for raw in text.splitlines():
        if not raw.strip():
            table = None
            continue
        stripped = raw.strip()
        if stripped.startswith("|"):
            # 表格不念（负责人 2026-11-06），整张显示在幻灯上：记下它跟在第几句之后。
            cells = [plain(cell) for cell in stripped.strip("|").split("|")]
            if all(_TABLE_RULE.match(cell) for cell in cells):
                continue
            if table is None:
                if current is None:
                    current = start("", 0)
                    current["sentences"] = []
                table = []
                current.setdefault("tables", []).append({"after": len(current["sentences"]), "rows": table})
            table.append(cells)
            continue
        table = None
        match = _HEADING.match(raw)
        if match:
            level, heading = len(match.group(1)), plain(match.group(2))
            if level <= 2:
                category = "釋經"
            found = category_of(heading)
            if found and level >= 3:
                category = found
                current = None
                continue
            current = start(heading, level)
            continue
        if _IMAGE.match(stripped) or _RULE.match(stripped):
            continue  # 图片、分隔线没有字可念
        line = plain(raw)
        if not line:
            continue
        if current is None:
            current = start("", 0)
            current["sentences"] = []
        current["sentences"].extend(split_sentences(line))
    return [piece for piece in pieces if piece["sentences"] or piece.get("tables")]


def plain_text(text: str) -> str:
    """独立于 `parse` 的另一条路：母本去掉排版、类别标题、表格、图片后的全文（不含空白）。"""

    out: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("|") or _IMAGE.match(stripped) or _RULE.match(stripped):
            continue
        match = _HEADING.match(raw)
        if match:
            heading = plain(match.group(2))
            if len(match.group(1)) >= 3 and category_of(heading):
                continue
            out.append(heading)
            continue
        out.append(plain(raw))
    return re.sub(r"\s+", "", "".join(out))


def check_parse(text: str, pieces: list[dict[str, Any]]) -> list[str]:
    """小节里的句子接起来，必须与母本去排版后的全文逐字相同。"""

    joined = re.sub(r"\s+", "", "".join(s for piece in pieces for s in piece["sentences"]))
    expected = plain_text(text)
    # 表格一行不少：母本里每一行表格（去掉 :--- 那一行）都在某一小节的表里。
    rows = sum(len(table["rows"]) for piece in pieces for table in piece.get("tables") or [])
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    expected_rows = sum(
        1 for line in lines
        if not all(_TABLE_RULE.match(plain(cell)) for cell in line.strip("|").split("|"))
    )
    problems = [] if rows == expected_rows else [f"表格 {rows} 行，母本 {expected_rows} 行"]
    if joined == expected:
        return problems
    at = next((i for i, (a, b) in enumerate(zip(joined, expected)) if a != b), min(len(joined), len(expected)))
    return problems + [f"切出的句子與母本不符，第 {at} 字起：{joined[at:at + 30]!r} ≠ {expected[at:at + 30]!r}"]


# ---------------------------------------------------------------------------
# 朗读稿：经文出处改成念法。只换出处，别的字不动。

_NUM = "零一二三四五六七八九"


def chinese(n: int) -> str:
    if n < 10:
        return _NUM[n]
    if n < 20:
        return "十" + (_NUM[n % 10] if n % 10 else "")
    if n < 100:
        return _NUM[n // 10] + "十" + (_NUM[n % 10] if n % 10 else "")
    hundreds, rest = divmod(n, 100)
    tail = "" if rest == 0 else ("零" + _NUM[rest] if rest < 10 else chinese(rest) if rest >= 20 else "一" + chinese(rest))
    return _NUM[hundreds] + "百" + tail


_WRITTEN_REFERENCE = re.compile(
    r"(?P<book>[一-鿿]{1,6}?)?\s*(?P<c1>\d{1,3})\s*[:：]\s*(?P<v1>\d{1,3})"
    r"(?:\s*[-–—~～]\s*(?:(?P<c2>\d{1,3})\s*[:：]\s*)?(?P<v2>\d{1,3}))?"
)


def _book_name(token: str | None) -> tuple[str, str]:
    """书名缩写 → 全名；认不出的不当书名，原样留在句子里。返回（全名，留下的前缀）。"""

    from backend.api.scripture import BOOK_SLUG_TO_NAME, CHINESE_BOOK_ABBREVIATIONS, _resolve_book_slug

    if not token:
        return "", ""
    for cut in range(len(token)):
        candidate = token[cut:]
        slug = _resolve_book_slug(candidate) or CHINESE_BOOK_ABBREVIATIONS.get(candidate)
        if slug and slug in BOOK_SLUG_TO_NAME:
            return BOOK_SLUG_TO_NAME[slug], token[:cut]
    return "", token


def speak(sentence: str) -> str:
    """「太 5:3–10」→「馬太福音五章三節到十節」；「5:13–7:29」→「五章十三節到七章二十九節」。"""

    def spoken(match: re.Match[str]) -> str:
        name, kept = _book_name(match.group("book"))
        c1, v1 = int(match["c1"]), int(match["v1"])
        out = f"{kept}{name}{chinese(c1)}章{chinese(v1)}節"
        if match["v2"]:
            c2 = int(match["c2"]) if match["c2"] else None
            out += "到" + (f"{chinese(c2)}章" if c2 and c2 != c1 else "") + f"{chinese(int(match['v2']))}節"
        return out

    return _WRITTEN_REFERENCE.sub(spoken, sentence).replace("——", "，")


def voice_parts(sentence: str) -> list[tuple[str, str]]:
    """一句的朗读：普通话声音，原文字词换希腊语、希伯来语声音。"""

    text = speak(sentence)
    parts: list[tuple[str, str]] = []
    position = 0
    for match in _ORIGINAL.finditer(text):
        if match.start() > position:
            parts.append(("zh", text[position:match.start()]))
        word = match.group()
        parts.append(("he" if re.search(f"[{_HEBREW}]", word) else "el", word))
        position = match.end()
    if position < len(text):
        parts.append(("zh", text[position:]))
    return [(lang, chunk) for lang, chunk in parts if chunk.strip()]


# ---------------------------------------------------------------------------
# 文件


@dataclass(frozen=True)
class Notes:
    project: str
    path: Path
    title: str

    @property
    def lecture(self) -> str:
        return LECTURE_PREFIX + self.project


def notes(data_base_dir: Path, project: str) -> Notes:
    folder = Path(data_base_dir) / NOTES_DIR / project
    title = project
    meta = folder / "meta.json"
    if meta.is_file():
        title = str(json.loads(meta.read_text(encoding="utf-8")).get("title") or project)
    return Notes(project=project, path=folder / "final.md", title=title)


def project_of(lecture: str) -> str:
    return lecture[len(LECTURE_PREFIX):] if lecture.startswith(LECTURE_PREFIX) else lecture


def work_path(data_base_dir: Path, project: str, name: str) -> Path:
    return Path(data_base_dir) / WORK_DIR / project / name


def audio_path(data_base_dir: Path, project: str) -> Path:
    from backend.pipeline.original_audio_index import MEDIA_DIR_NAME

    return Path(data_base_dir).parent / MEDIA_DIR_NAME / f"{AUDIO_PREFIX}{project}.mp3"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


def load_chapters(data_base_dir: Path) -> dict[str, Chapter]:
    return chapter_material.load_chapters(Path(data_base_dir) / CHAPTERS_FILE)


# ---------------------------------------------------------------------------
# 归段：模型给每一小节写它讲马太福音哪几节

VERSES_PROMPT = """你在幫助整理王教授的馬太福音釋經筆記（已由同工校對）。使用者給你一份筆記，已按標題切成編號小節（N001 起），每節標了類別與全文。

請為每一小節寫出它在解釋馬太福音哪幾節（verses）：
- 寫它實際在講解的那段馬太福音經文，格式「5:21-22」；跨章就寫「16:28-17:8」，不要截在章末；幾處用逗號分開，最主要的放第一個。
- 引別卷書（以賽亞書、雅各書……）來解釋馬太福音某一節的，寫被解釋的那一節馬太福音。
- 講整段或整卷結構的，寫整段，如「5:1-7:29」。
- 小節本身沒有出處（例如只有一個標題、一句過渡、生活應用、附錄），寫它所屬那一段講解的經節：通常就是前後小節在講的那幾節。
- 只提到一處經文作地理、時間的旁證，不算在講解它。

每一小節都必須有，一節一次，不可遺漏、合併或重複。只寫經節，不寫給讀者看的話。"""

VERSES_RESPONSE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pieces": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "verses": {"type": "string"}},
                "required": ["id", "verses"],
            },
        }
    },
    "required": ["pieces"],
}

_RANGE = re.compile(r"(\d+)\s*[:：]\s*(\d+)(?:\s*[-–—~～]\s*(?:(\d+)\s*[:：]\s*)?(\d+))?")
#: 马太福音每章的节数（和合本），核对经节在不在书里。
MATTHEW_VERSES = (25, 23, 17, 25, 48, 34, 29, 34, 38, 42, 30, 50, 58, 36, 39, 28, 27, 35, 30, 34,
                  46, 46, 39, 51, 46, 75, 66, 20)


def parse_ranges(text: str) -> list[tuple[int, int, int, int]] | None:
    """「5:21-22, 16:28-17:8」→ [(5,21,5,22), (16,28,17,8)]；读不出或不在马太福音里返回 None。"""

    out = []
    for match in _RANGE.finditer(str(text or "")):
        c1, v1 = int(match.group(1)), int(match.group(2))
        c2 = int(match.group(3)) if match.group(3) else c1
        v2 = int(match.group(4)) if match.group(4) else v1
        for c, v in ((c1, v1), (c2, v2)):
            if not (1 <= c <= len(MATTHEW_VERSES) and 1 <= v <= MATTHEW_VERSES[c - 1]):
                return None
        if (c2, v2) < (c1, v1):
            return None
        out.append((c1, v1, c2, v2))
    return out or None


def verses_prompt(record: dict[str, Any]) -> str:
    lines = [f"筆記：{record['title']}", ""]
    for piece in record["pieces"]:
        lines.append(f"[{piece['id']}]〔{piece['category']}〕" + (f" {piece['heading']}" if piece["heading"] else ""))
        body = piece["sentences"][1:] if piece["heading"] else piece["sentences"]
        if body:
            lines.append("".join(body))
        lines.append("")
    return "\n".join(lines)


def check_verses(answer: list[dict[str, Any]], pieces: list[dict[str, Any]]) -> tuple[list[str], dict[str, list]]:
    wanted = [piece["id"] for piece in pieces]
    problems: list[str] = []
    seen: dict[str, list] = {}
    for item in answer:
        piece_id = str(item.get("id") or "")
        if piece_id not in wanted:
            problems.append(f"{piece_id or '(空)'}: 不是這份筆記的小節")
        elif piece_id in seen:
            problems.append(f"{piece_id}: 寫了兩次")
        else:
            ranges = parse_ranges(str(item.get("verses") or ""))
            if ranges is None:
                problems.append(f"{piece_id}: 經節讀不出來或不在馬太福音裡：{item.get('verses')!r}")
            else:
                seen[piece_id] = [list(r) for r in ranges]
    missing = [piece_id for piece_id in wanted if piece_id not in seen and not any(p.startswith(piece_id) for p in problems)]
    if missing:
        problems.append(f"漏了 {len(missing)} 個小節：{'、'.join(missing[:10])}")
    return problems, seen


_MATTHEW_CITED = re.compile(r"(?:太|馬太福音)\s*(\d{1,2})\s*[:：]\s*\d")


def neighbour_chapters(pieces: list[dict[str, Any]], verses: dict[str, list]) -> list[str]:
    """小节里写了马太福音另一章的出处，模型给的经节却没有那一章——列给负责人看，不自动改。"""

    out = []
    for piece in pieces:
        cited = {int(c) for c in _MATTHEW_CITED.findall("".join(piece["sentences"]))}
        covered = {c for c1, _, c2, _ in verses.get(piece["id"], []) for c in range(c1, c2 + 1)}
        for chapter in sorted(cited - covered):
            if any(abs(chapter - c) == 1 for c in covered):
                out.append(f"{piece['id']}「{piece['heading'] or piece['sentences'][0][:20]}」寫了太 {chapter} 章，經節只到 {sorted(covered)}")
    return out


def map_verses(data_base_dir: Path, project: str, client: Any, *, force: bool = False) -> dict[str, Any]:
    source = notes(data_base_dir, project)
    text = source.path.read_text(encoding="utf-8")
    pieces = parse(text)
    problems = check_parse(text, pieces)
    if problems:
        return {"project": project, "status": "parse_failed", "problems": problems}
    target = work_path(data_base_dir, project, "verses.json")
    final_sha = chapter_exposition.file_sha256(source.path)
    if not force and target.is_file():
        old = json.loads(target.read_text(encoding="utf-8"))
        if old.get("schema_version") == VERSES_SCHEMA and old.get("final_sha256") == final_sha:
            if old["pieces"] == pieces:
                return {"project": project, "status": "unchanged"}
            # 母本没变、只是切法修过（如分隔线不再当句子）：小节的编号、标题、类别都
            # 没变，模型给的经节照用，不再调用。
            shape = lambda items: [(p["id"], p["heading"], p["category"]) for p in items]
            if shape(old["pieces"]) == shape(pieces):
                old.update({"pieces": pieces, "neighbour_chapters": neighbour_chapters(pieces, old["verses"]),
                            "reparsed_at": _now()})
                _write(target, old)
                return {"project": project, "status": "reparsed"}
    record = {"title": source.title, "pieces": pieces}
    prompt = verses_prompt(record)
    response = client.generate_json(VERSES_PROMPT, prompt, VERSES_RESPONSE)
    problems, verses = check_verses(response.get("pieces") or [], pieces)
    out = {
        "schema_version": VERSES_SCHEMA,
        "project": project,
        "title": source.title,
        "final_sha256": final_sha,
        "model": getattr(client, "model", ""),
        "prompt_sha256": sha256_text(VERSES_PROMPT + "\n" + prompt),
        "created_at": _now(),
        "pieces": pieces,
        "verses": verses,
        "neighbour_chapters": neighbour_chapters(pieces, verses),
        "raw": response.get("pieces") or [],
    }
    if problems:
        out["problems"] = problems
        _write(target.with_suffix(".rejected.json"), out)
        return {"project": project, "status": "rejected", "problems": problems}
    _write(target, out)
    return {"project": project, "status": "saved", "pieces": len(pieces), "neighbour_chapters": out["neighbour_chapters"]}


def load_verses(data_base_dir: Path, project: str) -> dict[str, Any] | None:
    source = notes(data_base_dir, project)
    path = work_path(data_base_dir, project, "verses.json")
    if not path.is_file() or not source.path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != VERSES_SCHEMA:
        return None
    if record.get("final_sha256") != chapter_exposition.file_sha256(source.path):
        return None
    return record


# ---------------------------------------------------------------------------
# AI 朗读

#: 一次请求合成的字数上限。Azure 一次最多十分钟的音频；按每分钟约 220 字，留余量。
CHUNK_CHARS = 1500
PIECE_BREAK_MS = 700
#: 念到表格的位置停下来，让人看幻灯上的表（负责人 2026-11-06：表格不念，停顿）。
#: 三秒起，每行加 1.2 秒，最多 20 秒；Azure 一个 `<break>` 最长 5 秒，接几个。
TABLE_PAUSE_MS = (3000, 1200, 20000)


def table_pause_ms(rows: int) -> int:
    low, per_row, high = TABLE_PAUSE_MS
    return min(high, low + per_row * max(0, rows - 1))


def script_of(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """朗读稿：每一句的编号、所属小节、显示的文字和念的各部分。"""

    out = []
    for piece in pieces:
        tables = {table["after"]: table for table in piece.get("tables") or []}
        for index, text in enumerate(piece["sentences"]):
            out.append({
                "id": f"S{len(out) + 1:04d}",
                "piece": piece["id"],
                "text": text,
                "voice": voice_parts(text),
            })
            table = tables.get(index + 1)
            if table:
                out[-1]["table"] = {"rows": table["rows"], "pause_ms": table_pause_ms(len(table["rows"]))}
    return out


#: Azure 一次请求最多 50 个 `<voice>`（2026-11-06 合成「登山寶訓-八福」时报 1007）。
#: 普通话接连的部分并成一个，换原文才另起；一次请求留余量用到 40 个。
MOST_VOICES = 40


def _runs(sentences: list[dict[str, Any]], breaks_after: set[str]) -> list[tuple[str, str]]:
    """一串句子写成 (声音, SSML 内容)；同一个声音接连的并成一段。"""

    runs: list[list[str]] = []
    for sentence in sentences:
        mark = f'<bookmark mark="{sentence["id"]}"/>'
        for lang, chunk in sentence["voice"]:
            body = mark + html.escape(chunk)
            mark = ""
            if runs and runs[-1][0] == lang:
                runs[-1][1] += body
            else:
                runs.append([lang, body])
        pause = ""
        if sentence.get("table"):
            # 书签记下表格出现的时间，接着停顿。
            pause += f'<bookmark mark="T{sentence["id"]}"/>'
            left = sentence["table"]["pause_ms"]
            while left > 0:
                pause += f'<break time="{min(left, 5000)}ms"/>'
                left -= 5000
        if sentence["id"] in breaks_after:
            pause += f'<break time="{PIECE_BREAK_MS}ms"/>'
        if pause:
            if runs and runs[-1][0] == "zh":
                runs[-1][1] += pause
            else:
                runs.append(["zh", pause])
    return [(lang, body) for lang, body in runs]


def ssml(sentences: list[dict[str, Any]], breaks_after: set[str]) -> str:
    parts = []
    for lang, body in _runs(sentences, breaks_after):
        voice = VOICE if lang == "zh" else ORIGINAL_VOICES[lang]
        pitch = f' pitch="{PITCH}"' if lang == "zh" else ""
        parts.append(f'<voice name="{voice}"><prosody rate="{RATE}"{pitch}>{body}</prosody></voice>')
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">' + "".join(parts) + "</speak>"
    )


def _switches(sentence: dict[str, Any]) -> int:
    """这一句最多多出几个 `<voice>`：每一处原文一进一出。"""

    return 2 * sum(1 for lang, _ in sentence["voice"] if lang != "zh")


def _chunks(script: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按小节凑成一次请求，不在小节中间切开（除非一节就超过上限）。"""

    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = voices = 0
    for index, sentence in enumerate(script):
        new_piece = index == 0 or script[index - 1]["piece"] != sentence["piece"]
        more = _switches(sentence)
        full = size >= CHUNK_CHARS or voices + more > MOST_VOICES // 2
        hard = size >= CHUNK_CHARS * 2 or voices + more > MOST_VOICES
        if current and ((new_piece and full) or hard):
            out.append(current)
            current, size, voices = [], 0, 0
        current.append(sentence)
        size += len(sentence["text"])
        voices += more
    if current:
        out.append(current)
    return out


def synthesize(data_base_dir: Path, project: str, *, force: bool = False) -> dict[str, Any]:
    """整份母本合成一个 mp3，每一句的起止时间按书签取得。"""

    import os

    import azure.cognitiveservices.speech as speechsdk

    verses = load_verses(data_base_dir, project)
    if verses is None:
        return {"project": project, "status": "no_verses"}
    script = script_of(verses["pieces"])
    # 没有表格的母本，SHA 与加表格之前一样，已合成的不用重做。
    script_sha = sha256_text(json.dumps([[s["id"], s["voice"]] + ([s["table"]] if s.get("table") else [])
                                         for s in script], ensure_ascii=False)
                             + VOICE + RATE + PITCH + json.dumps(ORIGINAL_VOICES))
    target = work_path(data_base_dir, project, "audio.json")
    mp3 = audio_path(data_base_dir, project)
    if not force and target.is_file() and mp3.is_file():
        old = json.loads(target.read_text(encoding="utf-8"))
        if old.get("schema_version") == AUDIO_SCHEMA and old.get("script_sha256") == script_sha:
            return {"project": project, "status": "unchanged"}

    config = speechsdk.SpeechConfig(subscription=os.environ["AZURE_SPEECH_KEY"], region=os.environ["AZURE_SPEECH_REGION"])
    config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)
    sample_rate, bytes_per_sample = 24000, 2
    breaks_after = {
        sentence["id"] for index, sentence in enumerate(script)
        if index + 1 == len(script) or script[index + 1]["piece"] != sentence["piece"]
    }
    starts: dict[str, float] = {}
    table_starts: dict[str, float] = {}
    pcm = bytearray()
    for chunk in _chunks(script):
        offset = len(pcm) / (sample_rate * bytes_per_sample)
        marks: dict[str, float] = {}
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        synthesizer.bookmark_reached.connect(
            lambda event, marks=marks: marks.__setitem__(event.text, event.audio_offset / 10_000_000)
        )
        result = synthesizer.speak_ssml_async(ssml(chunk, breaks_after)).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = result.cancellation_details
            raise RuntimeError(f"{project} {chunk[0]['id']}: 合成失败 {details.reason} {details.error_details}")
        # 没有可念的字的一句（只有标点）不报书签：它从下一句开始的地方算起，长度为零。
        chunk_end = result.audio_duration.total_seconds()
        following = chunk_end
        for sentence in reversed(chunk):
            following = marks.get(sentence["id"], following)
            starts[sentence["id"]] = round(offset + following, 3)
            if f"T{sentence['id']}" in marks:
                table_starts[sentence["id"]] = offset + marks[f"T{sentence['id']}"]
        missing = [s["id"] for s in chunk if s["id"] not in marks]
        if len(missing) > max(2, len(chunk) // 20):
            raise RuntimeError(f"{project}: 书签没有回报 {missing[:5]}")
        pcm.extend(result.audio_data)
    duration = len(pcm) / (sample_rate * bytes_per_sample)

    mp3.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".pcm") as raw:
        raw.write(bytes(pcm))
        raw.flush()
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(sample_rate), "-ac", "1",
             "-i", raw.name, "-codec:a", "libmp3lame", "-b:a", "64k", str(mp3)],
            check=True,
        )
    sentences = []
    tables = []
    for index, sentence in enumerate(script):
        end = starts[script[index + 1]["id"]] if index + 1 < len(script) else duration
        if sentence.get("table"):
            at = table_starts.get(sentence["id"], end - sentence["table"]["pause_ms"] / 1000)
            tables.append({"piece": sentence["piece"], "after": sentence["id"], "at": round(at, 3),
                           "until": round(end, 3), "rows": sentence["table"]["rows"]})
        sentences.append({"id": sentence["id"], "piece": sentence["piece"], "start": starts[sentence["id"]],
                          "end": round(end, 3), "text": sentence["text"]})
    _write(target, {
        "schema_version": AUDIO_SCHEMA,
        "project": project,
        "verses_sha256": chapter_exposition.file_sha256(work_path(data_base_dir, project, "verses.json")),
        "script_sha256": script_sha,
        "voice": {"zh": VOICE, "rate": RATE, "pitch": PITCH, **ORIGINAL_VOICES},
        "audio_file": mp3.name,
        "duration": round(duration, 3),
        "created_at": _now(),
        "sentences": sentences,
        **({"tables": tables} if tables else {}),
    })
    return {"project": project, "status": "saved", "seconds": round(duration), "sentences": len(sentences)}


def load_audio(data_base_dir: Path, project: str) -> dict[str, Any] | None:
    path = work_path(data_base_dir, project, "audio.json")
    verses_path = work_path(data_base_dir, project, "verses.json")
    if not path.is_file() or not verses_path.is_file() or not audio_path(data_base_dir, project).is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != AUDIO_SCHEMA:
        return None
    if record.get("verses_sha256") != chapter_exposition.file_sha256(verses_path):
        return None
    return record


# ---------------------------------------------------------------------------
# 一章的存档：与 `chapter_exposition` 同格式，后面的归段、幻灯、页面照用


def _in_chapter(ranges: list[list[int]], number: int) -> str | None:
    """这一小节在第 number 章出现时的经节；不在这一章就是 None。

    只看最主要的一处（模型把它放在第一个），后面几处是交叉参照：讲 5:32 时提到
    19:9，不因此进太 19。跨两章的（16:28-17:8）两章都出现，截到各章里；跨更多章的
    是讲整段结构（5:1-7:29、4:23-9:35），归这一段的第一章（方案 4.2）。
    """

    if not ranges:
        return None
    c1, v1, c2, v2 = ranges[0]
    if number != c1 and not (c2 - c1 == 1 and number == c2):
        return None
    low = v1 if c1 == number else 1
    high = v2 if c2 == number else MATTHEW_VERSES[number - 1]
    return f"mat-{number}-{low}" + (f"-{high}" if high != low else "")


def chapter_record(chapter: Chapter, project: str, data_base_dir: Path) -> dict[str, Any] | None:
    verses = load_verses(data_base_dir, project)
    audio = load_audio(data_base_dir, project)
    if verses is None or audio is None:
        return None
    _, number, _, _ = chapter_material.verse_range(chapter.passage)
    timed: dict[str, list[dict[str, Any]]] = {}
    for sentence in audio["sentences"]:
        timed.setdefault(sentence["piece"], []).append(sentence)
    pieces, decisions = [], []
    for piece in verses["pieces"]:
        slug = _in_chapter(verses["verses"].get(piece["id"], []), number)
        if slug is None:
            continue  # 这一小节讲别的章
        lines = timed.get(piece["id"]) or []
        if not lines:
            continue
        pieces.append({
            "id": piece["id"],
            "start": lines[0]["start"],
            "end": lines[-1]["end"],
            "text": "\n".join(line["text"] for line in lines),
            "sentences": [{"start": l["start"], "end": l["end"], "text": l["text"]} for l in lines],
            **({"tables": tables} if (tables := [
                {"at": t["at"], "until": t["until"], "rows": t["rows"]}
                for t in audio.get("tables") or [] if t["piece"] == piece["id"]
            ]) else {}),
        })
        keep, label = CATEGORIES[piece["category"]]
        decisions.append(
            {"id": piece["id"], "keep": True, "verses": [slug], "kind": label}
            if keep else {"id": piece["id"], "keep": False, "reason": label}
        )
    audio_file = work_path(data_base_dir, project, "audio.json")
    return {
        "schema_version": chapter_exposition.SCHEMA_VERSION,
        "passage": chapter.passage,
        "lecture": LECTURE_PREFIX + project,
        "source_kind": "notes_manuscript",
        "title": verses["title"],
        "transcript_file": str(notes(data_base_dir, project).path),
        # 绑定朗读时间：重新合成过，片段时间就变了，存档跟着重写。
        "transcript_sha256": chapter_exposition.file_sha256(audio_file),
        "created_at": _now(),
        "pieces": pieces,
        "claims_by_piece": {},
        "decisions": decisions,
        "raw_decisions": [],
    }


def write_records(chapter: Chapter, data_base_dir: Path) -> list[dict[str, Any]]:
    reports = []
    for lecture in chapter.lectures:
        project = project_of(lecture)
        record = chapter_record(chapter, project, data_base_dir)
        if record is None:
            reports.append({"lecture": lecture, "status": "not_ready"})
            continue
        if not any(d["keep"] for d in record["decisions"]):
            reports.append({"lecture": lecture, "status": "nothing_kept"})
            continue
        target = chapter_exposition.record_path(data_base_dir, chapter.passage, lecture)
        if target.is_file():
            old = json.loads(target.read_text(encoding="utf-8"))
            if {k: v for k, v in old.items() if k != "created_at"} == {k: v for k, v in record.items() if k != "created_at"}:
                reports.append({"lecture": lecture, "status": "unchanged"})
                continue
        _write(target, record)
        reports.append({"lecture": lecture, "status": "saved", "pieces": len(record["pieces"]),
                        "kept": sum(d["keep"] for d in record["decisions"])})
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("passage")
    parser.add_argument("--step", choices=["verses", "audio", "records", "slides", "review", "revise", "all"], default="all")
    parser.add_argument("--project", action="append", default=[], help="只跑这几份母本")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline import chapter_slides

    load_dotenv()
    data = Path(DATA_BASE_PATH)
    chapters = load_chapters(data)
    if args.passage not in chapters:
        parser.error(f"{args.passage} 不在 {CHAPTERS_FILE}")
    chapter = chapters[args.passage]
    lectures = [l for l in chapter.lectures if not args.project or project_of(l) in args.project]
    client = None
    if args.step in {"verses", "slides", "review", "revise", "all"}:
        from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient

        client = ClaudeSubscriptionClient(model=args.model)
    steps = ["verses", "audio", "records", "slides"] if args.step == "all" else [args.step]
    for step in steps:
        for lecture in lectures:
            project = project_of(lecture)
            if step == "verses":
                report = map_verses(data, project, client, force=args.force)
            elif step == "audio":
                report = synthesize(data, project, force=args.force)
            elif step == "slides":
                report = chapter_slides.decide(chapter, lecture, data_base_dir=data, client=client, force=args.force)
            elif step == "review":
                report = chapter_slides.review(chapter, lecture, data_base_dir=data, client=client)
            elif step == "revise":
                report = chapter_slides.revise(chapter, lecture, data_base_dir=data, client=client)
            else:
                continue
            print(json.dumps({"step": step, **report}, ensure_ascii=False), flush=True)
        if step == "records":
            for report in write_records(Chapter(**{**chapter.__dict__, "lectures": tuple(lectures)}), data):
                print(json.dumps({"step": step, **report}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
