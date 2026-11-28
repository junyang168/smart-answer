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
        values.append((root,item,Jsonb(value),generation))
    if (catalog/'config/sermon.json').read_bytes()!=raw or (catalog/'config/sermon_series.json').read_bytes()!=series_raw:
        raise Conflict('Catalog changed during synchronization')
    with store.connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('monitor:'+root,))
        for row in values:
            c.execute('INSERT INTO media_ingest.sermon_index(root,item,value,generation) VALUES(%s,%s,%s,%s) ON CONFLICT(root,item) DO UPDATE SET value=EXCLUDED.value,generation=EXCLUDED.generation,updated_at=now()',row)
        c.execute('DELETE FROM media_ingest.sermon_index WHERE root=%s AND generation<>%s',(root,generation))
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
    if active and all(t['state']=='complete' for t in active):return 'complete'
    if head.get('desired')=='paused':return 'paused'
    if 'interrupted' in states:return 'interrupted'
    return 'pending'


def snapshot(store,root,*,search='',series='',status='actionable',offset=0,limit=30,expected=None):
    root=str(Path(root).resolve())
    with store.connection() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows=list(c.execute('SELECT i.*,h.value AS head,h.revision FROM media_ingest.sermon_index i LEFT JOIN media_ingest.pipeline_heads h ON h.root=i.root AND h.item=i.item WHERE i.root=%s ORDER BY i.item',(root,)))
        if not rows:return {'initialized':False,'records':[],'series':[],'counts':{},'total':0,'filtered_total':0,'generated_at':datetime.now(timezone.utc).isoformat()}
        heads={r['head']['batch'] for r in rows if r['head']}
        jobs=list(c.execute('SELECT t.*,bt.batch,b.desired FROM media_ingest.tasks t JOIN media_ingest.batch_tasks bt ON bt.task=t.id JOIN media_ingest.batches b ON b.id=bt.batch WHERE bt.batch=ANY(%s)',(list(heads),))) if heads else []
    grouped={}
    for t in jobs:
        payload=t['payload'];lease=Path(payload['out'])/'leases'/t['id']
        t['live']=t['state']=='running' and lock_held(lease)
        grouped.setdefault((t['batch'],payload['item']),[]).append(t)
    records=[];series_map={};counts=Counter();live_count=0
    for row in rows:
        value=row['value'];head=row['head'];tasks=grouped.get((head['batch'],row['item']),[]) if head else []
        if head and tasks:head={**head,'desired':tasks[0]['desired']}
        current=state(value,head,tasks)
        marker=Path(root)/'transcript_review'/row['item']/'pipeline-document-version.json'
        if head and marker.exists() and value.get('document_marker')!=json.loads(marker.read_text()):
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
        record.update(state=current,groups=memberships,source_count=len(value['sources']),
            asset_id=value['sources'][0]['id'] if len(value['sources'])==1 else None,
            batch=head['batch'] if head else None,head_revision=row['revision'],
            editor_href=f'/admin/surmons/{quote(row["item"],safe="")}' if value['editor'] or any(t['stage']=='editor' and t['state']=='complete' for t in tasks) else None,
            tasks=[{k:t.get(k) for k in ('id','stage','state','live','progress','error','attempt','heartbeat')} for t in tasks])
        if (search and search.casefold() not in (value['title']+' '+row['item']+' '+' '.join(g['name'] for g in memberships)).casefold()
            or series and series not in {g['id'] for g in memberships}
            or status=='actionable' and current=='complete'
            or status not in ('all','actionable') and current!=status):continue
        records.append(record)
    revision=key([[(r['item'],r['generation'],r['revision'],r['updated_at'].isoformat()) for r in rows],
                  [(t['id'],t['state'],str(t['updated_at'])) for t in jobs]])
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
            editor=(folder/'context.json').is_file(),
            manual=value['manual'] or (folder/'context.json').is_file(),
            published=value['published'] or (root/'script_published'/f'{item}.json').is_file())
        c.execute('UPDATE media_ingest.sermon_index SET value=%s,updated_at=now() WHERE root=%s AND item=%s',
                  (Jsonb(value),str(root.resolve()),item))
