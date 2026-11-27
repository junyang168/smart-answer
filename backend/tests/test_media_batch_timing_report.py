import hashlib
import hmac
import json
import time
from contextlib import contextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.sc_api.media_batch_router import create_media_batch_router
from backend.pipeline.slide_change_pilot import save_json,file_sha
from backend.pipeline.transcript_timing_report import write_report,report_binding


def test_remote_timing_report_requires_live_editor_role_and_exact_bound_artifacts(tmp_path,monkeypatch):
    root=tmp_path/'review';roles=root/'config/config.json'
    def role(value):save_json(roles,dict(users=[dict(id='a')],user_roles=[dict(user='a',role=value)]))
    role('beta_tester');monkeypatch.setenv('TRANSCRIPT_REVIEW_ACTION_SECRET','test-report-secret')
    out=tmp_path/'out';e=out/'evidence';source=tmp_path/'original.mp3';source.write_bytes(b'ID3'+bytes(range(256))*8)
    save_json(out/'media-receipt.json',dict(path=str(source),sha256=file_sha(source),source_sha256='source',mime='audio/mpeg'))
    record=dict(source_sha256='source',paragraphs=[dict(id='w00-p0',window_id='w00',text='待核對的原文',start_ms=100,end_ms=1000)])
    save_json(out/'master.json',dict(windows=[dict(id='w00',start_ms=0,end_ms=2000)]))
    save_json(e/'assessment.json',dict(lectures={'s':dict(paragraphs=[dict(id='w00-p0',status='withheld')])}))
    write_report(out,record,e)
    task=dict(payload=dict(out=str(out),info=dict(source_sha='source')),result=report_binding(out,'B-test','R-test'))
    class Query:
        def __init__(self,row):self.row=row
        def fetchone(self):return self.row
    class Connection:
        def execute(self,sql,args):return Query(task if args==('B-test','R-test') else None)
    class Store:
        @contextmanager
        def connection(self):yield Connection()
    app=FastAPI();app.include_router(create_media_batch_router(root,Store));client=TestClient(app)
    path='/media_batches/B-test/timing/R-test'
    def signed(url,method='GET',headers=None):
        stamp=str(int(time.time()));msg='\n'.join((method,url,'a',stamp,hashlib.sha256(b'').hexdigest()))
        auth={'x-review-actor':'a','x-review-time':stamp,'x-review-signature':hmac.new(b'test-report-secret',msg.encode(),hashlib.sha256).hexdigest()}
        return client.request(method,url,headers={**auth,**(headers or {})})
    for url in [path,path+'/media']:assert client.get(url).status_code==401
    for value in ['admin','editor','beta_tester']:
        role(value);r=signed(path);assert r.status_code==200
        assert '待核對的原文' in r.text and f'src="/api/sc_api{path}/media"' in r.text
        assert 'nonce=' in r.text and "media-src 'self'" in r.headers['content-security-policy']
        assert r.headers['cache-control']=='private, no-store'
    r=signed(path+'/media',headers={'Range':'bytes=10-29'})
    assert r.status_code==206 and r.content==source.read_bytes()[10:30]
    assert r.headers['content-type']=='audio/mpeg'
    assert signed(path+'/media',method='HEAD').content==b''
    role('reader');assert signed(path).status_code==403 and signed(path+'/media').status_code==403
    role('editor');assert signed('/media_batches/B-other/timing/R-test').status_code==404
    task['payload']['info']['source_sha']='another';assert signed(path).status_code==409
    task['payload']['info']['source_sha']='source'
    media=out/'timing-review/playback.mp3';media.write_bytes(b'changed')
    assert signed(path).status_code==409 and signed(path+'/media').status_code==409
