"""Evidence for abandoning an unwritten CVP2 task after an interrupted call.

The provider outcome remains unknown. This proof establishes only that the
local transport ended without a saved answer; it never labels the call unmade.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.viewpoint_resolution_runtime import read_artifact


def verify(store, run_id):
    parent = store.get_cvp_group(run_id)
    if not parent or parent.get('kind') != 'cvp_management_action':
        raise ValueError('interrupted recovery requires the exact CVP2 parent')
    started = [(key, slot) for key, slot in parent['call_reservations'].items()
               if slot['state'] == 'started']
    if len(started) != 1 or any(slot['state'] not in {'started', 'returned'}
                                for slot in parent['call_reservations'].values()):
        raise ValueError('interrupted recovery requires exactly one unanswered call')
    if any(unit.get('completed_step') or unit['status'] == 'completed'
           for unit in parent['units'].values()):
        raise ValueError('interrupted recovery cannot abandon completed work')
    for unit in parent['units'].values():
        for step_id in unit['steps']:
            step = store.get_cvp_group(step_id)
            if (step.get('checkpoint') != 'prepared' or step.get('reviewed')
                    or step.get('change_set_ids')):
                raise ValueError('interrupted recovery found reviewed or written work')
    key, slot = started[0]
    root = Path(parent['execution_root']).resolve()
    cache = Path(slot['intent']['cache_path']).resolve()
    if cache != root / key or cache.exists():
        raise ValueError('interrupted recovery found an answer or foreign cache')
    stem = cache.stem
    intent_path = cache.with_name(stem + '.call-intent.json')
    failure_path = cache.with_name(stem + '.call-failure.json')
    transport = cache.with_name(stem + '.transport')
    exit_path = transport / 'exit.json'
    stdout_path = transport / 'stdout.log'
    intent = read_artifact(intent_path)
    failure = read_artifact(failure_path)
    exited = json.loads(exit_path.read_text())
    # The common runtime may select a smaller request encoding after the SQL
    # reservation, changing prompt/config SHAs but not the payload or model.
    if (intent.get('artifact_sha256') != sha256_json({k: v for k, v in intent.items()
                                                     if k != 'artifact_sha256'})
            or intent.get('schema_version') != 'wang_model_call_intent_v1'
            or intent.get('automatic_retry') is not False
            or not intent.get('request_payload_sha256')
            or any(intent.get(k) != slot['intent'].get(k) for k in
                   ('cache_path', 'model_id', 'backend', 'request_payload_sha256'))
            or failure.get('invocation_sha256') != intent['artifact_sha256']
            or failure.get('error_type') != 'KeyboardInterrupt'
            or failure.get('execution_status') != 'outcome_unknown'
            or failure.get('automatic_retry') is not False
            or exited.get('error_type') != 'KeyboardInterrupt'
            or exited.get('returncode') is not None
            or not stdout_path.exists() or stdout_path.stat().st_size != 0
            or exited.get('files', {}).get('stdout.log') != hashlib.sha256(b'').hexdigest()):
        raise ValueError('interrupted recovery lacks exact local exit evidence')
    return {'schema_version': 'wang_cvp2_abandoned_after_dispatch_v1',
            'run_id': run_id, 'call_key': key,
            'intent_sha256': slot['intent_sha256'],
            'provider_outcome': 'unknown', 'saved_response': False,
            'call_intent': binding(intent_path),
            'call_failure': binding(failure_path),
            'transport_exit': binding(exit_path),
            'transport_stdout': binding(stdout_path)}
