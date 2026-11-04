import json
import shutil
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.tests.test_transcript_review import setup,adopted,confirmed,op,signed
from backend.api.sc_api.transcript_review import ReviewStore,ReviewError,atomic,read,file_hash,pending_timing_ids
from backend.api.sc_api.transcript_review_router import create_review_router
from backend.pipeline import transcript_review_promotion as promotion

ITEM='講道 test'


def prepare(setup,tmp_path):
    source,root,_=setup;adopted(setup)
    prod=tmp_path/'production';(prod/'config').mkdir(parents=True)
    shutil.copy2(root/'config/config.json',prod/'config/config.json')
    atomic(prod/'config/sermon.json',[{'item':ITEM,'title':'舊發布標題','status':'published','assigned_to':'b'},
        {'item':'其他講道','status':'ready','assigned_to':'b'}])
    atomic(prod/'script_review'/f'{ITEM}.json',[{'index':'old','text':'舊校對'}])
    atomic(prod/'script_patched'/f'{ITEM}.json',[{'index':'old','text':'舊基準'}])
    atomic(prod/'script_published'/f'{ITEM}.json',{'metadata':{'title':'舊發布標題'},'script':[{'text':'舊發布'}]})
    backup=tmp_path/'backup';promotion.backup(root,prod,ITEM,backup)
    return source,root,prod,backup


def test_verified_backup_import_preserves_owner_history_and_publication(setup,tmp_path):
    source,beta,prod,backup=prepare(setup,tmp_path)
    before=source.document('a',ITEM);publication=file_hash(prod/'script_published'/f'{ITEM}.json')
    receipt=promotion.promote(beta,prod,ITEM,backup);target=ReviewStore(prod)
    after=target.document('a',ITEM)
    assert after['version']==before['version'] and after['epoch']==before['epoch']
    assert after['can_edit'] and after['header']['assigned_to']=='a'
    assert file_hash(prod/'script_published'/f'{ITEM}.json')==publication
    assert read(prod/'script_patched'/f'{ITEM}.json')==read(beta/'script_patched'/f'{ITEM}.json')
    assert read(backup/'production/script_patched'/f'{ITEM}.json')[0]['text']=='舊基準'
    for p in (beta/'transcript_review'/ITEM/'revisions').glob('*.json'):
        assert p.read_bytes()==(prod/'transcript_review'/ITEM/'revisions'/p.name).read_bytes()
    assert receipt['review_decisions_added']==0 and not receipt['published_by_migration']
    assert promotion.promote(beta,prod,ITEM,backup)==receipt
    with pytest.raises(ReviewError,match='移交正式資料'):source.document('a',ITEM)
    assert read(prod/'config/sermon.json')[1]['assigned_to']=='b'
    promotion.verify_backup(backup,read(backup/'backup.json'))


def test_beta_routes_read_write_production_and_keep_backup_immutable(setup,tmp_path,monkeypatch):
    source,beta,prod,backup=prepare(setup,tmp_path);promotion.promote(beta,prod,ITEM,backup)
    saved=file_hash(beta/'script_review'/f'{ITEM}.json')
    monkeypatch.setenv('TRANSCRIPT_REVIEW_ACTION_SECRET','test-secret')
    app=FastAPI();app.include_router(create_review_router(beta,beta_owner='a',role_root=prod,
        production_root=prod,api_base='/api/sc_api/beta'),prefix='/sc_api/beta');client=TestClient(app)
    path='/sc_api/beta/review_document/'+quote(ITEM,safe='')
    response=client.get(path,headers=signed('GET',path));assert response.status_code==200
    doc=ReviewStore(prod).document('a',ITEM);pid=doc['script'][0]['index']
    packet=op(doc,operations=[{'kind':'text','id':pid,'text':'正式資料上的新修改','before_sha256':doc['paragraph_shas'][pid]}])
    # Existing operation contract uses content_sha256 for target CAS.
    packet['operations'][0]['content_sha256']=doc['paragraph_shas'][pid]
    body=json.dumps(packet,ensure_ascii=False).encode();headers=signed('PATCH',path,body);headers['If-Match']=doc['version']
    response=client.patch(path,content=body,headers=headers);assert response.status_code==200,response.text
    assert ReviewStore(prod).document('a',ITEM)['script'][0]['text']=='正式資料上的新修改'
    assert file_hash(beta/'script_review'/f'{ITEM}.json')==saved
    assert client.get(path,headers=signed('GET',path,actor='b')).status_code==403
    assert client.get('/sc_api/beta/review_items',headers=signed('GET','/sc_api/beta/review_items')).json()[0]['assigned_to_name']==doc['header']['assigned_to_name']
    promotion.verify_backup(backup,read(backup/'backup.json'))


