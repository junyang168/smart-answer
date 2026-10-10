"""No provider calls: original unknown evidence and a single explicit retry."""
import json
from pathlib import Path
import pytest
from backend.pipeline import viewpoint_resolution_runtime as r
from backend.pipeline import cvp_model_retry_once as m
from backend.pipeline.cvp_sql_flow import binding
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


class Adapter:
    model_id='gpt-6.1-sol';backend='codex_subscription'
    prompt_sha256='prompt';generation_config_sha256='config';max_request_bytes=600000
    calls=0
    def request_bytes(self,p):return len(json.dumps(p).encode())
    def request_encoding_proof(self,p):return {'logical_sha256':sha256_json(p)}
    def generate(self,p):self.calls+=1;return {'answer':'complete'}


def fixture(tmp_path):
    root=tmp_path/'execution';run='CVPG-example';cache=root/run/'normal-review/raw-proposal.json'
    a=Adapter();p={'full':'source'};identity=r.model_request_identity(a,p)
    def save(suffix,d):
        q=cache.with_name(cache.stem+suffix);r.write_immutable(q,{**d,'artifact_sha256':sha256_json(d)});return q
    save('.call-intent.json',{'schema_version':'wang_model_call_intent_v1',**identity,
         'cache_path':str(cache.resolve()),'automatic_retry':False})
    intent=r.read_artifact(cache.with_name(cache.stem+'.call-intent.json'))
    save('.call-failure.json',{'schema_version':'wang_model_call_failure_v1','invocation_sha256':intent['artifact_sha256'],
         'execution_status':'outcome_unknown','automatic_retry':False,'error_type':'CodexSubscriptionError'})
    save('.request-encoding.json',{'schema_version':'wang_model_request_encoding_proof_v1',**a.request_encoding_proof(p)})
    r.write_immutable(cache.with_name(cache.stem+'.request-budget.json'),{'request_identity':identity,'actual_request_bytes':a.request_bytes(p)})
    log=tmp_path/'original.log';log.write_text('subprocess.TimeoutExpired: transport timed out after 1800.0 seconds')
    d={'schema_version':m.SCHEMA,'authorized_by':'owner','authorization_quote':'同意','maximum_additional_calls':1,
       'accept_original_outcome_unknown':True,'automatic_future_retries_authorized':False,'human_content_approval':False,
       'campaign_sha256':'campaign','execution_root':str(root.resolve()),'run_id':run,'cache_path':str(cache.resolve()),
       'original_bindings':[binding(cache.with_name(cache.stem+s)) for s in
          ('.call-intent.json','.call-failure.json','.request-encoding.json','.request-budget.json')],
       'timeout_log':binding(log)}
    auth=tmp_path/'owner.json';r.write_immutable(auth,{**d,'artifact_sha256':sha256_json(d)})
    return root,cache,a,p,auth


def test_once_answer_preserves_original_unknown_and_cache_reuses(tmp_path):
    root,c,a,p,auth=fixture(tmp_path)
    before={q:q.read_bytes() for q in c.parent.iterdir()}
    with m.authorized_retry(root,'campaign',auth):
        assert r.call_model(a,p,c)[1]==1
        assert r.call_model(a,p,c)==({'answer':'complete'},0,0.0)
    assert a.calls==1
    assert all(q.read_bytes()==data for q,data in before.items())
    assert r.read_artifact(c)['owner_retry_once']['original_outcome_preserved']=='outcome_unknown'
    with pytest.raises(ValueError,match='explicit'): 
        with m.authorized_retry(root,'campaign'):pass
    with pytest.raises(ValueError,match='authority'):r.read_saved_model_response(a,p,c)


def test_second_unknown_is_not_retried(tmp_path):
    root,c,a,p,auth=fixture(tmp_path)
    def fail(p):a.calls+=1;raise TimeoutError('still unknown')
    a.generate=fail
    with m.authorized_retry(root,'campaign',auth):
        with pytest.raises(TimeoutError):r.call_model(a,p,c)
        with pytest.raises(ValueError,match='unknown'):r.call_model(a,p,c)
    assert a.calls==1
    assert c.with_name(c.stem+'.owner-retry-once.call-failure.json').exists()


@pytest.mark.parametrize('fault',['request','config','encoding','bytes','campaign','receipt','answered'])
def test_drift_and_answered_request_refused(tmp_path,fault):
    root,c,a,p,auth=fixture(tmp_path)
    if fault=='request':p={'full':'changed'}
    if fault=='config':a.generation_config_sha256='other'
    if fault=='encoding':a.request_encoding_proof=lambda p:{'wrong':1}
    if fault=='bytes':a.request_bytes=lambda p:1234
    if fault=='receipt':c.with_name(c.stem+'.call-failure.json').write_text('{}')
    if fault=='answered':r.write_immutable(c,{'unrelated':'response'})
    with pytest.raises((ValueError,KeyError)):
        with m.authorized_retry(root,'foreign' if fault=='campaign' else 'campaign',auth):r.call_model(a,p,c)
    assert a.calls==0
