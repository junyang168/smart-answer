"""Claim action/round durability in socket-only disposable PostgreSQL."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from backend.api.canonical_repository.postgres_store import ChangeSetConflict, record_content_sha
from backend.pipeline import claim_management as m
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store
from backend.tests.test_claim_management import policy


@pytest.fixture
def action(postgres_store, tmp_path):
    store = postgres_store
    source_file = tmp_path / "synthetic-source.json"
    source_file.write_text(json.dumps({"script": [{"text": "甲是假设，不是断言。"}]}))
    source_binding = binding(source_file)
    source_payload = {"source_id": "S", "source_type": "sermon_transcript", "title": "synthetic",
                      "source_path": str(source_file), "source_file_sha256": source_binding["file_sha256"]}
    store.apply_plan(store.plan_package({"package_id": "synthetic-source", "source_documents": [source_payload]}))
    p = policy()
    policy_file = tmp_path / "explicit-policy.json"
    policy_file.write_text(p.model_dump_json())
    auth_file = tmp_path / "owner-direction.json"
    request = m.Request(action_id="isolated-" + uuid.uuid4().hex, operation="Create", allowed_operations=["Create"],
                        source_bindings={"S": source_binding}, policy=binding(policy_file),
                        authorization={"path": str(auth_file), "file_sha256": "a" * 64},
                        creation_permissions=[m.CreationPermission(collection="claims", source_id="S",
                            fields=["statement", "claim_type", "occurrences", "evidence_step_ids"])])
    direction = {"schema_version": m.VERSION, "authorized_by": "owner", "authorization_quote": "隔离测试，授权合成来源内新建",
                 "human_content_approval": False, "request": request.model_dump(mode="json", exclude={"authorization", "policy"}),
                 "policy": request.policy.model_dump()}
    auth_file.write_text(json.dumps(direction, ensure_ascii=False))
    request.authorization = m.source.FileBinding.model_validate(binding(auth_file))
    state = store.get_record_state("source_documents", "S")
    before = {"source_documents/S": state}
    pins = {("source_documents", "S"): (state["revision"], state["content_sha256"], False)}
    goal = m.original_goal(request, before)
    registered = store.prepare_claim_management_action(request, goal=goal, read_pins=pins, root=tmp_path / "original-root")
    unit = next(iter(goal["units"]))
    return store, request, registered, unit, tmp_path, pins


def test_original_goal_root_and_budget_are_immutable(action):
    store, request, registered, unit, tmp_path, pins = action
    observed = store.prepare_claim_management_action(request, goal=registered["original_goal"], read_pins=pins,
                                                      root=tmp_path / "original-root")
    assert observed == registered
    with pytest.raises(ChangeSetConflict, match="cannot reset"):
        store.prepare_claim_management_action(request, goal=registered["original_goal"], read_pins=pins,
                                              root=tmp_path / "different-root")
    assert store.get_cvp_group(registered["run_id"])["call_reservations"] == {}


def test_prepare_reserves_whole_round_and_recovery_returns_same_identity(action):
    store, _, registered, unit, _, _ = action
    material = {"claim_ids": [], "current_source": "S", "original_goal": registered["original_goal"]}
    rid = store.prepare_claim_management_round(registered["run_id"], unit_id=unit, round_number=0,
                                              materials=material, roles=["gpt", "opus", "gemini"])
    repeated = store.prepare_claim_management_round(registered["run_id"], unit_id=unit, round_number=0,
                                                   materials=material, roles=["gpt", "opus", "gemini"])
    assert rid == repeated
    root = store.get_cvp_group(registered["run_id"])
    assert len(root["call_reservations"]) == 3
    assert {v["state"] for v in root["call_reservations"].values()} == {"planned"}
    assert store.get_cvp_group(rid)["checkpoint"] == "prepared"
    assert root["latest_rounds"][unit] == rid


def test_late_role_budget_failure_rolls_back_all_reservations(action):
    store, _, registered, unit, _, _ = action
    with store.connect() as conn:
        metadata = deepcopy(registered)
        metadata.pop("run_id")
        metadata["policy"]["budget"]["gemini_calls"] = 0
        conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s",
                     (json.dumps(metadata), registered["run_id"]))
    with pytest.raises(ChangeSetConflict, match="complete next round"):
        store.prepare_claim_management_round(registered["run_id"], unit_id=unit, round_number=0,
                                             materials={}, roles=["gpt", "opus", "gemini"])
    value = store.get_cvp_group(registered["run_id"])
    assert value["latest_rounds"] == {} and value["call_reservations"] == {}
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM wang_knowledge.pipeline_runs WHERE metadata->>'parent_run_id'=%s",
                            (registered["run_id"],)).fetchone() == (0,)


def test_started_call_does_not_acquire_a_second_send(action):
    store, _, registered, unit, _, _ = action
    rid = store.prepare_claim_management_round(registered["run_id"], unit_id=unit, round_number=0,
                                              materials={}, roles=["gpt", "opus", "gemini"])
    intent = {"path": "/synthetic-immutable-intent", "request_payload_sha256": "a" * 64}
    store.begin_claim_management_call(registered["run_id"], round_id=rid, role="gpt", intent=intent)
    with pytest.raises(ChangeSetConflict, match="do not resend"):
        store.begin_claim_management_call(registered["run_id"], round_id=rid, role="gpt", intent=intent)
    assert store.get_cvp_group(registered["run_id"])["call_reservations"][rid + ":gpt"]["state"] == "started"


def test_round_cannot_drop_independent_role_or_invent_a_unit(action):
    store, _, registered, unit, _, _ = action
    with pytest.raises(ChangeSetConflict, match="every required role"):
        store.prepare_claim_management_round(registered["run_id"], unit_id=unit, round_number=0,
                                             materials={}, roles=["gpt", "opus"])
    with pytest.raises(ChangeSetConflict, match="invent"):
        store.prepare_claim_management_round(registered["run_id"], unit_id="unowned-unit", round_number=0,
                                             materials={}, roles=["gpt", "opus", "gemini"])


def test_stale_initial_pin_refuses_registration_before_any_action_row(action):
    store, request, registered, _, tmp_path, pins = action
    # Only this disposable DB is changed. Preserve the original request's
    # caller identity test separately; a fresh action needs an actual fresh pin.
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET revision=revision+1 WHERE collection='source_documents' AND object_id='S'")
    fresh = request.model_copy(deep=True)
    fresh.action_id = "fresh-" + uuid.uuid4().hex
    auth = json.loads(Path(request.authorization.path).read_text())
    auth["request"] = fresh.model_dump(mode="json", exclude={"authorization", "policy"})
    path = tmp_path / "fresh-authorization.json"
    path.write_text(json.dumps(auth))
    fresh.authorization = m.source.FileBinding.model_validate(binding(path))
    with pytest.raises(ChangeSetConflict, match="dependency drift"):
        store.prepare_claim_management_action(fresh, goal=registered['original_goal'], read_pins=pins,
                                              root=tmp_path / "fresh-root")
