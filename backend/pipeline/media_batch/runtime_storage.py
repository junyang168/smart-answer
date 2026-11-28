"""Audited reattachment of the same source paths after an SSD remount.

The original configuration, inference decisions and budgets stay immutable.
"""
import json,os,plistlib,subprocess
from pathlib import Path
from psycopg.types.json import Jsonb
from .store import Store,Conflict,key


def effective_guards(config):
    guards=config.get('storage_guards',[])
    if config.get('pipeline')!='gemini-transcript-v1' or not os.getenv('MEDIA_BATCH_DATABASE_URL'):return guards
    batch='B-'+key(config)[:24]
    with Store().connection() as c:
        row=c.execute("SELECT request FROM media_ingest.controls WHERE batch=%s AND request->>'action'='reattach-storage' ORDER BY revision DESC LIMIT 1",(batch,)).fetchone()
    if not row:return guards
    receipt=row['request']
    if receipt['original_configuration_sha256']!=key(config):raise Conflict('Storage reattachment config binding changed')
    return receipt['guards']


def volume_identity(path):
    result=plistlib.loads(subprocess.check_output(['diskutil','info','-plist',str(path)]))
    return {'volume_uuid':result['VolumeUUID'],'mount_point':result['MountPoint']}


def reattach(store,batch,operation,revision,reason):
    from .runtime import file_sha,lock_held,storage_guards
    if not reason.strip():raise ValueError('Storage fault and source verification reason required')
    intent={'revision':revision,'reason':reason}
    with store.connection() as c:
        header=c.execute('SELECT * FROM media_ingest.batches WHERE id=%s FOR UPDATE',(batch,)).fetchone()
        if not header:raise KeyError(batch)
        previous=c.execute('SELECT request FROM media_ingest.controls WHERE batch=%s AND operation=%s',(batch,operation)).fetchone()
        if previous:
            if previous['request'].get('input')!=intent:raise Conflict('Storage operation reused with different input')
            return previous['request']
        cfg=header['config']
        if header['desired']!='paused' or header['revision']!=revision:raise Conflict('Pause the exact batch revision before storage reattachment')
        tasks=c.execute('SELECT t.id,t.payload,t.state FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s',(batch,)).fetchall()
        if any(t['state']=='running' or lock_held(Path(t['payload']['out'])/'leases'/t['id']) for t in tasks):
            raise Conflict('A worker still owns a source lease')
        guards=storage_guards(cfg);old=cfg['storage_guards']
        if [(g['path'],g['mount']) for g in guards]!=[(g['path'],g['mount']) for g in old]:
            raise Conflict('Storage roots changed; this is not a reattachment')
        volumes={g['path']:volume_identity(g['path']) for g in guards if g['mount']}
        earlier=c.execute("SELECT request FROM media_ingest.controls WHERE batch=%s AND request->>'action'='reattach-storage' ORDER BY revision DESC LIMIT 1",(batch,)).fetchone()
        if earlier and earlier['request']['volumes']!=volumes:raise Conflict('Previously bound storage UUID changed')
        sources=[]
        for entry in cfg['scope']:
            info=entry['info'];source=Path(info['source'])
            if file_sha(source)!=info['source_sha']:raise Conflict('Source SHA changed during remount')
            sources.append({'item':entry['item'],'path':str(source),'sha256':info['source_sha']})
        result={'schema_version':'media-storage-reattachment.v1','action':'reattach-storage','input':intent,
                'original_configuration_sha256':key(cfg),'previous_guards':old,'guards':guards,
                'volumes':volumes,'sources':sources,'model_calls':0}
        c.execute('INSERT INTO media_ingest.controls VALUES(%s,%s,%s,%s)',(batch,operation,Jsonb(result),revision+1))
        c.execute('UPDATE media_ingest.batches SET revision=revision+1 WHERE id=%s',(batch,))
        return result
