from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.knowledge_importer import KnowledgePackageImporter
from backend.api.canonical_repository.models import Citation
from backend.api.canonical_repository.knowledge_models import (
    CanonicalViewpointRecord,
    ViewpointClaimLinkRecord,
    ViewpointComponentLocator,
    ViewpointCoverageSnapshotRecord,
    ViewpointIdentityDecisionRecord,
    ViewpointQualityReportRecord,
    ViewpointResolutionRow,
    ViewpointResolutionLedgerRecord,
    ViewpointRevisionRecord,
)
from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    build_change_set_plan,
    normalize_package,
)
from backend.api.canonical_repository.store import RepositoryStore
from backend.api.canonical_repository.viewpoint_foundation import (
    CLAIM_MANIFEST_VERSION,
    ViewpointFoundationValidationError,
    build_coverage_snapshot,
    build_foundation_quality_report,
    build_identity_candidate_seeds,
    build_input_claim_manifest,
    build_resolution_ledger,
    sha256_json,
)


def _source(source_id: str = "SRC-1") -> dict:
    return {
        "source_id": source_id,
        "source_type": "sermon_transcript",
        "title": "太十六章",
        "source_sha256": f"source-sha-{source_id}",
        "revision": 1,
    }


def _fragment(source_id: str = "SRC-1", suffix: str = "1") -> dict:
    return {
        "fragment_id": f"FR-{suffix}",
        "source_id": source_id,
        "verbatim_excerpt": "你是彼得，我要把我的教会建造在这磐石上。",
        "citation_id": f"CIT-{suffix}",
        "source_sha256": f"source-sha-{source_id}",
        "anchor_state": "source_version_bound",
    }


def _evidence(suffix: str = "1") -> dict:
    return {
        "evidence_step_id": f"E-{suffix}",
        "source_fragment_id": f"FR-{suffix}",
        "statement": "释经证据",
        "support_eligibility": "eligible",
        "citation_ids": [f"CIT-{suffix}"],
    }


def _claim(claim_id: str = "CL-PETER", suffix: str = "1", *, status: str = "approved") -> dict:
    return {
        "claim_id": claim_id,
        "statement": {
            "CL-PETER": "彼得本人是磐石",
            "CL-CONFESSION": "磐石是彼得所承认的基督与真理",
            "CL-REPRESENTATIVE": "彼得因认信代表使徒群体",
        }.get(claim_id, claim_id),
        "claim_type": "explicit_claim",
        "evidence_step_ids": [f"E-{suffix}"],
        "review_status": status,
        "revision": 1,
    }


def _universe_manifest(*sources: dict) -> dict:
    payload = {
        "schema_version": "wang_source_universe_manifest_v1",
        "source_universe_manifest_id": "SUM-1",
        "sources": [
            {
                "source_id": item["source_id"],
                "source_revision_id": f"{item['source_id']}@{item.get('revision', 1)}",
                "source_sha256": item["source_sha256"],
            }
            for item in sources
        ],
    }
    payload["manifest_sha256"] = sha256_json(payload)
    return payload


def _coverage_and_manifest(*claims: dict):
    source = _source()
    coverage = build_coverage_snapshot(
        [source],
        roles_by_source={
            "SRC-1": ["source_universe", "detailed_extraction", "viewpoint_reviewed"]
        },
        source_universe_manifest=_universe_manifest(source),
        created_at="2026-08-22T12:00:00+00:00",
    )
    suffixes = [str(index) for index in range(1, len(claims) + 1)]
    evidence = [_evidence(suffix) for suffix in suffixes]
    fragments = [_fragment(suffix=suffix) for suffix in suffixes]
    normalized_claims = []
    for claim, suffix in zip(claims, suffixes, strict=True):
        row = dict(claim)
        row["evidence_step_ids"] = [f"E-{suffix}"]
        normalized_claims.append(row)
    manifest = build_input_claim_manifest(normalized_claims, evidence, fragments, coverage)
    return coverage, manifest, normalized_claims, evidence, fragments


def _manifest_for_claim_ids(*claim_ids: str):
    return _coverage_and_manifest(*[_claim(claim_id) for claim_id in claim_ids])


