"""Plan the `observation_type` migration onto the closed vocabulary.

Reads every observation in the authoring store, classifies its legacy type
with `observation_type_vocabulary`, and reports what the migration would do
before it does anything.  Values the rules can only propose, and values no
rule claims, are emitted as a review queue rather than being written.

This never mutates the store.  `--apply` is deliberately absent: the decision
to rewrite 430 records belongs to the project owner, and the review queue is
the artifact that decision is made on.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from backend.pipeline.observation_type_vocabulary import (
    CERTAIN,
    OBSERVATION_TYPES,
    classify,
)


def build_migration_report(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the full before/after picture for one set of observations."""

    by_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_raw[str(row.get("observation_type") or "")].append(row)

    settled: dict[str, str] = {}
    review: list[dict[str, Any]] = []
    category_records: Counter[str] = Counter()
    for raw, rows in sorted(by_raw.items(), key=lambda item: (-len(item[1]), item[0])):
        result = classify(raw)
        if result.confidence == CERTAIN:
            settled[raw] = result.category or ""
            category_records[result.category or ""] += len(rows)
            continue
        review.append({
            "raw_value": raw,
            "record_count": len(rows),
            "suggested_category": result.category,
            "confidence": result.confidence,
            "reason": (
                "no rule claims this value" if result.confidence is None
                else "category is an editorial judgment, not a spelling difference"
            ),
            "observation_ids": [str(row.get("observation_id")) for row in rows],
            "sample_statements": [
                str(row.get("statement") or "")[:160] for row in rows[:3]
            ],
        })

    review_records = sum(item["record_count"] for item in review)
    total = len(observations)
    return {
        "schema_version": "wang_observation_type_migration_v1",
        "totals": {
            "observations": total,
            "distinct_legacy_values": len(by_raw),
            "settled_values": len(settled),
            "settled_records": total - review_records,
            "review_values": len(review),
            "review_records": review_records,
        },
        "category_records": {
            category: category_records.get(category, 0) for category in OBSERVATION_TYPES
        },
        "settled_map": settled,
        "review_queue": review,
    }


def build_migration_package(
    observations: list[dict[str, Any]],
    *,
    decisions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a package rewriting only the observations whose type changes.

    `decisions` supplies the reviewer's answer for values the rules would not
    settle; a value absent from it is left alone rather than guessed.  The
    original label is kept in `observation_type_original`, so the fold stays
    auditable and no information is destroyed by the rewrite.
    """

    decisions = decisions or {}
    updated: list[dict[str, Any]] = []
    for row in observations:
        raw = str(row.get("observation_type") or "")
        result = classify(raw)
        target = result.category if result.confidence == CERTAIN else decisions.get(raw)
        if not target or target == raw:
            continue
        if target not in OBSERVATION_TYPES:
            raise ValueError(f"decision for {raw!r} is not in the vocabulary: {target!r}")
        payload = dict(row)
        payload["observation_type"] = target
        payload.setdefault("observation_type_original", raw)
        updated.append(payload)

    return {
        "package_id": "OBSERVATION-TYPE-VOCABULARY-V1",
        "source_documents": [],
        "source_fragments": [],
        "observations": updated,
    }


def _load_observations(store: Any) -> list[dict[str, Any]]:
    package = store.compile_package()
    return list(package.get("observations", []))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package", type=Path, default=None,
        help="Read observations from a knowledge package JSON instead of PostgreSQL.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Write the report as JSON.")
    parser.add_argument(
        "--decisions", type=Path, default=None,
        help='JSON object of reviewer decisions, {"raw value": "category"}, for the review queue.',
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the rewrite to PostgreSQL as a change set. Without it, nothing is written.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.package:
        observations = list(json.loads(args.package.read_text("utf-8")).get("observations", []))
    else:
        from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

        observations = _load_observations(PostgresKnowledgeStore())

    report = build_migration_report(observations)
    totals = report["totals"]
    print(f"observations               {totals['observations']}")
    print(f"distinct legacy values     {totals['distinct_legacy_values']}")
    print(f"settled by rule            {totals['settled_values']} 種 / {totals['settled_records']} 筆")
    print(f"needs review               {totals['review_values']} 種 / {totals['review_records']} 筆")
    print()
    for category, count in report["category_records"].items():
        print(f"  {category:<22}{count:>6}")
    print("\n--- review queue ---")
    for item in report["review_queue"]:
        suggested = item["suggested_category"] or "—"
        print(f"  {item['raw_value']:<34} ({item['record_count']:>2})  建議 {suggested}")

    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.output}")

    decisions = json.loads(args.decisions.read_text("utf-8")) if args.decisions else {}
    migration = build_migration_package(observations, decisions=decisions)
    rewrites = migration["observations"]
    folded = Counter(row["observation_type"] for row in rewrites)
    print(f"\n--- rewrite: {len(rewrites)} observations ---")
    for category, count in folded.most_common():
        print(f"  {category:<22}{count:>5}")

    if not args.apply:
        print("\n未寫入。加 --apply 才會寫進 PostgreSQL。")
        return 0
    if args.package:
        print("\n--apply 需要 PostgreSQL，不能與 --package 併用。")
        return 1

    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore

    store = PostgresKnowledgeStore()
    plan = store.plan_package(migration, source_kind="observation_type_vocabulary_migration")
    result = store.apply_plan(plan, metadata={
        "vocabulary": list(OBSERVATION_TYPES),
        "reviewer_decisions": decisions,
        "rewritten": len(rewrites),
    })
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
