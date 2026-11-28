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


def test_recovery_reservation_preserves_caps_and_refuses_second_or_unknown(setup):
    from backend.pipeline.media_batch.engineering_recovery import reserve,verify
    store,root,item,actor,source,cfg,manifest=setup
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    task=store.claim(batch,'failed-worker');store.update(task,state='failed',error="KeyError: 'steps'")
    out=Path(task['payload']['out']);out.mkdir(parents=True)
    atomic(out/'asr-0.request.json',{'source_sha256':file_sha(source)})
    atomic(out/'asr-0.response.json',{'status':'completed','usage':{'total_output_tokens':0}})
    before=store.snapshot(batch)
    receipt=reserve(store,batch,task['id'],defect='Empty completed reply omitted steps',
                    validation='regression test passes',patch='exact diff fixture')
    verify(task['payload'],receipt['corrected_implementation'])
    after=store.snapshot(batch)
    before.pop('generated_at');after.pop('generated_at')
    assert after==before
    with pytest.raises(Conflict,match='already has a recovery'):
        reserve(store,batch,task['id'],defect='same',validation='same',patch='same')
    (out/'engineering-recovery.json').unlink()
    with store.connection() as c:
        c.execute('INSERT INTO media_ingest.calls(id,batch,task,model,reserved_usd,state) VALUES(%s,%s,%s,%s,%s,%s)',
                  ('unknown',batch,task['id'],'fixture',.5,'unknown'))
    with pytest.raises(Conflict,match='Unresolved'):
        reserve(store,batch,task['id'],defect='same',validation='same',patch='same')


def test_projection_recovers_original_delivery_after_crash_and_later_human_edit(setup,monkeypatch):
    from backend.pipeline import transcript_gemini_proofreading as flow
    from backend.pipeline.media_batch.sermon_monitor import refresh,snapshot
    monkeypatch.setattr(flow,'run',fake_flow)
    store,root,item,actor,source,cfg,manifest=setup
    atomic(root/'config/sermon_series.json',[{'id':'series','sermons':[item]}])
    refresh(store,root,cfg)
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    while task:=store.claim(batch,'test-worker'):
        result=execute(task['stage'],task['payload']);store.update(task,state='complete',result=result)
    # Simulate a crash before incremental indexing, then an authorized human edit.
    assert snapshot(store,root,status='all')['records'][0]['state']=='needs_sync'
    review=ReviewStore(root)
    with review.lock(item):
        rows,ctx=review.load(item);rows[0]['text']='人工修改。'
        review.commit(item,actor,'human-edit-after-delivery',{},rows,ctx,'edit')
    refresh(store,root,cfg)
    page=snapshot(store,root,status='all')
    assert page['records'][0]['state']=='needs_resolution'
    assert review.load(item)[0][0]['text']=='人工修改。'
    refresh(store,root,cfg)
    assert snapshot(store,root,status='all')['records'][0]['state']=='needs_resolution'


