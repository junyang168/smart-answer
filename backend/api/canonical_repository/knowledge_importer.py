from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import ValidationError

from .knowledge_models import (
    KNOWLEDGE_COLLECTIONS,
    CompositionDecisionRecord,
    CompositionPlanRecord,
    EvolvingKnowledgeRecord,
    KnowledgePackageManifest,
)
from .store import RepositoryStore


class KnowledgePackageValidationError(ValueError):
    def __init__(self, findings: list[str]):
        self.findings = findings
        super().__init__("Knowledge package validation failed")


class KnowledgePackageImporter:
    """Validate and import an evolving knowledge package into atomic records."""

    SOURCE_COLLECTION_KEYS = {
        "source_documents": "source_documents",
        "source_fragments": "source_fragments",
        "questions": "questions",
        "observations": "observations",
        "claims": "claims",
        "evidence_steps": "evidence_steps",
        "knowledge_relations": "knowledge_relations",
        "claim_relations": "claim_relations",
        "claim_relation_constraints": "claim_relation_constraints",
        "position_nodes": "position_nodes",
        "topic_nodes": "topic_nodes",
        "topic_identity_reconciliations": "topic_identity_reconciliations",
        "knowledge_routes": "knowledge_routes",
        "cross_source_syntheses": "editorial_syntheses",
        "editorial_checks": "editorial_checks",
        "tensions": "tensions",
        "viewpoint_coverage_snapshots": "viewpoint_coverage_snapshots",
        "canonical_viewpoints": "canonical_viewpoints",
        "viewpoint_revisions": "viewpoint_revisions",
        "viewpoint_claim_links": "viewpoint_claim_links",
        "argument_routes": "argument_routes",
        "argument_route_revisions": "argument_route_revisions",
        "argument_route_attestations": "argument_route_attestations",
        "viewpoint_relations": "viewpoint_relations",
        "viewpoint_identity_candidates": "viewpoint_identity_candidates",
        "viewpoint_identity_decisions": "viewpoint_identity_decisions",
        "viewpoint_resolution_ledgers": "viewpoint_resolution_ledgers",
        "viewpoint_quality_reports": "viewpoint_quality_reports",
    }

    REVIEW_FIELDS = {
        "review_status",
        "reviewed_at",
        "reviewed_by",
        "review_note",
        "revision",
        "visibility",
    }

    def __init__(
        self,
        store: RepositoryStore,
        provenance_binder: Optional[
            Callable[[dict[str, list[EvolvingKnowledgeRecord]]], None]
        ] = None,
    ):
        self.store = store
        self.provenance_binder = provenance_binder

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _model_records(payload: dict[str, Any]) -> dict[str, list[EvolvingKnowledgeRecord]]:
        findings: list[str] = []
        records: dict[str, list[EvolvingKnowledgeRecord]] = {
            collection: [] for collection in KNOWLEDGE_COLLECTIONS
        }

        for source_key, collection in KnowledgePackageImporter.SOURCE_COLLECTION_KEYS.items():
            model, _ = KNOWLEDGE_COLLECTIONS[collection]
            for index, item in enumerate(payload.get(source_key, [])):
                try:
                    records[collection].append(model.model_validate(item))
                except ValidationError as exc:
                    findings.append(f"{source_key}[{index}]: {exc}")

        for plan_index, raw_plan in enumerate(payload.get("product_plans", [])):
            plan_payload = dict(raw_plan)
            # A plan sent without its `decisions` is a plan nobody said anything
            # about the decisions of. Deriving `decision_ids` unconditionally
            # made it say "this plan has none": the contract migration of
            # 2026-08-17 emptied `decision_ids` on all three Matthew 16 plans
            # that way, and was undone 98 seconds later by
            # AUTHORING-CONTRACT-MIGRATION-RESTORE. Deriving it only from a
            # `decisions` key that is actually there also stops the derived
            # value from overwriting a `decision_ids` the package stated
            # directly, which is how such a package had to be written.
            has_decisions = "decisions" in plan_payload
            raw_decisions = plan_payload.pop("decisions", [])
            plan_payload.setdefault("product_type", "unspecified")
            if has_decisions:
                plan_payload["decision_ids"] = [
                    item.get("decision_id") for item in raw_decisions if item.get("decision_id")
                ]
            try:
                plan = CompositionPlanRecord.model_validate(plan_payload)
                records["composition_plans"].append(plan)
            except ValidationError as exc:
                findings.append(f"product_plans[{plan_index}]: {exc}")
                continue

            for decision_index, raw_decision in enumerate(raw_decisions):
                decision_payload = dict(raw_decision)
                decision_payload.setdefault("plan_id", plan.plan_id)
                try:
                    records["composition_decisions"].append(
                        CompositionDecisionRecord.model_validate(decision_payload)
                    )
                except ValidationError as exc:
                    findings.append(
                        f"product_plans[{plan_index}].decisions[{decision_index}]: {exc}"
                    )

        if findings:
            raise KnowledgePackageValidationError(findings)

        return records

    @staticmethod
    def _ids(records: dict[str, list[EvolvingKnowledgeRecord]], collection: str) -> set[str]:
        _, id_field = KNOWLEDGE_COLLECTIONS[collection]
        return {str(getattr(item, id_field)) for item in records[collection]}

    def _available_ids(
        self,
        records: dict[str, list[EvolvingKnowledgeRecord]],
        collection: str,
    ) -> set[str]:
        incoming = self._ids(records, collection)
        _, id_field = KNOWLEDGE_COLLECTIONS[collection]
        existing = {
            str(getattr(item, id_field))
            for item in self.store.list_knowledge_records(collection)
        }
        return incoming | existing

    def _validate_links(self, records: dict[str, list[EvolvingKnowledgeRecord]]) -> None:
        findings: list[str] = []
        for collection, items in records.items():
            _, id_field = KNOWLEDGE_COLLECTIONS[collection]
            ids = [str(getattr(item, id_field)) for item in items]
            duplicates = sorted(
                item_id for item_id, count in Counter(ids).items() if count > 1
            )
            if duplicates:
                findings.append(
                    f"{collection} contains duplicate IDs: {', '.join(duplicates)}"
                )

        source_ids = self._available_ids(records, "source_documents")
        fragment_ids = self._available_ids(records, "source_fragments")
        evidence_ids = self._available_ids(records, "evidence_steps")
        claim_ids = self._available_ids(records, "claims")
        position_ids = self._available_ids(records, "position_nodes")
        decision_ids = self._available_ids(records, "composition_decisions")
        topic_ids = self._available_ids(records, "topic_nodes")

        for fragment in records["source_fragments"]:
            if fragment.source_id not in source_ids:
                findings.append(
                    f"Source fragment {fragment.fragment_id} references missing source {fragment.source_id}"
                )

        for collection in ("questions", "observations", "evidence_steps"):
            _, id_field = KNOWLEDGE_COLLECTIONS[collection]
            for item in records[collection]:
                fragment_id = getattr(item, "source_fragment_id", None)
                if fragment_id and fragment_id not in fragment_ids:
                    findings.append(
                        f"{collection}/{getattr(item, id_field)} references missing fragment {fragment_id}"
                    )

        for claim in records["claims"]:
            missing = sorted(set(claim.evidence_step_ids) - evidence_ids)
            if missing:
                findings.append(
                    f"Claim {claim.claim_id} references missing evidence steps: {', '.join(missing)}"
                )
            if not claim.evidence_step_ids:
                findings.append(f"Claim {claim.claim_id} has no evidence steps")

        # A relation may start at an observation -- that edge is how "the
        # professor reasoned from this observation" is recorded, and the
        # extraction schema allows it.  Both layers have to agree, or a package
        # this store accepts becomes one the importer rejects.  The target
        # stays an evidence step: observations do not support each other.
        observation_ids = self._available_ids(records, "observations")
        relation_sources = evidence_ids | observation_ids
        for relation in records["knowledge_relations"]:
            if relation.from_id not in relation_sources or relation.to_id not in evidence_ids:
                findings.append(
                    f"Knowledge relation {relation.relation_id} has unresolved endpoint(s): "
                    f"{relation.from_id} -> {relation.to_id}"
                )

        claim_relation_endpoints = claim_ids | position_ids
        for relation in records["claim_relations"]:
            if relation.from_id not in claim_relation_endpoints or relation.to_id not in claim_relation_endpoints:
                findings.append(
                    f"Claim relation {relation.claim_relation_id} has unresolved endpoint(s): "
                    f"{relation.from_id} -> {relation.to_id}"
                )

        for constraint in records["claim_relation_constraints"]:
            if constraint.source_id not in claim_relation_endpoints or constraint.target_id not in claim_relation_endpoints:
                findings.append(
                    f"Claim relation constraint {constraint.constraint_id} has unresolved endpoint(s): "
                    f"{constraint.source_id} -> {constraint.target_id}"
                )

        for route in records["knowledge_routes"]:
            if route.claim_id not in claim_ids:
                findings.append(f"Knowledge route {route.route_id} references missing claim {route.claim_id}")
            missing = sorted(set(route.decision_ids) - decision_ids)
            if missing:
                findings.append(
                    f"Knowledge route {route.route_id} references missing decisions: {', '.join(missing)}"
                )
            missing_topics = sorted(set(route.canonical_topic_ids) - topic_ids)
            if missing_topics:
                findings.append(
                    f"Knowledge route {route.route_id} references missing canonical topics: "
                    f"{', '.join(missing_topics)}"
                )

        for synthesis in records["editorial_syntheses"]:
            missing = sorted(set(synthesis.claim_ids) - claim_ids)
            if missing:
                findings.append(
                    f"Editorial synthesis {synthesis.synthesis_id} references missing claims: "
                    f"{', '.join(missing)}"
                )

        for decision in records["composition_decisions"]:
            missing = sorted(set(decision.claim_ids) - claim_ids)
            if missing:
                findings.append(
                    f"Composition decision {decision.decision_id} references missing claims: "
                    f"{', '.join(missing)}"
                )

        if findings:
            raise KnowledgePackageValidationError(findings)

        # Shape validation cannot prove that a viewpoint's current revision,
        # membership decision, Claim SHA, ledger, and quality report all name
        # the same immutable graph. Reuse the PostgreSQL ChangeSet validator so
        # the filesystem exchange repository cannot accept a package the
        # authoring authority would refuse.
        viewpoint_collections = (
            "viewpoint_coverage_snapshots",
            "canonical_viewpoints",
            "viewpoint_revisions",
            "viewpoint_claim_links",
            "argument_routes",
            "argument_route_revisions",
            "argument_route_attestations",
            "viewpoint_relations",
            "viewpoint_identity_candidates",
            "viewpoint_identity_decisions",
            "viewpoint_resolution_ledgers",
            "viewpoint_quality_reports",
        )
        if any(records[collection] for collection in viewpoint_collections):
            from .viewpoint_foundation import (
                ViewpointFoundationValidationError,
                semantic_record_sha,
                validate_foundation_change_set,
            )

            normalized: dict[str, dict[str, dict[str, Any]]] = {}
            existing: dict[tuple[str, str], dict[str, Any]] = {}
            for collection, items in records.items():
                _, id_field = KNOWLEDGE_COLLECTIONS[collection]
                normalized[collection] = {
                    str(getattr(item, id_field)): item.model_dump(mode="json")
                    for item in items
                }
                for stored in self.store.list_knowledge_records(collection):
                    object_id = str(getattr(stored, id_field))
                    payload = stored.model_dump(mode="json")
                    existing[(collection, object_id)] = {
                        "revision": stored.revision,
                        "content_sha256": semantic_record_sha(payload),
                        "payload": payload,
                    }
            try:
                validate_foundation_change_set(normalized, existing)
            except ViewpointFoundationValidationError as exc:
                raise KnowledgePackageValidationError(exc.findings) from exc

    def _validate_provenance(
        self,
        records: dict[str, list[EvolvingKnowledgeRecord]],
    ) -> None:
        """Require publishable evidence to use the canonical Citation authority."""
        findings: list[str] = []
        fragments = {
            item.fragment_id: item for item in records["source_fragments"]
        }
        for evidence in records["evidence_steps"]:
            if evidence.support_eligibility not in {"eligible", "eligible_with_label"}:
                continue
            if not evidence.citation_ids:
                findings.append(
                    f"Evidence step {evidence.evidence_step_id} is eligible but has no canonical citation"
                )
                continue
            fragment = fragments.get(evidence.source_fragment_id or "")
            if fragment and fragment.citation_id not in evidence.citation_ids:
                findings.append(
                    f"Evidence step {evidence.evidence_step_id} is not bound to its source fragment citation"
                )
            for citation_id in evidence.citation_ids:
                try:
                    self.store.get_citation(citation_id)
                except FileNotFoundError:
                    findings.append(
                        f"Evidence step {evidence.evidence_step_id} references missing citation {citation_id}"
                    )
        if findings:
            raise KnowledgePackageValidationError(findings)

    def _preserve_review(self, collection: str, record: EvolvingKnowledgeRecord) -> EvolvingKnowledgeRecord:
        model, id_field = KNOWLEDGE_COLLECTIONS[collection]
        record_id = str(getattr(record, id_field))
        path = self.store.knowledge_record_path(collection, record_id)
        if not path.is_file():
            return record
        existing = json.loads(path.read_text(encoding="utf-8"))
        incoming = record.model_dump(mode="json")
        if existing.get("review_status", "candidate") != "candidate":
            for field in self.REVIEW_FIELDS:
                if field in existing:
                    incoming[field] = existing[field]
        return model.model_validate(incoming)

    def import_path(self, package_path: Path) -> dict[str, Any]:
        package_path = Path(package_path)
        payload = json.loads(package_path.read_text(encoding="utf-8"))
        package_id = payload.get("package_id")
        if not package_id:
            raise KnowledgePackageValidationError(["Package is missing package_id"])

        records = self._model_records(payload)
        self._validate_links(records)
        if self.provenance_binder is not None:
            self.provenance_binder(records)
        self._validate_provenance(records)

        changes = {"created": 0, "updated": 0, "unchanged": 0}
        record_ids: dict[str, list[str]] = {}
        for collection, items in records.items():
            _, id_field = KNOWLEDGE_COLLECTIONS[collection]
            record_ids[collection] = []
            for proposed in items:
                record = self._preserve_review(collection, proposed)
                record_id = str(getattr(record, id_field))
                record_ids[collection].append(record_id)
                path = self.store.knowledge_record_path(collection, record_id)
                existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
                normalized = record.model_dump(mode="json")
                if existing == normalized:
                    changes["unchanged"] += 1
                    continue
                changes["updated" if existing is not None else "created"] += 1
                self.store.save_knowledge_record(collection, record)

        manifest = KnowledgePackageManifest(
            package_id=package_id,
            source_schema_version=payload.get("schema_version", "unknown"),
            source_sha256=self._sha256(package_path),
            imported_at=datetime.now(timezone.utc).isoformat(),
            counts={collection: len(items) for collection, items in records.items()},
            record_ids=record_ids,
        )
        self.store.save_knowledge_package(manifest)
        return {
            "package": manifest.model_dump(mode="json"),
            "changes": changes,
            "repository_counts": self.store.knowledge_counts(),
        }
