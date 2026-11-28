"""Source-bound durable model calls shared by media stages."""
from pathlib import Path
import time
from backend.pipeline.slide_change_pilot import file_sha, object_sha, read_json, save_json

def _saved_call(out: Path, request: dict, reserved_usd: float, invoke, *, max_calls: int = 16,
               budget_usd: float = 3.0, admit=lambda: None) -> dict:
    folder = out / 'calls' / object_sha(request)
    state_path = folder / 'state.json'
    if state_path.exists():
        state = read_json(state_path)
        if read_json(folder / 'request.json') != request:
            raise ValueError('Saved model request changed')
        if state['status'] != 'complete':
            raise ValueError(f'Unresolved or rejected request at {folder}; never resend automatically')
        if file_sha(folder / 'response.json') != state['response_sha256']:
            raise ValueError('Saved model response changed')
        return read_json(folder / 'response.json')
    states = [read_json(p) for p in (out / 'calls').glob('*/state.json')]
    from backend.pipeline.media_batch.timeout_recovery import covered
    for p in (out / 'calls').glob('*/state.json'):
        if read_json(p)['status'] == 'sent_outcome_unknown' and not covered(out, p.parent, request):
            raise ValueError('Previous call outcome unknown; reconcile before another model call')
    if len(states) >= max_calls or sum(s['reserved_usd'] for s in states) + reserved_usd > budget_usd:
        raise ValueError('Pilot call or cost reservation limit exhausted')
    admit()
    save_json(folder / 'request.json', request)
    state = {'status': 'sent_outcome_unknown', 'reserved_usd': reserved_usd}
    save_json(state_path, state)
    started = time.perf_counter()
    try:
        result = invoke()
    except Exception as error:
        status = getattr(error, 'status_code', None) or getattr(error, 'code', None)
        state.update(http_status=status, error_type=type(error).__name__)
        provider_code = getattr(error, 'code', None)
        if isinstance(provider_code, str): state['provider_error_code'] = provider_code
        if status in (400, 401, 402, 403, 404, 422, 429):
            state['status'] = 'rejected'
        save_json(state_path, state)
        raise
    save_json(folder / 'response.json', result)
    state.update(status='complete', response_sha256=file_sha(folder / 'response.json'),
                 wall_seconds=time.perf_counter() - started)
    save_json(state_path, state)
    return result

def saved_call(out: Path, request: dict, reserved_usd: float, invoke, *, max_calls: int = 16,
               budget_usd: float = 3.0) -> dict:
    from backend.pipeline.media_batch.runtime import batch_call
    from backend.pipeline.media_batch.credit_recovery import request_for
    request = request_for(out, request)
    with batch_call(out, request, reserved_usd) as admit:
        return _saved_call(out, request, reserved_usd, invoke, max_calls=max_calls, budget_usd=budget_usd, admit=admit)
