from __future__ import annotations

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.legacy_candidate_reconciliation import plan_review_migration
from backend.pipeline.legacy_relation_id_review_reconciliation import SOURCE_KIND
from backend.pipeline.relation_id_namespace import migrate_legacy_cross_section_relation_ids


def test_relation_id_migration_changes_only_identifiers_and_round_trips():
    original = {
        "source_documents": [{"source_id": "SRC-1", "transcript_id": "S-1"}],
        "knowledge_relations": [{"relation_id": "XER001", "from_id": "E-1", "to_id": "E-2"}],
        "claim_relations": [{"claim_relation_id": "XCR001", "from_id": "C-1", "to_id": "C-2"}],
        "consensus_application": {"removed_claim_relation_ids": ["XCR001"]},
    }
    effective, manifest = migrate_legacy_cross_section_relation_ids(original)
    assert manifest["status"] == "applied"
    assert manifest["round_trip_verified"] is True
    assert manifest["semantic_change"] == "none_relation_identifiers_only"
    assert original["claim_relations"][0]["claim_relation_id"] == "XCR001"
    assert effective["claim_relations"][0]["claim_relation_id"] != "XCR001"


@pytest.mark.parametrize("status", ["withdrawn", "auto_applied"])
def test_relation_id_plan_requires_bound_proof(status):
    payload = {"claim_id": "CL-1", "statement": "原有主张", "review_status": "candidate", "revision": 1}
    current = {"CL-1": {"revision": 1, "content_sha256": record_content_sha(payload),
                        "payload": payload}}
    row = {
        "claim_id": "CL-1", "revision": 1, "content_sha256": current["CL-1"]["content_sha256"],
        "reason": "relation_id_only_graph_verified", "review_decision": "changes_suggested",
        "adjudication_status": status, "target_review_status": "ai_consensus_reviewed",
        "source_id": "SRC-1", "source_revision": 2, "source_content_sha256": "a" * 64,
        "reviewer_id": "independent-model", "adjudicator_id": "adjudicator-model",
        "adjudicated_at": "2026-09-01T00:00:00+00:00", "package_sha256": "b" * 64,
        "review_sha256": "c" * 64, "review_fingerprint": "d" * 64,
        "adjudication_sha256": "e" * 64, "reviewed_candidate_sha256": "f" * 64,
        "graph_guard_sha256": "1" * 64, "relation_id_manifest_sha256": "2" * 64,
        "effective_package_sha256": "3" * 64,
        "historical_replay_sha256": "4" * 64 if status == "auto_applied" else None,
        "historical_replay_code_sha256": "5" * 64 if status == "auto_applied" else None,
        "overrides_sha256": "6" * 64 if status == "auto_applied" else None,
    }
    plan = plan_review_migration([row], current, freeze_sha256="7" * 64,
                                 source_kind=SOURCE_KIND)
    assert plan.review_events[0].artifact["relation_id_manifest_sha256"] == "2" * 64
    with pytest.raises(ValueError, match="relation-id decision lacks proof"):
        plan_review_migration([{**row, "relation_id_manifest_sha256": None}], current,
                              freeze_sha256="7" * 64, source_kind=SOURCE_KIND)
