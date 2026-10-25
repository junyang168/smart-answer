"""Read-only knowledge graphs for the library admin (#453).

Two graphs, as the owner set them out: one of viewpoints across sermons,
whose edges are the recorded viewpoint relations; and one per sermon, whose
nodes are that sermon's Claims and whose edges are the recorded Claim
relations. A sermon's Claims are reached from a viewpoint through the
viewpoint's linked Claims; nothing here draws an edge the store does not
hold -- the knowledge layer does not improvise.

These read the PostgreSQL store directly with a few queries rather than
through the admin projection compiler, which loads every collection and
takes over twenty seconds per request: a graph that has to load in a page
cannot wait for that.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from fastapi import APIRouter, HTTPException

from .canonical_repository.postgres_store import PostgresKnowledgeStore, database_url_from_env

router = APIRouter(prefix="/admin/wang/graph", tags=["wang-admin"])

RELATION_LABELS = {
    "applies": "应用",
    "extends": "延伸",
    "specializes": "具体化",
    "generalizes": "概括",
    "entails": "推出",
    "qualifies": "限定",
    "tensions_with": "张力",
}

_ACTIVE = "retired_at IS NULL AND coalesce(payload->>'effective_state', 'active') = 'active'"

# Viewpoint -> member Claim -> evidence -> source fragment -> source. Evidence
# names its fragment in the singular in one extraction era and the plural in
# another; both are live (see viewpoint_admin_projection._source_ids_by_claim).
_CLAIM_SOURCES = f"""
WITH links AS (
    SELECT payload->>'viewpoint_id' AS viewpoint_id, payload->>'claim_id' AS claim_id
    FROM wang_knowledge.objects WHERE collection = 'viewpoint_claim_links' AND {_ACTIVE}
      {{viewpoint_filter}}
), evidence AS (
    SELECT links.viewpoint_id, links.claim_id, jsonb_array_elements_text(c.payload->'evidence_step_ids') AS step_id
    FROM links JOIN wang_knowledge.objects c
      ON c.collection = 'claims' AND c.object_id = links.claim_id AND c.retired_at IS NULL
), steps AS (
    SELECT evidence.viewpoint_id, evidence.claim_id,
           s.payload->>'source_fragment_id' AS fragment_id, s.payload->'source_fragment_ids' AS fragment_ids
    FROM evidence JOIN wang_knowledge.objects s
      ON s.collection = 'evidence_steps' AND s.object_id = evidence.step_id
), fragments AS (
    SELECT viewpoint_id, claim_id, fragment_id FROM steps WHERE fragment_id IS NOT NULL
    UNION
    SELECT viewpoint_id, claim_id, jsonb_array_elements_text(fragment_ids) FROM steps
    WHERE jsonb_typeof(fragment_ids) = 'array'
)
SELECT DISTINCT fragments.viewpoint_id, fragments.claim_id, f.payload->>'source_id'
FROM fragments JOIN wang_knowledge.objects f
  ON f.collection = 'source_fragments' AND f.object_id = fragments.fragment_id
