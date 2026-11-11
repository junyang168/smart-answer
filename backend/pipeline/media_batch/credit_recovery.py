"""One explicitly authorized retry of a proven credit rejection; never unknown calls."""

import json
import os
from pathlib import Path
from .runtime import file_sha
from backend.pipeline.slide_change_pilot import object_sha


def request_for(out, request):
    from .timeout_recovery import effective
    request = effective(out, request)
    request = recover_authentication(out, request)
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


def recover_authentication(out, request):
    """Permit one proven pre-execution ASR rejection to use the corrected credential."""
    folder = Path(out) / "calls" / object_sha(request)
    authority_path = folder / "authentication-recovery.json"
    if not authority_path.is_file():
        return request
    authority = json.loads(authority_path.read_text())
    state_path = folder / "state.json"
    state = json.loads(state_path.read_text())
    if (
        request.get("stage") != "asr"
        or request.get("config", {}).get("model") != "gpt-transcribe"
        or any(k in request for k in ("authentication_recovery", "credit_recovery", "timeout_recovery"))
        or state.get("status") != "rejected"
        or state.get("http_status") != 401
        or state.get("provider_error_code") != "invalid_api_key"
        or (folder / "response.json").exists()
        or json.loads((folder / "request.json").read_text()) != request
        or authority.get("schema_version") != "media-authentication-recovery.v1"
        or authority.get("original_request_sha256") != object_sha(request)
        or authority.get("rejected_state_sha256") != file_sha(state_path)
        or authority.get("max_attempts") != 1
    ):
        raise ValueError("Authentication recovery requires the exact rejected ASR request")
    retried = {
        **request,
        "authentication_recovery": {
            "authority_sha256": file_sha(authority_path),
            "original_request_sha256": object_sha(request),
        },
    }
    target = Path(out) / "calls" / object_sha(retried) / "state.json"
    cached = target.exists() and json.loads(target.read_text()).get("status") == "complete"
    ctx = json.loads(os.getenv("MEDIA_BATCH_CONTEXT", "{}"))
    if not cached and (
        not ctx.get("batch") or not ctx.get("task")
        or (ctx["batch"], ctx["task"]) != (authority.get("batch"), authority.get("task"))
    ):
        raise ValueError("Authentication recovery belongs to another batch or task")
    return retried
