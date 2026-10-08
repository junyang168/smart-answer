from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_request_encoding import (
    REF, encode_shared_text, decode_shared_text,
)
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_foundation import canonical_json
from backend.pipeline.viewpoint_batch_resolution_runner import CanonicalViewpointProposalResponse
from backend.pipeline.viewpoint_resolution_runtime import call_model


def payload():
    text='教授逐字原文\n空白  ψυχή，限定不可刪。'*80
    return {'claims':[{'claim_id':f'C{i}','source_id':f'S{i}',
            'text':text,'evidence':[text],'null':None,'true':True,'number':1.0}
            for i in range(30)],'distinct':'繁體字','empty':[]}


def test_exact_strings_only_preserve_all_occurrences_types_and_order():
    p=payload();before=deepcopy(p);encoded=encode_shared_text(p)
    assert len(encoded.encode()) < len(canonical_json(p).encode())
    decoded=decode_shared_text(json.loads(encoded))
    assert canonical_json(decoded)==canonical_json(p)
    assert p==before
    assert [c['claim_id'] for c in decoded['claims']]==[f'C{i}' for i in range(30)]
    assert decoded['claims'][0]['text']==decoded['claims'][29]['text']
    assert type(decoded['claims'][0]['number']) is float
    assert type(decoded['claims'][0]['true']) is bool


@pytest.mark.parametrize('mutation',['text','reference','negative','bool','extra','sha','instructions','format','type'])
def test_every_unbound_reference_or_changed_literal_fails(mutation):
    wire=json.loads(encode_shared_text(payload()))
    ref=wire['payload']['claims'][0]['text']
    if mutation=='text':wire['shared_text'][0]+='變更'
    if mutation=='reference':ref[REF]=100
    if mutation=='negative':ref[REF]=-1
    if mutation=='bool':ref[REF]=True
    if mutation=='extra':ref['another']='unknown'
    if mutation=='sha':wire['semantic_payload_sha256']='wrong'
    if mutation=='instructions':wire['instructions']='summarize instead'
    if mutation=='format':wire['format']='unknown'
    if mutation=='type':wire['shared_text'][0]=None
    with pytest.raises(ValueError):decode_shared_text(wire)


def test_plain_fallback_and_reserved_collision_are_safe():
    p={'small':['原文','原文']}
    assert json.loads(encode_shared_text(p))==p
    with pytest.raises(ValueError,match='reserved'):
        encode_shared_text({'original':{REF:0}})
    p=payload();p['claims'][0]['text']+=' '
    assert decode_shared_text(json.loads(encode_shared_text(p)))==p


def adapter(shared):
    calls=[]
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high',
        generate_json=lambda **kw:calls.append(kw) or {})
    a=StructuredJsonReviewerAdapter(client=client,prompt='unchanged prompt',
        response_model=CanonicalViewpointProposalResponse,schema_name='same schema',
        compact_payload=True,shared_text_payload=shared)
    a.max_request_bytes=500000
    return a,calls


def test_actual_wire_count_config_identity_and_immutable_proof(tmp_path):
    a,calls=adapter(True);old,_=adapter(False);p=payload()
    assert a.prompt_sha256==old.prompt_sha256
    assert a.generation_config_sha256!=old.generation_config_sha256
    assert a.compact_copy() is a
    proof=a.request_encoding_proof(p)
    assert proof['wire_request_bytes']==a.request_bytes(p)<proof['original_request_bytes']
    call_model(a,p,tmp_path/'raw-proposal.json')
    assert len(calls)==1
    assert decode_shared_text(json.loads(calls[0]['user_prompt']))==p
    assert calls[0]['system_prompt']=='unchanged prompt'
    assert (tmp_path/'raw-proposal.request-encoding.json').exists()
    assert call_model(a,p,tmp_path/'raw-proposal.json')[1]==0
    assert len(calls)==1


def test_still_stops_oversized_unique_text_before_any_model_call(tmp_path):
    a,calls=adapter(True)
    with pytest.raises(ValueError,match='configured ceiling 500000'):
        call_model(a,{'unique':'原'*200000},tmp_path/'raw-proposal.json')
    assert not calls and not (tmp_path/'raw-proposal.json').exists()


@pytest.mark.parametrize('factory',['build_proposer','build_reviewer','build_consolidator','build_grouper','build_reconsiderer'])
def test_every_cvp_factory_preserves_shared_text_contract(factory):
    from backend.pipeline import viewpoint_batch_resolution_runner as r
    a=getattr(r,factory)('claude-opus-5-5','high')
    assert a._compact_payload and a._shared_text_payload
