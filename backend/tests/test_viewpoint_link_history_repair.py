from copy import deepcopy

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import viewpoint_link_history_repair as repair


def version(payload, kind="review_decision", metadata=None):
    return dict(payload=payload, revision=payload["revision"], content_sha256=record_content_sha(payload),
                status="applied", source_kind=kind, source_sha256="source", change_set_id="KCS-test", metadata=metadata or {})


def claim():
    return dict(claim_id="CL-test", revision=1, statement="教授的原话", evidence_step_ids=["E1"],
                occurrences=[dict(source_id="SRC-old", anchors=[dict(paragraph_key="P1", proposed_highlight={"text": "原话"})])])


def link(p):
    return dict(viewpoint_claim_link_id="VCL-test", claim_id=p["claim_id"], revision=1,
                pinned_claim_revision=1, viewpoint_id="CV-test", decision_id="VID-test",
                occurrence_refs=["OCC-historical"], evidence_bindings=[dict(evidence_step_id="E1", source_fragment_id="FR1")],
                component_locator=dict(claim_sha256=record_content_sha(p), statement_component="原话",
                                       canonical_spans=[dict(start_char=3, end_char=5, exact_text="原话")]))


def plan_for(monkeypatch, before, after, collection="claims", groups=None):
    plan = dict(source_kind=after["source_kind"], change_set_id="KCS-test", source_sha256="source",
                operations=[dict(collection=collection, object_id=before["payload"].get("claim_id", "FR1"), operation="update",
                                 before_sha256=before["content_sha256"], after_sha256=after["content_sha256"],
                                 before_revision=before["revision"], after_revision=after["revision"], payload=after["payload"])])
    artifact = dict(plan=plan, groups=groups or [])
    monkeypatch.setattr(repair, "authenticated_plan", lambda *args: artifact)
    after["metadata"] = dict(source_plan_path="/plan.json", source_plan_file_sha256="sha")
    return artifact


def test_review_metadata_repin_preserves_identity_and_bindings():
    old = claim(); new = deepcopy(old); new.update(revision=2, reviewed_by="independent")
    original = link(old)
    updated, proof = repair.repair_link(original, claim=new, history=[version(old), version(new)])
    assert updated["pinned_claim_revision"] == 2
    assert updated["component_locator"]["claim_sha256"] == record_content_sha(new)
    for field in ("viewpoint_id", "decision_id", "evidence_bindings", "occurrence_refs"):
        assert updated[field] == original[field]
    assert original["revision"] == 1 and not proof["new_evidence_added_to_cvp"]


def test_coordinate_only_applied_plan(monkeypatch):
    old = claim(); new = deepcopy(old); new["revision"] = 2
    new["occurrences"][0]["anchors"][0]["paragraph_key"] = "S0001"
    a, b = version(old), version(new, "wkp368_source_contract_cleanup")
    plan_for(monkeypatch, a, b)
    updated, _ = repair.repair_link(link(old), claim=new, history=[a, b])
    assert updated["revision"] == 2


@pytest.mark.parametrize("field,value", [("statement", "编造新内容"), ("evidence_step_ids", ["E2"]), ("visibility", "public")])
def test_coordinate_plan_never_authorizes_semantic_drift(monkeypatch, field, value):
    old = claim(); new = deepcopy(old); new.update(revision=2); new[field] = value
    a, b = version(old), version(new, "wkp368_source_contract_cleanup")
    plan_for(monkeypatch, a, b)
    with pytest.raises(ValueError, match="meaning-bearing"):
        repair.repair_link(link(old), claim=new, history=[a, b])


def test_source_alias_requires_applied_declared_mapping(monkeypatch):
    old = claim(); new = deepcopy(old); new["revision"] = 2; new["occurrences"][0]["source_id"] = "SRC-canonical"
    a, b = version(old), version(new, "wkp368_source_duplicate_cleanup")
    artifact = plan_for(monkeypatch, a, b)
    with pytest.raises(ValueError, match="alias"):
        repair.repair_link(link(old), claim=new, history=[a, b])
    artifact["groups"] = [dict(alias_source_id="SRC-old", canonical_source_id="SRC-canonical")]
    repair.repair_link(link(old), claim=new, history=[a, b])


