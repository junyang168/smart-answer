from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.tests.test_viewpoint_required_cvp_coverage import proposal, payload, _claim
from backend.api.canonical_repository.viewpoint_batch_resolution import CoverageCanonicalViewpointReviewResponse
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
from backend.pipeline.viewpoint_review_format_repair import repair_coverage_pin


def fixture(tmp_path):
    p = proposal('new_viewpoint')
    original = CoverageCanonicalViewpointReviewResponse.model_validate(
        payload(p, coverage_claim_sha256='a'*64+'3f4')).model_dump(mode='json')
    adapter = SimpleNamespace(model_id='opus', backend='subscription',
        prompt_sha256='prompt', generation_config_sha256='config',
        _response_model=CoverageCanonicalViewpointReviewResponse)
    identity = {k:getattr(adapter,k) for k in ('model_id','backend','prompt_sha256','generation_config_sha256')}
    identity['request_payload_sha256']='request'
    raw = tmp_path/'raw-review.json'; packet = tmp_path/'batch-packet.json'
    raw.write_text(json.dumps(dict(identity,response=original)))
    packet.write_text('{}')
    auth = dict(schema_version='cvp_owner_review_pin_format.v1', maximum_calls=1,
        owner_request='동의.', batch_id='b', original_model_identity=identity,
        original_review={'path':str(raw.absolute()),'file_sha256':file_sha256(raw)},
        packet={'path':str(packet.absolute()),'file_sha256':file_sha256(packet)},
        fixes=[dict(index=0,claim_id='C1',component_index=0,old_sha256='a'*64+'3f4',expected_sha256='a'*64)])
    permission = tmp_path/'authorization.json'
    def save_auth():
        body={k:v for k,v in auth.items() if k!='artifact_sha256'}
        auth['artifact_sha256']=sha256_json(body)
        permission.write_text(json.dumps(auth))
    save_auth()
    args=dict(adapter=adapter,request={'batch_id':'b','proposal_sha256':sha256_json(p.model_dump(mode='json'))},
        claims=[_claim('C1','判斷').model_copy(update={'claim_revision_sha256':'a'*64})],proposal=p,
        raw_path=raw,packet_path=packet,authorization_path=permission,cache=tmp_path/'repair.json')
    fixed=deepcopy(original);fixed['change_reviews'][0]['coverage_claim_sha256']='a'*64
    return original,fixed,args,auth,save_auth


def test_owner_pin_exact_correction_preserves_original(tmp_path):
    original,fixed,args,_,_=fixture(tmp_path)
    before=args['raw_path'].read_bytes();calls=[]
    def call(adapter, request, path):
        calls.append(request);return fixed,1,0.1
    parsed,_,n,_=repair_coverage_pin(CoverageCanonicalViewpointReviewResponse.model_validate(original),call=call,**args)
    assert parsed.model_dump(mode='json')==fixed and n==1 and len(calls)==1
    assert args['raw_path'].read_bytes()==before
    assert calls[0]['format_repair_contract']['maximum_calls']==1


@pytest.mark.parametrize('field',['reason','decision','coverage_reason','claim_id'])
def test_owner_pin_other_response_change_stops(tmp_path,field):
    original,fixed,args,_,_=fixture(tmp_path)
    fixed['change_reviews'][0][field]='changed'
    with pytest.raises(ValueError,match='changed another field'):
        repair_coverage_pin(CoverageCanonicalViewpointReviewResponse.model_validate(original),
            call=lambda *a:(fixed,1,0.1),**args)


@pytest.mark.parametrize('fault',['raw','packet','identity','fixes','batch','seal'])
def test_owner_pin_bad_permission_never_calls(tmp_path,fault):
    original,_,args,auth,save=fixture(tmp_path)
    if fault=='raw':args['raw_path'].write_text(args['raw_path'].read_text()+' ')
    elif fault=='packet':args['packet_path'].write_text('{"changed":true}')
    elif fault=='identity':auth['original_model_identity']['model_id']='other';save()
    elif fault=='fixes':auth['fixes'][0]['expected_sha256']='b'*64;save()
    elif fault=='batch':auth['batch_id']='other';save()
    else:auth['artifact_sha256']='wrong';args['authorization_path'].write_text(json.dumps(auth))
    with pytest.raises(ValueError):
        repair_coverage_pin(CoverageCanonicalViewpointReviewResponse.model_validate(original),
            call=lambda *a:pytest.fail('no model call authorized'),**args)
