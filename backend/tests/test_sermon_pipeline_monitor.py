import json
from pathlib import Path
import pytest
from backend.tests.test_unified_gemini_pipeline import setup
from backend.pipeline.media_batch.sermon_monitor import refresh,snapshot,reconcile_item
from backend.pipeline.media_batch.runtime import atomic
from backend.pipeline.media_batch.gemini_plan import create,accept
from backend.pipeline.media_batch.store import Conflict


def test_catalog_series_order_zero_task_and_missing_metadata_survive_projection(setup):
    store,root,item,actor,source,cfg,manifest=setup
    atomic(root/'config/sermon.json',[{'item':item,'title':'第一講'},{'item':'other','title':'第二講'}])
    atomic(root/'config/sermon_series.json',[
        {'id':'2016 wrong year','title':'2014 corrected','sermons':[item,'other','missing']},
        {'id':'another','sermons':[item]}])
    cfg['source_roots']=[str(source.parent)]
    result=refresh(store,root,cfg)
    assert result['items']==3
    page=snapshot(store,root,status='all')
    assert page['total']==3 and len(page['records'])==3
    assert page['series'][0]['name']=='2014 corrected'
    assert page['series'][0]['total']==3
    assert page['series'][1]['total']==1
    assert [r['item'] for r in page['records']]==[item,'other','missing']
    assert len(page['records'][0]['groups'])==2
    assert page['records'][2]['state']=='needs_resolution'
    assert not any(r['tasks'] for r in page['records'])
    # GETs leave persisted task/call counts unchanged, including filtered/global searches.
    assert snapshot(store,root,search='第二',status='all')['filtered_total']==1
    assert snapshot(store,root,series='another',status='all')['filtered_total']==1
    assert snapshot(store,root,source='single',status='all')['filtered_total']==1
    assert snapshot(store,root,source='missing',status='all')['filtered_total']==2
    assert snapshot(store,root,source='multiple',status='all')['filtered_total']==0
    with store.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM media_ingest.calls').fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM media_ingest.tasks').fetchone()['n']==0


def test_current_plan_not_latest_updated_task_and_snapshots_are_pinned(setup):
    store,root,item,actor,source,cfg,manifest=setup
    atomic(root/'config/sermon_series.json',[{'id':'series','sermons':[item]}])
    refresh(store,root,cfg)
    first=create(store,manifest,cfg,actor);accept(store,first)
    second=create(store,manifest,{**cfg,'title':'not accepted','speaker_name':'different input'},actor)
    before=snapshot(store,root,status='all')
    assert before['records'][0]['batch']==first
    assert before['records'][0]['state']=='pending'
    assert store.claim(second,'not-current') is None
    # A late persisted history update must not override the current explicit head.
    with store.connection() as c:
        c.execute("UPDATE media_ingest.tasks SET state='failed',updated_at=now() WHERE id IN (SELECT task FROM media_ingest.batch_tasks WHERE batch=%s)",(second,))
    assert snapshot(store,root,status='all')['records'][0]['state']=='pending'
    task=store.claim(first,'current')
    store.update(task,state='failed',error='current failure')
    with pytest.raises(Conflict,match='snapshot_changed'):
        snapshot(store,root,status='all',expected=before['snapshot_revision'])
    assert snapshot(store,root,status='all')['records'][0]['state']=='failed'


def test_monitor_http_identity_plan_and_read_only_get(setup,monkeypatch):
    import hashlib,hmac,time
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.sc_api.media_batch_router import create_media_batch_router
    from backend.pipeline.media_batch.runtime import file_sha
    store,root,item,actor,source,cfg,manifest=setup
    renamed=source.with_name(item+'.wav');source.rename(renamed)
    atomic(root/'config/sermon_series.json',[{'id':'series','sermons':[item]}])
    cfg['source_roots']=[str(renamed.parent)]
    atomic(root/'config/media_pipeline.json',cfg)
    refresh(store,root,cfg)
    monkeypatch.setenv('TRANSCRIPT_REVIEW_ACTION_SECRET','test-secret')
    app=FastAPI();app.include_router(create_media_batch_router(root,lambda:store));client=TestClient(app)
    def request(method,path,body=None):
        raw=json.dumps(body).encode() if body is not None else b''
        timestamp=str(int(time.time()))
        signed='\n'.join((method,path,actor,timestamp,hashlib.sha256(raw).hexdigest()))
        return client.request(method,path,content=raw,headers={'Content-Type':'application/json',
            'x-review-actor':actor,'x-review-time':timestamp,
            'x-review-signature':hmac.new(b'test-secret',signed.encode(),hashlib.sha256).hexdigest()})
    assert client.get('/media_batches/sermons').status_code==401
    page=request('GET','/media_batches/sermons')
    assert page.status_code==200 and page.json()['records'][0]['item']==item
    with store.connection() as c:assert c.execute('SELECT count(*) AS n FROM media_ingest.tasks').fetchone()['n']==0
    body={'items':[item],'budget_usd':10,'max_calls':40,'operation_id':'plan-operation-1'}
    result=request('POST','/media_batches/sermons/plan',body)
    assert result.status_code==200,result.text
    assert result.json()['status']=='planned'
    assert request('POST','/media_batches/sermons/plan',body).json()['batch']==result.json()['batch']
    changed={**body,'budget_usd':20}
    assert request('POST','/media_batches/sermons/plan',changed).status_code==409
    assert str(renamed) not in result.text
    with store.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM media_ingest.calls').fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM media_ingest.pipeline_heads').fetchone()['n']==0


def test_human_edit_after_delivery_never_restored_to_complete_by_sync(setup):
    store,root,item,actor,source,cfg,manifest=setup
    atomic(root/'config/sermon_series.json',[{'id':'series','sermons':[item]}])
    refresh(store,root,cfg)
    batch=create(store,manifest,cfg,actor);accept(store,batch)
    marker=root/'transcript_review'/item/'pipeline-document-version.json'
    atomic(marker,{'script_sha256':'original','timings_sha256':'original'})
    from psycopg.types.json import Jsonb
    with store.connection() as c:
        c.execute("UPDATE media_ingest.tasks SET state='complete'")
        c.execute("UPDATE media_ingest.pipeline_heads SET value=value || %s",
            (Jsonb({'delivered_document_marker':{'script_sha256':'original','timings_sha256':'original'}}),))
    reconcile_item(store,root,item)
    assert snapshot(store,root,status='all')['records'][0]['state']=='complete'
    atomic(marker,{'script_sha256':'human edit','timings_sha256':'original'})
    assert snapshot(store,root,status='all')['records'][0]['state']=='needs_resolution'
    refresh(store,root,cfg);reconcile_item(store,root,item)
    assert snapshot(store,root,status='all')['records'][0]['state']=='needs_resolution'
