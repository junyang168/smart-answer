"""Read-only, snapshot-bound projections for the CanonicalViewpoint workbench.

The browser receives one compiled contract.  It never joins repository
collections or decides whether a Claim is a member, relation, route, or
exception.  Until a first-class RegistrySnapshot record lands, the projection
snapshot id is content-addressed over the exact records returned here.
"""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from .knowledge_models import (
    CanonicalViewpointRecord,
    ClaimRecord,
    ViewpointClaimLinkRecord,
    ViewpointCoverageSnapshotRecord,
    ViewpointQualityReportRecord,
    ViewpointResolutionLedgerRecord,
    ViewpointRevisionRecord,
)
from .viewpoint_foundation import semantic_record_sha, sha256_json
from .viewpoint_resolution import ViewpointExceptionQueueArtifact
from .viewpoint_runtime_projection import ViewpointRuntimeCompiler


MEMBERSHIP_TYPES = frozenset({"equivalent_full", "equivalent_component"})
RELATION_TYPES = frozenset(
    {"supports", "extends", "qualifies", "applies", "tension_evidence", "superseding_evidence"}
)


class AdminViewpointProjectionError(ValueError):
    pass


def _dump(item: Any) -> dict[str, Any]:
    return item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)


def _id(item: Any, field: str) -> str:
    return str(getattr(item, field))