def test_coverage_and_claim_manifest_are_source_bound_and_byte_stable() -> None:
    first, first_manifest, *_ = _manifest_for_claim_ids("CL-PETER")
    second, second_manifest, *_ = _manifest_for_claim_ids("CL-PETER")

    assert first.coverage_snapshot_id == second.coverage_snapshot_id
    assert first.sources_sha256 == second.sources_sha256
    assert first_manifest == second_manifest
    assert first_manifest["schema_version"] == CLAIM_MANIFEST_VERSION
    assert first_manifest["manifest_sha256"] == sha256_json(
        {key: value for key, value in first_manifest.items() if key != "manifest_sha256"}
    )


def test_coverage_refuses_unpinned_source_or_duplicate_current_revision() -> None:
    missing_sha = _source()
    missing_sha["source_sha256"] = None
    with pytest.raises(ViewpointFoundationValidationError, match="source_sha256"):
        build_coverage_snapshot(
            [missing_sha],
            roles_by_source={"SRC-1": ["source_universe"]},
            source_universe_manifest=_universe_manifest(_source()),
            created_at="2026-08-22T12:00:00+00:00",
        )

    with pytest.raises(ValidationError, match="multiple current revisions"):
        build_coverage_snapshot(
            [_source(), _source()],
            roles_by_source={"SRC-1": ["source_universe"]},
            source_universe_manifest=_universe_manifest(_source()),
            created_at="2026-08-22T12:00:00+00:00",
        )


def test_coverage_complete_requires_manifest_integrity_and_every_source_reviewed() -> None:
    source = _source()
    tampered = _universe_manifest(source)
    tampered["manifest_sha256"] = "tampered"
    with pytest.raises(ViewpointFoundationValidationError, match="manifest SHA mismatch"):
        build_coverage_snapshot(
            [source],
            roles_by_source={"SRC-1": ["source_universe"]},
            source_universe_manifest=tampered,
            created_at="2026-08-22T12:00:00+00:00",
        )

    with pytest.raises(ViewpointFoundationValidationError, match="viewpoint_reviewed"):
        build_coverage_snapshot(
            [source],
            roles_by_source={"SRC-1": ["source_universe"]},
            source_universe_manifest=_universe_manifest(source),
            created_at="2026-08-22T12:00:00+00:00",
            coverage_status="complete",
        )

    partial = build_coverage_snapshot(
        [source],
        roles_by_source={"SRC-1": ["source_universe"]},
        source_universe_manifest=_universe_manifest(source),
        created_at="2026-08-22T12:00:00+00:00",
    )
    direct_payload = partial.model_dump(mode="json")
    direct_payload["coverage_status"] = "complete"
    with pytest.raises(ValidationError, match="viewpoint_reviewed"):
        ViewpointCoverageSnapshotRecord.model_validate(direct_payload)

def test_missing_resolution_becomes_unprocessed_instead_of_disappearing() -> None:
    coverage, manifest, *_ = _manifest_for_claim_ids("CL-PETER", "CL-CONFESSION")
    first = manifest["claims"][0]
    ledger = build_resolution_ledger(
        manifest,
        [
            {
                **{
                    key: first[key]
                    for key in (
                        "claim_id",
                        "pinned_claim_revision",
                        "claim_revision_sha256",
                    )
                },
                "processing_status": "source_ineligible",
                "source_eligibility_reason_code": "external_position",
            }
        ],
        coverage_snapshot_id=coverage.coverage_snapshot_id,
    )

    assert ledger.statistics.input_claim_count == 2
    assert ledger.statistics.source_ineligible_count == 1
    assert ledger.statistics.unprocessed_count == 1
    assert ledger.coverage_status == "partial"
    assert {row.claim_id for row in ledger.rows} == {"CL-PETER", "CL-CONFESSION"}


def test_ledger_rejects_duplicates_extras_and_sha_substitution() -> None:
    coverage, manifest, *_ = _manifest_for_claim_ids("CL-PETER")
    row = {
        **{
            key: manifest["claims"][0][key]
            for key in ("claim_id", "pinned_claim_revision", "claim_revision_sha256")
        },
        "processing_status": "unprocessed",
    }
    with pytest.raises(ViewpointFoundationValidationError, match="duplicate proposed"):
        build_resolution_ledger(
            manifest, [row, row], coverage_snapshot_id=coverage.coverage_snapshot_id
        )
    with pytest.raises(ViewpointFoundationValidationError, match="outside input manifest"):
        build_resolution_ledger(
            manifest,
            [{**row, "claim_id": "CL-EXTRA"}],
            coverage_snapshot_id=coverage.coverage_snapshot_id,
        )
    with pytest.raises(ViewpointFoundationValidationError, match="SHA mismatch"):
        build_resolution_ledger(
            manifest,
            [{**row, "claim_revision_sha256": "wrong"}],
            coverage_snapshot_id=coverage.coverage_snapshot_id,
        )

    tampered_manifest = json.loads(json.dumps(manifest))
    tampered_manifest["claims"][0]["claim_revision_sha256"] = "tampered"
    with pytest.raises(ViewpointFoundationValidationError, match="manifest SHA mismatch"):
        build_identity_candidate_seeds(tampered_manifest, [], [])


