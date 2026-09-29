"""No-model checks for the Claude-only, disjoint reviewer queue."""

from threading import Barrier

import pytest

from backend.pipeline import claim_passage_role_independent_prefetch as queue


def test_batch_ownership_is_disjoint():
    assert queue._lane_for(82) == "a"
    assert queue._lane_for(83) == "b"
    assert queue._lane_for(84) == "a"


def test_two_review_workers_overlap_without_gpt_dependency(tmp_path, monkeypatch):
    gate = Barrier(2)
    calls = []
    packet = {"claims": [{}] * (83 * 16), "artifact_sha256": "packet"}
    manifest = {"total_batches": 798, "artifact_sha256": "manifest"}
    monkeypatch.setattr(queue, "_check_manifest", lambda *args: (packet, manifest))
    monkeypatch.setattr(queue.audited, "_check_graph", lambda *args: None)

    class FakeClient:
        model = "claude-opus-5-5"

    def fake_run_one(batch_id, *, packet, manifest, output_root, client):
        assert isinstance(client, FakeClient)
        calls.append((batch_id, queue._lane_for(batch_id)))
        gate.wait(timeout=2)
        return {"batch_id": batch_id, "claims": 16}

    monkeypatch.setattr(queue, "_run_one", fake_run_one)
    result = queue.run(tmp_path, tmp_path, tmp_path, None, last_batch=83,
                       client_factory=FakeClient)
    assert sorted(calls) == [(82, "a"), (83, "b")]
    assert result["completed_batches"] == 2
    assert result["claims"] == 32


def test_run_rejects_out_of_range_before_model_client(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "_check_manifest", lambda *args: (
        {"claims": [], "artifact_sha256": "packet"},
        {"total_batches": 83, "artifact_sha256": "manifest"},
    ))
    with pytest.raises(ValueError, match="outside"):
        queue.run(tmp_path, tmp_path, tmp_path, None, last_batch=84)
