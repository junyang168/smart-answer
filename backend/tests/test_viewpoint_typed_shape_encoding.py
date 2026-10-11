from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_request_encoding import (
    SHAPE_FORMAT_V2, encode_shared_shapes, encode_shared_shapes_v2,
    decode_shared_shapes_v2,
)
from backend.tests.test_viewpoint_shared_shape_encoding import payload
from backend.tests.test_viewpoint_short_key_encoding import adapter
from backend.pipeline.viewpoint_resolution_runtime import call_model, read_saved_model_response


def test_typed_shapes_exact_arrays_objects_scalar_types_and_old_bytes():
    p=payload();p['arrays']=[[],[0,1],[-1,2],[True,None,1.0],{'literal':[]}]
    before=deepcopy(p);old=encode_shared_shapes(p);new=encode_shared_shapes_v2(p)
    assert len(new.encode())<len(old.encode())
    w=json.loads(new);assert w['format']==SHAPE_FORMAT_V2
    restored=decode_shared_shapes_v2(w)
    assert restored==p==before and canonical_json(restored)==canonical_json(p)
    assert list(restored['records'][0]['ordered_fields'])==['b','a']
    assert type(restored['arrays'][3][2]) is float
    assert encode_shared_shapes(p)==old


@pytest.mark.parametrize('fault',['empty','boolean','negative','index','arity','table',
    'duplicate_shape','duplicate_key','reserved','literal','sha','instructions','version'])
def test_corrupt_typed_shapes_refuse(fault):
    w=json.loads(encode_shared_shapes_v2(payload()))
    if fault=='empty':w['payload']=[]
    if fault=='boolean':w['payload']=[True]
    if fault=='negative':w['payload']=[-2]
    if fault=='index':w['payload']=[999999]
    if fault=='arity':w['payload']=[0]
    if fault=='table':w['shared_shapes'][0]='not list'
    if fault=='duplicate_shape':w['shared_shapes'].append(w['shared_shapes'][0])
    if fault=='duplicate_key':w['shared_shapes'][0]=['x','x']
    if fault=='reserved':w['payload']={'$o':[0]}
    if fault=='literal':w['payload']=['not tagged']
    if fault=='sha':w['semantic_payload_sha256']='wrong'
    if fault=='instructions':w['instructions']='changed'
    if fault=='version':w['format']='foreign'
    with pytest.raises(ValueError):decode_shared_shapes_v2(w)


def test_unused_stage_and_exact_saved_new_and_legacy_identities(tmp_path):
    a,calls=adapter();p=payload();old=a.shared_shapes_copy();new=a.shared_shapes_v2_copy()
    assert new.prompt_sha256==old.prompt_sha256
    assert new.generation_config_sha256!=old.generation_config_sha256
    a.max_request_bytes=new.request_bytes(p)
    assert old.request_bytes(p)>a.max_request_bytes
    assert new.request_encoding_proof(p)['exact_roundtrip_verified']
    call_model(a,p,tmp_path/'raw.json')
    assert decode_shared_shapes_v2(json.loads(calls[0]['user_prompt']))==p
    assert call_model(a,p,tmp_path/'raw.json')[1]==0 and len(calls)==1
    a.max_request_bytes=1000000
    assert read_saved_model_response(a,p,tmp_path/'raw.json')=={}
    call_model(old,p,tmp_path/'legacy.json')
    assert call_model(a,p,tmp_path/'legacy.json')[1]==0 and len(calls)==2
    with pytest.raises(ValueError):read_saved_model_response(a,{'changed':p},tmp_path/'raw.json')


@pytest.mark.parametrize('marker',['call-intent','request-encoding'])
def test_unknown_preserved_without_codec_selection_or_call(tmp_path,marker):
    a,calls=adapter();p=payload();a.max_request_bytes=a.shared_shapes_v2_copy().request_bytes(p)
    marker_path=tmp_path/f'raw.{marker}.json';marker_path.write_text('unknown unchanged')
    with pytest.raises(ValueError):call_model(a,p,tmp_path/'raw.json')
    assert not calls and marker_path.read_text()=='unknown unchanged'


def test_unique_body_still_exceeds_without_intent(tmp_path):
    a,calls=adapter();a.max_request_bytes=600000
    with pytest.raises(ValueError,match='configured ceiling'):
        call_model(a,{'full_source':'字'*210000},tmp_path/'raw.json')
    assert not calls and not (tmp_path/'raw.call-intent.json').exists()


@pytest.mark.parametrize('fault',['prefix_table','duplicate_prefix','empty_prefix','prefix_boolean',
    'prefix_negative','prefix_missing','prefix_suffix','prefix_arity'])
