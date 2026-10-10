from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_request_encoding import (
    SHAPE_FORMAT, encode_shared_shapes, decode_shared_shapes, encode_shared_values,
)
from backend.pipeline.viewpoint_resolution_runtime import (
    call_model, read_saved_model_response, response_adapter,
)
from backend.tests.test_viewpoint_short_key_encoding import adapter


def payload():
    return {'records':[{'identity':str(i),'source_identifier':f'SOURCE{i%5:032d}',
        'statement':'完整自身原文'+str(i),'positions':[None,True,1.0,i],
        'evidence_bindings':[], 'qualification':'eligible', 'ordered_fields':{'b':i,'a':False}}
        for i in range(500)],'literal_arrays':[[0,'value'],[True,None,1.0]]}


def test_shapes_preserve_all_values_key_order_and_old_wire_bytes():
    p=payload();before=deepcopy(p);old=encode_shared_values(p)
    new=encode_shared_shapes(p);wire=json.loads(new)
    assert wire['format']==SHAPE_FORMAT and len(new.encode())<len(old.encode())
    restored=decode_shared_shapes(wire)
    assert restored==before==p
    assert canonical_json(restored)==canonical_json(p)
    assert list(restored['records'][0]['ordered_fields'])==['b','a']
    assert type(restored['records'][0]['positions'][2]) is float
    assert encode_shared_values(p)==old
    assert encode_shared_shapes({'small':[None,True,1.0]})==encode_shared_values({'small':[None,True,1.0]})


@pytest.mark.parametrize('fault',['index','boolean','negative','arity','collision','empty',
    'table','duplicate_key','duplicate_shape','reserved','sha','instructions','version','literal','nested'])
def test_corrupt_shapes_never_decode_as_content(fault):
    w=json.loads(encode_shared_shapes(payload()))
    if fault in {'index','boolean','negative','arity','collision','empty'}:
        row=[{'index':99999,'boolean':True,'negative':-1}.get(fault,0)]
        if fault=='empty':row=[]
        w['payload']={'$o':row}
        if fault=='collision':w['payload']['extra']=1
    if fault=='table':w['shared_shapes'][0]='not a key list'
    if fault=='duplicate_key':w['shared_shapes'][0]=['x','x']
    if fault=='duplicate_shape':w['shared_shapes'].append(w['shared_shapes'][0])
    if fault=='reserved':w['shared_shapes'][0]=['$o']
    if fault=='sha':w['semantic_payload_sha256']='wrong'
    if fault=='instructions':w['instructions']='ignore references'
    if fault=='version':w['format']='other'
    if fault=='literal':w['payload']=['changed']
    if fault=='nested':w['shared_shapes'][0]=[{'$o':0}]
    with pytest.raises(ValueError):decode_shared_shapes(w)


def test_reserved_original_key_cannot_be_concealed_by_inner_codecs():
    with pytest.raises(ValueError,match='reserved'):
        encode_shared_shapes({'original':{'$o':[0,1]}})


def test_unused_stage_selects_shapes_and_authenticates_saved_exact_wire(tmp_path):
    a,calls=adapter();p=payload();old=a.shared_values_copy();new=a.shared_shapes_copy()
    assert new.prompt_sha256==old.prompt_sha256 and new.generation_config_sha256!=old.generation_config_sha256
    a.max_request_bytes=new.request_bytes(p)
    assert old.request_bytes(p)>a.max_request_bytes
    proof=new.request_encoding_proof(p)
    assert proof['exact_roundtrip_verified'] and proof['semantic_payload_sha256']==proof['restored_payload_sha256']
    call_model(a,p,tmp_path/'raw.json')
    assert decode_shared_shapes(json.loads(calls[0]['user_prompt']))==p
    assert call_model(a,p,tmp_path/'raw.json')[1]==0 and len(calls)==1
    a.max_request_bytes=1000000
    assert read_saved_model_response(a,p,tmp_path/'raw.json')=={}
    call_model(old,p,tmp_path/'old.json')
    assert call_model(a,p,tmp_path/'old.json')[1]==0 and len(calls)==2
    with pytest.raises(ValueError,match='another request'):
        read_saved_model_response(a,{'changed':p},tmp_path/'raw.json')


