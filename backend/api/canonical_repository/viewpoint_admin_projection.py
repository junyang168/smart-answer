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
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .knowledge_models import (
    CanonicalViewpointRecord,
    ClaimRecord,
    ViewpointClaimLinkRecord,
    ViewpointAtomicCoverageSnapshotRecord,
    ViewpointAtomicQualityReportRecord,
    ViewpointAtomicResolutionLedgerRecord,
    ViewpointPropositionUnitLinkRecord,
    ViewpointPropositionUnitRecord,
    ViewpointCoverageSnapshotRecord,
    ViewpointQualityReportRecord,
    ViewpointResolutionLedgerRecord,
    ViewpointRevisionRecord,
    evidence_fragment_ids,
)
from .viewpoint_foundation import semantic_record_sha, sha256_json
from .viewpoint_resolution import ViewpointExceptionQueueArtifact
from .viewpoint_recall_blocking import ViewpointRecallBlockingArtifact
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

    def __init__(
        self,
        store: Any,
        exception_queue: ViewpointExceptionQueueArtifact | None = None,
        recall_blocking: ViewpointRecallBlockingArtifact | None = None,
    ):
        self.store = store
        self.exception_queue = exception_queue
        self.recall_blocking = recall_blocking
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
                "viewpoint_proposition_units",
                "viewpoint_proposition_unit_links",
                "viewpoint_atomic_coverage_snapshots",
                "viewpoint_atomic_resolution_ledgers",
                "viewpoint_atomic_quality_reports",
                "argument_routes",
                "argument_route_revisions",
                "argument_route_attestations",
                "viewpoint_relations",
                "viewpoint_structures",
                "viewpoint_structure_revisions",
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
        legacy_coverage = self._coverage(coverage_snapshot_id)
        legacy_ledger = self._ledger(
            legacy_coverage.coverage_snapshot_id if legacy_coverage else None
        )
        legacy_quality = self._quality(
            legacy_coverage.coverage_snapshot_id if legacy_coverage else None,
            legacy_ledger.resolution_ledger_id if legacy_ledger else None,
        )
        atomic_coverages = sorted(
            self.records["viewpoint_atomic_coverage_snapshots"],
            key=lambda item: (item.revision, item.atomic_coverage_snapshot_id),
        )
        atomic_coverage = atomic_coverages[-1] if atomic_coverages else None
        atomic_ledgers = [
            item
            for item in self.records["viewpoint_atomic_resolution_ledgers"]
            if atomic_coverage
            and item.atomic_coverage_snapshot_id
            == atomic_coverage.atomic_coverage_snapshot_id
        ]
        atomic_ledger = (
            sorted(
                atomic_ledgers,
                key=lambda item: (item.revision, item.atomic_resolution_ledger_id),
            )[-1]
            if atomic_ledgers
            else None
        )
        atomic_qualities = [
            item
            for item in self.records["viewpoint_atomic_quality_reports"]
            if atomic_ledger
            and item.atomic_resolution_ledger_id
            == atomic_ledger.atomic_resolution_ledger_id
        ]
        atomic_quality = (
            sorted(
                atomic_qualities,
                key=lambda item: (item.revision, item.atomic_quality_report_id),
            )[-1]
            if atomic_qualities
            else None
        )
        coverage = legacy_coverage or atomic_coverage
        ledger = legacy_ledger or atomic_ledger
        quality = legacy_quality or atomic_quality
        runtime = ViewpointRuntimeCompiler(
            {
                name: rows
                for name, rows in self.records.items()
                if name not in {
                    "viewpoint_proposition_units",
                    "viewpoint_proposition_unit_links",
                    "viewpoint_atomic_coverage_snapshots",
                    "viewpoint_atomic_resolution_ledgers",
                    "viewpoint_atomic_quality_reports",
                }
            },
            self.citations,
        )
        registry_snapshots = (
            runtime.compile_registry_snapshots(legacy_coverage.coverage_snapshot_id)
            if legacy_coverage else []
        )
        route_snapshots = (
            runtime.compile_route_snapshots(legacy_coverage.coverage_snapshot_id)
            if legacy_coverage else []
        )
        bound = []
        for name in (
            "source_documents",
            "source_fragments",
            "canonical_viewpoints",
            "viewpoint_revisions",
            "viewpoint_claim_links",
            "viewpoint_proposition_units",
            "viewpoint_proposition_unit_links",
            "viewpoint_atomic_coverage_snapshots",
            "viewpoint_atomic_resolution_ledgers",
            "viewpoint_atomic_quality_reports",
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
        if self.recall_blocking:
            bound.append({
                "collection": "viewpoint_recall_blocking",
                "id": self.recall_blocking.blocking_version,
                "sha256": self.recall_blocking.artifact_sha256,
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
            "atomic_coverage_snapshot_id", "atomic_resolution_ledger_id",
            "atomic_quality_report_id",
            "viewpoint_id", "viewpoint_revision_id", "viewpoint_claim_link_id",
            "proposition_unit_id", "viewpoint_proposition_unit_link_id",
            "argument_route_id", "argument_route_revision_id", "argument_route_attestation_id",
            "viewpoint_relation_id",
            "structure_id", "structure_revision_id",
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
            "viewpoint_proposition_units": "proposition_unit_id",
            "viewpoint_proposition_unit_links": "viewpoint_proposition_unit_link_id",
            "viewpoint_atomic_coverage_snapshots": "atomic_coverage_snapshot_id",
            "viewpoint_atomic_resolution_ledgers": "atomic_resolution_ledger_id",
            "viewpoint_atomic_quality_reports": "atomic_quality_report_id",
            "argument_routes": "argument_route_id",
            "argument_route_revisions": "argument_route_revision_id",
            "argument_route_attestations": "argument_route_attestation_id",
            "viewpoint_relations": "viewpoint_relation_id",
            "viewpoint_structures": "structure_id",
            "viewpoint_structure_revisions": "structure_revision_id",
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
                "coverage_snapshot_id": (
                    getattr(coverage, "coverage_snapshot_id", None)
                    or getattr(coverage, "atomic_coverage_snapshot_id", None)
                ) if coverage else None,
                "coverage_status": coverage.coverage_status if coverage else "unavailable",
                "resolution_ledger_id": (
                    getattr(ledger, "resolution_ledger_id", None)
                    or getattr(ledger, "atomic_resolution_ledger_id", None)
                ) if ledger else None,
                "resolution_status": ledger.coverage_status if ledger else "unavailable",
                "quality_report_id": (
                    getattr(quality, "quality_report_id", None)
                    or getattr(quality, "atomic_quality_report_id", None)
                ) if quality else None,
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
        atomic_coverage = isinstance(coverage, ViewpointAtomicCoverageSnapshotRecord)
        atomic_quality = isinstance(
            state["quality"], ViewpointAtomicQualityReportRecord
        )
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
                    "covered": (
                        len(coverage.source_ids)
                        if atomic_coverage
                        else sum(
                            "viewpoint_reviewed" in item.roles
                            for item in coverage.sources
                        ) if coverage else None
                    ),
                    "total": (
                        len(coverage.source_ids)
                        if atomic_coverage
                        else len(coverage.sources) if coverage else None
                    ),
                    "status": coverage.coverage_status if coverage else "unavailable",
                },
                "claim_resolution": _dump(ledger.statistics) if ledger else None,
                "active_viewpoints": len(active),
                "exceptions": len(self.exception_queue.bundles) if self.exception_queue else 0,
                "affected_products": len(affected),
                "quality_dimensions": (
                    [
                        {
                            "dimension": item.code,
                            "status": item.status,
                            "applicable": True,
                        }
                        for item in state["quality"].checks
                    ]
                    if atomic_quality
                    else [_dump(item) for item in state["quality"].dimensions]
                    if state["quality"]
                    else []
                ),
                "recall": {
                    "available": True,
                    "artifact_sha256": self.recall_blocking.artifact_sha256,
                    "blocking_version": self.recall_blocking.blocking_version,
                    "normalization_version": self.recall_blocking.normalization_version,
                    "statistics": self.recall_blocking.statistics,
                    "known_positive_recall": _dump(
                        self.recall_blocking.known_positive_recall
                    ),
                } if self.recall_blocking else {"available": False},
            },
            {
                "viewpoints": "/admin/wang/viewpoints",
                "exceptions": "/admin/wang/viewpoint-exceptions",
            },
        )

    def recall_diagnostics(
        self, *, cursor: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        state = self._state()
        artifact = self.recall_blocking
        if not artifact:
            return self._envelope(
                state,
                {
                    "available": False,
                    "items": [],
                    "total": 0,
                    "next_cursor": None,
                    "suppressed_blocks": [],
                    "unparsed_scripture_refs": [],
                },
                {
                    "self": "/admin/wang/viewpoints/recall-blocking",
                    "viewpoints": "/admin/wang/viewpoints",
                },
            )
        ordered = sorted(
            artifact.neighborhoods,
            key=lambda item: (
                -max((neighbor.score for neighbor in item.neighbors), default=0),
                -len(item.neighbors),
                item.focal_claim_id,
            ),
        )
        offset = _read_cursor(cursor, state["registry_snapshot_id"])
        page = ordered[offset:offset + limit]
        next_cursor = (
            _cursor(state["registry_snapshot_id"], offset + limit)
            if offset + limit < len(ordered)
            else None
        )
        items = []
        for neighborhood in page:
            items.append({
                "focal_claim_id": neighborhood.focal_claim_id,
                "focal_statement": neighborhood.focal_statement,
                "claim_role": neighborhood.claim_role,
                "normalized_topic_terms": neighborhood.normalized_topic_terms,
                "scripture_chapter_keys": neighborhood.scripture_chapter_keys,
                "neighbors": [
                    {
                        **_dump(neighbor),
                    }
                    for neighbor in neighborhood.neighbors
                ],
            })
        return self._envelope(
            state,
            {
                "available": True,
                "artifact_sha256": artifact.artifact_sha256,
                "blocking_version": artifact.blocking_version,
                "normalization_version": artifact.normalization_version,
                "statistics": artifact.statistics,
                "known_positive_recall": _dump(artifact.known_positive_recall),
                "items": items,
                "total": len(ordered),
                "next_cursor": next_cursor,
                "suppressed_blocks": [
                    _dump(item) for item in artifact.suppressed_blocks
                ],
                "unparsed_scripture_refs": artifact.unparsed_scripture_refs,
            },
            {
                "self": "/admin/wang/viewpoints/recall-blocking",
                "viewpoints": "/admin/wang/viewpoints",
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
        unit_links_by_viewpoint: dict[
            str, list[ViewpointPropositionUnitLinkRecord]
        ] = defaultdict(list)
        for link in self.records["viewpoint_proposition_unit_links"]:
            if link.effective_state == "active":
                unit_links_by_viewpoint[link.viewpoint_id].append(link)
        units: dict[str, ViewpointPropositionUnitRecord] = idx[
            "viewpoint_proposition_units"
        ]
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
            atomic_links = [
                item
                for item in unit_links_by_viewpoint.get(viewpoint.viewpoint_id, [])
                if item.validated_against_viewpoint_revision_id
                == viewpoint.current_revision_id
            ]
            member_units = [
                units[item.proposition_unit_id]
                for item in atomic_links
                if item.proposition_unit_id in units
            ]
            atomic_claims = [
                claims[item.parent_claim_id]
                for item in member_units
                if item.parent_claim_id in claims
            ]
            all_member_claims = [*member_claims, *atomic_claims]
            haystack = " ".join([viewpoint.viewpoint_id, revision.core_proposition, *revision.editorial_aliases]).casefold()
            if q and q.casefold() not in haystack:
                continue
            if review_status and revision.review_status != review_status:
                continue
            if topic_id and not any(topic_id in item.topic_ids for item in all_member_claims):
                continue
            if scripture and not any(
                scripture.casefold() in json.dumps(item.scripture_refs, ensure_ascii=False).casefold()
                for item in all_member_claims
            ):
                continue
            claim_ids = {
                *[item.claim_id for item in member_links],
                *[item.parent_claim_id for item in member_units],
            }
            source_ids = (
                set().union(
                    *(sources_by_claim.get(claim_id, set()) for claim_id in claim_ids)
                )
                if claim_ids
                else set()
            )
            source_ids.update(item.source_id for item in member_units)
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
                "topic_ids": sorted({topic for item in all_member_claims for topic in item.topic_ids}),
                "counts": {
                    "members": len(member_links) + len(atomic_links), "sources": len(source_ids),
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
                if step is None:
                    continue
                # Extraction wrote the fragment singular in one era and plural in
                # another, and both forms are live in the store. Reading only the
                # singular reports zero sources for every viewpoint built from
                # plural-era evidence.
                for fragment_id in evidence_fragment_ids(step):
                    fragment = fragments.get(fragment_id)
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
        atomic_links = sorted(
            [
                item
                for item in self.records["viewpoint_proposition_unit_links"]
                if item.viewpoint_id == viewpoint_id
                and item.effective_state == "active"
                and item.validated_against_viewpoint_revision_id
                == viewpoint.current_revision_id
            ],
            key=lambda item: (item.link_type, item.proposition_unit_id),
        )
        proposition_units = idx["viewpoint_proposition_units"]
        member_claim_ids.update(
            proposition_units[item.proposition_unit_id].parent_claim_id
            for item in atomic_links
            if item.proposition_unit_id in proposition_units
        )
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
                        "source_admin_url": (
                            f"/admin/wang/source-coverage?source={quote(fragment.source_id)}&fragment={quote(fragment.fragment_id)}"
                            if fragment else None
                        ),
                        "source_file_name": (
                            Path(getattr(source, "source_path", "")).name
                            if source and getattr(source, "source_path", "") else None
                        ),
                        "source_type": source.source_type if source else None,
                        "paragraph_key": fragment.paragraph_key if fragment else None,
                        "media_time": fragment.media_time if fragment else None,
                    },
                })
            members.append({"link": _dump(link), "claim": _dump(claim), "evidence": steps})
        for link in atomic_links:
            unit = proposition_units.get(link.proposition_unit_id)
            if not unit:
                continue
            claim = claims.get(unit.parent_claim_id)
            steps = []
            for binding in unit.evidence_bindings:
                step = evidence.get(binding.evidence_step_id)
                fragment = fragments.get(binding.source_fragment_id)
                source = sources.get(fragment.source_id) if fragment else None
                steps.append({
                    "evidence_step": _dump(step) if step else None,
                    "source_fragment": _dump(fragment) if fragment else None,
                    "source": _dump(source) if source else None,
                    "citations": [
                        _dump(citations[cid])
                        for cid in (step.citation_ids if step else [])
                        if cid in citations
                    ],
                    "locator": {
                        "source_url": (fragment.source_url if fragment else None)
                        or (source.source_url if source else None),
                        "source_admin_url": (
                            f"/admin/wang/source-coverage?source={quote(fragment.source_id)}&fragment={quote(fragment.fragment_id)}"
                            if fragment else None
                        ),
                        "source_file_name": (
                            Path(getattr(source, "source_path", "")).name
                            if source and getattr(source, "source_path", "") else None
                        ),
                        "source_type": source.source_type if source else None,
                        "paragraph_key": fragment.paragraph_key if fragment else None,
                        "media_time": fragment.media_time if fragment else None,
                    },
                })
            members.append({
                "membership_kind": "proposition_unit",
                "link": _dump(link),
                "proposition_unit": _dump(unit),
                "claim": _dump(claim) if claim else {
                    "claim_id": unit.parent_claim_id,
                    "statement": unit.unit_statement,
                    "review_status": "missing",
                },
                "evidence": steps,
            })
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
                    if (
                        item.argument_route_id == route.argument_route_id
                        and item.effective_state == "active"
                        and item.validated_against_route_revision_id == route.current_revision_id
                        and (
                            not snapshot
                            or item.argument_route_attestation_id
                            in snapshot.active_attestation_ids
                        )
                    )
                ],
                key=lambda item: item.argument_route_attestation_id,
            )
            evidence_step_ids = sorted({
                step_id
                for item in attestations
                for binding in item.step_bindings
                for step_id in binding.evidence_step_ids
            })
            approved_statuses = {"system_approved", "human_approved", "approved"}
            full_attestations = [
                item for item in attestations if item.completeness == "full"
            ]
            current_registry_ready = bool(
                route_revision
                and route_revision.review_status in approved_statuses
                and full_attestations
                and all(
                    item.review_status in approved_statuses
                    for item in full_attestations
                )
            )
            display_attestations = []
            nodes_by_key = {
                item.route_step_key: item
                for item in (route_revision.ordered_inference_nodes if route_revision else [])
            }
            for attestation in attestations:
                source = sources.get(attestation.source_id)
                bindings = []
                for binding in attestation.step_bindings:
                    evidence_items = []
                    for evidence_id in binding.evidence_step_ids:
                        step = evidence.get(evidence_id)
                        bound_fragment_ids = (
                            set(evidence_fragment_ids(step))
                            & set(binding.source_fragment_ids)
                            if step else set()
                        )
                        evidence_items.append({
                            "evidence_step": _dump(step) if step else None,
                            "fragments": [
                                {
                                    "source_fragment": _dump(fragments[fragment_id]),
                                    "locator": {
                                        "source_url": (
                                            fragments[fragment_id].source_url
                                            or (source.source_url if source else None)
                                        ),
                                        "source_admin_url": (
                                            f"/admin/wang/source-coverage?source={quote(attestation.source_id)}"
                                            f"&fragment={quote(fragment_id)}"
                                        ),
                                        "source_file_name": (
                                            Path(getattr(source, "source_path", "")).name
                                            if source and getattr(source, "source_path", "")
                                            else None
                                        ),
                                        "source_type": source.source_type if source else None,
                                        "paragraph_key": fragments[fragment_id].paragraph_key,
                                        "media_time": fragments[fragment_id].media_time,
                                    },
                                }
                                for fragment_id in sorted(bound_fragment_ids)
                                if fragment_id in fragments
                            ],
                        })
                    bindings.append({
                        "binding": _dump(binding),
                        "node": (
                            _dump(nodes_by_key[binding.route_step_key])
                            if binding.route_step_key in nodes_by_key else None
                        ),
                        "evidence": evidence_items,
                    })
                display_attestations.append({
                    "attestation": _dump(attestation),
                    "source": _dump(source) if source else None,
                    "bindings": bindings,
                })
            coverage = (
                {
                    "mode": "coverage_snapshot",
                    "eligibility": snapshot.eligibility,
                    "full_attestation_count": snapshot.full_attestation_count,
                    "partial_attestation_count": snapshot.partial_attestation_count,
                }
                if snapshot else {
                    "mode": "current_registry",
                    "eligibility": (
                        "approved_evidence_ready"
                        if current_registry_ready else "candidate_only"
                    ),
                    "full_attestation_count": len(full_attestations),
                    "partial_attestation_count": len(attestations) - len(full_attestations),
                }
            )
            routes.append({
                "route_id": route.argument_route_id,
                "route_type": route_revision.route_label if route_revision else "推理路线",
                "claim_id": attestations[0].claim_ids[0] if attestations else None,
                "evidence_step_ids": evidence_step_ids,
                "route": _dump(route),
                "revision": _dump(route_revision) if route_revision else None,
                "attestations": display_attestations,
                "snapshot": snapshot.model_dump(mode="json") if snapshot else None,
                "coverage": coverage,
            })
        revisions = sorted(
            [_dump(item) for item in self.records["viewpoint_revisions"] if item.viewpoint_id == viewpoint_id],
            key=lambda item: item["revision_number"], reverse=True,
        )
        impact = self._impact(member_claim_ids, viewpoint.current_revision_id)
        graph = {
            "nodes": [
                {"id": viewpoint_id, "kind": "viewpoint", "label": revision.core_proposition},
                *[{
                    "id": (item.get("proposition_unit") or item["claim"])[
                        "proposition_unit_id" if item.get("proposition_unit") else "claim_id"
                    ],
                    "kind": "member",
                    "label": (item.get("proposition_unit") or item["claim"])[
                        "unit_statement" if item.get("proposition_unit") else "statement"
                    ],
                } for item in members],
                *[{"id": item["route_id"], "kind": "route", "label": item.get("route_type") or "推理路线"} for item in routes],
                *[{"id": item["to_viewpoint_id"], "kind": "related_viewpoint", "label": item["to_viewpoint_id"]} for item in relations if item["to_viewpoint_id"]],
            ],
            "edges": [
                *[{
                    "from": (item.get("proposition_unit") or item["claim"])[
                        "proposition_unit_id" if item.get("proposition_unit") else "claim_id"
                    ],
                    "to": viewpoint_id,
                    "kind": item["link"]["link_type"],
                } for item in members],
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

    def structures(self) -> dict[str, Any]:
        """Reviewed centres: which viewpoints add up to one argument, and how.

        A structure never asserts anything of its own, so every row here is a
        pointer into viewpoints that already stand on their own evidence.
        """

        state = self._state()
        revisions = {
            item.viewpoint_revision_id: item for item in self.records["viewpoint_revisions"]
        }
        viewpoint_by_revision = {
            item.viewpoint_revision_id: item.viewpoint_id
            for item in self.records["viewpoint_revisions"]
        }
        sources_by_claim = self._source_ids_by_claim()
        claims_by_revision: dict[str, set[str]] = defaultdict(set)
        for link in self.records["viewpoint_claim_links"]:
            if link.effective_state == "active" and link.link_type == "equivalent_component":
                claims_by_revision[link.validated_against_viewpoint_revision_id].add(link.claim_id)

        structure_revisions = {
            item.structure_revision_id: item
            for item in self.records["viewpoint_structure_revisions"]
        }
        items = []
        for structure in self.records["viewpoint_structures"]:
            if structure.effective_state != "active":
                continue
            revision = structure_revisions.get(structure.current_revision_id)
            if revision is None:
                continue
            focal = []
            for entry in revision.focal_viewpoints:
                pinned = revisions.get(entry.viewpoint_revision_id)
                claim_ids = claims_by_revision.get(entry.viewpoint_revision_id, set())
                source_ids: set[str] = set()
                for claim_id in claim_ids:
                    source_ids.update(sources_by_claim.get(claim_id, set()))
                focal.append({
                    "structure_role": entry.structure_role,
                    "viewpoint_revision_id": entry.viewpoint_revision_id,
                    "viewpoint_id": viewpoint_by_revision.get(entry.viewpoint_revision_id),
                    "core_proposition": pinned.core_proposition if pinned else None,
                    "counts": {"members": len(claim_ids), "sources": len(source_ids)},
                })
            items.append({
                "structure_id": structure.structure_id,
                "structure_revision_id": revision.structure_revision_id,
                "central_synthesis": revision.central_synthesis,
                "wording_label": "编辑归一化（非逐字引文）",
                "focal": focal,
                "unresolved_items": revision.unresolved_items,
                "review_status": revision.review_status,
            })
        items.sort(key=lambda item: item["structure_id"])
        return self._envelope(
            state,
            {"items": items, "total": len(items)},
            {"self": "/admin/wang/viewpoint-structures", "viewpoints": "/admin/wang/viewpoints"},
        )

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
