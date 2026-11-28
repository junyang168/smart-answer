"""Dedicated SQL and real FFmpeg; no model calls."""
import json
import os
import subprocess
import uuid
from pathlib import Path
import pytest
from backend.pipeline.media_batch.store import Store, Conflict
from backend.pipeline.media_batch.gemini_plan import create,accept
from backend.pipeline.media_batch.gemini_stages import execute
from backend.pipeline.media_batch.runtime import file_sha,atomic
from backend.pipeline.slide_change_pilot import object_sha
from backend.api.sc_api.transcript_review import ReviewStore,digest,ReviewError,atomic as review_save
from backend.pipeline.gemini_review import initialize,empty_snapshot

@pytest.fixture
def setup(tmp_path):
    dsn=os.getenv('MEDIA_BATCH_TEST_DATABASE_URL')
    if not dsn:pytest.skip('Dedicated MEDIA_BATCH_TEST_DATABASE_URL required')
    store=Store(dsn);store.install()
    with store.connection() as c:
        c.execute('TRUNCATE media_ingest.pipeline_heads,media_ingest.sermon_index,media_ingest.batch_files,media_ingest.disk_reservations,media_ingest.controls,media_ingest.calls,media_ingest.batch_tasks,media_ingest.tasks,media_ingest.assets,media_ingest.recordings,media_ingest.batches')
    root=tmp_path/'review';(root/'config').mkdir(parents=True)
    item='lecture-'+uuid.uuid4().hex[:12];actor='editor@example.com'
    atomic(root/'config/sermon.json',[{'item':item,'title':'講道','status':'processing'}])
    atomic(root/'config/config.json',{'users':[{'id':actor}], 'user_roles':[{'user':actor,'role':'editor'}],
        'role_permissions':[{'role':'editor','permission':p} for p in ['read','write_owned_item']]})
    source=tmp_path/'source.wav'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=440:duration=3',str(source)],check=True)
    cfg=dict(title='test',artifact_root=str(tmp_path/'artifacts'),media_root=str(tmp_path/'media'),
        review_root=str(root),capacity_root=str(tmp_path/'capacity'),aligner_python='/unused',
        budget_usd=10,max_calls=40,ssd_reserve_gib=0,internal_reserve_gib=0,working_set_gib=15)
    manifest={'schema_version':'gemini-media-jobs.v1','jobs':[dict(item=item,source=str(source),source_sha256=file_sha(source))]}
    return store,root,item,actor,source,cfg,manifest


def fake_flow(cues,media,out,**kw):
    out.mkdir(parents=True,exist_ok=True)
    atomic(out/'run.json',{'identity':{'audio_sha256':file_sha(media)}})
    atomic(out/'transcribe.json',{'value':{'duration':3}})
    p=dict(index='1',start=0,end=3,text='原文~~重複~~。',timing_review=False,start_timing_method='original_word',speaker_turns=[])
    value={'paragraphs':[p], 'omissions_for_review':[], 'coverage':[]}
    atomic(out/'layout.json',{'value':value,'output_sha256':object_sha(value)})
    if kw.get('through') not in (None,'layout','transcribe'):
        atomic(out/f"{kw['through']}.json",{'value':value,'output_sha256':object_sha(value)})
    return value


def test_existing_metadata_only_full_delivery_and_retry_preserves_human_edit(setup,monkeypatch):
    store,root,item,actor,source,cfg,manifest=setup
    from backend.pipeline import transcript_gemini_proofreading as flow
    monkeypatch.setattr(flow,'run',fake_flow)
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    while task:=store.claim(batch,'test-worker'):
        result=execute(task['stage'],task['payload'])
        store.update(task,state='complete',result=result)
    with store.connection() as c:
        tasks=list(c.execute('SELECT t.* FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s',(batch,)))
    assert len(tasks)==11 and all(t['state']=='complete' for t in tasks)
    review=ReviewStore(root);rows,context=review.load(item)
    assert rows[0]['text']=='原文~~重複~~。'
    assert context['timing_quality']['policy']=='gemini-paragraph-start.v1'
    assert context['playback_media']['file']=='playback.mp3'
    editor=next(t for t in tasks if t['stage']=='editor')
    # A user modification after successful initialization must survive task retry.
    rows[0]['text']='人工修改。';context['script_sha256']=digest(rows)
    review_save(root/'script_review'/f'{item}.json',rows);review_save(review.folder(item)/'context.json',context)
    assert execute('editor',editor['payload'])['version']==editor['result']['version']
    assert review.load(item)[0][0]['text']=='人工修改。'
    assert file_sha(source)==manifest['jobs'][0]['source_sha256']


def test_source_drift_and_nonempty_manuscript_rejected_before_dispatch(setup):
    store,root,item,actor,source,cfg,manifest=setup
    wrong=json.loads(json.dumps(manifest));wrong['jobs'][0]['source_sha256']='0'*64
    with pytest.raises(ValueError,match='SHA'):create(store,wrong,cfg,actor)
    atomic(root/'script'/f'{item}.json',{'entries':[{'text':'existing rough'}]})
    with pytest.raises(ReviewError,match='已有稿件'):create(store,manifest,cfg,actor)


def test_concurrent_accepted_plans_cannot_steal_head(setup):
    store,root,item,actor,source,cfg,manifest=setup
    first=create(store,manifest,cfg,actor);accept(store,first)
    second=create(store,manifest,{**cfg,'title':'other controller'},actor)
    with pytest.raises(Conflict,match='current plan'):accept(store,second)


def test_plan_to_import_cas_rejects_new_human_rough(setup):
    store,root,item,actor,source,cfg,manifest=setup
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    task=store.claim(batch,'cas')
    out=Path(task['payload']['out']);fake_flow(None,source,out)
    from backend.pipeline.media_batch.gemini_media import playback
    media=playback(task['payload'])
    atomic(root/'script_review'/f'{item}.json',[{'text':'new human draft'}])
    with pytest.raises(ReviewError):
        initialize(ReviewStore(root),item,actor,source,out,media,task['payload']['empty'],'test-import')
    assert json.loads((root/'script_review'/f'{item}.json').read_text())[0]['text']=='new human draft'
