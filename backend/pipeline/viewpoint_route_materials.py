"""Build a Route input cut from one current CVP, across its source corpus."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.api.canonical_repository.knowledge_models import (
    CanonicalViewpointRecord,
    ClaimRecord,
    EvidenceStepRecord,
    SourceFragmentRecord,
    ViewpointClaimLinkRecord,
    ViewpointRevisionRecord,
    evidence_fragment_ids,
)
from backend.api.canonical_repository.models import Citation
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim, compile_review_claim
from backend.pipeline.cvp_materials import _source_packet
from backend.pipeline.viewpoint_evidence_source_context import load_context_source

ROUTE_MATERIAL_VERSION = "wang_cvp_route_material_v1"


def _read_exact(store: Any, keys: set[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    rows = store.read_current_records(keys)
    if set(rows) != keys or any(row["retired"] for row in rows.values()):
        raise ValueError("Route input has a missing or retired current dependency")
    if any(record_content_sha(row["payload"]) != row["content_sha256"] for row in rows.values()):
        raise ValueError("Route input has a current dependency SHA mismatch")
    return rows


def prepare_cvp_route_materials(
    store: Any,
    viewpoint_id: str,
    *,
    source_loader: Callable[[dict[str, Any]], Any] = load_context_source,
) -> dict[str, Any]:
    """Close active links, their evidence, and related physical source sections.

    The previous Claim production scope is intentionally absent. Every active
    connection of this CVP must be present, even when it came from another
    sermon or an earlier CVP production batch.
    """

    head_row = _read_exact(store, {("canonical_viewpoints", viewpoint_id)})
    viewpoint = CanonicalViewpointRecord.model_validate(
        head_row["canonical_viewpoints", viewpoint_id]["payload"]
    )
    if viewpoint.identity_status != "active":
        raise ValueError("Route work requires an active current CVP")
    revision_id = viewpoint.current_revision_id
    revision_rows = _read_exact(store, {("viewpoint_revisions", revision_id)})
    revision = ViewpointRevisionRecord.model_validate(
        revision_rows["viewpoint_revisions", revision_id]["payload"]
    )
    if revision.viewpoint_id != viewpoint_id:
        raise ValueError("Route CVP head points at another viewpoint")

    route_rows = store.read_cvp_route_records(viewpoint_id)
    if any(row["retired"] or record_content_sha(row["payload"]) != row["content_sha256"]
           for row in route_rows.values()):
        raise ValueError("Route baseline has a retired or inconsistent record")
    existing_routes = []
    for (collection, route_id), row in sorted(route_rows.items()):
        if collection != "argument_routes" or row["payload"].get("route_status") != "active":
            continue
        route = row["payload"]
        revision_row = route_rows.get(("argument_route_revisions", route["current_revision_id"]))
        if revision_row is None:
            raise ValueError("Route baseline has no current revision")
        route_revision = revision_row["payload"]
        if (route_revision["argument_route_id"] != route_id
                or route["conclusion_viewpoint_id"] != viewpoint_id):
            raise ValueError("Route baseline points at another CVP")
        existing_routes.append({
            "argument_route_id": route_id,
            "route_revision_id": route["current_revision_id"],
            "conclusion_viewpoint_revision_id": route_revision[
                "validated_against_conclusion_viewpoint_revision_id"
            ],
            "route": route,
            "revision": route_revision,
        })
    existing_attestations = [
        row["payload"] for (collection, _), row in sorted(route_rows.items())
        if collection == "argument_route_attestations"
    ]

    link_rows = {
        key: row for key, row in store.read_cvp_links(viewpoint_ids=[viewpoint_id]).items()
        if row["payload"].get("viewpoint_id") == viewpoint_id
        and row["payload"].get("effective_state") == "active"
    }
    if not link_rows:
        raise ValueError("Route CVP has no active Claim connections")
    links = [ViewpointClaimLinkRecord.model_validate(row["payload"])
             for _, row in sorted(link_rows.items())]
    if any(link.validated_against_viewpoint_revision_id != revision_id for link in links):
        raise ValueError("Route CVP has an active connection pinned to another revision")
    claim_ids = {link.claim_id for link in links}
    claim_rows = _read_exact(store, {("claims", claim_id) for claim_id in claim_ids})
    claims = {claim_id: ClaimRecord.model_validate(claim_rows["claims", claim_id]["payload"])
              for claim_id in claim_ids}
    if any(claims[link.claim_id].revision != link.pinned_claim_revision for link in links):
        raise ValueError("Route CVP has a stale linked Claim revision")

    evidence_ids = {eid for claim in claims.values() for eid in claim.evidence_step_ids}
    evidence_rows = _read_exact(store, {("evidence_steps", eid) for eid in evidence_ids})
    evidence = {eid: EvidenceStepRecord.model_validate(evidence_rows["evidence_steps", eid]["payload"])
                for eid in evidence_ids}
    fragment_ids = {fid for step in evidence.values() for fid in evidence_fragment_ids(step)}
    fragment_rows = _read_exact(store, {("source_fragments", fid) for fid in fragment_ids})
    fragments = {fid: SourceFragmentRecord.model_validate(fragment_rows["source_fragments", fid]["payload"])
                 for fid in fragment_ids}
    source_ids = {fragment.source_id for fragment in fragments.values()}
    source_rows = _read_exact(store, {("source_documents", sid) for sid in source_ids})
    sources = {sid: source_rows["source_documents", sid]["payload"] for sid in source_ids}
    citation_ids = {
        str(cid) for step in evidence.values() for cid in step.citation_ids
    } | {str(fragment.citation_id) for fragment in fragments.values() if fragment.citation_id}
    citation_rows = _read_exact(store, {("citations", cid) for cid in citation_ids}) if citation_ids else {}
    citations = {cid: Citation.model_validate(citation_rows["citations", cid]["payload"])
                 for cid in citation_ids}
    source_shas = {sid: str(source.get("source_sha256") or "") for sid, source in sources.items()}

    review_claims: list[ReviewClaim] = []
    unavailable_evidence = []
    fragments_by_source: dict[str, dict[str, Any]] = {sid: {} for sid in source_ids}
    for claim_id in sorted(claim_ids):
        unavailable = frozenset(eid for eid in claims[claim_id].evidence_step_ids
            if evidence[eid].support_eligibility == 'withheld_missing_anchor')
        review, findings = compile_review_claim(
            claim=claims[claim_id],
            evidence_index=evidence,
            fragment_index=fragments,
            citation_index=citations,
            coverage_source_sha256=source_shas,
            attestation_sha=None,
            active_full_viewpoint_id=viewpoint_id if any(
                link.claim_id == claim_id and link.link_type == "equivalent_full"
                for link in links
            ) else None,
            unavailable_evidence_ids=unavailable,
        )
        if review is None or findings:
            raise ValueError(f"{claim_id}: Route evidence incomplete: {'; '.join(findings)}")
        review_claims.append(review)
        unavailable_evidence.extend({
            'claim_id': claim_id, 'evidence_step_id': eid,
            'support_eligibility': evidence[eid].support_eligibility,
            'anchor_quality': evidence[eid].anchor_quality,
            'usable_for_route_binding': False,
        } for eid in sorted(unavailable))
        for item in review.evidence:
            fragments_by_source[item.source_id][item.source_fragment_id] = (
                fragment_rows["source_fragments", item.source_fragment_id]["payload"]
            )

    section_packets = []
    physical_sources = []
    for source_id in sorted(source_ids):
        loaded = source_loader(sources[source_id])
        section_packets.append(_source_packet(
            source_id,
            sources[source_id],
            loaded,
            [fragments_by_source[source_id][fid] for fid in sorted(fragments_by_source[source_id])],
        ))
        physical_sources.append({"source_id": source_id, "path": str(loaded[0]),
                                 "file_sha256": loaded[1]})

    records = {**head_row, **revision_rows, **route_rows, **link_rows, **claim_rows,
               **evidence_rows, **fragment_rows, **source_rows, **citation_rows}
    pins = {(collection, object_id): (row["revision"], row["content_sha256"], False)
            for (collection, object_id), row in records.items()}
    store.verify_current_read_pins(pins)
    body = {
        "schema_version": ROUTE_MATERIAL_VERSION,
        "viewpoint_id": viewpoint_id,
        "viewpoint_revision_id": revision_id,
        "viewpoint": viewpoint.model_dump(mode="json"),
        "revision": revision.model_dump(mode="json"),
        "links": [link.model_dump(mode="json") for link in links],
        "claims": [claim.model_dump(mode="json") for claim in review_claims],
        "source_sections": section_packets,
        "existing_routes": existing_routes,
        "existing_attestations": existing_attestations,
        "record_pins": [
            {"collection": collection, "object_id": object_id,
             "revision": row["revision"], "content_sha256": row["content_sha256"]}
            for (collection, object_id), row in sorted(records.items())
        ],
        "physical_sources": physical_sources,
    }
    if unavailable_evidence:
        body['unavailable_evidence'] = unavailable_evidence
    return body | {"artifact_sha256": sha256_json(body)}


def verify_cvp_route_materials(store: Any, materials: dict[str, Any]) -> None:
    """Fence a model result against current Registry and physical source heads."""

    if materials.get("schema_version") != ROUTE_MATERIAL_VERSION or materials.get(
        "artifact_sha256"
    ) != sha256_json({key: value for key, value in materials.items() if key != "artifact_sha256"}):
        raise ValueError("Route material artifact changed")
    store.verify_current_read_pins({
        (row["collection"], row["object_id"]):
            (row["revision"], row["content_sha256"], False)
        for row in materials["record_pins"]
    })
    current_links = store.read_cvp_links(viewpoint_ids=[materials["viewpoint_id"]])
    active_ids = {
        key[1] for key, row in current_links.items()
        if row["payload"].get("viewpoint_id") == materials["viewpoint_id"]
        and row["payload"].get("effective_state") == "active"
    }
    expected_ids = {row["viewpoint_claim_link_id"] for row in materials["links"]}
    if active_ids != expected_ids:
        raise ValueError("Route CVP membership changed after material preparation")
    current_routes = store.read_cvp_route_records(materials["viewpoint_id"])
    route_keys = {key for key in current_routes if key[0] in {
        "argument_routes", "argument_route_revisions", "argument_route_attestations"
    }}
    pinned_route_keys = {(row["collection"], row["object_id"])
                         for row in materials["record_pins"] if row["collection"] in {
                             "argument_routes", "argument_route_revisions", "argument_route_attestations"
                         }}
    if route_keys != pinned_route_keys:
        raise ValueError("Route baseline changed after material preparation")
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    for source in materials["physical_sources"]:
        verify_current_source_binding({"path": source["path"],
                                       "file_sha256": source["file_sha256"]})
