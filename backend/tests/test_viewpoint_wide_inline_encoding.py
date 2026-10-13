import hashlib
import json
import os
from pathlib import Path
from contextvars import ContextVar
import pytest
from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_request_encoding import (
    encode_shared_shapes_v5, encode_shared_shapes_v6, decode_shared_shapes_v6)
from backend.tests.test_viewpoint_inline_piece_encoding import inline_material
from backend.tests.test_viewpoint_string_piece_encoding import adapter


def test_roundtrip_old_bytes_and_new_identity():
    p=inline_material();p['escapes']=['~','~~','~00','~\\',True,None,{},[],42]
    old=encode_shared_shapes_v5(p);wire=json.loads(encode_shared_shapes_v6(p))
    assert canonical_json(decode_shared_shapes_v6(wire))==canonical_json(p)
    assert encode_shared_shapes_v5(p)==old
    a,_=adapter();v5=a.shared_shapes_v5_copy();v6=a.shared_shapes_v6_copy()
    assert v5.generation_config_sha256!=v6.generation_config_sha256
    assert (v5.model_id,v5.backend,v5.prompt_sha256)==(v6.model_id,v6.backend,v6.prompt_sha256)
    assert v6.request_encoding_proof(p)['exact_roundtrip_verified']


@pytest.mark.parametrize('fault',['sha','instruction','extra','duplicate','overflow','foreign','bool','missing','index','escape','type'])
def test_corruption_refused(fault):
    w=json.loads(encode_shared_shapes_v6(inline_material()))
    if fault=='sha':w['semantic_payload_sha256']='0'*64
    elif fault=='instruction':w['instructions']+='x'
    elif fault=='extra':w['extra']=True
    elif fault=='duplicate':w['shared_inline']*=2
    elif fault=='overflow':w['shared_inline']*=100
    elif fault=='foreign':w['shared_inline']=['foreign']
    elif fault=='bool':w['shared_inline']=[True]
    elif fault=='missing':del w['encoded']
    elif fault=='index':w['encoded']={'$j':'~zz'}
    elif fault=='escape':w['encoded']={'$j':'~'}
    else:w['encoded']={'$j':True}
    with pytest.raises((ValueError,KeyError)):decode_shared_shapes_v6(w)


@pytest.mark.parametrize('marker',['call-intent','request-encoding'])
def test_unknown_never_reencoded(tmp_path,marker):
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    a,calls=adapter();a.max_request_bytes=1
    path=tmp_path/f'raw.{marker}.json';path.write_text('unknown')
    with pytest.raises(ValueError):call_model(a,inline_material(),tmp_path/'raw.json')
    assert not calls and path.read_text()=='unknown'


def test_saved_exact_v6_and_unique_body_budget(tmp_path):
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    a,calls=adapter();v6=a.shared_shapes_v6_copy();p=inline_material()
    a.max_request_bytes=1000000
    raw=tmp_path/'raw.json'
    assert call_model(v6,p,raw)[1]==1
    assert call_model(a,p,raw)[1]==0 and len(calls)==1
    a.max_request_bytes=600000
    with pytest.raises(ValueError):call_model(a,{'body':'字'*210000},tmp_path/'unique.json')
    assert len(calls)==1 and not (tmp_path/'unique.call-intent.json').exists()
    with pytest.raises(ValueError):encode_shared_shapes_v6({'nested':{'$j':'literal'}})


def wide_material():
    return {'sources':[''.join(''.join(chr(0x4e00+((i+n*j*17)%240)*4+k)
                                  for k in range(4))+f'/{j}:{n}/' for n in range(10))
                       for i in range(240) for j in range(6)]}


def test_unused_normal_stage_selection_and_exact_cache(tmp_path):
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    a,calls=adapter();p=wide_material();v6=a.shared_shapes_v6_copy()
    a.max_request_bytes=v6.request_bytes(p)
    assert a.shared_shapes_v5_copy().request_bytes(p)>a.max_request_bytes
    assert call_model(a,p,tmp_path/'raw.json')[1]==1
    assert call_model(a,p,tmp_path/'raw.json')[1]==0 and len(calls)==1
    assert json.loads(calls[0]['user_prompt'])['format']=='wkp_shared_shapes_json_v6'


def test_source_selection_saved_and_reservation_guard(tmp_path):
    from backend.pipeline import source_reconciliation_runner as r
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.pipeline.viewpoint_resolution_runtime import response_adapter
    a=model_adapter('proposal');p=wide_material();base=response_adapter(a);v6=base.shared_shapes_v6_copy()
    a.max_request_bytes=v6.request_bytes(p)
    chosen=r.stage_adapter(a,p,tmp_path/'proposal.raw.json')
    assert chosen.generation_config_sha256==v6.generation_config_sha256
    saved={k:getattr(v6,k) for k in ('model_id','backend','prompt_sha256','generation_config_sha256')}
    assert r.stage_adapter(a,p,tmp_path/'proposal.raw.json',saved=saved).generation_config_sha256==v6.generation_config_sha256
    (tmp_path/'proposal.reservation.json').write_text('frozen')
    assert r.stage_adapter(a,p,tmp_path/'proposal.raw.json').generation_config_sha256==base.generation_config_sha256


@pytest.mark.skipif(os.getenv('WKP_WIDE_INLINE_ACTUAL')!='1',reason='explicit current SQL readonly saved preflight')
def test_actual_g154_complete_unused_review(monkeypatch):
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline import cvp_sql_flow as f,viewpoint_batch_resolution_runner as r
    from backend.pipeline import viewpoint_resolution_runtime as rt,cvp_batch_identity_integration as bi
    load_dotenv('.env');s=ReadOnly()
    rid='CVPG-33eaa47f42cfcb59b69c35a0b944763dd8adf69e744d30f91686f5ce5109c2a2'
    g=s.get_cvp_group(rid);m=g['materials'];root=Path(g['execution_roots']['group'])/'normal-review'
    def files():return {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before=files();seen=[]
    with s.connect() as c,c.cursor() as cur:s._assert_transaction_read_pins(cur,f.decoded_pins(m['read_pins']))
    class Boundary(Exception):pass
    def call(a,p,path):
        if path.name=='raw-review.json':
            assert all(not path.with_name(path.stem+x).exists() for x in ('.json','.call-intent.json','.request-encoding.json','.request-budget.json'))
            old=rt.response_adapter(a).shared_shapes_v5_copy();new=old.shared_shapes_v6_copy()
            proof=new.request_encoding_proof(p)
            print('ACTUAL_G154',old.request_bytes(p),new.request_bytes(p),json.dumps(proof))
            assert old.request_bytes(p)==617721
            assert proof['wire_request_bytes']<600000 and proof['exact_roundtrip_verified']
            assert proof['semantic_payload_sha256']==proof['restored_payload_sha256']
            assert len(p['packet']['claims'])==len(m['packet']['claims'])
            assert len(p['packet']['retained_existing_claim_links'])==len(m['packet']['existing_links'])
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
