"""No-model concurrency and ownership checks for the two-worker pilot."""

from __future__ import annotations

from threading import Barrier

import pytest

from backend.pipeline import claim_passage_role_parallel_pilot as pilot
from backend.pipeline import claim_passage_role_runner as base


ROW = {
    "claim_id": "C1", "statement": "application, not an interpretation",
    "scripture_refs": [],
    "evidence_steps": [{"statement": "support", "scripture_refs": [], "fragments": []}],
}


class FakeClient:
    def __init__(self, model: str, barrier: Barrier, response: dict):
        self.model = model
        self.barrier = barrier
        self.response = response

    def generate_json(self, prompt, payload, schema):
        assert "C1" in payload
        self.barrier.wait(timeout=2)
        return self.response


def _answer():
    return {"schema_version": base.RESPONSE_VERSION, "decisions": {"C1": {
        "role": "other", "interpreted_ref_indices": [],
        "interpreted_evidence_refs": [], "reason": "An application, not exegesis.",
    }}}


def test_reviewers_run_concurrently_and_retain_separate_raw_answers(tmp_path):
    gate = Barrier(2)
    reviewed, seconds = pilot.review_pair_parallel(
        rows=[ROW], packet_sha="packet", manifest_sha="manifest",
        batch_id=80, output_root=tmp_path,
        primary_client=FakeClient("gpt-6-sol", gate, _answer()),
        independent_client=FakeClient("claude-opus-5-5", gate, _answer()),
    )
    assert seconds >= 0
    assert len(reviewed) == 1 and reviewed[0]["role"] == "other"
    for role in ("primary", "independent"):
        raw = base._read_json(tmp_path / "attempts" / f"{role}-00080-attempt-1.json")
        base._check_artifact(raw)
        assert raw["response"] == _answer()
        accepted = base._read_json(tmp_path / f"{role}-00080.json")
        base._check_artifact(accepted)


def test_preflight_rejects_overlapping_workers_before_reading_data():
    with pytest.raises(ValueError, match="disjoint"):
        pilot.preflight(None, None, (80, 80), None)