def _cursor(snapshot_id: str, offset: int) -> str:
    raw = json.dumps({"snapshot": snapshot_id, "offset": offset}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _read_cursor(value: str | None, snapshot_id: str) -> int:
    if not value:
        return 0
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload != {"snapshot": snapshot_id, "offset": int(payload["offset"])}:
            raise ValueError
        if payload["offset"] < 0:
            raise ValueError
        return int(payload["offset"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AdminViewpointProjectionError("cursor does not belong to this projection snapshot") from exc


class AdminViewpointProjectionCompiler:
    """Compile repository objects into one immutable admin-facing read model."""

    def __init__(self, store: Any, exception_queue: ViewpointExceptionQueueArtifact | None = None):
        self.store = store
        self.exception_queue = exception_queue
        self.records = {
            name: list(store.list_knowledge_records(name))
            for name in (
                "source_documents",
                "source_fragments",
                "claims",
                "evidence_steps",
                "claim_relations",
                "knowledge_routes",
                "product_dependencies",
                "impact_events",
                "viewpoint_coverage_snapshots",
                "canonical_viewpoints",
                "viewpoint_revisions",
                "viewpoint_claim_links",
                "argument_routes",
                "argument_route_revisions",
                "argument_route_attestations",
                "viewpoint_relations",
                "viewpoint_resolution_ledgers",
                "viewpoint_quality_reports",
            )
        }
        self.citations = list(store.list_citations())

    def _coverage(self, requested: str | None = None) -> ViewpointCoverageSnapshotRecord | None:
        rows = sorted(
            self.records["viewpoint_coverage_snapshots"],
            key=lambda item: (item.created_at, item.coverage_snapshot_id),
        )
        if not rows:
            if requested:
                raise AdminViewpointProjectionError(f"unknown coverage snapshot: {requested}")
            return None
        if requested:
            for item in rows:
                if item.coverage_snapshot_id == requested:
                    if item.coverage_snapshot_id != rows[-1].coverage_snapshot_id:
                        raise AdminViewpointProjectionError(
                            "historical coverage exists but its registry record revisions are unavailable; current records were not mixed into it"
                        )
                    return item
            raise AdminViewpointProjectionError(f"unknown coverage snapshot: {requested}")
        return rows[-1]

    def _ledger(self, coverage_id: str | None) -> ViewpointResolutionLedgerRecord | None:
        if not coverage_id:
            return None
        rows = [
            item for item in self.records["viewpoint_resolution_ledgers"]
            if item.coverage_snapshot_id == coverage_id
        ]
        return sorted(rows, key=lambda item: (item.revision, item.resolution_ledger_id))[-1] if rows else None

    def _quality(
        self, coverage_id: str | None, ledger_id: str | None
    ) -> ViewpointQualityReportRecord | None:
        rows = [
            item for item in self.records["viewpoint_quality_reports"]
            if item.coverage_snapshot_id == coverage_id
            and (not ledger_id or item.resolution_ledger_id == ledger_id)
        ]
        return sorted(rows, key=lambda item: (item.revision, item.quality_report_id))[-1] if rows else None

    def _state(self, coverage_snapshot_id: str | None = None) -> dict[str, Any]:
        coverage = self._coverage(coverage_snapshot_id)
        ledger = self._ledger(coverage.coverage_snapshot_id if coverage else None)
        quality = self._quality(
            coverage.coverage_snapshot_id if coverage else None,
            ledger.resolution_ledger_id if ledger else None,
        )
        runtime = ViewpointRuntimeCompiler(self.records, self.citations)
        registry_snapshots = (
            runtime.compile_registry_snapshots(coverage.coverage_snapshot_id)
            if coverage else []
        )
        route_snapshots = (
            runtime.compile_route_snapshots(coverage.coverage_snapshot_id)
            if coverage else []
        )
        bound = []
        for name in (
            "source_documents",
            "source_fragments",
            "canonical_viewpoints",
            "viewpoint_revisions",
            "viewpoint_claim_links",
            "claims",
            "evidence_steps",
            "knowledge_routes",
            "argument_routes",
            "argument_route_revisions",
            "argument_route_attestations",
            "viewpoint_relations",
            "claim_relations",
            "product_dependencies",
            "impact_events",
        ):
            bound.extend({"collection": name, "id": self._record_id(name, item), "sha256": semantic_record_sha(item)} for item in self.records[name])
        for item in (coverage, ledger, quality):
            if item:
                bound.append({"collection": item.schema_version, "id": self._record_id_from_model(item), "sha256": semantic_record_sha(item)})
        bound.extend(
            {"collection": "citations", "id": item.citation_id, "sha256": sha256_json(_dump(item))}
            for item in self.citations
        )
        if self.exception_queue:
            bound.append({
                "collection": "viewpoint_exception_queue",
                "id": self.exception_queue.exception_queue_id,
                "sha256": self.exception_queue.artifact_sha256,
            })
        bound.sort(key=lambda item: (item["collection"], item["id"]))
        projection_sha = sha256_json(bound)
        registry_set_sha = sha256_json(
            [item.artifact_sha256 for item in registry_snapshots]
        )
        return {
            "coverage": coverage,
            "ledger": ledger,
            "quality": quality,
            "registry_snapshot_id": f"RGSET-{registry_set_sha[:20]}",
            "registry_snapshots": registry_snapshots,
            "route_snapshots": route_snapshots,
            "projection_sha256": projection_sha,
        }

    @staticmethod
    def _record_id_from_model(item: Any) -> str:
        for name in (
            "coverage_snapshot_id", "resolution_ledger_id", "quality_report_id",
            "viewpoint_id", "viewpoint_revision_id", "viewpoint_claim_link_id",
            "argument_route_id", "argument_route_revision_id", "argument_route_attestation_id",
            "viewpoint_relation_id",
            "claim_id", "evidence_step_id", "route_id", "claim_relation_id",
            "dependency_id", "impact_event_id", "source_id", "fragment_id",
        ):
            if hasattr(item, name):
                return str(getattr(item, name))
        raise AdminViewpointProjectionError(f"record has no stable id: {type(item).__name__}")

    @classmethod
    def _record_id(cls, collection: str, item: Any) -> str:
        fields = {
            "source_documents": "source_id", "source_fragments": "fragment_id",
            "claims": "claim_id", "evidence_steps": "evidence_step_id",
            "claim_relations": "claim_relation_id", "knowledge_routes": "route_id",
            "product_dependencies": "dependency_id", "impact_events": "impact_event_id",
            "viewpoint_coverage_snapshots": "coverage_snapshot_id",
            "canonical_viewpoints": "viewpoint_id", "viewpoint_revisions": "viewpoint_revision_id",
            "viewpoint_claim_links": "viewpoint_claim_link_id",
            "argument_routes": "argument_route_id",
            "argument_route_revisions": "argument_route_revision_id",
            "argument_route_attestations": "argument_route_attestation_id",
            "viewpoint_relations": "viewpoint_relation_id",
            "viewpoint_resolution_ledgers": "resolution_ledger_id",
            "viewpoint_quality_reports": "quality_report_id",
        }
        field = fields.get(collection)
        return str(getattr(item, field)) if field else cls._record_id_from_model(item)

    def _envelope(self, state: dict[str, Any], data: dict[str, Any], links: dict[str, str]) -> dict[str, Any]:
        coverage = state["coverage"]
        ledger = state["ledger"]
        quality = state["quality"]
        return {
            "schema_version": "wang_admin_viewpoint_projection_v1",
            "authority": {
                "kind": "canonical_viewpoint_registry",
                "projection": "AdminViewpointProjection",
                "representation": "editorial_normalization_not_direct_quote",
                "read_only": True,
            },
            "as_of": {
                "registry_snapshot_id": state["registry_snapshot_id"],
                "coverage_snapshot_id": coverage.coverage_snapshot_id if coverage else None,
                "coverage_status": coverage.coverage_status if coverage else "unavailable",
                "resolution_ledger_id": ledger.resolution_ledger_id if ledger else None,
                "resolution_status": ledger.coverage_status if ledger else "unavailable",
                "quality_report_id": quality.quality_report_id if quality else None,
                "quality_decision": quality.eligibility_decision if quality else "unavailable",
            },
            "projection_sha256": state["projection_sha256"],
            "links": links,
            "data": data,
        }

    def overview(self, coverage_snapshot_id: str | None = None) -> dict[str, Any]:
        state = self._state(coverage_snapshot_id)
        coverage = state["coverage"]
        ledger = state["ledger"]
        active = [item for item in self.records["canonical_viewpoints"] if item.identity_status == "active"]
        affected = {
            (item.consumer_kind, item.consumer_id)
            for item in self.records["product_dependencies"]
            if item.status != "current"
        }
        return self._envelope(
            state,
            {
                "source_coverage": {
                    "covered": sum("viewpoint_reviewed" in item.roles for item in coverage.sources) if coverage else None,
                    "total": len(coverage.sources) if coverage else None,
                    "status": coverage.coverage_status if coverage else "unavailable",
                },
                "claim_resolution": _dump(ledger.statistics) if ledger else None,
                "active_viewpoints": len(active),
                "exceptions": len(self.exception_queue.bundles) if self.exception_queue else 0,
                "affected_products": len(affected),
                "quality_dimensions": [_dump(item) for item in state["quality"].dimensions] if state["quality"] else [],
            },
            {
                "viewpoints": "/admin/wang/viewpoints",
                "exceptions": "/admin/wang/viewpoint-exceptions",
            },
        )

    def _indexes(self) -> dict[str, Any]:
        return {
            name: {self._record_id(name, item): item for item in rows}
            for name, rows in self.records.items()
        }

    def list_viewpoints(
        self,
        *,
        coverage_snapshot_id: str | None = None,
        q: str | None = None,
        topic_id: str | None = None,
        scripture: str | None = None,
        review_status: str | None = None,
        impact_only: bool = False,
        cursor: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        state = self._state(coverage_snapshot_id)
        idx = self._indexes()
        revisions = idx["viewpoint_revisions"]
        claims: dict[str, ClaimRecord] = idx["claims"]
        links_by_viewpoint: dict[str, list[ViewpointClaimLinkRecord]] = defaultdict(list)
        for link in self.records["viewpoint_claim_links"]:
            if link.effective_state == "active":
                links_by_viewpoint[link.viewpoint_id].append(link)
        deps_by_claim: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for dep in self.records["product_dependencies"]:
            deps_by_claim[dep.claim_id].add((dep.consumer_kind, dep.consumer_id))
        sources_by_claim = self._source_ids_by_claim()
        rows: list[dict[str, Any]] = []
        for viewpoint in self.records["canonical_viewpoints"]:
            revision = revisions.get(viewpoint.current_revision_id)
            if not revision:
                continue
            links = [
                item for item in links_by_viewpoint.get(viewpoint.viewpoint_id, [])
                if item.validated_against_viewpoint_revision_id == viewpoint.current_revision_id
            ]
            member_links = [item for item in links if item.link_type in MEMBERSHIP_TYPES]
            related_links = [item for item in links if item.link_type in RELATION_TYPES]
            member_claims = [claims[item.claim_id] for item in member_links if item.claim_id in claims]
            haystack = " ".join([viewpoint.viewpoint_id, revision.core_proposition, *revision.editorial_aliases]).casefold()
            if q and q.casefold() not in haystack:
                continue
            if review_status and revision.review_status != review_status:
                continue
            if topic_id and not any(topic_id in item.topic_ids for item in member_claims):
                continue
            if scripture and not any(
                scripture.casefold() in json.dumps(item.scripture_refs, ensure_ascii=False).casefold()
                for item in member_claims
            ):
                continue
            claim_ids = {item.claim_id for item in member_links}
            source_ids = set().union(*(sources_by_claim.get(claim_id, set()) for claim_id in claim_ids)) if claim_ids else set()
            route_count = sum(
                item.conclusion_viewpoint_id == viewpoint.viewpoint_id
                for item in self.records["argument_routes"]
                if item.route_status == "active"
            )
            viewpoint_relations = [
                item for item in self.records["viewpoint_relations"]
                if item.effective_state == "active"
                and viewpoint.viewpoint_id in {item.source_viewpoint_id, item.target_viewpoint_id}
            ]
            products = set().union(*(deps_by_claim.get(claim_id, set()) for claim_id in claim_ids)) if claim_ids else set()
            if impact_only and not products:
                continue
            rows.append({
                "viewpoint_id": viewpoint.viewpoint_id,
                "viewpoint_revision_id": revision.viewpoint_revision_id,
                "core_proposition": revision.core_proposition,
                "wording_label": "编辑归一化（非逐字引文）",
                "identity_status": viewpoint.identity_status,
                "review_status": revision.review_status,
                "approval_basis": self._approval_basis(revision),
                "scripture_scope": revision.scope.scripture_scope,
                "topic_ids": sorted({topic for item in member_claims for topic in item.topic_ids}),
                "counts": {
                    "members": len(member_links), "sources": len(source_ids),
                    "routes": route_count,
                    "tensions": sum(item.relation_type == "tensions_with" for item in viewpoint_relations),
                    "related": len(viewpoint_relations),
                },
                "product_impact_count": len(products),
                "quality_blocked": state["quality"].eligibility_decision == "fail" if state["quality"] else None,
            })
        rows.sort(key=lambda item: (item["core_proposition"], item["viewpoint_id"]))
        offset = _read_cursor(cursor, state["registry_snapshot_id"])
        page = rows[offset:offset + limit]
        next_cursor = _cursor(state["registry_snapshot_id"], offset + limit) if offset + limit < len(rows) else None
        return self._envelope(
            state,
            {"items": page, "total": len(rows), "next_cursor": next_cursor},
            {"self": "/admin/wang/viewpoints", "exceptions": "/admin/wang/viewpoint-exceptions"},
        )

    @staticmethod
    def _approval_basis(revision: ViewpointRevisionRecord) -> str:
        if revision.review_status == "system_approved":
            return "dual_model_consensus"
        if revision.review_status in {"human_approved", "approved"}:
            return "human_exception_review"
        return "not_approved"

    def _source_ids_by_claim(self) -> dict[str, set[str]]:
        fragments = {item.fragment_id: item for item in self.records["source_fragments"]}
        evidence = {item.evidence_step_id: item for item in self.records["evidence_steps"]}
        result: dict[str, set[str]] = defaultdict(set)
        for claim in self.records["claims"]:
            for evidence_id in claim.evidence_step_ids:
                step = evidence.get(evidence_id)
                fragment = fragments.get(step.source_fragment_id) if step and step.source_fragment_id else None
                if fragment:
                    result[claim.claim_id].add(fragment.source_id)
        return result

    def detail(
        self, viewpoint_id: str, *, coverage_snapshot_id: str | None = None,
        registry_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        state = self._state(coverage_snapshot_id)
        if registry_snapshot_id and registry_snapshot_id != state["registry_snapshot_id"]:
            raise AdminViewpointProjectionError("registry snapshot is stale or unknown; no current redirect was followed")
        idx = self._indexes()
        viewpoint: CanonicalViewpointRecord | None = idx["canonical_viewpoints"].get(viewpoint_id)
        if not viewpoint:
            raise AdminViewpointProjectionError(f"unknown viewpoint: {viewpoint_id}")
        revision: ViewpointRevisionRecord | None = idx["viewpoint_revisions"].get(viewpoint.current_revision_id)
        if not revision:
            raise AdminViewpointProjectionError(f"missing current revision for viewpoint: {viewpoint_id}")
        links = sorted(
            [
                item for item in self.records["viewpoint_claim_links"]
                if item.viewpoint_id == viewpoint_id
                and item.effective_state == "active"
                and item.validated_against_viewpoint_revision_id == viewpoint.current_revision_id
            ],
            key=lambda item: (item.link_type, item.claim_id),
        )
        member_claim_ids = {item.claim_id for item in links if item.link_type in MEMBERSHIP_TYPES}
        claims = idx["claims"]
        evidence = idx["evidence_steps"]
        fragments = idx["source_fragments"]
        sources = idx["source_documents"]
        citations = {item.citation_id: item for item in self.citations}
        members = []
        for link in [item for item in links if item.link_type in MEMBERSHIP_TYPES]:
            claim = claims.get(link.claim_id)
            if not claim:
                continue
            steps = []
            for evidence_id in claim.evidence_step_ids:
                step = evidence.get(evidence_id)
                fragment = fragments.get(step.source_fragment_id) if step and step.source_fragment_id else None
                source = sources.get(fragment.source_id) if fragment else None
                steps.append({
                    "evidence_step": _dump(step) if step else None,
                    "source_fragment": _dump(fragment) if fragment else None,
                    "source": _dump(source) if source else None,
                    "citations": [_dump(citations[cid]) for cid in (step.citation_ids if step else []) if cid in citations],
                    "locator": {
                        "source_url": (fragment.source_url if fragment else None) or (source.source_url if source else None),
                        "paragraph_key": fragment.paragraph_key if fragment else None,
                        "media_time": fragment.media_time if fragment else None,
                    },
                })
            members.append({"link": _dump(link), "claim": _dump(claim), "evidence": steps})
        relations = []
        for relation in sorted(
            [
                item for item in self.records["viewpoint_relations"]
                if item.effective_state == "active"
                and viewpoint_id in {item.source_viewpoint_id, item.target_viewpoint_id}
            ],
            key=lambda item: item.viewpoint_relation_id,
        ):
            relations.append({
                "relation_id": relation.viewpoint_relation_id,
                "relation_type": relation.relation_type,
                "from_viewpoint_id": relation.source_viewpoint_id,
                "to_viewpoint_id": relation.target_viewpoint_id,
                "claim_id": relation.supporting_claim_ids[0] if relation.supporting_claim_ids else None,
                "claim_statement": relation.reason,
                "supporting_relation_ids": relation.supporting_claim_relation_ids,
                "review_status": relation.review_status,
            })
        routes = []
        route_revisions = idx["argument_route_revisions"]
        route_attestations = self.records["argument_route_attestations"]
        snapshots = {
            item.argument_route_id: item
            for item in state["route_snapshots"]
            if item.conclusion_viewpoint_id == viewpoint_id
        }
        for route in sorted(
            [item for item in self.records["argument_routes"] if item.conclusion_viewpoint_id == viewpoint_id and item.route_status == "active"],
            key=lambda item: item.argument_route_id,
        ):
            route_revision = route_revisions.get(route.current_revision_id)
            snapshot = snapshots.get(route.argument_route_id)
            attestations = sorted(
                [
                    item for item in route_attestations
                    if snapshot and item.argument_route_attestation_id in snapshot.active_attestation_ids
                ],
                key=lambda item: item.argument_route_attestation_id,
            )
            evidence_step_ids = [
                step_id for item in attestations for step_id in item.ordered_evidence_step_ids
            ]
            routes.append({
                "route_id": route.argument_route_id,
                "route_type": route_revision.route_label if route_revision else "推理路线",
                "claim_id": attestations[0].claim_id if attestations else None,
                "evidence_step_ids": evidence_step_ids,
                "route": _dump(route),
                "revision": _dump(route_revision) if route_revision else None,
                "attestations": [_dump(item) for item in attestations],
                "snapshot": snapshot.model_dump(mode="json") if snapshot else None,
            })
        revisions = sorted(
            [_dump(item) for item in self.records["viewpoint_revisions"] if item.viewpoint_id == viewpoint_id],
            key=lambda item: item["revision_number"], reverse=True,
        )
        impact = self._impact(member_claim_ids, viewpoint.current_revision_id)
        graph = {
            "nodes": [
                {"id": viewpoint_id, "kind": "viewpoint", "label": revision.core_proposition},
                *[{"id": item["claim"]["claim_id"], "kind": "member", "label": item["claim"]["statement"]} for item in members],
                *[{"id": item["route_id"], "kind": "route", "label": item.get("route_type") or "推理路线"} for item in routes],
                *[{"id": item["to_viewpoint_id"], "kind": "related_viewpoint", "label": item["to_viewpoint_id"]} for item in relations if item["to_viewpoint_id"]],
            ],
            "edges": [
                *[{"from": item["claim"]["claim_id"], "to": viewpoint_id, "kind": item["link"]["link_type"]} for item in members],
                *[{"from": item["route_id"], "to": viewpoint_id, "kind": "argument_route"} for item in routes],
                *[{"from": viewpoint_id, "to": item["to_viewpoint_id"], "kind": item["relation_type"]} for item in relations if item["to_viewpoint_id"]],
            ],
        }
        return self._envelope(
            state,
            {
                "viewpoint": _dump(viewpoint), "revision": _dump(revision),
                "members": members, "routes": routes, "relations": relations,
                "history": revisions, "impact": impact, "graph": graph,
                "quality": _dump(state["quality"]) if state["quality"] else None,
            },
            {"self": f"/admin/wang/viewpoints/{viewpoint_id}", "collection": "/admin/wang/viewpoints"},
        )

    def _impact(self, claim_ids: set[str], viewpoint_revision_id: str) -> dict[str, Any]:
        dependencies = [
            item for item in self.records["product_dependencies"]
            if item.claim_id in claim_ids or viewpoint_revision_id in item.viewpoint_revision_ids
        ]
        dep_ids = {item.dependency_id for item in dependencies}
        events = [item for item in self.records["impact_events"] if dep_ids.intersection(item.affected_dependency_ids)]
        return {"dependencies": [_dump(item) for item in dependencies], "events": [_dump(item) for item in events]}

    def lineage(self, viewpoint_id: str, **kwargs: Any) -> dict[str, Any]:
        detail = self.detail(viewpoint_id, **kwargs)
        detail["data"] = {"viewpoint": detail["data"]["viewpoint"], "history": detail["data"]["history"]}
        return detail

    def impact(self, viewpoint_id: str, **kwargs: Any) -> dict[str, Any]:
        detail = self.detail(viewpoint_id, **kwargs)
        detail["data"] = {"viewpoint_id": viewpoint_id, **detail["data"]["impact"]}
        return detail

    def exceptions(self, *, cursor: str | None = None, limit: int = 25) -> dict[str, Any]:
        state = self._state()
        bundles = self.exception_queue.bundles if self.exception_queue else []
        offset = _read_cursor(cursor, state["registry_snapshot_id"])
        page = bundles[offset:offset + limit]
        next_cursor = _cursor(state["registry_snapshot_id"], offset + limit) if offset + limit < len(bundles) else None
        items = [{
            "exception_bundle_id": item.exception_bundle_id,
            "candidate_id": item.candidate_id,
            "priority": item.priority,
            "consumer_impact": item.consumer_impact,
            "blocker_codes": item.blocker_codes,
            "remaining_findings": item.remaining_findings,
            "claim_count": len(item.claims),
        } for item in page]
        return self._envelope(
            state,
            {"items": items, "total": len(bundles), "next_cursor": next_cursor},
            {"self": "/admin/wang/viewpoint-exceptions", "viewpoints": "/admin/wang/viewpoints"},
        )

    def exception_detail(self, bundle_id: str) -> dict[str, Any]:
        state = self._state()
        if not self.exception_queue:
            raise AdminViewpointProjectionError(f"unknown exception bundle: {bundle_id}")
        bundle = next((item for item in self.exception_queue.bundles if item.exception_bundle_id == bundle_id), None)
        if not bundle:
            raise AdminViewpointProjectionError(f"unknown exception bundle: {bundle_id}")
        return self._envelope(
            state,
            _dump(bundle),
            {"self": f"/admin/wang/viewpoint-exceptions/{bundle_id}", "collection": "/admin/wang/viewpoint-exceptions"},
        )
