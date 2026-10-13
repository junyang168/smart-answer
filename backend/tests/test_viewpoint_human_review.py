from copy import deepcopy
import json

import pytest
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.pipeline.viewpoint_human_review import (
    binding, create_handoff, record_decision, resolution_status, sealed, validate_handoff,
)


def case(tmp_path):
    packet = {"after": "reviewed exact postimage"}
    write_immutable(tmp_path / "packet.json", packet)
    write_immutable(tmp_path / "raw-review.json", {"approved": False, "reason": "salt/light relation"})
    write_immutable(tmp_path / "before.json", {"pre_fingerprint_sha256": "registry-sha"})
    source = tmp_path / "source.txt"; source.write_text("own original text")
    handoff = create_handoff(tmp_path / "handoff.json", stage="repair_review",
        packet_path=tmp_path / "packet.json", review_paths=[tmp_path / "raw-review.json"],
        items=[{"item_id": "relation-a", "findings": ["meaning not proven"]},
               {"item_id": "relation-b", "findings": ["meaning not proven"]}],
        source_bindings=[binding(source)], before_path=tmp_path / "before.json")
    value = {"handoff_sha256": handoff["artifact_sha256"], "decided_by": "owner",
        "authorization_quotes": ["整体可以用发光概括，与做盐做光相容"],
        "item_ids": ["relation-a", "relation-b"], "action": "interpretation", "rationale": "compatible, not identical"}
    return packet, handoff, value


def test_interpretation_does_not_approve_postimage_or_rewrite_review(tmp_path):
    packet, handoff, value = case(tmp_path)
    raw_before = (tmp_path / "raw-review.json").read_bytes()
    d = record_decision(tmp_path / "handoff.json", value, tmp_path / "decision.json")
    before = deepcopy(d)
    result = resolution_status(tmp_path / "handoff.json", [tmp_path / "decision.json"],
        packet=packet, registry_fingerprint_sha256="registry-sha")
    assert result["status"] == "needs_owner_action"
    assert result["interpretations_recorded"] == ["relation-a", "relation-b"]
    assert not result["apply_allowed"] and result["master_data_mutations"] == result["model_calls"] == 0
    assert d == before and (tmp_path / "raw-review.json").read_bytes() == raw_before
    assert record_decision(tmp_path / "handoff.json", value, tmp_path / "decision.json") == d


def test_exact_acceptance_requires_execution_gates(tmp_path):
    packet, handoff, value = case(tmp_path)
    value.update(action="accept_exact_postimage", accepted_packet_sha256=handoff["packet_sha256"])
    d = record_decision(tmp_path / "handoff.json", value, tmp_path / "decision.json")
    r = resolution_status(tmp_path / "handoff.json", [tmp_path / "decision.json"],
        packet=packet, registry_fingerprint_sha256="registry-sha")
    assert r["status"] == "human_resolved_pending_execution_gates"
    assert r["approval_basis"] == "human_exception_review" and not r["apply_allowed"]
    assert d["model_verdicts_rewritten"] is False


@pytest.mark.parametrize("action", ["authorize_revision", "defer"])
def test_new_revision_or_defer_not_acceptance(tmp_path, action):
    packet, _, value = case(tmp_path); value["action"] = action
    record_decision(tmp_path / "handoff.json", value, tmp_path / "decision.json")
    assert resolution_status(tmp_path / "handoff.json", [tmp_path / "decision.json"],
        packet=packet, registry_fingerprint_sha256="registry-sha")["pending_item_ids"]


@pytest.mark.parametrize("mutation", ["foreign", "duplicate", "empty_actor", "empty_quote", "extra",
    "wrong_postimage", "missing_postimage", "interpretation_with_acceptance"])
def test_invalid_decisions_refused(tmp_path, mutation):
    _, h, v = case(tmp_path)
    if mutation == "foreign": v["item_ids"] = ["foreign"]
    if mutation == "duplicate": v["item_ids"] *= 2
    if mutation == "empty_actor": v["decided_by"] = " "
    if mutation == "empty_quote": v["authorization_quotes"] = [" "]
    if mutation == "extra": v["claim_statement"] = "rewrite"
    if mutation == "wrong_postimage": v.update(action="accept_exact_postimage", accepted_packet_sha256="wrong")
    if mutation == "missing_postimage": v["action"] = "accept_exact_postimage"
    if mutation == "interpretation_with_acceptance": v["accepted_packet_sha256"] = h["packet_sha256"]
    with pytest.raises(ValueError): record_decision(tmp_path / "handoff.json", v, tmp_path / "decision.json")


