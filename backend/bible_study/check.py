"""Mechanical checks on a study folder, and the list of unfinished studies.

    python -m backend.bible_study.check status
    python -m backend.bible_study.check <folder> [--stage outline|script]

Only what can be judged mechanically is checked here: that every discussion
conclusion the owner adopted reached the outline and the script, that what was
left for next time stayed out, that every section names a source. Tone, "the
leader's own words", "no sermon quotes": those are writing rules, kept in the
bible-study skill, not here.

Formats (the skill writes them this way):

notes.md, one entry per conclusion:

    ## n12 · 採用 · 「用人」與「僕人」是兩個詞
    - 問：26 節和 27 節的「用人」「僕人」有甚麼分別？
    - 結論：……
    - 依據：carson v2 p433；claim 01H…；太 20:26-27

  status is one of 採用 / 不用 / 下次講 / 待定. 依據 names Carson pages,
  claim ids, scripture, or 負責人 for the owner's own view.

outline.md: a scope section, then one `##` heading per section of the study.
Each section ends with a tag line; the scope names what is left for next time:

    <!-- approved: 2026-09-26 -->          (added when the owner approves)
    # 範圍
    <!-- deferred: n7 n15 -->
    ## 五、座位與服事
    <!-- notes: n12 n13 | sources: carson v2 p433; 太 20:25-28 -->

script.md: the same `##` headings, in the same order. A paragraph that carries
conclusions ends with `<!-- n12 n13; carson v2 p433 -->`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys

from backend.bible_study.paths import data_base_dir, studies_dir

ADOPTED, REJECTED, DEFERRED, OPEN = "採用", "不用", "下次講", "待定"
STATUSES = (ADOPTED, REJECTED, DEFERRED, OPEN)

_NOTE = re.compile(r"^##\s+(n\d+)\s*·\s*(\S+)\s*·\s*(.+?)\s*$", re.MULTILINE)
_BASIS = re.compile(r"^-\s*依據[:：]\s*(.*)$", re.MULTILINE)
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_NOTE_IDS = re.compile(r"\bn\d+\b")
_OUTLINE_TAG = re.compile(r"<!--\s*notes:\s*(?P<notes>[^|]*?)\s*\|\s*sources:\s*(?P<sources>.*?)\s*-->")
_DEFERRED_TAG = re.compile(r"<!--\s*deferred:\s*(.*?)\s*-->")
_APPROVED_TAG = re.compile(r"<!--\s*approved:\s*(\d{4}-\d{2}-\d{2})\s*-->")
_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)


@dataclass
class Note:
    note_id: str
    status: str
    title: str
    basis: str


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _blocks(text: str, heading: re.Pattern[str]) -> list[tuple[re.Match[str], str]]:
    """(heading, body) for each heading match, body running to the next heading."""

    matches = list(heading.finditer(text))
    return [(m, text[m.end() : (matches[i + 1].start() if i + 1 < len(matches) else len(text))]) for i, m in enumerate(matches)]


def parse_notes(text: str, report: Report) -> dict[str, Note]:
    notes: dict[str, Note] = {}
    for match, body in _blocks(text, _NOTE):
        note_id, status, title = match.groups()
        if note_id in notes:
            report.errors.append(f"notes.md: {note_id} appears twice")
        if status not in STATUSES:
            report.errors.append(f"notes.md: {note_id} status {status!r} is not one of {' / '.join(STATUSES)}")
        basis = _BASIS.search(body)
        basis_text = basis.group(1).strip() if basis else ""
        if not basis_text:
            report.errors.append(f"notes.md: {note_id} has no 依據 (Carson page, claim id, scripture, or 負責人)")
        notes[note_id] = Note(note_id, status, title, basis_text)
    return notes


@dataclass
class Section:
    title: str
    notes: list[str]
    sources: str


def parse_outline(text: str, report: Report) -> tuple[list[Section], list[str], str | None]:
    approved = _APPROVED_TAG.search(text)
    deferred_tag = _DEFERRED_TAG.search(text)
    deferred = _NOTE_IDS.findall(deferred_tag.group(1)) if deferred_tag else []
    sections = []
    for match, body in _blocks(text, _SECTION):
        tag = _OUTLINE_TAG.search(body)
        if not tag:
            report.errors.append(f"outline.md: section 「{match.group(1)}」 has no <!-- notes: … | sources: … --> line")
            sections.append(Section(match.group(1), [], ""))
            continue
        sections.append(Section(match.group(1), _NOTE_IDS.findall(tag.group("notes")), tag.group("sources").strip(" -")))
    return sections, deferred, approved.group(1) if approved else None


def check_outline(notes: dict[str, Note], sections: list[Section], deferred: list[str], report: Report) -> None:
    placed = {n for s in sections for n in s.notes}
    for note in notes.values():
        if note.status == OPEN:
            report.errors.append(f"{note.note_id} 「{note.title}」 is still 待定: decide 採用 / 不用 / 下次講 first")
        if note.status == ADOPTED and note.note_id not in placed:
            report.errors.append(f"{note.note_id} 「{note.title}」 is 採用 but no outline section carries it")
        if note.status == DEFERRED and note.note_id not in deferred:
            report.errors.append(f"{note.note_id} 「{note.title}」 is 下次講 but not listed as deferred in 範圍")
    for note_id in sorted(placed | set(deferred)):
        if note_id not in notes:
            report.errors.append(f"outline.md names {note_id}, which is not in notes.md")
        elif notes[note_id].status == REJECTED:
            report.errors.append(f"outline.md places {note_id}, which the owner marked 不用")
    for section in sections:
        if not section.sources and not section.notes:
            report.errors.append(f"outline.md: section 「{section.title}」 names no source and carries no notes")


def check_script(
    notes: dict[str, Note], sections: list[Section], deferred: list[str], script: str, report: Report
) -> None:
    script_sections = _blocks(script, _SECTION)
    titles = [m.group(1) for m, _ in script_sections]
    outline_titles = [s.title for s in sections]
    missing = [t for t in outline_titles if t not in titles]
    for title in missing:
        report.errors.append(f"script.md: outline section 「{title}」 is not written yet")
    present = [t for t in titles if t in outline_titles]
    if present != [t for t in outline_titles if t in present]:
        report.errors.append("script.md: sections are not in the outline's order")

    where: dict[str, list[str]] = {}
    for match, body in script_sections:
        for paragraph in re.split(r"\n\s*\n", body):
            for comment in _COMMENT.findall(paragraph):
                for note_id in _NOTE_IDS.findall(comment):
                    where.setdefault(note_id, []).append(match.group(1))

    assigned = {n: s.title for s in sections for n in s.notes}
    for note in notes.values():
        if note.status == ADOPTED and note.note_id not in where:
            # Only an error once its section is written; unwritten sections are reported above.
            if assigned.get(note.note_id) not in missing:
                report.errors.append(f"{note.note_id} 「{note.title}」 is 採用 but no script paragraph carries it")
        if note.status == DEFERRED and len(where.get(note.note_id, [])) > 1:
            report.errors.append(
                f"{note.note_id} 「{note.title}」 is 下次講: preview it in one paragraph at most, "
                f"it is in {len(where[note.note_id])}"
            )
        if note.status == REJECTED and note.note_id in where:
            report.errors.append(f"{note.note_id} 「{note.title}」 is 不用 but the script carries it")
    for note_id, found_in in where.items():
        if note_id not in notes:
            report.errors.append(f"script.md tags {note_id}, which is not in notes.md")
        elif note_id in assigned and assigned[note_id] not in found_in:
            report.warnings.append(f"{note_id} is in 「{found_in[0]}」, the outline put it in 「{assigned[note_id]}」")


def check(folder: Path, stage: str | None = None) -> Report:
    report = Report()
    notes_path, outline_path, script_path = (folder / n for n in ("notes.md", "outline.md", "script.md"))
    if not notes_path.exists():
        report.errors.append("notes.md does not exist yet")
        return report
    notes = parse_notes(notes_path.read_text(encoding="utf-8"), report)
    stage = stage or ("script" if script_path.exists() else "outline" if outline_path.exists() else "notes")
    if stage == "notes":
        return report
    if not outline_path.exists():
        report.errors.append("outline.md does not exist yet")
        return report
    sections, deferred, approved = parse_outline(outline_path.read_text(encoding="utf-8"), report)
    check_outline(notes, sections, deferred, report)
    if stage == "script":
        if not approved:
            report.errors.append("outline.md is not approved (no <!-- approved: YYYY-MM-DD --> line): write no script before it is")
        if script_path.exists():
            check_script(notes, sections, deferred, script_path.read_text(encoding="utf-8"), report)
    return report


# --- status ------------------------------------------------------------------


def published(folder: Path) -> list[str]:
    """Files already copied to the fellowship folder for this study's date."""

    date = folder.name[:10]
    target = data_base_dir() / "fellowship" / "docs" / date
    return sorted(p.name for p in target.glob("*.pptx")) if target.is_dir() else []


