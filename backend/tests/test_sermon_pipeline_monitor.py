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
