"""Deterministically select passage Claims from the reviewed argument graph.

``scripture_refs`` seed a passage scope; they do not define its final
membership.  Membership can additionally be established by a source-section
occurrence, by walking *upstream* along a source-local argument dependency, or
by an approved ArgumentRoute whose conclusion viewpoint is already in scope.

Candidate ClaimRelation edges are deliberately usable for recall.  Doing so
does not approve the relation or the Claim: it only admits material to the
proposal/review denominator.  ArgumentRoute edges have the stronger,
cross-source semantics and therefore require active approved records pinned to
the current approved route revision.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from backend.pipeline.passage_knowledge_slice import reference_overlaps


ARGUMENT_DEPENDENCY_RELATION_TYPES = frozenset(
    {"supports", "qualifies", "extends", "explains", "corroborates"}
)
APPROVED_REVIEW_STATUSES = frozenset(
    {"approved", "ai_consensus", "ai_consensus_reviewed", "human_approved", "system_approved"}
)
REJECTED_REVIEW_STATUSES = frozenset({"invalidated", "rejected", "retired"})


def _value(row: Mapping[str, Any] | Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(row, Mapping) and name in row:
            return row[name]
        if not isinstance(row, Mapping) and hasattr(row, name):
            return getattr(row, name)
    return default


def _approved(row: Mapping[str, Any] | Any) -> bool:
    return str(_value(row, "review_status", default="")) in APPROVED_REVIEW_STATUSES


def _active(row: Mapping[str, Any] | Any, field: str) -> bool:
    return str(_value(row, field, default="active")) == "active"


def direct_seed_units(
    claims: Iterable[Mapping[str, Any]], passage_units: Mapping[str, Any]
) -> dict[str, set[str]]:
    """Return exact passage units directly supported by each Claim's refs."""

    result: dict[str, set[str]] = {}
    for claim in claims:
        claim_id = str(claim["claim_id"])
        refs = [str(value) for value in claim.get("scripture_refs") or []]
        units = {
            unit_id
            for unit_id, passages in passage_units.items()
            if any(
                reference_overlaps(reference, passage)
                for reference in refs
                for passage in passages
            )
        }
        if units:
            result[claim_id] = units
    return result


