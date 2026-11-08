"""Bounded calls and source-local input loading for Claim Update Flash."""
from copy import deepcopy
import json
import math
import hashlib
from pathlib import Path

from backend.pipeline import evidence_refresh as e
from backend.pipeline import claim_refresh as c
from backend.pipeline import claim_flash_questions as jevq
from backend.api.canonical_repository.source_claim_membership import belongs_to_source, load_source_claims

VERSION = 'claim_update_flash_v1'
LOWER = {'evidence_steps', 'observations', 'questions', 'position_nodes'}
PROTECTED = {'approved', 'human_approved', 'human_hold', 'rejected', 'superseded'}
GPT_BYTES = 60000
GPT_JOBS = 4
JEV_BYTES = 40000
JEV_CONTRACT = jevq.CONTRACT
MATERIAL_CONTRACT = 'claim_flash_same_paragraph_spans_v1'
REVIEW_CONTRACT = 'claim_flash_semantic_review_v3'
SEMANTIC_REVIEW = (
    '只審本次 task 指定的本層目標；其他層的材料僅作依據或上下文，不審閱下一層內容，'
    '不替參考物件作完整審閱，不因發現一句判斷便要求建立下一層記錄。'
    '已完成的下層作為依據，不重新全面審改；確實發現下層缺陷時記清對象並退回該層，'
    '不在本層順手改寫，也不把下層新結論偷偷補成目標已表達的內容。'
    'Jev 是待核對的線索，不是通過或錯誤的證明；復查目標完整意思，不限於提示的類別。'
    '逐項核對實際涉及的對象與範圍、必要／充分條件、程度與重心、否定與排他性、'
    '因果、可能／應當／必然、說話者與採納歸屬，並查自身原文及必要上下文的限制或反證。'
    '判準是讀者據摘要能支持或反駁的，是否仍是教授斷言的同一個判斷；'
    '改弱與改強都可能改變意思。合理同義概括、口語省略及語序變化不要求逐字一致，'
    '不為無關維度造表，不憑外部神學或想像邊緣情形挑錯。'
    '每個實質成分應有本層實際關聯材料共同支持；單項只支持一部分可以，'
    '但不能以大意相近或多數部分正確掩蓋某個無據成分。'
    '只在本次授權層內核對同一判斷的相關表述；關係類型成立不代表理由的每項斷言、'
    '支持方向或來源歸屬都成立。保留教授實際使用的聖經證據、推理、限定與反方角色。'
    'stance 標記須與 statement 這個判斷一致，不可把反方判斷的 opposed 標到教授採納的否定句。'
    '經文引用的規範化章節定位不等於教授逐字念出章節；上下文清楚對應的既有經卷、節次，'
    '不因局部片段未重述就刪除。無法核實時先明示所缺上下文，不能以刪去已有論據代替查證。'
    '沿用現有 reason 欄位，簡短指出實際影響判斷的限定和來源短句或位置；'
    'keep 也須解釋為何現有表述保留了該意思，不能只說整體有支持。'
    '仍可有據保留，不能為符合另一模型的意見而強制修改；修訂只輸出必要差異。')


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


def resume_committed(store,root,result):
    """Reuse a saved SQL receipt, allowing only later groups' bookkeeping writes."""
    root=Path(root)
    if not (root/'commit.json').exists():return None
    receipt=json.loads((root/'commit.json').read_text())
    intent=json.loads((root/'commit-intent.json').read_text())
    history=store.get_change_history(receipt['change_set_id'])
    if (not history or history['status']!='applied' or history['source_kind']!=result['version']
            or history['fingerprint_sha256']!=intent['fingerprint_sha256']
            or history['change_set_id']!=intent['change_set_id']):
        raise ValueError('saved Flash SQL receipt differs')
    # Lower fidelity deliberately excludes owner/answer bookkeeping. A later
    # group may update these fields, but an unrelated writer must not be hidden.
    allowed={'evidence_steps':{'produced_claim_ids'},'questions':{'answer_claim_ids','answer_state'}}
    readback={**result,'updates':deepcopy(result['updates'])}
    for key,expected in result['updates'].items():
        col,oid=key.split('/',1);state=store.get_record_state(col,oid)
        if not state or state['retired']:raise ValueError('committed Flash object missing: '+key)
        different={k for k,v in expected.items() if k!='revision' and state['payload'].get(k)!=v}
        if not different:continue
        if not different<=allowed.get(col,set()):raise ValueError('committed Flash content changed: '+key)
        with store.connect() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            row=conn.execute('''SELECT cs.source_kind,cs.status,cs.metadata
                FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets cs USING(change_set_id)
                WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s''',(col,oid,state['revision'])).fetchone()
        path=Path((row[2] or {}).get('verification_path','')) if row else Path()
        parent=path.parent
        later=(parent.name.startswith('group-') and parent.name[6:].isdigit()
               and parent.parent.resolve()==root.parent.resolve()
               and (root.name=='lower' or root.name.startswith('group-') and root.name[6:].isdigit()
                    and int(parent.name[6:])>int(root.name[6:])))
        if not row or row[:2]!=(c.VERSION,'applied') or path.name!='verification.json' or not later:
            raise ValueError('committed Flash bookkeeping changed outside later campaign group: '+key)
        for field in different:readback['updates'][key][field]=state['payload'].get(field)
    e.readback(store,readback)
    return e.publish(root,{**result,'status':'applied','applied':True,'commit':receipt})


