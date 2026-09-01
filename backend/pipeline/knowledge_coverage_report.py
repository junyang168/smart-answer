"""Audit passage-scope coverage without changing master data.

The report compares the former undirected/all-relation expansion with the
legal four-signal selector. Its Claim universe is always the supplied scope
artifact; unrelated registry Claims and non-Claim endpoints cannot enter.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.matthew16_viewpoint_pilot import PASSAGE_UNITS
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_scope_selection import (
    ARGUMENT_DEPENDENCY_RELATION_TYPES,
    direct_seed_units,
    select_scope_units,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def seed_claims(
    claims: Iterable[Mapping[str, Any]], passage_units: Mapping[str, Any]
) -> set[str]:
    return set(direct_seed_units(claims, passage_units))


def _legacy_undirected_closure(
    seeds: set[str], relations: Iterable[Mapping[str, Any]], universe: set[str]
) -> tuple[set[str], list[int]]:
    """Reproduce the old broad graph walk inside the correct denominator."""

    edges = [
        (str(row.get("from_id") or ""), str(row.get("to_id") or ""))
        for row in relations
    ]
    selected = set(seeds)
    growth: list[int] = []
    while True:
        additions: set[str] = set()
        for left, right in edges:
            if left in selected and right in universe - selected:
                additions.add(right)
            if right in selected and left in universe - selected:
                additions.add(left)
        if not additions:
            return selected, growth
        selected.update(additions)
        growth.append(len(additions))


def relation_closure(
    in_scope: set[str],
    relations: Iterable[Mapping[str, Any]],
    *,
    claims: Iterable[Mapping[str, Any]],
) -> tuple[set[str], list[int]]:
    """Correct upstream, typed, source-local relation-only closure."""

    selection = select_scope_units(
        claims=[dict(row) for row in claims],
        passage_units={"scope": ()},
        relations=relations,
        occurrence_unit_ids_by_claim={claim_id: ["scope"] for claim_id in in_scope},
    )
    return set(selection["claim_units"]), selection["relation_growth"]


def closure_with_units(
    seed_units: dict[str, set[str]],
    relations: Iterable[Mapping[str, Any]],
    attestations: Iterable[Mapping[str, Any]],
    *,
    claims: Iterable[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]] = (),
    routes: Iterable[Mapping[str, Any]] = (),
    route_revisions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, set[str]]:
    """Correct closure with unit propagation and reviewed route expansion."""

    selection = select_scope_units(
        claims=[dict(row) for row in claims],
        passage_units={key: () for values in seed_units.values() for key in values},
        relations=relations,
        links=links,
        routes=routes,
        route_revisions=route_revisions,
        attestations=attestations,
        occurrence_unit_ids_by_claim=seed_units,
    )
    return {key: set(values) for key, values in selection["claim_units"].items()}


def build_report(
    *,
    claims: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    attestations: list[dict[str, Any]],
    links: list[dict[str, Any]],
    passage_units: Mapping[str, Any],
    routes: list[dict[str, Any]] | None = None,
    route_revisions: list[dict[str, Any]] | None = None,
    occurrence_unit_ids_by_claim: Mapping[str, Iterable[str]] | None = None,
    scope_lanes: Mapping[str, str] | None = None,
    scope_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, SHA-bound legal-scope difference report."""

    claim_ids = {str(row["claim_id"]) for row in claims}
    seeds = seed_claims(claims, passage_units)
    legacy_scope, legacy_growth = _legacy_undirected_closure(
        seeds, relations, claim_ids
    )
    relation_only_selection = select_scope_units(
        claims=claims,
        passage_units=passage_units,
        relations=relations,
        occurrence_unit_ids_by_claim=occurrence_unit_ids_by_claim,
    )
    selection = select_scope_units(
        claims=claims,
        passage_units=passage_units,
        relations=relations,
        links=links,
        routes=routes or [],
        route_revisions=route_revisions or [],
        attestations=attestations,
        occurrence_unit_ids_by_claim=occurrence_unit_ids_by_claim,
    )
    relation_only_scope = set(relation_only_selection["claim_units"])
    corrected_scope = set(selection["claim_units"])
    linked = {
        str(row.get("claim_id") or "")
        for row in links
        if row.get("effective_state") == "active"
        and str(row.get("review_status") or "")
        in {"approved", "human_approved", "system_approved"}
    }
    lanes = dict(scope_lanes or {})
    statements = {
        str(row["claim_id"]): str(row.get("statement") or "") for row in claims
    }
    # #320's disposition population is the 41 legacy-only relation
    # admissions. Keep route/occurrence as annotations on that exact set so a
    # valid second route does not make an item disappear from the audit.
    disputed = sorted(legacy_scope - relation_only_scope)
    occurrence_available = occurrence_unit_ids_by_claim is not None
    disputed_rows = []
    for claim_id in disputed:
        route_admissions = [
            row
            for row in selection["admissions"].get(claim_id, [])
            if row["signal"] == "argument_route"
        ]
        occurrence_admissions = [
            row
            for row in selection["admissions"].get(claim_id, [])
            if row["signal"] == "occurrence_section"
        ]
        route_signal = bool(route_admissions)
        occurrence_signal = bool(occurrence_admissions)
        if route_signal:
            qualification = "proved_by_argument_route"
        elif occurrence_signal:
            qualification = "proved_by_occurrence_section"
        elif occurrence_available:
            qualification = "unproved"
        else:
            qualification = "pending_occurrence_evidence"
        disputed_rows.append(
            {
                "claim_id": claim_id,
                "statement": statements[claim_id],
                "current_lane": lanes.get(claim_id),
                "active_viewpoint_claim_link": claim_id in linked,
                "scripture_ref_signal": claim_id in seeds,
                "claim_relation_signal": False,
                "argument_route_signal": route_signal,
                "argument_route_admissions": route_admissions,
                "occurrence_section_signal": (
                    occurrence_signal
                    if occurrence_available
                    else "unavailable_in_current_master_schema"
                ),
                "occurrence_section_admissions": occurrence_admissions,
                "scope_qualification": qualification,
            }
        )
    rejected_outside = [
        row
        for row in selection["rejected_relations"]
        if row["reason"] == "endpoint_outside_claim_universe"
    ]
    report: dict[str, Any] = {
        "schema_version": "wang_knowledge_coverage_report_v2",
        "scope_artifact_sha256": scope_artifact_sha256,
        "policy": {
            "relation_direction": "upstream_from_to",
            "relation_type_allowlist": sorted(ARGUMENT_DEPENDENCY_RELATION_TYPES),
            "relation_scope": "source_local",
            "candidate_relations": "recall_only",
            "route_scope": "approved_current_route_may_cross_source",
            "claim_universe": "exact_scope_artifact",
        },
        "input_sha256s": {
            "claims": sha256_json(
                sorted(claims, key=lambda row: str(row["claim_id"]))
            ),
            "claim_relations": sha256_json(
                sorted(
                    relations,
                    key=lambda row: str(row.get("claim_relation_id") or ""),
                )
            ),
            "viewpoint_claim_links": sha256_json(
                sorted(
                    links,
                    key=lambda row: str(row.get("viewpoint_claim_link_id") or ""),
                )
            ),
            "argument_routes": sha256_json(
                sorted(
                    routes or [],
                    key=lambda row: str(row.get("argument_route_id") or ""),
                )
            ),
            "argument_route_revisions": sha256_json(
                sorted(
                    route_revisions or [],
                    key=lambda row: str(
                        row.get("argument_route_revision_id") or ""
                    ),
                )
            ),
            "argument_route_attestations": sha256_json(
                sorted(
                    attestations,
                    key=lambda row: str(
                        row.get("argument_route_attestation_id") or ""
                    ),
                )
            ),
        },
        "claims_total": len(claim_ids),
        "seed_count": len(seeds),
        "legacy_undirected_growth": legacy_growth,
        "legacy_undirected_scope_count": len(legacy_scope),
        "relation_only_growth": relation_only_selection["relation_growth"],
        "relation_only_scope_count": len(relation_only_scope),
        "relation_only_recovered_from_context_count": len(
            relation_only_scope - seeds
        ),
        "corrected_relation_growth": selection["relation_growth"],
        "corrected_route_growth": selection["route_growth"],
        "corrected_scope_count": len(corrected_scope),
        "corrected_scope_claim_ids": sorted(corrected_scope),
        "corrected_recovered_from_context_lane": sorted(
            claim_id
            for claim_id in corrected_scope
            if lanes.get(claim_id) == "source_context_candidate"
        ),
        "corrected_scope_unlinked": sorted(corrected_scope - linked),
        "orphan_claim_ids": sorted(claim_ids - corrected_scope),
        "disputed_legacy_admission_count": len(disputed_rows),
        "disputed_legacy_admissions": disputed_rows,
        "disputed_active_link_count": sum(
            bool(row["active_viewpoint_claim_link"]) for row in disputed_rows
        ),
        "disputed_proved_by_route_count": sum(
            row["scope_qualification"] == "proved_by_argument_route"
            for row in disputed_rows
        ),
        "disputed_pending_occurrence_count": sum(
            row["scope_qualification"] == "pending_occurrence_evidence"
            for row in disputed_rows
        ),
        "occurrence_signal_status": selection["occurrence_signal_status"],
        "rejected_relation_count": len(selection["rejected_relations"]),
        "out_of_universe_relation_endpoints": rejected_outside,
    }
    report["report_sha256"] = sha256_json(report)
    return report


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable report differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    scope = _read(args.scope_artifact)
    stated_scope_sha = str(scope.get("artifact_sha256") or "")
    unsigned_scope = {
        key: value for key, value in scope.items() if key != "artifact_sha256"
    }
    if not stated_scope_sha or stated_scope_sha != sha256_json(unsigned_scope):
        raise ValueError("scope artifact SHA mismatch")
    scope_claims = [dict(row) for row in scope.get("claims") or []]
    if not scope_claims:
        raise ValueError("scope artifact has no Claim universe")

    store = PostgresKnowledgeStore()
    report = build_report(
        claims=scope_claims,
        relations=store.list_records("claim_relations"),
        attestations=store.list_records("argument_route_attestations"),
        links=store.list_records("viewpoint_claim_links"),
        routes=store.list_records("argument_routes"),
        route_revisions=store.list_records("argument_route_revisions"),
        passage_units=PASSAGE_UNITS,
        scope_lanes={
            str(row["claim_id"]): str(row.get("lane") or "")
            for row in scope_claims
        },
        scope_artifact_sha256=stated_scope_sha,
    )
    _write_immutable(args.output, report)
    print(
        json.dumps(
            {
                "claims_total": report["claims_total"],
                "seed_count": report["seed_count"],
                "legacy_scope_count": report["legacy_undirected_scope_count"],
                "corrected_scope_count": report["corrected_scope_count"],
                "disputed_legacy_admission_count": report[
                    "disputed_legacy_admission_count"
                ],
                "disputed_active_link_count": report[
                    "disputed_active_link_count"
                ],
                "occurrence_signal_status": report["occurrence_signal_status"],
                "report_sha256": report["report_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
