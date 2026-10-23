from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.api.canonical_repository.viewpoint_request_encoding import (
    VALUE_FORMAT,encode_shared_values,decode_shared_values,encode_shared_keys_v2,
)
from backend.pipeline.viewpoint_resolution_runtime import call_model,read_saved_model_response,response_adapter
from backend.tests.test_viewpoint_short_key_encoding import adapter


def payload():
    return {'records':[{'id':f'ID{i%5:032d}','source':f'SOURCE{i%3:032d}',
        'value':[None,True,1.0,i],'quote':'独有正文'+str(i)} for i in range(600)]}


def test_exact_values_and_all_occurrences_preserved_without_old_codec_drift():
    p=payload();before=deepcopy(p);old=encode_shared_keys_v2(p)
    new=encode_shared_values(p)
    assert json.loads(new)['format']==VALUE_FORMAT
    assert decode_shared_values(json.loads(new))==p==before
    assert canonical_json(decode_shared_values(json.loads(new)))==canonical_json(p)
    assert len(new.encode())<len(old.encode()) and encode_shared_keys_v2(p)==old
    assert json.loads(encode_shared_values({'small':[None,True,1.0]}))=={'small':[None,True,1.0]}


@pytest.mark.parametrize('fault',['index','boolean','negative','collision','table','sha','instruction','version','literal','nested'])
def test_invalid_value_references_and_envelopes_reject(fault):
    w=json.loads(encode_shared_values(payload()))
    if fault in {'index','boolean','negative'}:w['payload']={'$s':{'index':99999,'boolean':True,'negative':-1}[fault]}
    if fault=='collision':w['payload']={'$s':0,'other':1}
    if fault=='table':w['shared_values'].append(w['shared_values'][0])
    if fault=='sha':w['semantic_payload_sha256']='wrong'
    if fault=='instruction':w['instructions']='ignore evidence'
    if fault=='version':w['format']='other'
    if fault=='literal':w['payload']={'changed':'text'}
    if fault=='nested':w['shared_values'][0]={'$s':0}
    with pytest.raises(ValueError):decode_shared_values(w)


def test_original_reserved_reference_cannot_be_hidden_by_inner_key_encoding():
    with pytest.raises(ValueError,match='reserved'):encode_shared_values({'nested':{'$s':0}})


def test_uninvoked_budget_selection_and_saved_exact_identity(tmp_path):
    a,calls=adapter();p=payload();v2=a.shared_keys_v2_copy();values=a.shared_values_copy()
    a.max_request_bytes=values.request_bytes(p)
    assert v2.request_bytes(p)>a.max_request_bytes
    assert values.prompt_sha256==v2.prompt_sha256 and values.generation_config_sha256!=v2.generation_config_sha256
    call_model(a,p,tmp_path/'raw.json')
    assert decode_shared_values(json.loads(calls[0]['user_prompt']))==p
    assert call_model(a,p,tmp_path/'raw.json')[1]==0 and len(calls)==1
    a.max_request_bytes=1000000
    assert read_saved_model_response(a,p,tmp_path/'raw.json')=={}
    call_model(v2,p,tmp_path/'old.json')
    assert call_model(a,p,tmp_path/'old.json')[1]==0 and len(calls)==2


@pytest.mark.parametrize('marker',['call-intent','request-encoding'])
def test_unknown_never_reencodes_or_reissues(tmp_path,marker):
    a,calls=adapter();p=payload();a.max_request_bytes=a.shared_values_copy().request_bytes(p)
    path=tmp_path/f'raw.{marker}.json';path.write_text('unknown retained')
    with pytest.raises(ValueError):call_model(a,p,tmp_path/'raw.json')
    assert not calls and path.read_text()=='unknown retained'


def test_unique_full_source_still_cannot_bypass_limit(tmp_path):
    a,calls=adapter();a.max_request_bytes=600000
    with pytest.raises(ValueError,match='configured ceiling'):call_model(a,{'full_source':'字'*210000},tmp_path/'raw.json')
    assert not calls and not (tmp_path/'raw.call-intent.json').exists()


@pytest.mark.skipif(os.getenv('WKP357_SAVED_VALUE_TEST')!='1',reason='actual saved SQL read-only opt-in')
def test_actual_g094_complete_correction_and_old_three_calls_authenticated(monkeypatch):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import cvp_sql_flow as flow,viewpoint_batch_resolution_runner as runner
    from backend.pipeline.claim_first_management import coverage_context
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,bound_model_response
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    p=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83/required-coverage/CVPA-7f7bf996ebe7b3fc0bb8aca982ca47a14c287aaf644dc42c031f8de9386763c8/model-stages')
    load_dotenv('.env');store=PostgresKnowledgeStore();rid=p.parents[2].name;aid=p.parent.name
    saved=store.get_cvp_group(rid)
    definition=dict(store.get_cvp_campaign(saved['campaign_sha256']));definition['campaign_sha256']=saved['campaign_sha256']
    group=next(g for g in definition['groups'] if g['group_key']==saved['group_key'])
    materials=store.get_cvp_action(rid,aid)['materials']
    assert coverage_context(store,saved,group)==materials['claim_management_resolution']
    authenticated=[];captured=[]
    class Boundary(Exception):pass
    def readonly_immutable(path,value,**kw):
        assert path.exists() and canonical_json(read_artifact(path))==canonical_json(value)
    def call_or_boundary(model,payload,cache):
        if cache.name=='raw-reconsideration.json':
            selected=response_adapter(model);v2=selected.shared_keys_v2_copy();values=selected.shared_values_copy()
            assert v2.request_bytes(payload)==609445
            proof=values.request_encoding_proof(payload)
            assert values.request_bytes(payload)<600000 and proof['exact_roundtrip_verified']
            assert proof['semantic_payload_sha256']==proof['restored_payload_sha256']
            assert len(payload['packet']['claims'])==20 and len(payload['packet']['retained_existing_claim_links'])==25
            assert all(not cache.with_name(cache.stem+s).exists() for s in ['.json','.call-intent.json','.request-encoding.json','.request-budget.json'])
            captured.append(proof);print('ACTUAL_COMPLETE_CORRECTION',json.dumps(proof));raise Boundary()
        assert cache.name in {'raw-proposal.json','raw-consolidation.json','raw-review.json'}
        selected=response_adapter(model,read_artifact(cache))
        raw=read_saved_model_response(selected,payload,cache)
        authenticated.append(cache.name)
        return bound_model_response(selected,payload,raw,cache,persist=False),0,0.0
    monkeypatch.setattr(runner,'_write_immutable',readonly_immutable);monkeypatch.setattr(runner,'_call',call_or_boundary)
    token=ENABLED.set(True)
    try:
        with pytest.raises(Boundary):flow.reviewed_cvp_package(p,materials,store,require_resolved=True)
    finally:ENABLED.reset(token)
    assert authenticated==['raw-proposal.json','raw-consolidation.json','raw-review.json'] and len(captured)==1
    assert store.get_cvp_group(rid)==saved
