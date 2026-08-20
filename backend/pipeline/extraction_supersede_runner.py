"""Ingest a re-extraction and retire the extraction it replaces, in one change set.

Planning is the default and `--apply` is opt-in, for the same reason the rest
of this store works that way: seeing what a change set would do is a question
anyone may ask, and changing the authoring authority is a decision somebody
makes once.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    build_retirement_plan,
    combined_plan,
)
from backend.pipeline.extraction_supersede import package_source_ids, superseded
from backend.pipeline.record_withdrawal import ANCHORED_COLLECTIONS

RELATION_COLLECTIONS = ("claim_relations", "knowledge_relations")

#: Records that are written *from* the claim layer rather than into it. They
#: are not part of a withdrawal's closure -- a composition plan is an editorial
#: artifact and deciding its fate is not an extraction's business -- but a
#: withdrawal that leaves one of them citing a retired record has moved the
#: dangling reference up a layer instead of removing it.
DEPENDENT_COLLECTIONS = (
    "knowledge_routes",
    "composition_decisions",
    "composition_plans",
    "product_dependencies",
    "editorial_syntheses",
)


def dependents_on(cursor: Any, object_ids: set[str]) -> dict[str, list[str]]:
    """Downstream artifacts naming any of these records.

    Deliberately broad: a match anywhere in the payload counts, because these
    artifacts cite claims from several differently shaped fields and a
    withdrawal must not turn on having enumerated all of them correctly.
    """

    found: dict[str, list[str]] = {}
    if not object_ids:
        return found
    for collection in DEPENDENT_COLLECTIONS:
        for object_id, payload in _live(cursor, collection).items():
            blob = json.dumps(payload, ensure_ascii=False)
            if any(name in blob for name in object_ids):
                found.setdefault(collection, []).append(object_id)
    return found


def _live(cursor: Any, collection: str) -> dict[str, dict[str, Any]]:
    cursor.execute(
        """SELECT object_id, payload FROM wang_knowledge.objects
           WHERE collection=%s AND retired_at IS NULL""",
        (collection,),
    )
    return {str(object_id): payload for object_id, payload in cursor.fetchall()}


def plan(store: PostgresKnowledgeStore, package: dict[str, Any], *, source_kind: str):
    """The one change set that lands `package` and withdraws its predecessor.

    Returns the plan, the withdrawal, and whatever downstream artifact still
    cites something in it.
    """

    with store.connect() as conn, conn.cursor() as cursor:
        withdrawal = superseded(
            package,
            live_fragments=_live(cursor, "source_fragments"),
            owners={name: _live(cursor, name) for name in ANCHORED_COLLECTIONS},
            claims=_live(cursor, "claims"),
            relations={name: _live(cursor, name) for name in RELATION_COLLECTIONS},
        )
        dependents = dependents_on(cursor, {o for _, o in withdrawal.closure()})
    arrival = store.plan_package(package, source_kind=source_kind)
    keys = withdrawal.closure()
    retirement = store.plan_retirement(
        keys,
        reason=f"superseded by {package.get('package_id')}",
        package_id=str(package.get("package_id") or "PACKAGE"),
        source_kind="extraction_supersede",
    ) if keys else build_retirement_plan(
        [], {}, reason="none", package_id=str(package.get("package_id") or "PACKAGE"),
    )
    return combined_plan(arrival, retirement), withdrawal, dependents


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--database-url")
    parser.add_argument("--source-kind", default="knowledge_package")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--allow-dependent-artifacts", action="store_true",
        help="proceed even though downstream artifacts cite records being retired",
    )
    args = parser.parse_args(argv)

    package = json.loads(args.package.read_text(encoding="utf-8"))
    store = PostgresKnowledgeStore(args.database_url)
    change_set, withdrawal, dependents = plan(store, package, source_kind=args.source_kind)
    output: dict[str, Any] = {
        "package": str(args.package),
        "sources": sorted(package_source_ids(package)),
        "supersedes": withdrawal.as_dict(),
        "change_set_id": change_set.change_set_id,
        "summary": change_set.as_dict()["summary"],
        "dependent_artifacts": {name: len(rows) for name, rows in dependents.items()},
    }
    if dependents and not args.allow_dependent_artifacts:
        # Retiring anyway would move the dangling reference up a layer rather
        # than remove it, and what should happen to an editorial artifact whose
        # source material was re-extracted is an editorial decision.
        output["refused"] = (
            "records in this withdrawal are cited by downstream artifacts; "
            "decide what happens to those first, or pass --allow-dependent-artifacts"
        )
        output["dependent_ids"] = {name: sorted(rows) for name, rows in dependents.items()}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 1
    if args.apply:
        output["result"] = store.apply_plan(
            change_set,
            metadata={"input_path": str(args.package), "supersedes": withdrawal.as_dict()},
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
