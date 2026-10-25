from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository.cvp_recall import assert_current_index, build_index, seal
from backend.pipeline.cvp_recall import (
    build_reviewed_index, load_reviewed_index, maintain_reviewed_index, refresh_recall, run_recall,
)
from backend.pipeline.viewpoint_resolution_runtime import read_artifact
from test_cvp_recall import heads, claims, index
from test_cvp_recall_stages import Client, review_client

LIMIT=100000


def maintain(hs,p,r,out,previous=None,**kwargs):
    return maintain_reviewed_index(hs,previous_output_dir=previous,proposer=p,reviewer=r,
        output_dir=out,max_request_bytes=LIMIT,max_calls=10,**kwargs)


def refresh(ix,cs,c,out,previous=None,**kwargs):
    return refresh_recall(ix,cs,client=c,output_dir=out,previous_output_dir=previous,
                          max_request_bytes=LIMIT,max_calls=10,**kwargs)


def changed_heads():
    hs=heads(4)
    hs[0].update(viewpoint_revision_id='CVR-1-next',head_sha256='a'*64,core_proposition='变化后的观点，保留限定。')
    return [hs[0],hs[1],hs[3]]


def changed_index():
    hs=changed_heads()
    return build_index(hs,[dict(h,summary='更新摘要'+h['viewpoint_id']) for h in hs])


def test_incremental_index_reviews_only_changed_new_heads_retires_and_reuses_direct_raw(tmp_path):
    p,r=Client(),review_client();base=tmp_path/'base';next_=tmp_path/'next'
    initial,_=build_reviewed_index(heads(3),proposer=p,reviewer=r,output_dir=base,
                                   max_request_bytes=LIMIT,max_calls=10)
    output,metrics=maintain(changed_heads(),p,r,next_,base)
    assert metrics['model_calls']==2 and metrics['reused_cards']==1
    assert metrics['changed_viewpoint_ids']==['CV-1','CV-4']
    assert metrics['retired_viewpoint_ids']==['CV-3']
    assert len(p.requests)==len(r.requests)==2
    assert len(json.loads(p.requests[-1]['user_prompt'])['viewpoints'])==2
    assert_current_index(output,changed_heads())
    assert {c['viewpoint_id'] for c in output['cards']}=={'CV-1','CV-2','CV-4'}
    assert len(output['summary_proofs'])==2
    # Authentication follows original raw leaves, never old index artifacts.
    (base/'index.json').unlink();(base/'index-plan.json').unlink()
    assert load_reviewed_index(changed_heads(),proposer=p,reviewer=r,output_dir=next_)==output
    (next_/'index-maintenance.json').unlink()
    again,m=maintain(changed_heads(),p,r,tmp_path/'third',next_)
    assert again==output and m['model_calls']==0
    assert len(p.requests)==len(r.requests)==2


def test_index_unchanged_and_empty_registry_require_zero_calls_even_with_zero_budget(tmp_path):
    p,r=Client(),review_client()
    ix,_=maintain(heads(),p,r,tmp_path/'a')
    def forbidden(*args):raise AssertionError('unchanged cards must not invoke')
    again,m=maintain_reviewed_index(heads(),previous_output_dir=tmp_path/'a',proposer=p,reviewer=r,
        output_dir=tmp_path/'b',max_request_bytes=LIMIT,max_calls=0,invoke=forbidden)
    assert again==ix and m['model_calls']==0
    empty,m=maintain_reviewed_index([],previous_output_dir=tmp_path/'b',proposer=p,reviewer=r,
        output_dir=tmp_path/'empty',max_request_bytes=LIMIT,max_calls=0,invoke=forbidden)
    assert empty['cards']==[] and empty['summary_proofs']=={} and m['model_calls']==0
    assert load_reviewed_index([],proposer=p,reviewer=r,output_dir=tmp_path/'empty')==empty