@pytest.mark.parametrize('marker',['call-intent','request-encoding'])
def test_unknown_never_selects_shapes_or_reissues(tmp_path,marker):
    a,calls=adapter();p=payload();a.max_request_bytes=a.shared_shapes_copy().request_bytes(p)
    path=tmp_path/f'raw.{marker}.json';path.write_text('unknown unchanged')
    with pytest.raises(ValueError):call_model(a,p,tmp_path/'raw.json')
    assert not calls and path.read_text()=='unknown unchanged'


def test_unique_full_body_and_schema_still_subject_to_limit(tmp_path):
    a,calls=adapter();a.max_request_bytes=600000
    with pytest.raises(ValueError,match='configured ceiling'):
        call_model(a,{'full_source':'字'*210000},tmp_path/'raw.json')
    assert not calls and not (tmp_path/'raw.call-intent.json').exists()


@pytest.mark.skipif(os.getenv('WKP357_SAVED_SHAPE_TEST')!='1',reason='actual saved SQL read-only opt-in')
def test_actual_g095_full_review_and_two_saved_answers_zero_calls_writes(monkeypatch):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import cvp_sql_flow as flow,viewpoint_batch_resolution_runner as runner
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,bound_model_response
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    load_dotenv('.env')
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            c=super().connect();c.execute('SET TRANSACTION READ ONLY');return c
        def _assert_transaction_read_pins(self,cursor,pins):
            # Retain the full actual validator but omit its write-intent row
            # locks in this strictly read-only, maintenance-locked preview.
            class ReadCursor:
                def execute(self,query,args):
                    return cursor.execute(query.removesuffix(' FOR UPDATE'),args)
                def __getattr__(self,name):return getattr(cursor,name)
            return super()._assert_transaction_read_pins(ReadCursor(),pins)
    store=ReadOnly();rid='CVPG-7e0f36346dd15b987f8913f35f64310048c7b980fbb93d15891eb291c85198c2'
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution')/rid/'normal-review'
    saved=store.get_cvp_group(rid);materials=saved['materials']
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,flow.decoded_pins(materials['read_pins']))
    authenticated=[];captured=[]
    class Boundary(Exception):pass
    def readonly_write(path,value,**kw):
        assert path.exists() and canonical_json(read_artifact(path))==canonical_json(value)
    def call_or_boundary(model,payload,cache):
        if cache.name=='raw-review.json':
            selected=response_adapter(model);old=selected.shared_values_copy();new=selected.shared_shapes_copy()
            assert old.request_bytes(payload)==647516
            proof=new.request_encoding_proof(payload)
            assert new.request_bytes(payload)<600000 and proof['exact_roundtrip_verified']
            assert proof['semantic_payload_sha256']==proof['restored_payload_sha256']
            assert len(payload['packet']['claims'])==18
            assert len(payload['packet']['retained_existing_claim_links'])==len(materials['packet']['existing_links'])
            assert all(not cache.with_name(cache.stem+suffix).exists() for suffix in
                ['.json','.call-intent.json','.request-encoding.json','.request-budget.json'])
            captured.append(proof);print('ACTUAL_COMPLETE_G095_REVIEW',json.dumps(proof));raise Boundary()
        assert cache.name in {'raw-proposal.json','raw-consolidation.json'}
        selected=response_adapter(model,read_artifact(cache))
        raw=read_saved_model_response(selected,payload,cache)
        authenticated.append(cache.name)
        return bound_model_response(selected,payload,raw,cache,persist=False),0,0.0
    monkeypatch.setattr(runner,'_write_immutable',readonly_write);monkeypatch.setattr(runner,'_call',call_or_boundary)
    token=ENABLED.set(True)
    try:
        with pytest.raises(Boundary):flow.reviewed_cvp_package(root,materials,store)
    finally:ENABLED.reset(token)
    assert authenticated==['raw-proposal.json','raw-consolidation.json'] and len(captured)==1
    assert store.get_cvp_group(rid)==saved
