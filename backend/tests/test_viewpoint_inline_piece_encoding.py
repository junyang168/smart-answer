import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from contextvars import ContextVar
import pytest
from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_request_encoding import (
    encode_shared_shapes_v4,encode_shared_shapes_v5,decode_shared_shapes_v5,SHAPE_FORMAT_V5)
from backend.tests.test_viewpoint_string_piece_encoding import material,adapter


def inline_material():
    p=material();p['source_more']=[f'首部{i}耶穌是基督，聖經原文的意思需要解釋。'+('完整教授教導不是摘要。'*2)+f'尾部{i}~literal~1' for i in range(400)]
    return p


def test_inline_piece_roundtrip_legacy_unchanged():
    p=inline_material();p['types']=[True,False,None,-1,[],[-2,3],{'x':[]}]
    before=deepcopy(p);old=encode_shared_shapes_v4(p);new=encode_shared_shapes_v5(p)
    assert json.loads(new)['format']==SHAPE_FORMAT_V5
    assert len(new.encode())<len(old.encode())
    assert canonical_json(decode_shared_shapes_v5(json.loads(new)))==canonical_json(p)
    assert p==before and encode_shared_shapes_v4(p)==old
    a,_=adapter();v4=a.shared_shapes_v4_copy();v5=a.shared_shapes_v5_copy()
    assert (v5.model_id,v5.backend,v5.prompt_sha256)==(v4.model_id,v4.backend,v4.prompt_sha256)
    assert v5.generation_config_sha256!=v4.generation_config_sha256
    assert v5.request_encoding_proof(p)['exact_roundtrip_verified']


@pytest.mark.parametrize('row',[True,None,0,'',{'$c':0},{'$c':True},{'$c':'~'},{'$c':'~\\'},{'$c':'x','extra':1},[{'$c':True}]])
def test_bad_inline_inner_refused(row):
    w=json.loads(encode_shared_shapes_v5(inline_material()))
    w['encoded']=row
    with pytest.raises(ValueError):decode_shared_shapes_v5(w)


@pytest.mark.parametrize('marker',['call-intent','request-encoding'])
def test_unknown_call_never_reencoded_or_reissued(tmp_path,marker):
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    a,calls=adapter();p=inline_material();a.max_request_bytes=a.shared_shapes_v5_copy().request_bytes(p)
    path=tmp_path/f'raw.{marker}.json';path.write_text('original unknown')
    with pytest.raises(ValueError):call_model(a,p,tmp_path/'raw.json')
    assert not calls and path.read_text()=='original unknown'


def test_unused_call_exact_v5_cache_and_legacy_v4(tmp_path):
    from backend.pipeline.viewpoint_resolution_runtime import call_model,read_saved_model_response
    a,calls=adapter();p=inline_material();v4=a.shared_shapes_v4_copy();v5=a.shared_shapes_v5_copy()
    a.max_request_bytes=v5.request_bytes(p);assert v4.request_bytes(p)>a.max_request_bytes
    raw=tmp_path/'raw.json'
    assert call_model(a,p,raw)[1]==1
    assert json.loads(calls[0]['user_prompt'])['format']==SHAPE_FORMAT_V5
    assert call_model(a,p,raw)[1]==0 and len(calls)==1
    assert read_saved_model_response(a,p,raw)=={}
    a.max_request_bytes=1000000
    old=tmp_path/'legacy.json';call_model(v4,p,old)
    assert call_model(a,p,old)[1]==0 and len(calls)==2
    assert json.loads(old.read_text())['generation_config_sha256']==v4.generation_config_sha256


def test_reserved_key_and_unique_body_still_refused(tmp_path):
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    with pytest.raises(ValueError,match='reserved'):encode_shared_shapes_v5({'nested':{'$c':'literal'}})
    a,calls=adapter();a.max_request_bytes=600000
    with pytest.raises(ValueError,match='configured ceiling'):call_model(a,{'body':'字'*210000},tmp_path/'raw.json')
    assert not calls and not (tmp_path/'raw.call-intent.json').exists()


