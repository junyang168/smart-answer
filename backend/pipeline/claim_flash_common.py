"""Bounded calls and source-local input loading for Claim Update Flash."""
from copy import deepcopy
import json
import math
from pathlib import Path

from backend.pipeline import evidence_refresh as e
from backend.pipeline import claim_refresh as c

VERSION = 'claim_update_flash_v1'
LOWER = {'evidence_steps', 'observations', 'questions', 'position_nodes'}
PROTECTED = {'approved', 'human_approved', 'human_hold', 'rejected', 'superseded'}
GPT_BYTES = 60000
GPT_JOBS = 4
JEV_BYTES = 40000


def bind_source(store,sid,root):
    """A completed root belongs to one source version, not just a reusable source ID."""
    import hashlib
    state=store.get_record_state('source_documents',sid)
    if not state or state['retired']:
        raise ValueError('missing source: '+sid)
    doc=state['payload'];path=doc['source_path']
    actual=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual!=doc['source_file_sha256']:
        raise ValueError('registered source file changed; run source migration first')
    pin={'source_id':sid,'path':path,'file_sha256':actual}
    e.save(Path(root)/'source-version.json',pin)
    return pin


def projected(row):
    p = row['payload']
    return {k: v for k, v in p.items() if k not in {
        'revision', 'produced_claim_ids', 'answer_claim_ids', 'review_status', 'visibility',
        'support_eligibility', 'claim_group_ids', 'maturity', 'answer_state'}}


def load_source(store, sid):
    """One typed source census, including unowned records; no corpus snapshot."""
    records = {}
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute('SET LOCAL statement_timeout=5000')
        def query(sql, args):
            cur.execute('SELECT collection,object_id,revision,content_sha256,payload FROM '
                        'wang_knowledge.objects WHERE retired_at IS NULL AND ' + sql, args)
            for col, oid, rev, sha, payload in cur.fetchall():
                records[col+'/'+oid] = dict(payload=payload, revision=rev, content_sha256=sha, retired=False)
        query("collection='source_documents' AND object_id=%s", (sid,))
        if 'source_documents/'+sid not in records:
            raise ValueError('missing source: '+sid)
        doc = records['source_documents/'+sid]['payload']
        aliases = sorted({sid, doc.get('transcript_id') or sid})
        query("collection='source_fragments' AND payload @> %s::jsonb", (json.dumps({'source_id': sid}),))
        fids = [k.split('/', 1)[1] for k in records if k.startswith('source_fragments/')]
        for offset in range(0, len(fids), 8):
            needles = [json.dumps({field: [fid] if field.endswith('ids') else fid})
                       for fid in fids[offset:offset+8] for field in ['source_fragment_id', 'source_fragment_ids']]
            query('collection=ANY(%s) AND payload @> ANY(%s::jsonb[])', (sorted(LOWER), needles))
        query("collection='claims' AND payload @> ANY(%s::jsonb[])",
              ([json.dumps({'occurrences': [{'source_id': alias}]}) for alias in aliases],))
        # Include missing-source Evidence explicitly used by source-local Claims, so it cannot disappear.
        eids = sorted({eid for k, r in records.items() if k.startswith('claims/')
                       for eid in r['payload'].get('evidence_step_ids', [])})
        query("collection='evidence_steps' AND object_id=ANY(%s)", (eids,))
        ids = sorted(k.split('/', 1)[1] for k in records if k.split('/', 1)[0] in LOWER | {'claims'})
        for field in ['from_id', 'to_id', 'source_id', 'target_id']:
            query('collection=ANY(%s) AND payload->>%s=ANY(%s)',
                  (['knowledge_relations', 'claim_relations', 'claim_relation_constraints'], field, ids))
    sources = {sid: e.source_snapshot({'path': doc['source_path'],
                    'file_sha256': doc['source_file_sha256']}, source=doc)}
    return records, sources


