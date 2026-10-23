"""Prove a CVP2 Codex call failed before any provider process was launched."""
from __future__ import annotations

from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.viewpoint_resolution_runtime import read_artifact


def verify(store, run_id):
    parent=store.get_cvp_group(run_id)
    if not parent or parent.get('kind')!='cvp_management_action':
        raise ValueError('nonlaunch recovery requires the exact CVP2 parent')
    started=[(key,slot) for key,slot in parent['call_reservations'].items() if slot['state']=='started']
    if len(started)!=1 or any(slot['state'] not in {'started','returned'}
                              for slot in parent['call_reservations'].values()):
        raise ValueError('nonlaunch recovery requires exactly one uncertain call')
    key,slot=started[0]
    cache=Path(slot['intent']['cache_path']).resolve()
    root=Path(parent['execution_root']).resolve()
    if (cache!=root/key or cache.exists() or cache.with_name(cache.stem+'.transport').exists()
            or cache.with_name(cache.stem+'.owner-retry-once.transport').exists()
            or any(unit['steps'] for unit in parent['units'].values())):
        raise ValueError('nonlaunch recovery found an answer, transport, or prepared step')
    intent_path=cache.with_name(cache.stem+'.call-intent.json')
    failure_path=cache.with_name(cache.stem+'.call-failure.json')
    intent=read_artifact(intent_path);failure=read_artifact(failure_path)
    expected={'schema_version':'wang_model_call_intent_v1',
              **{k:v for k,v in slot['intent'].items() if k not in {'cache_path','request_bytes'}},
              'cache_path':str(cache),'automatic_retry':False}
    if (intent!={**expected,'artifact_sha256':sha256_json(expected)}
            or failure.get('schema_version')!='wang_model_call_failure_v1'
            or failure.get('invocation_sha256')!=intent['artifact_sha256']
            or failure.get('error_type')!='CodexSubscriptionError'
            or failure.get('execution_status')!='outcome_unknown'
            or failure.get('automatic_retry') is not False):
        raise ValueError('nonlaunch recovery call receipt differs')
    group=next(iter(parent['units'].values())) if len(parent['units'])==1 else None
    detail=group.get('detail') if group else None
    prefix='Codex executable unavailable: '
    if (not isinstance(detail,str) or not detail.startswith(prefix)
            or slot['intent']['backend']!='codex_subscription'
            or Path(detail[len(prefix):]).exists()):
        raise ValueError('nonlaunch recovery lacks a verified missing CLI executable')
    # CodexSubscriptionClient.verify_cli_identity raises this exact error before
    # login status or model transport. Preserve the original unknown receipt.
    return {'schema_version':'wang_cvp2_pre_dispatch_nonlaunch_v1','run_id':run_id,
            'call_key':key,'intent_sha256':slot['intent_sha256'],
            'call_intent':binding(intent_path),'call_failure':binding(failure_path),
            'missing_executable':detail[len(prefix):]}
