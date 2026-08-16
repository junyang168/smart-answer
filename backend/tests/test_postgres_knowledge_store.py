from __future__ import annotations

import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import (
    SOURCE_KEYS,
    build_active_snapshot,
    build_change_set_plan,
    normalize_package,
    record_content_sha,
    reviewed_relations_package,
)


def _package() -> dict:
    return {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "PKG-1",
        "source_documents": [
            {"source_id": "SRC-1", "source_type": "sermon_transcript", "title": "讲道"}
        ],
        "source_fragments": [
            {"fragment_id": "FR-1", "source_id": "SRC-1", "verbatim_excerpt": "原话"}
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "E-1",
                "source_fragment_id": "FR-1",
                "statement": "证据",
                "support_eligibility": "withheld_unreviewed",
            }
        ],
        "claims": [
            {
                "claim_id": "CL-1",
                "statement": "教授的主张",
                "claim_type": "explicit_claim",
                "evidence_step_ids": ["E-1"],
            }
        ],
    }


def test_plan_is_stable_and_revision_is_not_semantic_content() -> None:
    package = _package()
    first = build_change_set_plan(package, {})
    assert first.as_dict()["summary"] == {
        "created": 4,
        "updated": 0,
        "unchanged": 0,
        "operations": 4,
    }
    claim = normalize_package(package)["claims"]["CL-1"]
    existing_claim = dict(claim, revision=8)
    existing = {
        ("claims", "CL-1"): {
            "revision": 8,
            "content_sha256": record_content_sha(existing_claim),
            "payload": existing_claim,
        }
    }
    partial = build_change_set_plan(package, existing)
    assert not any(item.object_id == "CL-1" for item in partial.operations)


def test_human_review_fields_survive_ai_reimport() -> None:
    package = _package()
    reviewed = normalize_package(package)["claims"]["CL-1"]
    reviewed.update(
        {
            "review_status": "approved",
            "review_note": "同工已核对",
            "reviewed_by": "reviewer-1",
            "revision": 4,
        }
    )
    existing = {
        ("claims", "CL-1"): {
            "revision": 4,
            "content_sha256": record_content_sha(reviewed),
            "payload": reviewed,
        }
    }
    changed = _package()
    changed["claims"][0]["statement"] = "更新后的候选文字"
    plan = build_change_set_plan(changed, existing)
    operation = next(item for item in plan.operations if item.object_id == "CL-1")
    assert operation.payload["review_status"] == "approved"
    assert operation.payload["review_note"] == "同工已核对"
    assert operation.payload["reviewed_by"] == "reviewer-1"


def test_reviewed_relation_artifact_becomes_edges_and_negative_constraints() -> None:
    artifact = {
        "result": {
            "reviewed_relations": [
                {
                    "candidate_id": "XSR-1",
                    "source_claim_id": "CL-1",
                    "target_claim_id": "CL-2",
                    "relation_type": "supports",
                    "reason": "跨讲支持",
                    "review_status": "ai_consensus",
                },
            ],
            "negative_comparisons": [
                {
                    "candidate_id": "XSR-2",
                    "source_claim_id": "CL-1",
                    "target_claim_id": "CL-3",
                    "relation_type": "unrelated",
                    "reason": "不能合并",
                    "review_status": "ai_consensus",
                }
            ],
        }
    }
    package = reviewed_relations_package(artifact)
    assert package["claim_relations"][0]["relation_type"] == "supports"
    assert package["claim_relation_constraints"][0]["bidirectional"] is True
    assert "duplicate" in package["claim_relation_constraints"][0]["forbidden_relation_types"]


def test_reviewed_relation_package_preserves_human_approval() -> None:
    artifact = {
        "result": {
            "reviewed_relations": [
                {
                    "candidate_id": "XSR-APPROVED",
                    "source_claim_id": "CL-1",
                    "target_claim_id": "CL-2",
                    "relation_type": "supports",
                    "review_status": "approved",
                }
            ]
        }
    }
    package = reviewed_relations_package(artifact)
    assert package["claim_relations"][0]["review_status"] == "approved"


def test_topic_identity_reconciliation_is_a_persisted_knowledge_record() -> None:
    package = {
        "schema_version": "topic_identity_test_v1",
        "package_id": "PKG-TOPIC-IDENTITY",
        "topic_identity_reconciliations": [{
            "reconciliation_id": "TIR-1",
            "candidate_topic_id": "TCAND-1",
            "label": "候选母题：约与关系",
            "topic_level": "family",
            "claim_ids": ["CL-1"],
            "status": "pending_match",
            "candidate_matches": [{"existing_topic_id": "covenant"}],
            "origin_batch_id": "RB-ONE",
        }],
    }
    normalized = normalize_package(package)
    record = normalized["topic_identity_reconciliations"]["TIR-1"]
    assert record["candidate_topic_id"] == "TCAND-1"
    assert record["status"] == "pending_match"
    assert record["candidate_matches"][0]["existing_topic_id"] == "covenant"
    assert SOURCE_KEYS["topic_identity_reconciliations"] == "topic_identity_reconciliations"


def test_current_shared_package_can_be_normalized() -> None:
    path = Path(__file__).parent / "fixtures/wang_knowledge_platform/shared-knowledge-pilot.json"
    normalized = normalize_package(json.loads(path.read_text(encoding="utf-8")))
    assert normalized["claims"]
    assert normalized["source_documents"]


def test_migration_defines_transactional_authoring_tables() -> None:
    sql = Path(
        "backend/api/canonical_repository/migrations/001_postgres_authoring_store.sql"
    ).read_text(encoding="utf-8")
    for table in (
        "wang_knowledge.objects",
        "wang_knowledge.object_versions",
        "wang_knowledge.change_sets",
        "wang_knowledge.change_operations",
        "wang_knowledge.edges",
        "wang_knowledge.review_events",
    ):
        assert table in sql


def test_active_snapshot_contains_only_approved_claims_with_bound_evidence() -> None:
    package = _package()
    package["source_documents"][0]["source_sha256"] = "source-hash"
    package["source_fragments"][0].update(
        {
            "anchor_state": "source_version_bound",
            "paragraph_text_sha256": "paragraph-hash",
            "verbatim_excerpt_sha256": "excerpt-hash",
        }
    )
    package["evidence_steps"][0]["support_eligibility"] = "eligible"
    package["claims"][0]["review_status"] = "approved"
    package["claims"].append(
        {
            "claim_id": "CL-2",
            "statement": "尚未批准的主张",
            "claim_type": "explicit_claim",
            "evidence_step_ids": ["E-1"],
            "review_status": "candidate",
        }
    )

    snapshot, findings = build_active_snapshot(package, build_id="ACTIVE-TEST")

    assert snapshot["build_id"] == "ACTIVE-TEST"
    assert [item["claim_id"] for item in snapshot["claims"]] == ["CL-1"]
    assert [item["evidence_step_id"] for item in snapshot["evidence_steps"]] == ["E-1"]
    assert [item["fragment_id"] for item in snapshot["source_fragments"]] == ["FR-1"]
    assert not [item for item in findings if item["severity"] == "error"]


def test_active_snapshot_rejects_approved_claim_with_unbound_source() -> None:
    package = _package()
    package["claims"][0]["review_status"] = "approved"
    package["evidence_steps"][0]["support_eligibility"] = "eligible"

    snapshot, findings = build_active_snapshot(package, build_id="ACTIVE-TEST")

    assert snapshot["claims"] == []
    codes = {item["code"] for item in findings if item["severity"] == "error"}
    assert "approved_claim_has_unbound_evidence" in codes
    assert "approved_claim_without_publishable_evidence" in codes
