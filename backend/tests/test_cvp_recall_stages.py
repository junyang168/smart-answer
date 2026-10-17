import json
from copy import deepcopy

import pytest

from backend.api.canonical_repository.cvp_recall import build_index
from backend.pipeline.cvp_recall import (
    build_reviewed_index, load_reviewed_index, recall_adapter, run_recall,
)
from backend.pipeline.viewpoint_resolution_runtime import read_artifact
from test_cvp_recall import claims, heads, index


class Client:
    backend='codex_subscription'
    model='gpt-6.1-sol'
    reasoning_effort='high'
    max_output_tokens=64000
    def __init__(self,behavior=None):
        self.requests=[];self.behavior=behavior
    def generate_json(self,**request):
        self.requests.append(request)
        p=json.loads(request['user_prompt'])
        if self.behavior:return self.behavior(p)
        if 'summaries' in p:
            return {'verdicts':[{'cvp_ref':v['cvp_ref'],'decision':'approve','reason':'忠实'}
                                 for v in p['viewpoints']]}
        if 'claims' in p:
            return {'claim_candidates':[{'claim_ref':c['claim_ref'],
                'cvp_refs':[v['cvp_ref'] for v in p['viewpoints']]} for c in p['claims']]}
        return {'summaries':[{'cvp_ref':v['cvp_ref'],'summary':'保留观点及限定。'} for v in p['viewpoints']]}


def review_client(behavior=None):
    c=Client(behavior);c.model='claude-opus-5-5';c.backend='claude_subscription';return c


