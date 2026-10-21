from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import pytest
from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_request_encoding import (
    encode_shared_shapes_v2,encode_shared_shapes_v3,decode_shared_shapes_v3,SHAPE_FORMAT_V3)
from backend.tests.test_viewpoint_short_key_encoding import adapter
from backend.pipeline.viewpoint_resolution_runtime import call_model


def material():
    return {'source':[f'{i:04d}: '+('逐字原文共享片段不是摘要、保持來源全部文字與每次出現。'*5)+f'尾部{i}' for i in range(100)],
            'literal':[[],[-2,0,'literal'],True,None,1.25,{}]}


def test_exact_piece_roundtrip_and_legacy_bytes():
    p=material();before=deepcopy(p);old=encode_shared_shapes_v2(p)
    new=encode_shared_shapes_v3(p);assert len(new.encode())<len(old.encode())
    assert json.loads(new)['format']==SHAPE_FORMAT_V3
    assert canonical_json(decode_shared_shapes_v3(json.loads(new)))==canonical_json(p)
    assert p==before and encode_shared_shapes_v2(p)==old


@pytest.mark.parametrize('fault',['sha','instructions','format','duplicate','table','boolean','negative','missing','empty','foreign'])
def test_corrupt_piece_wrapper_refuses(fault):
    w=json.loads(encode_shared_shapes_v3(material()))
    if fault=='sha':w['semantic_payload_sha256']='bad'
    elif fault=='instructions':w['instructions']='bad'
    elif fault=='format':w['format']='bad'
    elif fault=='duplicate':w['shared_pieces']*=2
    elif fault=='table':w['shared_pieces']=[None]
    else:
        refs={'boolean':[True],'negative':[-1],'missing':[999999],'empty':[],'foreign':[{}]}
        w['encoded']={'$p':refs[fault]}
    with pytest.raises((ValueError,AttributeError)):decode_shared_shapes_v3(w)


def test_unused_and_saved_exact_identity(tmp_path):
    a,calls=adapter();p=material();old=a.shared_shapes_v2_copy();new=a.shared_shapes_v3_copy()
    assert old.prompt_sha256==new.prompt_sha256 and old.generation_config_sha256!=new.generation_config_sha256
    a.max_request_bytes=new.request_bytes(p);assert old.request_bytes(p)>a.max_request_bytes
    call_model(a,p,tmp_path/'raw.json');assert len(calls)==1
    assert json.loads(calls[0]['user_prompt'])['format']==SHAPE_FORMAT_V3
    assert call_model(a,p,tmp_path/'raw.json')[1]==0 and len(calls)==1


@pytest.mark.parametrize('marker',['call-intent','request-encoding'])
def test_unknown_never_reencoded(tmp_path,marker):
    a,calls=adapter();p=material();a.max_request_bytes=a.shared_shapes_v3_copy().request_bytes(p)
    path=tmp_path/f'raw.{marker}.json';path.write_text('original unknown')
    with pytest.raises(ValueError):call_model(a,p,tmp_path/'raw.json')
    assert not calls and path.read_text()=='original unknown'


def test_unique_fullbody_remains_over_budget(tmp_path):
    a,calls=adapter();a.max_request_bytes=600000
    with pytest.raises(ValueError,match='configured ceiling'):call_model(a,{'body':'字'*210000},tmp_path/'raw.json')
    assert not calls and not (tmp_path/'raw.call-intent.json').exists()


def test_typed_array_tags_bool_negative_empty_and_reserved_key():
    from backend.tests.test_viewpoint_shared_shape_encoding import payload
    p=payload();p['literal']=[[],[True,0],[-1],[-2,0,'not a reference'],[1.5],{},[0,1]]
    p['substring']=material()['source']
    assert canonical_json(decode_shared_shapes_v3(json.loads(encode_shared_shapes_v3(p))))==canonical_json(p)
    with pytest.raises(ValueError,match='reserved'):encode_shared_shapes_v3({'data':{'$p':['x']}})


@pytest.mark.skipif(os.getenv('WKP357_SAVED_PIECE_TEST')!='1',reason='explicit saved SQL readonly opt-in')
def test_actual_current_g118_required_coverage_review_no_calls_writes(monkeypatch):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import cvp_sql_flow as f,viewpoint_batch_resolution_runner as r
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,response_adapter,read_saved_model_response,bound_model_response
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    load_dotenv('.env')
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            c=super().connect();c.execute('SET TRANSACTION READ ONLY');return c
    s=ReadOnly();rid='CVPG-b9bdf23b7ec4f63b20fa5d69818109697a8fb808138b18557dffe2e225369031'
    aid='CVPA-0286a4a7a882d7a3cc4e1ab02f5336e8d8a130ccf7d3c1fbff73a16d91ff9220'
    action=s.get_cvp_action(rid,aid);group=s.get_cvp_group(rid);m=action['materials']
    with s.connect() as conn,conn.cursor() as cursor:
        class Cursor:
            def execute(self,q,args):return cursor.execute(q.removesuffix(' FOR UPDATE'),args)
            def __getattr__(self,n):return getattr(cursor,n)
        s._assert_transaction_read_pins(Cursor(),f.decoded_pins(m['read_pins']))
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution')/rid/'required-coverage'/aid/'model-stages'
    def files():return {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before=files();seen=[]
    class Boundary(Exception):pass
    monkeypatch.setattr(r,'_write_immutable',lambda *a,**kw:None)
    def call(model,p,cache):
        if cache.name=='raw-review.json':
            old=response_adapter(model).shared_shapes_v2_copy();new=old.shared_shapes_v3_copy()
            assert old.request_bytes(p)==606509
            proof=new.request_encoding_proof(p);assert proof['wire_request_bytes']<600000
            assert proof['exact_roundtrip_verified'] and proof['semantic_payload_sha256']==proof['restored_payload_sha256']
            assert len(p['packet']['claims'])==13
            assert not any(cache.with_name(cache.stem+x).exists() for x in ['.json','.call-intent.json','.request-encoding.json','.request-budget.json'])
            print('ACTUAL_COMPLETE_REVIEW',json.dumps(proof));raise Boundary()
        model=response_adapter(model,read_artifact(cache));raw=read_saved_model_response(model,p,cache);seen.append(cache.name)
        return bound_model_response(model,p,raw,cache,persist=False),0,0.
    monkeypatch.setattr(r,'_call',call);t=ENABLED.set(True)
    try:
        with pytest.raises(Boundary):f.reviewed_cvp_package(root,m,s)
    finally:ENABLED.reset(t)
    assert seen==['raw-proposal.json','raw-consolidation.json']
    assert files()==before and s.get_cvp_group(rid)==group and s.get_cvp_action(rid,aid)==action
