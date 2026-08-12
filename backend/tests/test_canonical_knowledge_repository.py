from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.api.canonical_repository.knowledge_importer import (
    KnowledgePackageImporter,
    KnowledgePackageValidationError,
)
from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.service import CanonicalRepositoryService
from backend.api.canonical_repository.store import RepositoryStore


def _package() -> dict:
    return {
        "schema_version": "wang_shared_knowledge_v1.1",
        "package_id": "SKP-TEST",
        "source_documents": [
            {"source_id": "SRC-1", "source_type": "sermon_transcript", "title": "第三讲"}
        ],
        "source_fragments": [
            {
                "fragment_id": "FR-1",
                "source_id": "SRC-1",
                "verbatim_excerpt": "云彩显明神的临在。",
                "custom_anchor_field": "preserved",
            }
        ],
        "questions": [
            {
                "question_id": "Q-1",
                "question": "云彩表示什么？",
                "source_fragment_id": "FR-1",
                "answer_state": "answered",
            }
        ],
        "observations": [
            {
                "observation_id": "OBS-1",
                "statement": "叙事中出现云彩。",
                "source_fragment_id": "FR-1",
            }
        ],
        "claims": [
            {
                "claim_id": "CL-1",
                "title": "云彩显明神的临在",
                "claim_type": "explicit_claim",
                "evidence_step_ids": ["ES-1"],
            }
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "ES-1",
                "source_fragment_id": "FR-1",
                "statement": "云彩显明神的临在。",
                "produced_claim_ids": ["CL-1"],
                "support_eligibility": "withheld_unreviewed",
            }
        ],
        "knowledge_relations": [
            {
                "relation_id": "KR-1",
                "source_id": "ES-1",
                "target_id": "ES-1",
                "relation_type": "supports",
            }
        ],
        "position_nodes": [
            {"position_id": "POS-1", "title": "云彩没有神学意义"}
        ],
        "claim_relations": [
            {
                "claim_relation_id": "CR-1",
                "source_id": "CL-1",
                "target_id": "POS-1",
                "relation_type": "refutes",
            }
        ],
        "knowledge_routes": [
            {
                "route_id": "ROUTE-1",
                "claim_id": "CL-1",
                "route_type": "scripture_exposition",
                "target_id": "CP-1",
                "decision_ids": ["CD-1"],
            }
        ],
        "cross_source_syntheses": [
            {
                "synthesis_id": "SYN-1",
                "synthesis_type": "cross_source_claims",
                "title": "云彩主题",
                "claim_ids": ["CL-1"],
            }
        ],
        "product_plans": [
            {
                "plan_id": "CP-1",
                "product_type": "scripture_exposition",
                "title": "马太福音17章",
                "decisions": [
                    {
                        "decision_id": "CD-1",
                        "action": "include_as_core",
                        "decision": "在正文解释云彩。",
                        "rationale": "这是经文细节。",
                        "claim_ids": ["CL-1"],
                    }
                ],
            }
        ],
        "editorial_checks": [
            {"check_id": "CHECK-1", "title": "核对经文", "status": "pending"}
        ],
        "tensions": [
            {"tension_id": "TEN-1", "question": "是否每次云彩都表示临在？"}
        ],
    }