def test_index_reuse_authenticates_exact_raw_review_and_summary_not_just_checksum(tmp_path):
    p,r=Client(),review_client()
    out,_=maintain(heads(),p,r,tmp_path/'a')
    edited=deepcopy(out);edited['cards'][0]['summary']='篡改摘要'
    edited=seal({k:v for k,v in edited.items() if k!='artifact_sha256'})
    (tmp_path/'a/index.json').write_text(json.dumps(edited))
    with pytest.raises(ValueError,match='derivation'):
        maintain(heads(),p,r,tmp_path/'b',tmp_path/'a')
    assert len(p.requests)==len(r.requests)==1
    assert not (tmp_path/'b/index.json').exists()


def test_index_missing_original_review_cannot_reuse_or_regenerate_summary(tmp_path):
    p,r=Client(),review_client();base=tmp_path/'base'
    ix,_=maintain(heads(),p,r,base)
    proof=next(iter(ix['summary_proofs'].values()))
    from pathlib import Path
    Path(proof['review_cache']).unlink()
    with pytest.raises(FileNotFoundError):maintain(heads(),p,r,tmp_path/'b',base)
    assert len(p.requests)==len(r.requests)==1


def test_index_unknown_change_call_is_not_repeated_on_resume(tmp_path):
    p,r=Client(),review_client();base=tmp_path/'base';new=tmp_path/'new'
    maintain(heads(),p,r,base)
    def timeout(payload):raise TimeoutError('unknown provider outcome')
    p.behavior=timeout
    with pytest.raises(TimeoutError):maintain(changed_heads(),p,r,new,base)
    with pytest.raises(ValueError,match='unknown'):maintain(changed_heads(),p,r,new,base)
    assert len(p.requests)==2 and len(r.requests)==1


def test_refresh_replaces_changed_candidates_preserves_unchanged_and_merges_current_active_links(tmp_path):
    c=Client();base=tmp_path/'base'
    run_recall(index(),claims(),client=c,output_dir=base,max_request_bytes=LIMIT,max_calls=10)
    current=changed_index()
    # Keep CV-2 byte-identical so only CV-1 and CV-4 are recalled.
    current['cards'][1]=index()['cards'][1]
    current=seal({k:v for k,v in current.items() if k!='artifact_sha256'})
    c.behavior=lambda payload:{'claim_candidates':[{'claim_ref':r['claim_ref'],'cvp_refs':[]} for r in payload['claims']]}
    links=[{'claim_id':'CL-0','viewpoint_id':'CV-4','effective_state':'active'}]
    result,m=refresh(current,claims(),c,tmp_path/'new',base,existing_links=links)
    assert m['changed_viewpoint_ids']==['CV-1','CV-4'] and m['reused_cards']==1
    assert m['retired_viewpoint_ids']==['CV-3'] and m['model_calls']==1
    assert len(json.loads(c.requests[-1]['user_prompt'])['viewpoints'])==2
    assert result['claim_candidates']==[{'claim_id':'CL-0','viewpoint_ids':['CV-2','CV-4']},
                                         {'claim_id':'CL-1','viewpoint_ids':['CV-2']}]
    assert result['plan_sha256']==m['current_plan']['artifact_sha256']
    assert m['current_plan']['index_sha256']==current['artifact_sha256']
    assert set(m['current_plan']['viewpoint_refs'].values())=={'CV-1','CV-2','CV-4'}
    # Direct leaf proofs survive deletion of both old roots' derived plans/results.
    (base/'recall-result.json').unlink();(base/'recall-plan.json').unlink()
    (tmp_path/'new/changes/recall-authentication.json').unlink()
    replay,again=refresh(current,claims(),c,tmp_path/'third',tmp_path/'new',existing_links=[])
    assert again['model_calls']==0 and len(c.requests)==2
    assert replay['claim_candidates'][0]['viewpoint_ids']==['CV-2']


def test_changed_claim_projection_requires_full_current_registry_recall(tmp_path):
    c=Client();base=tmp_path/'base'
    refresh(index(),claims(),c,base)
    cs=claims();cs[0]['conditions']=['新的必要限定']
    result,m=refresh(index(),cs,c,tmp_path/'new',base)
    assert m['changed_viewpoint_ids']==['CV-1','CV-2','CV-3']
    assert m['model_calls']==1 and m['reused_cards']==0
    assert len(json.loads(c.requests[-1]['user_prompt'])['viewpoints'])==3
    assert json.loads(c.requests[-1]['user_prompt'])['claims'][0]['conditions']==['新的必要限定']