@pytest.mark.parametrize("drift", ["packet", "raw", "source", "baseline", "handoff"])
def test_physical_drift_refused(tmp_path, drift):
    _, _, _ = case(tmp_path)
    name = {"packet": "packet.json", "raw": "raw-review.json", "source": "source.txt",
            "baseline": "before.json", "handoff": "handoff.json"}[drift]
    (tmp_path / name).write_text("{}")
    with pytest.raises(ValueError): validate_handoff(tmp_path / "handoff.json")


@pytest.mark.parametrize("drift", ["live_registry", "missing_live_registry", "postimage", "duplicate_decision", "foreign_handoff", "decision_sha"])
def test_consumption_drift_or_duplicate_refused(tmp_path, drift):
    p, h, v = case(tmp_path)
    d = record_decision(tmp_path / "handoff.json", v, tmp_path / "decision.json")
    paths = [tmp_path / "decision.json"]; live = "registry-sha"
    if drift == "live_registry": live = "changed"
    if drift == "missing_live_registry": live = None
    if drift == "postimage": p["after"] = "changed"
    if drift == "duplicate_decision": paths *= 2
    if drift == "foreign_handoff":
        body = {k: value for k, value in d.items() if k != "artifact_sha256"}
        body["handoff_sha256"] = "foreign"
        (tmp_path / "decision.json").unlink(); write_immutable(tmp_path / "decision.json", sealed(body))
    if drift == "decision_sha":
        (tmp_path / "decision.json").write_text('{"schema_version":"wrong"}')
    with pytest.raises(ValueError): resolution_status(tmp_path / "handoff.json", paths, packet=p, registry_fingerprint_sha256=live)


def test_partial_acceptance_never_applies_half_package(tmp_path):
    p, h, v = case(tmp_path)
    v.update(action="accept_exact_postimage", accepted_packet_sha256=h["packet_sha256"], item_ids=["relation-a"])
    record_decision(tmp_path / "handoff.json", v, tmp_path / "decision.json")
    assert resolution_status(tmp_path / "handoff.json", [tmp_path / "decision.json"], packet=p,
        registry_fingerprint_sha256="registry-sha")["pending_item_ids"] == ["relation-b"]


def test_interpretation_and_later_explicit_action_are_separate(tmp_path):
    p, h, v = case(tmp_path)
    record_decision(tmp_path / "handoff.json", v, tmp_path / "interpretation.json")
    v.update(action="accept_exact_postimage", accepted_packet_sha256=h["packet_sha256"],
             authorization_quotes=["explicitly accept the exact submitted postimage"])
    record_decision(tmp_path / "handoff.json", v, tmp_path / "acceptance.json")
    r = resolution_status(tmp_path / "handoff.json", [tmp_path / "interpretation.json", tmp_path / "acceptance.json"],
        packet=p, registry_fingerprint_sha256="registry-sha")
    assert r["interpretations_recorded"] and not r["pending_item_ids"] and not r["apply_allowed"]


def test_execution_authority_requires_full_exact_acceptance(tmp_path):
    from backend.pipeline.viewpoint_human_review import execution_authority
    p, h, v = case(tmp_path)
    record_decision(tmp_path / "handoff.json", v, tmp_path / "interpretation.json")
    with pytest.raises(ValueError, match="exact execution action"):
        execution_authority(tmp_path / "handoff.json", [tmp_path / "interpretation.json"], packet=p,
                            registry_fingerprint_sha256="registry-sha")


