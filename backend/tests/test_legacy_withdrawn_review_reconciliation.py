from __future__ import annotations

from backend.api.canonical_repository.postgres_store import (
    _contains_exact_value,
    record_content_sha,
)
from backend.pipeline.legacy_candidate_reconciliation import plan_review_migration
from backend.pipeline.legacy_withdrawn_review_reconciliation import (
    SOURCE_KIND,
    _live_graph_guard,
    _package_graph_unchanged,
)

import pytest


def _package() -> dict:
    return {
        "claims": {"CL-1": {"claim_id": "CL-1", "statement": "原有主张",
                              "evidence_step_ids": ["E-1"]}},
        "evidence_steps": {"E-1": {"evidence_step_id": "E-1",
                                   "produced_claim_ids": ["CL-1"],
                                   "source_fragment_ids": ["F-1"]}},
        "source_fragments": {"F-1": {"fragment_id": "F-1", "source_id": "SRC-1",
                                     "verbatim_excerpt": "原话",
                                     "source_sha256": "a" * 64,
                                     "visual_facts": []}},
        "claim_relations": {},
        "knowledge_relations": {},
    }


def test_withdrawn_package_must_leave_claim_and_evidence_graph_unchanged():
    original = _package()
    reviewed = _package()
    assert _package_graph_unchanged(original, reviewed, "CL-1")
    reviewed["claim_relations"]["R-1"] = {
        "from_id": "CL-1", "to_id": "CL-2", "relation_type": "supports"
    }
    assert not _package_graph_unchanged(original, reviewed, "CL-1")


def test_missing_reciprocal_evidence_is_not_a_status_only_candidate():
    package = _package()
    package["evidence_steps"]["E-1"]["produced_claim_ids"] = []
    assert not _package_graph_unchanged(package, package, "CL-1")


def test_graph_guard_accepts_only_equivalent_source_hash_representation():
    package = _package()
    expected = {
        (collection, object_id): payload
        for collection, records in package.items() if collection != "claims"
        for object_id, payload in records.items()
    }
    live = {
        key: (2, "b" * 64, dict(value)) for key, value in expected.items()
    }
    live[("source_fragments", "F-1")][2]["source_sha256"] = "c" * 64
    live[("source_fragments", "F-1")][2].pop("visual_facts")
    hashes = {"SRC-1": {"a" * 64, "c" * 64}}
    guard = _live_graph_guard(expected, live, hashes)
    assert guard is not None and len(guard) == 2
    live[("source_fragments", "F-1")][2]["verbatim_excerpt"] = "改过的原话"
    assert _live_graph_guard(expected, live, hashes) is None


def test_exact_graph_reference_does_not_match_substrings():
    assert _contains_exact_value({"from_id": "CL-1"}, "CL-1")
    assert not _contains_exact_value({"from_id": "CL-10"}, "CL-1")


def test_withdrawn_plan_requires_graph_proof_and_nonhuman_review():
    payload = {"claim_id": "CL-1", "statement": "原有主张",
               "evidence_step_ids": ["E-1"], "review_status": "candidate",
               "revision": 1}
    current = {"CL-1": {"revision": 1,
                        "content_sha256": record_content_sha(payload),
                        "payload": payload}}
    row = {
        "claim_id": "CL-1", "revision": 1,
        "content_sha256": current["CL-1"]["content_sha256"],
        "reason": "withdrawn_unchanged_graph_verified",
        "review_decision": "changes_suggested",
        "adjudication_status": "withdrawn",
        "target_review_status": "ai_consensus_reviewed",
        "source_id": "SRC-1", "source_revision": 2,
        "source_content_sha256": "a" * 64,
        "reviewer_id": "independent-model",
        "adjudicator_id": "adjudicator-model",
        "adjudicated_at": "2026-09-01T00:00:00+00:00",
        "package_sha256": "b" * 64,
        "review_sha256": "c" * 64,
        "review_fingerprint": "d" * 64,
        "adjudication_sha256": "e" * 64,
        "reviewed_candidate_sha256": "f" * 64,
        "graph_guard_sha256": "1" * 64,
    }
    plan = plan_review_migration([row], current, freeze_sha256="2" * 64,
                                 source_kind=SOURCE_KIND)
    assert plan.operations[0].payload["review_status"] == "ai_consensus_reviewed"
    assert plan.review_events[0].artifact["graph_guard_sha256"] == "1" * 64
    with pytest.raises(ValueError, match="graph proof"):
        plan_review_migration([{**row, "review_decision": "human_review_required"}],
                              current, freeze_sha256="2" * 64,
                              source_kind=SOURCE_KIND)


def test_auto_applied_plan_requires_historical_replay_proof():
    from backend.pipeline.legacy_auto_applied_review_reconciliation import (
        SOURCE_KIND as AUTO_SOURCE_KIND,
    )

    payload = {"claim_id": "CL-1", "statement": "已接受补丁",
               "evidence_step_ids": ["E-1"], "review_status": "candidate",
               "revision": 1}
    current = {"CL-1": {"revision": 1,
                        "content_sha256": record_content_sha(payload),
                        "payload": payload}}
    row = {
        "claim_id": "CL-1", "revision": 1,
        "content_sha256": current["CL-1"]["content_sha256"],
        "reason": "auto_applied_historical_replay_graph_verified",
        "review_decision": "changes_suggested",
        "adjudication_status": "auto_applied",
        "target_review_status": "ai_consensus_reviewed",
        "source_id": "SRC-1", "source_revision": 2,
        "source_content_sha256": "a" * 64,
        "reviewer_id": "independent-model",
        "adjudicator_id": "adjudicator-model",
        "adjudicated_at": "2026-09-01T00:00:00+00:00",
        "package_sha256": "b" * 64,
        "review_sha256": "c" * 64,
        "review_fingerprint": "d" * 64,
        "adjudication_sha256": "e" * 64,
        "reviewed_candidate_sha256": "f" * 64,
        "graph_guard_sha256": "1" * 64,
        "historical_replay_sha256": "2" * 64,
        "historical_replay_code_sha256": "3" * 64,
        "overrides_sha256": "4" * 64,
    }
    plan = plan_review_migration([row], current, freeze_sha256="5" * 64,
                                 source_kind=AUTO_SOURCE_KIND)
    assert plan.operations[0].payload["review_status"] == "ai_consensus_reviewed"
    assert plan.review_events[0].artifact["historical_replay_sha256"] == "2" * 64
    with pytest.raises(ValueError, match="replay proof"):
        plan_review_migration([{**row, "overrides_sha256": ""}], current,
                              freeze_sha256="5" * 64,
                              source_kind=AUTO_SOURCE_KIND)


def test_historical_replay_rejects_unpinned_code(monkeypatch):
    from types import SimpleNamespace
    import backend.pipeline.legacy_auto_applied_review_reconciliation as replay

    monkeypatch.setattr(replay, "_historical_function", None)
    monkeypatch.setattr(
        replay.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"different historical code"),
    )
    with pytest.raises(ValueError, match="code SHA changed"):
        replay._historical_applier()