def value(key, records, sources):
    p = records[key]['payload']
    return e.material({**p, 'evidence_step_id': key,
        'statement': p.get('statement', p.get('text', p.get('title', '')))}, records, sources)


def jev(root, ports, state, questions):
    request = {'model': e.MODEL, 'state': state, 'questions': questions}
    reply = e.recorded_call(root, 'jev', request, ports.judge)
    if set(reply.get('answers', {})) != set(questions):
        raise ValueError('Jev question set differs')
    choices = {}
    if reply.get('model') != e.MODEL:
        raise ValueError('Jev returned a different model')
    for name, spec in questions.items():
        answer = reply['answers'][name]
        if spec['type'] == 'noul':
            probability = answer.get('noul')
            if (answer.get('type') != 'noul' or type(probability) not in {int, float}
                    or not math.isfinite(probability) or not 0 <= probability <= 1):
                raise ValueError('invalid Jev Noul probability')
            choices[name] = probability
        else:
            choices[name] = c.choice({'model': reply.get('model'), 'answers': {name: answer}},
                                    name, spec['criteria'])
    e.save(Path(root)/'jev'/e.digest(request)/'metrics.json', {
        'request_bytes': len(json.dumps(request, ensure_ascii=False).encode()),
        'questions': len(questions)})
    return choices


def yn(text):
    return c.question(text, {'yes': '有此問題。', 'no': '沒有此問題。', 'uncertain': '無法確定。'})


def gpt(root, ports, task, jobs, schema, instruction, *, shared=None, prepare=None):
    """Split BEFORE calling by both complete wire size and task count. No truncation."""
    def request(batch):
        return {'identity': ports.identity['gpt'], 'system': instruction,
                'input': {'task': task, 'jobs': batch, **(shared or {})}, 'schema': schema}
    batches, batch = [], []
    for job in jobs:
        trial = batch+[job]
        if batch and (len(trial) > GPT_JOBS or len(json.dumps(request(trial), ensure_ascii=False).encode()) > GPT_BYTES):
            batches.append(batch); batch=[]
        batch.append(job)
        if len(json.dumps(request(batch), ensure_ascii=False).encode()) > GPT_BYTES:
            raise ValueError('one complete GPT job exceeds Flash payload limit; no call made')
    if batch:
        batches.append(batch)
    # Preflight all jobs before sending any, so a late oversized job does not consume partial calls.
    while batches:
        batch=batches.pop(0)
        if prepare:
            batch=prepare(deepcopy(batch))
        req=request(batch)
        if len(json.dumps(req,ensure_ascii=False).encode())>GPT_BYTES:
            if len(batch)==1:
                raise ValueError('one complete GPT job exceeds Flash payload limit; no truncation')
            middle=len(batch)//2
            batches[:0]=[batch[:middle],batch[middle:]]
            continue
        reply=e.recorded_call(root, 'gpt', req, ports.repair, preflight=getattr(ports, 'preflight', None))
        e.save(Path(root)/'gpt'/e.digest(req)/'metrics.json', {
            'task':task, 'jobs':len(batch), 'request_bytes':len(json.dumps(req,ensure_ascii=False).encode()),
            'response_bytes':len(json.dumps(reply,ensure_ascii=False).encode())})
        yield batch,reply


def apply_overlay(records, updates, fragments=None):
    working=deepcopy(records)
    for k,p in {**updates, **(fragments or {})}.items():
        working[k]={**working.get(k, {}), 'payload':p, 'retired':False}
    return working


def source_unchanged(sources):
    import hashlib
    for source in sources.values():
        if hashlib.sha256(Path(source['file']['path']).read_bytes()).hexdigest() != source['file']['file_sha256']:
            raise ValueError('source changed during Flash')


def exactly(items, expected, field):
    if any('reason' in item and not item['reason'].strip() for item in items):
        raise ValueError('GPT disposition requires a substantive reason')
    if len(items)!=len(expected) or {x[field] for x in items}!=set(expected):
        raise ValueError('GPT must dispose every job exactly once')
