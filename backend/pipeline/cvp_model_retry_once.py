"""Explicit owner acceptance of one exact unanswered transport retry.

Never resets the original intent or claims the provider did not execute it.
The same cache receives the eventual answer with separate retry provenance.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

SCHEMA = 'wang_cvp_owner_model_retry_once_v1'
NAME = 'owner-model-retry-once-binding.json'
ACTIVE = ContextVar('cvp_owner_model_retry_once', default=None)


def sealed(d):
    if d.get('artifact_sha256') != sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'}):
        raise ValueError('owner retry seal drift')


def validate(d, root, campaign):
    from backend.pipeline.cvp_sql_commit import check_binding
    sealed(d)
    if (d.get('schema_version') != SCHEMA or d.get('authorized_by') != 'owner'
            or not d.get('authorization_quote') or d.get('maximum_additional_calls') != 1
            or d.get('accept_original_outcome_unknown') is not True
            or d.get('automatic_future_retries_authorized') is not False
            or d.get('human_content_approval') is not False
            or d.get('campaign_sha256') != campaign
            or Path(d.get('execution_root','')).resolve() != Path(root).resolve()):
        raise ValueError('invalid exact owner retry authority')
    cache=Path(d['cache_path']).resolve()
    if cache.parent != Path(root).resolve()/d['run_id']/'normal-review' or cache.name!='raw-proposal.json':
        raise ValueError('owner retry outside original normal proposal stage')
    wanted={str(cache.with_name(cache.stem+s)) for s in
            ('.call-intent.json','.call-failure.json','.request-encoding.json','.request-budget.json')}
    pins=d['original_bindings']
    if len(pins)!=4 or {p['path'] for p in pins}!=wanted:
        raise ValueError('owner retry original receipt population drift')
    for pin in pins:check_binding(pin)
    check_binding(d['timeout_log'])
    log=Path(d['timeout_log']['path']).read_text()
    if 'subprocess.TimeoutExpired:' not in log or 'timed out after 1800.0 seconds' not in log:
        raise ValueError('owner retry lacks exact transport timeout evidence')
    intent=read_artifact(cache.with_name(cache.stem+'.call-intent.json'));sealed(intent)
    failure=read_artifact(cache.with_name(cache.stem+'.call-failure.json'));sealed(failure)
    if (failure.get('invocation_sha256')!=intent['artifact_sha256']
            or failure.get('execution_status')!='outcome_unknown'
            or failure.get('automatic_retry') is not False
            or failure.get('error_type')!='CodexSubscriptionError'
            or intent.get('cache_path')!=str(cache) or intent.get('automatic_retry') is not False
            or intent.get('model_id')!='gpt-6.1-sol' or intent.get('backend')!='codex_subscription'):
        raise ValueError('owner retry original invocation drift')
    return intent


@contextmanager
def authorized_retry(root, campaign, authorization=None, store=None):
    from backend.pipeline.cvp_sql_commit import binding
    root=Path(root).resolve();path=root/NAME
    if authorization is None:
        if path.exists():raise ValueError('saved owner retry requires explicit original authorization')
        yield;return
    pin=binding(authorization);d=read_artifact(authorization);validate(d,root,campaign)
    if store is not None:
        group=store.get_cvp_group(d['run_id'])
        if not group or group['campaign_sha256']!=campaign:
            raise ValueError('owner retry foreign SQL group')
    value={'authorization':pin}
    write_immutable(path,value)
    token=ACTIVE.set((pin,d))
    try:yield
    finally:ACTIVE.reset(token)


def authority(cache, identity, encoding, request_bytes):
    from backend.pipeline.cvp_sql_commit import check_binding
    scope=ACTIVE.get()
    if scope is None or Path(cache).resolve()!=Path(scope[1]['cache_path']):return None
    pin,d=scope;check_binding(pin)
    intent=validate(d,d['execution_root'],d['campaign_sha256'])
    expected={'schema_version':'wang_model_call_intent_v1',**identity,
              'cache_path':str(Path(cache).resolve()),'automatic_retry':False}
    if intent!={**expected,'artifact_sha256':sha256_json(expected)}:
        raise ValueError('owner retry request/model/prompt/config drift')
    old=read_artifact(Path(cache).with_name(Path(cache).stem+'.request-encoding.json'))
    body={'schema_version':'wang_model_request_encoding_proof_v1',**encoding}
    if old!={**body,'artifact_sha256':sha256_json(body)}:
        raise ValueError('owner retry encoding drift')
    budget=read_artifact(Path(cache).with_name(Path(cache).stem+'.request-budget.json'))
    if budget.get('request_identity')!=identity or budget.get('actual_request_bytes')!=request_bytes:
        raise ValueError('owner retry bytes/budget drift')
    return {'authorization':pin,'original_invocation_sha256':intent['artifact_sha256'],
            'original_failure_sha256':read_artifact(Path(cache).with_name(Path(cache).stem+'.call-failure.json'))['artifact_sha256'],
            'maximum_additional_calls':1,'original_outcome_preserved':'outcome_unknown'}


def validate_saved(cache, adapter, payload, artifact):
    from backend.pipeline.viewpoint_resolution_runtime import model_request_identity
    proof=authority(cache,model_request_identity(adapter,payload),adapter.request_encoding_proof(payload),adapter.request_bytes(payload))
    if proof is None or proof!=artifact.get('owner_retry_once'):
        raise ValueError('saved retry lacks original exact owner authority')
    return proof