def _eligible_relations(
    relations: Iterable[Mapping[str, Any]],
    claim_sources: Mapping[str, str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Filter dependency edges and disclose every rejected graph edge."""

    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    universe = set(claim_sources)
    for raw in relations:
        relation_id = str(
            _value(raw, "claim_relation_id", "relation_id", default="")
        )
        source = str(_value(raw, "from_id", "source_id", "from_claim_id", default=""))
        target = str(_value(raw, "to_id", "target_id", "to_claim_id", default=""))
        relation_type = str(_value(raw, "relation_type", default=""))
        review_status = str(_value(raw, "review_status", default="candidate"))
        reason = ""
        if source not in universe or target not in universe:
            reason = "endpoint_outside_claim_universe"
        elif relation_type not in ARGUMENT_DEPENDENCY_RELATION_TYPES:
            reason = "relation_type_not_argument_dependency"
        elif review_status in REJECTED_REVIEW_STATUSES:
            reason = "relation_review_status_rejected"
        elif claim_sources[source] != claim_sources[target]:
            reason = "cross_source_relation_requires_argument_route"
        row = {
            "relation_id": relation_id,
            "relation_type": relation_type,
            "review_status": review_status,
            "from_claim_id": source,
            "to_claim_id": target,
        }
        (rejected if reason else accepted).append(row | ({"reason": reason} if reason else {}))
    key = lambda row: (
        row["to_claim_id"],
        row["from_claim_id"],
        row["relation_type"],
        row["relation_id"],
    )
    return sorted(accepted, key=key), sorted(rejected, key=key)


def _eligible_route_groups(
    *,
    claim_sources: Mapping[str, str],
    links: Iterable[Mapping[str, Any]],
    routes: Iterable[Mapping[str, Any]],
    route_revisions: Iterable[Mapping[str, Any]],
    attestations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return current approved route attestations with valid Claim members."""

    active_links = [
        row
        for row in links
        if _active(row, "effective_state")
        and _approved(row)
        and str(_value(row, "claim_id", default="")) in claim_sources
    ]
    links_by_viewpoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in active_links:
        links_by_viewpoint[str(_value(row, "viewpoint_id", default=""))].append(dict(row))

    revisions = {
        str(_value(row, "argument_route_revision_id", default="")): row
        for row in route_revisions
        if _approved(row)
    }
    valid_routes: dict[str, dict[str, str]] = {}
    for route in routes:
        route_id = str(_value(route, "argument_route_id", default=""))
        revision_id = str(_value(route, "current_revision_id", default=""))
        revision = revisions.get(revision_id)
        if not (_active(route, "route_status") and _approved(route) and revision):
            continue
        if str(_value(revision, "argument_route_id", default="")) != route_id:
            continue
        viewpoint_id = str(_value(route, "conclusion_viewpoint_id", default=""))
        conclusion_revision_id = str(
            _value(revision, "validated_against_conclusion_viewpoint_revision_id", default="")
        )
        if conclusion_revision_id not in {
            str(_value(link, "validated_against_viewpoint_revision_id", default=""))
            for link in links_by_viewpoint.get(viewpoint_id, [])
        }:
            continue
        valid_routes[route_id] = {
            "conclusion_viewpoint_id": viewpoint_id,
            "conclusion_viewpoint_revision_id": conclusion_revision_id,
            "route_revision_id": revision_id,
        }

    groups: list[dict[str, Any]] = []
    for attestation in attestations:
        route_id = str(_value(attestation, "argument_route_id", default=""))
        route = valid_routes.get(route_id)
        if not route or not _active(attestation, "effective_state") or not _approved(attestation):
            continue
        if str(_value(attestation, "validated_against_route_revision_id", default="")) != route[
            "route_revision_id"
        ]:
            continue
        source_id = str(_value(attestation, "source_id", default=""))
        claim_ids = sorted(
            {
                str(value)
                for value in (_value(attestation, "claim_ids", default=[]) or [])
                if str(value) in claim_sources and claim_sources[str(value)] == source_id
            }
        )
        if not claim_ids:
            continue
        groups.append(
            {
                **route,
                "argument_route_id": route_id,
                "argument_route_attestation_id": str(
                    _value(attestation, "argument_route_attestation_id", default="")
                ),
                "claim_ids": claim_ids,
                "terminal_claim_ids": sorted(
                    {
                        str(_value(link, "claim_id", default=""))
                        for link in links_by_viewpoint.get(route["conclusion_viewpoint_id"], [])
                        if str(
                            _value(
                                link,
                                "validated_against_viewpoint_revision_id",
                                default="",
                            )
                        )
                        == route["conclusion_viewpoint_revision_id"]
                    }
                ),
            }
        )
    return sorted(
        groups,
        key=lambda row: (
            row["argument_route_id"],
            row["argument_route_attestation_id"],
        ),
    )


def select_scope_units(
    *,
    claims: Iterable[Mapping[str, Any]],
    passage_units: Mapping[str, Any],
    relations: Iterable[Mapping[str, Any]] = (),
    links: Iterable[Mapping[str, Any]] = (),
    routes: Iterable[Mapping[str, Any]] = (),
    route_revisions: Iterable[Mapping[str, Any]] = (),
    attestations: Iterable[Mapping[str, Any]] = (),
    occurrence_unit_ids_by_claim: Mapping[str, Iterable[str]] | None = None,
    occurrence_admissions_by_claim: Mapping[
        str, Iterable[Mapping[str, Any]]
    ]
    | None = None,
) -> dict[str, Any]:
    """Apply the four legal admission signals to one exact Claim universe."""

    rows = {str(row["claim_id"]): dict(row) for row in claims}
    claim_sources = {
        claim_id: str(row.get("source_id") or "") for claim_id, row in rows.items()
    }
    units = direct_seed_units(rows.values(), passage_units)
    admissions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim_id, claim_units in sorted(units.items()):
        admissions[claim_id].append(
            {"signal": "scripture_ref", "passage_unit_ids": sorted(claim_units)}
        )

    if (
        occurrence_unit_ids_by_claim is not None
        and occurrence_admissions_by_claim is not None
    ):
        raise ValueError("supply either occurrence unit ids or detailed admissions, not both")
    occurrence_map = occurrence_unit_ids_by_claim or {}
    for claim_id, raw_units in sorted(occurrence_map.items()):
        if claim_id not in rows:
            continue
        valid_units = set(str(value) for value in raw_units) & set(passage_units)
        if not valid_units:
            continue
        units.setdefault(claim_id, set()).update(valid_units)
        admissions[claim_id].append(
            {"signal": "occurrence_section", "passage_unit_ids": sorted(valid_units)}
        )
    for claim_id, raw_admissions in sorted(
        (occurrence_admissions_by_claim or {}).items()
    ):
        if claim_id not in rows:
            continue
        for raw_admission in raw_admissions:
            detail = dict(raw_admission)
            valid_units = set(
                str(value) for value in detail.get("passage_unit_ids") or []
            ) & set(passage_units)
            if not valid_units:
                continue
            units.setdefault(claim_id, set()).update(valid_units)
            detail.pop("signal", None)
            detail["passage_unit_ids"] = sorted(valid_units)
            admissions[claim_id].append(
                {"signal": "occurrence_section", **detail}
            )

    dependencies, rejected_relations = _eligible_relations(relations, claim_sources)
    route_groups = _eligible_route_groups(
        claim_sources=claim_sources,
        links=links,
        routes=routes,
        route_revisions=route_revisions,
        attestations=attestations,
    )
    relation_growth: list[int] = []
    route_growth: list[int] = []
    while True:
        relation_added: set[str] = set()
        relation_changed = False
        for relation in dependencies:
            source = relation["from_claim_id"]
            target = relation["to_claim_id"]
            inherited = set(units.get(target, set())) - set(units.get(source, set()))
            if not inherited:
                continue
            relation_changed = True
            first_admission = source not in units
            units.setdefault(source, set()).update(inherited)
            if first_admission:
                relation_added.add(source)
            admissions[source].append(
                {
                    "signal": "claim_relation",
                    "passage_unit_ids": sorted(inherited),
                    "relation_id": relation["relation_id"],
                    "relation_type": relation["relation_type"],
                    "relation_review_status": relation["review_status"],
                    "via_claim_id": target,
                    "authority": "recall_only",
                }
            )
        if relation_added:
            relation_growth.append(len(relation_added))

        route_added: set[str] = set()
        route_changed = False
        for group in route_groups:
            route_units: set[str] = set()
            for terminal_claim_id in group["terminal_claim_ids"]:
                route_units.update(units.get(terminal_claim_id, set()))
            if not route_units:
                continue
            for claim_id in group["claim_ids"]:
                inherited = route_units - set(units.get(claim_id, set()))
                if not inherited:
                    continue
                route_changed = True
                first_admission = claim_id not in units
                units.setdefault(claim_id, set()).update(inherited)
                if first_admission:
                    route_added.add(claim_id)
                admissions[claim_id].append(
                    {
                        "signal": "argument_route",
                        "passage_unit_ids": sorted(inherited),
                        "argument_route_id": group["argument_route_id"],
                        "route_revision_id": group["route_revision_id"],
                        "argument_route_attestation_id": group[
                            "argument_route_attestation_id"
                        ],
                        "conclusion_viewpoint_id": group["conclusion_viewpoint_id"],
                    }
                )
        if route_added:
            route_growth.append(len(route_added))
        if not relation_changed and not route_changed:
            break

    return {
        "claim_units": {key: sorted(value) for key, value in sorted(units.items())},
        "admissions": {
            key: sorted(
                value,
                key=lambda row: (
                    row["signal"],
                    row["passage_unit_ids"],
                    row.get("relation_id", ""),
                    row.get("argument_route_attestation_id", ""),
                ),
            )
            for key, value in sorted(admissions.items())
        },
        "relation_growth": relation_growth,
        "route_growth": route_growth,
        "rejected_relations": rejected_relations,
        "occurrence_signal_status": (
            "available"
            if occurrence_unit_ids_by_claim is not None
            or occurrence_admissions_by_claim is not None
            else "unavailable"
        ),
    }
