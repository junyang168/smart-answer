"""Bounded explicit code-defect recovery, preserving the original plan and budget.

This is an engineering receipt, never a model verdict or permission to retry an
unknown dispatch. An operator must bind a concrete defect and regression result.
"""
import json
import os
from pathlib import Path
from .runtime import file_sha, lock_held
from .store import key, Conflict


def receipts(out):
    return [p for p in [out/'engineering-recovery.json',
                       out/'engineering-recovery-2.json',out/'engineering-recovery-3.json'] if p.exists()]


def verify(payload, current):
    paths = receipts(Path(payload['out']))
    if not paths:
        raise ValueError('Pipeline implementation changed after planning; saved results preserved')
    receipt = json.loads(paths[-1].read_text())
    if (receipt['original_implementation'] != payload['config']['implementation']
            or receipt['corrected_implementation'] != current
            or receipt['payload_sha256'] != key(payload)):
        raise ValueError('Engineering recovery does not bind this exact plan and code')
    for name, sha in receipt['proof'].items():
        if file_sha(Path(payload['out']) / name) != sha:
            raise ValueError('Engineering recovery saved proof changed')
    if receipt.get('prior_receipts') != {p.name:file_sha(p) for p in paths[:-1]}:
        if paths[:-1] or receipt.get('prior_receipts'):
            raise ValueError('Engineering recovery history changed')


def reserve(store, batch, task_id, *, defect, validation, patch):
    """Record once; does not change task states, calls, config or resource caps."""
    if not defect.strip() or not validation.strip() or not patch.strip():
        raise ValueError('Concrete defect, validation and exact program delta required')
    from .gemini_plan import implementation
    with store.connection() as c:
        c.execute('SELECT id FROM media_ingest.batches WHERE id=%s FOR UPDATE', (batch,)).fetchone()
        task = c.execute('SELECT t.* FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.id=%s', (batch, task_id)).fetchone()
        if not task or task['state'] != 'failed':
            raise Conflict('A saved failed task is required')
        if c.execute("SELECT 1 FROM media_ingest.calls WHERE batch=%s AND state<>'complete' LIMIT 1", (batch,)).fetchone():
            raise Conflict('Unresolved calls prevent engineering recovery')
        payload = task['payload']; out = Path(payload['out'])
        if lock_held(out / 'leases' / task_id):
            raise Conflict('Task worker is still live')
        # Bind the original saved request/answer tree, including empty replies.
        files = sorted(set(out.rglob('*.request.json')) | set(out.rglob('*.response.json')) |
                       set(out.glob('calls/*/request.json')) | set(out.glob('calls/*/response.json')) |
                       {p for p in (out/'run.json',out/'transcribe.json',out/'flash.json') if p.exists()})
        if not files:
            raise Conflict('Saved source requests and answers required')
        previous=receipts(out)
        if len(previous)>=3:raise Conflict('Bounded engineering recovery reservations exhausted')
        if previous and json.loads(previous[-1].read_text())['corrected_implementation']==implementation():
            raise Conflict('This corrected implementation already has a recovery reservation')
        receipt = dict(schema_version='media-engineering-recovery.v1', batch=batch,
                       failed_task=task_id, original_error=task['error'], defect=defect,
                       validation=validation, program_delta=patch,
                       original_implementation=payload['config']['implementation'],
                       corrected_implementation=implementation(), payload_sha256=key(payload),
                       prior_receipts={p.name:file_sha(p) for p in previous},
                       proof={str(p.relative_to(out)):file_sha(p) for p in files})
        path = out / ('engineering-recovery.json' if not previous else f'engineering-recovery-{len(previous)+1}.json')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(receipt, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        return receipt
