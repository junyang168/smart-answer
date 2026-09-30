from __future__ import annotations

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.legacy_candidate_reconciliation import plan_review_migration
from backend.pipeline.legacy_relation_id_review_reconciliation import (
    SOURCE_KIND, _pass_projection_unchanged,
)
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


def test_pass_projection_plan_needs_narrow_proof_and_excludes_spot_check():
    payload = {"claim_id": "CL-1", "statement": "原有主张", "review_status": "candidate", "revision": 1}
    current = {"CL-1": {"revision": 1, "content_sha256": record_content_sha(payload),
                        "payload": payload}}
    row = {
        "claim_id": "CL-1", "revision": 1, "content_sha256": current["CL-1"]["content_sha256"],
        "reason": "pass_review_projection_id_only_graph_verified",
        "review_decision": "pass", "spot_check_selected": False,
        "adjudication_status": "not_required", "target_review_status": "ai_consensus_reviewed",
        "source_id": "SRC-1", "source_revision": 2, "source_content_sha256": "a" * 64,
        "reviewer_id": "independent-model", "adjudicator_id": "adjudicator-model",
        "adjudicated_at": "2026-09-01T00:00:00+00:00", "package_sha256": "b" * 64,
        "review_sha256": "c" * 64, "review_fingerprint": "d" * 64,
        "adjudication_sha256": "e" * 64, "reviewed_candidate_sha256": "f" * 64,
        "graph_guard_sha256": "1" * 64, "relation_id_manifest_sha256": "2" * 64,
        "effective_package_sha256": "3" * 64, "review_projection_sha256": "4" * 64,
        "historical_replay_sha256": "5" * 64,
        "historical_replay_code_sha256": "6" * 64,
        "overrides_sha256": "7" * 64,
    }
    plan = plan_review_migration([row], current, freeze_sha256="8" * 64,
                                 source_kind=SOURCE_KIND)
    assert plan.review_events[0].artifact["review_projection_sha256"] == "4" * 64
    for bad in ({"spot_check_selected": True}, {"review_projection_sha256": None},
                {"adjudication_status": "auto_applied"}):
        with pytest.raises(ValueError, match="pass projection decision lacks proof"):
            plan_review_migration([{**row, **bad}], current, freeze_sha256="8" * 64,
                                  source_kind=SOURCE_KIND)


def test_pass_projection_accepts_only_topic_tags_with_identical_review_and_graph():
    original = {"claims": {"CL-1": {"claim_id": "CL-1", "statement": "原话",
                                     "topic_terms": ["旧"], "evidence_step_ids": []}}}
    reviewed = {"claims": {"CL-1": {**original["claims"]["CL-1"],
                                     "topic_terms": ["旧", "新"]}}}
    effective = {"claims": dict(original["claims"])}
    projection = {"CL-1": {"claim_id": "CL-1", "statement": "原话"}}
    assert _pass_projection_unchanged(original, reviewed, effective,
                                      projection, projection, "CL-1")
    assert not _pass_projection_unchanged(
        original, reviewed, effective, projection,
        {"CL-1": {"claim_id": "CL-1", "statement": "改写"}}, "CL-1"
    )
    reviewed["claims"]["CL-1"]["scripture_refs"] = ["太 1:1"]
    assert not _pass_projection_unchanged(original, reviewed, effective,
                                          projection, projection, "CL-1")
    reviewed["claims"]["CL-1"].pop("scripture_refs")
    reviewed["claim_relations"] = {"R-1": {"claim_relation_id": "R-1", "from_id": "CL-1", "to_id": "CL-2"}}
    assert not _pass_projection_unchanged(original, reviewed, effective,
                                          projection, projection, "CL-1")
