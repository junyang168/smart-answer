from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.knowledge_models import (
    ArgumentRouteAttestationRecord,
    ArgumentRouteRecord,
    ArgumentRouteRevisionRecord,
    EvidenceStepRecord,
    KnowledgeSourceDocument,
    SourceFragmentRecord,
    ViewpointCoverageSnapshotRecord,
)
from backend.api.canonical_repository.viewpoint_runtime_projection import (
    ViewpointRuntimeCompiler,
    ViewpointRuntimeProjectionError,
    build_projection_dependencies,
    validate_runtime_authoring_graph,
)
from backend.tests.test_viewpoint_admin_projection import _fixture


def _serialized(store):
    result = {}
    for collection, rows in store.records.items():
        result[collection] = {
            ViewpointRuntimeCompiler._record_id(collection, item): item.model_dump(mode="json")
            for item in rows
        }
    return result


def test_projection_is_public_ready_and_binds_every_dependency():
    store = _fixture()
    projection = ViewpointRuntimeCompiler(store.records, store.citations).compile_projection(
        consumer_kind="qa_answer", coverage_snapshot_id="CVS-16", viewpoint_ids=["CV-PETER"]
    )

    assert projection.eligibility == "public_attribution"
    assert projection.blocker_codes == []
    assert projection.expanded_claims[0]["claim_id"] == "CL-PETER"
    assert projection.expanded_evidence[0]["evidence_step_id"] == "EV-ROCK"
    assert projection.expanded_citations[0]["citation_id"] == "CIT-ROCK"
    assert {item.collection for item in projection.dependency_manifest} >= {
        "viewpoint_registry_snapshots", "claims", "evidence_steps", "citations",
        "viewpoint_resolution_ledgers", "viewpoint_quality_reports",
    }
    dependencies = build_projection_dependencies(projection, consumer_id="QA-16-18")
    assert len(dependencies) == 1
    assert dependencies[0].projection_sha256 == projection.projection_sha256
    assert dependencies[0].viewpoint_revision_ids == ["CVR-PETER-1"]
    assert dependencies[0].argument_route_revision_ids == ["ARR-PETER-1"]
    assert any(
        item["collection"] == "argument_route_attestations"
        for item in dependencies[0].dependency_manifest
    )
    tampered = projection.model_dump(mode="json")
    tampered["expanded_claims"][0]["statement"] = "被静默篡改"
    with pytest.raises(ValidationError, match="projection SHA mismatch"):
        type(projection).model_validate(tampered)


def test_projection_sha_changes_when_citation_changes_and_public_fails_closed():
    store = _fixture()
    compiler = ViewpointRuntimeCompiler(store.records, store.citations)
    before = compiler.compile_projection(
        consumer_kind="search_card", coverage_snapshot_id="CVS-16", viewpoint_ids=["CV-PETER"]
    )
    store.citations[0] = store.citations[0].model_copy(update={"status": "stale"})
    after = ViewpointRuntimeCompiler(store.records, store.citations).compile_projection(
        consumer_kind="search_card", coverage_snapshot_id="CVS-16", viewpoint_ids=["CV-PETER"]
    )

    assert after.projection_sha256 != before.projection_sha256
    assert after.eligibility == "composition"
    assert "citation_not_public" in after.blocker_codes


def test_route_snapshots_keep_distinct_routes_and_count_only_full_sources():
    store = _fixture()
    store.records["source_documents"].append(KnowledgeSourceDocument(
        source_id="SRC-16-B", source_type="lecture", source_sha256="b" * 64,
        review_status="approved",
    ))
    store.records["source_fragments"].append(SourceFragmentRecord(
        fragment_id="FR-ROCK-B", source_id="SRC-16-B", verbatim_excerpt="第二处论证",
        source_sha256="b" * 64, anchor_state="source_version_bound", review_status="approved",
    ))
    store.records["evidence_steps"].append(EvidenceStepRecord(
        evidence_step_id="EV-ROCK-B", source_fragment_id="FR-ROCK-B",
        statement="第二处局部论证", scripture_refs=["Matt 16:18"],
        support_eligibility="eligible", review_status="approved",
    ))
    coverage = store.records["viewpoint_coverage_snapshots"][0]
    coverage_payload = coverage.model_dump(mode="json")
    coverage_payload["sources"] = [*coverage_payload["sources"], {
            "source_id": "SRC-16-B", "source_revision_id": "SRC-16-B@1",
            "source_sha256": "b" * 64,
            "roles": ["detailed_extraction", "source_universe", "viewpoint_reviewed"],
        }]
    coverage_payload["sources"].sort(key=lambda item: item["source_revision_id"])
    store.records["viewpoint_coverage_snapshots"][0] = ViewpointCoverageSnapshotRecord.model_validate(
        coverage_payload
    )
    store.records["argument_route_attestations"].append(ArgumentRouteAttestationRecord(
        argument_route_attestation_id="ARA-PETER-B", argument_route_id="AR-PETER",
        validated_against_argument_route_revision_id="ARR-PETER-1", source_id="SRC-16-B",
        claim_id="CL-PETER", occurrence_ref_id="FR-ROCK-B",
        ordered_evidence_step_ids=["EV-ROCK-B"], terminal_claim_link_id="VCL-PETER",
        completeness="partial", scripture_refs=["Matt 16:18"], review_status="system_approved",
    ))
    store.records["argument_routes"].append(ArgumentRouteRecord(
        argument_route_id="AR-PETER-SECOND", conclusion_viewpoint_id="CV-PETER",
        current_revision_id="ARR-PETER-SECOND-1", review_status="system_approved",
    ))
    store.records["argument_route_revisions"].append(ArgumentRouteRevisionRecord(
        argument_route_revision_id="ARR-PETER-SECOND-1", argument_route_id="AR-PETER-SECOND",
        revision_number=1, validated_against_conclusion_viewpoint_revision_id="CVR-PETER-1",
        route_label="第二条独立路线", route_signature={
            "premise_roles": ["context"], "inference_pattern": "contextual_inference",
            "conclusion_viewpoint_id": "CV-PETER",
        }, review_artifact_sha256="e" * 64, approved_by="system:viewpoint-resolution",
        approved_at="2026-08-22T12:00:00Z", review_status="system_approved",
    ))

    snapshots = ViewpointRuntimeCompiler(store.records, store.citations).compile_route_snapshots("CVS-16")

    assert [item.argument_route_id for item in snapshots] == ["AR-PETER", "AR-PETER-SECOND"]
    assert snapshots[0].full_attestation_count == 1
    assert snapshots[0].partial_attestation_count == 1
    assert snapshots[0].distinct_full_source_count == 1
    assert snapshots[1].eligibility == "candidate_only"


def test_authoring_graph_rejects_cross_source_and_stale_attestation():
    store = _fixture()
    graph = _serialized(store)
    attestation = graph["argument_route_attestations"]["ARA-PETER"]
    attestation["source_id"] = "SRC-OTHER"
    attestation["validated_against_argument_route_revision_id"] = "ARR-OLD"

    findings = validate_runtime_authoring_graph(graph)

    assert any("source-local" in item for item in findings)
    assert any("route revision is stale" in item for item in findings)


def test_snapshot_compiler_rejects_stale_active_attestation():
    store = _fixture()
    store.records["argument_route_attestations"][0] = store.records[
        "argument_route_attestations"
    ][0].model_copy(update={"validated_against_argument_route_revision_id": "ARR-OLD"})

    with pytest.raises(ViewpointRuntimeProjectionError, match="stale attestations"):
        ViewpointRuntimeCompiler(store.records, store.citations).compile_route_snapshots("CVS-16")