def test_refresh_does_not_promote_prior_link_only_candidates_to_model_recall(tmp_path):
    c=Client(lambda p:{'claim_candidates':[{'claim_ref':v['claim_ref'],'cvp_refs':[]} for v in p['claims']]})
    links=[{'claim_id':'CL-0','viewpoint_id':'CV-1','effective_state':'active'}]
    run_recall(index(),claims(),client=c,output_dir=tmp_path/'a',max_request_bytes=LIMIT,max_calls=10,
               existing_links=links)
    result,m=refresh(index(),claims(),c,tmp_path/'b',tmp_path/'a',existing_links=[])
    assert m['model_calls']==0 and all(not r['viewpoint_ids'] for r in result['claim_candidates'])
    assert len(c.requests)==1


def test_refresh_unchanged_empty_and_retired_registry_have_complete_zero_call_results(tmp_path):
    c=Client();refresh(index(),claims(),c,tmp_path/'a')
    nochange,m=refresh_recall(index(),claims(),client=c,output_dir=tmp_path/'b',previous_output_dir=tmp_path/'a',
        max_request_bytes=LIMIT,max_calls=0,invoke=lambda *args:pytest.fail('no changes must not invoke'))
    assert m['model_calls']==0 and m['changed_viewpoint_ids']==[]
    empty,m=refresh(index(0),claims(),c,tmp_path/'empty',tmp_path/'b')
    assert m['model_calls']==0 and empty['claim_candidates']==[
        {'claim_id':'CL-0','viewpoint_ids':[]},{'claim_id':'CL-1','viewpoint_ids':[]}]
    refresh(index(0),claims(),c,tmp_path/'empty-next',tmp_path/'empty')
    assert len(c.requests)==1


def test_refresh_rejects_tampered_prior_output_and_missing_raw_without_fallback(tmp_path):
    c=Client();refresh(index(),claims(),c,tmp_path/'a')
    path=tmp_path/'a/recall-result.json';out=read_artifact(path)
    out['claim_candidates'][0]['viewpoint_ids']=[]
    out=seal({k:v for k,v in out.items() if k!='artifact_sha256'});path.write_text(json.dumps(out))
    with pytest.raises(ValueError,match='binding changed'):
        refresh(index(),claims(),c,tmp_path/'b',tmp_path/'a')
    assert len(c.requests)==1


def test_refresh_unknown_delta_does_not_dispatch_again_and_checks_full_budget_before_call(tmp_path):
    c=Client();refresh(index(),claims(),c,tmp_path/'a')
    with pytest.raises(ValueError,match='budget'):
        refresh_recall(changed_index(),claims(),client=c,output_dir=tmp_path/'budget',
            previous_output_dir=tmp_path/'a',max_request_bytes=LIMIT,max_calls=0)
    assert len(c.requests)==1
    def timeout(p):raise TimeoutError('unknown delta outcome')
    c.behavior=timeout
    with pytest.raises(TimeoutError):refresh(changed_index(),claims(),c,tmp_path/'b',tmp_path/'a')
    with pytest.raises(ValueError,match='unknown'):refresh(changed_index(),claims(),c,tmp_path/'b',tmp_path/'a')
    assert len(c.requests)==2


def test_completed_incremental_stages_authenticate_same_root_with_zero_remaining_call_budget(tmp_path):
    p,r=Client(),review_client()
    maintain(heads(),p,r,tmp_path/'index-base')
    expected,_=maintain(changed_heads(),p,r,tmp_path/'index-next',tmp_path/'index-base')
    again,m=maintain_reviewed_index(changed_heads(),previous_output_dir=tmp_path/'index-base',
        proposer=p,reviewer=r,output_dir=tmp_path/'index-next',max_request_bytes=LIMIT,max_calls=0,
        invoke=lambda *args:pytest.fail('completed stage must not invoke'))
    assert again==expected and m['model_calls']==0
    c=Client();refresh(index(),claims(),c,tmp_path/'recall-base')
    expected,_=refresh(changed_index(),claims(),c,tmp_path/'recall-next',tmp_path/'recall-base')
    again,m=refresh_recall(changed_index(),claims(),previous_output_dir=tmp_path/'recall-base',
        client=c,output_dir=tmp_path/'recall-next',max_request_bytes=LIMIT,max_calls=0,
        invoke=lambda *args:pytest.fail('completed stage must not invoke'))
    assert again==expected and m['model_calls']==0
    assert len(p.requests)==len(r.requests)==2 and len(c.requests)==2


