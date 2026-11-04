"""一堂课里哪些片段是这一章的释经：模型逐片段决定，程序核对后存档。

方案见 `docs/wang-knowledge-platform/30-authoring/matthew_chapter_exposition_page_solution.md`
第 4.2、4.3 节。负责人 2026-11-03：

- 主张覆盖教授讲的所有内容，但不是所有主张都是释经。释经、神学有通行的定义，
  由模型根据主张和逐字稿原文按定义判断，不用经文引用、主张关系或标题去推。
- **逐字稿的每个片段都必须决定留不留，否则整份输出作废。**
- 不留的部分在页面上标灰，仍可播放、可看逐字稿。

模型只做决定，不写给读者看的话；时间由程序按字数推算，模型不给时间。

用法::

    backend/.venv/bin/python -m backend.pipeline.chapter_exposition mat-5-1-48
    backend/.venv/bin/python -m backend.pipeline.chapter_exposition mat-5-1-48 --lecture （二）3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.pipeline import chapter_material
from backend.pipeline.chapter_material import Chapter

SCHEMA_VERSION = "chapter_exposition_decisions_v1"

#: 一个片段约多少字。
#:
#: 逐字稿的段落有的很长：（二）1 五十四分钟只有 9 个带时间的段落，最长一段 13.7
#: 分钟，前半是释经、后半是见证。按段落决定就只能整段留或整段丢。300 字约一分
#: 半；太 5 六堂切成 313 个片段，（二）3 讲脸部过敏的两分半因此能单独丢掉。
PIECE_CHARS = 300

#: 少于这么多字的段落没有内容可判断（逐字稿的空洞或残字）。
MIN_PIECE_CHARS = 5

KINDS = ("釋經", "神學", "應用")

DEFAULT_MODEL = "claude-opus-5-5"

_SENTENCE = re.compile(r"[^。？！?!]+[。？！?!」』）)]*|[。？！?!」』）)]+")
_VERSE = re.compile(r"(\d+)\s*[:：]\s*(\d+)(?:\s*[-–—~～]\s*(?:(\d+)\s*[:：]\s*)?(\d+))?")


SYSTEM_PROMPT = """你在幫助整理王教授在 NYSC 馬太福音系列講座中對馬太福音某一章的釋經。使用者會給你：這一章的經文、一堂課的逐字稿（已切成編號片段，每段帶時間）、落在各片段裡的主張（Claim）。

任務：逐一決定每個片段是否保留——保留的是解釋這一章某段經文的片段，並標出對應的經節。

判斷標準（按通行定義）：
- 釋經：解釋經文本身的含義——字詞、文法、原文、上下文、歷史與舊約背景、平行經文、經文結構。引用別卷書只要目的是解釋這節經文就算（如用雅各書說明太 5:28 的「慾念」）。
- 神學：從這段經文推出的教義或原則。必須是從這一章的經文推出來的；與這一章經文無關的神學討論不算。
- 應用：從這段經文推出的、直接相關的生活應用。個人見證若是直接用來說明這節經文的應用，也算應用。
- 不保留：與這一章經文無關的見證、問答、其他章節的釋經、開場禱告、行政事項。

