"""Owner-authorized single retry; original unknown receipt remains immutable."""

import json
import os
from pathlib import Path
from .runtime import file_sha
from backend.pipeline.slide_change_pilot import object_sha


def effective(out, request):
    folder = Path(out) / "calls" / object_sha(request)
    authority = folder / "timeout-recovery.json"
    if not authority.exists():
        return request
    saved = json.loads(authority.read_text())
    state = folder / "state.json"
    if (
        saved.get("schema_version") != "media-timeout-recovery.v1"
        or saved.get("max_attempts") != 1
        or saved.get("original_request_sha256") != object_sha(request)
        or saved.get("unknown_state_sha256") != file_sha(state)
        or json.loads(state.read_text()).get("status") != "sent_outcome_unknown"
        or json.loads((folder / "request.json").read_text()) != request
        or (folder / "response.json").exists()
    ):
        raise ValueError("Timeout recovery does not match original unknown request")
    result = {
        **request,
        "timeout_recovery": {
            "authority_sha256": file_sha(authority),
            "original_request_sha256": object_sha(request),
        },
    }
    target = Path(out) / "calls" / object_sha(result) / "state.json"
    cached = (
        target.exists() and json.loads(target.read_text()).get("status") == "complete"
    )
    ctx = json.loads(os.getenv("MEDIA_BATCH_CONTEXT", "{}"))
    if not cached and (ctx.get("batch"), ctx.get("task")) != (
        saved.get("batch"),
        saved.get("task"),
    ):
        raise ValueError("Timeout retry is authorized only for its original task")
    return result


def covered(out, folder, current_request):
    """Only this exact authorized retry, or its verified success, covers a hold."""
    if not (folder / "timeout-recovery.json").exists():
        return False
    original = json.loads((folder / "request.json").read_text())
    retry = effective(out, original)
    if retry == current_request:
        return True
    target = Path(out) / "calls" / object_sha(retry)
    state_path = target / "state.json"
    if not state_path.exists():
        return False
    state = json.loads(state_path.read_text())
    return (
        state.get("status") == "complete"
        and json.loads((target / "request.json").read_text()) == retry
        and file_sha(target / "response.json") == state.get("response_sha256")
    )
