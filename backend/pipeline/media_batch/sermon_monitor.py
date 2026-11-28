"""Series catalog projection includes zero-task sermons; GET never scans media."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
from urllib.parse import quote
from psycopg.types.json import Jsonb
from .store import key,Conflict
from .runtime import file_sha,lock_held


def settings(root):
    path=Path(root)/'config/media_pipeline.json'
    if not path.is_file():raise ValueError('尚未設定本環境影音管線')
    cfg=json.loads(path.read_text())
    cfg.setdefault('review_root',str(root));cfg.setdefault('catalog_root',str(root))
    if Path(cfg['review_root']).resolve()!=Path(root).resolve():raise ValueError('Review destination differs from this backend')
    return cfg


def refresh(store,root,cfg):
    root=str(Path(root).resolve());catalog=Path(cfg.get('catalog_root',root))
    raw=(catalog/'config/sermon.json').read_bytes();series_raw=(catalog/'config/sermon_series.json').read_bytes()
    meta=json.loads(raw);series=json.loads(series_raw)
    groups={};items={r['item']:r for r in meta};order=0
    for s in series:
        title=s.get('title') or ''
        # Same rule as the co-worker seriesName helper; stable IDs are never changed.
        name=title if title!=s['id'] and title[:4].isdigit() else s['id']
        for n,item in enumerate(s.get('sermons',[])):
            groups.setdefault(item,[]).append({'id':s['id'],'name':name,'series_order':order,'order':n})
            items.setdefault(item,{'item':item,'title':item,'metadata_missing':True})
        order+=1
    located={}
    for directory in cfg.get('source_roots',[]):
        directory=Path(directory)
        if not directory.is_dir():continue
        for f in directory.iterdir():
            if f.is_file() and f.suffix.lower() in ('.mp4','.mp3','.wav','.m4a','.aac','.mov','.flac'):
                located.setdefault(f.stem,[]).append(str(f.resolve()))
    generation=key([raw.hex(),series_raw.hex(),located]);now=datetime.now(timezone.utc).isoformat()
    values=[]
    for item,meta in items.items():
        manual=False;rough=False;editor=False;published=False;marker=None
        for base in {catalog,Path(root)}:
            published |= (base/'script_published'/f'{item}.json').is_file()
            editor |= (base/'transcript_review'/item/'context.json').is_file()
            manual |= published or editor or (base/'script_patched'/f'{item}.json').is_file()
            rough |= (base/'script'/f'{item}.json').is_file()
            p=base/'transcript_review'/item/'pipeline-document-version.json'
            if p.exists():marker=json.loads(p.read_text())
        paths=located.get(item,[])
        value={'item':item,'title':meta.get('title') or item,'groups':groups.get(item,[]),
            'sources':[{'id':key(path),'path':path} for path in paths],
            'manual':manual,'rough':rough,'editor':editor,'published':published,'document_marker':marker,
            'metadata_missing':meta.get('metadata_missing',False),'synced_at':now}
        value['local_editor']=(Path(root)/'transcript_review'/item/'context.json').is_file()
        if value['local_editor']:
            from backend.api.sc_api.transcript_review import ReviewStore,digest
            review=ReviewStore(Path(root))
            with review.lock(item):
                rows,context=review.load(item)
                value['document_version']=digest(review.document_value(rows,context))
        values.append((root,item,Jsonb(value),generation))
    if (catalog/'config/sermon.json').read_bytes()!=raw or (catalog/'config/sermon_series.json').read_bytes()!=series_raw:
        raise Conflict('Catalog changed during synchronization')
    with store.connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('monitor:'+root,))
        for row in values:
            c.execute('INSERT INTO media_ingest.sermon_index(root,item,value,generation) VALUES(%s,%s,%s,%s) ON CONFLICT(root,item) DO UPDATE SET value=EXCLUDED.value,generation=EXCLUDED.generation,updated_at=now()',row)
        c.execute('DELETE FROM media_ingest.sermon_index WHERE root=%s AND generation<>%s',(root,generation))
        heads=list(c.execute('SELECT item FROM media_ingest.pipeline_heads WHERE root=%s',(root,)))
    for head in heads:
        reconcile_item(store,root,head['item'])
    return {'items':len(values),'generation':generation,'synced_at':now}


def state(value,head,tasks):
    if value.get('metadata_missing'):return 'needs_resolution'
    if not head:
        if value['manual']:return 'existing_transcript'
        if value['rough']:return 'rough_complete'
        return 'ready_to_plan' if len(value['sources'])==1 else 'waiting_source'
    active=[t for t in tasks if t['stage']!='existing']
    states={t['state'] for t in active}
    if 'unknown_outcome' in states:return 'unknown_outcome'
    if states & {'failed','needs_resolution'}:return 'failed'
    if any(t['state']=='running' and not t['live'] for t in active):return 'unverified'
    if any(t['live'] for t in active):return 'running'
    if active and all(t['state']=='complete' for t in active):
        return 'candidate_ready' if 'candidate' in head.get('wanted',[]) and 'editor_draft' not in head.get('wanted',[]) else 'complete'
    if head.get('desired')=='paused' or any(t['desired']=='paused' and t['state']=='pending' for t in active):return 'paused'
    if 'interrupted' in states:return 'interrupted'
    return 'pending'


def snapshot(store,root,*,search='',series='',status='actionable',source='',offset=0,limit=30,expected=None):
    root=str(Path(root).resolve())
    with store.connection() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows=list(c.execute('SELECT i.*,h.value AS head,h.revision FROM media_ingest.sermon_index i LEFT JOIN media_ingest.pipeline_heads h ON h.root=i.root AND h.item=i.item WHERE i.root=%s ORDER BY i.item',(root,)))
        if not rows:return {'initialized':False,'records':[],'series':[],'counts':{},'total':0,'filtered_total':0,'generated_at':datetime.now(timezone.utc).isoformat()}
        heads={batch for r in rows if r['head'] for batch in
               [r['head']['batch'],*(v['batch'] for v in r['head'].get('deliveries',{}).values())]}
        jobs=list(c.execute('SELECT t.*,bt.batch,b.desired FROM media_ingest.tasks t JOIN media_ingest.batch_tasks bt ON bt.task=t.id JOIN media_ingest.batches b ON b.id=bt.batch WHERE bt.batch=ANY(%s)',(list(heads),))) if heads else []
        calls=list(c.execute('SELECT task,state,count(*) AS n FROM media_ingest.calls WHERE batch=ANY(%s) GROUP BY task,state ORDER BY task,state',(list(heads),))) if heads else []
    call_counts={}
    for call in calls:
        call_counts.setdefault(call['task'],{})[call['state']]=call['n']
    grouped={}
    for t in jobs:
        payload=t['payload'];lease=Path(payload['out'])/'leases'/t['id']
        t['live']=t['state']=='running' and lock_held(lease)
        grouped.setdefault(payload['item'],[]).append(t)
    records=[];series_map={};counts=Counter();live_count=0
    for row in rows:
        value=row['value'];head=row['head'];tasks=grouped.get(row['item'],[]) if head else []
        if head:
            selected={t['id']:t for t in tasks if t['id'] in head.get('task_ids',[]) or
                      'task_ids' not in head and t['batch']==head['batch']}
            tasks=list(selected.values())
            current=next((t for t in tasks if t['batch']==head['batch']),None)
            if current:head={**head,'desired':current['desired']}
        current=state(value,head,tasks)
        marker=Path(root)/'transcript_review'/row['item']/'pipeline-document-version.json'
        current_marker=json.loads(marker.read_text()) if marker.exists() else None
        semantic_marker={k:current_marker.get(k) for k in ('script_sha256','timings_sha256')} if current_marker else None
        if head and current=='complete' and 'editor_draft' in head.get('wanted',[]):
            if 'delivered_document_marker' not in head:current='needs_sync'
            elif head['delivered_document_marker']!=semantic_marker:current='needs_resolution'
        elif head and marker.exists() and value.get('document_marker')!=current_marker:
            # Own current delivery may have committed since explicit inventory refresh.
            # Until incremental reconciliation observes that version do not claim completion.
            if current=='complete':current='needs_sync'
        counts[current]+=1
        live_count+=any(t['live'] for t in tasks)
        memberships=value['groups'] or [{'id':'__ungrouped','name':'未分組','series_order':999999,'order':0}]
        for g in memberships:
            summary=series_map.setdefault(g['id'],{**g,'total':0,'pending':0})
            summary['total']+=1;summary['pending']+=current!='complete'
        record={k:value[k] for k in ('item','title','manual','editor','published','synced_at')}
        record.update(local_editor=value.get('local_editor',False),document_version=(current_marker or {}).get('version') or value.get('document_version'))
        record.update(state=current,groups=memberships,source_count=len(value['sources']),
            asset_id=value['sources'][0]['id'] if len(value['sources'])==1 else None,
            batch=head['batch'] if head else None,head_revision=row['revision'],
            editor_href=f'/admin/surmons/{quote(row["item"],safe="")}' if value['editor'] or any(t['stage']=='editor' and t['state']=='complete' for t in tasks) else None,
            tasks=[{k:t.get(k) for k in ('id','stage','state','live','progress','error','attempt','heartbeat','batch')} for t in tasks])
        for task in record['tasks']:
            task['model_calls']=call_counts.get(task['id'],{})
        candidate=next((t['result'] for t in tasks if t['stage']=='candidate' and t['state']=='complete'),None)
        record['candidate_href']=(f'/api/sc_api/media_batches/sermons/{quote(row["item"],safe="")}/candidates/{candidate["candidate_id"]}' if candidate else None)
        if (search and search.casefold() not in (value['title']+' '+row['item']+' '+' '.join(g['name'] for g in memberships)).casefold()
            or series and series not in {g['id'] for g in memberships}
            or status=='actionable' and current=='complete'
            or source=='missing' and len(value['sources'])!=0
            or source=='single' and len(value['sources'])!=1
            or source=='multiple' and len(value['sources'])<=1
            or status not in ('all','actionable') and current!=status):continue
        records.append(record)
    revision=key([[(r['item'],r['generation'],r['revision'],r['updated_at'].isoformat()) for r in rows],
                  [(t['id'],t['state'],str(t['updated_at']),t['live']) for t in jobs],
                  [(r['item'],r['state']) for r in records],calls])
    if expected and expected!=revision:raise Conflict('snapshot_changed')
    records.sort(key=lambda r:(min(g['series_order'] for g in r['groups']),min(g['order'] for g in r['groups']),r['item']))
    return {'initialized':True,'records':records[offset:offset+limit], 'series':sorted(series_map.values(),key=lambda s:s['series_order']),
        'counts':dict(counts),'total':len(rows),'filtered_total':len(records),'live_count':live_count,
        'pending':len(rows)-counts['complete'],'snapshot_revision':revision,
        'generated_at':datetime.now(timezone.utc).isoformat(),'offset':offset,'limit':limit,
        'synced_at':min(r['value']['synced_at'] for r in rows)}


def reconcile_item(store,root,item):
    """Small per-item readback, independent of model payloads and full inventory."""
    root=Path(root);folder=root/'transcript_review'/item
    marker=folder/'pipeline-document-version.json'
    with store.connection() as c:
        row=c.execute('SELECT value FROM media_ingest.sermon_index WHERE root=%s AND item=%s FOR UPDATE',
                      (str(root.resolve()),item)).fetchone()
        if not row:return
        value=row['value']
        value.update(document_marker=json.loads(marker.read_text()) if marker.exists() else None,
            local_editor=(folder/'context.json').is_file(),
            editor=(folder/'context.json').is_file(),
            manual=value['manual'] or (folder/'context.json').is_file(),
            published=value['published'] or (root/'script_published'/f'{item}.json').is_file())
        c.execute('UPDATE media_ingest.sermon_index SET value=%s,updated_at=now() WHERE root=%s AND item=%s',
                  (Jsonb(value),str(root.resolve()),item))
        head=c.execute('SELECT value FROM media_ingest.pipeline_heads WHERE root=%s AND item=%s FOR UPDATE',
                       (str(root.resolve()),item)).fetchone()
        if head and 'delivered_document_marker' not in head['value']:
            ids=head['value'].get('task_ids')
            unfinished=(c.execute("SELECT 1 FROM media_ingest.tasks WHERE id=ANY(%s) AND state<>'complete' LIMIT 1",(ids,)).fetchone() if ids is not None else
                c.execute("SELECT 1 FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.payload->>'item'=%s AND t.state<>'complete' LIMIT 1",(head['value']['batch'],item)).fetchone())
            if not unfinished:
                editor_batch=head['value'].get('deliveries',{}).get('editor_draft',{}).get('batch',head['value']['batch'])
                editor=c.execute("SELECT t.result FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.payload->>'item'=%s AND t.stage='editor' AND t.state='complete'",
                                 (editor_batch,item)).fetchone()
                if editor and editor['result']:
                    import re
                    from backend.api.sc_api.transcript_review import digest
                    opid=editor['result']['operation_id']
                    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',opid):raise ValueError('Invalid Editor receipt identity')
                    op=json.loads((folder/'operations'/f'{opid}.json').read_text())
                    result=op['result']
                    revision=json.loads((folder/'revisions'/f"{result['revision']:08}.json").read_text())
                    if (op['state']!='committed' or result['version']!=editor['result']['version']
                            or digest(revision)!=result['revision_sha256']):raise ValueError('Editor delivery proof changed')
                    context=revision['context']
                    baseline={'script_sha256':context['script_sha256'],
                              'timings_sha256':digest(context.get('paragraph_bindings',{}))}
                    saved={**head['value'],'delivered_document_marker':baseline}
                    c.execute('UPDATE media_ingest.pipeline_heads SET value=%s,revision=revision+1,updated_at=now() WHERE root=%s AND item=%s',
                              (Jsonb(saved),str(root.resolve()),item))