規則：
- 每一個片段都必須有一個決定，不可遺漏、不可合併、不可重複。少一個片段，整份輸出作廢。
- 保留的片段：keep 為 true，verses 寫經節，格式為「章:節」或「章:節-節」，幾處用逗號分開，例如「5:27-28」或「5:3, 5:10」；kind 為「釋經」「神學」「應用」之一。
- 不保留的片段：keep 為 false，reason 寫一句話說明為什麼不保留。
- decisions 按片段編號順序排列。"""


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "keep": {"type": "boolean"},
                    "verses": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "reason": {"type": "string"},
                },
                "required": ["id", "keep"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


def cut(sermon: dict[str, Any]) -> list[dict[str, Any]]:
    """把一堂课的逐字稿在句末切成约 `PIECE_CHARS` 字的片段，每片带起止秒数。

    时间沿用原声页的字位置插值（`original_audio_index._timeline`），与主张的录音
    时间同一把尺。
    """

    from backend.pipeline.original_audio_index import STRIKETHROUGH, _at, _timeline

    anchors = _timeline(sermon)
    pieces: list[dict[str, Any]] = []
    position = 0
    for segment in sermon.get("segments") or []:
        text = STRIKETHROUGH.sub("", str(segment.get("text") or ""))
        if len(text.strip()) >= MIN_PIECE_CHARS and anchors:
            # 一句一句往里放，再放一句就超过 `PIECE_CHARS` 时另起一片。
            spans: list[tuple[int, int]] = []
            current: tuple[int, int] | None = None
            for match in _SENTENCE.finditer(text):
                if current is not None and match.end() - current[0] > PIECE_CHARS:
                    spans.append(current)
                    current = None
                current = (current[0] if current else match.start(), match.end())
            if current is not None:
                spans.append(current)
            for begin, end in spans:
                body = text[begin:end].strip()
                if len(body) < 2:
                    continue
                start = _at(anchors, position + begin)
                finish = _at(anchors, position + end)
                if start is None or finish is None:
                    continue
                pieces.append({"start": round(start, 2), "end": round(finish, 2), "text": body})
        position += len(text)
    for number, piece in enumerate(pieces, start=1):
        piece["id"] = f"U{number:03d}"
    return pieces


def place_claims(
    pieces: list[dict[str, Any]],
    claim_starts: dict[str, list[float]],
) -> dict[str, list[str]]:
    """每条主张落在哪个片段：取它在这一堂里证据时间的中位数。"""

    out: dict[str, list[str]] = {piece["id"]: [] for piece in pieces}
    if not pieces:
        return out
    for claim_id, starts in claim_starts.items():
        if not starts:
            continue
        at = sorted(starts)[len(starts) // 2]
        piece = next(
            (p for p in pieces if p["start"] <= at < p["end"]),
            min(pieces, key=lambda p: min(abs(p["start"] - at), abs(p["end"] - at))),
        )
        out[piece["id"]].append(claim_id)
    return out


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def user_prompt(
    chapter_label: str,
    verse_texts: dict[str, str],
    lecture: str,
    pieces: list[dict[str, Any]],
    claims_by_piece: dict[str, list[str]],
    claims: dict[str, dict[str, Any]],
) -> str:
    lines = [f"# {chapter_label}（和合本）"]
    for slug, text in sorted(verse_texts.items(), key=lambda item: int(item[0].split("-")[-1])):
        lines.append(f"{slug.split('-')[-1]} {text}")
    lines += ["", f"# 逐字稿：{lecture}，切成 {len(pieces)} 個片段"]
    for piece in pieces:
        lines.append(f"\n[{piece['id']}] {_clock(piece['start'])}–{_clock(piece['end'])}\n{piece['text']}")
        for claim_id in claims_by_piece.get(piece["id"], []):
            claim = claims.get(claim_id) or {}
            refs = "、".join(str(ref) for ref in claim.get("scripture_refs") or []) or "無"
            lines.append(f"  主張：{str(claim.get('statement') or '').strip()}（引用：{refs}）")
    return "\n".join(lines)


def parse_verses(text: str, chapter: Chapter) -> list[str] | None:
    """「5:27-28」「5:3, 5:10」「5:21-22; 5:27-28」→ 经文 slug。

    有一处落在这一章之外，或一处都读不出来，返回 None。
    """

    book, number, low, high = chapter_material.verse_range(chapter.passage)
    out: list[str] = []
    for match in _VERSE.finditer(str(text or "")):
        first_chapter, first, second_chapter, last = match.groups()
        if int(first_chapter) != number or (second_chapter and int(second_chapter) != number):
            return None
        start = int(first)
        end = int(last) if last else start
        if not (low <= start <= end <= high):
            return None
        out.append(f"{book}-{number}-{start}" + (f"-{end}" if end != start else ""))
    return out or None


def check(
    decisions: Iterable[dict[str, Any]],
    pieces: list[dict[str, Any]],
    chapter: Chapter,
) -> tuple[list[str], list[dict[str, Any]]]:
    """模型的决定合不合规矩。返回（问题，整理后的决定）；有问题就不能用。"""

    wanted = [piece["id"] for piece in pieces]
    problems: list[str] = []
    seen: dict[str, dict[str, Any]] = {}
    for raw in decisions:
        piece_id = str(raw.get("id") or "")
        if piece_id not in wanted:
            problems.append(f"{piece_id or '(空)'}: 不是這一堂的片段")
            continue
        if piece_id in seen:
            problems.append(f"{piece_id}: 決定了兩次")
            continue
        if raw.get("keep") is True:
            verses = parse_verses(str(raw.get("verses") or ""), chapter)
            kind = str(raw.get("kind") or "")
            if verses is None:
                problems.append(f"{piece_id}: 經節讀不出來或不在這一章：{raw.get('verses')!r}")
                continue
            if kind not in KINDS:
                problems.append(f"{piece_id}: 保留的片段沒有標釋經、神學或應用：{kind!r}")
                continue
            seen[piece_id] = {"id": piece_id, "keep": True, "verses": verses, "kind": kind}
        elif raw.get("keep") is False:
            reason = str(raw.get("reason") or "").strip()
            if not reason:
                problems.append(f"{piece_id}: 不保留卻沒有理由")
                continue
            seen[piece_id] = {"id": piece_id, "keep": False, "reason": reason}
        else:
            problems.append(f"{piece_id}: 沒有說留不留")
    missing = [piece_id for piece_id in wanted if piece_id not in seen and not any(
        problem.startswith(f"{piece_id}:") for problem in problems
    )]
    if missing:
        problems.append(f"漏了 {len(missing)} 個片段：{'、'.join(missing[:10])}")
    return problems, [seen[piece_id] for piece_id in wanted if piece_id in seen]


def record_path(data_base_dir: Path, passage: str, lecture: str) -> Path:
    return Path(data_base_dir) / "chapter_exposition" / passage / f"{lecture}.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_record(
    data_base_dir: Path,
    passage: str,
    lecture: str,
    transcript_sha256: str | None,
) -> dict[str, Any] | None:
    """这一堂存档的决定。逐字稿变了（SHA 不符）就不用——片段的时间对不上了。"""

    path = record_path(data_base_dir, passage, lecture)
    if not path.is_file() or not transcript_sha256:
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema_version") != SCHEMA_VERSION:
        return None
    if record.get("transcript_sha256") != transcript_sha256:
        return None
    return record


def lecture_rows(chapter: Chapter, record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """一堂课在各段里的一行：保留的片段，以及夹在中间的灰色片段。

    - 保留的片段按经节起点归段；同一段里接连的片段并成一段录音。
    - 不保留的片段，接连的并成一块灰色，跟在它前面那个保留的片段所在的段里——
      听的人在原来的位置看见他岔开去讲了什么；一堂课开头就不保留的，跟在后面
      第一个保留的片段那一段。一整堂都不保留的，不出现。
    - 每个片段只出现一次。
    """

    decisions = {item["id"]: item for item in record.get("decisions") or []}
    pieces = [piece for piece in record.get("pieces") or [] if piece["id"] in decisions]

    # 先按片段次序标好每个片段归哪一段。
    owner: list[str | None] = []
    for piece in pieces:
        decision = decisions[piece["id"]]
        owner.append(
            chapter_material.section_of(chapter, decision["verses"][0]) if decision["keep"] else None
        )
    kept_sections = [section for section in owner if section]
    if not kept_sections:
        return {}

    rows: dict[str, dict[str, Any]] = {}

    def row(section: str) -> dict[str, Any]:
        return rows.setdefault(section, {"stretches": [], "pieces": []})

    previous_section: str | None = None
    index = 0
    while index < len(pieces):
        piece = pieces[index]
        decision = decisions[piece["id"]]
        if decision["keep"]:
            section = owner[index]
            target = row(section)
            last = target["stretches"][-1] if target["stretches"] else None
            if (
                last is not None
                and not last.get("left_out")
                and last.get("_until") == index
            ):
                last["end"] = piece["end"]
                last["_until"] = index + 1
            else:
                target["stretches"].append(
                    {"start": piece["start"], "end": piece["end"], "_until": index + 1}
                )
            target["pieces"].append({
                "start": piece["start"],
                "end": piece["end"],
                "verse": decision["verses"][0],
                "kind": decision["kind"],
            })
            previous_section = section
            index += 1
            continue
        run_end = index
        while run_end < len(pieces) and not decisions[pieces[run_end]["id"]]["keep"]:
            run_end += 1
        section = previous_section or next(s for s in owner[run_end:] if s)
        reasons: list[str] = []
        for item in pieces[index:run_end]:
            reason = decisions[item["id"]]["reason"]
            if reason not in reasons:
                reasons.append(reason)
        row(section)["stretches"].append({
            "start": pieces[index]["start"],
            "end": pieces[run_end - 1]["end"],
            "left_out": True,
            "reasons": reasons,
            # 每个片段自己的理由：一块灰色可能长达半小时，播到哪里说哪里的理由。
            "parts": [
                {"start": item["start"], "end": item["end"], "reason": decisions[item["id"]]["reason"]}
                for item in pieces[index:run_end]
            ],
            "text": "\n\n".join(item["text"] for item in pieces[index:run_end]),
        })
        index = run_end

    for value in rows.values():
        value["stretches"].sort(key=lambda item: item["start"])
        for stretch in value["stretches"]:
            stretch.pop("_until", None)
    return rows


def _claim_starts(
    claims: dict[str, dict[str, Any]],
    steps: dict[str, dict[str, Any]],
    fragments: dict[str, dict[str, Any]],
    sermon: dict[str, Any],
    source_id: str,
    lecture: str,
) -> dict[str, list[float]]:
    from backend.pipeline.original_audio_index import segment_time

    out: dict[str, list[float]] = {}
    for claim_id, claim in claims.items():
        if str(claim.get("review_status") or "") == "superseded":
            continue
        if not any(
            str(occurrence.get("transcript_id") or "") == lecture
            for occurrence in claim.get("occurrences") or []
        ):
            continue
        for step_id in claim.get("eligible_evidence_step_ids") or claim.get("evidence_step_ids") or []:
            step = steps.get(str(step_id)) or {}
            ids = ([step["source_fragment_id"]] if step.get("source_fragment_id") else [])
            ids += list(step.get("source_fragment_ids") or [])
            for fragment_id in ids:
                fragment = fragments.get(str(fragment_id))
                if not fragment or str(fragment.get("source_id") or "") != source_id:
                    continue
                span = segment_time(sermon, fragment)
                if span:
                    out.setdefault(claim_id, []).append(span[0])
    return out


def decide(
    chapter: Chapter,
    lecture: str,
    *,
    store: Any,
    data_base_dir: Path,
    client: Any,
    force: bool = False,
) -> dict[str, Any]:
    """跑一堂课：切片、问模型、核对、存档。返回一份报告。"""

    from backend.api.scripture import format_chinese_reference
    from backend.pipeline.original_audio_index import Sermons, verse_texts

    def by_id(collection: str, key: str) -> dict[str, dict[str, Any]]:
        return {str(row.get(key)): row for row in store.list_records(collection) if row.get(key)}

    documents = by_id("source_documents", "source_id")
    source_id, document = next(
        ((sid, doc) for sid, doc in documents.items() if str(doc.get("transcript_id") or "") == lecture),
        (None, None),
    )
    if document is None:
        return {"lecture": lecture, "status": "no_source_document"}
    sermons = Sermons(data_base_dir)
    sermon = sermons.load(source_id, document)
    path = sermons.transcript_path(document)
    if not sermon or path is None:
        return {"lecture": lecture, "status": "no_transcript"}
    sha = file_sha256(path)
    if not force and load_record(data_base_dir, chapter.passage, lecture, sha):
        return {"lecture": lecture, "status": "unchanged"}

    claims = by_id("claims", "claim_id")
    pieces = cut(sermon)
    starts = _claim_starts(
        claims,
        by_id("evidence_steps", "evidence_step_id"),
        by_id("source_fragments", "fragment_id"),
        sermon,
        source_id,
        lecture,
    )
    placed = place_claims(pieces, starts)
    book, number, low, high = chapter_material.verse_range(chapter.passage)
    prompt = user_prompt(
        format_chinese_reference(chapter.passage),
        verse_texts(data_base_dir, book, number, low, high),
        lecture,
        pieces,
        placed,
        claims,
    )
    response = client.generate_json(SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA)
    problems, decisions = check(response.get("decisions") or [], pieces, chapter)
    record = {
        "schema_version": SCHEMA_VERSION,
        "passage": chapter.passage,
        "lecture": lecture,
        "source_id": source_id,
        "transcript_file": path.name,
        "transcript_sha256": sha,
        "piece_chars": PIECE_CHARS,
        "model": getattr(client, "model", ""),
        "reasoning_effort": getattr(client, "reasoning_effort", ""),
        "prompt_sha256": hashlib.sha256((SYSTEM_PROMPT + "\n" + prompt).encode("utf-8")).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pieces": pieces,
        "claims_by_piece": placed,
        "decisions": decisions,
        "raw_decisions": response.get("decisions") or [],
    }
    target = record_path(data_base_dir, chapter.passage, lecture)
    target.parent.mkdir(parents=True, exist_ok=True)
    if problems:
        # 不改写模型的回答，也不重试：整堂不上页面，报给负责人。
        record["problems"] = problems
        rejected = target.with_suffix(".rejected.json")
        rejected.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"lecture": lecture, "status": "rejected", "problems": problems, "file": str(rejected)}
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    kept = sum(
        piece["end"] - piece["start"]
        for piece, decision in zip(pieces, decisions)
        if decision["keep"]
    )
    return {
        "lecture": lecture,
        "status": "saved",
        "pieces": len(pieces),
        "kept_minutes": round(kept / 60, 1),
        "file": str(target),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("passage", choices=sorted(chapter_material.CHAPTERS))
    parser.add_argument(
        "--lecture",
        action="append",
        default=[],
        help="只跑这几堂；写全名或结尾，如 （二）3",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true", help="逐字稿没变也重跑")
    args = parser.parse_args(argv)

    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient

    chapter = chapter_material.CHAPTERS[args.passage]
    lectures = [
        lecture for lecture in chapter.lectures
        if not args.lecture or any(lecture.endswith(wanted) for wanted in args.lecture)
    ]
    if not lectures:
        parser.error("没有符合的讲道")
    store = PostgresKnowledgeStore()
    client = ClaudeSubscriptionClient(model=args.model)
    failed = False
    for lecture in lectures:
        report = decide(
            chapter, lecture, store=store, data_base_dir=Path(DATA_BASE_PATH),
            client=client, force=args.force,
        )
        print(json.dumps(report, ensure_ascii=False))
        failed = failed or report["status"] not in {"saved", "unchanged"}
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
