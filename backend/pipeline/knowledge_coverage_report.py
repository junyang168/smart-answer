"""Measure which Claims a passage scope can actually see, and which it loses.

The owner's ruling (#312): which verse a Claim cites is not which verse it is
interpreting. `scripture_refs` alone once routed the professor's Eph 2:20
argument for Matt 16:18 out of resolution, and every downstream model went
blind with it. Scope membership is therefore computed as a closure:

    1. Seed — every Claim whose scripture_refs overlap the scope's passage
       windows enters core.
    2. Argument-dependency closure — every Claim that supports, qualifies, or
       otherwise stands in a recorded ClaimRelation to an in-scope Claim joins
       the scope, whatever verse it cites; repeat to a fixed point.
    3. Route edges — Claims bound in the attestations of an ArgumentRoute that
       already touches the scope join with it (the closure's recall depends on
       the relation graph; route bindings are the second belt).
    4. Whatever is still outside is listed by name, never silently dropped.

Read-only: no model calls, no master-data writes. The report compares the
closure against the current lane assignment and against link coverage, so the
numbers say how much load-bearing material the old single-signal filter hid.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.matthew16_viewpoint_pilot import PASSAGE_UNITS
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.passage_knowledge_slice import reference_overlaps

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def seed_claims(
    claims: Iterable[Mapping[str, Any]], passage_units: Mapping[str, Any]
) -> set[str]:
    """Claims whose own scripture_refs overlap any scope passage window."""

    seeded: set[str] = set()
    for claim in claims:
        refs = [str(value) for value in claim.get("scripture_refs") or []]
        for passages in passage_units.values():
            if any(
                reference_overlaps(reference, passage)
                for reference in refs
                for passage in passages
            ):
                seeded.add(str(claim["claim_id"]))
                break
    return seeded


def relation_closure(
    in_scope: set[str], relations: Iterable[Mapping[str, Any]]
) -> tuple[set[str], list[int]]:
    """Fixed-point closure over recorded ClaimRelations, both directions.

    A Claim supporting an in-scope Claim serves the passage's argument; an
    in-scope Claim's own recorded qualifications belong with it equally. The
    per-round growth is returned so the report can show how deep the
    professor's cross-scripture argumentation actually chains.
    """

    edges: list[tuple[str, str]] = [
        (str(item.get("from_id") or ""), str(item.get("to_id") or ""))
        for item in relations
    ]
    scope = set(in_scope)
    growth: list[int] = []
    while True:
        added: set[str] = set()
        for left, right in edges:
            if left in scope and right not in scope:
                added.add(right)
            if right in scope and left not in scope:
                added.add(left)
        if not added:
            break
        scope |= added
        growth.append(len(added))
    return scope, growth


def route_edge_expansion(
    in_scope: set[str], attestations: Iterable[Mapping[str, Any]]
) -> set[str]:
    """Claims attested alongside in-scope Claims on the same route."""

    added: set[str] = set()
    by_route: dict[str, set[str]] = {}
    for attestation in attestations:
        route_id = str(attestation.get("argument_route_id") or "")
        claim_ids = {str(value) for value in attestation.get("claim_ids") or []}
        by_route.setdefault(route_id, set()).update(claim_ids)
    for claim_ids in by_route.values():
        if claim_ids & in_scope:
            added |= claim_ids - in_scope
    return added


def build_report(
    *,
    claims: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    attestations: list[dict[str, Any]],
    links: list[dict[str, Any]],
    passage_units: Mapping[str, Any],
    scope_lanes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    claim_ids = {str(item["claim_id"]) for item in claims}
    seeds = seed_claims(claims, passage_units)
    after_relations, growth = relation_closure(seeds, relations)
    route_added = route_edge_expansion(after_relations, attestations)
    scope = after_relations | route_added
    orphans = sorted(claim_ids - scope)

    linked = {
        str(item.get("claim_id") or "")
        for item in links
        if item.get("effective_state") == "active"
    }
    lanes = dict(scope_lanes or {})
    recovered_from_context = sorted(
        value
        for value in scope
        if lanes.get(value) == "source_context_candidate"
    )
    report = {
        "schema_version": "wang_knowledge_coverage_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claims_total": len(claim_ids),
        "seed_count": len(seeds),
        "closure_rounds": growth,
        "closure_count": len(after_relations),
        "route_edge_added": sorted(route_added),
        "scope_count": len(scope),
        "scope_unlinked": sorted(
            value for value in scope if value in claim_ids and value not in linked
        ),
        "recovered_from_context_lane": recovered_from_context,
        "orphans": orphans,
    }
    report["report_sha256"] = sha256_json(report)
    return report


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope-artifact",
        type=Path,
        help="pilot scope artifact whose lane assignments the report compares against",
    )
    parser.add_argument(
        "--source-prefix",
        action="append",
        help="limit claims to ids starting with these prefixes (default: every claim)",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    store = PostgresKnowledgeStore()
    claims = store.list_records("claims")
    if args.source_prefix:
        prefixes = tuple(args.source_prefix)
        claims = [c for c in claims if str(c["claim_id"]).startswith(prefixes)]
    relations = store.list_records("claim_relations")
    attestations = store.list_records("argument_route_attestations")
    links = store.list_records("viewpoint_claim_links")

    scope_lanes: dict[str, str] = {}
    if args.scope_artifact:
        artifact = json.loads(args.scope_artifact.read_text(encoding="utf-8"))
        scope_lanes = {
            str(row.get("claim_id") or ""): str(row.get("lane") or "")
            for row in artifact.get("claims") or []
        }

    report = build_report(
        claims=claims,
        relations=relations,
        attestations=attestations,
        links=links,
        passage_units=PASSAGE_UNITS,
        scope_lanes=scope_lanes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: (len(report[key]) if isinstance(report[key], list) else report[key])
                for key in (
                    "claims_total",
                    "seed_count",
                    "closure_rounds",
                    "scope_count",
                    "route_edge_added",
                    "recovered_from_context_lane",
                    "scope_unlinked",
                    "orphans",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
