import copy
import pytest
from backend.tests.test_transcript_review import setup,adopted,op
from backend.api.sc_api.transcript_review import ReviewError,digest,atomic,read
from backend.api.sc_api.transcript_recovery import prepare_recovery

ITEM='講道 test'


def timing_change(store,doc):
    with store.lock(ITEM):
        rows,c=store.load(ITEM);c=copy.deepcopy(c)
        c['paragraph_bindings']['1']['start_ms']+=400
        store.commit(ITEM,'a','timing_fixture_01',{'operations':[]},rows,c,'timing_repair')
    return store.document('a',ITEM)


def plan(store,current,base,ops,**extra):
    return prepare_recovery(store,'a',ITEM,op(current,base_version=base['version'],pending_operations=ops,**extra))


def test_recover_text_after_timing_repair_via_normal_write_with_no_automatic_confirmation(setup):
    store,_,_=setup;base=adopted(setup);current=timing_change(store,base)
    text='人工校對 ~~軟刪除~~ 文字。';binding=base['context']['paragraph_bindings']['1']
    ops=[{'kind':'text','id':'1','text':text},{'kind':'paragraph_review','id':'1','decision':'confirmed','content_sha256':digest({'text':text,**binding})}]
    result=plan(store,current,base,ops)
    assert result['read_only'];assert result['operations']==ops[:1];assert result['recheck_paragraph_ids']==['1']
    assert store.document('a',ITEM)==current
    store.mutate('a',ITEM,op(current,operations=result['operations']))
    saved=store.document('a',ITEM)
    assert saved['script'][0]['text']==text
    assert saved['context']['paragraph_bindings']==current['context']['paragraph_bindings']
    assert 'p:1' not in saved['context']['reviews']


def test_other_paragraph_change_survives_recovery_but_same_paragraph_conflicts(setup):
    store,_,_=setup;base=adopted(setup)
    store.mutate('a',ITEM,op(base,operations=[{'kind':'text','id':'2','text':'另一裝置改第二段'}]))
    current=store.document('a',ITEM)
    result=plan(store,current,base,[{'kind':'text','id':'1','text':'本機改第一段'}])
    store.mutate('a',ITEM,op(current,operations=result['operations']))
    current=store.document('a',ITEM)
    assert current['script'][1]['text']=='另一裝置改第二段'
    with pytest.raises(ReviewError,match='已有其他修改'):
        plan(store,current,base,[{'kind':'text','id':'2','text':'覆蓋第二段'}])
    assert store.document('a',ITEM)==current


def test_commit_receipt_is_reconciled_without_resending_or_reconfirming(setup):
    store,_,_=setup;base=adopted(setup)
    packet=op(base,operations=[{'kind':'text','id':'1','text':'已儲存文字'}])
    store.mutate('a',ITEM,packet);current=store.document('a',ITEM)
    queued={'kind':'text','id':'2','text':'尚未儲存文字'}
    result=plan(store,current,base,[*packet['operations'],queued],pending_packet=packet)
    assert result['operations']==[queued]
    result=plan(store,current,base,packet['operations'],pending_packet=packet)
    assert result['operations']==[]
    assert store.document('a',ITEM)==current


def test_current_epoch_cannot_authorize_old_draft_across_reclaim(setup):
    store,_,_=setup;base=adopted(setup)
    store.claim('a',ITEM,op(base,action='unclaim'));d=store.document('a',ITEM)
    store.claim('a',ITEM,op(d,action='claim'));current=store.document('a',ITEM)
    with pytest.raises(ReviewError,match='認領已變更'):
        plan(store,current,base,[{'kind':'text','id':'1','text':'舊草稿'}])


@pytest.mark.parametrize('fault',['unknown_version','tampered_snapshot','source','permission','stale_cas'])
def test_fail_closed_without_mutating_anything(setup,fault):
    store,_,_=setup;base=adopted(setup);current=timing_change(store,base)
    request=op(current,base_version=base['version'],pending_operations=[{'kind':'text','id':'1','text':'本機修改'}])
    if fault=='unknown_version':request['base_version']='a'*64
    elif fault=='tampered_snapshot':
        path=store.folder(ITEM)/'revisions'/f'{base["context"]["revision"]:08d}.json';s=read(path);s['script'][0]['text']='篡改';atomic(path,s)
    elif fault=='source':
        path=store.folder(ITEM)/'context.json';s=read(path);s['source_record_sha256']='changed';atomic(path,s);request['version']=store.document('a',ITEM)['version']
    elif fault=='permission':request['epoch']='wrong'
    else:request['version']=base['version']
    before=store.document('a',ITEM)
    with pytest.raises(ReviewError):prepare_recovery(store,'a',ITEM,request)
    assert store.document('a',ITEM)==before


def test_native_cas_still_blocks_a_race_after_read_only_plan(setup):
    store,_,_=setup;base=adopted(setup);current=timing_change(store,base)
    result=plan(store,current,base,[{'kind':'text','id':'1','text':'本機修改'}])
    store.mutate('a',ITEM,op(current,operations=[{'kind':'text','id':'1','text':'後來的修改'}]))
    with pytest.raises(ReviewError) as e:store.mutate('a',ITEM,op(current,operations=result['operations']))
    assert e.value.status==412
    assert store.document('a',ITEM)['script'][0]['text']=='後來的修改'
