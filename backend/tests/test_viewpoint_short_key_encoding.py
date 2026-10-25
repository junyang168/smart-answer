from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_foundation import canonical_json,sha256_json
from backend.api.canonical_repository.viewpoint_request_encoding import (
    KEY_FORMAT_V2,encode_shared_keys,encode_shared_keys_v2,decode_shared_keys_v2,
)
from backend.pipeline.viewpoint_resolution_runtime import call_model,read_saved_model_response,response_adapter


def payload():
    return {'claims':[{'claim_id':str(i),'source_id':'S'+str(i),'statement':'原文'+str(i),
        'physical_source_identifier':'S'+str(i),'positions':[None,True,1.0,i]}
        for i in range(500)],'order':[True,None,1.0,1]}


def adapter():
    from backend.pipeline.viewpoint_g037_claim_statement_repair import review_adapter
    calls=[]
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high',
        generate_json=lambda **kw:calls.append(kw) or {})
    return review_adapter(client),calls


def test_new_codec_is_exact_and_does_not_change_v1_bytes():
    p=payload();before=deepcopy(p);old=encode_shared_keys(p)
    assert hashlib.sha256(old.encode()).hexdigest()=='abf17b72d987ba0fd34a9816bb1f9304afcca001f0d32b40f3c3b8223a72b4aa'
    new=encode_shared_keys_v2(p)
    assert json.loads(new)['format']==KEY_FORMAT_V2
    assert canonical_json(decode_shared_keys_v2(json.loads(new)))==canonical_json(p)
    assert p==before and encode_shared_keys(p)==old
    assert len(new.encode())<len(old.encode())
    assert json.loads(encode_shared_keys_v2({'small':[None,True,1.0]}))=={'small':[None,True,1.0]}


@pytest.mark.parametrize('fault',['index','leading_zero','negative','collision','table','sha','instructions','version','literal'])
def test_short_codec_rejects_unbound_or_ambiguous_content(fault):
    w=json.loads(encode_shared_keys_v2(payload()))
    if fault=='index':w['payload']['@99999']=1
    if fault=='leading_zero':w['payload']['@00']=1
    if fault=='negative':w['payload']['@-1']=1
    if fault=='collision':w['payload']['@0']=1;w['payload'][w['shared_keys'][0]]=2
    if fault=='table':w['shared_keys'].append(w['shared_keys'][0])
    if fault=='sha':w['semantic_payload_sha256']='wrong'
    if fault=='instructions':w['instructions']='omit evidence'
    if fault=='version':w['format']='wkp_shared_keys_json_v1'
    if fault=='literal':w['payload']['order'][0]=False
    with pytest.raises(ValueError):decode_shared_keys_v2(w)


def test_reserved_key_prefix_is_not_silently_reinterpreted():
    with pytest.raises(ValueError,match='reserved'):encode_shared_keys_v2({'original':{'@literal':1}})


def test_budget_selects_v2_once_and_saved_v1_and_v2_keep_exact_identity(tmp_path):
    a,calls=adapter();p=payload();v1=a.shared_keys_copy();v2=a.shared_keys_v2_copy()
    assert v1.prompt_sha256==v2.prompt_sha256==a.prompt_sha256
    assert len({a.generation_config_sha256,v1.generation_config_sha256,v2.generation_config_sha256})==3
    a.max_request_bytes=v2.request_bytes(p)
    assert v1.request_bytes(p)>a.max_request_bytes
    call_model(a,p,tmp_path/'v2.json')
    assert decode_shared_keys_v2(json.loads(calls[0]['user_prompt']))==p
    assert call_model(a,p,tmp_path/'v2.json')[1]==0 and len(calls)==1
    # Restore under a higher limit must authenticate the actual saved codec.
    a.max_request_bytes=1000000
    assert read_saved_model_response(a,p,tmp_path/'v2.json')=={}
    call_model(v1,p,tmp_path/'v1.json')
    assert call_model(a,p,tmp_path/'v1.json')[1]==0 and len(calls)==2


@pytest.mark.parametrize('marker',['call-intent','request-encoding'])
def test_unknown_stage_is_never_reencoded_or_reissued(tmp_path,marker):
    a,calls=adapter();p=payload();a.max_request_bytes=a.shared_keys_v2_copy().request_bytes(p)
    path=tmp_path/f'raw.{marker}.json';path.write_text('original unknown')
    with pytest.raises(ValueError,match='configured ceiling'):call_model(a,p,tmp_path/'raw.json')
    assert path.read_text()=='original unknown' and not calls
    assert not (tmp_path/'raw.json').exists()