def test_incremental_summary_reject_and_current_drift_never_gain_extra_calls(tmp_path):
    p,r=Client(),review_client();maintain(heads(),p,r,tmp_path/'base')
    r.behavior=lambda payload:{'verdicts':[{'cvp_ref':v['cvp_ref'],'decision':'reject','reason':'限定缺漏'}
                                           for v in payload['viewpoints']]}
    for _ in range(2):
        with pytest.raises(ValueError,match='rejected'):
            maintain(changed_heads(),p,r,tmp_path/'new',tmp_path/'base')
    assert len(p.requests)==len(r.requests)==2
    def drift():raise ValueError('current head changed')
    with pytest.raises(ValueError,match='current head changed'):
        maintain(changed_heads(),p,r,tmp_path/'drift',tmp_path/'base',check_current=drift)
    assert len(p.requests)==len(r.requests)==2


def test_incremental_recall_missing_direct_raw_cannot_be_recreated(tmp_path):
    c=Client();refresh(index(),claims(),c,tmp_path/'base')
    (tmp_path/'base/changes/recall-0000.json').unlink()
    with pytest.raises(FileNotFoundError):refresh(index(),claims(),c,tmp_path/'new',tmp_path/'base')
    assert len(c.requests)==1 and not (tmp_path/'new/recall-result.json').exists()


def test_capacity_subset_reuses_exact_direct_raw_then_recalls_only_new_cards(tmp_path):
    from backend.pipeline.cvp_recall import subset_recall,_authenticate_recall_root
    c=Client();cs=claims();ix=index()
    refresh(ix,cs,c,tmp_path/'whole')
    selected=[cs[-1]]
    result,m=subset_recall(ix,selected,origin_output_dir=tmp_path/'whole',output_dir=tmp_path/'child',client=c)
    assert m['model_calls']==0 and len(c.requests)==1
    assert [r['claim_id'] for r in result['claim_candidates']]==[selected[0]['claim_id']]
    auth,_=_authenticate_recall_root(tmp_path/'child',client=c)
    assert next(iter(auth['proofs'].values()))['claims']==cs
    changed_ix=changed_index()
    changed_ix=seal({k:v for k,v in changed_ix.items() if k!='artifact_sha256'}|{
        'cards':[ix['cards'][1] if card['viewpoint_id']=='CV-2' else card for card in changed_ix['cards']]})
    next_,m=refresh(changed_ix,selected,c,tmp_path/'child-refresh',tmp_path/'child')
    assert m['model_calls']==1 and len(c.requests)==2
    payload=json.loads(c.requests[-1]['user_prompt'])
    assert len(payload['claims'])==1 and len(payload['viewpoints'])==2
    assert _authenticate_recall_root(tmp_path/'child-refresh',client=c)[0]['result_sha256']==next_['artifact_sha256']
    changed=deepcopy(selected);changed[0]['statement']='changed input'
    with pytest.raises(ValueError,match='exact original Claim'):
        subset_recall(ix,changed,origin_output_dir=tmp_path/'whole',output_dir=tmp_path/'bad',client=c)
    # A re-sealed child cannot use another Claim's raw binding.
    auth['claims'][0]['statement']='forged'
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    forged=seal({k:v for k,v in auth.items() if k!='artifact_sha256'})
    (tmp_path/'child/recall-authentication.json').write_text(json.dumps(forged))
    with pytest.raises(ValueError):_authenticate_recall_root(tmp_path/'child',client=c)
