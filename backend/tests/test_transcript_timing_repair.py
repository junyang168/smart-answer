import copy,hashlib,json,uuid
from pathlib import Path
import pytest
from backend.api.sc_api.transcript_review import ReviewStore,ReviewError,atomic,digest,file_hash
from backend.pipeline.transcript_timing_repair import apply_timing_repair


@pytest.fixture
def repair(tmp_path):
    root=tmp_path/'data';folder=root/'transcript_review'/'lecture';folder.mkdir(parents=True)
    (folder/'source.mp4').write_bytes(b'audio-source')
    atomic(folder/'source-record.json',{'duration_ms':10000})
    rows=[{'index':'1','text':'人工校對文字','type':'content'}]
    binding={'source_paragraph_id':'p1','start_ms':1000,'end_ms':9000}
    context={'revision':1,'script_sha256':digest(rows),'source_video_sha256':file_hash(folder/'source.mp4'),'source_record_sha256':file_hash(folder/'source-record.json'),'paragraph_bindings':{'1':binding},'groups':[],'reviews':{'p:1':{'decision':'confirmed','sha256':digest({'text':rows[0]['text'],**binding}),'actor':'reviewer','time':'original-time'}}}
    atomic(root/'script_review/lecture.json',rows);atomic(folder/'context.json',context)
    atomic(folder/'revisions/00000001.json',{'script':rows,'context':context})
    atomic(root/'config/sermon.json',[{'item':'lecture','assigned_to':'owner','assignment_epoch':'epoch'}])
    atomic(root/'config/config.json',{'users':[{'id':'owner'}],'user_roles':[{'user':'owner','role':'editor'}],'role_permissions':[{'role':'editor','permission':p} for p in ['read_any_item','write_owned_item']]})
    store=ReviewStore(root);doc=store.document('owner','lecture')
    evidence={'schema_version':'paragraph-timing-correction.v1','paragraph_id':'1','source_video_sha256':context['source_video_sha256'],'source_record_sha256':context['source_record_sha256'],'current_text_sha256':hashlib.sha256(rows[0]['text'].encode()).hexdigest(),'before_binding':binding,'after_binding':{**binding,'start_ms':3000}}
    path=tmp_path/'evidence.json';atomic(path,evidence)
    request={'operation_id':uuid.uuid4().hex,'version':doc['version'],'epoch':'epoch','evidence_sha256':file_hash(path),'operations':[{'kind':'paragraph_timing','id':'1'}]}
    return store,doc,evidence,path,request


def test_text_and_human_confirmation_preserved_with_transparent_rebind(repair):
    store,before,evidence,path,request=repair
    result=apply_timing_repair(store,'owner','lecture',request,path)
    after=store.document('owner','lecture')
    assert before['script']==after['script']
    review=after['context']['reviews']['p:1']
    assert {k:review[k] for k in ['decision','actor','time']}=={k:before['context']['reviews']['p:1'][k] for k in ['decision','actor','time']}
    assert review['sha256']==after['paragraph_shas']['1']
    assert after['context']['timing_repairs'][0]['new_human_confirmation'] is False
    assert apply_timing_repair(store,'owner','lecture',request,path)==result


@pytest.mark.parametrize('fault',['version','epoch','text','source','evidence','out_of_range','source_identity'])
def test_fail_closed(repair,fault):
    store,before,evidence,path,request=repair
    if fault=='version':request['version']='stale'
    elif fault=='epoch':request['epoch']='wrong'
    elif fault=='text':evidence['current_text_sha256']='different'
    elif fault=='source':(store.folder('lecture')/'source.mp4').write_bytes(b'changed')
    elif fault=='evidence':request['evidence_sha256']='wrong'
    elif fault=='out_of_range':evidence['after_binding']['end_ms']=11000
    elif fault=='source_identity':evidence['after_binding']['source_paragraph_id']='other'
    if fault in ['text','out_of_range','source_identity']:
        atomic(path,evidence);request['evidence_sha256']=file_hash(path)
    with pytest.raises(ReviewError):apply_timing_repair(store,'owner','lecture',request,path)
    assert store.document('owner','lecture')['script']==before['script']
    assert store.document('owner','lecture')['context']==before['context']


def test_stale_review_is_not_revived(repair):
    store,before,evidence,path,request=repair
    context=copy.deepcopy(before['context']);context['reviews']['p:1']['sha256']='stale-review'
    atomic(store.folder('lecture')/'context.json',context)
    request['version']=store.document('owner','lecture')['version']
    apply_timing_repair(store,'owner','lecture',request,path)
    after=store.document('owner','lecture')
    assert after['context']['reviews']['p:1']['sha256']=='stale-review'
    assert not after['context']['timing_repairs'][0]['existing_review_hash_rebound']


def test_saved_p10_padding_counterexample_requires_acoustic_retry():
    from backend.pipeline.full_video_transcript import leading_padding_match
    clip={'source_origin_sample':3163680}
    assert leading_padding_match({'start_ms':197730},clip,200730,199640)
    assert not leading_padding_match({'start_ms':200680},clip,200730,199640)
    assert not leading_padding_match({'start_ms':197730},clip,197730,197000)
    assert not leading_padding_match({'start_ms':197730},clip,200730,197000)


def test_batch_validation_is_atomic_and_quality_binds_postimage(repair):
    store,before,evidence,path,request=repair
    second={**evidence,'paragraph_id':'missing'}
    batch={'schema_version':'paragraph-timing-corrections.v1',
           'source_video_sha256':evidence['source_video_sha256'],
           'source_record_sha256':evidence['source_record_sha256'],
           'entries':[evidence,second]}
    request['operations']=[{'kind':'paragraph_timing','id':key} for key in ['1','missing']]
    atomic(path,batch);request['evidence_sha256']=file_hash(path)
    with pytest.raises(ReviewError):apply_timing_repair(store,'owner','lecture',request,path)
    assert store.document('owner','lecture')['context']==before['context']
    batch['entries']=[evidence];request['operations']=[{'kind':'paragraph_timing','id':'1'}]
    batch['quality_audit']={'status':'withheld','paragraph_states':{'1':{'status':'withheld','binding_sha256':digest(evidence['before_binding'])}}}
    atomic(path,batch);request['evidence_sha256']=file_hash(path)
    with pytest.raises(ReviewError):apply_timing_repair(store,'owner','lecture',request,path)
    assert store.document('owner','lecture')['context']==before['context']
    batch['quality_audit']['paragraph_states']['1']['binding_sha256']=digest(evidence['after_binding'])
    atomic(path,batch);request['evidence_sha256']=file_hash(path)
    apply_timing_repair(store,'owner','lecture',request,path)
    assert store.document('owner','lecture')['context']['timing_quality']['status']=='withheld'
