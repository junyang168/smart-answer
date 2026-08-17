"""Measure how much of the professor's observation actually carries argument.

The number this replaces was "an observation whose source fragment is also
cited by some evidence step" -- 55 of 430.  That metric is a proxy standing in
for an edge the schema never had, and it is brittle in a specific way: an
observation and an evidence step share a fragment only when the model quoted a
byte-identical excerpt, because fragments are keyed by
`(segment_index, verbatim_excerpt)`.  Widen the evidence step's excerpt by four
characters and the same sentence becomes two fragments and the link disappears.

So a single coverage percentage hides two unrelated failures with different
fixes.  This separates them:

  in_argument              the fragment really is cited by an evidence step
  paired_by_excerpt        no shared fragment, but an evidence step in the same
                           paragraph quotes a superstring or substring of the
                           observation's excerpt -- the same sentence, split at
                           a different point.  Content reached the argument;
                           only the link is missing.
  same_paragraph_unpaired  the paragraph has evidence, but nothing that looks
                           like this observation.  Needs a human.
  paragraph_has_no_evidence  no evidence step was extracted from that paragraph
                           at all.
  no_anchor                the observation resolves to no fragment.

Every status is a *structural* judgment scoped to one source paragraph, and
that is the hard limit on what these numbers mean.  A professor who states a
fact in one paragraph and draws the conclusion five paragraphs later produces
`paragraph_has_no_evidence` even though the argument layer is complete: Matt
16:19's future perfect sits at S0063 with its inference at S0068, and is
extracted again from three other sources, each with evidence and claims.

So `same_paragraph_unpaired` and `paragraph_has_no_evidence` are upper bounds
on what was lost, not counts of it.  Turning those bounds into a real number
needs the semantic question -- is this observation's content anywhere in the
argument layer -- which no structural comparison can answer.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from backend.pipeline.observation_type_vocabulary import classify

IN_ARGUMENT = "in_argument"
PAIRED_BY_EXCERPT = "paired_by_excerpt"
SAME_PARAGRAPH_UNPAIRED = "same_paragraph_unpaired"
PARAGRAPH_HAS_NO_EVIDENCE = "paragraph_has_no_evidence"
NO_ANCHOR = "no_anchor"

STATUSES: tuple[str, ...] = (
    IN_ARGUMENT,
    PAIRED_BY_EXCERPT,
    SAME_PARAGRAPH_UNPAIRED,
    PARAGRAPH_HAS_NO_EVIDENCE,
    NO_ANCHOR,
)

# Statuses where the observation's content is present in the argument layer,
# whether or not an edge records it.
REACHED = frozenset({IN_ARGUMENT, PAIRED_BY_EXCERPT})


def fragment_ids(record: dict[str, Any]) -> list[str]:
    """Read fragment references, tolerating both the list and singular forms."""

    values = record.get("source_fragment_ids")
    if isinstance(values, list) and values:
        return [str(value) for value in values if value]
    single = record.get("source_fragment_id")
    return [str(single)] if single else []


def _paragraph(fragment: dict[str, Any]) -> tuple[str, str]:
    return (str(fragment.get("source_id") or ""), str(fragment.get("paragraph_key") or ""))


def _excerpt(fragment: dict[str, Any]) -> str:
    return str(fragment.get("verbatim_excerpt") or "").strip()


def classify_observation(
    observation: dict[str, Any],
    *,
    fragments: dict[str, dict[str, Any]],
    evidence_fragment_ids: set[str],
    evidence_by_paragraph: dict[tuple[str, str], list[tuple[str, str]]],
) -> dict[str, Any]:
    """Decide one observation's status, and say which evidence step it points at."""

    own = fragment_ids(observation)
    resolved = [fragment_id for fragment_id in own if fragment_id in fragments]
    if not resolved:
        return {"status": NO_ANCHOR, "evidence_step_id": None, "paragraph": None}

    shared = [fragment_id for fragment_id in resolved if fragment_id in evidence_fragment_ids]
    if shared:
        return {"status": IN_ARGUMENT, "evidence_step_id": None, "paragraph": None}

    saw_paragraph_evidence = False
    for fragment_id in resolved:
        fragment = fragments[fragment_id]
        paragraph = _paragraph(fragment)
        excerpt = _excerpt(fragment)
        candidates = evidence_by_paragraph.get(paragraph, [])
        if candidates:
            saw_paragraph_evidence = True
        for evidence_step_id, evidence_excerpt in candidates:
            if excerpt and (excerpt in evidence_excerpt or evidence_excerpt in excerpt):
                return {
                    "status": PAIRED_BY_EXCERPT,
                    "evidence_step_id": evidence_step_id,
                    "paragraph": list(paragraph),
                }

    first = fragments[resolved[0]]
    return {
        "status": SAME_PARAGRAPH_UNPAIRED if saw_paragraph_evidence else PARAGRAPH_HAS_NO_EVIDENCE,
        "evidence_step_id": None,
        "paragraph": list(_paragraph(first)),
    }


def observations_for_passage(
    package: dict[str, Any], passage: "Passage"
) -> list[dict[str, Any]]:
    """Select the observations one article's passage draws on.

    Uses `passage_knowledge_slice`'s own scripture-reference overlap, so the
    scope measured here is exactly the scope the authoring pipeline would put
    in front of the author -- not a second, differently-drawn boundary.
    """

    from backend.pipeline.passage_knowledge_slice import reference_overlaps

    return [
        row for row in package.get("observations", [])
        if any(reference_overlaps(str(ref), passage) for ref in row.get("scripture_refs") or [])
    ]


