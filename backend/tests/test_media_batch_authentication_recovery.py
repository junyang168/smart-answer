import json

import pytest

from backend.pipeline.media_batch.credit_recovery import request_for
from backend.pipeline.media_batch.runtime import atomic, file_sha
from backend.pipeline.slide_change_pilot import object_sha


@pytest.fixture
def rejected_asr(tmp_path, monkeypatch):
    request = {"stage": "asr", "config": {"model": "gpt-transcribe"}, "audio_sha256": "audio"}
    folder = tmp_path / "calls" / object_sha(request)
    state = {"status": "rejected", "http_status": 401, "provider_error_code": "invalid_api_key"}
    atomic(folder / "request.json", request)
    atomic(folder / "state.json", state)
    authority = {
        "schema_version": "media-authentication-recovery.v1",
        "batch": "batch",
        "task": "task",
        "original_request_sha256": object_sha(request),
        "rejected_state_sha256": file_sha(folder / "state.json"),
        "max_attempts": 1,
    }
    monkeypatch.setenv("MEDIA_BATCH_CONTEXT", json.dumps({"batch": "batch", "task": "task"}))
    return request, folder, state, authority


def test_rejected_asr_recovery_preserves_original_and_has_one_stable_retry(tmp_path, rejected_asr):
    request, folder, state, authority = rejected_asr
    assert request_for(tmp_path, request) == request
    atomic(folder / "authentication-recovery.json", authority)
    recovered = request_for(tmp_path, request)
    assert {k: v for k, v in recovered.items() if k != "authentication_recovery"} == request
    assert object_sha(recovered) != object_sha(request)
    assert request_for(tmp_path, request) == request_for(tmp_path, recovered) == recovered
    assert json.loads((folder / "state.json").read_text()) == state


@pytest.mark.parametrize("mismatch", ["unknown", "other_401", "changed_request", "wrong_task", "response"])
def test_recovery_rejects_unproven_or_out_of_scope_calls(tmp_path, rejected_asr, mismatch):
    request, folder, state, authority = rejected_asr
    if mismatch in {"unknown", "other_401"}:
        state.update(status="sent_outcome_unknown") if mismatch == "unknown" else state.update(provider_error_code="other")
        atomic(folder / "state.json", state)
        authority["rejected_state_sha256"] = file_sha(folder / "state.json")
    if mismatch == "changed_request":
        atomic(folder / "request.json", {**request, "audio_sha256": "changed"})
    if mismatch == "wrong_task":
        authority["task"] = "another task"
    if mismatch == "response":
        atomic(folder / "response.json", {"text": "provider already replied"})
    atomic(folder / "authentication-recovery.json", authority)
    with pytest.raises(ValueError, match="Authentication recovery"):
        request_for(tmp_path, request)