def test_human_package_uses_existing_strict_provenance_schema(tmp_path):
    from backend.pipeline.viewpoint_human_review import execution_authority, mark_human_package
    from backend.tests.test_viewpoint_g033_identity_repair import storage_case
    from backend.api.canonical_repository.knowledge_models import ViewpointRevisionRecord, ViewpointRelationRecord
    p, h, v = case(tmp_path)
    v.update(action="accept_exact_postimage", accepted_packet_sha256=h["packet_sha256"])
    record_decision(tmp_path / "handoff.json", v, tmp_path / "decision.json")
    a = execution_authority(tmp_path / "handoff.json", [tmp_path / "decision.json"], packet=p,
                            registry_fingerprint_sha256="registry-sha")
    revision = storage_case()["after_revision"]
    revision.update(approved_at="2026-10-04T00:00:00Z")
    relation = ViewpointRelationRecord(viewpoint_relation_id="REL", source_viewpoint_id="A", target_viewpoint_id="B",
        validated_source_viewpoint_revision_id="AR", validated_target_viewpoint_revision_id="BR",
        relation_type="extends", reason="own source", review_provenance={"basis_identity_decision_ids": [], "review_artifact_sha256": "old"}).model_dump(mode="json")
    pkg = {"package_id": "SYNTHETIC", "viewpoint_revisions": [revision], "viewpoint_relations": [relation]}
    before = deepcopy(pkg); result = mark_human_package(pkg, a)
    assert pkg == before
    for collection, model in (("viewpoint_revisions", ViewpointRevisionRecord), ("viewpoint_relations", ViewpointRelationRecord)):
        model.model_validate(result[collection][0])
        assert result[collection][0]["review_status"] == "human_approved"
    assert result["viewpoint_revisions"][0]["provenance"]["review_artifact_sha256"] == a["artifact_sha256"]


@pytest.mark.parametrize("drift", [None, "response", "model", "backend", "packet", "population"])
def test_repair_hook_binds_actual_saved_raw_review(tmp_path, drift):
    from backend.pipeline.viewpoint_human_review import repair_handoff
    packet = {"targets": [{"claim": {"claim_id": "C"}, "sources": []}]}
    review = {"verdicts": [{"claim_id": "C", "approved": False, "reason": "semantic disagreement", "problems": ["relation scope"]}]}
    raw = {"schema_version": "wang_canonical_viewpoint_batch_raw_response_v1",
           "model_id": "claude-opus-5-5", "backend": "claude_subscription",
           "request_payload_sha256": sha256_json(packet),
           "response": deepcopy(review), "response_sha256": sha256_json(review)}
    if drift == "response": raw["response"]["verdicts"][0]["approved"] = True
    if drift == "model": raw["model_id"] = "other"
    if drift == "backend": raw["backend"] = "api"
    if drift == "packet": raw["request_payload_sha256"] = "foreign"
    if drift == "population": review["verdicts"] *= 2
    write_immutable(tmp_path / "packet.json", packet)
    write_immutable(tmp_path / "before.json", {"pre_fingerprint_sha256": "registry"})
    write_immutable(tmp_path / "raw-opus.json", sealed(raw))
    if drift:
        with pytest.raises(ValueError): repair_handoff(tmp_path, packet, {"opus": review})
    else:
        assert repair_handoff(tmp_path, packet, {"opus": review})["items"][0]["item_id"] == "opus:C"


@pytest.mark.parametrize("accept", [False, True])
def test_existing_repair_executor_consumes_only_explicit_exact_action(tmp_path, monkeypatch, accept):
    # The obsolete orchestration is no longer a supported solution.
    # Current content validators and SQL action tests carry its safety rules.
    from backend.pipeline import viewpoint_audit_link_repair as worker
    with pytest.raises(RuntimeError,match='runtime is retired'):
        worker.run(None,None)


@pytest.mark.parametrize("fields", [{"human_review_from": "old"}, {"human_handoff": "handoff"}, {"human_decisions": ["decision"]}])
def test_incomplete_human_resume_arguments_refused(tmp_path, monkeypatch, fields):
    # The obsolete orchestration is no longer a supported solution.
    # Current content validators and SQL action tests carry its safety rules.
    from backend.pipeline import viewpoint_audit_link_repair as worker
    with pytest.raises(RuntimeError,match='runtime is retired'):
        worker.run(None,None)


@pytest.mark.parametrize("stage", ["quota", "schema", "source_drift", "audit"])
def test_mechanical_and_audit_failures_not_waived(tmp_path, stage):
    p, h, v = case(tmp_path)
    with pytest.raises(ValueError): create_handoff(tmp_path / "other.json", stage=stage,
        packet_path=tmp_path / "packet.json", review_paths=[tmp_path / "raw-review.json"],
        items=h["items"])