@pytest.mark.parametrize('faststart',[True,False])
def test_video_reuse_or_versioned_compression_and_receipt_drift(setup,faststart):
    from backend.pipeline.media_batch.gemini_media import playback,compatible
    store,root,item,actor,source,cfg,manifest=setup
    video=source.with_suffix('.mp4')
    command=['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=blue:s=96x64:r=10:d=3',
             '-i',str(source),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest']
    if faststart:command+=['-movflags','+faststart']
    subprocess.run(command+[str(video)],check=True)
    sha=file_sha(video)
    manifest['jobs'][0].update(source=str(video),source_sha256=sha)
    batch=create(store,manifest,cfg,actor)
    with store.connection() as c:
        payload=c.execute("SELECT t.payload FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.stage='media'",(batch,)).fetchone()['payload']
    result=playback(payload)
    assert file_sha(video)==sha
    assert compatible(Path(result['path']),True)
    assert playback(payload)==result
    if faststart:
        assert result['origin']=='verified_existing' and Path(result['path'])==video
    else:
        target=Path(result['path'])
        assert target.is_relative_to(Path(cfg['media_root'])/'playback'/item)
        assert target!=video and result['source_sha256']==sha
        with target.open('ab') as f:f.write(b'drift')
        with pytest.raises(ValueError,match='drift'):playback(payload)
        assert file_sha(video)==sha


def test_recovery_chain_is_bounded_and_preserves_prior_receipts(setup,monkeypatch):
    from backend.pipeline.media_batch.engineering_recovery import reserve,verify
    from backend.pipeline.media_batch import gemini_plan
    store,root,item,actor,source,cfg,manifest=setup
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    task=store.claim(batch,'failed-worker');store.update(task,state='failed',error='parser defect')
    out=Path(task['payload']['out']);out.mkdir(parents=True)
    atomic(out/'asr-0.request.json',{'source_sha256':file_sha(source)})
    atomic(out/'asr-0.response.json',{'status':'completed'})
    for i in range(3):
        monkeypatch.setattr(gemini_plan,'implementation',lambda i=i:f'fixed-{i}')
        receipt=reserve(store,batch,task['id'],defect=f'defect-{i}',validation='test passed',patch=f'diff-{i}')
        verify(task['payload'],f'fixed-{i}')
        assert len(receipt['prior_receipts'])==i
    monkeypatch.setattr(gemini_plan,'implementation',lambda:'fourth')
    with pytest.raises(Conflict,match='exhausted'):
        reserve(store,batch,task['id'],defect='fourth',validation='test',patch='diff')
    (out/'engineering-recovery.json').write_text('{}')
    with pytest.raises(ValueError,match='history changed'):verify(task['payload'],'fixed-2')


def test_candidate_only_preserves_existing_text_and_never_initializes_editor(setup,monkeypatch):
    from backend.pipeline import transcript_gemini_proofreading as flow
    monkeypatch.setattr(flow,'run',fake_flow)
    store,root,item,actor,source,cfg,manifest=setup
    original=[{'index':'1','text':'人工原稿。'}]
    atomic(root/'script_patched'/f'{item}.json',original)
    manifest['jobs'][0].update(deliverables=['candidate','playback'],adoption='register_candidate')
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    while task:=store.claim(batch,'test-worker'):
        store.update(task,state='complete',result=execute(task['stage'],task['payload']))
    assert json.loads((root/'script_patched'/f'{item}.json').read_text())==original
    assert not (root/'transcript_review'/item/'context.json').exists()
    candidates=list((root/'gemini_transcript_candidates'/item).glob('*.json'))
    assert len(candidates)==1
    assert not ReviewStore(root).candidate_path(item).exists()
    with store.connection() as c:
        stages=[r['stage'] for r in c.execute('select stage from media_ingest.tasks')]
    assert 'candidate' in stages and 'editor' not in stages and 'slides' not in stages
    wrong=json.loads(json.dumps(manifest));wrong['jobs'][0]['adoption']='initialize_empty_draft'
    with pytest.raises(ValueError,match='contradicts'):create(store,wrong,cfg,actor)


def test_failed_slides_supplement_keeps_completed_transcript_and_other_requirements(setup,monkeypatch):
    from backend.pipeline import transcript_gemini_proofreading as flow
    from backend.pipeline.media_batch.sermon_monitor import refresh,snapshot,reconcile_item
    monkeypatch.setattr(flow,'run',fake_flow)
    store,root,item,actor,source,cfg,manifest=setup
    atomic(root/'config/sermon_series.json',[{'id':'series','sermons':[item]}]);refresh(store,root,cfg)
    first=create(store,manifest,cfg,actor);accept(store,first)
    while task:=store.claim(first,'first'):
        store.update(task,state='complete',result=execute(task['stage'],task['payload']))
    reconcile_item(store,root,item)
    with store.connection() as c:
        c.execute("UPDATE media_ingest.tasks SET state='failed',error='slide failure' WHERE stage='slides'")
        before=c.execute("SELECT id,result,attempt FROM media_ingest.tasks WHERE stage='transcribe'").fetchone()
    supplement=json.loads(json.dumps(manifest));supplement['jobs'][0]['deliverables']=['slides']
    second=create(store,supplement,{**cfg,'operation_id':'supplement'},actor);accept(store,second)
    page=snapshot(store,root,status='all')
    assert page['records'][0]['state']=='pending'
    assert any(t['id']==before['id'] and t['state']=='complete' for t in page['records'][0]['tasks'])
    monkeypatch.setattr(flow,'run',lambda *a,**k:pytest.fail('supplement must not retranscribe'))
    while task:=store.claim(second,'supplement'):
        store.update(task,state='complete',result=execute(task['stage'],task['payload']))
    reconcile_item(store,root,item)
    assert snapshot(store,root,status='all')['records'][0]['state']=='complete'
    with store.connection() as c:
        after=c.execute("SELECT id,result,attempt FROM media_ingest.tasks WHERE stage='transcribe'").fetchone()
    assert after==before


def test_playback_only_requires_version_and_does_not_change_editor_text(setup,monkeypatch):
    from backend.pipeline import transcript_gemini_proofreading as flow
    monkeypatch.setattr(flow,'run',fake_flow)
    store,root,item,actor,source,cfg,manifest=setup
    atomic(root/'config/sermon.json',[{'item':item,'title':'講道','status':'processing',
                                     'assigned_to':actor,'assignment_epoch':1}])
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    while task:=store.claim(batch,'first'):
        store.update(task,state='complete',result=execute(task['stage'],task['payload']))
    review=ReviewStore(root);rows,ctx=review.load(item)
    only=json.loads(json.dumps(manifest));only['jobs'][0]['deliverables']=['playback']
    with pytest.raises(Conflict,match='version'):create(store,only,cfg,actor)
    only['jobs'][0]['expected_version']=digest(review.document_value(rows,ctx))
    second=create(store,only,{**cfg,'operation_id':'media-supplement'},actor);accept(store,second)
    monkeypatch.setattr(flow,'run',lambda *a,**k:pytest.fail('no transcription'))
    while task:=store.claim(second,'supplement'):
        store.update(task,state='complete',result=execute(task['stage'],task['payload']))
    assert review.load(item)[0]==rows
    assert review.load(item)[1]['revision']==ctx['revision']+1


def test_asr_coverage_gap_is_withheld_in_editor_without_changing_text(setup):
    store,root,item,actor,source,cfg,manifest=setup
    review=ReviewStore(root);expected=empty_snapshot(review,item,actor)
    out=Path(cfg['artifact_root']);fake_flow(None,source,out)
    layout=json.loads((out/'layout.json').read_text())['value']
    layout['coverage']=[{'start':0,'end':3,'word_count':0,'coverage_review':True}]
    atomic(out/'layout.json',{'value':layout,'output_sha256':object_sha(layout)})
    playback={'path':str(source),'source_sha256':file_sha(source),'sha256':file_sha(source),
              'mime':'audio/mpeg','duration':3}
    initialize(review,item,actor,source,out,playback,expected,'coverage-gap-import')
    rows,ctx=review.load(item)
    assert rows[0]['text']=='原文~~重複~~。'
    assert ctx['timing_quality']['status']=='editor_draft_only'
    assert ctx['timing_quality']['source_holds']['1']['reason']=='asr_coverage_unverified'


def test_editor_accepts_existing_source_word_correction_method_but_rejects_unknown(setup):
    from backend.pipeline.gemini_review import candidate
    store,root,item,actor,source,cfg,manifest=setup
    out=Path(cfg['artifact_root']);fake_flow(None,source,out)
    def method(value):
        saved=json.loads((out/'layout.json').read_text());saved['value']['paragraphs'][0]['start_timing_method']=value
        saved['output_sha256']=object_sha(saved['value']);atomic(out/'layout.json',saved)
    playback={'source_sha256':file_sha(source),'sha256':file_sha(source),'path':str(source)}
    method('same_length_correction')
    assert candidate(source,out,playback)['layout']['paragraphs'][0]['start_timing_method']=='same_length_correction'
    method('invented_clock')
    with pytest.raises(ValueError,match='Unproven'):candidate(source,out,playback)


def test_recovery_bound_is_per_original_task_without_new_call_budget(setup,monkeypatch):
    from backend.pipeline.media_batch.engineering_recovery import reserve,verify
    from backend.pipeline.media_batch import gemini_plan
    store,root,item,actor,source,cfg,manifest=setup
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    first=store.claim(batch,'first');store.update(first,state='failed',error='first defect')
    out=Path(first['payload']['out']);out.mkdir(parents=True)
    atomic(out/'asr-0.request.json',{'source_sha256':file_sha(source)})
    atomic(out/'asr-0.response.json',{'status':'completed'})
    for i in range(3):
        monkeypatch.setattr(gemini_plan,'implementation',lambda i=i:f'fixed-{i}')
        reserve(store,batch,first['id'],defect='first task defect',validation='test',patch=f'fix-{i}')
    second=store.claim(batch,'other-original-stage');assert second and second['id']!=first['id']
    store.update(second,state='failed',error='adapter defect')
    monkeypatch.setattr(gemini_plan,'implementation',lambda:'fixed-next-stage')
    before=store.snapshot(batch);before.pop('generated_at')
    result=reserve(store,batch,second['id'],defect='next original task defect',validation='test',patch='next stage fix')
    assert result['original_task_recovery_ordinal']==1 and len(result['prior_receipts'])==3
    verify(second['payload'],'fixed-next-stage')
    after=store.snapshot(batch);after.pop('generated_at');assert after==before


def test_ssd_reattachment_is_audited_without_changing_original_configuration(setup,monkeypatch):
    from backend.pipeline.media_batch import runtime_storage,runtime
    store,root,item,actor,source,cfg,manifest=setup
    batch=create(store,manifest,cfg,actor)
    with store.connection() as c:before=c.execute('select * from media_ingest.batches where id=%s',(batch,)).fetchone()
    revision=store.control(batch,'pause-for-storage',before['revision'],'pause')
    saved=before['config']['storage_guards'];changed=[{**g,'device':g['device']+1} for g in saved]
    monkeypatch.setattr(runtime,'storage_guards',lambda cfg:changed)
    receipt=runtime_storage.reattach(store,batch,'same-storage-reattach',revision,'same paths and exact original source after disconnect')
    assert receipt['guards']==changed and receipt['model_calls']==0
    assert runtime_storage.reattach(store,batch,'same-storage-reattach',revision,'same paths and exact original source after disconnect')==receipt
    monkeypatch.setenv('MEDIA_BATCH_DATABASE_URL',store.dsn)
    assert runtime_storage.effective_guards(before['config'])==changed
    with store.connection() as c:
        after=c.execute('select * from media_ingest.batches where id=%s',(batch,)).fetchone()
        assert after['config']==before['config'] and after['calls']==before['calls']==0
        assert after['reserved_usd']==before['reserved_usd']
    source.write_bytes(b'changed recording')
    with pytest.raises(Conflict,match='Source SHA'):
        runtime_storage.reattach(store,batch,'another-reattach',after['revision'],'source must still match')


def test_authorized_timeout_coverage_keeps_original_unknown_and_original_budget(setup,monkeypatch):
    from backend.pipeline.media_batch.timeout_recovery import effective,reconcile,verify_coverage
    from backend.pipeline.media_batch.store import key
    from backend.pipeline.media_batch.engineering_recovery import reserve
    from backend.pipeline.media_batch import gemini_plan
    store,root,item,actor,source,cfg,manifest=setup
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    task=store.claim(batch,'interrupted');out=Path(task['payload']['out']);out.mkdir(parents=True)
    request={'model':'fixture','source_sha256':file_sha(source)}
    original=out/'calls'/object_sha(request);atomic(original/'request.json',request)
    atomic(original/'state.json',{'status':'sent_outcome_unknown','reserved_usd':.08})
    parent=key([task['id'],str(out.resolve()),request]);store.reserve(batch,task['id'],parent,'fixture',.08)
    atomic(original/'timeout-recovery.json',{'schema_version':'media-timeout-recovery.v1','batch':batch,'task':task['id'],
        'max_attempts':1,'original_request_sha256':object_sha(request),'unknown_state_sha256':file_sha(original/'state.json')})
    monkeypatch.setenv('MEDIA_BATCH_CONTEXT',json.dumps({'batch':batch,'task':task['id']}))
    retry=effective(out,request);target=out/'calls'/object_sha(retry);atomic(target/'request.json',retry)
    atomic(target/'response.json',{'status':'completed','answer':'saved'})
    state={'status':'complete','response_sha256':file_sha(target/'response.json'),'reserved_usd':.08}
    atomic(target/'state.json',state)
    child=key([task['id'],str(out.resolve()),retry]);store.reserve(batch,task['id'],child,'fixture',.08);store.call_complete(child,state)
    store.update(task,state='unknown_outcome',error='SSD disconnected')
    revision=store.control(batch,'pause-for-reconcile',1,'pause')
    result=reconcile(store,batch,task['id'],out,request,'reconcile-approved-one-attempt',revision)
    assert result['model_calls']==0 and result['proof']['original_unknown_preserved']
    with store.connection() as c:
        header=c.execute('select * from media_ingest.batches where id=%s',(batch,)).fetchone()
        parentrow=c.execute('select * from media_ingest.calls where id=%s',(parent,)).fetchone()
    assert parentrow['state']=='unknown' and header['calls']==2
    assert header['config']['budget_usd']==10 and header['config']['max_calls']==40
    verify_coverage(store,parentrow,task['payload'])
    monkeypatch.setattr(gemini_plan,'implementation',lambda:'fixed-local-recovery')
    reserve(store,batch,task['id'],defect='local SSD recovery fix',validation='test passed',patch='exact fixed code')
    store.control(batch,'resume-covered-only',header['revision'],'retry')
    with store.connection() as c:assert c.execute('select state from media_ingest.tasks where id=%s',(task['id'],)).fetchone()['state']=='pending'
    atomic(target/'response.json',{'changed':'answer'})
    with pytest.raises(ValueError,match='proof drift'):verify_coverage(store,parentrow,task['payload'])
