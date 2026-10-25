"""What a draft-first review did, read back from its directories for a person.

The review runner keeps everything a colleague needs to judge a draft --
what each gate and the reviewer flagged, how the author answered, how the
reviewer ruled on a dispute, where each checklist judgement landed -- but in
files no page showed (#453). This module reads them; it changes nothing.

An owner-note round runs in its own directory that continues from the
previous one (`continued_from` in review-run.json) and re-records the round
it continues with the owner's notes added, so the chain is walked oldest
first and a later directory's copy of a round file replaces the earlier one;
a file the later copy does not have is read from the earlier.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GATE_LABELS = {
    "review": "审稿",
    "quote_check": "引文核对",
    "backstage_check": "幕后用语",
    "self_check": "作者自查",
    "blind_read": "盲读",
    "program_check": "程序检查",
    "owner": "负责人",
}
RESPONSE_LABELS = {"fixed": "已改", "disputed": "申辩", "cannot_fix": "改不了"}
RULING_LABELS = {"accepted": "接受申辩", "upheld": "维持原判"}
STATUS_LABELS = {"landed": "已写到", "absent": "未写", "distorted": "写走样", "missing": "漏写"}


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def review_chain(final_dir: Path) -> list[Path]:
    """The review directories behind `final_dir`, oldest first."""

    chain: list[Path] = []
    current: Path | None = final_dir
    while current is not None and current not in chain:
        run = _read(current / "review-run.json")
        if not isinstance(run, dict):
            break
        chain.append(current)
        previous = run.get("continued_from")
        current = Path(previous) if previous else None
    return list(reversed(chain))


class _Round:
    """One round number across the chain: its directories, newest first."""

    def __init__(self) -> None:
        self.dirs: list[Path] = []

    def read(self, name: str) -> Any:
        for directory in self.dirs:
            if (directory / name).is_file():
                return _read(directory / name)
        return None

    @property
    def latest(self) -> Path:
        return self.dirs[0]


def _rounds(chain: list[Path]) -> dict[int, _Round]:
    rounds: dict[int, _Round] = {}
    for review_dir in chain:
        for path in review_dir.glob("round-*"):
            try:
                number = int(path.name.split("-", 1)[1])
            except ValueError:
                continue
            rounds.setdefault(number, _Round()).dirs.insert(0, path)
    return rounds


def _checklist(round_dir: Path) -> list[dict[str, Any]]:
    for attempt in sorted(round_dir.glob("review-attempt-*"), reverse=True):
        items = _read(attempt / "folder" / "checklist.json")
        if isinstance(items, list):
            return items
    return []


def _round(number: int, round_: _Round, next_review: dict[str, Any] | None) -> dict[str, Any]:
    outcome = round_.read("outcome.json") or {}
    review = round_.read("review.json") or {}
    revision = round_.read("revision.json") or {}
    reviewer = {str(f.get("anchor")): f for f in review.get("findings") or []}
    responses = {str(r.get("finding_id")): r for r in revision.get("responses") or []}
    rulings = {str(r.get("finding_id")): r for r in (next_review or {}).get("dispute_rulings") or []}
    findings = []
    for finding in outcome.get("blocking_findings") or []:
        finding_id = str(finding.get("finding_id"))
        detail = reviewer.get(str(finding.get("anchor"))) if finding.get("gate") == "review" else None
        response = responses.get(finding_id)
        ruling = rulings.get(finding_id)
        findings.append({
            "finding_id": finding_id,
            "gate": finding.get("gate"),
            "gate_label": GATE_LABELS.get(str(finding.get("gate")), str(finding.get("gate"))),
            "anchor": finding.get("anchor"),
            # The runner folds the reviewer's required change into the summary.
            "summary": str(finding.get("summary") or "").split("｜required:", 1)[0].strip(),
            "required_change": (detail or {}).get("required_change") or finding.get("required_change"),
            "source_quote": (detail or {}).get("source_quote"),
            "response": response and {
                "kind": response.get("response"),
                "label": RESPONSE_LABELS.get(str(response.get("response")), str(response.get("response"))),
                "note": response.get("note"),
            },
            "ruling": ruling and {
                "kind": ruling.get("ruling"),
                "label": RULING_LABELS.get(str(ruling.get("ruling")), str(ruling.get("ruling"))),
                "note": ruling.get("note"),
            },
        })
    verdict = review.get("verdict") or {}
    return {
        "round": number,
        "passed": bool(outcome.get("passed")),
        "reviewed": bool(review),
        "summary": review.get("summary"),
        "dimensions_below_minimum": verdict.get("dimensions_below_minimum") or [],
        "findings": findings,
        "edit_count": len(revision.get("edits") or []),
    }


def review_report(final_dir: Path) -> dict[str, Any] | None:
    chain = review_chain(final_dir)
    if not chain:
        return None
    by_number = _rounds(chain)
    numbers = sorted(by_number)
    reviews = {n: by_number[n].read("review.json") for n in numbers}
    rounds = [_round(n, by_number[n], reviews.get(numbers[i + 1]) if i + 1 < len(numbers) else None)
              for i, n in enumerate(numbers)]

    items = next((found for n in reversed(numbers) for d in by_number[n].dirs
                  if (found := _checklist(d))), [])
    self_check = {str(i.get("item_id")): i for i in _read(final_dir / "final-self-check.json") or []}
    last_review = next((reviews[n] for n in reversed(numbers) if reviews.get(n)), None) or {}
    reviewer_items = {str(i.get("item_id")): i for i in last_review.get("checklist_review") or []}
    checklist = []
    for item in items:
        item_id = str(item.get("item_id"))
        author = self_check.get(item_id) or {}
        reviewer = reviewer_items.get(item_id) or {}
        checklist.append({
            "item_id": item_id,
            "kind": item.get("kind"),
            "text": item.get("text"),
            "status": author.get("status"),
            "status_label": STATUS_LABELS.get(str(author.get("status")), author.get("status")),
            "anchor": author.get("anchor"),
            "author_reason": author.get("reason"),
            "reviewer_acceptable": reviewer.get("acceptable"),
            "reviewer_reason": reviewer.get("reason"),
        })
    final_run = _read(final_dir / "review-run.json") or {}
    return {
        "status": final_run.get("status"),
        "rounds": rounds,
        "checklist": checklist,
        "dimension_scores": last_review.get("dimension_scores") or [],
    }