def measure_coverage(package: dict[str, Any]) -> dict[str, Any]:
    """Return the full coverage picture for one knowledge package."""

    fragments = {
        str(row.get("fragment_id") or row.get("source_fragment_id")): row
        for row in package.get("source_fragments", [])
        if row.get("fragment_id") or row.get("source_fragment_id")
    }
    evidence_steps = package.get("evidence_steps", [])

    evidence_fragment_ids: set[str] = set()
    evidence_by_paragraph: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for step in evidence_steps:
        step_id = str(step.get("evidence_step_id") or "")
        for fragment_id in fragment_ids(step):
            evidence_fragment_ids.add(fragment_id)
            fragment = fragments.get(fragment_id)
            if fragment is not None:
                evidence_by_paragraph[_paragraph(fragment)].append((step_id, _excerpt(fragment)))

    observations = package.get("observations", [])
    status_counts: Counter[str] = Counter()
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    missing_paragraphs: dict[tuple[str, str], list[str]] = defaultdict(list)
    rows: list[dict[str, Any]] = []

    for observation in observations:
        outcome = classify_observation(
            observation,
            fragments=fragments,
            evidence_fragment_ids=evidence_fragment_ids,
            evidence_by_paragraph=evidence_by_paragraph,
        )
        status = outcome["status"]
        status_counts[status] += 1

        result = classify(observation.get("observation_type"))
        category = result.category if result.confidence == "certain" else "(unmapped)"
        by_category[category][status] += 1

        if status == PARAGRAPH_HAS_NO_EVIDENCE and outcome["paragraph"]:
            key = (outcome["paragraph"][0], outcome["paragraph"][1])
            missing_paragraphs[key].append(str(observation.get("observation_id")))

        rows.append({
            "observation_id": str(observation.get("observation_id")),
            "observation_type": observation.get("observation_type"),
            "normalized_type": category,
            "status": status,
            "paired_evidence_step_id": outcome["evidence_step_id"],
        })

    total = len(observations)
    reached = sum(status_counts[status] for status in REACHED)
    return {
        "schema_version": "wang_observation_argument_coverage_v1",
        "totals": {
            "observations": total,
            "evidence_steps": len(evidence_steps),
            "reached_argument_layer": reached,
            "reached_pct": round(100.0 * reached / total, 1) if total else 0.0,
            "linked_by_shared_fragment": status_counts[IN_ARGUMENT],
            "linked_by_shared_fragment_pct": (
                round(100.0 * status_counts[IN_ARGUMENT] / total, 1) if total else 0.0
            ),
        },
        "status_counts": {status: status_counts[status] for status in STATUSES},
        "by_normalized_type": {
            category: {status: counts[status] for status in STATUSES if counts[status]}
            for category, counts in sorted(by_category.items())
        },
        "extraction_gap_paragraphs": [
            {
                "source_id": source_id,
                "paragraph_key": paragraph_key,
                "observation_ids": sorted(observation_ids),
            }
            for (source_id, paragraph_key), observation_ids in sorted(missing_paragraphs.items())
        ],
        "observations": rows,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package", type=Path, default=None,
        help="Measure a knowledge package JSON instead of the PostgreSQL store.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Write the report as JSON.")
    parser.add_argument(
        "--type", dest="normalized_type", default=None,
        help="Print the per-status breakdown for one normalized type only.",
    )
    # Same four arguments as passage_knowledge_slice, so a scope named there
    # is named identically here.
    parser.add_argument("--book", default="Matt")
    parser.add_argument("--chapter", type=int, default=None)
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    return parser.parse_args(argv)


def _load_package(path: Optional[Path]) -> dict[str, Any]:
    if path:
        return json.loads(path.read_text("utf-8"))
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    return PostgresKnowledgeStore().compile_package()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    package = _load_package(args.package)

    scope = "whole store"
    if args.chapter is not None:
        from backend.pipeline.passage_knowledge_slice import Passage

        passage = Passage(args.book, args.chapter, args.start, args.end)
        # Scope the observations, but keep every evidence step and fragment:
        # an observation in this passage may pair with a step the model tagged
        # with a neighbouring reference, and dropping those would invent gaps.
        package = {**package, "observations": observations_for_passage(package, passage)}
        scope = passage.display

    report = measure_coverage(package)
    totals = report["totals"]

    print(f"scope                           {scope}")
    print(f"observations                    {totals['observations']}")
    print(f"evidence steps                  {totals['evidence_steps']}")
    print(
        f"reached the argument layer      {totals['reached_argument_layer']}"
        f"  ({totals['reached_pct']}%)"
    )
    print(
        f"  of which linked by fragment   {totals['linked_by_shared_fragment']}"
        f"  ({totals['linked_by_shared_fragment_pct']}%)  <- the old metric"
    )
    print()
    for status, count in report["status_counts"].items():
        print(f"  {status:<26}{count:>5}")

    print("\n--- by normalized observation_type ---")
    for category, counts in report["by_normalized_type"].items():
        if args.normalized_type and category != args.normalized_type:
            continue
        total = sum(counts.values())
        reached = sum(counts.get(status, 0) for status in REACHED)
        pct = round(100.0 * reached / total, 1) if total else 0.0
        print(f"  {category:<20}{total:>5} 條, 進論證層 {reached:>4} ({pct}%)")

    gaps = report["extraction_gap_paragraphs"]
    print(f"\n--- extraction gaps: {len(gaps)} paragraphs with observations but no evidence ---")
    for gap in gaps[:10]:
        print(f"  {gap['source_id'][:52]:<52} {gap['paragraph_key']:<8} {len(gap['observation_ids'])}")
    if len(gaps) > 10:
        print(f"  ... and {len(gaps) - 10} more")

    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
