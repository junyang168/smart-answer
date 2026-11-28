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


def uncovered_sql(alias='m'):
    """A parent stays unknown; only its verified successful replacement covers it."""
    return f"""({alias}.state='unknown' AND NOT EXISTS (
      SELECT 1 FROM media_ingest.calls replacement
      WHERE replacement.id={alias}.info->'coverage_proof'->>'replacement_call'
      AND replacement.batch={alias}.batch AND replacement.task={alias}.task
      AND replacement.state='complete'
      AND replacement.info->>'response_sha256'={alias}.info->'coverage_proof'->>'response_sha256'))"""


def verify_coverage(store, call, payload):
    from .store import key
    proof=call.get('info',{}).get('coverage_proof')
    if call['state']!='unknown' or not proof:raise ValueError('Uncovered unknown call')
    out=Path(proof['out'])
    if not out.resolve().is_relative_to(Path(payload['out']).resolve()):raise ValueError('Recovery left the original task root')
    request=json.loads((out/'calls'/proof['original_request_sha256']/'request.json').read_text())
    if key([call['task'],str(out.resolve()),request])!=call['id']:raise ValueError('Recovery parent call binding changed')
    retry=effective(out,request);target=out/'calls'/object_sha(retry)
    state=json.loads((target/'state.json').read_text())
    if (state.get('status')!='complete' or file_sha(target/'response.json')!=proof['response_sha256']
            or state.get('response_sha256')!=proof['response_sha256']
            or json.loads((target/'request.json').read_text())!=retry):raise ValueError('Replacement response proof drift')
    child=key([call['task'],str(out.resolve()),retry])
    with store.connection() as c:row=c.execute('SELECT * FROM media_ingest.calls WHERE id=%s',(child,)).fetchone()
    if (child!=proof['replacement_call'] or not row or row['state']!='complete'
            or row['batch']!=call['batch'] or row['task']!=call['task']
            or row['info'].get('response_sha256')!=proof['response_sha256']):raise ValueError('Replacement SQL proof drift')
    return proof


def reconcile(store,batch,task_id,out,request,operation,revision):
    from .runtime import lock_held
    from .store import key,Conflict
    from psycopg.types.json import Jsonb
    out=Path(out);parent=key([task_id,str(out.resolve()),request]);retry=effective(out,request)
    if retry==request:raise Conflict('Owner-authorized one-attempt recovery required')
    target=out/'calls'/object_sha(retry)
    proof={'out':str(out.resolve()),'original_request_sha256':object_sha(request),
           'replacement_call':key([task_id,str(out.resolve()),retry]),
           'response_sha256':file_sha(target/'response.json'),'original_unknown_preserved':True}
    with store.connection() as c:
        header=c.execute('SELECT * FROM media_ingest.batches WHERE id=%s FOR UPDATE',(batch,)).fetchone()
        old=c.execute('SELECT request FROM media_ingest.controls WHERE batch=%s AND operation=%s',(batch,operation)).fetchone()
        if old:
            if old['request'].get('proof')!=proof:raise Conflict('Recovery operation changed')
            return old['request']
        if not header or header['desired']!='paused' or header['revision']!=revision:raise Conflict('Pause the exact batch revision first')
        task=c.execute('SELECT * FROM media_ingest.tasks WHERE id=%s',(task_id,)).fetchone()
        call=c.execute('SELECT * FROM media_ingest.calls WHERE id=%s AND batch=%s',(parent,batch)).fetchone()
        if not task or not call or lock_held(Path(task['payload']['out'])/'leases'/task_id):raise Conflict('Original worker or call unavailable')
        augmented={**call,'info':{**call['info'],'coverage_proof':proof}}
        verify_coverage(store,augmented,task['payload'])
        result={'action':'reconcile-authorized-timeout','proof':proof,'parent_call':parent,
                'original_task_state':task['state'],'original_task_error':task['error'],'model_calls':0}
        c.execute('UPDATE media_ingest.calls SET info=%s WHERE id=%s',(Jsonb(augmented['info']),parent))
        c.execute("UPDATE media_ingest.tasks SET state='failed',error='Unknown request covered by owner-authorized replacement; awaiting code-bound resume' WHERE id=%s",(task_id,))
        c.execute('INSERT INTO media_ingest.controls VALUES(%s,%s,%s,%s)',(batch,operation,Jsonb(result),revision+1))
        c.execute('UPDATE media_ingest.batches SET revision=revision+1 WHERE id=%s',(batch,))
        return result