def test_codec_cannot_shrink_unique_source_text_to_bypass_ceiling(tmp_path):
    a,calls=adapter();a.max_request_bytes=600000
    with pytest.raises(ValueError,match='configured ceiling'):call_model(a,{'source_body':'字'*210000},tmp_path/'raw.json')
    assert not calls and not (tmp_path/'raw.call-intent.json').exists()


@pytest.mark.skipif(os.getenv('WKP357_SAVED_SHORT_KEY_TEST')!='1',reason='actual saved SQL packet read-only opt-in')
def test_saved_g094_review_request_is_lossless_below_600k_with_zero_calls(monkeypatch):
    from backend.pipeline.cvp_sql_flow import binding
    from backend.pipeline.deferred_claim_cvp_continuation import adapters
    p=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83/required-coverage/CVPA-7f7bf996ebe7b3fc0bb8aca982ca47a14c287aaf644dc42c031f8de9386763c8/model-stages')
    packet=json.loads((p/'batch-packet.json').read_text());prop=json.loads((p/'proposal.json').read_text())
    request={'batch_id':packet['batch_id'],'packet':packet,'proposal_sha256':prop['proposal_sha256'],
        'proposal':prop['proposal'],'revision_dependents':{},'same_batch_identity_integration':{
            'same_batch_groups':[],'sole_correction_used':False,'own_source_bodies':[]}}
    model=adapters({'cvp_policy':binding(Path('backend/pipeline/policies/wang_cvp_resolution_policy_v6.json'))},cached_only=True)['reviewer']
    original=response_adapter(model).shared_keys_copy();short=original.shared_keys_v2_copy()
    assert original.request_bytes(request)==624839
    proof=short.request_encoding_proof(request)
    assert short.request_bytes(request)<600000 and proof['exact_roundtrip_verified']
    assert proof['semantic_payload_sha256']==proof['restored_payload_sha256']=='714b59dd8c0b38cbd965ca051476b2fca0091da627a76f5f0141276454ae0802'
    assert len(packet['claims'])==20 and len(packet['retained_existing_claim_links'])==25
    # The historical request remains reproducible after production saves review.
    # Rebuild through the actual common content compiler, authenticate both
    # saved model inputs, and stop before the never-invoked review boundary.
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import cvp_sql_flow as flow,viewpoint_batch_resolution_runner as runner
    from backend.pipeline.claim_first_management import coverage_context
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,bound_model_response
    load_dotenv('.env');store=PostgresKnowledgeStore()
    rid=p.parents[2].name;aid=p.parent.name
    saved=store.get_cvp_group(rid)
    definition=dict(store.get_cvp_campaign(saved['campaign_sha256']));definition['campaign_sha256']=saved['campaign_sha256']
    group=next(g for g in definition['groups'] if g['group_key']==saved['group_key'])
    resolution=coverage_context(store,saved,group)
    materials=store.get_cvp_action(rid,aid)['materials']
    assert materials['claim_management_resolution']==resolution
    authenticated=[];captured=[]
    class Boundary(Exception):pass
    def readonly_immutable(path,value,**kwargs):
        assert path.exists() and canonical_json(read_artifact(path))==canonical_json(value)
    def saved_or_boundary(model,payload,cache):
        if cache.name=='raw-review.json':
            assert canonical_json(payload)==canonical_json(request)
            captured.append(payload);raise Boundary()
        assert cache.name in {'raw-proposal.json','raw-consolidation.json'} and cache.exists()
        selected=response_adapter(model,read_artifact(cache))
        raw=read_saved_model_response(selected,payload,cache)
        authenticated.append(cache.name)
        return bound_model_response(selected,payload,raw,cache,persist=False),0,0.0
    monkeypatch.setattr(runner,'_write_immutable',readonly_immutable)
    monkeypatch.setattr(runner,'_call',saved_or_boundary)
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    token=ENABLED.set(True)
    try:
        with pytest.raises(Boundary):flow.reviewed_cvp_package(p,materials,store,require_resolved=True)
    finally:ENABLED.reset(token)
    assert authenticated==['raw-proposal.json','raw-consolidation.json'] and len(captured)==1
    assert store.get_cvp_group(rid)==saved