def _write_package(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_import_creates_versioned_records_and_preserves_extra_fields(tmp_path: Path) -> None:
    store = RepositoryStore(tmp_path / "repo")
    importer = KnowledgePackageImporter(store)

    result = importer.import_path(_write_package(tmp_path / "package.json", _package()))

    assert result["changes"]["created"] == 15
    assert result["repository_counts"]["claims"] == 1
    claim = store.get_knowledge_record("claims", "CL-1")
    assert isinstance(claim, ClaimRecord)
    assert claim.statement == "云彩显明神的临在"
    fragment = store.get_knowledge_record("source_fragments", "FR-1")
    assert fragment.model_dump()["custom_anchor_field"] == "preserved"
    plan = store.get_knowledge_record("composition_plans", "CP-1")
    assert plan.decision_ids == ["CD-1"]
    decision = store.get_knowledge_record("composition_decisions", "CD-1")
    assert decision.plan_id == "CP-1"


def test_reimport_is_idempotent_and_does_not_overwrite_human_review(tmp_path: Path) -> None:
    store = RepositoryStore(tmp_path / "repo")
    importer = KnowledgePackageImporter(store)
    package_path = _write_package(tmp_path / "package.json", _package())
    importer.import_path(package_path)

    claim = store.get_knowledge_record("claims", "CL-1")
    claim.review_status = "approved"
    claim.revision = 4
    store.save_knowledge_record("claims", claim)

    result = importer.import_path(package_path)

    reviewed = store.get_knowledge_record("claims", "CL-1")
    assert reviewed.review_status == "approved"
    assert reviewed.revision == 4
    assert result["changes"]["created"] == 0
    assert result["changes"]["updated"] == 0
    assert result["changes"]["unchanged"] == 15


def test_import_rejects_claim_with_missing_evidence(tmp_path: Path) -> None:
    payload = _package()
    payload["claims"][0]["evidence_step_ids"] = ["ES-MISSING"]
    importer = KnowledgePackageImporter(RepositoryStore(tmp_path / "repo"))

    with pytest.raises(KnowledgePackageValidationError) as error:
        importer.import_path(_write_package(tmp_path / "bad-package.json", payload))

    assert "missing evidence steps" in " ".join(error.value.findings)
    assert not (tmp_path / "repo" / "knowledge" / "claims").exists()


def test_import_rejects_eligible_evidence_without_canonical_citation(
    tmp_path: Path,
) -> None:
    payload = _package()
    payload["evidence_steps"][0]["support_eligibility"] = "eligible"
    importer = KnowledgePackageImporter(RepositoryStore(tmp_path / "repo"))

    with pytest.raises(KnowledgePackageValidationError) as error:
        importer.import_path(_write_package(tmp_path / "bad-provenance.json", payload))

    assert "no canonical citation" in " ".join(error.value.findings)
    assert not (tmp_path / "repo" / "knowledge" / "claims").exists()


def test_incremental_package_can_reference_existing_records(tmp_path: Path) -> None:
    store = RepositoryStore(tmp_path / "repo")
    importer = KnowledgePackageImporter(store)
    importer.import_path(_write_package(tmp_path / "base.json", _package()))
    incremental = {
        "schema_version": "wang_shared_knowledge_v1.1",
        "package_id": "SKP-INCREMENTAL",
        "claims": [
            {
                "claim_id": "CL-2",
                "title": "云彩具有记号功能",
                "claim_type": "editorial_candidate",
                "evidence_step_ids": ["ES-1"],
            }
        ],
        "claim_relations": [
            {
                "claim_relation_id": "CR-2",
                "source_id": "CL-2",
                "target_id": "CL-1",
                "relation_type": "contextualizes",
            }
        ],
    }

    result = importer.import_path(
        _write_package(tmp_path / "incremental.json", incremental)
    )

    assert result["changes"]["created"] == 2
    assert store.get_knowledge_record("claims", "CL-2").evidence_step_ids == ["ES-1"]


def test_update_knowledge_record_uses_revision_guard(tmp_path: Path) -> None:
    store = RepositoryStore(tmp_path / "repo")
    KnowledgePackageImporter(store).import_path(
        _write_package(tmp_path / "package.json", _package())
    )

    updated = store.update_knowledge_record(
        "claims",
        "CL-1",
        {"review_status": "approved", "review_note": "人工核对通过"},
        expected_revision=1,
    )

    assert updated.review_status == "approved"
    assert updated.revision == 2
    assert updated.model_dump()["review_note"] == "人工核对通过"
    with pytest.raises(ValueError, match="Revision conflict"):
        store.update_knowledge_record(
            "claims",
            "CL-1",
            {"review_status": "candidate"},
            expected_revision=1,
        )


def test_claim_change_invalidates_reverse_product_dependency(tmp_path: Path) -> None:
    service = CanonicalRepositoryService(tmp_path / "repo")
    KnowledgePackageImporter(service.store).import_path(
        _write_package(tmp_path / "package.json", _package())
    )
    snapshot = service.snapshot_product_dependencies(
        "composition_plan", "CP-1", ["CL-1"]
    )
    dependency_id = snapshot["dependency_ids"][0]

    impact = service.analyze_claim_impact("CL-1")
    assert dependency_id in impact["product_dependency_ids"]
    assert "CP-1" in impact["composition_plan_ids"]

    service.update_knowledge_record(
        "claims",
        "CL-1",
        {"statement": "云彩在这段叙事中显明神的临在"},
        expected_revision=1,
    )

    dependency = service.store.get_knowledge_record(
        "product_dependencies", dependency_id
    )
    assert dependency.status == "invalidated"
    events = list(service.store.list_knowledge_records("impact_events"))
    assert len(events) == 1
    assert dependency_id in events[0].affected_dependency_ids
    assert "withdraw_or_rebuild_published_consumers" in events[0].required_actions