def stage_of(folder: Path) -> str:
    has = {n: (folder / n).exists() for n in ("sources.json", "notes.md", "outline.md", "script.md", "slides.json")}
    if published(folder) and has["slides.json"]:
        return "published"
    if has["slides.json"]:
        return "slides"
    if has["script.md"]:
        outline = (folder / "outline.md").read_text(encoding="utf-8") if has["outline.md"] else ""
        written = len(_blocks((folder / "script.md").read_text(encoding="utf-8"), _SECTION))
        if not outline:
            return f"script ({written} sections, no outline)"
        return f"script ({written}/{len(_blocks(outline, _SECTION))} sections)"
    if has["outline.md"]:
        approved = _APPROVED_TAG.search((folder / "outline.md").read_text(encoding="utf-8"))
        return f"outline approved {approved.group(1)}" if approved else "outline (not approved)"
    if has["notes.md"]:
        count = len(_NOTE.findall((folder / "notes.md").read_text(encoding="utf-8")))
        return f"discussion ({count} notes)"
    return "sources" if has["sources.json"] else "empty"


def status(root: Path | None = None) -> list[tuple[str, str, str]]:
    root = root or studies_dir()
    rows = []
    for folder in sorted(p for p in root.glob("*") if p.is_dir() and not p.name.startswith("_")):
        passage = ""
        sources = folder / "sources.json"
        if sources.exists():
            passage = json.loads(sources.read_text(encoding="utf-8"))["passage"]["text"]
        rows.append((folder.name, passage, stage_of(folder)))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help="`status`, or a study folder (name or path)")
    parser.add_argument("--stage", choices=("notes", "outline", "script"))
    args = parser.parse_args(argv)
    if args.target == "status":
        rows = status()
        if not rows:
            print("no studies yet")
        for name, passage, stage in rows:
            print(f"{name:32s} {passage:18s} {stage}")
        return 0
    folder = Path(args.target)
    if not folder.is_dir():
        folder = studies_dir() / args.target
    if not folder.is_dir():
        print(f"error: no study folder {args.target}", file=sys.stderr)
        return 2
    report = check(folder, args.stage)
    for line in report.errors:
        print(f"ERROR  {line}")
    for line in report.warnings:
        print(f"warn   {line}")
    print("ok" if report.ok else f"{len(report.errors)} error(s)")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
