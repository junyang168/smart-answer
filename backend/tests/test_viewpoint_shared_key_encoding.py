from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from backend.api.canonical_repository.viewpoint_request_encoding import (
    KEY_FORMAT,KEY_PREFIX,encode_shared_keys,decode_shared_keys,
)
from backend.api.canonical_repository.viewpoint_foundation import canonical_json


def payload():
    return {'claims':[{'claim_identity_long_field':str(i),'current_revision_number':i,
        'physical_source_identifier':'S'+str(i),'verbatim_source_paragraph_text':'原文'+str(i),
        'evidence_support_eligibility':'eligible','positions':[None,True,1.0,i]}
        for i in range(200)],'order':[True,None,1.0,1]}


def test_lossless_keys_preserve_values_identity_and_order():
    p=payload();before=deepcopy(p);wire=json.loads(encode_shared_keys(p))
    assert wire['format']==KEY_FORMAT
    assert canonical_json(decode_shared_keys(wire))==canonical_json(p)
    assert p==before


@pytest.mark.parametrize('change',['bad_index','leading_zero','duplicate','table','sha','instructions'])
def test_shared_key_tamper_rejected(change):
    wire=json.loads(encode_shared_keys(payload()))
    if change=='bad_index':wire['payload'][KEY_PREFIX+'9999']=1
    elif change=='leading_zero':wire['payload'][KEY_PREFIX+'00']=1
    elif change=='duplicate':
        wire['payload'][KEY_PREFIX+'0']=1;wire['payload'][wire['shared_keys'][0]]=2
    elif change=='table':wire['shared_keys'].append(wire['shared_keys'][0])
    elif change=='sha':wire['semantic_payload_sha256']='wrong'
    else:wire['instructions']='omit sources'
    with pytest.raises(ValueError):decode_shared_keys(wire)


def test_reserved_prefix_and_small_fallback():
    with pytest.raises(ValueError):encode_shared_keys({KEY_PREFIX+'0':1})
    assert json.loads(encode_shared_keys({'small':[1,True,None]}))=={'small':[1,True,None]}


def test_adapter_transport_exact_once_new_config_and_old_unchanged(tmp_path):
    from backend.pipeline.viewpoint_g037_claim_statement_repair import review_adapter
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    calls=[]
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high',
        generate_json=lambda **kw:calls.append(kw) or {})
    old=review_adapter(client);p=payload();original_bytes=old.request_bytes(p)
    new=old.shared_keys_copy()
    assert old.request_bytes(p)==original_bytes and not old._shared_key_payload
    assert new.prompt_sha256==old.prompt_sha256
    assert new.generation_config_sha256!=old.generation_config_sha256
    assert new.request_bytes(p)<original_bytes
    proof=new.request_encoding_proof(p)
    assert proof['exact_roundtrip_verified'] and proof['wire_request_bytes']==new.request_bytes(p)
    call_model(new,p,tmp_path/'raw.json')
    assert decode_shared_keys(json.loads(calls[0]['user_prompt']))==p
    assert call_model(new,p,tmp_path/'raw.json')[1]==0 and len(calls)==1
    with pytest.raises(ValueError):call_model(old,p,tmp_path/'raw.json')


def test_shared_key_codec_does_not_bypass_size_or_unknown_hold(tmp_path):
    from backend.pipeline.viewpoint_g037_claim_statement_repair import review_adapter
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high',
        generate_json=lambda **kw:pytest.fail('no model call'))
    a=review_adapter(client).shared_keys_copy()
    with pytest.raises(ValueError,match='configured ceiling'):
        call_model(a,{'unique':'字'*200000},tmp_path/'large.json')
    (tmp_path/'unknown.call-intent.json').write_text('{}')
    with pytest.raises(ValueError,match='outcome unknown'):
        call_model(a,payload(),tmp_path/'unknown.json')
