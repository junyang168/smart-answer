from copy import deepcopy
import json
import pytest
from backend.api.canonical_repository.viewpoint_request_encoding import (
    RECORD_REF, RECORD_FORMAT, encode_shared_records, decode_shared_records, encode_shared_text,
)
from backend.api.canonical_repository.viewpoint_foundation import canonical_json


def payload():
    row={'statement':'忠於原文，未行動已有決定。'*30,'source':'S1','anchors':[{'start':12,'end':45,'text':'原句'}]*10}
    return {'targets':[{'claim_id':str(i),'record':deepcopy(row)} for i in range(12)],'before':row,'order':[True,None,1.0,1]}


def test_exact_records_losslessly_restore_every_identity_order_and_type():
    p=payload();old=deepcopy(p);wire=json.loads(encode_shared_records(p))
    assert wire['format']==RECORD_FORMAT
    assert canonical_json(decode_shared_records(wire))==canonical_json(p)
    assert p==old
    assert len(encode_shared_records(p).encode())<len(encode_shared_text(p).encode())
    result=decode_shared_records(wire)
    result['targets'][0]['record']['source']='different'
    assert result['targets'][1]['record']['source']=='S1'


@pytest.mark.parametrize('mutation',['index','negative','bool','extra','table','recursive','sha','instructions','format','type'])
def test_tamper_bad_reference_and_cycles_fail(mutation):
    wire=json.loads(encode_shared_records(payload()))
    wire['payload']['new']={RECORD_REF:0}
    if mutation=='index':wire['payload']['new'][RECORD_REF]=9999
    if mutation=='negative':wire['payload']['new'][RECORD_REF]=-1
    if mutation=='bool':wire['payload']['new'][RECORD_REF]=True
    if mutation=='extra':wire['payload']['new']['extra']=0
    if mutation=='table':wire['shared_records'][0]={'changed':True}
    if mutation=='recursive':wire['shared_records'][0]={RECORD_REF:0}
    if mutation=='sha':wire['semantic_payload_sha256']='wrong'
    if mutation=='instructions':wire['instructions']='omit text'
    if mutation=='format':wire['format']='unknown'
    if mutation=='type':wire['shared_records'][0]=1
    with pytest.raises(ValueError):decode_shared_records(wire)


def test_reserved_key_and_small_fallback():
    with pytest.raises(ValueError,match='reserved'):encode_shared_records({'literal':{RECORD_REF:0}})
    assert json.loads(encode_shared_records({'small':[],'a':1}))=={'small':[],'a':1}


def test_adapter_transport_proof_new_identity_without_changing_old(tmp_path):
    from types import SimpleNamespace
    from backend.pipeline.viewpoint_g037_claim_statement_repair import review_adapter,PROMPT
    from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    calls=[]
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high',generate_json=lambda **kw:calls.append(kw) or {})
    old=link_review_adapter(client,PROMPT);a=review_adapter(client);p=payload()
    assert a.prompt_sha256==old.prompt_sha256
    assert a.generation_config_sha256!=old.generation_config_sha256
    proof=a.request_encoding_proof(p)
    assert proof['exact_roundtrip_verified'] and proof['wire_request_bytes']==a.request_bytes(p)
    call_model(a,p,tmp_path/'raw.json')
    assert decode_shared_records(json.loads(calls[0]['user_prompt']))==p
    assert calls[0]['json_schema']['name']=='audit_link_repair_review_v1'
    assert call_model(a,p,tmp_path/'raw.json')[1]==0 and len(calls)==1
    assert not old._shared_record_payload


def test_unique_oversize_still_stops_without_call(tmp_path):
    from types import SimpleNamespace
    from backend.pipeline.viewpoint_g037_claim_statement_repair import review_adapter
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    calls=[]
    a=review_adapter(SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high',generate_json=lambda **kw:calls.append(kw)))
    with pytest.raises(ValueError,match='configured ceiling'):call_model(a,{'unique':'字'*200000},tmp_path/'raw.json')
    assert not calls