def load_source(store, sid, *, visual_context=False):
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
        query("collection='source_fragments' AND payload @> %s::jsonb", (json.dumps({'source_id': sid}),))
        fids = [k.split('/', 1)[1] for k in records if k.startswith('source_fragments/')]
        for offset in range(0, len(fids), 8):
            needles = [json.dumps({field: [fid] if field.endswith('ids') else fid})
                       for fid in fids[offset:offset+8] for field in ['source_fragment_id', 'source_fragment_ids']]
            query('collection=ANY(%s) AND payload @> ANY(%s::jsonb[])', (sorted(LOWER), needles))
        records.update(load_source_claims(cur,sid,records))
        # Include missing-source Evidence explicitly used by source-local Claims, so it cannot disappear.
        eids = sorted({eid for k, r in records.items() if k.startswith('claims/')
                       for eid in r['payload'].get('evidence_step_ids', [])})
        query("collection='evidence_steps' AND object_id=ANY(%s)", (eids,))
        ids = sorted(k.split('/', 1)[1] for k in records if k.split('/', 1)[0] in LOWER | {'claims'})
        for field in ['from_id', 'to_id', 'source_id', 'target_id']:
            query('collection=ANY(%s) AND payload->>%s=ANY(%s)',
                  (['knowledge_relations', 'claim_relations', 'claim_relation_constraints'], field, ids))
    sources = {sid: e.source_snapshot({'path': doc['source_path'],
                    'file_sha256': doc['source_file_sha256']}, source=doc, visual_context=visual_context)}
    return records, sources


def value(key, records, sources):
    p = records[key]['payload']
    result = e.material({**p, 'evidence_step_id': key,
        'statement': p.get('statement', p.get('text', p.get('title', '')))}, records, sources)
    if key.split('/')[0] not in {'evidence_steps','observations'}:
        return result
    # Physical binding validation above must succeed before assembling any text.
    # The original fragments remain highlights; no database object is changed.
    groups={}
    for fragment in result['fragments']:
        groups.setdefault(fragment['paragraph_key'],[]).append(fragment)
    sid=records['source_fragments/'+result['fragments'][0]['fragment_id']]['payload']['source_id']
    excerpts=[];assembly=[]
    for pk,paragraph in sources[sid]['paragraphs'].items():
        fragments=groups.get(pk)
        if not fragments:continue
        text=paragraph['text']
        if len(fragments)<2 or any(text.count(x['quote'])!=1 for x in fragments):
            excerpts.extend(x['quote'] for x in fragments)
            continue
        start=min(text.index(x['quote']) for x in fragments)
        end=max(text.index(x['quote'])+len(x['quote']) for x in fragments)
        span=text[start:end]
        excerpts.append(span)
        assembly.append({'source_id':sid,'source_sha256':sources[sid]['body_sha256'],'paragraph_key':pk,
                         'fragment_ids':[x['fragment_id'] for x in fragments],'start':start,'end':end,
                         'span_sha256':hashlib.sha256(span.encode()).hexdigest()})
    result.update(original_text_excerpts=excerpts,source_assembly=assembly)
    return result


def jev(root, ports, state, questions):
    request = {'model': e.MODEL, 'state': state, 'questions': questions}
    reply = e.recorded_call(root, 'jev', request, ports.judge,
                            preflight=getattr(ports, 'judge_preflight', None))
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


def gpt(root, ports, task, jobs, schema, instruction, *, shared=None, prepare=None, semantic_review=True):
    """Split BEFORE calling by both complete wire size and task count. No truncation."""
    def request(batch):
        return {'identity': ports.identity['gpt'],
                'system': instruction+('\n'+SEMANTIC_REVIEW if semantic_review else ''),
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
        # A schema-valid reply may omit jobs. Keep every returned adjudication;
        # request only the missing jobs once, under the same provider call budget.
        # Unknown/duplicate IDs are not silently assigned or adjudicated by code.
        field='claim_id' if task in {'claim_statement','candidate_statement'} else 'job_id'
        items=reply.get('items')
        ids=[item.get(field) for item in items] if isinstance(items,list) and all(isinstance(x,dict) for x in items) else None
        expected=[job['job_id'] for job in batch]
        if (schema.get('properties',{}).get('items',{}).get('type')=='array'
                and ids is not None and len(set(ids))==len(ids) and set(ids)<set(expected)):
            missing=[job for job in batch if job['job_id'] not in ids]
            correction=request(missing)
            correction['system']+='\n前次漏答；本次只處置這些未答工作，每個 ID 恰好一次，不重做已答項。'
            correction['schema']=deepcopy(schema)
            array=correction['schema'].get('properties',{}).get('items',{})
            array.update(minItems=len(missing),maxItems=len(missing))
            definition=array.get('items',{})
            if '$ref' in definition:
                definition=correction['schema']['$defs'][definition['$ref'].rsplit('/',1)[1]]
            if field in definition.get('properties',{}):
                definition['properties'][field]['enum']=[job['job_id'] for job in missing]
            if len(json.dumps(correction,ensure_ascii=False).encode())>GPT_BYTES:
                raise ValueError('complete missing-job correction exceeds Flash payload limit; no call made')
            recovery=Path(root)/'output-contract'/e.digest(req)
            e.save(recovery/'recovery.json',{'original_request_sha256':e.digest(req),
                'original_answer_sha256':e.digest(reply),'retained_ids':ids,
                'missing_ids':[j['job_id'] for j in missing],'max_additional_calls':1})
            extra=e.recorded_call(recovery,'gpt',correction,ports.repair,preflight=getattr(ports,'preflight',None))
            exactly(extra.get('items',[]),[j['job_id'] for j in missing],field)
            byid={item[field]:item for item in items+extra['items']}
            reply={**reply,'items':[byid[job['job_id']] for job in batch]}
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
