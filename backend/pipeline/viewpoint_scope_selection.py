"""Deterministic passage-scope selection over the reviewed argument graph.

``scripture_refs`` identify the text a Claim cites.  They do not, by
themselves, identify the passage that Claim helps to interpret.  Passage refs
therefore provide only the seed set.  Reviewed, directed Claim relations and
approved ArgumentRoute attestations provide the dependency edges that close
the scope without asking a model to rediscover relationships already present
in master data.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence


ARGUMENT_DEPENDENCY_RELATION_TYPES = frozenset(
    {
        "applies",
        "contextualizes",
        "depends_on",
        "extends",
        "qualifies",
        "supports",
        "superseding_evidence",
        "tension_evidence",
    }
)


def _value(row: Mapping[str, Any] | Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(row, Mapping):
            if name in row:
                return row[name]
        elif hasattr(row, name):
            return getattr(row, name)
    return default


def _active(row: Mapping[str, Any] | Any, field: str) -> bool:
    value = _value(row, field, default="active")
    return value in {None, "", "active"}


def _reviewed(row: Mapping[str, Any] | Any) -> bool:
    return str(_value(row, "review_status", default="approved")) in {
        "approved",
        "ai_consensus",
        "human_approved",
        "system_approved",
    }


def _relation_rows(
    claim_relations: Sequence[Mapping[str, Any] | Any],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in claim_relations:
        relation_type = str(_value(raw, "relation_type", default=""))
        if relation_type not in ARGUMENT_DEPENDENCY_RELATION_TYPES:
            continue
        # ClaimRelation records extracted from one reviewed source package
        # commonly remain `candidate`.  Using that edge for recall does not
        # approve either Claim or relation; it only makes the Claim visible to
        # the proposal/review gate.  Excluding candidate edges recreates the
        # exact silent-loss bug this closure exists to prevent.
        if not _active(raw, "effective_state") or str(
            _value(raw, "review_status", default="candidate")
        ) in {"invalidated", "rejected", "retired"}:
            continue
        source = str(_value(raw, "from_id", "source_id", "from_claim_id", default=""))
        target = str(_value(raw, "to_id", "target_id", "to_claim_id", default=""))
        relation_id = str(_value(raw, "claim_relation_id", "relation_id", default=""))
        if source and target and relation_id:
            rows.append(
                {
                    "relation_id": relation_id,
                    "relation_type": relation_type,
                    "from_claim_id": source,
                    "to_claim_id": target,
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            row["to_claim_id"],
            row["from_claim_id"],
            row["relation_type"],
            row["relation_id"],
        ),
    )


def _route_claim_groups(
    *,
    selected_claim_ids: set[str],
    viewpoint_claim_links: Sequence[Mapping[str, Any] | Any],
    argument_routes: Sequence[Mapping[str, Any] | Any],
    route_attestations: Sequence[Mapping[str, Any] | Any],
) -> list[dict[str, Any]]:
    """Return approved route Claim groups whose conclusion CVP is in scope."""

    selected_viewpoints = {
        str(_value(link, "viewpoint_id", default=""))
        for link in viewpoint_claim_links
        if str(_value(link, "claim_id", default="")) in selected_claim_ids
        and _active(link, "effective_state")
        and _reviewed(link)
    }
    selected_viewpoints.discard("")
    routes = {
        str(_value(route, "argument_route_id", default="")): str(
            _value(route, "conclusion_viewpoint_id", default="")
        )
        for route in argument_routes
        if _active(route, "route_status") and _reviewed(route)
    }
    result: list[dict[str, Any]] = []
    for attestation in route_attestations:
        route_id = str(_value(attestation, "argument_route_id", default=""))
        viewpoint_id = routes.get(route_id, "")
        if viewpoint_id not in selected_viewpoints:
            continue
        if not _active(attestation, "effective_state") or not _reviewed(attestation):
            continue
        claim_ids = sorted(
            {str(item) for item in (_value(attestation, "claim_ids", default=[]) or [])}
        )
        if claim_ids:
            result.append(
                {
                    "argument_route_id": route_id,
                    "argument_route_attestation_id": str(
                        _value(attestation, "argument_route_attestation_id", default="")
                    ),
                    "conclusion_viewpoint_id": viewpoint_id,
                    "claim_ids": claim_ids,
                }
            )
    return sorted(
        result,
        key=lambda row: (
            row["argument_route_id"],
            row["argument_route_attestation_id"],
            row["claim_ids"],
        ),
    )


def select_scope_claims(
    *,
    scope: Mapping[str, Any],
    passage_unit_ids: Sequence[str],
    claim_relations: Sequence[Mapping[str, Any] | Any] = (),
    viewpoint_claim_links: Sequence[Mapping[str, Any] | Any] = (),
    argument_routes: Sequence[Mapping[str, Any] | Any] = (),
    route_attestations: Sequence[Mapping[str, Any] | Any] = (),
) -> dict[str, Any]:
    """Select one passage's Claims and disclose every deterministic admission.

    Relation closure is deliberately source-local.  It restores cross-Scripture
    reasoning inside one sermon or manuscript without manufacturing a single
    argument from several sources.  Approved route attestations are a second,
    explicitly reviewed edge type and may add occurrences from other sources.
    """

    rows = {
        str(row["claim_id"]): dict(row) for row in (scope.get("claims") or [])
    }
    wanted = set(passage_unit_ids)
    seeds = {
        claim_id
        for claim_id, row in rows.items()
        if row.get("lane") == "core"
        and (not wanted or wanted & set(row.get("passage_unit_ids") or []))
    }
    if not seeds:
        return {
            "seed_claim_ids": [],
            "selected_claim_ids": [],
            "dependency_additions": [],
            "route_additions": [],
            "orphan_context_claim_ids": sorted(
                claim_id
                for claim_id, row in rows.items()
                if row.get("lane") == "source_context_candidate"
            ),
            "out_of_unit_core_claim_ids": sorted(
                claim_id for claim_id, row in rows.items() if row.get("lane") == "core"
            ),
            "dangling_dependencies": [],
        }

    selected = set(seeds)
    inherited_units: dict[str, set[str]] = {
        claim_id: (
            wanted & set(rows[claim_id].get("passage_unit_ids") or [])
            if wanted
            else set(rows[claim_id].get("passage_unit_ids") or [])
        )
        for claim_id in seeds
    }
    dependency_claim_paths: dict[str, list[str]] = {
        claim_id: [claim_id] for claim_id in seeds
    }
    dependency_relation_paths: dict[str, list[str]] = {
        claim_id: [] for claim_id in seeds
    }
    dependencies = _relation_rows(claim_relations)
    dependency_additions: dict[str, dict[str, Any]] = {}
    route_additions: dict[str, dict[str, Any]] = {}

    changed = True
    round_number = 0
    while changed:
        changed = False
        round_number += 1

        # The edge direction is premise/support -> supported conclusion.  Walk
        # backwards from a selected conclusion to include its prerequisites.
        for relation in dependencies:
            source = relation["from_claim_id"]
            target = relation["to_claim_id"]
            if target not in selected or source in selected:
                continue
            if source not in rows or target not in rows:
                continue
            if str(rows[source].get("source_id") or "") != str(
                rows[target].get("source_id") or ""
            ):
                continue
            selected.add(source)
            inherited_units[source] = set(inherited_units.get(target) or wanted)
            dependency_claim_paths[source] = [
                source,
                *dependency_claim_paths.get(target, [target]),
            ]
            dependency_relation_paths[source] = [
                relation["relation_id"],
                *dependency_relation_paths.get(target, []),
            ]
            dependency_additions[source] = {
                "claim_id": source,
                "admission_round": round_number,
                "inherited_passage_unit_ids": sorted(inherited_units[source]),
                "via_claim_id": target,
                "dependency_path_claim_ids": dependency_claim_paths[source],
                "dependency_path_relation_ids": dependency_relation_paths[source],
                **relation,
            }
            changed = True

        route_groups = _route_claim_groups(
            selected_claim_ids=selected,
            viewpoint_claim_links=viewpoint_claim_links,
            argument_routes=argument_routes,
            route_attestations=route_attestations,
        )
        for group in route_groups:
            selected_units = set().union(
                *(inherited_units.get(item, set()) for item in group["claim_ids"] if item in selected)
            )
            if not selected_units:
                selected_units = set(wanted)
            for claim_id in group["claim_ids"]:
                if claim_id in selected or claim_id not in rows:
                    continue
                selected.add(claim_id)
                inherited_units[claim_id] = set(selected_units)
                dependency_claim_paths[claim_id] = [claim_id]
                dependency_relation_paths[claim_id] = []
                route_additions[claim_id] = {
                    "claim_id": claim_id,
                    "admission_round": round_number,
                    "inherited_passage_unit_ids": sorted(inherited_units[claim_id]),
                    "argument_route_id": group["argument_route_id"],
                    "argument_route_attestation_id": group[
                        "argument_route_attestation_id"
                    ],
                    "conclusion_viewpoint_id": group["conclusion_viewpoint_id"],
                }
                changed = True

    dangling: list[dict[str, Any]] = []
    for relation in dependencies:
        source = relation["from_claim_id"]
        target = relation["to_claim_id"]
        if target not in selected or source in selected:
            continue
        if source not in rows or target not in rows:
            continue
        if str(rows[source].get("source_id") or "") == str(
            rows[target].get("source_id") or ""
        ):
            dangling.append(relation)
    if dangling:
        details = ", ".join(
            f"{row['from_claim_id']} --{row['relation_type']}--> {row['to_claim_id']}"
            for row in dangling
        )
        raise ValueError(f"scope dependency closure left eligible dependencies out: {details}")

    return {
        "seed_claim_ids": sorted(seeds),
        "selected_claim_ids": sorted(selected),
        "dependency_additions": sorted(
            dependency_additions.values(), key=lambda row: row["claim_id"]
        ),
        "route_additions": sorted(route_additions.values(), key=lambda row: row["claim_id"]),
        "orphan_context_claim_ids": sorted(
            claim_id
            for claim_id, row in rows.items()
            if row.get("lane") == "source_context_candidate" and claim_id not in selected
        ),
        "out_of_unit_core_claim_ids": sorted(
            claim_id
            for claim_id, row in rows.items()
            if row.get("lane") == "core" and claim_id not in selected
        ),
        "dangling_dependencies": dangling,
    }
