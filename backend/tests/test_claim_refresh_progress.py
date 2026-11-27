"""Progress must expose stopped work, not invent liveness or owner decisions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import claim_refresh_progress as ui


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def plan():
    return {"original_count": 3, "sources": {"sermon-1": {"source_id": "S", "name": "講道一", "groups": [
        {"id": "1", "label": "第一段", "members": ["C1", "C2"]},
        {"id": "2", "label": "第二段", "members": ["C3"]},
    ]}}}


def action(aid="A", targets=("C1",), status="completed", **kwargs):
    return {"action_id": aid, "targets": ["claims/" + c for c in targets], "sources": {"S": {}},
            "result": {"status": status}, "updated_at": "2026-11-03T10:00:00+00:00", **kwargs}


def project(root, actions, workers=(), saved=()):
    return ui.project({"root": str(root), "id": "sample", "title": "三條原始 Claim"}, plan(), actions, set(workers), list(saved))


def test_partial_completion_and_reference_reuse_do_not_hide_unprepared_next_group(tmp_path):
    write(tmp_path / "sermon-1/group-1/operator-prepared.json", {"reference_claim_ids": ["C2"]})
    result = project(tmp_path, [action(created_keys=["claims/NEW", "evidence_steps/E"])])
    assert (result["total"], result["processed"], result["created"]) == (3, 2, 1)
    assert result["groups_processed"] == 1
    assert result["sources"][0]["groups"][1]["state"] == "pending"
    assert result["running_actions"] == 0
    assert result["owner_items"] == []


def test_successor_owns_current_stop_not_its_old_budget_error(tmp_path):
    old = action(status="paused", continued_by="B", result={"status": "paused", "reason": "budget_exhausted"})
    current = action("B", ("C1", "C2"))
    result = project(tmp_path, [old, current])
    assert result["processed"] == 2
    assert result["agent_items"] == []


@pytest.mark.parametrize("status", ["prepared", "running", "paused"])
def test_sql_running_or_paused_is_not_a_live_worker_or_human_task(tmp_path, status):
    row = action(status=status, result={"status": status, "reason": "engineering_required", "detail": "saved validator error"})
    result = project(tmp_path, [row])
    assert result["running_actions"] == 0
    assert result["processed"] == 0
    assert result["owner_items"] == []
    assert result["agent_items"][0]["detail"] == "saved validator error"
    live = project(tmp_path, [row], workers=["A"])
    assert live["running_actions"] == 1
    assert live["sources"][0]["groups"][0]["state"] == "running"
    assert live["agent_items"] == []


def test_real_source_question_and_recorded_answer_are_distinct_from_ai_flags(tmp_path):
    finding = {"category": "source_uncertain", "human_question": "這句是教授的評論嗎？", "reason": "歸屬待核",
               "citations": [{"source_id": "S", "paragraph_key": "S1", "exact_quote": "教會不會增長才怪。"}]}
    row = action(status="paused", result={"status": "paused", "reason": "human_required"}, findings={"F1": finding})
    pending = project(tmp_path, [row])
    assert pending["owner_items"][0]["question"] == finding["human_question"]
    assert pending["owner_items"][0]["citations"][0]["exact_quote"] == "教會不會增長才怪。"
    answered = project(tmp_path, [row], saved=[{"source_id": "S", "entry": {"record": {"answers": [
        {"finding_id": "F1", "question": finding["human_question"], "answer": "教授的評論"}]}}}])
    assert answered["owner_items"] == []
    assert answered["agent_items"][0]["reason"] == "human_required"
    wrong = project(tmp_path, [row], saved=[{"source_id": "OTHER", "entry": {"record": {"answers": [
        {"finding_id": "F1", "question": finding["human_question"], "answer": "教授的評論"}]}}}])
    assert len(wrong["owner_items"]) == 1


def test_saved_qualification_is_dated_and_not_a_currentness_rebuild(tmp_path):
    write(tmp_path / "sermon-1/final-currentness.json", {"completed": True, "members": ["C1", "C2", "C3", "NEW"]})
    result = project(tmp_path, [action(targets=("C1", "C2", "C3"))])
    verified = result["sources"][0]["verification"]
    assert verified["passed"] and verified["count"] == 4 and verified["at"]
    assert result["total"] == 3  # a created Claim never changes the denominator


def test_multi_source_stop_is_agent_work_and_does_not_discard_three_claims(tmp_path):
    write(tmp_path / "sermon-1/group-1/source-ownership-preparation-stop.json", {"members": ["C1", "C2"]})
    result = project(tmp_path, [])
    assert result["total"] == 3 and result["processed"] == 0
    assert result["owner_items"] == []
    assert result["agent_items"][0]["reason"] == "source_ownership"


@pytest.mark.parametrize("locked", [False, True])
def test_live_action_needs_its_exact_execution_lock_not_a_saved_pid(tmp_path, monkeypatch, locked):
    execution = tmp_path / "execution"
    write(tmp_path / "batch-v1/status.json", {"active": {"B": 1234}})
    rows = [action(execution_root=str(execution)), action("B", execution_root=str(tmp_path / "other"))]
    monkeypatch.setattr(ui, "held", lambda path: locked and path == execution / "worker.lock")
    assert ui.live_actions(tmp_path, rows) == ({"A"} if locked else set())


def test_active_slot_journal_alone_or_finished_transport_is_not_a_session(tmp_path, monkeypatch):
    capacity = tmp_path / "capacity"
    transport = tmp_path / "transport"
    write(tmp_path / "capacity.json", {"root": str(capacity)})
    write(capacity / "opus/slot-0.json", {"active": True, "artifacts": str(transport)})
    monkeypatch.setattr(ui, "held", lambda _: False)
    assert ui.sessions(tmp_path)["opus"]["active"] == 0
    monkeypatch.setattr(ui, "held", lambda _: True)
    assert ui.sessions(tmp_path)["opus"]["active"] == 1
    write(transport / "exit.json", {"code": 0})
    assert ui.sessions(tmp_path)["opus"]["active"] == 0


def test_lock_probe_is_read_only(tmp_path):
    missing = tmp_path / "missing.lock"
    assert not ui.held(missing) and not missing.exists()
    lock = tmp_path / "existing.lock"
    lock.write_text("unchanged")
    assert not ui.held(lock)
    assert lock.read_text() == "unchanged"


def test_native_recovery_liveness_requires_its_exact_held_transport(tmp_path, monkeypatch):
    capacity = tmp_path / "capacity"
    execution = tmp_path / "action"
    write(tmp_path / "capacity.json", {"root": str(capacity)})
    transport = execution / "round/gpt.transport"
    write(capacity / "gpt/slot-0.json", {"active": True, "artifacts": str(transport)})
    rows = [action(execution_root=str(execution)), action("B", execution_root=str(tmp_path / "other"))]
    monkeypatch.setattr(ui, "held", lambda _: True)
    assert ui.active_model_actions(tmp_path, rows) == {"A"}
    write(transport / "exit.json", {"code": 0})
    assert ui.active_model_actions(tmp_path, rows) == set()


def test_frozen_plan_rejects_drift_and_duplicate_denominator(tmp_path):
    path = write(tmp_path / "plan.json", plan())
    stat = path.stat()
    with pytest.raises(ValueError, match="版本不符"):
        ui.frozen_plan(str(path), stat.st_mtime_ns, stat.st_size, "0" * 64)
    changed = plan()
    changed["sources"]["sermon-1"]["groups"][1]["members"] = ["C2"]
    write(path, changed)
    stat = path.stat()
    with pytest.raises(ValueError, match="遺漏或重複"):
        ui.frozen_plan(str(path), stat.st_mtime_ns, stat.st_size, ui.hashlib.sha256(path.read_bytes()).hexdigest())


def test_route_uses_registered_ids_and_does_not_rescan_sql_or_call_models(tmp_path, monkeypatch):
    path = write(tmp_path / "plan.json", plan())
    write(tmp_path / "registry.json", {"campaigns": [{"id": "one", "title": "Example", "root": str(tmp_path),
          "plan": {"path": str(path), "file_sha256": ui.hashlib.sha256(path.read_bytes()).hexdigest()}, "seed_action_ids": ["A"]}]})
    write(tmp_path / "agent-prepared-refill/one.json", {"action_id": "B"})
    monkeypatch.setenv("WANG_CLAIM_REFRESH_CAMPAIGNS_FILE", str(tmp_path / "registry.json"))
    monkeypatch.setattr(ui, "PostgresKnowledgeStore", lambda: object())
    calls = []
    monkeypatch.setattr(ui, "action_rows", lambda store, ids: calls.append(ids) or [action()])
    monkeypatch.setattr(ui, "decisions", lambda *a: [])
    app = FastAPI(); app.include_router(ui.router)
    client = TestClient(app)
    response = client.get("/admin/wang/claim-refresh")
    assert response.status_code == 200
    assert response.json()["selected"]["total"] == 3
    assert calls == [{"A", "B"}]
    assert client.get("/admin/wang/claim-refresh?campaign=../another-root").status_code == 404
    assert client.post("/admin/wang/claim-refresh").status_code == 405
    write(path, {"bad": "changed"})
    assert client.get("/admin/wang/claim-refresh").status_code == 503
