"""One explicitly authorized retry of a proven credit rejection; never unknown calls."""

import json
import os
from pathlib import Path
from .runtime import file_sha
from backend.pipeline.slide_change_pilot import object_sha


def request_for(out, request):
    from .timeout_recovery import effective
    request = effective(out, request)
    folder = Path(out) / "calls" / object_sha(request)
    state_path = folder / "state.json"
    authority = folder / "credit-recovery.json"
    if not state_path.exists() or not authority.exists():
        return request
    state = json.loads(state_path.read_text())
    if state.get("status") == "complete":
        return request
    saved = json.loads(authority.read_text())
    if (
        state.get("status") != "rejected"
        or state.get("http_status") != 429
        or saved.get("schema_version") != "media-credit-recovery.v1"
        or saved.get("reason") != "owner-confirmed-credit-topup"
        or saved.get("max_attempts") != 1
        or saved.get("original_request_sha256") != object_sha(request)
        or saved.get("rejected_state_sha256") != file_sha(state_path)
    ):
        raise ValueError("Credit recovery does not match a proven rejected request")
    if json.loads((folder / "request.json").read_text()) != request:
        raise ValueError("Original rejected request changed")
    effective = {
        **request,
        "credit_recovery": dict(
            authority_sha256=file_sha(authority),
            original_request_sha256=object_sha(request),
            rejected_state_sha256=file_sha(state_path),
        ),
    }
    target = Path(out) / "calls" / object_sha(effective) / "state.json"
    # A saved successful retry may be reused in a later full-pipeline batch.
    cached = (
        target.exists() and json.loads(target.read_text()).get("status") == "complete"
    )
    context = json.loads(os.getenv("MEDIA_BATCH_CONTEXT", "{}"))
    if not cached and context.get("batch") != saved.get("batch"):
        raise ValueError("Credit recovery is authorized only for its original batch")
    return effective
