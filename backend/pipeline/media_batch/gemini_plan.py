"""One manifest planner for single/multiple Gemini recordings. No model calls."""
from pathlib import Path
import json
from psycopg.types.json import Jsonb
from .store import key, Conflict
from .runtime import file_sha, storage_guards
from .planner import probe, pcm_identity
from backend.api.sc_api.transcript_review import ReviewStore, digest, read
from backend.pipeline.gemini_review import empty_snapshot

PIPELINE='gemini-transcript-v1'
STAGES=('audio','transcribe','flash','timing','speakers','repetitions','layout','media','editor','slides','delivery')


def create(store, manifest, config, actor):
    if isinstance(manifest,(str,Path)):manifest=json.loads(Path(manifest).read_text())
    if manifest.get('schema_version')!='gemini-media-jobs.v1' or not manifest.get('jobs'):
        raise ValueError('Expected a nonempty gemini-media-jobs.v1 manifest')
    if config['budget_usd']<=0 or config['max_calls']<=0:raise ValueError('Explicit bounded budget required')
    cfg={**config,'pipeline':PIPELINE,'actor':actor,'mode':'gemini','profile':PIPELINE}
    for k in ('artifact_root','review_root','media_root','capacity_root'):
        cfg[k]=str(Path(cfg[k]).resolve())
    cfg['storage_guards']=storage_guards(cfg)
    cfg.setdefault('ssd_reserve_gib',0);cfg.setdefault('internal_reserve_gib',1);cfg.setdefault('working_set_gib',15)
    code=Path(__file__).resolve().parents[1]
    files=['transcript_gemini_audio.py','transcript_gemini_proofreading.py','transcript_punctuation.py',
           'transcript_word_timing.py','gemini_review.py','media_operations.py','media_model_calls.py',
           'slide_pilot_models.py','editor_slide_runner.py','slide_editor_alignment.py',
           'media_batch/gemini_plan.py','media_batch/gemini_stages.py','media_batch/gemini_media.py']
    cfg['implementation']=key({name:file_sha(code/name) for name in files})
    cfg['models']={'asr':'gemini-3.5-transcribe','audio_review':'gemini-3.8-flash','text':'gpt-6.1-sol'}
    review=ReviewStore(Path(cfg['review_root']));prepared=[];seen=set()
    for job in manifest['jobs']:
        item=job['item'];review.folder(item)
        if item in seen:raise ValueError('Duplicate item in manifest')
        seen.add(item)
        source=Path(job['source']).resolve(strict=True)
        before=source.stat();sha=file_sha(source)
        if sha!=job['source_sha256'] or before.st_mtime_ns!=source.stat().st_mtime_ns:
            raise ValueError('Source SHA/version mismatch')
        if source.is_relative_to(Path(cfg['artifact_root'])):raise ValueError('Source must be outside artifacts')
        pr=probe(source);video=any(s['codec_type']=='video' for s in pr['streams'])
        if video and abs(float(pr['format'].get('start_time',0)))>.001:
            raise ValueError('Nonzero video origin requires a verified mapping')
        wanted=job.get('deliverables',['editor_draft','playback','slides'])
        if not wanted or set(wanted)-{'rough','editor_draft','playback','slides'}:
            raise ValueError('Unsupported deliverables')
        if 'rough' in wanted and len(wanted)>1:raise ValueError('Rough-only cannot promise Editor delivery')
        if 'editor_draft' in wanted and 'playback' not in wanted:raise ValueError('Editor requires verified playback')
        _,meta=review.meta(item)
        with review.lock(item):
            empty=empty_snapshot(review,item,actor) if 'editor_draft' in wanted else None
            expected_version=None
            if 'slides' in wanted and 'editor_draft' not in wanted and video:
                review.require(actor,item,write=True,epoch=meta.get('assignment_epoch'))
                rows,ctx=review.load(item);expected_version=digest(review.document_value(rows,ctx))
                if job.get('expected_version')!=expected_version:raise Conflict('Expected Editor version required')
        # Separate read-only catalog protection also covers worktree preview destinations.
        catalog=Path(cfg.get('catalog_root',cfg['review_root']))
        if 'editor_draft' in wanted and catalog.resolve()!=review.root.resolve():
            for sub in ('script','script_patched','script_review','script_published'):
                value=read(catalog/sub/f'{item}.json')
                if value not in (None,[],{}):raise Conflict('Existing catalog manuscript is protected')
        v=next((s for s in pr['streams'] if s['codec_type']=='video'),{})
        info=dict(name=item,source=str(source),source_sha=sha,bytes=before.st_size,
                  duration=float(pr['format']['duration']),video=video,width=v.get('width',0),height=v.get('height',0),pcm_sha=pcm_identity(source))
        binding=key([PIPELINE,cfg['implementation'],cfg.get('speaker_name'),cfg['models'],cfg['aligner_python'],item,sha,wanted,empty,expected_version,cfg['review_root'],cfg['media_root']])
        prepared.append(dict(item=item,info=info,binding=binding,wanted=wanted,empty=empty,
                             expected_version=expected_version,recording='R-'+key([item,sha])[:24]))
    cfg['scope']=prepared
    batch=store.batch(cfg)
    with store.connection() as c:
        for entry in prepared:
            info=entry['info'];rid=entry['recording']
            prior=c.execute('SELECT * FROM media_ingest.recordings WHERE item=%s FOR UPDATE',(entry['item'],)).fetchone()
            if prior:
                if prior['source_sha']!=info['source_sha']:raise Conflict('Existing recording source differs; explicit source reconciliation required')
                rid=prior['id']
            else:
                c.execute('INSERT INTO media_ingest.recordings(id,item,source_sha,info) VALUES(%s,%s,%s,%s)',
                          (rid,entry['item'],info['source_sha'],Jsonb(info)))
            c.execute('INSERT INTO media_ingest.assets(sha,recording,info) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                      (info['source_sha'],rid,Jsonb(info)))
            out=Path(cfg['artifact_root'])/rid/entry['binding'][:24]
            payload=dict(recording=rid,item=entry['item'],info=info,out=str(out),config=cfg,
                         wanted=entry['wanted'],empty=entry['empty'],expected_version=entry['expected_version'])
            ids={}
            if 'editor_draft' in entry['wanted'] or 'rough' in entry['wanted']:
                phases=STAGES[:2] if 'rough' in entry['wanted'] else STAGES[:7]
                for stage in phases:
                    deps=[] if stage=='audio' else [ids[phases[phases.index(stage)-1]]]
                    ids[stage]=store.task(c,batch,rid,stage,'all',key([entry['binding'],stage]),payload,deps)
            if 'playback' in entry['wanted']:
                ids['media']=store.task(c,batch,rid,'media','all',key([entry['binding'],'media']),payload)
            if 'editor_draft' in entry['wanted']:
                ids['editor']=store.task(c,batch,rid,'editor','all',key([entry['binding'],'editor']),payload,[ids['layout'],ids['media']])
            if 'slides' in entry['wanted']:
                ids['slides']=store.task(c,batch,rid,'slides','all',key([entry['binding'],'slides']),payload,[ids['editor']] if 'editor' in ids else [])
            store.task(c,batch,rid,'delivery','all',key([entry['binding'],'delivery']),payload,list(ids.values()))
    return batch


def accept(store,batch):
    """Activating the planned work is separate from read-only plan creation."""
    with store.connection() as c:
        row=c.execute('SELECT config FROM media_ingest.batches WHERE id=%s',(batch,)).fetchone()
        if not row:raise KeyError(batch)
        cfg=row['config']
        if cfg.get('pipeline')!=PIPELINE:return
        for e in cfg['scope']:
            identity=(cfg['review_root'],e['item'])
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key(identity),))
            prior=c.execute('SELECT value FROM media_ingest.pipeline_heads WHERE root=%s AND item=%s FOR UPDATE',identity).fetchone()
            if prior and prior['value'].get('batch')==batch:continue
            if prior:
                # Do not steal another accepted plan, even if this batch is newer.
                unfinished=c.execute("SELECT 1 FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.state<>'complete' LIMIT 1",(prior['value']['batch'],)).fetchone()
                if unfinished:raise Conflict('Another current plan still has unmet requirements')
                if prior['value']['source_sha256']!=e['info']['source_sha']:raise Conflict('Source switch requires reconciliation')
            value={'batch':batch,'pipeline':PIPELINE,'source_sha256':e['info']['source_sha'],
                   'wanted':e['wanted'],'recording':e['recording']}
            c.execute('INSERT INTO media_ingest.pipeline_heads(root,item,value) VALUES(%s,%s,%s) ON CONFLICT(root,item) DO UPDATE SET value=EXCLUDED.value,revision=media_ingest.pipeline_heads.revision+1,updated_at=now()',(*identity,Jsonb(value)))