"""


def _store() -> PostgresKnowledgeStore:
    return PostgresKnowledgeStore(database_url_from_env())


def _rows(cursor: Any, sql: str, params: Iterable[Any] = ()) -> list[tuple]:
    cursor.execute(sql, tuple(params))
    return cursor.fetchall()


def _payloads(cursor: Any, collection: str, where: str = "", params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row[0]) for row in _rows(
        cursor,
        f"SELECT payload FROM wang_knowledge.objects WHERE collection = %s AND {_ACTIVE} {where} ORDER BY object_id",
        (collection, *params),
    )]


def _source_titles(cursor: Any, source_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = sorted(set(source_ids))
    if not ids:
        return {}
    return {
        row[0]: {"source_id": row[0], "title": row[1] or row[0], "source_type": row[2]}
        for row in _rows(cursor, """
            SELECT object_id, payload->>'title', payload->>'source_type' FROM wang_knowledge.objects
            WHERE collection = 'source_documents' AND object_id = ANY(%s)""", (ids,))
    }


def components(node_ids: Iterable[str], edges: Iterable[tuple[str, str]]) -> dict[str, int]:
    """Connected-component number per node; isolated nodes get none."""

    neighbours: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        neighbours[a].add(b)
        neighbours[b].add(a)
    number: dict[str, int] = {}
    for start in sorted(node_ids):
        if start in number or start not in neighbours:
            continue
        group = len(set(number.values()))
        stack = [start]
        number[start] = group
        while stack:
            for nxt in neighbours[stack.pop()]:
                if nxt not in number:
                    number[nxt] = group
                    stack.append(nxt)
    return number


def viewpoint_graph(store: PostgresKnowledgeStore) -> dict[str, Any]:
    with store.connect() as conn, conn.cursor() as cursor:
        viewpoints = _payloads(cursor, "canonical_viewpoints", "AND payload->>'identity_status' = 'active'")
        revisions = {r["viewpoint_revision_id"]: r for r in _payloads(cursor, "viewpoint_revisions")}
        relations = _payloads(cursor, "viewpoint_relations")
        claim_sources = _rows(cursor, _CLAIM_SOURCES.format(viewpoint_filter=""))
        attestation_sources = _rows(cursor, f"""
            SELECT r.payload->>'conclusion_viewpoint_id', a.payload->>'source_id'
            FROM wang_knowledge.objects a JOIN wang_knowledge.objects r
              ON r.collection = 'argument_routes' AND r.object_id = a.payload->>'argument_route_id'
            WHERE a.collection = 'argument_route_attestations' AND a.retired_at IS NULL
              AND coalesce(a.payload->>'effective_state', 'active') = 'active'""")
    sermons: dict[str, set[str]] = defaultdict(set)
    claims: dict[str, set[str]] = defaultdict(set)
    for viewpoint_id, claim_id, source_id in claim_sources:
        claims[viewpoint_id].add(claim_id)
        if source_id:
            sermons[viewpoint_id].add(source_id)
    for viewpoint_id, source_id in attestation_sources:
        if viewpoint_id and source_id:
            sermons[viewpoint_id].add(source_id)

    ids = {v["viewpoint_id"] for v in viewpoints}
    edges = [
        {
            "id": r["viewpoint_relation_id"],
            "source": r["source_viewpoint_id"],
            "target": r["target_viewpoint_id"],
            "type": r["relation_type"],
            "label": RELATION_LABELS.get(r["relation_type"], r["relation_type"]),
        }
        for r in relations
        if r["source_viewpoint_id"] in ids and r["target_viewpoint_id"] in ids
    ]
    group = components(ids, ((e["source"], e["target"]) for e in edges))
    nodes = []
    for viewpoint in viewpoints:
        viewpoint_id = viewpoint["viewpoint_id"]
        revision = revisions.get(viewpoint.get("current_revision_id")) or {}
        nodes.append({
            "id": viewpoint_id,
            "text": revision.get("core_proposition") or "",
            "scripture": (revision.get("scope") or {}).get("scripture_scope") or [],
            "sermons": len(sermons[viewpoint_id]),
            "claims": len(claims[viewpoint_id]),
            "group": group.get(viewpoint_id),
        })
    return {
        "nodes": nodes,
        "edges": edges,
        "relation_labels": RELATION_LABELS,
        "stats": {
            "viewpoints": len(nodes),
            "relations": len(edges),
            "connected": len(group),
            "isolated": len(nodes) - len(group),
            "groups": len(set(group.values())),
        },
    }


def viewpoint_detail(store: PostgresKnowledgeStore, viewpoint_id: str) -> dict[str, Any] | None:
    with store.connect() as conn, conn.cursor() as cursor:
        found = _payloads(cursor, "canonical_viewpoints", "AND object_id = %s", (viewpoint_id,))
        if not found:
            return None
        viewpoint = found[0]
        history = _payloads(cursor, "viewpoint_revisions", "AND payload->>'viewpoint_id' = %s", (viewpoint_id,))
        current = next((r for r in history if r["viewpoint_revision_id"] == viewpoint.get("current_revision_id")), {})
        relations = _payloads(cursor, "viewpoint_relations",
                              "AND (payload->>'source_viewpoint_id' = %s OR payload->>'target_viewpoint_id' = %s)",
                              (viewpoint_id, viewpoint_id))
        others = {r["source_viewpoint_id"] for r in relations} | {r["target_viewpoint_id"] for r in relations}
        others.discard(viewpoint_id)
        other_text = {
            row[0]: row[1] for row in _rows(cursor, """
                SELECT v.object_id, r.payload->>'core_proposition'
                FROM wang_knowledge.objects v JOIN wang_knowledge.objects r
                  ON r.collection = 'viewpoint_revisions' AND r.object_id = v.payload->>'current_revision_id'
                WHERE v.collection = 'canonical_viewpoints' AND v.object_id = ANY(%s)""", (sorted(others),))
        }
        routes = _payloads(cursor, "argument_routes", "AND payload->>'conclusion_viewpoint_id' = %s", (viewpoint_id,))
        route_revisions = {
            r["argument_route_revision_id"]: r for r in _payloads(
                cursor, "argument_route_revisions", "AND payload->>'argument_route_id' = ANY(%s)",
                ([r["argument_route_id"] for r in routes],))
        } if routes else {}
        attestations = _payloads(cursor, "argument_route_attestations", "AND payload->>'argument_route_id' = ANY(%s)",
                                 ([r["argument_route_id"] for r in routes],)) if routes else []
        observations = _payloads(cursor, "viewpoint_observation_supports", "AND payload->>'viewpoint_id' = %s",
                                 (viewpoint_id,))
        claim_sources = _rows(cursor, _CLAIM_SOURCES.format(viewpoint_filter="AND payload->>'viewpoint_id' = %s"),
                              (viewpoint_id,))
        claim_ids = sorted({row[1] for row in claim_sources})
        statements = {
            row[0]: row[1] for row in _rows(cursor, """
                SELECT object_id, payload->>'statement' FROM wang_knowledge.objects
                WHERE collection = 'claims' AND object_id = ANY(%s)""", (claim_ids,))
        } if claim_ids else {}
        titles = _source_titles(cursor, [row[2] for row in claim_sources if row[2]]
                                + [a["source_id"] for a in attestations]
                                + [o["source_id"] for o in observations if o.get("source_id")])

    claim_source: dict[str, set[str]] = defaultdict(set)
    for _, claim_id, source_id in claim_sources:
        if source_id:
            claim_source[claim_id].add(source_id)
    by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attestation in attestations:
        by_route[attestation["argument_route_id"]].append(attestation)
    return {
        "id": viewpoint_id,
        "text": current.get("core_proposition") or "",
        "summary": viewpoint.get("summary"),
        "scripture": (current.get("scope") or {}).get("scripture_scope") or [],
        "routes": [
            {
                "id": route["argument_route_id"],
                "label": (route_revisions.get(route.get("current_revision_id")) or {}).get("route_label"),
                "steps": [
                    {"key": node.get("route_step_key"), "role": node.get("role"),
                     "text": node.get("normalized_proposition")}
                    for node in (route_revisions.get(route.get("current_revision_id")) or {}).get("ordered_inference_nodes") or []
                ],
                "sermons": [
                    {**titles.get(a["source_id"], {"source_id": a["source_id"], "title": a["source_id"]}),
                     "completeness": a.get("completeness")}
                    for a in by_route[route["argument_route_id"]]
                ],
            }
            for route in routes
        ],
        "observations": [
            {
                "summary": o.get("observation_summary"),
                "supports": o.get("supports"),
                "scope_note": o.get("support_scope_note"),
                "sermon": titles.get(o.get("source_id"), {"source_id": o.get("source_id"), "title": o.get("source_id")}),
            }
            for o in observations
        ],
        "claims": [
            {
                "id": claim_id,
                "text": statements.get(claim_id) or "",
                "sermons": [titles.get(s, {"source_id": s, "title": s}) for s in sorted(claim_source[claim_id])],
            }
            for claim_id in claim_ids
        ],
        "relations": [
            {
                "type": r["relation_type"],
                "label": RELATION_LABELS.get(r["relation_type"], r["relation_type"]),
                "direction": "out" if r["source_viewpoint_id"] == viewpoint_id else "in",
                "other_id": r["target_viewpoint_id"] if r["source_viewpoint_id"] == viewpoint_id else r["source_viewpoint_id"],
                "other_text": other_text.get(
                    r["target_viewpoint_id"] if r["source_viewpoint_id"] == viewpoint_id else r["source_viewpoint_id"], ""),
                "reason": r.get("reason"),
            }
            for r in relations
        ],
        "history": [
            {"revision": r.get("revision_number"), "text": r.get("core_proposition"),
             "approved_at": r.get("approved_at"), "current": r["viewpoint_revision_id"] == viewpoint.get("current_revision_id")}
            for r in sorted(history, key=lambda r: (r.get("revision_number") or 0, str(r.get("approved_at") or "")))
        ],
    }


@router.get("/viewpoints")
def viewpoints() -> dict[str, Any]:
    return viewpoint_graph(_store())


@router.get("/viewpoints/{viewpoint_id}")
def viewpoint(viewpoint_id: str) -> dict[str, Any]:
    detail = viewpoint_detail(_store(), viewpoint_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="没有这个观点")
    return detail