def test_corrupt_backup_or_changed_source_cannot_be_promoted(setup,tmp_path):
    source,beta,prod,backup=prepare(setup,tmp_path)
    atomic(beta/'script_review'/f'{ITEM}.json',[{'index':'changed','text':'新的未備份修改'}])
    with pytest.raises(ValueError,match='changed after backup'):promotion.promote(beta,prod,ITEM,backup)
    assert not (prod/'transcript_review'/ITEM/'context.json').exists()


def test_partial_promotion_resumes_without_replacing_other_drafts(setup,tmp_path,monkeypatch):
    source,beta,prod,backup=prepare(setup,tmp_path);original=promotion.atomic;failed=False
    def fail_catalog(path,value):
        nonlocal failed
        if path==prod/'config/sermon.json' and not failed:
            failed=True;raise OSError('simulated interruption after context install')
        return original(path,value)
    monkeypatch.setattr(promotion,'atomic',fail_catalog)
    with pytest.raises(OSError):promotion.promote(beta,prod,ITEM,backup)
    assert (prod/'transcript_review'/ITEM/'context.json').exists()
    receipt=promotion.promote(beta,prod,ITEM,backup)
    assert receipt['assigned_to']=='a'


def test_deleted_only_time_failure_is_not_a_publication_block_but_live_text_is():
    rows=[{'index':'1','text':'保留口述'},{'index':'2','text':'~~整段刪除~~'}]
    c={'timing_quality':{'status':'withheld','paragraph_states':{'1':{'status':'qualified'},'2':{'status':'withheld'}}}}
    assert pending_timing_ids(rows,c)==[]
    rows[1]['text']='仍有口述~~部分刪除~~'
    assert pending_timing_ids(rows,c)==['2']
    c['timing_quality']['paragraph_states']={}
    assert pending_timing_ids(rows,c)==['1','2']


def test_beta_publish_command_writes_public_production_only(setup,tmp_path,monkeypatch):
    source,beta,prod,backup=prepare(setup,tmp_path);promotion.promote(beta,prod,ITEM,backup)
    target=ReviewStore(prod);confirmed(target)
    monkeypatch.setenv('TRANSCRIPT_REVIEW_ACTION_SECRET','test-secret')
    app=FastAPI();app.include_router(create_review_router(beta,beta_owner='a',role_root=prod,
        production_root=prod,api_base='/api/sc_api/beta'),prefix='/sc_api/beta');client=TestClient(app)
    doc=target.document('a',ITEM);path='/sc_api/beta/review_document/'+quote(ITEM,safe='')+'/publish'
    packet=op(doc);body=json.dumps(packet).encode();headers=signed('POST',path,body);headers['If-Match']=doc['version']
    response=client.post(path,content=body,headers=headers);assert response.status_code==200,response.text
    published=read(prod/'script_published'/f'{ITEM}.json')
    assert published['metadata']['reviewed_by']=='a'
    assert '/api/sc_api/review_public/' in published['script'][0]['text']
    assert '/api/sc_api/beta/review_public/' not in published['script'][0]['text']
    assert not (beta/'script_published'/f'{ITEM}.json').exists()
    promotion.verify_backup(backup,read(backup/'backup.json'))


def test_corrupt_backup_cannot_be_used_as_migration_authority(setup,tmp_path):
    _,beta,prod,backup=prepare(setup,tmp_path)
    (backup/'production/script_review'/f'{ITEM}.json').write_text('corrupted')
    with pytest.raises(ValueError,match='checksum'):promotion.promote(beta,prod,ITEM,backup)
    assert not (prod/'transcript_review'/ITEM).exists()


def test_fully_deleted_paragraph_can_publish_without_faking_timing_qualification(setup):
    store,root,_=setup;adopted(setup);doc=store.document('a',ITEM);pid=doc['script'][0]['index']
    store.mutate('a',ITEM,op(doc,operations=[{'kind':'text','id':pid,'text':'~~全部刪除~~','content_sha256':doc['paragraph_shas'][pid]}]))
    confirmed(store)
    c=read(store.folder(ITEM)/'context.json')
    c['timing_quality']={'status':'withheld','paragraph_states':{p['index']:{'status':'qualified'} for p in doc['script']}}
    c['timing_quality']['paragraph_states'][pid]={'status':'withheld'}
    atomic(store.folder(ITEM)/'context.json',c);doc=store.document('a',ITEM)
    store.publish('a',ITEM,op(doc))
    assert read(store.folder(ITEM)/'context.json')['timing_quality']['status']=='withheld'
    assert read(root/'script_published'/f'{ITEM}.json')['metadata']['reviewed_by']=='a'
