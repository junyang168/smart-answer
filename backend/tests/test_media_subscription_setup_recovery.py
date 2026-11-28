import json
from pathlib import Path
import pytest
from backend.pipeline.media_batch.credit_recovery import request_for,setup_covered
from backend.pipeline.media_batch.store import key
from backend.pipeline.slide_change_pilot import object_sha,save_json
from backend.pipeline.media_batch.runtime import file_sha

@pytest.fixture
def rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path/'home'))
    ctx={'batch':'B-'+'1'*24,'task':'2'*64}
    monkeypatch.setenv('MEDIA_BATCH_CONTEXT',json.dumps(ctx))
    out=tmp_path/'out';q={'stage':'slide_editor_alignment','model':'gemini-3.8-flash','event_ids':['a','b']}
    f=out/'calls'/object_sha(q)
    save_json(f/'request.json',q);save_json(f/'state.json',{'status':'rejected','http_status':400,'error_type':'SubscriptionSetupError','reserved_usd':.08})
    mirror=Path.home()/'.local/state/smart-answer-media-batches'/ctx['batch']/'model-calls'/ctx['task']/object_sha(q)
    save_json(mirror/'request.json',q);save_json(mirror/'identity.json',{**ctx,'out':str(out.resolve()),'call':key([ctx['task'],str(out.resolve()),q])})
    events=[{'direction':'client','message':{'id':n,'method':method}} for n,method in enumerate(['initialize','authenticate','session/new'],1)]
    events.append({'direction':'agent','message':{'id':3,'error':{'code':-32603,'message':'Failed to start Antigravity conversation'}}})
    rpc=mirror/'subscription/rpc.jsonl';rpc.parent.mkdir(parents=True);rpc.write_text(''.join(json.dumps(x)+'\n' for x in events))
    return out,q,f,mirror,rpc


def test_pre_prompt_recovery_is_same_input_bounded_and_preserves_parent(rejected,monkeypatch):
    out,q,f,mirror,rpc=rejected
    before=(f/'state.json').read_bytes();retry=request_for(out,q)
    assert {k:v for k,v in retry.items() if k!='subscription_setup_recovery'}==q
    assert retry['subscription_setup_recovery']['model_prompt_sent'] is False
    assert retry['subscription_setup_recovery']['max_attempts']==1
    assert (f/'state.json').read_bytes()==before
    target=out/'calls'/object_sha(retry)
    save_json(target/'request.json',retry);save_json(target/'state.json',{'status':'sent_outcome_unknown'})
    assert request_for(out,q)==retry and request_for(out,retry)==retry
    assert not setup_covered(out,f)
    from backend.pipeline.media_model_calls import _saved_call
    with pytest.raises(ValueError,match="never resend"):
        _saved_call(out,retry,.08,lambda:pytest.fail("unknown child must not dispatch"))
    save_json(target/'response.json',{'answer':'saved'})
    save_json(target/'state.json',{'status':'complete','response_sha256':file_sha(target/'response.json')})
    monkeypatch.delenv('MEDIA_BATCH_CONTEXT')
    assert request_for(out,q)==retry and setup_covered(out,f)
    rpc.write_text(rpc.read_text().replace('"direction"','"direction" ',1))
    with pytest.raises(ValueError,match='proof changed'):request_for(out,q)


@pytest.mark.parametrize('defect',['prompt_intent','sent_prompt','wrong_identity','wrong_error'])
def test_no_retry_when_pre_prompt_proof_is_missing_or_changed(rejected,defect):
    out,q,f,mirror,rpc=rejected
    if defect=='prompt_intent':save_json(mirror/'subscription/prompt-intent.json',{'sent':True})
    if defect=='sent_prompt':rpc.write_text(rpc.read_text()+json.dumps({'direction':'client','message':{'id':4,'method':'session/prompt'}})+'\n')
    if defect=='wrong_identity':save_json(mirror/'identity.json',{'batch':'other'})
    if defect=='wrong_error':rpc.write_text(rpc.read_text().replace('Failed to start Antigravity conversation','Quota exhausted'))
    with pytest.raises(ValueError):request_for(out,q)
    assert not (f/'subscription-setup-recovery.json').exists()


def test_unknown_or_provider_rejection_never_acquires_setup_retry(rejected):
    out,q,f,mirror,rpc=rejected
    for state in [{'status':'sent_outcome_unknown','http_status':400,'error_type':'SubscriptionSetupError'},
                  {'status':'rejected','http_status':429,'error_type':'SubscriptionSetupError'},
                  {'status':'rejected','http_status':400,'error_type':'ValueError'}]:
        save_json(f/'state.json',state);assert request_for(out,q)==q
    assert not (f/'subscription-setup-recovery.json').exists()


def test_alignment_collection_counts_only_verified_setup_replacement(rejected):
    from backend.pipeline.editor_slide_runner import effective_alignment_requests
    out,q,f,mirror,rpc=rejected;retry=request_for(out,q)
    target=out/'calls'/object_sha(retry);save_json(target/'request.json',retry)
    save_json(target/'response.json',{'answer':'saved'});save_json(target/'state.json',{'status':'complete','response_sha256':file_sha(target/'response.json')})
    assert list(effective_alignment_requests(out))==[retry]