def test_source_stage_v5_identity_and_frozen_markers(tmp_path):
    from backend.pipeline import source_reconciliation_runner as r
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.pipeline.viewpoint_resolution_runtime import response_adapter
    a=model_adapter('proposal');p=inline_material();base=response_adapter(a);v5=base.shared_shapes_v5_copy()
    a.max_request_bytes=v5.request_bytes(p)
    chosen=r.stage_adapter(a,p,tmp_path/'proposal.raw.json')
    assert chosen.generation_config_sha256==v5.generation_config_sha256
    saved={k:getattr(v5,k) for k in ('model_id','backend','prompt_sha256','generation_config_sha256')}
    assert r.stage_adapter(a,p,tmp_path/'proposal.raw.json',saved=saved).generation_config_sha256==v5.generation_config_sha256
    (tmp_path/'proposal.reservation.json').write_text('original frozen')
    assert r.stage_adapter(a,p,tmp_path/'proposal.raw.json').generation_config_sha256==base.generation_config_sha256


@pytest.mark.parametrize('fault',['sha','instruction','format','extra','table_duplicate','table_foreign','table_bool','table_overflow','codes','outside'])
def test_envelope_corruption_refused(fault):
    w=json.loads(encode_shared_shapes_v5(inline_material()))
    if fault=='sha':w['semantic_payload_sha256']='0'*64
    elif fault=='instruction':w['instructions']+='x'
    elif fault=='format':w['format']='foreign'
    elif fault=='extra':w['extra']=[]
    elif fault=='table_duplicate':w['shared_inline']*=2
    elif fault=='table_foreign':w['shared_inline']=['foreign']
    elif fault=='table_bool':w['shared_inline']=[False]
    elif fault=='table_overflow':w['shared_inline']*=100
    elif fault=='codes':w['codes']='foreign'
    else:w['shared_inline']=[];w['encoded']={'$c':'~0'}
    with pytest.raises(ValueError):decode_shared_shapes_v5(w)


@pytest.mark.skipif(os.getenv('WKP_INLINE_PIECE_ACTUAL')!='1',reason='explicit saved current SQL readonly preflight')
def test_actual_g144_complete_review_saved_only(monkeypatch):
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline import cvp_sql_flow as f,viewpoint_batch_resolution_runner as r
    from backend.pipeline import viewpoint_resolution_runtime as rt,cvp_batch_identity_integration as bi
    load_dotenv('.env');s=ReadOnly()
    rid='CVPG-14b93eb94db03e71f7fa82e208b89295ce918f1308403631cb16a4474b62116b'
    g=s.get_cvp_group(rid);m=g['materials'];root=Path(g['execution_roots']['group'])/'normal-review'
    def files():return {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before=files();seen=[]
    with s.connect() as c,c.cursor() as cur:s._assert_transaction_read_pins(cur,f.decoded_pins(m['read_pins']))
    original=r._call
    class Boundary(Exception):pass
    def call(a,p,path):
        if path.name=='raw-review.json':
            assert all(not path.with_name(path.stem+x).exists() for x in ('.json','.call-intent.json','.request-encoding.json','.request-budget.json'))
            old=rt.response_adapter(a).shared_shapes_v4_copy();new=old.shared_shapes_v5_copy()
            assert old.request_bytes(p)==613294
            proof=new.request_encoding_proof(p)
            assert proof['wire_request_bytes']<600000 and proof['exact_roundtrip_verified']
            assert proof['semantic_payload_sha256']==proof['restored_payload_sha256']
            assert len(p['packet']['claims'])==len(m['packet']['claims'])
            assert len(p['packet']['retained_existing_claim_links'])==len(m['packet']['existing_links'])
            print('ACTUAL_INLINE_PIECE_REVIEW',json.dumps(proof))
            raise Boundary()
        selected=rt.response_adapter(a,rt.read_artifact(path));raw=rt.read_saved_model_response(selected,p,path)
        seen.append(path.name);return rt.bound_model_response(selected,p,raw,path,persist=False),0,0.0
    monkeypatch.setattr(bi,'ENABLED',ContextVar('readonly_integration',default=True))
    monkeypatch.setattr(r,'_call',call)
    for module,name in ((r,'_write_immutable'),(r,'_write_derived'),(rt,'write_immutable')):
        monkeypatch.setattr(module,name,lambda *a,**kw:None)
    with rt.authorized_request_limit(root.parents[1],g['campaign_sha256'],root.parents[2]/'owner-request-limit-600k.json'):
        with pytest.raises(Boundary):f.reviewed_cvp_package(root,m,s)
    assert seen==['raw-proposal.json','raw-consolidation.json']
    assert files()==before and s.get_cvp_group(rid)==g
