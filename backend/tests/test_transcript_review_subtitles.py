import copy
import pytest
from backend.tests.test_transcript_review import setup,adopted,confirmed,op
from backend.api.sc_api.transcript_review import ReviewError,read
from backend.pipeline.source_projection import source_body_sha256,is_editorial_row
from backend.api.sc_api.transcript_recovery import prepare_recovery

ITEM='講道 test'

def insert(store,doc):
    operation={'kind':'subtitles','id':'editorial-subtitles','source_version':doc['version'],'insertions':[{'after_index':'START','text':'講道引言','level':1},{'after_index':'1','text':'信心的回應','level':2}]}
    request=op(doc,operations=[operation]);store.mutate('a',ITEM,request)
    return store.document('a',ITEM),request


def test_subtitles_preserve_body_review_history_times_and_publish_in_legacy_shape(setup):
    store,_,_=setup;adopted(setup);confirmed(store);before=store.document('a',ITEM)
    after,request=insert(store,before)
    assert source_body_sha256(before['script'])==source_body_sha256(after['script'])
    assert after['context']['paragraph_bindings']==before['context']['paragraph_bindings']
    assert after['context']['reviews']==before['context']['reviews']
    headings=[p for p in after['script'] if p['type']=='subtitle'];assert [p['text'] for p in headings]==['## 講道引言','### 信心的回應']
    assert all(p['index'].startswith('subtitle-') and is_editorial_row(p) for p in headings)
    store.mutate('a',ITEM,request);assert store.document('a',ITEM)['version']==after['version']
    store.publish('a',ITEM,op(after))
    pub=read(store.root/'script_published'/f'{ITEM}.json')
    assert pub['script'][0]['type']=='subtitle' and 'start_time' not in pub['script'][0]
    assert pub['script'][1]['start_time']==before['context']['paragraph_bindings']['1']['start_ms']/1000


def test_subtitle_edit_delete_autosave_preserves_existing_body_and_can_recover(setup):
    store,_,_=setup;base=adopted(setup);doc,_=insert(store,base);heading=doc['script'][0]['index']
    ops=[{'kind':'text','id':heading,'text':'## 修訂小標題'}]
    plan=prepare_recovery(store,'a',ITEM,op(doc,base_version=doc['version'],pending_operations=ops))
    store.mutate('a',ITEM,op(doc,operations=plan['operations']));doc=store.document('a',ITEM)
    store.mutate('a',ITEM,op(doc,operations=[{'kind':'remove_subtitle','id':heading}]))
    after=store.document('a',ITEM);assert source_body_sha256(after['script'])==source_body_sha256(base['script'])
    assert len([p for p in after['script'] if p['type']=='subtitle'])==1
    with pytest.raises(ReviewError):store.mutate('a',ITEM,op(after,operations=[{'kind':'remove_subtitle','id':'1'}]))


@pytest.mark.parametrize('fault',['source_version','anchor','level','empty'])
def test_invalid_heading_cannot_change_any_row_or_context(setup,fault):
    store,_,_=setup;doc=adopted(setup)
    operation={'kind':'subtitles','id':'headings','source_version':doc['version'],'insertions':[{'after_index':'START','text':'新標題','level':1}]}
    if fault=='source_version':operation['source_version']='stale'
    elif fault=='anchor':operation['insertions'][0]['after_index']='invented'
    elif fault=='level':operation['insertions'][0]['level']=5
    else:operation['insertions'][0]['text']=''
    with pytest.raises(ReviewError):store.mutate('a',ITEM,op(doc,operations=[operation]))
    assert store.document('a',ITEM)==doc


def test_heading_suggestions_cannot_be_rebased_after_body_changes(setup):
    store,_,_=setup;base=adopted(setup)
    store.mutate('a',ITEM,op(base,operations=[{'kind':'text','id':'2','text':'另一處修改過的講道內容'}]));current=store.document('a',ITEM)
    operation={'kind':'subtitles','id':'headings','source_version':base['version'],'insertions':[{'after_index':'START','text':'舊建議','level':1}]}
    with pytest.raises(ReviewError):prepare_recovery(store,'a',ITEM,op(current,base_version=base['version'],pending_operations=[operation]))


@pytest.mark.parametrize('mode',['success','stale','failure'])
def test_suggestion_endpoint_uses_live_saved_body_and_never_writes_suggestions(setup,monkeypatch,mode):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from urllib.parse import quote
    import json
    from backend.tests.test_transcript_review import signed
    from backend.api.sc_api.transcript_review_router import create_review_router
    import backend.pipeline.subtitle_generation as generator
    store,root,_=setup;doc=adopted(setup)
    store.mutate('a',ITEM,op(doc,operations=[{'kind':'text','id':'1','text':'保留~~刪除~~正文'}]));doc=store.document('a',ITEM)
    doc,_=insert(store,doc)
    def generate(rows,**kwargs):
        assert len(rows)==2 and all(p['type']!='subtitle' for p in rows)
        assert '刪除' not in rows[0]['text'] and '保留' in rows[0]['text']
        if mode=='failure':raise generator.SubtitleGenerationError('fixture model unavailable')
        if mode=='stale':store.mutate('a',ITEM,op(doc,operations=[{'kind':'text','id':'2','text':'另一處修改'}]))
        return [{'after_index':'START','text':'## 建議','level':1}]
    monkeypatch.setattr(generator,'generate_subtitles',generate);monkeypatch.setenv('TRANSCRIPT_REVIEW_ACTION_SECRET','test-secret')
    app=FastAPI();app.include_router(create_review_router(root),prefix='/sc_api');client=TestClient(app)
    path='/sc_api/review_document/'+quote(ITEM,safe='')+'/heading_suggestions'
    body=json.dumps(op(doc)).encode();headers={**signed('POST',path,body),'content-type':'application/json','if-match':'"'+doc['version']+'"'}
    assert client.post(path,content=body).status_code==401
    response=client.post(path,content=body,headers=headers)
    assert response.status_code=={'success':200,'stale':412,'failure':502}[mode]
    if mode!='stale':assert store.document('a',ITEM)==doc
    if mode=='success':assert response.json()['insertions'][0]['text']=='## 建議'


def test_heading_source_version_cannot_bless_body_changes_in_the_same_packet(setup):
    store,_,_=setup;doc=adopted(setup)
    operations=[{'kind':'text','id':'1','text':'模型未讀過的新內容'},
        {'kind':'subtitles','id':'headings','source_version':doc['version'],
         'insertions':[{'after_index':'START','text':'舊建議','level':1}]}]
    with pytest.raises(ReviewError):store.mutate('a',ITEM,op(doc,operations=operations))
    assert store.document('a',ITEM)==doc
