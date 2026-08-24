from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.canonical_repository.knowledge_models import (
    KNOWLEDGE_COLLECTIONS,
    ArgumentRouteAttestationRecord,
    ArgumentRouteRecord,
    ArgumentRouteRevisionRecord,
    CanonicalViewpointRecord,
    ClaimRecord,
    EvidenceStepRecord,
    KnowledgeSourceDocument,
    SourceFragmentRecord,
    ViewpointClaimLinkRecord,
    ViewpointCoverageSnapshotRecord,
    ViewpointQualityReportRecord,
    ViewpointResolutionLedgerRecord,
    ViewpointRevisionRecord,
    ViewpointRelationRecord,
)
from backend.api.canonical_repository.models import Citation
from backend.api.canonical_repository.viewpoint_admin_projection import (
    AdminViewpointProjectionCompiler,
    AdminViewpointProjectionError,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_recall_blocking import (
    build_viewpoint_recall_blocking,
)
from backend.api import viewpoint_admin


class FakeStore:
    def __init__(self, records, citations):
        self.records = records
        self.citations = citations

    def list_knowledge_records(self, collection):
        return self.records.get(collection, [])

    def list_citations(self):
        return self.citations


def _fixture() -> FakeStore:
    records = defaultdict(list)
    source = KnowledgeSourceDocument(
        source_id="SRC-16", source_type="sermon_transcript", title="马太福音十六章",
        source_url="https://example.test/sermon/16", source_sha256="s" * 64,
        review_status="approved",
    )
    fragment = SourceFragmentRecord(
        fragment_id="FR-ROCK", source_id=source.source_id,
        verbatim_excerpt="我要把我的教会建造在这磐石上。", paragraph_key="p16",
        media_time=618.0, citation_id="CIT-ROCK", source_sha256="s" * 64,
        anchor_state="source_version_bound", review_status="approved",
    )
    evidence = EvidenceStepRecord(
        evidence_step_id="EV-ROCK", source_fragment_id=fragment.fragment_id,
        statement="彼得与磐石在句法上相关，但正面所指仍需区分。",
        citation_ids=["CIT-ROCK"], support_eligibility="eligible",
        scripture_refs=["Matt 16:18"],
        speaker="王教授", stance="affirmed", review_status="approved",
    )
    peter_claim = ClaimRecord(
        claim_id="CL-PETER", statement="彼得在此承担教会建造的代表性角色",
        claim_type="explicit_claim", scripture_refs=["Matt 16:18"], topic_ids=["TOP-CHURCH"],
        evidence_step_ids=[evidence.evidence_step_id], review_status="approved",
    )
    rock_claim = ClaimRecord(
        claim_id="CL-ROCK", statement="磐石的正面所指不是简单等同于彼得个人",
        claim_type="explicit_claim", scripture_refs=["Matt 16:18"], topic_ids=["TOP-CHURCH"],
        evidence_step_ids=[evidence.evidence_step_id], review_status="approved",
    )
    viewpoints = [
        CanonicalViewpointRecord(
            viewpoint_id="CV-PETER", current_revision_id="CVR-PETER-1",
            created_from_candidate_id="VIC-PETER", review_status="system_approved",
        ),
        CanonicalViewpointRecord(
            viewpoint_id="CV-ROCK", current_revision_id="CVR-ROCK-1",
            created_from_candidate_id="VIC-ROCK", review_status="human_approved",
        ),
    ]
    signature = {
        "subject": "彼得与磐石", "predicate": "需要", "object": "区分",
        "polarity": "affirmed", "modality": "asserted",
        "temporal_scope": [], "conditions": [], "population_scope": [],
    }
    revisions = [
        ViewpointRevisionRecord(
            viewpoint_revision_id="CVR-PETER-1", viewpoint_id="CV-PETER", revision_number=1,
            core_proposition="彼得因认信承担使徒群体的代表性角色",
            proposition_signature=signature, scope={"scripture_scope": ["Matt 16:18"]},
            provenance={"basis_identity_decision_ids": ["VID-PETER"], "review_artifact_sha256": "a" * 64},
            approved_by="system:viewpoint-resolution", approved_at="2026-08-22T12:00:00Z",
            review_status="system_approved",
        ),
        ViewpointRevisionRecord(
            viewpoint_revision_id="CVR-ROCK-1", viewpoint_id="CV-ROCK", revision_number=1,
            core_proposition="磐石的正面所指不可简化为彼得个人",
            proposition_signature=signature, scope={"scripture_scope": ["Matt 16:18"]},
            provenance={"basis_identity_decision_ids": ["VID-ROCK"], "review_artifact_sha256": "b" * 64},
            approved_by="editor:fixture", approved_at="2026-08-22T12:00:00Z",
            review_status="human_approved",
        ),
    ]
    links = [
        ViewpointClaimLinkRecord(
            viewpoint_claim_link_id="VCL-PETER", viewpoint_id="CV-PETER",
            validated_against_viewpoint_revision_id="CVR-PETER-1", claim_id="CL-PETER",
            pinned_claim_revision=1,
            link_type="equivalent_full", decision_id="VID-PETER", review_status="system_approved",
        ),
        ViewpointClaimLinkRecord(
            viewpoint_claim_link_id="VCL-ROCK", viewpoint_id="CV-ROCK",
            validated_against_viewpoint_revision_id="CVR-ROCK-1", claim_id="CL-ROCK",
            pinned_claim_revision=1,
            link_type="equivalent_full", decision_id="VID-ROCK", review_status="human_approved",
        ),
        ViewpointClaimLinkRecord(
            viewpoint_claim_link_id="VCL-TENSION", viewpoint_id="CV-PETER",
            validated_against_viewpoint_revision_id="CVR-PETER-1", claim_id="CL-ROCK",
            pinned_claim_revision=1,
            link_type="tension_evidence", decision_id="VID-PETER", review_status="system_approved",
        ),
    ]
    coverage = ViewpointCoverageSnapshotRecord(
        coverage_snapshot_id="CVS-16", source_universe_manifest_id="SUM-16",
        source_universe_manifest_sha256="u" * 64,
        sources=[{"source_id": "SRC-16", "source_revision_id": "SRC-16@1", "source_sha256": "s" * 64,
                  "roles": ["detailed_extraction", "source_universe", "viewpoint_reviewed"]}],
        sources_sha256="x" * 64, coverage_status="complete",
        created_at="2026-08-22T12:00:00Z",
    )
    ledger = ViewpointResolutionLedgerRecord(
        resolution_ledger_id="VRL-16", coverage_snapshot_id="CVS-16",
        input_claim_manifest_sha256="m" * 64, eligibility_policy_version="v1",
        candidate_blocking_version="v1",
        rows=[
            {"claim_id": "CL-PETER", "pinned_claim_revision": 1, "claim_revision_sha256": "p" * 64,
             "processing_status": "resolved", "resolution_kind": "member_existing",
             "primary_viewpoint_id": "CV-PETER", "viewpoint_claim_link_id": "VCL-PETER", "decision_id": "VID-PETER"},
            {"claim_id": "CL-ROCK", "pinned_claim_revision": 1, "claim_revision_sha256": "r" * 64,
             "processing_status": "resolved", "resolution_kind": "member_existing",
             "primary_viewpoint_id": "CV-ROCK", "viewpoint_claim_link_id": "VCL-ROCK", "decision_id": "VID-ROCK"},
        ],
        statistics={"input_claim_count": 2, "resolved_count": 2, "source_ineligible_count": 0,
                    "deferred_count": 0, "unprocessed_count": 0},
        coverage_status="complete", build_fingerprint_sha256="f" * 64, artifact_sha256="l" * 64,
    )
    dimensions = [
        {"dimension": name, "applicable": True, "minimum_policy": "fixture", "status": "pass"}
        for name in sorted({"provenance_integrity", "source_maturity", "resolution_coverage",
                            "candidate_recall", "identity_precision", "route_fidelity", "temporal_correctness",
                            "consumer_projection_integrity"})
    ]
    quality = ViewpointQualityReportRecord(
        quality_report_id="VQR-16", scope_kind="registry_snapshot", scope_ids=["CVS-16"],
        coverage_snapshot_id="CVS-16", resolution_ledger_id="VRL-16",
        input_artifact_sha256s=["i" * 64], dimensions=dimensions,
        eligibility_decision="pass", validator_version="v1",
        build_fingerprint_sha256="q" * 64, artifact_sha256="z" * 64,
    )
    route = ArgumentRouteRecord(
        argument_route_id="AR-PETER", conclusion_viewpoint_id="CV-PETER",
        current_revision_id="ARR-PETER-1", review_status="system_approved",
    )
    route_revision = ArgumentRouteRevisionRecord(
        argument_route_revision_id="ARR-PETER-1", argument_route_id="AR-PETER",
        revision_number=1, validated_against_conclusion_viewpoint_revision_id="CVR-PETER-1",
        route_label="文本—认信—代表性角色", route_signature={
            "inference_method_codes": ["theological_synthesis"],
            "conclusion_viewpoint_id": "CV-PETER",
        }, ordered_inference_nodes=[
            {"route_step_key": "P1", "role": "premise",
             "normalized_proposition": "文本与认信共同说明彼得的代表性角色",
             "required_for_full_attestation": True},
            {"route_step_key": "C1", "role": "conclusion",
             "conclusion_viewpoint_revision_id": "CVR-PETER-1",
             "required_for_full_attestation": True},
        ], review_artifact_sha256="d" * 64, approved_by="system:viewpoint-resolution",
        approved_at="2026-08-22T12:00:00Z", review_status="system_approved",
    )
    attestation = ArgumentRouteAttestationRecord(
        argument_route_attestation_id="ARA-PETER", argument_route_id="AR-PETER",
        validated_against_route_revision_id="ARR-PETER-1", source_id="SRC-16",
        source_revision_sha256="s" * 64, claim_ids=["CL-PETER"], occurrence_ref_id="FR-ROCK",
        step_bindings=[
            {"route_step_key": key, "claim_component_keys": ["CCK-fixture"],
             "evidence_step_ids": ["EV-ROCK"], "source_fragment_ids": ["FR-ROCK"],
             "attestation_status": "attested"}
            for key in ("P1", "C1")
        ], terminal_claim_link_id="VCL-PETER",
        completeness="full", scripture_refs_derived=["Matt 16:18"],
        review_artifact_sha256="d" * 64, review_status="system_approved",
    )
    viewpoint_relation = ViewpointRelationRecord(
        viewpoint_relation_id="VPR-PETER-ROCK", source_viewpoint_id="CV-PETER",
        target_viewpoint_id="CV-ROCK", validated_source_viewpoint_revision_id="CVR-PETER-1",
        validated_target_viewpoint_revision_id="CVR-ROCK-1", relation_type="tensions_with",
        reason="彼得的代表性角色不等于磐石所指可以简化为彼得个人。",
        supporting_claim_ids=["CL-ROCK"], review_status="system_approved",
    )
    for collection, rows in {
        "source_documents": [source], "source_fragments": [fragment], "evidence_steps": [evidence],
        "claims": [peter_claim, rock_claim], "canonical_viewpoints": viewpoints,
        "viewpoint_revisions": revisions, "viewpoint_claim_links": links,
        "viewpoint_coverage_snapshots": [coverage], "viewpoint_resolution_ledgers": [ledger],
        "viewpoint_quality_reports": [quality], "argument_routes": [route],
        "argument_route_revisions": [route_revision],
        "argument_route_attestations": [attestation], "viewpoint_relations": [viewpoint_relation],
    }.items():
        records[collection] = rows
    for collection in KNOWLEDGE_COLLECTIONS:
        records.setdefault(collection, [])
    citation = Citation(
        citation_id="CIT-ROCK", source_id="SRC-16", source_sha256="s" * 64,
        locator={"kind": "transcript", "paragraph_keys": ["p16"], "highlight_text": fragment.verbatim_excerpt,
                 "highlight_text_sha256": "h" * 64, "start_time": 618.0},
        status="approved",
    )
    return FakeStore(records, [citation])


def _recall_fixture(store: FakeStore):
    claims = [
        ClaimRecord.model_validate(
            {**item.model_dump(mode="json"), "topic_terms": ["彼得與磐石"]}
        )
        for item in store.records["claims"]
    ]
    payload = {
        "schema_version": "viewpoint_input_claim_manifest_v1",
        "coverage_snapshot_id": "CVS-16",
        "claims": [
            {
                "claim_id": item.claim_id,
                "pinned_claim_revision": item.revision,
                "claim_revision_sha256": semantic_record_sha(item),
                "source_id": "SRC-16",
            }
            for item in claims
        ],
    }
    payload["manifest_sha256"] = sha256_json(payload)
    return build_viewpoint_recall_blocking(
        claim_manifest=payload, claims=claims
    )


def test_overview_and_list_are_bound_to_one_projection_snapshot():
    compiler = AdminViewpointProjectionCompiler(_fixture())
    overview = compiler.overview()
    listing = compiler.list_viewpoints(limit=1)

    assert overview["as_of"]["coverage_snapshot_id"] == "CVS-16"
    assert overview["data"]["source_coverage"] == {"covered": 1, "total": 1, "status": "complete"}
    assert listing["projection_sha256"] == overview["projection_sha256"]
    assert listing["data"]["total"] == 2
    assert listing["data"]["next_cursor"]


def test_recall_diagnostics_are_sha_bound_and_show_candidate_evidence():
    store = _fixture()
    recall = _recall_fixture(store)
    compiler = AdminViewpointProjectionCompiler(store, recall_blocking=recall)

    overview = compiler.overview()
    diagnostics = compiler.recall_diagnostics()

    assert overview["data"]["recall"]["artifact_sha256"] == recall.artifact_sha256
    assert diagnostics["projection_sha256"] == overview["projection_sha256"]
    assert diagnostics["data"]["statistics"]["unique_candidate_pair_count"] == 1
    assert diagnostics["data"]["items"][0]["neighbors"][0]["statement"]


def test_peter_and_rock_remain_distinct_and_tension_is_not_membership():
    detail = AdminViewpointProjectionCompiler(_fixture()).detail("CV-PETER")

    assert [item["claim"]["claim_id"] for item in detail["data"]["members"]] == ["CL-PETER"]
    assert detail["data"]["relations"] == [
        {
            "relation_id": "VPR-PETER-ROCK", "relation_type": "tensions_with",
            "from_viewpoint_id": "CV-PETER", "to_viewpoint_id": "CV-ROCK",
            "claim_id": "CL-ROCK", "claim_statement": "彼得的代表性角色不等于磐石所指可以简化为彼得个人。",
            "supporting_relation_ids": [], "review_status": "system_approved",
        }
    ]
    assert {node["id"] for node in detail["data"]["graph"]["nodes"]} >= {"CV-PETER", "CV-ROCK"}


def test_detail_drills_to_evidence_citation_and_source_locator():
    detail = AdminViewpointProjectionCompiler(_fixture()).detail("CV-PETER")
    evidence = detail["data"]["members"][0]["evidence"][0]

    assert evidence["citations"][0]["citation_id"] == "CIT-ROCK"
    assert evidence["locator"] == {
        "source_url": "https://example.test/sermon/16",
        "source_admin_url": "/admin/wang/source-coverage?source=SRC-16&fragment=FR-ROCK",
        "source_file_name": None,
        "source_type": "sermon_transcript",
        "paragraph_key": "p16",
        "media_time": 618.0,
    }


def test_detail_expands_route_nodes_and_source_local_evidence():
    detail = AdminViewpointProjectionCompiler(_fixture()).detail("CV-PETER")
    route = detail["data"]["routes"][0]

    assert route["coverage"] == {
        "mode": "coverage_snapshot",
        "eligibility": "approved_evidence_ready",
        "full_attestation_count": 1,
        "partial_attestation_count": 0,
    }
    displayed = route["attestations"][0]
    assert displayed["source"]["source_id"] == "SRC-16"
    assert [item["node"]["role"] for item in displayed["bindings"]] == [
        "premise", "conclusion",
    ]
    route_evidence = displayed["bindings"][0]["evidence"][0]
    assert route_evidence["evidence_step"]["evidence_step_id"] == "EV-ROCK"
    assert route_evidence["fragments"][0]["source_fragment"]["fragment_id"] == "FR-ROCK"
    assert route_evidence["fragments"][0]["locator"]["source_admin_url"].endswith(
        "source=SRC-16&fragment=FR-ROCK"
    )


def test_detail_uses_current_registry_attestations_without_legacy_coverage():
    store = _fixture()
    store.records["viewpoint_coverage_snapshots"] = []
    store.records["viewpoint_resolution_ledgers"] = []
    store.records["viewpoint_quality_reports"] = []

    route = AdminViewpointProjectionCompiler(store).detail("CV-PETER")["data"]["routes"][0]

    assert route["snapshot"] is None
    assert route["coverage"] == {
        "mode": "current_registry",
        "eligibility": "approved_evidence_ready",
        "full_attestation_count": 1,
        "partial_attestation_count": 0,
    }
    assert route["evidence_step_ids"] == ["EV-ROCK"]
    assert len(route["attestations"]) == 1


def test_cursor_and_requested_snapshot_fail_closed():
    compiler = AdminViewpointProjectionCompiler(_fixture())

    with pytest.raises(AdminViewpointProjectionError, match="cursor"):
        compiler.list_viewpoints(cursor="not-a-cursor")
    with pytest.raises(AdminViewpointProjectionError, match="stale or unknown"):
        compiler.detail("CV-PETER", registry_snapshot_id="RGS-OLD")


def test_projection_sha_binds_citations_and_historical_coverage_fails_closed():
    first_store = _fixture()
    first_sha = AdminViewpointProjectionCompiler(first_store).overview()["projection_sha256"]
    first_store.citations[0] = first_store.citations[0].model_copy(update={"status": "stale"})
    second_sha = AdminViewpointProjectionCompiler(first_store).overview()["projection_sha256"]
    assert second_sha != first_sha

    older = first_store.records["viewpoint_coverage_snapshots"][0].model_copy(
        update={"coverage_snapshot_id": "CVS-OLD", "created_at": "2026-08-21T12:00:00Z"}
    )
    first_store.records["viewpoint_coverage_snapshots"].append(older)
    with pytest.raises(AdminViewpointProjectionError, match="current records were not mixed"):
        AdminViewpointProjectionCompiler(first_store).overview("CVS-OLD")


def test_read_only_http_boundary_exposes_projection_and_no_mutation(monkeypatch):
    store = _fixture()
    compiler = AdminViewpointProjectionCompiler(
        store, recall_blocking=_recall_fixture(store)
    )
    monkeypatch.setattr(viewpoint_admin, "_compiler", lambda: compiler)
    app = FastAPI()
    app.include_router(viewpoint_admin.router)
    client = TestClient(app)

    overview = client.get("/admin/wang/viewpoints/overview")
    listing = client.get("/admin/wang/viewpoints", params={"q": "代表性"})
    detail = client.get("/admin/wang/viewpoints/CV-PETER")
    recall = client.get("/admin/wang/viewpoints/recall-blocking")

    assert overview.status_code == 200
    assert listing.status_code == 200
    assert [item["viewpoint_id"] for item in listing.json()["data"]["items"]] == ["CV-PETER"]
    assert detail.json()["data"]["members"][0]["claim"]["claim_id"] == "CL-PETER"
    assert recall.status_code == 200
    assert recall.json()["data"]["available"] is True
    assert client.post("/admin/wang/viewpoints/CV-PETER", json={}).status_code == 405
    assert client.post("/admin/wang/viewpoint-exceptions/VEX-1/changesets", json={}).status_code == 404


def test_viewpoint_pilot_is_optional_and_fails_closed(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(viewpoint_admin.router)
    client = TestClient(app)

    monkeypatch.delenv("WANG_VIEWPOINT_PILOT_FILE", raising=False)
    absent = client.get("/admin/wang/viewpoints/pilot")
    assert absent.status_code == 404
    assert absent.json()["detail"] == "No viewpoint pilot is configured"

    monkeypatch.setenv(
        "WANG_VIEWPOINT_PILOT_FILE", str(tmp_path / "missing-pilot.json")
    )
    missing = client.get("/admin/wang/viewpoints/pilot")
    assert missing.status_code == 503
    assert "configured viewpoint pilot does not exist" in missing.json()["detail"]

    malformed = tmp_path / "malformed-pilot.json"
    malformed.write_text('{"schema_version":"not-a-pilot"}', encoding="utf-8")
    monkeypatch.setenv("WANG_VIEWPOINT_PILOT_FILE", str(malformed))
    invalid = client.get("/admin/wang/viewpoints/pilot")
    assert invalid.status_code == 503
    assert "Viewpoint pilot unavailable" in invalid.json()["detail"]