def pair_metadata(old, actions):
    auth = dict(actions=actions); auth["artifact_sha256"] = sha256_json(auth)
    guard = dict(plan_identity=dict(change_set_id="KCS-test", source_kind="wkp364_claim_evidence_pair_adjudication", source_sha256="source"), expected_pair_adjudication_authorization=auth)
    guard["guard_sha256"] = sha256_json(guard)
    return dict(claim_evidence_reciprocity_repair=dict(store_guard=guard, pair_adjudication_authorization_sha256=auth["artifact_sha256"]))


def test_adjudicated_addition_does_not_expand_cvp_evidence():
    old = claim(); new = deepcopy(old); new.update(revision=2, evidence_step_ids=["E1", "E2"])
    action = dict(claim_id="CL-test", evidence_step_id="E2", effective_decision="include", reviewed_decision="include",
                  decision_basis="independent_model_consensus_include", claim_content_sha256=record_content_sha(old))
    a, b = version(old), version(new, "wkp364_claim_evidence_pair_adjudication", pair_metadata(old, [action]))
    updated, proof = repair.repair_link(link(old), claim=new, history=[a, b])
    assert updated["evidence_bindings"] == link(old)["evidence_bindings"]
    assert proof["claim_transitions"][0]["authorized_added_evidence_ids"] == ["E2"]


def test_unreviewed_addition_rejected():
    old = claim(); new = deepcopy(old); new.update(revision=2, evidence_step_ids=["E1", "E2"])
    a, b = version(old), version(new, "wkp364_claim_evidence_pair_adjudication", pair_metadata(old, []))
    with pytest.raises(ValueError, match="include decisions"):
        repair.repair_link(link(old), claim=new, history=[a, b])


@pytest.mark.parametrize("corruption", ["sha", "status", "gap", "baseline", "component"])
def test_corrupted_history_or_component_rejected(corruption):
    old = claim(); new = deepcopy(old); new.update(revision=2, reviewed_by="independent")
    a, b = version(old), version(new); l = link(old)
    if corruption == "sha": b["content_sha256"] = "wrong"
    if corruption == "status": b["status"] = "planned"
    if corruption == "gap": b["revision"] = 3
    if corruption == "baseline": l["pinned_claim_revision"] = 2
    if corruption == "component": l["component_locator"]["claim_sha256"] = "wrong"
    with pytest.raises(ValueError):
        repair.repair_link(l, claim=new, history=[a, b])


def test_plan_operation_generation_must_match(monkeypatch):
    old = claim(); new = deepcopy(old); new["revision"] = 2
    a, b = version(old), version(new, "wkp368_source_contract_cleanup")
    artifact = plan_for(monkeypatch, a, b); artifact["plan"]["operations"][0]["before_sha256"] = "wrong"
    with pytest.raises(ValueError, match="exact before/after"):
        repair.repair_link(link(old), claim=new, history=[a, b])


def test_reverse_evidence_index_preserves_argument():
    old = dict(revision=1, produced_claim_ids=["CL1"], statement="原有论证")
    new = dict(revision=2, produced_claim_ids=["CL1", "CL2"], statement="原有论证")
    action = dict(claim_id="CL2", evidence_step_id="E1", effective_decision="include",
                  evidence_content_sha256=record_content_sha(old))
    a, b = version(old), version(new, "wkp364_claim_evidence_pair_adjudication", pair_metadata(old, [action]))
    repair.verify_chain([a, b], collection="evidence_steps", object_id="E1", current=new)
    new["statement"] = "改变论证"
    b = version(new, "wkp364_claim_evidence_pair_adjudication", pair_metadata(old, [action]))
    with pytest.raises(ValueError, match="non-index"):
        repair.verify_chain([a, b], collection="evidence_steps", object_id="E1", current=new)


def test_physical_plan_sha_is_mandatory(tmp_path):
    path = tmp_path / "plan.json"; path.write_text("{}")
    repair.authenticated_plan.cache_clear()
    with pytest.raises(ValueError, match="physical SHA"):
        repair.authenticated_plan(str(path), "wrong")
