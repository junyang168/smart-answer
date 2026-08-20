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
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    build_retirement_plan,
    combined_plan,
)
from backend.pipeline.extraction_supersede import package_source_ids, superseded
from backend.pipeline.record_withdrawal import ANCHORED_COLLECTIONS

RELATION_COLLECTIONS = ("claim_relations", "knowledge_relations")

#: A composition plan that has produced a manuscript. Plans without one are
#: candidates, and a candidate plan is rebuilt from whatever the claim layer
#: holds when somebody writes from it -- there is nothing to tell anyone about.
ARTICLE_COLLECTION = "composition_plans"


def articles_to_regenerate(
    object_ids: set[str],
    *,
    routes: Mapping[str, Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]],
    plans: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The written articles this withdrawal invalidates.

    Not a reason to refuse. New material means the article that drew on the
    old material is rewritten, and the plan, its decisions and its routes are
    all products of that rewrite -- so a decision citing a retired claim is
    the expected state between ingesting and regenerating, not a fault to
    repair. The one thing nobody can work out for themselves is which
    manuscripts just went stale.

    Traced through the actual citation paths rather than by searching the
    payload text, so an id mentioned in prose does not read as a dependency.
    """

    if not object_ids:
        return []
    by_plan: dict[str, set[str]] = {}
    for payload in routes.values():
        if str(payload.get("claim_id") or "") in object_ids:
            by_plan.setdefault(str(payload.get("target_id") or ""), set()).add(
                str(payload.get("claim_id"))
            )
    for payload in decisions.values():
        cited = {str(value) for value in (payload.get("claim_ids") or [])} & object_ids
        if cited:
            by_plan.setdefault(str(payload.get("plan_id") or ""), set()).update(cited)

    articles: list[dict[str, Any]] = []
    for plan_id, payload in plans.items():
        if not payload.get("manuscript_sha256"):
            continue
        cited = by_plan.get(plan_id, set())
        if cited:
            articles.append({
                "plan_id": plan_id,
                "description": str(payload.get("description") or "")[:80],
                "claims_withdrawn": len(cited),
            })
    return sorted(articles, key=lambda row: -row["claims_withdrawn"])


def _live(cursor: Any, collection: str) -> dict[str, dict[str, Any]]:
    cursor.execute(
        """SELECT object_id, payload FROM wang_knowledge.objects
           WHERE collection=%s AND retired_at IS NULL""",
        (collection,),
    )
    return {str(object_id): payload for object_id, payload in cursor.fetchall()}


def plan(store: PostgresKnowledgeStore, package: dict[str, Any], *, source_kind: str):
    """The one change set that lands `package` and withdraws its predecessor.

    Returns the plan, the withdrawal, and the written articles it invalidates.
    """

    with store.connect() as conn, conn.cursor() as cursor:
        withdrawal = superseded(
            package,
            live_fragments=_live(cursor, "source_fragments"),
            owners={name: _live(cursor, name) for name in ANCHORED_COLLECTIONS},
            claims=_live(cursor, "claims"),
            relations={name: _live(cursor, name) for name in RELATION_COLLECTIONS},
        )
        articles = articles_to_regenerate(
            {o for _, o in withdrawal.closure()},
            routes=_live(cursor, "knowledge_routes"),
            decisions=_live(cursor, "composition_decisions"),
            plans=_live(cursor, ARTICLE_COLLECTION),
        )
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
    return combined_plan(arrival, retirement), withdrawal, articles


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--database-url")
    parser.add_argument("--source-kind", default="knowledge_package")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    package = json.loads(args.package.read_text(encoding="utf-8"))
    store = PostgresKnowledgeStore(args.database_url)
    change_set, withdrawal, articles = plan(store, package, source_kind=args.source_kind)
    output: dict[str, Any] = {
        "package": str(args.package),
        "sources": sorted(package_source_ids(package)),
        "supersedes": withdrawal.as_dict(),
        "change_set_id": change_set.change_set_id,
        "summary": change_set.as_dict()["summary"],
        # The only thing a person has to act on: new material means the
        # articles written from the old material get regenerated.
        "articles_to_regenerate": articles,
    }
    if args.apply:
        output["result"] = store.apply_plan(
            change_set,
            metadata={"input_path": str(args.package), "supersedes": withdrawal.as_dict()},
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
