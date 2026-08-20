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
import hashlib
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
    is_terminal,
    reconcile,
    summarise,
    summarise_by_category,
)

#: Collections whose records can be placed on the source text. `claims` is not
#: one: a claim reaches the text only through the evidence steps that produced
#: it, and pretending otherwise invents an anchor the package does not hold.
ANCHORED_COLLECTIONS = ("evidence_steps", "observations", "questions", "position_nodes")


def load_segments(source_path: Path) -> list[tuple[int, str]]:
    """Segment the source the way extraction did, so spans are comparable.

    Numbered by position, 1-based, on both branches, because that is the only
    number extraction ever writes down: `segment_locator` stamps `S0007` into
    every anchor and every exclusion id from the segment's position in the
    file. A transcript's own `index` field is a different quantity -- the
    subtitle line the segment starts at (`1, 38, 51, ...`) -- so keying the
    inventory on it addressed sentences no exclusion could ever name, and on
    the 24 published transcripts carrying editor-inserted `##` headings it is
    not even a number (`subtitle-1778084124190-0`) and `int()` raised. Those
    24 are, unhelpfully, 24 of the 25 transcripts that can be sectioned
    without asking a model for boundaries. `source_coverage_view` already
    keys on position; this is the same scheme, not a new one.
    """

    if source_path.suffix == ".json":
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        script = payload.get("script") if isinstance(payload, dict) else payload
        return [
            (position, str((row or {}).get("text") or ""))
            for position, row in enumerate(script or [], start=1)
        ]
    text = source_path.read_text(encoding="utf-8")
    return list(enumerate(markdown_blocks(text), start=1))


def place_fragments(
    package: dict[str, Any],
    segments: list[tuple[int, str]],
    source_sha256: str | None = None,
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
        if len(hits) > 1 and _key_still_binds(fragment, source_sha256):
            # A phrase the manuscript genuinely repeats -- the 太16 母本 states
            # the 該撒利亞腓立比 geography under both 釋經 and 附錄. Choosing
            # between occurrences by similarity is exactly the guess this tool
            # refuses. `paragraph_key` is not a guess *only while the source it
            # was validated against is still the source being read*: extraction
            # checked the excerpt was verbatim in that segment, but across the
            # 24 staged packages only 20% of claimed indices still resolve,
            # because the sources were re-segmented afterwards. So the key is
            # trusted exactly when the fragment's own `source_sha256` matches
            # the file in hand, and never otherwise.
            claimed = str(fragment.get("paragraph_key") or "")
            wanted = int(claimed[1:]) if claimed[1:].isdigit() else None
            hits = [hit for hit in hits if wanted is not None and hit[0] == wanted] or hits
        if len(hits) != 1:
            unplaced.append(fragment_id)
            continue
        segment_index, start = hits[0]
        spans.append(
            AnchoredSpan(owner.get(fragment_id, fragment_id), segment_index, start, start + len(excerpt))
        )
    return spans, unplaced


def _key_still_binds(fragment: dict[str, Any], source_sha256: str | None) -> bool:
    """Whether this fragment's `paragraph_key` was validated against this source."""

    recorded = str(fragment.get("source_sha256") or "")
    return bool(source_sha256) and recorded == source_sha256


def terminal_exclusions(package: dict[str, Any]) -> dict[str, str]:
    """The package's exclusions that count without a human having looked.

    Which is, deliberately, almost none of them: `is_terminal` clears
    `duplicate_of` because a machine can check it, and holds everything else
    until somebody approves. An unapproved exclusion still leaves its sentence
    `unprocessed` — the point is that the reasoning is now on the record and
    reviewable, not that it counts.
    """

    return {
        str(row["sentence_id"]): str(row["exclusion_id"])
        for row in package.get("sentence_exclusions") or []
        if row.get("reason_code")
        and is_terminal(str(row["reason_code"]), approved=bool(row.get("decided_by")))
    }


def run(source_path: Path, package_path: Path, passage: str | None = None) -> dict[str, Any]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    source_id = str(package["source_documents"][0]["source_id"])
    segments = load_segments(source_path)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    inventory = build_inventory(segments, source_id=source_id)
    spans, unplaced = place_fragments(package, segments, source_sha256)

    target = None
    if passage:
        raw = parse_passage_range(passage)
        target = ScriptureRef(
            BOOK_CODE_TO_CHINESE.get(raw.book, raw.book), raw.chapter, raw.start_verse, raw.end_verse
        )

    rows = reconcile(
        inventory, spans,
        exclusions_by_sentence=terminal_exclusions(package),
        target=target, reconciled_against=package_path.name,
    )
    summary = summarise(rows)
    categories = summarise_by_category(inventory, rows, dict(segments))
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
        "exclusions_recorded": len(package.get("sentence_exclusions") or []),
        "exclusions_terminal": len(terminal_exclusions(package)),
        "blocks": summary.blocks,
        # The total is not the score. Headings are represented 0% of the time
        # by design and are a quarter of the sentences, so a change in prose
        # coverage is invisible in the total it is averaged into.
        "by_category": {
            name: {
                "total": category.total,
                "represented": category.represented,
                "excluded": category.excluded,
                "unprocessed": category.unprocessed,
                "represented_pct": (
                    round(100 * category.represented / category.total, 1) if category.total else None
                ),
            }
            for name, category in categories.items()
        },
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
