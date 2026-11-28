"""One explicit code-defect recovery, preserving the original plan and budget.

This is an engineering receipt, never a model verdict or permission to retry an
unknown dispatch. An operator must bind a concrete defect and regression result.
"""
import json
import os
from pathlib import Path
from .runtime import file_sha, lock_held
from .store import key, Conflict


def verify(payload, current):
    path = Path(payload['out']) / 'engineering-recovery.json'
    if not path.exists():
        raise ValueError('Pipeline implementation changed after planning; saved results preserved')
    receipt = json.loads(path.read_text())
    if (receipt['original_implementation'] != payload['config']['implementation']
            or receipt['corrected_implementation'] != current
            or receipt['payload_sha256'] != key(payload)):
        raise ValueError('Engineering recovery does not bind this exact plan and code')
    for name, sha in receipt['proof'].items():
        if file_sha(Path(payload['out']) / name) != sha:
            raise ValueError('Engineering recovery saved proof changed')


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
        files = sorted(set(out.glob('asr-*.request.json')) | set(out.glob('asr-*.response.json')))
        if not files:
            raise Conflict('Saved source requests and answers required')
        receipt = dict(schema_version='media-engineering-recovery.v1', batch=batch,
                       failed_task=task_id, original_error=task['error'], defect=defect,
                       validation=validation, program_delta=patch,
                       original_implementation=payload['config']['implementation'],
                       corrected_implementation=implementation(), payload_sha256=key(payload),
                       proof={str(p.relative_to(out)):file_sha(p) for p in files})
        # One reservation per recording workspace: no chain of code retries.
        path = out / 'engineering-recovery.json'
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(receipt, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        return receipt
