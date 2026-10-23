import hashlib
import json
import os
from pathlib import Path
from copy import deepcopy
from contextvars import ContextVar
import pytest
from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_request_encoding import (
    encode_shared_shapes_v3,encode_shared_shapes_v4,decode_shared_shapes_v4,SHAPE_FORMAT_V4)
from backend.tests.test_viewpoint_string_piece_encoding import material,adapter


def test_compact_pieces_exact_and_legacy_immutable():
    p=material();p['types']=[True,False,None,0,-1,[],[3,'x'],{'x':[]}]
    before=deepcopy(p);old=encode_shared_shapes_v3(p);new=encode_shared_shapes_v4(p)
    assert len(new.encode())<len(old.encode())
    assert canonical_json(decode_shared_shapes_v4(json.loads(new)))==canonical_json(p)
    assert p==before and encode_shared_shapes_v3(p)==old
    a,_=adapter();v3=a.shared_shapes_v3_copy();v4=a.shared_shapes_v4_copy()
    assert v4.generation_config_sha256!=v3.generation_config_sha256
    assert (v4.model_id,v4.backend,v4.prompt_sha256)==(v3.model_id,v3.backend,v3.prompt_sha256)
    assert v4.request_encoding_proof(p)['exact_roundtrip_verified']


@pytest.mark.parametrize('fault',['format','instruction','sha','table_duplicate','boolean','negative','outside','empty','mixed'])
def test_compact_ref_corruption_refused(fault):
    w=json.loads(encode_shared_shapes_v4(material()));assert w['format']==SHAPE_FORMAT_V4
    if fault=='format':w['format']='foreign'
    elif fault=='instruction':w['instructions']+='x'
    elif fault=='sha':w['semantic_payload_sha256']='0'*64
    elif fault=='table_duplicate':w['shared_pieces'].append(w['shared_pieces'][0])
    else:
        w['encoded']={'':{'boolean':True,'negative':-1,'outside':len(w['shared_pieces']),'empty':[],'mixed':[False]}[fault]}
    with pytest.raises(ValueError):decode_shared_shapes_v4(w)


def test_reserved_and_unique_data_still_budget_bound():
    with pytest.raises(ValueError,match='reserved'):encode_shared_shapes_v4({'x':{'':'data'}})
    a,_=adapter();p={'unique':''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(1000))}
    assert a.shared_shapes_v4_copy().request_bytes(p)>10000


def test_runtime_select_cache_and_unknown(tmp_path):
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    a,calls=adapter();p=material();a.max_request_bytes=a.shared_shapes_v4_copy().request_bytes(p)
    cache=tmp_path/'raw.json'
    _,n,_=call_model(a,p,cache);assert n==1
    _,n,_=call_model(a,p,cache);assert n==0 and len(calls)==1
    raw=json.loads(cache.read_text());assert raw['generation_config_sha256']==a.shared_shapes_v4_copy().generation_config_sha256
    other=tmp_path/'unknown.json';other.with_name('unknown.call-intent.json').write_text('{}')
    with pytest.raises(ValueError):call_model(a,p,other)
    assert len(calls)==1 and other.with_name('unknown.call-intent.json').read_text()=='{}'


@pytest.mark.skipif(os.getenv('WKP_COMPACT_PIECE_ACTUAL')!='1',reason='explicit current SQL saved readonly opt-in')
def test_actual_complete_review_saved_only(monkeypatch):
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline import cvp_sql_flow as f,viewpoint_batch_resolution_runner as r
    from backend.pipeline import viewpoint_resolution_runtime as rt,cvp_batch_identity_integration as bi
    load_dotenv('.env');s=ReadOnly()
    rid='CVPG-672f2ec1f1cf888927475e8aa6c5d9846b0b8f72fce7a4c95c41541ad426268c'
    g=s.get_cvp_group(rid);m=g['materials'];root=Path(g['execution_roots']['group'])/'normal-review'
    def files():return {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before=files();seen=[]
    with s.connect() as c,c.cursor() as cur:s._assert_transaction_read_pins(cur,f.decoded_pins(m['read_pins']))
    original=r._call
    class Boundary(Exception):pass
    def call(a,p,path):
        if path.name=='raw-review.json':
            assert all(not path.with_name(path.stem+x).exists() for x in ('.json','.call-intent.json','.request-encoding.json','.request-budget.json'))
            old=rt.response_adapter(a).shared_shapes_v3_copy();new=old.shared_shapes_v4_copy()
            assert old.request_bytes(p)==603198
            proof=new.request_encoding_proof(p)
            assert proof['wire_request_bytes']<600000 and proof['exact_roundtrip_verified']
            assert proof['semantic_payload_sha256']==proof['restored_payload_sha256']
            assert len(p['packet']['claims'])==len(m['packet']['claims'])
            print('ACTUAL_FULL_REVIEW',json.dumps(proof));raise Boundary()
        seen.append(path.name);return original(a,p,path)
    monkeypatch.setattr(bi,'ENABLED',ContextVar('readonly_integration',default=True))
    monkeypatch.setattr(r,'_call',call)
    for module,name in ((r,'_write_immutable'),(r,'_write_derived'),(rt,'write_immutable')):
        monkeypatch.setattr(module,name,lambda *a,**kw:None)
    with rt.authorized_request_limit(root.parents[1],g['campaign_sha256'],root.parents[2]/'owner-request-limit-600k.json'):
        with pytest.raises(Boundary):f.reviewed_cvp_package(root,m,s)
    assert seen==['raw-proposal.json','raw-consolidation.json']
    assert files()==before and s.get_cvp_group(rid)==g
