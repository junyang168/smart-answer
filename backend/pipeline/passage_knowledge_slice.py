"""Build a small, deterministic knowledge slice for one Scripture passage."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MATTHEW_BOOK_PATTERN = r"(?:太|马太(?:福音)?|馬太(?:福音)?|Matt(?:hew)?\.?|Mt\.?)"
REFERENCE_RE = re.compile(
    rf"(?P<book>{MATTHEW_BOOK_PATTERN})\s*(?P<chapter>\d+)\s*[:：.]\s*"
    rf"(?P<start>\d+)(?:\s*[-–—]\s*"
    rf"(?:(?:(?P<end_book>{MATTHEW_BOOK_PATTERN})\s*)?"
    rf"(?P<end_chapter>\d+)\s*[:：.]\s*)?"
    rf"(?P<end>\d+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Passage:
    book: str
    chapter: int
    start_verse: int
    end_verse: int

    @property
    def display(self) -> str:
        end = f"–{self.end_verse}" if self.end_verse != self.start_verse else ""
        return f"{self.book}{self.chapter}:{self.start_verse}{end}"


def _canonical_book(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    return (
        "Matt"
        if compact.lower().startswith("matt")
        or compact.lower().rstrip(".") == "mt"
        or compact in {"太", "马太", "馬太", "马太福音", "馬太福音"}
        else compact
    )


def _match_span(match: re.Match[str]) -> tuple[tuple[int, int], tuple[int, int]]:
    start = (int(match.group("chapter")), int(match.group("start")))
    end_book = match.group("end_book")
    if end_book and _canonical_book(end_book) != _canonical_book(match.group("book")):
        return start, start
    end = (
        int(match.group("end_chapter") or start[0]),
        int(match.group("end") or start[1]),
    )
    return start, end


def reference_overlaps(reference: str, passage: Passage) -> bool:
    passage_start = (passage.chapter, passage.start_verse)
    passage_end = (passage.chapter, passage.end_verse)
    for match in REFERENCE_RE.finditer(reference):
        if _canonical_book(match.group("book")) != _canonical_book(passage.book):
            continue
        start, end = _match_span(match)
        if start <= passage_end and end >= passage_start:
            return True
    return False


def _record_overlaps(record: dict[str, Any], passage: Passage) -> bool:
    return any(reference_overlaps(str(ref), passage) for ref in record.get("scripture_refs") or [])


def _directly_scoped(record: dict[str, Any], passage: Passage) -> bool:
    """Keep bounded references; retain very broad ranges only as context leads."""
    maximum_width = max(8, (passage.end_verse - passage.start_verse + 1) * 3)
    for reference in record.get("scripture_refs") or []:
        for match in REFERENCE_RE.finditer(str(reference)):
            if _canonical_book(match.group("book")) != _canonical_book(passage.book):
                continue
            start, end = _match_span(match)
            passage_start = (passage.chapter, passage.start_verse)
            passage_end = (passage.chapter, passage.end_verse)
            if start <= passage_end and end >= passage_start:
                if start[0] != end[0]:
                    return False
                return end[1] - start[1] + 1 <= maximum_width
    return False


def _fragment_ids(records: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(fragment_id)
        for record in records
        for fragment_id in (
            record.get("source_fragment_ids")
            or ([record.get("source_fragment_id")] if record.get("source_fragment_id") else [])
        )
        if fragment_id
    }


def build_passage_slice(package: dict[str, Any], passage: Passage) -> dict[str, Any]:
    overlapping_claims = [
        row for row in package.get("claims", []) if _record_overlaps(row, passage)
    ]
    claims = [row for row in overlapping_claims if _directly_scoped(row, passage)]
    contextual_claims = [row for row in overlapping_claims if row not in claims]
    claim_ids = {str(row.get("claim_id")) for row in claims}
    observations = [
        row for row in package.get("observations", []) if _record_overlaps(row, passage)
    ]
    questions = [row for row in package.get("questions", []) if _record_overlaps(row, passage)]
    evidence_ids = {
        str(evidence_id)
        for claim in claims
        for evidence_id in claim.get("evidence_step_ids") or []
        if evidence_id
    }
    evidence = [
        {**row, "produced_claim_ids": [
            claim_id for claim_id in row.get("produced_claim_ids") or [] if claim_id in claim_ids
        ]}
        for row in package.get("evidence_steps", [])
        if str(row.get("evidence_step_id")) in evidence_ids
    ]
    position_ids = {
        str(position_id)
        for claim in claims
        for position_id in claim.get("opposed_position_ids") or []
    }
    positions = [
        row for row in package.get("position_nodes", [])
        if str(row.get("position_id")) in position_ids
    ]
    wanted_fragments = _fragment_ids([*evidence, *observations, *questions, *positions])
    fragments = [
        row for row in package.get("source_fragments", [])
        if str(row.get("fragment_id") or row.get("source_fragment_id")) in wanted_fragments
    ]
    source_ids = {str(row.get("source_id")) for row in fragments if row.get("source_id")}
    sources = [
        row for row in package.get("source_documents", [])
        if str(row.get("source_id")) in source_ids
    ]
    relations = [
        row for row in package.get("claim_relations", [])
        if str(row.get("source_claim_id") or row.get("from_id")) in claim_ids
        and str(row.get("target_claim_id") or row.get("to_id")) in claim_ids
    ]

    covered_verses = {
        verse
        for verse in range(passage.start_verse, passage.end_verse + 1)
        if any(
            reference_overlaps(ref, Passage(passage.book, passage.chapter, verse, verse))
            for row in [*claims, *observations]
            for ref in row.get("scripture_refs") or []
        )
    }
    missing_verses = sorted(
        set(range(passage.start_verse, passage.end_verse + 1)) - covered_verses
    )
    eligible_evidence = [
        row for row in evidence
        if row.get("support_eligibility") in {"eligible", "eligible_candidate", "eligible_with_label"}
    ]
    return {
        "schema_version": "wang_passage_knowledge_slice_v1",
        "package_id": f"{package.get('package_id', 'knowledge')}-{passage.display}-slice",
        "source_documents": sources,
        "source_fragments": fragments,
        "observations": observations,
        "questions": questions,
        "claims": claims,
        "contextual_claim_leads": [
            {
                "claim_id": row.get("claim_id"),
                "title": row.get("title") or row.get("statement"),
                "scripture_refs": row.get("scripture_refs") or [],
                "reason": "scripture_reference_range_is_broader_than_fast_path_scope",
            }
            for row in contextual_claims
        ],
        "evidence_steps": evidence,
        "position_nodes": positions,
        "claim_relations": relations,
        "passage_slice": {
            "passage": passage.display,
            "selection_policy": "structured_scripture_reference_overlap",
            "covered_verses": sorted(covered_verses),
            "missing_verses": missing_verses,
            "requires_model_extraction": bool(missing_verses or not eligible_evidence),
        },
        "summary": {
            "source_documents": len(sources),
            "source_fragments": len(fragments),
            "observations": len(observations),
            "questions": len(questions),
            "claims": len(claims),
            "contextual_claim_leads": len(contextual_claims),
            "evidence_steps": len(evidence),
            "eligible_evidence_steps": len(eligible_evidence),
            "claim_relations": len(relations),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--book", default="Matt")
    parser.add_argument("--chapter", required=True, type=int)
    parser.add_argument("--start", required=True, type=int)
    parser.add_argument("--end", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    package = json.loads(args.input.read_text(encoding="utf-8"))
    result = build_passage_slice(
        package,
        Passage(args.book, args.chapter, args.start, args.end),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result["summary"], **result["passage_slice"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