def test_source_ineligible_and_component_membership_cannot_be_faked() -> None:
    locator = ViewpointComponentLocator(
        statement_component="磐石彼得",
        claim_sha256="claim-sha",
        canonical_spans=[
            {"start_char": 0, "end_char": 2, "exact_text": "磐石"},
            {"start_char": 4, "end_char": 6, "exact_text": "彼得"},
        ],
    )
    assert locator.statement_component == "磐石彼得"
    with pytest.raises(ValidationError, match="does not match canonical spans"):
        ViewpointComponentLocator(
            statement_component="磐石不是彼得",
            claim_sha256="claim-sha",
            canonical_spans=[
                {"start_char": 0, "end_char": 2, "exact_text": "磐石"},
                {"start_char": 4, "end_char": 6, "exact_text": "彼得"},
            ],
        )

    with pytest.raises(ValidationError, match="closed reason code"):
        ViewpointResolutionRow(
            claim_id="CL-1",
            pinned_claim_revision=1,
            claim_revision_sha256="sha",
            processing_status="source_ineligible",
        )

    with pytest.raises(ValidationError, match="component_locator"):
        ViewpointClaimLinkRecord(
            viewpoint_claim_link_id="VCL-1",
            viewpoint_id="CV-1",
            validated_against_viewpoint_revision_id="CVR-1",
            claim_id="CL-1",
            pinned_claim_revision=1,
            link_type="equivalent_component",
            decision_id="VID-1",
        )

    with pytest.raises(ValidationError, match="active Claim link requires"):
        ViewpointClaimLinkRecord(
            viewpoint_claim_link_id="VCL-CANDIDATE",
            viewpoint_id="CV-1",
            validated_against_viewpoint_revision_id="CVR-1",
            claim_id="CL-1",
            pinned_claim_revision=1,
            link_type="equivalent_full",
            decision_id="VID-1",
        )


def test_duplicate_chain_is_one_cluster_without_transitive_approval() -> None:
    coverage, manifest, *_ = _manifest_for_claim_ids("CL-A", "CL-B", "CL-C")
    candidates = build_identity_candidate_seeds(
        manifest,
        [
            {
                "claim_relation_id": "CR-AB",
                "from_id": "CL-A",
                "to_id": "CL-B",
                "relation_type": "duplicate",
                "review_status": "ai_consensus",
            },
            {
                "claim_relation_id": "CR-BC",
                "from_id": "CL-B",
                "to_id": "CL-C",
                "relation_type": "duplicate",
                "review_status": "ai_consensus",
            },
        ],
        [],
    )

    assert coverage.coverage_snapshot_id == candidates[0].coverage_snapshot_id
    assert [item.candidate_claim_ids for item in candidates] == [
        ["CL-A", "CL-B", "CL-C"]
    ]
    assert candidates[0].review_status == "candidate"
    assert candidates[0].proposed_action == "create_new"


