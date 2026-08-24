"""Immutable CanonicalViewpoint snapshots and the shared downstream read contract."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .knowledge_models import ProductDependencyRecord, evidence_fragment_ids
from .viewpoint_foundation import semantic_record_sha, sha256_json


APPROVED = frozenset({"system_approved", "human_approved", "approved"})
PUBLIC_ANCHORS = frozenset({"source_version_bound", "canonical_citation_bound", "verified", "valid"})
PUBLIC_EVIDENCE = frozenset({"eligible", "eligible_with_label"})
MEMBERSHIP_TYPES = frozenset({"equivalent_full", "equivalent_component"})


class ViewpointRuntimeProjectionError(ValueError):
    def __init__(self, findings: Sequence[str]):
        self.findings = list(findings)
        super().__init__("viewpoint runtime projection failed: " + "; ".join(self.findings))


def _dump(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def _id(value: Any, name: str) -> str:
    return str(getattr(value, name, _dump(value).get(name)))


class ImmutableArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArgumentRouteSnapshot(ImmutableArtifact):
    schema_version: Literal["wang_argument_route_snapshot_v1"] = "wang_argument_route_snapshot_v1"
    argument_route_snapshot_id: str
    argument_route_id: str
    argument_route_revision_id: str
    conclusion_viewpoint_id: str
    conclusion_viewpoint_revision_id: str
    coverage_snapshot_id: str
    active_attestation_ids: list[str]
    full_attestation_count: int = Field(ge=0)
    partial_attestation_count: int = Field(ge=0)
    distinct_full_source_count: int = Field(ge=0)
    eligibility: Literal["candidate_only", "approved_evidence_ready"]
    build_fingerprint_sha256: str
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_counts(self) -> "ArgumentRouteSnapshot":
        if self.active_attestation_ids != sorted(set(self.active_attestation_ids)):
            raise ValueError("active_attestation_ids must be sorted and unique")
        if self.argument_route_snapshot_id != f"ARS-{self.build_fingerprint_sha256[:20]}":
            raise ValueError("argument route snapshot id does not match build fingerprint")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("argument route snapshot artifact SHA mismatch")
        return self


class ViewpointRegistrySnapshot(ImmutableArtifact):
    schema_version: Literal["wang_viewpoint_registry_snapshot_v1"] = "wang_viewpoint_registry_snapshot_v1"
    viewpoint_registry_snapshot_id: str
    viewpoint_id: str
    viewpoint_revision_id: str
    coverage_snapshot_id: str
    member_link_ids: list[str]
    related_claim_link_ids: list[str]
    argument_route_snapshot_ids: list[str]
    viewpoint_relation_ids: list[str]
    member_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    route_count: int = Field(ge=0)
    relation_count: int = Field(ge=0)
    eligibility: Literal["candidate_only", "approved_evidence_ready"]
    build_fingerprint_sha256: str
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ViewpointRegistrySnapshot":
        for field_name in (
            "member_link_ids", "related_claim_link_ids", "argument_route_snapshot_ids",
            "viewpoint_relation_ids",
        ):
            values = getattr(self, field_name)
            if values != sorted(set(values)):
                raise ValueError(f"{field_name} must be sorted and unique")
        if self.viewpoint_registry_snapshot_id != f"RGS-{self.build_fingerprint_sha256[:20]}":
            raise ValueError("registry snapshot id does not match build fingerprint")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("registry snapshot artifact SHA mismatch")
        return self


class ProjectionDependency(ImmutableArtifact):
    collection: str
    record_id: str
    revision: int = Field(ge=1)
    sha256: str


class ViewpointKnowledgeProjection(ImmutableArtifact):
    schema_version: Literal["wang_viewpoint_knowledge_projection_v1"] = (
        "wang_viewpoint_knowledge_projection_v1"
    )
    consumer_kind: Literal["registry_review", "composition_plan", "qa_answer", "search_card"]
    scope_viewpoint_ids: list[str]
    coverage_snapshot_id: str
    resolution_ledger_id: str | None
    quality_report_id: str | None
    eligibility: Literal["internal_candidate", "composition", "public_attribution"]
    blocker_codes: list[str]
    viewpoints: list[dict[str, Any]]
    argument_routes: list[dict[str, Any]]
    expanded_claims: list[dict[str, Any]]
    expanded_evidence: list[dict[str, Any]]
    expanded_fragments: list[dict[str, Any]]
    expanded_sources: list[dict[str, Any]]
    expanded_citations: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    dependency_manifest: list[ProjectionDependency]
    dependency_manifest_sha256: str
    projection_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ViewpointKnowledgeProjection":
        serialized_manifest = [item.model_dump(mode="json") for item in self.dependency_manifest]
        if self.dependency_manifest_sha256 != sha256_json(serialized_manifest):
            raise ValueError("projection dependency manifest SHA mismatch")
        payload = self.model_dump(mode="json", exclude={"projection_sha256"})
        if self.projection_sha256 != sha256_json(payload):
            raise ValueError("projection SHA mismatch")
        return self


def validate_runtime_authoring_graph(
    collections: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> list[str]:
    """Return cross-collection findings for route and relation authoring records."""
    routes = collections.get("argument_routes", {})
    route_revisions = collections.get("argument_route_revisions", {})
    attestations = collections.get("argument_route_attestations", {})
    viewpoints = collections.get("canonical_viewpoints", {})
    viewpoint_revisions = collections.get("viewpoint_revisions", {})
    links = collections.get("viewpoint_claim_links", {})
    claims = collections.get("claims", {})
    evidence = collections.get("evidence_steps", {})
    fragments = collections.get("source_fragments", {})
    sources = collections.get("source_documents", {})
    claim_relations = collections.get("claim_relations", {})
    relations = collections.get("viewpoint_relations", {})
    findings: list[str] = []

    for route_id, route in routes.items():
        revision_id = str(route["current_revision_id"])
        revision = route_revisions.get(revision_id)
        viewpoint_id = str(route["conclusion_viewpoint_id"])
        if viewpoint_id not in viewpoints:
            findings.append(f"{route_id}: missing conclusion viewpoint {viewpoint_id}")
        if not revision or revision.get("argument_route_id") != route_id:
            findings.append(f"{route_id}: invalid current route revision {revision_id}")
        elif revision.get("route_signature", {}).get("conclusion_viewpoint_id") != viewpoint_id:
            findings.append(f"{route_id}: route signature conclusion mismatch")

    for revision_id, revision in route_revisions.items():
        route = routes.get(str(revision["argument_route_id"]))
        viewpoint_revision_id = str(revision["validated_against_conclusion_viewpoint_revision_id"])
        viewpoint_revision = viewpoint_revisions.get(viewpoint_revision_id)
        if not route:
            findings.append(f"{revision_id}: missing argument route {revision['argument_route_id']}")
        elif viewpoint_revision_id != viewpoints.get(str(route["conclusion_viewpoint_id"]), {}).get("current_revision_id"):
            findings.append(f"{revision_id}: conclusion viewpoint revision is stale")
        if not viewpoint_revision or viewpoint_revision.get("viewpoint_id") != revision.get("route_signature", {}).get("conclusion_viewpoint_id"):
            findings.append(f"{revision_id}: invalid conclusion viewpoint revision")
        prior_id = revision.get("supersedes_revision_id")
        if prior_id and route_revisions.get(str(prior_id), {}).get("argument_route_id") != revision.get("argument_route_id"):
            findings.append(f"{revision_id}: invalid superseded route revision {prior_id}")

    for attestation_id, attestation in attestations.items():
        route_id = str(attestation["argument_route_id"])
        route = routes.get(route_id)
        revision_id = str(attestation["validated_against_route_revision_id"])
        claim_ids = [str(value) for value in attestation.get("claim_ids") or []]
        link = links.get(str(attestation["terminal_claim_link_id"]))
        if not route:
            findings.append(f"{attestation_id}: missing argument route {route_id}")
        elif revision_id != route.get("current_revision_id"):
            findings.append(f"{attestation_id}: attestation route revision is stale")
        if revision_id not in route_revisions:
            findings.append(f"{attestation_id}: missing route revision {revision_id}")
        if str(attestation["source_id"]) not in sources:
            findings.append(f"{attestation_id}: missing source {attestation['source_id']}")
        for claim_id in claim_ids:
            if claim_id not in claims:
                findings.append(f"{attestation_id}: missing Claim {claim_id}")
        if not link or str(link.get("claim_id")) not in claim_ids:
            findings.append(f"{attestation_id}: terminal Claim link mismatch")
        elif route and link.get("viewpoint_id") != route.get("conclusion_viewpoint_id"):
            findings.append(f"{attestation_id}: terminal Claim link concludes another viewpoint")
        elif link.get("effective_state") != "active":
            findings.append(f"{attestation_id}: terminal Claim link is not active")
        derived_refs: set[str] = set()
        route_revision = route_revisions.get(revision_id) or {}
        node_keys = {
            str(item.get("route_step_key"))
            for item in route_revision.get("ordered_inference_nodes") or []
        }
        required_keys = {
            str(item.get("route_step_key"))
            for item in route_revision.get("ordered_inference_nodes") or []
            if item.get("required_for_full_attestation")
        }
        attested_keys: set[str] = set()
        for binding in attestation.get("step_bindings") or []:
            step_key = str(binding.get("route_step_key") or "")
            if step_key not in node_keys:
                findings.append(f"{attestation_id}: unknown route step {step_key}")
            if binding.get("attestation_status") == "attested":
                attested_keys.add(step_key)
            selected_fragment_ids = {
                str(value) for value in binding.get("source_fragment_ids") or []
            }
            allowed_fragment_union: set[str] = set()
            for step_id in binding.get("evidence_step_ids") or []:
                step = evidence.get(str(step_id))
                if not step:
                    findings.append(f"{attestation_id}: missing evidence step {step_id}")
                    continue
                allowed_fragment_ids = set(evidence_fragment_ids(step))
                allowed_fragment_union |= allowed_fragment_ids
                if not selected_fragment_ids & allowed_fragment_ids:
                    findings.append(f"{attestation_id}: evidence/fragment binding mismatch")
                derived_refs.update(str(value) for value in step.get("scripture_refs") or [])
            if not selected_fragment_ids or not selected_fragment_ids <= allowed_fragment_union:
                findings.append(f"{attestation_id}: evidence/fragment binding mismatch")
            bound = [fragments.get(value) for value in selected_fragment_ids]
            if any(
                not fragment
                or fragment.get("source_id") != attestation.get("source_id")
                or fragment.get("source_sha256") != attestation.get("source_revision_sha256")
                for fragment in bound
            ):
                findings.append(f"{attestation_id}: route binding is not source-local")
        if attestation.get("completeness") == "full" and not required_keys <= attested_keys:
            findings.append(f"{attestation_id}: full attestation misses required route steps")
        if sorted(derived_refs) != list(attestation.get("scripture_refs_derived") or []):
            findings.append(f"{attestation_id}: derived scripture refs mismatch")

    for relation_id, relation in relations.items():
        for side in ("source", "target"):
            viewpoint_id = str(relation[f"{side}_viewpoint_id"])
            revision_id = str(relation[f"validated_{side}_viewpoint_revision_id"])
            viewpoint = viewpoints.get(viewpoint_id)
            if not viewpoint:
                findings.append(f"{relation_id}: missing {side} viewpoint {viewpoint_id}")
            elif viewpoint.get("current_revision_id") != revision_id:
                findings.append(f"{relation_id}: {side} viewpoint revision is stale")
            if viewpoint_revisions.get(revision_id, {}).get("viewpoint_id") != viewpoint_id:
                findings.append(f"{relation_id}: invalid {side} viewpoint revision")
        for claim_id in relation.get("supporting_claim_ids") or []:
            if str(claim_id) not in claims:
                findings.append(f"{relation_id}: missing supporting Claim {claim_id}")
        for claim_relation_id in relation.get("supporting_claim_relation_ids") or []:
            if str(claim_relation_id) not in claim_relations:
                findings.append(f"{relation_id}: missing supporting Claim relation {claim_relation_id}")
        if relation.get("relation_type") == "supersedes":
            for claim_id in relation.get("temporal_assertion", {}).get("correction_evidence_claim_ids") or []:
                if str(claim_id) not in claims:
                    findings.append(f"{relation_id}: missing correction evidence Claim {claim_id}")
    return findings


class ViewpointRuntimeCompiler:
    """Compile exact authoring revisions into byte-stable downstream artifacts."""

    def __init__(self, records: Mapping[str, Sequence[Any]], citations: Sequence[Any] = ()):
        self.records = records
        self.citations = list(citations)
        self.index = {
            name: {self._record_id(name, item): item for item in items}
            for name, items in records.items()
        }

    @staticmethod
    def _record_id(collection: str, value: Any) -> str:
        fields = {
            "source_documents": "source_id", "source_fragments": "fragment_id",
            "claims": "claim_id", "evidence_steps": "evidence_step_id",
            "claim_relations": "claim_relation_id", "canonical_viewpoints": "viewpoint_id",
            "viewpoint_revisions": "viewpoint_revision_id", "viewpoint_claim_links": "viewpoint_claim_link_id",
            "viewpoint_proposition_units": "proposition_unit_id",
            "viewpoint_proposition_unit_links": "viewpoint_proposition_unit_link_id",
            "viewpoint_atomic_coverage_snapshots": "atomic_coverage_snapshot_id",
            "viewpoint_atomic_resolution_ledgers": "atomic_resolution_ledger_id",
            "viewpoint_atomic_quality_reports": "atomic_quality_report_id",
            "viewpoint_automated_promotion_decisions": "automated_promotion_decision_id",
            "argument_routes": "argument_route_id", "argument_route_revisions": "argument_route_revision_id",
            "argument_route_attestations": "argument_route_attestation_id",
            "viewpoint_relations": "viewpoint_relation_id", "viewpoint_coverage_snapshots": "coverage_snapshot_id",
            "viewpoint_resolution_ledgers": "resolution_ledger_id", "viewpoint_quality_reports": "quality_report_id",
            "knowledge_routes": "route_id", "product_dependencies": "dependency_id",
            "impact_events": "impact_event_id",
        }
        return _id(value, fields[collection])

    def _coverage(self, coverage_snapshot_id: str) -> Any:
        coverage = self.index.get("viewpoint_coverage_snapshots", {}).get(coverage_snapshot_id)
        if not coverage:
            raise ViewpointRuntimeProjectionError([f"missing coverage snapshot {coverage_snapshot_id}"])
        return coverage

    def compile_route_snapshots(self, coverage_snapshot_id: str) -> list[ArgumentRouteSnapshot]:
        coverage = self._coverage(coverage_snapshot_id)
        covered_sources = {item.source_id for item in coverage.sources}
        results: list[ArgumentRouteSnapshot] = []
        for route in sorted(self.records.get("argument_routes", ()), key=lambda item: item.argument_route_id):
            if route.route_status != "active":
                continue
            revision = self.index["argument_route_revisions"].get(route.current_revision_id)
            viewpoint = self.index["canonical_viewpoints"].get(route.conclusion_viewpoint_id)
            if not revision or not viewpoint or revision.validated_against_conclusion_viewpoint_revision_id != viewpoint.current_revision_id:
                raise ViewpointRuntimeProjectionError([f"{route.argument_route_id}: stale or missing current revision"])
            attestations = sorted([
                item for item in self.records.get("argument_route_attestations", ())
                if item.argument_route_id == route.argument_route_id
                and item.effective_state == "active"
                and item.source_id in covered_sources
            ], key=lambda item: item.argument_route_attestation_id)
            stale = [item.argument_route_attestation_id for item in attestations if item.validated_against_route_revision_id != route.current_revision_id]
            if stale:
                raise ViewpointRuntimeProjectionError([f"{route.argument_route_id}: stale attestations {', '.join(stale)}"])
            full = [item for item in attestations if item.completeness == "full"]
            ready = revision.review_status in APPROVED and bool(full) and all(item.review_status in APPROVED for item in full)
            build = {
                "compiler_version": "viewpoint-runtime-v1", "coverage_snapshot_id": coverage_snapshot_id,
                "argument_route_id": route.argument_route_id, "argument_route_revision_sha256": semantic_record_sha(revision),
                "conclusion_viewpoint_revision_id": viewpoint.current_revision_id,
                "attestation_sha256s": [semantic_record_sha(item) for item in attestations],
            }
            fingerprint = sha256_json(build)
            base = {
                "schema_version": "wang_argument_route_snapshot_v1",
                "argument_route_snapshot_id": f"ARS-{fingerprint[:20]}",
                "argument_route_id": route.argument_route_id,
                "argument_route_revision_id": route.current_revision_id,
                "conclusion_viewpoint_id": route.conclusion_viewpoint_id,
                "conclusion_viewpoint_revision_id": viewpoint.current_revision_id,
                "coverage_snapshot_id": coverage_snapshot_id,
                "active_attestation_ids": [item.argument_route_attestation_id for item in attestations],
                "full_attestation_count": len(full),
                "partial_attestation_count": len(attestations) - len(full),
                "distinct_full_source_count": len({item.source_id for item in full}),
                "eligibility": "approved_evidence_ready" if ready else "candidate_only",
                "build_fingerprint_sha256": fingerprint,
            }
            base["artifact_sha256"] = sha256_json(base)
            results.append(ArgumentRouteSnapshot.model_validate(base))
        return results

    def compile_registry_snapshots(self, coverage_snapshot_id: str) -> list[ViewpointRegistrySnapshot]:
        self._coverage(coverage_snapshot_id)
        route_snapshots = self.compile_route_snapshots(coverage_snapshot_id)
        routes_by_viewpoint: dict[str, list[ArgumentRouteSnapshot]] = defaultdict(list)
        for item in route_snapshots:
            routes_by_viewpoint[item.conclusion_viewpoint_id].append(item)
        fragments = self.index.get("source_fragments", {})
        evidence = self.index.get("evidence_steps", {})
        claims = self.index.get("claims", {})
        results: list[ViewpointRegistrySnapshot] = []
        for viewpoint in sorted(self.records.get("canonical_viewpoints", ()), key=lambda item: item.viewpoint_id):
            if viewpoint.identity_status != "active":
                continue
            revision = self.index["viewpoint_revisions"].get(viewpoint.current_revision_id)
            if not revision:
                raise ViewpointRuntimeProjectionError([f"{viewpoint.viewpoint_id}: missing current revision"])
            links = sorted([
                item for item in self.records.get("viewpoint_claim_links", ())
                if item.viewpoint_id == viewpoint.viewpoint_id and item.effective_state == "active"
            ], key=lambda item: item.viewpoint_claim_link_id)
            stale = [item.viewpoint_claim_link_id for item in links if item.validated_against_viewpoint_revision_id != viewpoint.current_revision_id]
            if stale:
                raise ViewpointRuntimeProjectionError([f"{viewpoint.viewpoint_id}: stale Claim links {', '.join(stale)}"])
            members = [item for item in links if item.link_type in MEMBERSHIP_TYPES]
            related = [item for item in links if item.link_type not in MEMBERSHIP_TYPES]
            relations = sorted([
                item for item in self.records.get("viewpoint_relations", ())
                if item.effective_state == "active" and viewpoint.viewpoint_id in {item.source_viewpoint_id, item.target_viewpoint_id}
            ], key=lambda item: item.viewpoint_relation_id)
            sources: set[str] = set()
            for link in members:
                claim = claims.get(link.claim_id)
                for step_id in claim.evidence_step_ids if claim else []:
                    step = evidence.get(step_id)
                    for fragment_id in evidence_fragment_ids(step) if step else []:
                        fragment = fragments.get(fragment_id)
                        if fragment:
                            sources.add(fragment.source_id)
            route_items = routes_by_viewpoint.get(viewpoint.viewpoint_id, [])
            ready = (
                revision.review_status in APPROVED and bool(members)
                and all(item.review_status in APPROVED for item in members)
                and all(item.eligibility == "approved_evidence_ready" for item in route_items)
            )
            build = {
                "compiler_version": "viewpoint-runtime-v1", "coverage_snapshot_id": coverage_snapshot_id,
                "viewpoint_revision_sha256": semantic_record_sha(revision),
                "member_link_sha256s": [semantic_record_sha(item) for item in members],
                "related_link_sha256s": [semantic_record_sha(item) for item in related],
                "route_snapshot_sha256s": [item.artifact_sha256 for item in route_items],
                "relation_sha256s": [semantic_record_sha(item) for item in relations],
            }
            fingerprint = sha256_json(build)
            base = {
                "schema_version": "wang_viewpoint_registry_snapshot_v1",
                "viewpoint_registry_snapshot_id": f"RGS-{fingerprint[:20]}",
                "viewpoint_id": viewpoint.viewpoint_id, "viewpoint_revision_id": viewpoint.current_revision_id,
                "coverage_snapshot_id": coverage_snapshot_id,
                "member_link_ids": [item.viewpoint_claim_link_id for item in members],
                "related_claim_link_ids": [item.viewpoint_claim_link_id for item in related],
                "argument_route_snapshot_ids": [item.argument_route_snapshot_id for item in route_items],
                "viewpoint_relation_ids": [item.viewpoint_relation_id for item in relations],
                "member_count": len(members), "source_count": len(sources), "route_count": len(route_items),
                "relation_count": len(relations),
                "eligibility": "approved_evidence_ready" if ready else "candidate_only",
                "build_fingerprint_sha256": fingerprint,
            }
            base["artifact_sha256"] = sha256_json(base)
            results.append(ViewpointRegistrySnapshot.model_validate(base))
        return results

    def compile_projection(
        self, *, consumer_kind: Literal["registry_review", "composition_plan", "qa_answer", "search_card"],
        coverage_snapshot_id: str, viewpoint_ids: Sequence[str] | None = None,
    ) -> ViewpointKnowledgeProjection:
        if coverage_snapshot_id in self.index.get(
            "viewpoint_atomic_coverage_snapshots", {}
        ):
            return self._compile_atomic_projection(
                consumer_kind=consumer_kind,
                coverage_snapshot_id=coverage_snapshot_id,
                viewpoint_ids=viewpoint_ids,
            )
        snapshots = self.compile_registry_snapshots(coverage_snapshot_id)
        selected_ids = sorted(set(viewpoint_ids or [item.viewpoint_id for item in snapshots]))
        selected = [item for item in snapshots if item.viewpoint_id in selected_ids]
        if {item.viewpoint_id for item in selected} != set(selected_ids):
            raise ViewpointRuntimeProjectionError(["projection scope contains unknown viewpoint"])
        coverage = self._coverage(coverage_snapshot_id)
        ledgers = sorted([
            item for item in self.records.get("viewpoint_resolution_ledgers", ())
            if item.coverage_snapshot_id == coverage_snapshot_id
        ], key=lambda item: (item.revision, item.resolution_ledger_id))
        ledger = ledgers[-1] if ledgers else None
        reports = sorted([
            item for item in self.records.get("viewpoint_quality_reports", ())
            if item.coverage_snapshot_id == coverage_snapshot_id
            and (not ledger or item.resolution_ledger_id == ledger.resolution_ledger_id)
        ], key=lambda item: (item.revision, item.quality_report_id))
        quality = reports[-1] if reports else None
        link_ids = {value for item in selected for value in item.member_link_ids}
        links = [self.index["viewpoint_claim_links"][value] for value in sorted(link_ids)]
        claims = [self.index["claims"][item.claim_id] for item in links if item.claim_id in self.index["claims"]]
        step_ids = sorted({value for item in claims for value in item.evidence_step_ids})
        steps = [self.index["evidence_steps"][value] for value in step_ids if value in self.index["evidence_steps"]]
        citation_ids = sorted({value for item in steps for value in item.citation_ids})
        citation_index = {_id(item, "citation_id"): item for item in self.citations}
        citations = [citation_index[value] for value in citation_ids if value in citation_index]
        relation_ids = {value for item in selected for value in item.viewpoint_relation_ids}
        relations = [self.index["viewpoint_relations"][value] for value in sorted(relation_ids)]
        route_snapshot_index = {
            item.argument_route_snapshot_id: item
            for item in self.compile_route_snapshots(coverage_snapshot_id)
        }
        selected_route_snapshots = [
            route_snapshot_index[value]
            for value in sorted({value for item in selected for value in item.argument_route_snapshot_ids})
        ]
        routes = [self.index["argument_routes"][item.argument_route_id] for item in selected_route_snapshots]
        route_revisions = [
            self.index["argument_route_revisions"][item.argument_route_revision_id]
            for item in selected_route_snapshots
        ]
        attestation_ids = sorted({
            value for item in selected_route_snapshots for value in item.active_attestation_ids
        })
        attestations = [self.index["argument_route_attestations"][value] for value in attestation_ids]
        fragment_ids = sorted({
            value for step in steps for value in evidence_fragment_ids(step)
        })
        fragments = [self.index["source_fragments"][value] for value in fragment_ids if value in self.index["source_fragments"]]
        source_ids = sorted({item.source_id for item in fragments} | {item.source_id for item in attestations})
        sources = [self.index["source_documents"][value] for value in source_ids if value in self.index["source_documents"]]
        blockers: list[str] = []
        if any(item.eligibility != "approved_evidence_ready" for item in selected): blockers.append("registry_not_evidence_ready")
        if not ledger or ledger.coverage_status != "complete": blockers.append("resolution_ledger_incomplete")
        if not quality or quality.eligibility_decision != "pass": blockers.append("quality_not_passed")
        if coverage.coverage_status != "complete": blockers.append("coverage_incomplete")
        if len(citations) != len(citation_ids) or any(_dump(item).get("status") != "approved" for item in citations): blockers.append("citation_not_public")
        if any(item.support_eligibility not in PUBLIC_EVIDENCE for item in steps): blockers.append("evidence_not_public")
        fragment_index = self.index.get("source_fragments", {})
        if any(
            not evidence_fragment_ids(step)
            or any(
                fragment_index.get(fragment_id) is None
                or fragment_index[fragment_id].anchor_state not in PUBLIC_ANCHORS
                for fragment_id in evidence_fragment_ids(step)
            )
            for step in steps
        ): blockers.append("source_anchor_not_public")
        composition_blockers = {"registry_not_evidence_ready", "resolution_ledger_incomplete", "quality_not_passed", "coverage_incomplete"}
        if not composition_blockers.intersection(blockers):
            eligibility = "public_attribution" if not blockers else "composition"
        else:
            eligibility = "internal_candidate"
        dependency_values: list[tuple[str, str, Any]] = [
            ("viewpoint_coverage_snapshots", coverage_snapshot_id, coverage),
            *[("viewpoint_registry_snapshots", item.viewpoint_registry_snapshot_id, item) for item in selected],
            *[("viewpoint_claim_links", item.viewpoint_claim_link_id, item) for item in links],
            *[("claims", item.claim_id, item) for item in claims],
            *[("evidence_steps", item.evidence_step_id, item) for item in steps],
            *[("citations", _id(item, "citation_id"), item) for item in citations],
            *[("viewpoint_relations", item.viewpoint_relation_id, item) for item in relations],
            *[("argument_route_snapshots", item.argument_route_snapshot_id, item) for item in selected_route_snapshots],
            *[("argument_routes", item.argument_route_id, item) for item in routes],
            *[("argument_route_revisions", item.argument_route_revision_id, item) for item in route_revisions],
            *[("argument_route_attestations", item.argument_route_attestation_id, item) for item in attestations],
            *[("source_fragments", item.fragment_id, item) for item in fragments],
            *[("source_documents", item.source_id, item) for item in sources],
        ]
        if ledger: dependency_values.append(("viewpoint_resolution_ledgers", ledger.resolution_ledger_id, ledger))
        if quality: dependency_values.append(("viewpoint_quality_reports", quality.quality_report_id, quality))
        manifest = sorted([
            ProjectionDependency(collection=collection, record_id=record_id,
                revision=int(_dump(value).get("revision", 1)), sha256=semantic_record_sha(value))
            for collection, record_id, value in dependency_values
        ], key=lambda item: (item.collection, item.record_id))
        manifest_sha = sha256_json([item.model_dump(mode="json") for item in manifest])
        base = {
            "schema_version": "wang_viewpoint_knowledge_projection_v1",
            "consumer_kind": consumer_kind, "scope_viewpoint_ids": selected_ids,
            "coverage_snapshot_id": coverage_snapshot_id,
            "resolution_ledger_id": ledger.resolution_ledger_id if ledger else None,
            "quality_report_id": quality.quality_report_id if quality else None,
            "eligibility": eligibility, "blocker_codes": sorted(set(blockers)),
            "viewpoints": [{"snapshot": item.model_dump(mode="json"),
                "revision": _dump(self.index["viewpoint_revisions"][item.viewpoint_revision_id])} for item in selected],
            "argument_routes": [{
                "snapshot": snapshot.model_dump(mode="json"),
                "route": _dump(self.index["argument_routes"][snapshot.argument_route_id]),
                "revision": _dump(self.index["argument_route_revisions"][snapshot.argument_route_revision_id]),
                "attestations": [
                    _dump(self.index["argument_route_attestations"][value])
                    for value in snapshot.active_attestation_ids
                ],
            } for snapshot in selected_route_snapshots],
            "expanded_claims": [_dump(item) for item in claims],
            "expanded_evidence": [_dump(item) for item in steps],
            "expanded_fragments": [_dump(item) for item in fragments],
            "expanded_sources": [_dump(item) for item in sources],
            "expanded_citations": [_dump(item) for item in citations],
            "relations": [_dump(item) for item in relations],
            "dependency_manifest": [item.model_dump(mode="json") for item in manifest],
            "dependency_manifest_sha256": manifest_sha,
        }
        base["projection_sha256"] = sha256_json(base)
        return ViewpointKnowledgeProjection.model_validate(base)

    def _compile_atomic_projection(
        self,
        *,
        consumer_kind: Literal[
            "registry_review", "composition_plan", "qa_answer", "search_card"
        ],
        coverage_snapshot_id: str,
        viewpoint_ids: Sequence[str] | None,
    ) -> ViewpointKnowledgeProjection:
        """Compile an approved PropositionUnit master boundary for consumers."""

        coverage = self.index["viewpoint_atomic_coverage_snapshots"][
            coverage_snapshot_id
        ]
        ledgers = sorted(
            [
                item
                for item in self.records.get(
                    "viewpoint_atomic_resolution_ledgers", ()
                )
                if item.atomic_coverage_snapshot_id == coverage_snapshot_id
            ],
            key=lambda item: (item.revision, item.atomic_resolution_ledger_id),
        )
        ledger = ledgers[-1] if ledgers else None
        reports = sorted(
            [
                item
                for item in self.records.get("viewpoint_atomic_quality_reports", ())
                if ledger
                and item.atomic_resolution_ledger_id
                == ledger.atomic_resolution_ledger_id
            ],
            key=lambda item: (item.revision, item.atomic_quality_report_id),
        )
        quality = reports[-1] if reports else None
        promotions = [
            item
            for item in self.records.get(
                "viewpoint_automated_promotion_decisions", ()
            )
            if quality
            and item.atomic_quality_report_artifact_sha256
            == quality.artifact_sha256
            and item.decision == "approve"
        ]
        available_ids = sorted({item.viewpoint_id for item in promotions})
        selected_ids = sorted(set(viewpoint_ids or available_ids))
        selected_promotions = [
            item for item in promotions if item.viewpoint_id in set(selected_ids)
        ]
        if sorted({item.viewpoint_id for item in selected_promotions}) != selected_ids:
            raise ViewpointRuntimeProjectionError(
                ["atomic projection scope contains unknown or unapproved viewpoint"]
            )

        viewpoints = self.index.get("canonical_viewpoints", {})
        revisions = self.index.get("viewpoint_revisions", {})
        units = self.index.get("viewpoint_proposition_units", {})
        all_links = self.index.get("viewpoint_proposition_unit_links", {})
        claims_index = self.index.get("claims", {})
        evidence_index = self.index.get("evidence_steps", {})
        fragment_index = self.index.get("source_fragments", {})
        source_index = self.index.get("source_documents", {})
        selected_links = sorted(
            [
                item
                for item in all_links.values()
                if item.viewpoint_id in set(selected_ids)
                and item.effective_state == "active"
            ],
            key=lambda item: (item.viewpoint_id, item.proposition_unit_id),
        )
        selected_unit_ids = sorted(
            {item.proposition_unit_id for item in selected_links}
        )
        missing_unit_ids = sorted(set(selected_unit_ids) - set(units))
        selected_units = [units[item] for item in selected_unit_ids if item in units]
        claim_ids = sorted({item.parent_claim_id for item in selected_units})
        claims = [claims_index[item] for item in claim_ids if item in claims_index]
        binding_pairs = sorted(
            {
                (binding.evidence_step_id, binding.source_fragment_id)
                for unit in selected_units
                for binding in unit.evidence_bindings
            }
        )
        steps = [
            evidence_index[evidence_id]
            for evidence_id in sorted({item[0] for item in binding_pairs})
            if evidence_id in evidence_index
        ]
        fragments = [
            fragment_index[fragment_id]
            for fragment_id in sorted({item[1] for item in binding_pairs})
            if fragment_id in fragment_index
        ]
        source_ids = sorted({item.source_id for item in fragments})
        sources = [source_index[item] for item in source_ids if item in source_index]
        citation_ids = sorted({value for item in steps for value in item.citation_ids})
        citation_index = {_id(item, "citation_id"): item for item in self.citations}
        citations = [
            citation_index[item] for item in citation_ids if item in citation_index
        ]

        blockers: list[str] = []
        if coverage.coverage_status != "complete":
            blockers.append("coverage_incomplete")
        if not ledger or ledger.coverage_status != "complete":
            blockers.append("resolution_ledger_incomplete")
        if not quality or quality.eligibility_decision != "pass":
            blockers.append("quality_not_passed")
        if len(selected_promotions) != len(selected_ids):
            blockers.append("promotion_not_approved")
        if missing_unit_ids:
            blockers.append("atomic_member_missing")
        if len(claims) != len(claim_ids) or len(steps) != len({x[0] for x in binding_pairs}):
            blockers.append("atomic_dependency_missing")
        if len(fragments) != len({x[1] for x in binding_pairs}) or len(sources) != len(source_ids):
            blockers.append("atomic_dependency_missing")
        for promotion in selected_promotions:
            viewpoint = viewpoints.get(promotion.viewpoint_id)
            revision = revisions.get(promotion.viewpoint_revision_id)
            member_links = [
                item for item in selected_links if item.viewpoint_id == promotion.viewpoint_id
            ]
            if not viewpoint or viewpoint.identity_status != "active":
                blockers.append("registry_not_evidence_ready")
            if not revision or revision.review_status not in APPROVED:
                blockers.append("registry_not_evidence_ready")
            if any(item.review_status not in APPROVED for item in member_links):
                blockers.append("registry_not_evidence_ready")
        public_blockers = list(blockers)
        if len(citations) != len(citation_ids) or any(
            _dump(item).get("status") != "approved" for item in citations
        ):
            public_blockers.append("citation_not_public")
        if any(item.support_eligibility not in PUBLIC_EVIDENCE for item in steps):
            public_blockers.append("evidence_not_public")
        if any(item.anchor_state not in PUBLIC_ANCHORS for item in fragments):
            public_blockers.append("source_anchor_not_public")
        blockers = sorted(set(public_blockers))
        composition_blockers = {
            "coverage_incomplete",
            "resolution_ledger_incomplete",
            "quality_not_passed",
            "promotion_not_approved",
            "atomic_member_missing",
            "atomic_dependency_missing",
            "registry_not_evidence_ready",
        }
        eligibility = (
            "internal_candidate"
            if composition_blockers.intersection(blockers)
            else "public_attribution"
            if not blockers
            else "composition"
        )

        dependency_values: list[tuple[str, str, Any]] = [
            ("viewpoint_atomic_coverage_snapshots", coverage_snapshot_id, coverage),
            *[("viewpoint_automated_promotion_decisions", item.automated_promotion_decision_id, item) for item in selected_promotions],
            *[("canonical_viewpoints", item.viewpoint_id, item) for item in (viewpoints.get(value) for value in selected_ids) if item],
            *[("viewpoint_revisions", item.viewpoint_revision_id, item) for item in (revisions.get(value.viewpoint_revision_id) for value in selected_promotions) if item],
            *[("viewpoint_proposition_unit_links", item.viewpoint_proposition_unit_link_id, item) for item in selected_links],
            *[("viewpoint_proposition_units", item.proposition_unit_id, item) for item in selected_units],
            *[("claims", item.claim_id, item) for item in claims],
            *[("evidence_steps", item.evidence_step_id, item) for item in steps],
            *[("source_fragments", item.fragment_id, item) for item in fragments],
            *[("source_documents", item.source_id, item) for item in sources],
            *[("citations", _id(item, "citation_id"), item) for item in citations],
        ]
        if ledger:
            dependency_values.append(
                (
                    "viewpoint_atomic_resolution_ledgers",
                    ledger.atomic_resolution_ledger_id,
                    ledger,
                )
            )
        if quality:
            dependency_values.append(
                (
                    "viewpoint_atomic_quality_reports",
                    quality.atomic_quality_report_id,
                    quality,
                )
            )
        manifest = sorted(
            [
                ProjectionDependency(
                    collection=collection,
                    record_id=record_id,
                    revision=int(_dump(value).get("revision", 1)),
                    sha256=semantic_record_sha(value),
                )
                for collection, record_id, value in dependency_values
            ],
            key=lambda item: (item.collection, item.record_id),
        )
        manifest_payload = [item.model_dump(mode="json") for item in manifest]
        viewpoint_rows = []
        for promotion in sorted(selected_promotions, key=lambda item: item.viewpoint_id):
            viewpoint = viewpoints[promotion.viewpoint_id]
            revision = revisions[promotion.viewpoint_revision_id]
            member_units = [
                unit
                for unit in selected_units
                if any(
                    link.viewpoint_id == promotion.viewpoint_id
                    and link.proposition_unit_id == unit.proposition_unit_id
                    for link in selected_links
                )
            ]
            viewpoint_rows.append({
                "viewpoint": _dump(viewpoint),
                "revision": _dump(revision),
                "member_proposition_units": [_dump(item) for item in member_units],
                "automated_promotion_decision": _dump(promotion),
            })
        base = {
            "schema_version": "wang_viewpoint_knowledge_projection_v1",
            "consumer_kind": consumer_kind,
            "scope_viewpoint_ids": selected_ids,
            "coverage_snapshot_id": coverage_snapshot_id,
            "resolution_ledger_id": ledger.atomic_resolution_ledger_id if ledger else None,
            "quality_report_id": quality.atomic_quality_report_id if quality else None,
            "eligibility": eligibility,
            "blocker_codes": blockers,
            "viewpoints": viewpoint_rows,
            "argument_routes": [],
            "expanded_claims": [_dump(item) for item in claims],
            "expanded_evidence": [_dump(item) for item in steps],
            "expanded_fragments": [_dump(item) for item in fragments],
            "expanded_sources": [_dump(item) for item in sources],
            "expanded_citations": [_dump(item) for item in citations],
            "relations": [],
            "dependency_manifest": manifest_payload,
            "dependency_manifest_sha256": sha256_json(manifest_payload),
        }
        base["projection_sha256"] = sha256_json(base)
        return ViewpointKnowledgeProjection.model_validate(base)


def build_projection_dependencies(
    projection: ViewpointKnowledgeProjection, *, consumer_id: str
) -> list[ProductDependencyRecord]:
    """Materialize exact downstream pins used by ChangeSet invalidation."""
    registry_ids = sorted(
        item["snapshot"]["viewpoint_registry_snapshot_id"]
        for item in projection.viewpoints
        if "snapshot" in item
    )
    viewpoint_revision_ids = sorted(
        item["revision"]["viewpoint_revision_id"] for item in projection.viewpoints
    )
    route_snapshot_ids = sorted(
        item["snapshot"]["argument_route_snapshot_id"]
        for item in projection.argument_routes
    )
    route_revision_ids = sorted(
        item["revision"]["argument_route_revision_id"]
        for item in projection.argument_routes
    )
    route_ids = sorted(item["route"]["argument_route_id"] for item in projection.argument_routes)
    quality_dependency = next(
        (
            item
            for item in projection.dependency_manifest
            if item.collection in {
                "viewpoint_quality_reports",
                "viewpoint_atomic_quality_reports",
            }
        ),
        None,
    )
    manifest = [item.model_dump(mode="json") for item in projection.dependency_manifest]
    dependencies: list[ProductDependencyRecord] = []
    for claim in projection.expanded_claims:
        identity = {
            "consumer_kind": projection.consumer_kind,
            "consumer_id": consumer_id,
            "claim_id": claim["claim_id"],
            "projection_sha256": projection.projection_sha256,
        }
        dependencies.append(ProductDependencyRecord(
            dependency_id=f"KDEP-{sha256_json(identity)[:20]}",
            consumer_kind=projection.consumer_kind,
            consumer_id=consumer_id,
            claim_id=str(claim["claim_id"]),
            pinned_claim_revision=int(claim.get("revision", 1)),
            route_ids=route_ids,
            viewpoint_revision_ids=viewpoint_revision_ids,
            viewpoint_registry_snapshot_ids=registry_ids,
            argument_route_revision_ids=route_revision_ids,
            argument_route_snapshot_ids=route_snapshot_ids,
            coverage_snapshot_id=projection.coverage_snapshot_id,
            resolution_ledger_id=projection.resolution_ledger_id,
            quality_report_id=projection.quality_report_id,
            quality_report_sha256=quality_dependency.sha256 if quality_dependency else None,
            projection_sha256=projection.projection_sha256,
            dependency_manifest=manifest,
            review_status="system_verified",
        ))
    return dependencies