def files(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_recall_uses_actual_encoded_size_and_replays_saved_answers(tmp_path):
    client=Client();probe={'schema_version':'wang_cvp_recall_v1','claims':[
        {'claim_ref':'c1',**{k:v for k,v in claims(1)[0].items() if k!='claim_id'}}],
        'viewpoints':[{'cvp_ref':'v1',**{k:v for k,v in index()['cards'][0].items()
                                       if k in ('core_proposition','summary','scope','proposition_signature')}}]}
    size=recall_adapter(client,probe,max_request_bytes=100000).request_bytes(probe)
    # JSON schema, system prompt and user payload all count. This budget fits
    # a card but not the complete registry; no application truncation is used.
    out,metrics=run_recall(index(8),claims(1),client=client,output_dir=tmp_path,
                           max_request_bytes=size+80,max_calls=20)
    assert 1 < metrics['shards']==len(client.requests)
    assert all(len(r['json_schema']['schema']['properties'])==1 for r in client.requests)
    assert len(out['claim_candidates'][0]['viewpoint_ids'])==8
    before=files(tmp_path)
    replay,again=run_recall(index(8),claims(1),client=client,output_dir=tmp_path,
                            max_request_bytes=size+80,max_calls=20)
    assert replay==out and again['model_calls']==0
    assert files(tmp_path)==before


def test_all_requests_fit_and_call_budget_is_bound_before_any_dispatch(tmp_path):
    c=Client()
    with pytest.raises(ValueError,match='capacity'):
        run_recall(index(),claims(),client=c,output_dir=tmp_path,max_request_bytes=1,max_calls=5)
    assert c.requests==[] and files(tmp_path)=={}
    with pytest.raises(ValueError,match='budget'):
        run_recall(index(),claims(),client=c,output_dir=tmp_path,max_request_bytes=100000,max_calls=0)
    assert c.requests==[] and files(tmp_path)=={}


def test_invalid_recall_answer_remains_failed_on_resume_without_another_call(tmp_path):
    c=Client(lambda p:{'claim_candidates':[{'claim_ref':'c1','cvp_refs':['v999']} ]})
    for _ in range(2):
        with pytest.raises(ValueError):
            run_recall(index(),claims(1),client=c,output_dir=tmp_path,
                       max_request_bytes=100000,max_calls=5)
    assert len(c.requests)==1
    assert read_artifact(tmp_path/'recall-0000.json')['response']['claim_candidates'][0]['cvp_refs']==['v999']
    assert not (tmp_path/'recall-result.json').exists()


def test_unknown_dispatch_is_not_automatically_repeated(tmp_path):
    def fail(p):raise TimeoutError('unknown provider outcome')
    c=Client(fail)
    with pytest.raises(TimeoutError):
        run_recall(index(),claims(),client=c,output_dir=tmp_path,max_request_bytes=100000,max_calls=5)
    with pytest.raises(ValueError,match='unknown'):
        run_recall(index(),claims(),client=c,output_dir=tmp_path,max_request_bytes=100000,max_calls=5)
    assert len(c.requests)==1


def test_head_drift_check_prevents_dispatch_and_completion(tmp_path):
    c=Client()
    def changed():raise ValueError('current head changed')
    with pytest.raises(ValueError,match='head changed'):
        run_recall(index(),claims(),client=c,output_dir=tmp_path,
                   max_request_bytes=100000,max_calls=5,check_current=changed)
    assert c.requests==[]
    assert not (tmp_path/'recall-result.json').exists()


def test_summary_generation_review_and_authenticated_readonly_reuse(tmp_path):
    proposer=Client();reviewer=review_client()
    result,metrics=build_reviewed_index(heads(5),proposer=proposer,reviewer=reviewer,
        output_dir=tmp_path,max_request_bytes=100000,max_calls=10,batch_size=2)
    assert metrics['model_calls']==6
    assert len(proposer.requests)==len(reviewer.requests)==3
    before=files(tmp_path)
    assert load_reviewed_index(heads(5),proposer=proposer,reviewer=reviewer,output_dir=tmp_path)==result
    assert len(proposer.requests)==3 and len(reviewer.requests)==3
    assert files(tmp_path)==before


def test_readonly_index_reuse_rejects_missing_raw_and_does_not_recreate_it(tmp_path):
    p=Client();r=review_client()
    build_reviewed_index(heads(),proposer=p,reviewer=r,output_dir=tmp_path,
                         max_request_bytes=100000,max_calls=4)
    (tmp_path/'summary-review-0000.json').unlink()
    before=files(tmp_path)
    with pytest.raises(FileNotFoundError):load_reviewed_index(heads(),proposer=p,reviewer=r,output_dir=tmp_path)
    assert len(p.requests)==len(r.requests)==1 and files(tmp_path)==before


def test_review_reject_is_preserved_and_does_not_become_an_approved_card(tmp_path):
    p=Client();r=review_client(lambda data:{'verdicts':[
        {'cvp_ref':v['cvp_ref'],'decision':'reject','reason':'遗漏限定'} for v in data['viewpoints']]})
    for _ in range(2):
        with pytest.raises(ValueError,match='rejected'):
            build_reviewed_index(heads(),proposer=p,reviewer=r,output_dir=tmp_path,
                                 max_request_bytes=100000,max_calls=4)
    assert len(p.requests)==len(r.requests)==1
    assert not (tmp_path/'index.json').exists()


def test_index_size_budget_and_late_oversized_head_checked_before_any_model(tmp_path):
    p=Client();r=review_client();hs=heads();hs[-1]['core_proposition']='字'*100000
    with pytest.raises(ValueError,match='capacity'):
        build_reviewed_index(hs,proposer=p,reviewer=r,output_dir=tmp_path,
                             max_request_bytes=20000,max_calls=10,batch_size=1)
    assert p.requests==r.requests==[] and files(tmp_path)=={}


def test_tampered_index_cannot_replace_authenticated_provider_answers(tmp_path):
    p=Client();r=review_client()
    build_reviewed_index(heads(),proposer=p,reviewer=r,output_dir=tmp_path,
                         max_request_bytes=100000,max_calls=4)
    altered=index();(tmp_path/'index.json').write_text(json.dumps(altered))
    with pytest.raises(ValueError,match='derivation'):
        load_reviewed_index(heads(),proposer=p,reviewer=r,output_dir=tmp_path)
    assert len(p.requests)==len(r.requests)==1


def test_response_schema_can_only_select_given_short_references():
    c=Client();data={'claims':[{'claim_ref':'c1','statement':'原文'}],
                     'viewpoints':[{'cvp_ref':'v1','core_proposition':'命题','summary':'摘要',
                                    'scope':{},'proposition_signature':{}}]}
    a=recall_adapter(c,data,max_request_bytes=10000)
    with pytest.raises(ValueError):a._response_model.model_validate(
        {'claim_candidates':[{'claim_ref':'c1','cvp_refs':['foreign']}]})


def test_recall_and_summary_review_cannot_substitute_other_roles(tmp_path):
    p=Client();p.model='different'
    with pytest.raises(ValueError,match='subscription role'):
        run_recall(index(),claims(),client=p,output_dir=tmp_path,max_request_bytes=100000,max_calls=10)
    p=Client()
    with pytest.raises(ValueError,match='subscription role'):
        build_reviewed_index(heads(),proposer=p,reviewer=p,output_dir=tmp_path,
                             max_request_bytes=100000,max_calls=10)
    assert p.requests==[] and files(tmp_path)=={}


def test_schema_change_cannot_reinterpret_a_saved_answer_even_with_same_prompt(tmp_path,monkeypatch):
    from pydantic import create_model
    from backend.pipeline import cvp_recall as stage
    client=Client()
    run_recall(index(),claims(),client=client,output_dir=tmp_path,max_request_bytes=100000,max_calls=10)
    before=files(tmp_path);original=stage._adapter
    def changed(c,prompt,model,name,limit):
        upgraded=create_model('NewOutputContract',__base__=model,new_interpretation=(bool,False))
        return original(c,prompt,upgraded,name,limit)
    monkeypatch.setattr(stage,'_adapter',changed)
    with pytest.raises(ValueError,match='generation config'):
        run_recall(index(),claims(),client=client,output_dir=tmp_path,max_request_bytes=100000,max_calls=10)
    assert len(client.requests)==1 and files(tmp_path)==before