def test_prefix_corruption_refuses(fault):
    w=json.loads(encode_shared_shapes_v2(payload()))
    if fault=='prefix_table':w['shared_prefixes']='not a list'
    if fault=='duplicate_prefix':w['shared_prefixes']=['same','same']
    if fault=='empty_prefix':w['shared_prefixes']=['']
    if fault=='prefix_boolean':w['payload']=[-2,True,'suffix']
    if fault=='prefix_negative':w['payload']=[-2,-1,'suffix']
    if fault=='prefix_missing':w['payload']=[-2,999999,'suffix']
    if fault=='prefix_suffix':w['payload']=[-2,0,{}]
    if fault=='prefix_arity':w['payload']=[-2,0]
    with pytest.raises(ValueError):decode_shared_shapes_v2(w)


def test_unicode_prefix_and_literal_negative_tag_arrays_preserved():
    p=payload();p['unicode']=['完整自身逐字原文保持相同前綴'*10+str(i) for i in range(200)]
    p['literal']=[[-2,0,'not a reference'],[-1],[],{},[-2,False],['$o']]
    w=json.loads(encode_shared_shapes_v2(p))
    assert any('原文' in prefix for prefix in w['shared_prefixes'])
    assert canonical_json(decode_shared_shapes_v2(w))==canonical_json(p)
    with pytest.raises(ValueError,match='reserved'):
        encode_shared_shapes_v2({'nested':{'$o':[0,1]}})


@pytest.mark.skipif(os.getenv('WKP357_SAVED_TYPED_SHAPE_TEST')!='1',reason='actual saved SQL read-only opt-in')
def test_actual_g118_complete_review_original_answers_no_calls_writes(monkeypatch):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import cvp_sql_flow as flow, viewpoint_batch_resolution_runner as runner
    from backend.pipeline.viewpoint_resolution_runtime import (
        read_artifact, response_adapter, bound_model_response,
    )
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    load_dotenv('.env')
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            c=super().connect();c.execute('SET TRANSACTION READ ONLY');return c
        def _assert_transaction_read_pins(self,cursor,pins):
            class C:
                def execute(self,q,args):return cursor.execute(q.removesuffix(' FOR UPDATE'),args)
                def __getattr__(self,n):return getattr(cursor,n)
            return super()._assert_transaction_read_pins(C(),pins)
    s=ReadOnly();rid='CVPG-b9bdf23b7ec4f63b20fa5d69818109697a8fb808138b18557dffe2e225369031'
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution')/rid/'normal-review'
    saved=s.get_cvp_group(rid);materials=saved['materials']
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    with s.connect() as conn,conn.cursor() as cursor:
        s._assert_transaction_read_pins(cursor,flow.decoded_pins(materials['read_pins']))
    authenticated=[];captured=[]
    class Boundary(Exception):pass
    def readonly_write(path,value,**kw):
        assert path.exists() and canonical_json(read_artifact(path))==canonical_json(value)
    def call_or_boundary(model,p,cache):
        if cache.name=='raw-review.json':
            selected=response_adapter(model);old=selected.shared_shapes_copy();new=selected.shared_shapes_v2_copy()
            assert old.request_bytes(p)==621067
            assert new.request_bytes(p)<600000
            proof=new.request_encoding_proof(p)
            assert proof['exact_roundtrip_verified'] and proof['semantic_payload_sha256']==proof['restored_payload_sha256']
            assert len(p['packet']['claims'])==len(materials['packet']['claims'])
            assert len(p['packet']['retained_existing_claim_links'])==len(materials['packet']['existing_links'])
            assert all(not cache.with_name(cache.stem+suffix).exists() for suffix in
                ['.json','.call-intent.json','.request-encoding.json','.request-budget.json'])
            captured.append(proof);print('ACTUAL_COMPLETE_G118_REVIEW',json.dumps(proof));raise Boundary()
        assert cache.name in {'raw-proposal.json','raw-consolidation.json'}
        selected=response_adapter(model,read_artifact(cache));raw=read_saved_model_response(selected,p,cache)
        authenticated.append(cache.name)
        return bound_model_response(selected,p,raw,cache,persist=False),0,0.0
    monkeypatch.setattr(runner,'_write_immutable',readonly_write);monkeypatch.setattr(runner,'_call',call_or_boundary)
    token=ENABLED.set(True)
    try:
        with pytest.raises(Boundary):flow.reviewed_cvp_package(root,materials,s)
    finally:ENABLED.reset(token)
    assert authenticated==['raw-proposal.json','raw-consolidation.json'] and len(captured)==1
    assert s.get_cvp_group(rid)==saved
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