def test_negative_constraint_blocks_duplicate_seed_without_approving_anything() -> None:
    _, manifest, *_ = _manifest_for_claim_ids("CL-PETER", "CL-CONFESSION")
    candidates = build_identity_candidate_seeds(
        manifest,
        [
            {
                "claim_relation_id": "CR-PC",
                "from_id": "CL-PETER",
                "to_id": "CL-CONFESSION",
                "relation_type": "duplicate",
                "review_status": "ai_consensus",
            }
        ],
        [
            {
                "constraint_id": "CRC-PC",
                "source_id": "CL-PETER",
                "target_id": "CL-CONFESSION",
                "forbidden_relation_types": ["duplicate"],
                "bidirectional": True,
                "review_status": "approved",
            }
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].proposed_action == "defer"
    assert candidates[0].blocker_codes == ["approved_negative_duplicate_constraint"]
    assert candidates[0].review_status == "candidate"


def test_reviewed_material_relation_blocks_an_unsafe_duplicate_component() -> None:
    _, manifest, *_ = _manifest_for_claim_ids("CL-A", "CL-B", "CL-C")
    candidates = build_identity_candidate_seeds(
        manifest,
        [
            {
                "claim_relation_id": "CR-AB",
                "from_id": "CL-A",
                "to_id": "CL-B",
                "relation_type": "duplicate",
                "review_status": "ai_consensus",
            },
            {
                "claim_relation_id": "CR-BC",
                "from_id": "CL-B",
                "to_id": "CL-C",
                "relation_type": "duplicate",
                "review_status": "ai_consensus",
            },
            {
                "claim_relation_id": "CR-AC-CONTRAST",
                "from_id": "CL-A",
                "to_id": "CL-C",
                "relation_type": "contrasts",
                "review_status": "ai_consensus",
            },
        ],
        [],
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_claim_ids == ["CL-A", "CL-B", "CL-C"]
    assert candidates[0].proposed_action == "defer"
    assert candidates[0].blocker_codes == ["reviewed_material_relation"]
    assert candidates[0].seed_relation_ids == ["CR-AB", "CR-BC"]


def test_non_edge_negative_constraint_blocks_duplicate_chain_transitivity() -> None:
    _, manifest, *_ = _manifest_for_claim_ids("CL-A", "CL-B", "CL-C")
    candidates = build_identity_candidate_seeds(
        manifest,
        [
            {
                "claim_relation_id": "CR-AB",
                "from_id": "CL-A",
                "to_id": "CL-B",
                "relation_type": "duplicate",
                "review_status": "ai_consensus",
            },
            {
                "claim_relation_id": "CR-BC",
                "from_id": "CL-B",
                "to_id": "CL-C",
                "relation_type": "duplicate",
                "review_status": "ai_consensus",
            },
        ],
        [
            {
                "constraint_id": "CRC-AC",
                "source_id": "CL-A",
                "target_id": "CL-C",
                "forbidden_relation_types": ["duplicate"],
                "review_status": "approved",
            }
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_claim_ids == ["CL-A", "CL-B", "CL-C"]
    assert candidates[0].proposed_action == "defer"
    assert candidates[0].blocker_codes == ["approved_negative_duplicate_constraint"]


def test_unreviewed_relation_or_constraint_has_no_master_data_authority() -> None:
    _, manifest, *_ = _manifest_for_claim_ids("CL-A", "CL-B")
    relation = {
        "claim_relation_id": "CR-AB",
        "from_id": "CL-A",
        "to_id": "CL-B",
        "relation_type": "duplicate",
        "review_status": "candidate",
    }
    candidates = build_identity_candidate_seeds(manifest, [relation], [])
    assert {tuple(item.candidate_claim_ids) for item in candidates} == {
        ("CL-A",),
        ("CL-B",),
    }

    relation["review_status"] = "ai_consensus"
    candidates = build_identity_candidate_seeds(
        manifest,
        [relation],
        [
            {
                "constraint_id": "CRC-AB",
                "source_id": "CL-A",
                "target_id": "CL-B",
                "forbidden_relation_types": ["duplicate"],
                "review_status": "candidate",
            }
        ],
    )
    assert len(candidates) == 1
    assert candidates[0].proposed_action == "create_new"
    assert candidates[0].blocker_codes == []


def test_supporting_peter_claims_are_not_identity_pairs() -> None:
    _, manifest, *_ = _manifest_for_claim_ids(
        "CL-PETER", "CL-CONFESSION", "CL-REPRESENTATIVE"
    )
    candidates = build_identity_candidate_seeds(
        manifest,
        [
            {
                "claim_relation_id": "CR-1",
                "from_id": "CL-CONFESSION",
                "to_id": "CL-PETER",
                "relation_type": "contrasts",
            },
            {
                "claim_relation_id": "CR-2",
                "from_id": "CL-REPRESENTATIVE",
                "to_id": "CL-PETER",
                "relation_type": "extends",
            },
        ],
        [],
    )

    assert {tuple(item.candidate_claim_ids) for item in candidates} == {
        ("CL-PETER",),
        ("CL-CONFESSION",),
        ("CL-REPRESENTATIVE",),
    }


def _foundation_package() -> tuple[dict, dict]:
    coverage, manifest, claims, evidence, fragments = _manifest_for_claim_ids("CL-PETER")
    candidate = build_identity_candidate_seeds(manifest, [], [])[0]
    decision = ViewpointIdentityDecisionRecord(
        identity_decision_id="VID-1",
        identity_candidate_id=candidate.identity_candidate_id,
        decision="create_new",
        resolved_viewpoint_id="CV-1",
        claim_link_decisions=[{"claim_id": "CL-PETER", "link_type": "equivalent_full"}],
        reviewer_kind="human_editor",
        reviewer_id="editor-1",
        approval_basis="human_exception_review",
        reason="测试中确认命题身份",
        input_sha256=candidate.generation_fingerprint,
        created_at="2026-08-22T12:01:00+00:00",
        review_status="human_approved",
    )
    revision = ViewpointRevisionRecord(
        viewpoint_revision_id="CVR-1",
        viewpoint_id="CV-1",
        revision_number=1,
        core_proposition="彼得本人是磐石",
        proposition_signature={
            "subject": "彼得本人",
            "predicate": "是",
            "object": "磐石",
            "polarity": "affirmed",
            "modality": "asserted",
        },
        scope={"scripture_scope": ["Matt.16.18"]},
        provenance={
            "basis_identity_decision_ids": ["VID-1"],
            "review_artifact_sha256": "review-sha",
        },
        approved_by="editor-1",
        approved_at="2026-08-22T12:02:00+00:00",
        review_status="human_approved",
    )
    viewpoint = CanonicalViewpointRecord(
        viewpoint_id="CV-1",
        current_revision_id="CVR-1",
        created_from_candidate_id=candidate.identity_candidate_id,
        review_status="human_approved",
    )
    link = ViewpointClaimLinkRecord(
        viewpoint_claim_link_id="VCL-1",
        viewpoint_id="CV-1",
        validated_against_viewpoint_revision_id="CVR-1",
        claim_id="CL-PETER",
        pinned_claim_revision=1,
        link_type="equivalent_full",
        decision_id="VID-1",
        review_status="human_approved",
    )
    entry = manifest["claims"][0]
    ledger = build_resolution_ledger(
        manifest,
        [
            {
                **{
                    key: entry[key]
                    for key in ("claim_id", "pinned_claim_revision", "claim_revision_sha256")
                },
                "processing_status": "resolved",
                "resolution_kind": "member_existing",
                "primary_viewpoint_id": "CV-1",
                "viewpoint_claim_link_id": "VCL-1",
                "decision_id": "VID-1",
            }
        ],
        coverage_snapshot_id=coverage.coverage_snapshot_id,
    )
    quality = build_foundation_quality_report(
        scope_ids=["VID-1"],
        coverage_snapshot=coverage,
        ledger=ledger,
        claims=claims,
        evidence_steps=evidence,
        source_fragments=fragments,
        candidate_regression_artifact_sha256="regression-sha",
        candidate_regression_passed=True,
    )
    package = {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "VIEWPOINT-FOUNDATION-TEST",
        "source_documents": [_source()],
        "source_fragments": fragments,
        "evidence_steps": evidence,
        "claims": claims,
        "viewpoint_coverage_snapshots": [coverage.model_dump(mode="json")],
        "viewpoint_identity_candidates": [candidate.model_dump(mode="json")],
        "viewpoint_identity_decisions": [decision.model_dump(mode="json")],
        "canonical_viewpoints": [viewpoint.model_dump(mode="json")],
        "viewpoint_revisions": [revision.model_dump(mode="json")],
        "viewpoint_claim_links": [link.model_dump(mode="json")],
        "viewpoint_resolution_ledgers": [ledger.model_dump(mode="json")],
        "viewpoint_quality_reports": [quality.model_dump(mode="json")],
    }
    return package, {
        "coverage": coverage,
        "candidate": candidate,
        "ledger": ledger,
        "quality": quality,
    }


def test_foundation_package_is_registered_and_plans_one_viewpoint_edge() -> None:
    package, records = _foundation_package()
    normalized = normalize_package(package)
    plan = build_change_set_plan(package, {})

    assert records["quality"].eligibility_decision == "pass"
    assert "total_score" not in records["quality"].model_dump()
    assert set(normalized["canonical_viewpoints"]) == {"CV-1"}
    assert set(normalized["viewpoint_resolution_ledgers"]) == {
        records["ledger"].resolution_ledger_id
    }
    operation = next(item for item in plan.operations if item.collection == "viewpoint_claim_links")
    assert operation.object_id == "VCL-1"
    assert PostgresKnowledgeStore._edge_values(operation.collection, operation.payload) == (
        "CV-1",
        "CL-PETER",
        "equivalent_full",
    )


def test_foundation_package_round_trips_through_repository_importer(tmp_path) -> None:
    package, records = _foundation_package()
    path = tmp_path / "viewpoint-foundation.json"
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    store = RepositoryStore(tmp_path / "repository")
    store.save_citation(
        Citation(
            citation_id="CIT-1",
            source_id="SRC-1",
            source_sha256="source-sha-SRC-1",
            locator={
                "kind": "transcript",
                "paragraph_keys": ["S0001"],
                "highlight_text": "你是彼得",
                "highlight_text_sha256": "highlight-sha",
            },
            evidence_ids=["E-1"],
            status="approved",
        )
    )
    importer = KnowledgePackageImporter(store)

    first = importer.import_path(path)
    second = importer.import_path(path)

    assert first["changes"]["created"] == 12
    assert second["changes"] == {"created": 0, "updated": 0, "unchanged": 12}
    stored = store.get_knowledge_record("viewpoint_claim_links", "VCL-1")
    assert isinstance(stored, ViewpointClaimLinkRecord)
    assert stored.claim_id == "CL-PETER"
    quality = store.get_knowledge_record(
        "viewpoint_quality_reports", records["quality"].quality_report_id
    )
    assert quality.eligibility_decision == "pass"


def test_change_set_refuses_claim_sha_or_revision_drift() -> None:
    package, _ = _foundation_package()
    package = json.loads(json.dumps(package))
    package["claims"][0]["statement"] = "突变后的另一条主张"

    with pytest.raises(ViewpointFoundationValidationError, match="Claim SHA mismatch"):
        build_change_set_plan(package, {})


def test_change_set_refuses_unstable_candidate_identity() -> None:
    package, _ = _foundation_package()
    package = json.loads(json.dumps(package))
    original_id = package["viewpoint_identity_candidates"][0]["identity_candidate_id"]
    package["viewpoint_identity_candidates"][0]["identity_candidate_id"] = "VIC-FORGED"
    package["viewpoint_identity_decisions"][0]["identity_candidate_id"] = "VIC-FORGED"
    package["canonical_viewpoints"][0]["created_from_candidate_id"] = "VIC-FORGED"
    assert original_id != "VIC-FORGED"

    with pytest.raises(ViewpointFoundationValidationError, match="unstable identity candidate id"):
        build_change_set_plan(package, {})


@pytest.mark.parametrize(
    "collection,field,error",
    [
        ("viewpoint_coverage_snapshots", "sources_sha256", "sources_sha256 mismatch"),
        ("viewpoint_resolution_ledgers", "artifact_sha256", "artifact SHA mismatch"),
        ("viewpoint_quality_reports", "artifact_sha256", "artifact SHA mismatch"),
    ],
)
def test_change_set_refuses_tampered_derived_hashes(collection, field, error) -> None:
    package, _ = _foundation_package()
    package = json.loads(json.dumps(package))
    package[collection][0][field] = "tampered"

    with pytest.raises(ViewpointFoundationValidationError, match=error):
        build_change_set_plan(package, {})


def test_change_set_refuses_two_active_full_memberships() -> None:
    package, records = _foundation_package()
    package = json.loads(json.dumps(package))
    package["viewpoint_identity_decisions"].append(
        {
            **package["viewpoint_identity_decisions"][0],
            "identity_decision_id": "VID-2",
            "resolved_viewpoint_id": "CV-2",
        }
    )
    package["canonical_viewpoints"].append(
        {
            **package["canonical_viewpoints"][0],
            "viewpoint_id": "CV-2",
            "current_revision_id": "CVR-2",
        }
    )
    package["viewpoint_revisions"].append(
        {
            **package["viewpoint_revisions"][0],
            "viewpoint_revision_id": "CVR-2",
            "viewpoint_id": "CV-2",
            "provenance": {
                "basis_identity_decision_ids": ["VID-2"],
                "review_artifact_sha256": "review-sha-2",
            },
        }
    )
    package["viewpoint_claim_links"].append(
        {
            **package["viewpoint_claim_links"][0],
            "viewpoint_claim_link_id": "VCL-2",
            "viewpoint_id": "CV-2",
            "validated_against_viewpoint_revision_id": "CVR-2",
            "decision_id": "VID-2",
        }
    )

    with pytest.raises(ViewpointFoundationValidationError, match="multiple active"):
        build_change_set_plan(package, {})


def test_semantic_revision_cannot_be_rewritten_in_place() -> None:
    package, _ = _foundation_package()
    initial = build_change_set_plan(package, {})
    existing = {
        (item.collection, item.object_id): {
            "revision": item.after_revision,
            "content_sha256": item.after_sha256,
            "payload": item.payload,
        }
        for item in initial.operations
    }
    changed = json.loads(json.dumps(package))
    changed["viewpoint_revisions"][0]["core_proposition"] = "同一个 ID 下偷换命题"

    with pytest.raises(ViewpointFoundationValidationError, match="immutable record"):
        build_change_set_plan(changed, existing)


def test_quality_report_fails_each_dimension_without_compensation() -> None:
    coverage, manifest, claims, evidence, fragments = _coverage_and_manifest(
        _claim("CL-PETER", status="candidate")
    )
    ledger = build_resolution_ledger(
        manifest, [], coverage_snapshot_id=coverage.coverage_snapshot_id
    )
    report = build_foundation_quality_report(
        scope_ids=["VIC-1"],
        coverage_snapshot=coverage,
        ledger=ledger,
        claims=claims,
        evidence_steps=evidence,
        source_fragments=fragments,
        candidate_regression_artifact_sha256="regression-sha",
        candidate_regression_passed=False,
    )

    assert report.eligibility_decision == "fail"
    failed = {item.dimension for item in report.dimensions if item.status == "fail"}
    assert failed == {"source_maturity", "resolution_coverage", "candidate_recall"}
    assert {item.dimension for item in report.hard_failures} == failed
    assert "total_score" not in report.model_dump()


def test_quality_report_cannot_omit_an_independent_dimension() -> None:
    _, records = _foundation_package()
    payload = records["quality"].model_dump(mode="json")
    payload["dimensions"] = payload["dimensions"][:-1]

    with pytest.raises(ValidationError, match="must contain every dimension"):
        ViewpointQualityReportRecord.model_validate(payload)


def test_quality_report_fails_unusable_evidence_even_when_claim_is_approved() -> None:
    coverage, manifest, claims, evidence, fragments = _manifest_for_claim_ids("CL-PETER")
    evidence[0]["support_eligibility"] = "withheld_unreviewed"
    entry = manifest["claims"][0]
    ledger = build_resolution_ledger(
        manifest,
        [
            {
                **{
                    key: entry[key]
                    for key in ("claim_id", "pinned_claim_revision", "claim_revision_sha256")
                },
                "processing_status": "resolved",
                "resolution_kind": "no_registry_assertion",
                "resolution_reason_code": "not_a_registerable_proposition",
                "decision_id": "VID-TEST",
            }
        ],
        coverage_snapshot_id=coverage.coverage_snapshot_id,
    )
    report = build_foundation_quality_report(
        scope_ids=["VID-TEST"],
        coverage_snapshot=coverage,
        ledger=ledger,
        claims=claims,
        evidence_steps=evidence,
        source_fragments=fragments,
        candidate_regression_artifact_sha256="regression-sha",
        candidate_regression_passed=True,
    )

    assert report.eligibility_decision == "fail"
    assert [item.dimension for item in report.dimensions if item.status == "fail"] == [
        "provenance_integrity"
    ]


def test_system_decision_cannot_masquerade_as_human_approval() -> None:
    with pytest.raises(ValidationError, match="human_exception_review"):
        ViewpointIdentityDecisionRecord(
            identity_decision_id="VID-BAD",
            identity_candidate_id="VIC-1",
            decision="defer",
            reviewer_kind="system",
            reviewer_id="validator",
            approval_basis="human_exception_review",
            reason="不能冒充人工",
            input_sha256="input-sha",
            created_at="2026-08-22T12:00:00+00:00",
            review_status="human_approved",
        )


def test_ledger_model_rejects_self_reported_statistics() -> None:
    _, records = _foundation_package()
    payload = records["ledger"].model_dump(mode="json")
    payload["statistics"]["resolved_count"] = 0
    with pytest.raises(ValidationError, match="statistics do not match"):
        ViewpointResolutionLedgerRecord.model_validate(payload)
