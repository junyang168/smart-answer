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
    request = recover_subscription_setup(out, request)
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


def recover_subscription_setup(out, request):
    """One engineering retry only when native facts prove no prompt was sent."""
    if 'subscription_setup_recovery' in request:
        return request
    folder=Path(out)/'calls'/object_sha(request)
    state_path=folder/'state.json'
    if not state_path.exists():return request
    state=json.loads(state_path.read_text())
    if (state.get('status'),state.get('http_status'),state.get('error_type')) != ('rejected',400,'SubscriptionSetupError'):
        return request
    from .store import key
    from backend.pipeline.media_call_mirror import save
    authority=folder/'subscription-setup-recovery.json'
    ctx=json.loads(os.getenv('MEDIA_BATCH_CONTEXT','{}'))
    saved=json.loads(authority.read_text()) if authority.exists() else ctx
    batch,task=saved.get('batch'),saved.get('task')
    import re
    if not isinstance(batch,str) or not re.fullmatch('B-[0-9a-f]{24}',batch) or not isinstance(task,str) or not re.fullmatch('[0-9a-f]{64}',task):
        raise ValueError('Subscription setup recovery requires its original task')
    mirror=Path.home()/'.local/state/smart-answer-media-batches'/batch/'model-calls'/task/object_sha(request)
    native=mirror/'subscription';rpc=native/'rpc.jsonl'
    if ((native/'prompt-intent.json').exists() or (folder/'response.json').exists() or (mirror/'response.json').exists()
            or json.loads((folder/'request.json').read_text())!=request
            or json.loads((mirror/'request.json').read_text())!=request
            or json.loads((mirror/'identity.json').read_text())!={'batch':batch,'task':task,'out':str(Path(out).resolve()),'call':key([task,str(Path(out).resolve()),request])}):
        raise ValueError('Subscription setup recovery lacks exact pre-prompt proof')
    events=[json.loads(line) for line in rpc.read_text().splitlines()]
    methods=[r['message'] for r in events if r['direction']=='client']
    if ([m.get('method') for m in methods]!=['initialize','authenticate','session/new']
            or not events or events[-1].get('direction')!='agent'
            or events[-1]['message'].get('id')!=methods[-1]['id']
            or events[-1]['message'].get('error')!={'code':-32603,'message':'Failed to start Antigravity conversation'}):
        raise ValueError('Subscription setup recovery requires a proven session/new rejection')
    proof={'schema_version':'media-subscription-setup-recovery.v1','batch':batch,'task':task,'max_attempts':1,
           'original_request_sha256':object_sha(request),'rejected_state_sha256':file_sha(state_path),
           'native_rpc_sha256':file_sha(rpc),'model_prompt_sent':False}
    if authority.exists() and saved!=proof:raise ValueError('Subscription setup recovery proof changed')
    retry={**request,'subscription_setup_recovery':proof}
    target=Path(out)/'calls'/object_sha(retry)
    cached=(target/'state.json').exists() and json.loads((target/'state.json').read_text()).get('status')=='complete'
    if not cached and (ctx.get('batch'),ctx.get('task'))!=(batch,task):
        raise ValueError('Subscription setup recovery belongs to another task')
    save(authority,proof)
    return retry


def setup_covered(out, folder):
    original=json.loads((folder/'request.json').read_text())
    if not (folder/'subscription-setup-recovery.json').exists():return False
    retry=recover_subscription_setup(out,original)
    if retry==original:return False
    target=Path(out)/'calls'/object_sha(retry)
    if not (target/'state.json').exists():return False
    state=json.loads((target/'state.json').read_text())
    return (state.get('status')=='complete' and json.loads((target/'request.json').read_text())==retry
            and file_sha(target/'response.json')==state.get('response_sha256'))
