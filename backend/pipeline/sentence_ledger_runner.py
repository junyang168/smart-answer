"""Report the ledger for one source, from a source file and an extraction package.

Read-only: it writes no records and gates nothing. The point of running it
before anything is wired to a gate is to find out how large the residue really
is, because a gate that opens onto a queue nobody can drain gets switched off
within a month.

Fragments are placed by locating their `verbatim_excerpt` in the source text
rather than by trusting `source_segment_index`. Measured across the 24 staged
packages, 100% of excerpts are still verbatim in their source while only 20%
resolve at their claimed index -- the index is what broke, not the text -- and
99.9% are uniquely locatable by exact substring search. No similarity
threshold is involved anywhere: a match that "mostly resembles" a sentence is
the silent loss this tool exists to find.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.pipeline.base_contract_coverage import (
    BOOK_CODE_TO_CHINESE,
    ScriptureRef,
    parse_passage_range,
)
from backend.pipeline.knowledge_source import markdown_blocks
from backend.pipeline.sentence_ledger import (
    AnchoredSpan,
    build_inventory,
    reconcile,
    summarise,
)

#: Collections whose records can be placed on the source text. `claims` is not
#: one: a claim reaches the text only through the evidence steps that produced
#: it, and pretending otherwise invents an anchor the package does not hold.
ANCHORED_COLLECTIONS = ("evidence_steps", "observations", "questions", "position_nodes")


def load_segments(source_path: Path) -> list[tuple[int, str]]:
    """Segment the source the way extraction did, so spans are comparable."""

    if source_path.suffix == ".json":
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        script = payload.get("script") if isinstance(payload, dict) else payload
        return [(int(row["index"]), str(row.get("text") or "")) for row in (script or [])]
    text = source_path.read_text(encoding="utf-8")
    return list(enumerate(markdown_blocks(text), start=1))


def place_fragments(
    package: dict[str, Any], segments: list[tuple[int, str]]
) -> tuple[list[AnchoredSpan], list[str]]:
    """Locate each cited fragment in the source. Returns spans and the unplaceable.

    A fragment found in more than one segment is left unplaced rather than
    guessed at: one ambiguous anchor is not worth a wrong verdict, and the
    caller is told about it instead.
    """

    cited: set[str] = set()
    owner: dict[str, str] = {}
    for collection in ANCHORED_COLLECTIONS:
        for record in package.get(collection, []) or []:
            record_id = next((v for k, v in record.items() if k.endswith("_id")), "?")
            for fragment_id in record.get("source_fragment_ids", []) or []:
                cited.add(fragment_id)
                owner.setdefault(fragment_id, record_id)

    spans: list[AnchoredSpan] = []
    unplaced: list[str] = []
    for fragment in package.get("source_fragments", []) or []:
        fragment_id = fragment.get("fragment_id")
        excerpt = fragment.get("verbatim_excerpt") or ""
        if fragment_id not in cited or not excerpt:
            continue
        hits = [
            (index, text.find(excerpt))
            for index, text in segments
            if excerpt in text
        ]
        if len(hits) != 1:
            unplaced.append(fragment_id)
            continue
        segment_index, start = hits[0]
        spans.append(
            AnchoredSpan(owner.get(fragment_id, fragment_id), segment_index, start, start + len(excerpt))
        )
    return spans, unplaced


def run(source_path: Path, package_path: Path, passage: str | None = None) -> dict[str, Any]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    source_id = str(package["source_documents"][0]["source_id"])
    segments = load_segments(source_path)
    inventory = build_inventory(segments, source_id=source_id)
    spans, unplaced = place_fragments(package, segments)

    target = None
    if passage:
        raw = parse_passage_range(passage)
        target = ScriptureRef(
            BOOK_CODE_TO_CHINESE.get(raw.book, raw.book), raw.chapter, raw.start_verse, raw.end_verse
        )

    rows = reconcile(inventory, spans, target=target, reconciled_against=package_path.name)
    summary = summarise(rows)
    return {
        "source_id": source_id,
        "segments": len(segments),
        "sentences": summary.total,
        "anchored_spans": len(spans),
        "fragments_unplaced": len(unplaced),
        "represented": summary.represented,
        "excluded": summary.excluded,
        "unprocessed": summary.unprocessed,
        "unprocessed_flagged": summary.unprocessed_flagged,
        "blocks": summary.blocks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--passage", help="OSIS range, e.g. Matt.16.13-Matt.16.20")
    args = parser.parse_args(argv)
    print(json.dumps(run(args.source, args.package, args.passage), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
