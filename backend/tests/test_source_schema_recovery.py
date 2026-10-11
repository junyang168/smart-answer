import json
from copy import deepcopy
import pytest
from backend.pipeline import source_schema_recovery as m
from backend.pipeline.source_reconciliation_runner import write,body,read
from backend.api.canonical_repository.postgres_store import sha256_json


def files(root):
    packet={'manifest':{'anchor_qualification':{'path':'owner','file_sha256':'0'*64}},'complete_body':['all own paragraphs']}
    request={'model_id':'gpt-6.1-sol','backend':'codex_subscription','request_payload_sha256':sha256_json(packet),
        'request_encoding_proof':{'exact_roundtrip_verified':True}}
    error={'status':400,'error':{'code':'invalid_json_schema','param':'text.format.schema'}}
    failure={'provider_raw':{'stdout':'','last_message':None,'stderr':'ERROR: '+json.dumps(error)}}
    write(root/'proposal.packet.json',packet);write(root/'proposal.reservation.json',request)
    write(root/'proposal.failure.json',failure)
    return packet,request,failure


def test_fixed_parent_once_binding_and_cache(tmp_path):
    packet,_,_=files(tmp_path)
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    assert m.select(tmp_path,packet,persist=True)==m.STAGE
    assert all((tmp_path/name).read_bytes()==value for name,value in before.items())
    write(tmp_path/'initial_review.reservation.json',{'after answered recovery':True})
    assert m.select(tmp_path,packet)==m.STAGE
    assert body(read(tmp_path/m.NAME))['automatic_retry'] is False


@pytest.mark.parametrize('fault',['network','quota','output','last_message','model','request','encoding','initial_started','packet'])
def test_no_ambiguous_call_or_content_retry(tmp_path,fault):
    packet,request,failure=files(tmp_path)
    if fault=='network':failure['provider_raw']['stderr']='network timeout'
    if fault=='quota':failure['provider_raw']['stderr']='ERROR: '+json.dumps({'status':429,'error':{'code':'quota'}})
    if fault=='output':failure['provider_raw']['stdout']='answer part'
    if fault=='last_message':failure['provider_raw']['last_message']='answer'
    if fault=='model':request['model_id']='other'
    if fault=='request':request['request_payload_sha256']='0'*64
    if fault=='encoding':request['request_encoding_proof']['exact_roundtrip_verified']=False
    if fault=='initial_started':write(tmp_path/'initial_review.reservation.json',{})
    write(tmp_path/'proposal.reservation.json',request,immutable=False)
    write(tmp_path/'proposal.failure.json',failure,immutable=False)
    if fault=='packet':packet=deepcopy(packet);packet['complete_body']=[]
    with pytest.raises(ValueError):m.select(tmp_path,packet,persist=True)
    assert not (tmp_path/m.NAME).exists()


def test_new_transport_is_closed_schema_and_roundtrips():
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.pipeline.deferred_claim_repair import decode_proposal
    from backend.api.canonical_repository.viewpoint_resolution import _strict_json_schema
    packet={'manifest':{'anchor_qualification':{'path':'d','file_sha256':'0'*64}}}
    base=model_adapter('proposal');fixed=m.adapter(base,packet,m.STAGE)
    schema=_strict_json_schema(fixed._response_model.model_json_schema())
    assert 'payload_json' in schema['$defs']['JsonRow']['properties']
    assert fixed.prompt_sha256!=base.prompt_sha256
    assert fixed.generation_config_sha256!=base.generation_config_sha256
    assert decode_proposal({'records':[{'collection':'claims','object_id':'C','payload_json':'{"claim_id":"C","statement":"原文"}'}],
        'dispositions':[],'sentence_dispositions':[],'unadmitted_candidates_json':[]})['records'][0]['payload']['statement']=='原文'
